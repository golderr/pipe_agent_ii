from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from typer.testing import CliRunner

import tcg_pipeline.cli as cli_module
from tcg_pipeline.agents.profiles import NEWS_AGENT_PROFILE
from tcg_pipeline.cli import app
from tcg_pipeline.db.models import (
    AgentRun,
    AgentRunOutcome,
    AgentRunReviewItem,
    LLMCostUsage,
    NewsArticle,
    NewsExtraction,
    NewsExtractionParseStatus,
    NewsExtractionPass,
    NewsFetchStatus,
    NewsSemanticInterpretation,
    NewsSource,
    NewsTriageStatus,
    Priority,
    ReviewItem,
    ReviewItemType,
    SourceRun,
    SystemAlert,
)
from tcg_pipeline.evaluation.news_agent_smoke import (
    build_news_agent_smoke_report,
    validate_news_agent_smoke_report,
)
from tcg_pipeline.news.llm import DEFAULT_EXTRACTION_MODEL, LLM_PROVIDER_ANTHROPIC
from tcg_pipeline.settings import Settings

runner = CliRunner()


def _ensure_status_regression_review_item_type(postgres_session: Session) -> None:
    exists = postgres_session.execute(
        text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM pg_enum enum_value
                JOIN pg_type enum_type
                  ON enum_type.oid = enum_value.enumtypid
                WHERE enum_type.typname = 'review_item_type_enum'
                  AND enum_value.enumlabel = :enum_label
            )
            """
        ),
        {"enum_label": ReviewItemType.STATUS_REGRESSION_REVIEW.value},
    ).scalar()
    if not exists:
        pytest.skip("Apply the status regression review-item enum migration before this test.")


def test_news_agent_smoke_report_summarizes_window(
    postgres_session: Session,
) -> None:
    _ensure_status_regression_review_item_type(postgres_session)
    source = _news_source()
    now = datetime(2099, 5, 11, 21, tzinfo=UTC)
    old_source_run = _source_run(source, run_timestamp=now - timedelta(days=2))
    source_run = _source_run(source, run_timestamp=now - timedelta(hours=1), records_pulled=4)
    article = _article(source, title="Smoke article", fetched_at=now - timedelta(minutes=50))
    old_article = _article(source, title="Old smoke article", fetched_at=now - timedelta(days=2))
    postgres_session.add_all([source, old_source_run, source_run, article, old_article])
    postgres_session.flush()
    old_agent_run = _agent_run(
        source_run=old_source_run,
        article=old_article,
        triggered_by=["new_candidate"],
        created_at=now - timedelta(days=2),
    )
    completed_run = _agent_run(
        source_run=source_run,
        article=article,
        triggered_by=["new_candidate"],
        outcome=AgentRunOutcome.COMPLETED.value,
        cost_usd=Decimal("0.070000"),
        created_at=now - timedelta(minutes=40),
    )
    escalated_run = _agent_run(
        source_run=source_run,
        article=article,
        triggered_by=[
            "low_confidence",
            "pass1_pass2_conflict",
            "status_regression_candidate",
        ],
        outcome=AgentRunOutcome.ESCALATED.value,
        cost_usd=Decimal("0.030000"),
        created_at=now - timedelta(minutes=30),
    )
    postgres_session.add_all([old_agent_run, completed_run, escalated_run])
    postgres_session.flush()
    _link_review_item(postgres_session, source_run=source_run, agent_run=completed_run)
    _link_review_item(
        postgres_session,
        source_run=source_run,
        agent_run=escalated_run,
        item_type=ReviewItemType.STATUS_REGRESSION_REVIEW,
    )
    postgres_session.add_all(
        [
            _cost_usage(
                cost_date=now.date(),
                capability="agent.news_v1",
                call_count=2,
                spent_usd=Decimal("0.100000"),
            ),
            _cost_usage(
                cost_date=now.date(),
                capability="semantic.news_v1",
                call_count=4,
                spent_usd=Decimal("0.400000"),
            ),
        ]
    )
    postgres_session.add(
        SystemAlert(
            alert_key="news_job_failed",
            severity="warning",
            scope={"source_name": source.slug},
            message="News job failed.",
            detail={"job_id": "test"},
            raised_at=now - timedelta(minutes=20),
            last_seen_at=now - timedelta(minutes=10),
        )
    )
    postgres_session.add(
        SystemAlert(
            alert_key="permit_daily_cost_warn_cap_reached",
            severity="warning",
            scope={"bucket": "permits"},
            message="Permit alert should not be included.",
            raised_at=now - timedelta(minutes=20),
            last_seen_at=now - timedelta(minutes=10),
        )
    )
    postgres_session.flush()

    report = build_news_agent_smoke_report(
        postgres_session,
        since=now - timedelta(hours=2),
        until=now,
        source_name=source.slug,
    )

    assert report.source_run_count == 1
    assert report.agent_run_count == 2
    assert report.outcome_counts == {
        AgentRunOutcome.COMPLETED.value: 1,
        AgentRunOutcome.ESCALATED.value: 1,
    }
    assert report.trigger_counts == {
        "low_confidence": 1,
        "new_candidate": 1,
        "pass1_pass2_conflict": 1,
        "status_regression_candidate": 1,
    }
    assert report.review_item_type_counts == {
        ReviewItemType.NEW_CANDIDATE.value: 1,
        ReviewItemType.STATUS_REGRESSION_REVIEW.value: 1,
    }
    assert report.status_regression_agent_run_count == 1
    assert report.status_regression_review_item_count == 1
    assert report.agent_run_total_cost_usd == Decimal("0.100000")
    assert report.missing_review_link_count == 0
    assert report.runs[1].review_item_type_counts == {
        ReviewItemType.STATUS_REGRESSION_REVIEW.value: 1,
    }
    assert report.runs[1].status_regression_review_item_count == 1
    assert report.cost_usage_total_usd == Decimal("0.500000")
    capability_costs = [
        (row.capability, row.call_count, row.spent_usd)
        for row in report.cost_usage_by_capability
    ]
    assert capability_costs == [
        ("agent.news_v1", 2, Decimal("0.100000")),
        ("semantic.news_v1", 4, Decimal("0.400000")),
    ]
    assert report.cost_cap_days[0].cost_date == now.date()
    assert report.cost_cap_days[0].spent_usd == Decimal("0.500000")
    assert report.semantic_parse_status_counts == {}
    assert report.semantic_issue_count == 0
    assert report.alert_count >= 1
    assert any(alert.alert_key == "news_job_failed" for alert in report.alerts)
    assert report.runs[0].article_title == "Smoke article"
    assert report.runs[0].article_source_slug == source.slug
    assert validate_news_agent_smoke_report(
        report,
        min_source_runs=1,
        min_agent_runs=2,
        required_triggers=(
            "new_candidate",
            "low_confidence",
            "status_regression_candidate",
        ),
        required_outcomes=(AgentRunOutcome.COMPLETED.value,),
        allowed_outcomes=(AgentRunOutcome.COMPLETED.value, AgentRunOutcome.ESCALATED.value),
        min_status_regression_review_items=1,
        min_total_cost_usd=Decimal("0.05"),
        max_total_cost_usd=Decimal("0.50"),
    ) == []


def test_news_agent_smoke_validation_reports_missing_expectations(
    postgres_session: Session,
) -> None:
    source = _news_source()
    now = datetime(2099, 5, 12, 21, tzinfo=UTC)
    source_run = _source_run(source, run_timestamp=now - timedelta(minutes=30))
    article = _article(source, fetched_at=now - timedelta(minutes=25))
    postgres_session.add_all([source, source_run, article])
    postgres_session.flush()
    postgres_session.add(
        _agent_run(
            source_run=source_run,
            article=article,
            triggered_by=["new_candidate"],
            outcome=AgentRunOutcome.COMPLETED.value,
            cost_usd=Decimal("0.250000"),
            created_at=now - timedelta(minutes=20),
        )
    )
    postgres_session.add(
        _cost_usage(
            cost_date=now.date(),
            capability="agent.news_v1",
            spent_usd=Decimal("0.250000"),
        )
    )
    postgres_session.flush()
    report = build_news_agent_smoke_report(
        postgres_session,
        since=now - timedelta(hours=1),
        until=now,
        source_name=source.slug,
    )

    failures = validate_news_agent_smoke_report(
        report,
        min_source_runs=2,
        max_source_runs=0,
        min_agent_runs=2,
        max_agent_runs=0,
        required_triggers=("low_confidence",),
        required_outcomes=(AgentRunOutcome.KILLED_BY_SWITCH.value,),
        allowed_outcomes=(AgentRunOutcome.KILLED_BY_SWITCH.value,),
        min_status_regression_review_items=1,
        min_total_cost_usd=Decimal("0.30"),
        max_total_cost_usd=Decimal("0.10"),
        require_review_links=True,
    )

    assert failures == [
        "Expected at least 2 news source runs; found 1.",
        "Expected at most 0 news source runs; found 1.",
        "Expected at least 2 news agent runs; found 1.",
        "Expected at most 0 news agent runs; found 1.",
        "Missing required trigger(s): low_confidence.",
        "Missing required outcome(s): killed_by_switch.",
        "Unexpected outcome(s): completed.",
        "Expected at least 1 linked status regression review item(s); found 0.",
        "Expected total news bucket cost >= $0.30; found $0.250000.",
        "Expected total news bucket cost <= $0.10; found $0.250000.",
        "1 news agent run(s) have no linked review item.",
    ]


def test_news_agent_smoke_report_counts_review_item_types(
    postgres_session: Session,
) -> None:
    source = _news_source()
    now = datetime(2099, 5, 13, 18, tzinfo=UTC)
    source_run = _source_run(source, run_timestamp=now - timedelta(minutes=30))
    article = _article(source, fetched_at=now - timedelta(minutes=25))
    postgres_session.add_all([source, source_run, article])
    postgres_session.flush()
    agent_run = _agent_run(
        source_run=source_run,
        article=article,
        triggered_by=["new_candidate"],
        outcome=AgentRunOutcome.COMPLETED.value,
        created_at=now - timedelta(minutes=20),
    )
    postgres_session.add(agent_run)
    postgres_session.flush()
    _link_review_item(postgres_session, source_run=source_run, agent_run=agent_run)
    postgres_session.flush()

    report = build_news_agent_smoke_report(
        postgres_session,
        since=now - timedelta(hours=1),
        until=now,
        source_name=source.slug,
    )

    assert report.review_item_type_counts == {ReviewItemType.NEW_CANDIDATE.value: 1}
    assert report.status_regression_agent_run_count == 0
    assert report.status_regression_review_item_count == 0
    assert report.missing_review_link_count == 0
    assert report.runs[0].review_item_type_counts == {
        ReviewItemType.NEW_CANDIDATE.value: 1,
    }


def test_news_agent_smoke_cli_prints_validation_and_failed_run_details(
    monkeypatch,
    postgres_session: Session,
    tmp_path,
) -> None:
    _ensure_status_regression_review_item_type(postgres_session)
    source = _news_source()
    now = datetime(2099, 5, 13, 21, tzinfo=UTC)
    source_run = _source_run(source, run_timestamp=now - timedelta(minutes=30))
    completed_article = _article(source, title="Completed article", fetched_at=now)
    timeout_article = _article(source, title="Timeout article", fetched_at=now)
    postgres_session.add_all([source, source_run, completed_article, timeout_article])
    postgres_session.flush()
    semantic_extraction = _news_extraction(timeout_article)
    completed_run = _agent_run(
        source_run=source_run,
        article=completed_article,
        triggered_by=["new_candidate", "status_regression_candidate"],
        outcome=AgentRunOutcome.COMPLETED.value,
        cost_usd=Decimal("0.060000"),
        created_at=now - timedelta(minutes=20),
    )
    timeout_run = _agent_run(
        source_run=source_run,
        article=timeout_article,
        triggered_by=["material_contradiction"],
        outcome=AgentRunOutcome.FAILED_TIMEOUT.value,
        cost_usd=Decimal("0.010000"),
        error_text="wallclock exceeded 300s",
        created_at=now - timedelta(minutes=10),
    )
    postgres_session.add_all([completed_run, timeout_run])
    postgres_session.flush()
    _link_review_item(postgres_session, source_run=source_run, agent_run=completed_run)
    _link_review_item(
        postgres_session,
        source_run=source_run,
        agent_run=completed_run,
        item_type=ReviewItemType.STATUS_REGRESSION_REVIEW,
    )
    _link_review_item(postgres_session, source_run=source_run, agent_run=timeout_run)
    postgres_session.add_all(
        [
            _cost_usage(
                cost_date=now.date(),
                capability="agent.news_v1",
                call_count=2,
                spent_usd=Decimal("0.070000"),
            ),
            semantic_extraction,
            _semantic_row(
                article=timeout_article,
                extraction_id=semantic_extraction.id,
                parse_status=NewsExtractionParseStatus.TRUNCATED.value,
                parse_error_text="max_tokens",
                created_at=now - timedelta(minutes=12),
            ),
            SystemAlert(
                alert_key="agent_news_v1_cost_overshoot",
                severity="warning",
                scope={"profile_name": "news_v1"},
                message="Agent cost overshot reservation.",
                raised_at=now - timedelta(minutes=9),
                last_seen_at=now - timedelta(minutes=8),
            ),
        ]
    )
    postgres_session.flush()

    @contextmanager
    def fake_session_factory():
        yield postgres_session

    monkeypatch.setattr(cli_module, "get_session_factory", lambda: fake_session_factory)
    monkeypatch.setattr(
        cli_module,
        "get_settings",
        lambda: Settings(database_url="postgresql://user:password@example.com/tcg"),
    )
    output_path = tmp_path / "news_smoke.json"

    result = runner.invoke(
        app,
        [
            "news-agent-smoke-report",
            "--source-name",
            source.slug,
            "--since",
            "2099-05-13T19:00:00Z",
            "--until",
            "2099-05-13T21:00:00Z",
            "--min-source-runs",
            "1",
            "--min-agent-runs",
            "2",
            "--require-triggers",
            "new_candidate,material_contradiction,status_regression_candidate",
            "--require-outcomes",
            "completed",
            "--allow-outcomes",
            "completed,failed_timeout",
            "--min-status-regression-review-items",
            "1",
            "--min-total-cost-usd",
            "0.05",
            "--max-total-cost-usd",
            "1.00",
            "--output",
            str(output_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Source runs: 1" in result.output
    assert "Agent runs: 2" in result.output
    assert "Outcomes: completed=1, failed_timeout=1" in result.output
    assert (
        "Triggers: material_contradiction=1, new_candidate=1, "
        "status_regression_candidate=1"
    ) in result.output
    assert "Review item types: new_candidate=2, status_regression_review=1" in result.output
    assert "Status regression agent runs: 1" in result.output
    assert "Status regression review items: 1" in result.output
    assert "News bucket total cost: $0.070000" in result.output
    assert "Cost-usage breakdown:" in result.output
    assert "agent.news_v1: $0.070000 (2 calls)" in result.output
    assert "Semantic parse statuses: truncated=1" in result.output
    assert "Semantic parse issues:" in result.output
    assert f"truncated ({timeout_article.id}): max_tokens" in result.output
    assert "Failure runs:" in result.output
    assert f"failed_timeout ({timeout_article.id}): wallclock exceeded 300s" in result.output
    assert "Recent/active news alerts:" in result.output
    report_json = output_path.read_text(encoding="utf-8")
    assert '"min_source_runs": 1' in report_json
    assert '"min_status_regression_review_items": 1' in report_json
    assert '"status_regression_agent_run_count": 1' in report_json
    assert '"status_regression_review_item_count": 1' in report_json
    assert '"max_total_cost_usd": "1.00"' in report_json
    assert '"cost_usage_by_capability": [' in report_json
    assert '"semantic_issue_count": 1' in report_json
    assert '"failures": []' in report_json


def test_news_agent_smoke_report_marks_alert_truncation(
    postgres_session: Session,
) -> None:
    source = _news_source()
    now = datetime(2099, 5, 14, 21, tzinfo=UTC)
    postgres_session.add(source)
    postgres_session.flush()
    postgres_session.add_all(
        SystemAlert(
            alert_key=f"news_test_alert_{index}",
            severity="warning",
            scope={"source_name": source.slug},
            message=f"News alert {index}.",
            raised_at=now - timedelta(minutes=index),
            last_seen_at=now - timedelta(minutes=index),
        )
        for index in range(51)
    )
    postgres_session.flush()

    report = build_news_agent_smoke_report(
        postgres_session,
        since=now - timedelta(hours=2),
        until=now,
        source_name=source.slug,
    )

    assert report.alert_count == 50
    assert report.alert_limit == 50
    assert report.alerts_truncated is True


def _news_source() -> NewsSource:
    slug = f"news_smoke_{uuid.uuid4().hex}"
    return NewsSource(
        slug=slug,
        name="News Smoke Test",
        base_url="https://example.com",
        collector_class="test",
    )


def _source_run(
    source: NewsSource,
    *,
    run_timestamp: datetime,
    records_pulled: int = 1,
) -> SourceRun:
    return SourceRun(
        market="los_angeles",
        source_name=source.slug,
        collection_mode="incremental",
        trigger_type="scheduled",
        run_timestamp=run_timestamp,
        finished_at=run_timestamp + timedelta(minutes=5),
        records_pulled=records_pulled,
        rows_inserted=records_pulled,
        rows_updated=records_pulled,
        rows_unchanged=0,
        new_matches=0,
    )


def _article(
    source: NewsSource,
    *,
    title: str = "News smoke article",
    fetched_at: datetime,
) -> NewsArticle:
    article_id = uuid.uuid4()
    return NewsArticle(
        id=article_id,
        source=source,
        url_canonical=f"https://example.com/news-smoke-{article_id}",
        url_original=f"https://example.com/news-smoke-{article_id}",
        url_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        fetch_status=NewsFetchStatus.FETCHED.value,
        triage_status=NewsTriageStatus.RELEVANT.value,
        fetched_at=fetched_at,
        title=title,
        ingest_method=source.slug,
    )


def _agent_run(
    *,
    source_run: SourceRun,
    article: NewsArticle,
    triggered_by: list[str],
    outcome: str = AgentRunOutcome.COMPLETED.value,
    cost_usd: Decimal = Decimal("0"),
    error_text: str | None = None,
    created_at: datetime,
) -> AgentRun:
    resolved_error_text = error_text
    if resolved_error_text is None and outcome == AgentRunOutcome.KILLED_BY_SWITCH.value:
        resolved_error_text = "agent_allow_live_llm=false"
    return AgentRun(
        intake_source_type=NEWS_AGENT_PROFILE.intake_source_type,
        intake_record_id=str(article.id),
        source_run_id=source_run.id,
        profile_name=NEWS_AGENT_PROFILE.name,
        profile_version=NEWS_AGENT_PROFILE.profile_version,
        triggered_by=triggered_by,
        provider=LLM_PROVIDER_ANTHROPIC,
        model=DEFAULT_EXTRACTION_MODEL,
        prompt_version=NEWS_AGENT_PROFILE.prompt_version,
        input_tokens_uncached=0,
        input_tokens_cache_creation=0,
        input_tokens_cached=0,
        output_tokens=0,
        cost_usd=cost_usd,
        latency_ms=0,
        evidence_consulted=[],
        tool_calls_summary=[],
        outcome=outcome,
        error_text=resolved_error_text,
        budget_consumed_usd=cost_usd,
        tool_calls_count=0,
        wallclock_seconds=0,
        started_at=created_at,
        completed_at=created_at,
        created_at=created_at,
    )


def _news_extraction(article: NewsArticle) -> NewsExtraction:
    return NewsExtraction(
        id=uuid.uuid4(),
        article_id=article.id,
        pass_name=NewsExtractionPass.EXTRACTION.value,
        triggered_by="smoke",
        prompt_id="extract_v2",
        prompt_version="v2",
        prompt_hash=uuid.uuid4().hex,
        model=DEFAULT_EXTRACTION_MODEL,
        output_json={"relevance": "confirmed", "project_references": [], "diagnostic": {}},
        parse_status=NewsExtractionParseStatus.OK.value,
    )


def _cost_usage(
    *,
    cost_date,
    capability: str,
    call_count: int = 1,
    spent_usd: Decimal,
) -> LLMCostUsage:
    return LLMCostUsage(
        bucket="news",
        cost_date=cost_date,
        capability=capability,
        provider=LLM_PROVIDER_ANTHROPIC,
        model=DEFAULT_EXTRACTION_MODEL,
        call_count=call_count,
        input_tokens_uncached=10 * call_count,
        input_tokens_cache_creation=20 * call_count,
        input_tokens_cached=30 * call_count,
        output_tokens=40 * call_count,
        spent_usd=spent_usd,
    )


def _semantic_row(
    *,
    article: NewsArticle,
    extraction_id: uuid.UUID,
    parse_status: str,
    parse_error_text: str | None = None,
    created_at: datetime,
) -> NewsSemanticInterpretation:
    return NewsSemanticInterpretation(
        article_id=article.id,
        extraction_id=extraction_id,
        prompt_id="interpret_v1",
        prompt_version="v1",
        prompt_hash=uuid.uuid4().hex,
        model=DEFAULT_EXTRACTION_MODEL,
        model_provider=LLM_PROVIDER_ANTHROPIC,
        output_json=None,
        parse_status=parse_status,
        parse_error_text=parse_error_text,
        created_at=created_at,
    )


def _link_review_item(
    session: Session,
    *,
    source_run: SourceRun,
    agent_run: AgentRun,
    item_type: ReviewItemType = ReviewItemType.NEW_CANDIDATE,
) -> None:
    review_item = ReviewItem(
        source_run_id=source_run.id,
        item_type=item_type,
        priority=Priority.MEDIUM,
        payload={"source_article_id": agent_run.intake_record_id},
    )
    session.add(review_item)
    session.flush()
    session.add(
        AgentRunReviewItem(
            agent_run_id=agent_run.id,
            review_item_id=review_item.id,
        )
    )

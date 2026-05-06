from __future__ import annotations

import asyncio
import json
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import delete, func, select

from tcg_pipeline.collectors.base import CollectionMode, CollectionRequest
from tcg_pipeline.collectors.factory import build_collector
from tcg_pipeline.db.collect import persist_collected_records
from tcg_pipeline.db.connection import get_session_factory, redact_database_url
from tcg_pipeline.db.models import (
    DeveloperRegistry,
    DismissReason,
    Project,
    ResearcherOverride,
    ResolutionLog,
    SourceRun,
)
from tcg_pipeline.db.review_workflow import (
    accept_review_item,
    defer_review_item,
    reject_review_item,
)
from tcg_pipeline.db.seed import (
    ingest_costar_workbooks,
    ingest_pipedream_workbooks,
    persist_costar_import_result,
    persist_pipedream_import_results,
)
from tcg_pipeline.developer import canonicalize_project_developers
from tcg_pipeline.ingesters.costar import CoStarImportResult, CoStarIngester
from tcg_pipeline.ingesters.pipedream import PipedreamImportResult, PipedreamIngester
from tcg_pipeline.market_config import get_market_config
from tcg_pipeline.resolution import resolve_project
from tcg_pipeline.resolution.engine import LOGGED_FIELDS
from tcg_pipeline.review.contradictions import (
    detect_contradictions as detect_override_contradictions,
)
from tcg_pipeline.settings import get_settings
from tcg_pipeline.status_rules import get_status_evidence_rule
from tcg_pipeline.utils.logging import configure_logging
from tcg_pipeline.workers.scrape_jobs import run_worker

app = typer.Typer(help="TCG pipeline tracker CLI.")
news_app = typer.Typer(help="News research and extraction utilities.")
app.add_typer(news_app, name="news")
COLLECTION_MODE_OPTION = typer.Option(
    CollectionMode.FULL,
    help="Collection mode: full backfill or incremental from the last source-run cursor.",
)


@app.callback()
def main() -> None:
    """Root CLI entrypoint."""
    configure_logging(get_settings().log_level)


@app.command()
def doctor() -> None:
    """Print basic environment and configuration status."""
    settings = get_settings()

    typer.echo(f"Environment: {settings.app_env}")
    typer.echo(f"Supabase URL configured: {bool(settings.supabase_url)}")
    typer.echo(f"Supabase project ref: {settings.project_ref or 'missing'}")
    typer.echo(f"Database URL configured: {settings.has_database_url}")
    typer.echo(f"Seed directory: {settings.seed_dir}")
    typer.echo(f"Output directory: {settings.output_dir}")

    if not settings.has_database_url:
        typer.echo(
            "Next required credential: DATABASE_URL from Supabase Project Settings -> Database.",
            err=True,
        )


@app.command("worker")
def worker(
    queue_name: str | None = typer.Option(
        None,
        help="RQ queue name to consume. Defaults to SCRAPE_JOB_QUEUE_NAME.",
    ),
    burst: bool = typer.Option(False, help="Exit when the queue is empty."),
) -> None:
    """Run the durable scrape-job worker."""
    run_worker(queue_name=queue_name, burst=burst)


@news_app.command("ab-extract")
def news_ab_extract(
    fixture: Annotated[
        Path,
        typer.Option(
            help="JSON fixture of article objects to run through each extraction candidate.",
        ),
    ] = Path("tests/fixtures/news/urbanize_la/pass1_validation_articles.json"),
    candidates: Annotated[
        str,
        typer.Option(
            help=(
                "Comma-separated '<provider>:<model>' candidates, e.g. "
                "'anthropic:claude-opus-4-7,anthropic:claude-sonnet-4-6,openai:gpt-5.4'."
            ),
        ),
    ] = (
        "anthropic:claude-opus-4-7,"
        "anthropic:claude-sonnet-4-6,"
        "openai:gpt-5.4"
    ),
    source_slug: Annotated[
        str,
        typer.Option(help="News source slug used for source scope and matcher defaults."),
    ] = "urbanize_la",
    output: Annotated[
        Path | None,
        typer.Option(help="Optional report path. Defaults to data/output/news/ab_extract_*.json."),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option(min=1, help="Optional maximum number of fixture articles to run."),
    ] = None,
) -> None:
    """Run the AGENT.1 default-extraction A/B harness."""
    from tcg_pipeline.news.ab_harness import (
        load_article_fixtures,
        parse_candidate_specs,
        run_extraction_ab_harness,
    )

    try:
        settings = get_settings()
        candidate_specs = parse_candidate_specs(candidates)
        fixtures = load_article_fixtures(fixture)
        article_count = len(fixtures[:limit]) if limit is not None else len(fixtures)
        database_label = (
            redact_database_url(settings.database_url)
            if settings.database_url
            else "missing DATABASE_URL"
        )
        typer.echo(
            "Running A/B harness against "
            f"{database_label} | "
            f"articles={article_count} | "
            f"candidates={len(candidate_specs)} | "
            f"planned LLM calls={article_count * len(candidate_specs)}"
        )
        report = run_extraction_ab_harness(
            fixture_path=fixture,
            candidates=candidates,
            source_slug=source_slug,
            output_path=output,
            limit=limit,
            settings=settings,
        )
    except (RuntimeError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Report: {report['output_path']}")
    for summary in report["candidate_summaries"]:
        typer.echo(
            "  "
            f"{summary['candidate']} | "
            f"articles={summary['articles']} | "
            f"parse={summary['parse_status_counts']} | "
            f"refs={summary['references']} | "
            f"agent_rate={summary['agent_trigger_rate']} | "
            f"cost=${summary['total_cost_usd']}"
        )


@news_app.command("index-articles")
def news_index_articles(
    source_slug: Annotated[
        str | None,
        typer.Option(help="Optional news source slug filter, e.g. urbanize_la."),
    ] = None,
    article_id: Annotated[
        uuid.UUID | None,
        typer.Option(help="Optional single news article UUID to index."),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option(
            min=1,
            help=(
                "Maximum accepted references to plan or index. Each reference can produce "
                "one reference chunk plus one whole-article chunk per article."
            ),
        ),
    ] = None,
    apply: Annotated[
        bool,
        typer.Option(
            "--apply",
            help="Call the embedding API and write news_article_chunks. Default is plan-only.",
        ),
    ] = False,
) -> None:
    """Plan or run AGENT.1 accepted-reference article chunk indexing."""
    from tcg_pipeline.news.embeddings import run_news_article_chunk_indexing

    try:
        result = run_news_article_chunk_indexing(
            source_slug=source_slug,
            article_id=article_id,
            limit=limit,
            apply=apply,
        )
    except RuntimeError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Apply mode: {result.apply}")
    typer.echo(f"Gated references: {result.gated_reference_count}")
    typer.echo(f"Planned chunks: {result.planned_chunk_count}")
    typer.echo(f"  Reference chunks: {result.planned_reference_chunk_count}")
    typer.echo(f"  Whole-article chunks: {result.planned_whole_article_chunk_count}")
    if result.apply:
        typer.echo(f"Indexed chunks: {result.indexed_chunk_count}")
        typer.echo(f"Skipped unchanged chunks: {result.skipped_unchanged_chunk_count}")
        typer.echo(f"Superseded active chunks: {result.superseded_chunk_count}")
        typer.echo(f"Embedding calls: {result.embedding_call_count}")
        typer.echo(f"Embedding input tokens: {result.input_tokens}")
        typer.echo(f"Embedding cost: ${result.cost_usd}")
    if result.skipped_reason:
        typer.echo(f"Skipped reason: {result.skipped_reason}", err=True)
        raise typer.Exit(1)


@news_app.command("paste-link-smoke")
def news_paste_link_smoke(
    url: Annotated[str, typer.Argument(help="Absolute news article URL to ingest.")],
    note: Annotated[
        str | None,
        typer.Option(help="Optional note stored on the news article."),
    ] = "CLI paste-link smoke.",
    force_project_id: Annotated[
        uuid.UUID | None,
        typer.Option(help="Optional project UUID hint for single-reference matcher tests."),
    ] = None,
) -> None:
    """Run the paste-a-link pipeline without using the frontend."""
    from fastapi import HTTPException

    from tcg_pipeline.api.auth import AuthenticatedUser
    from tcg_pipeline.api.routers.research import enqueue_paste_a_link_article
    from tcg_pipeline.api.schemas import ResearchArticleCreateRequest
    from tcg_pipeline.db.models import (
        AgentRun,
        NewsArticle,
        NewsProjectReference,
        ReviewItem,
        ScrapeJob,
    )
    from tcg_pipeline.workers.news_jobs import run_news_paste_a_link_job

    settings = get_settings()
    database_label = (
        redact_database_url(settings.database_url)
        if settings.database_url
        else "missing DATABASE_URL"
    )
    typer.echo(
        "Running paste-link smoke against "
        f"{database_label}"
    )
    typer.echo(
        "Agent flags: "
        f"AGENT_ENABLED_FOR_NEWS={settings.agent_enabled_for_news} | "
        f"AGENT_ALLOW_LIVE_LLM={settings.agent_allow_live_llm} | "
        f"NEWS_USE_LEGACY_PASS3={settings.news_use_legacy_pass3}"
    )

    payload = ResearchArticleCreateRequest(
        url=url,
        force_project_id=force_project_id,
        note=note,
    )
    user = AuthenticatedUser(
        user_id=uuid.UUID(int=0),
        email="codex-smoke@local",
        role="service_role",
        claims={"sub": str(uuid.UUID(int=0)), "email": "codex-smoke@local"},
    )

    try:
        with get_session_factory()() as session:
            article, job, existing_article = enqueue_paste_a_link_article(
                session,
                payload=payload,
                user=user,
            )
            session.commit()
            article_id = article.id
            job_id = job.id if job else None
            typer.echo(f"Article: {article_id}")
            typer.echo(f"Existing article: {existing_article}")
            if job_id is None:
                typer.echo("No new scrape job was created.")
                return
            typer.echo(f"Scrape job: {job_id}")

        run_news_paste_a_link_job(job_id)

        with get_session_factory()() as session:
            article = session.get(NewsArticle, article_id)
            job = session.get(ScrapeJob, job_id)
            references = session.execute(
                select(NewsProjectReference).where(NewsProjectReference.article_id == article_id)
            ).scalars().all()
            agent_runs = session.execute(
                select(AgentRun)
                .where(AgentRun.intake_record_id == str(article_id))
                .order_by(AgentRun.started_at.desc())
            ).scalars().all()
            review_items = session.execute(
                select(ReviewItem).where(
                    ReviewItem.payload["source_article_id"].astext == str(article_id)
                )
            ).scalars().all()

            typer.echo(f"Job status: {job.status if job else 'missing'}")
            if job and job.error_text:
                typer.echo(f"Job error: {job.error_text}", err=True)
            if article is not None:
                typer.echo(f"Title: {article.title or 'untitled'}")
                typer.echo(f"Fetch status: {article.fetch_status}")
                typer.echo(f"Triage status: {article.triage_status}")
                typer.echo(
                    "Current extraction: "
                    f"{article.current_extraction_id or 'none'} "
                    f"(version {article.current_extraction_version})"
                )
            typer.echo(f"References: {len(references)}")
            typer.echo(f"Review items: {len(review_items)}")
            typer.echo(f"Agent runs: {len(agent_runs)}")
            for run in agent_runs[:3]:
                typer.echo(
                    "  "
                    f"{run.id} | outcome={run.outcome} | "
                    f"decision={run.agent_revised_verdict} | cost=${run.cost_usd}"
                )
    except HTTPException as exc:
        typer.echo(f"Error: {exc.detail}", err=True)
        raise typer.Exit(1) from exc
    except RuntimeError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc


@app.command()
def preview_pipedream(
    workbook_path: Path,
    market: str = typer.Option(..., help="Market slug, e.g. los_angeles."),
    source_name: str = typer.Option("pipedream", help="Source name stored on source records."),
    allowed_city: str | None = typer.Option(
        None,
        help="Optional city filter for import preview, e.g. 'Los Angeles'.",
    ),
) -> None:
    """Preview a Pipedream workbook import without writing to the database."""
    allowed_cities = [allowed_city] if allowed_city else None
    ingester = PipedreamIngester(
        market=market,
        source_name=source_name,
        allowed_cities=allowed_cities,
    )
    result = ingester.ingest_workbook(workbook_path)

    typer.echo(f"Workbook: {workbook_path}")
    typer.echo(f"Imported projects: {result.imported_count}")
    typer.echo(f"Dismissed records: {result.dismissed_count}")
    typer.echo(f"Staged relationships: {len(result.staged_relationships)}")
    typer.echo(f"Skipped project ids: {len(result.skipped_project_ids)}")
    typer.echo(f"Missing ProjectID rows: {result.missing_project_id_rows}")
    typer.echo(f"Issues: {result.issue_count}")
    for issue_type, count in sorted(result.issue_counts.items()):
        typer.echo(f"  {issue_type}: {count}")


@app.command()
def seed_pipedream(
    workbook_paths: list[Path],
    market: str = typer.Option(..., help="Market slug, e.g. los_angeles."),
    source_name: str = typer.Option("pipedream", help="Source name stored on source records."),
    allowed_city: Annotated[
        list[str] | None,
        typer.Option(help="Optional city filter; may be provided multiple times."),
    ] = None,
    dry_run: bool = typer.Option(
        False,
        help="Preview import results without writing to the database.",
    ),
) -> None:
    """Ingest one or more Pipedream workbooks and optionally persist them."""
    import_results = ingest_pipedream_workbooks(
        workbook_paths,
        market=market,
        source_name=source_name,
        allowed_cities=allowed_city or None,
    )
    _echo_pipedream_import_summary(import_results)

    if dry_run:
        return

    session_factory = get_session_factory()
    with session_factory() as session:
        _echo_developer_registry_bootstrap_warning_if_needed(session)
        persist_result = persist_pipedream_import_results(session, import_results)
        session.commit()

    typer.echo(f"Persisted projects: {persist_result.inserted_projects}")
    typer.echo(f"Persisted dismissed records: {persist_result.inserted_dismissed_records}")
    typer.echo(f"Skipped existing projects: {persist_result.skipped_existing_project_count}")
    typer.echo(
        "Skipped existing dismissed records: "
        f"{persist_result.skipped_existing_dismissed_count}"
    )
    typer.echo(f"Created relationships: {persist_result.created_relationships}")
    typer.echo(f"Skipped existing relationships: {persist_result.skipped_existing_relationships}")
    typer.echo(f"Unresolved relationships: {persist_result.unresolved_relationship_count}")


@app.command()
def preview_costar(
    workbook_paths: list[Path],
    market: str = typer.Option(..., help="Market slug, e.g. los_angeles."),
    source_name: str = typer.Option("costar", help="Source name stored on source records."),
    allowed_city: Annotated[
        list[str] | None,
        typer.Option(help="Optional city filter; may be provided multiple times."),
    ] = None,
) -> None:
    """Preview one or more CoStar workbooks without writing to the database."""
    resolved_workbook_paths = _expand_workbook_inputs(workbook_paths, suffix=".xlsx")
    if not resolved_workbook_paths:
        raise typer.BadParameter("No .xlsx CoStar workbooks found in the provided paths.")

    ingester = CoStarIngester(
        market=market,
        source_name=source_name,
        allowed_cities=allowed_city or None,
    )
    result = ingester.ingest_workbooks(resolved_workbook_paths)
    _echo_costar_import_summary(result)


@app.command()
def seed_costar(
    workbook_paths: list[Path],
    market: str = typer.Option(..., help="Market slug, e.g. los_angeles."),
    source_name: str = typer.Option("costar", help="Source name stored on source records."),
    allowed_city: Annotated[
        list[str] | None,
        typer.Option(help="Optional city filter; may be provided multiple times."),
    ] = None,
    dry_run: bool = typer.Option(
        False,
        help="Preview import results without writing to the database.",
    ),
) -> None:
    """Ingest one or more CoStar workbooks and optionally persist them."""
    resolved_workbook_paths = _expand_workbook_inputs(workbook_paths, suffix=".xlsx")
    if not resolved_workbook_paths:
        raise typer.BadParameter("No .xlsx CoStar workbooks found in the provided paths.")

    import_result = ingest_costar_workbooks(
        resolved_workbook_paths,
        market=market,
        source_name=source_name,
        allowed_cities=allowed_city or None,
    )
    _echo_costar_import_summary(import_result)

    if dry_run:
        return

    session_factory = get_session_factory()
    with session_factory() as session:
        _echo_developer_registry_bootstrap_warning_if_needed(session)
        persist_result = persist_costar_import_result(session, import_result)
        session.commit()

    typer.echo(f"Persisted new projects: {persist_result.inserted_projects}")
    typer.echo(f"Matched existing projects: {persist_result.matched_existing_projects}")
    typer.echo(f"Matched by CoStar property id: {persist_result.matched_by_costar_property_id}")
    typer.echo(f"Matched by APN: {persist_result.matched_by_apn}")
    typer.echo(f"Matched by address: {persist_result.matched_by_address}")
    typer.echo(f"Inserted identifiers: {persist_result.inserted_identifiers}")
    typer.echo(f"Skipped existing identifiers: {persist_result.skipped_existing_identifiers}")
    typer.echo(f"Inserted source records: {persist_result.inserted_source_records}")
    typer.echo(f"Updated source records: {persist_result.updated_source_records}")
    typer.echo(f"Inserted status history entries: {persist_result.inserted_status_history_entries}")
    typer.echo(
        "Skipped existing status history entries: "
        f"{persist_result.skipped_existing_status_history_entries}"
    )
    typer.echo(f"Merged fields: {persist_result.merged_fields}")
    typer.echo(f"Ambiguous matches: {persist_result.ambiguous_match_count}")


@app.command()
def preview_source(
    source_name: str,
    market: str = typer.Option(..., help="Market slug, e.g. los_angeles."),
    limit: int = typer.Option(5, min=1, help="Maximum records to collect for the preview."),
) -> None:
    """Collect a small preview batch from a configured public source."""
    source_config = get_market_config(market).get_source(source_name).model_copy(
        update={"max_records": limit}
    )
    collector = build_collector(source_config, market=market)
    raw_records = asyncio.run(
        collector.collect(CollectionRequest(mode=CollectionMode.PREVIEW))
    )

    typer.echo(f"Market: {market}")
    typer.echo(f"Source: {source_name}")
    typer.echo(f"Mode: {CollectionMode.PREVIEW.value}")
    typer.echo(f"Collected records: {len(raw_records)}")
    for raw_record in raw_records[: min(limit, len(raw_records))]:
        direct_status = raw_record.mapped_fields.get("pipeline_status")
        suggested_status = None
        evidence_type = raw_record.mapped_fields.get("status_evidence_type")
        evidence_rule = get_status_evidence_rule(str(evidence_type) if evidence_type else None)
        if evidence_rule is not None:
            suggested_status = evidence_rule.suggested_status.value
        status_label = direct_status or suggested_status or "n/a"
        status_prefix = "status" if direct_status else "suggested_status"
        typer.echo(
            "  "
            f"{raw_record.source_record_id} | "
            f"{raw_record.canonical_address or 'NO_ADDRESS'} | "
            f"units={raw_record.mapped_fields.get('total_units', 'n/a')} | "
            f"{status_prefix}={status_label}"
        )


@app.command()
def collect_source(
    source_name: str,
    market: str = typer.Option(..., help="Market slug, e.g. los_angeles."),
    mode: CollectionMode = COLLECTION_MODE_OPTION,
    updated_since: str | None = typer.Option(
        None,
        help="Optional explicit lower bound for incremental mode, in ISO-8601 format.",
    ),
    limit: int | None = typer.Option(
        None,
        min=1,
        help="Optional max record count to collect for this run.",
    ),
    overlap_hours: int | None = typer.Option(
        None,
        min=0,
        help="Optional override for the incremental overlap window. Defaults to the source config.",
    ),
    dry_run: bool = typer.Option(
        False,
        help="Collect and summarize without writing to the database.",
    ),
) -> None:
    """Collect a configured public source and persist the first-pass review artifacts."""
    source_config = get_market_config(market).get_source(source_name)
    if limit is not None:
        source_config = source_config.model_copy(update={"max_records": limit})

    if updated_since is not None and mode != CollectionMode.INCREMENTAL:
        raise typer.BadParameter("--updated-since can only be used with --mode incremental.")

    request = CollectionRequest(mode=mode)
    if mode == CollectionMode.INCREMENTAL:
        if updated_since is not None:
            request.updated_since = _parse_cli_datetime(updated_since)
        else:
            effective_overlap_hours = (
                overlap_hours
                if overlap_hours is not None
                else source_config.incremental_overlap_hours
            )
            session_factory = get_session_factory()
            with session_factory() as session:
                request.updated_since = _resolve_incremental_cursor(
                    session,
                    market=market,
                    source_name=source_name,
                    overlap_hours=effective_overlap_hours,
                )
        if request.updated_since is None:
            typer.echo(
                "No prior source-run cursor metadata found; falling back to full collection mode."
            )
            request.mode = CollectionMode.FULL

    collector = build_collector(source_config, market=market)
    raw_records = asyncio.run(collector.collect(request))

    typer.echo(f"Market: {market}")
    typer.echo(f"Source: {source_name}")
    typer.echo(f"Mode: {request.mode.value}")
    if request.updated_since is not None:
        typer.echo(f"Incremental since: {request.updated_since.isoformat()}")
    typer.echo(f"Collected records: {len(raw_records)}")

    if dry_run:
        return

    session_factory = get_session_factory()
    with session_factory() as session:
        _echo_developer_registry_bootstrap_warning_if_needed(session)
        persist_result = persist_collected_records(
            session,
            market=market,
            source_name=source_name,
            raw_records=raw_records,
            collection_mode=request.mode.value,
            incremental_since=request.updated_since,
            create_new_candidates=source_config.create_new_candidates,
        )
        session.commit()

    typer.echo(f"Source run id: {persist_result.source_run_id}")
    if persist_result.source_min_updated_at is not None:
        typer.echo(f"Source min updated_at: {persist_result.source_min_updated_at.isoformat()}")
    if persist_result.source_max_updated_at is not None:
        typer.echo(f"Source max updated_at: {persist_result.source_max_updated_at.isoformat()}")
    typer.echo(f"Matched existing projects: {persist_result.matched_existing_projects}")
    typer.echo(f"Matched by source record: {persist_result.matched_by_source_record}")
    typer.echo(f"Matched by identifier: {persist_result.matched_by_identifier}")
    typer.echo(f"Matched by address: {persist_result.matched_by_address}")
    typer.echo(f"Inserted source records: {persist_result.inserted_source_records}")
    typer.echo(f"Updated source records: {persist_result.updated_source_records}")
    typer.echo(f"Unchanged source records: {persist_result.unchanged_source_records}")
    typer.echo(f"Inserted identifiers: {persist_result.inserted_identifiers}")
    typer.echo(f"New candidate review items: {persist_result.new_candidate_review_items}")
    typer.echo(
        "Suppressed new candidate records: "
        f"{persist_result.suppressed_new_candidate_records}"
    )
    typer.echo(
        "Dismissed discovery records skipped: "
        f"{persist_result.dismissed_discovery_records_skipped}"
    )
    typer.echo(f"Status change review items: {persist_result.status_change_review_items}")
    typer.echo(f"Possible match review items: {persist_result.possible_match_review_items}")


@app.command("review-accept")
def review_accept_command(
    review_item_id: Annotated[uuid.UUID, typer.Option(help="Review item UUID.")],
    actor: Annotated[str, typer.Option(help="Researcher or operator name.")],
    project_id: Annotated[
        uuid.UUID | None,
        typer.Option(
            help="Existing project UUID to accept into.",
        ),
    ] = None,
    create_new: Annotated[
        bool,
        typer.Option(
            help="Create a new project and accept the review item into it.",
        ),
    ] = False,
    notes: Annotated[
        str | None,
        typer.Option(
            help="Optional decision notes.",
        ),
    ] = None,
    field_overrides_json: Annotated[
        str | None,
        typer.Option(
            help="Optional JSON object to write as researcher_overrides before resolve.",
        ),
    ] = None,
    canonical_address: Annotated[
        str | None,
        typer.Option(
            help="Required when --create-new and the review payload lacks canonical_address.",
        ),
    ] = None,
    city: Annotated[
        str | None,
        typer.Option(
            help="Required when --create-new and the review payload lacks city.",
        ),
    ] = None,
    state: Annotated[
        str | None,
        typer.Option(
            help="Required when --create-new and the review payload lacks state.",
        ),
    ] = None,
    county: Annotated[
        str | None,
        typer.Option(
            help="Required when --create-new and the review payload lacks county.",
        ),
    ] = None,
    zip_code: Annotated[
        str | None,
        typer.Option(
            "--zip",
            help="Optional zip code for --create-new.",
        ),
    ] = None,
    project_name: Annotated[
        str | None,
        typer.Option(
            help="Optional project name for --create-new.",
        ),
    ] = None,
) -> None:
    """Accept a discovery review item into an existing or newly created project."""
    if create_new == (project_id is not None):
        raise typer.BadParameter("Provide exactly one of --project-id or --create-new.")

    field_overrides = _parse_json_mapping_option(
        field_overrides_json,
        option_name="--field-overrides-json",
    )
    new_project_data = {
        "canonical_address": canonical_address,
        "city": city,
        "state": state,
        "county": county,
        "zip": zip_code,
        "project_name": project_name,
    }

    session_factory = get_session_factory()
    with session_factory() as session:
        result = accept_review_item(
            session,
            review_item_id=review_item_id,
            actor=actor,
            project_id=project_id,
            create_new=create_new,
            notes=notes,
            field_overrides=field_overrides,
            new_project_data=new_project_data,
        )
        session.commit()

    typer.echo(f"Review item: {result.review_item_id}")
    typer.echo(f"Action: {result.action.value}")
    typer.echo(f"Project id: {result.project_id}")
    typer.echo(f"Linked evidence rows: {result.linked_evidence_count}")
    typer.echo(f"Source record created: {result.source_record_created}")
    typer.echo(f"Source record updated: {result.source_record_updated}")
    typer.echo(f"Identifiers inserted: {result.identifiers_inserted}")
    typer.echo(f"Identifier conflicts: {len(result.identifier_conflicts)}")
    for conflict in result.identifier_conflicts:
        typer.echo(
            "  "
            f"{conflict.identifier_type.value}:{conflict.value} already belongs to "
            f"{conflict.owner_project_id}"
        )
    typer.echo(f"Change log rows created: {result.change_log_entries_created}")
    typer.echo(f"Follow-up review items created: {result.follow_up_review_items_created}")


@app.command("review-reject")
def review_reject_command(
    review_item_id: Annotated[uuid.UUID, typer.Option(help="Review item UUID.")],
    actor: Annotated[str, typer.Option(help="Researcher or operator name.")],
    notes: Annotated[
        str | None,
        typer.Option(help="Optional decision notes."),
    ] = None,
    reason: Annotated[
        DismissReason,
        typer.Option(help="Dismissal reason for discovery review items."),
    ] = DismissReason.OTHER,
) -> None:
    """Reject a review item and dismiss future discovery resurfacing for that source record."""
    session_factory = get_session_factory()
    with session_factory() as session:
        result = reject_review_item(
            session,
            review_item_id=review_item_id,
            actor=actor,
            notes=notes,
            reason=reason,
        )
        session.commit()

    typer.echo(f"Review item: {result.review_item_id}")
    typer.echo(f"Action: {result.action.value}")


@app.command("review-defer")
def review_defer_command(
    review_item_id: Annotated[uuid.UUID, typer.Option(help="Review item UUID.")],
    actor: Annotated[str, typer.Option(help="Researcher or operator name.")],
    notes: Annotated[
        str | None,
        typer.Option(help="Optional decision notes."),
    ] = None,
) -> None:
    """Defer a review item without linking or dismissing it."""
    session_factory = get_session_factory()
    with session_factory() as session:
        result = defer_review_item(
            session,
            review_item_id=review_item_id,
            actor=actor,
            notes=notes,
        )
        session.commit()

    typer.echo(f"Review item: {result.review_item_id}")
    typer.echo(f"Action: {result.action.value}")


@app.command("resolve-all")
def resolve_all_command(
    market: str | None = typer.Option(
        None,
        help="Optional market slug filter, e.g. los_angeles.",
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply resolved values to the project table instead of shadow-mode logging only.",
    ),
    clear_log: bool = typer.Option(
        False,
        help="Delete existing resolution_log rows before running.",
    ),
    limit: int | None = typer.Option(
        None,
        min=1,
        help="Optional max project count to resolve.",
    ),
    batch_size: int = typer.Option(
        100,
        min=1,
        help="Projects to resolve per transaction.",
    ),
    start_after: Annotated[
        uuid.UUID | None,
        typer.Option(help="Resume after this Project.id using keyset pagination."),
    ] = None,
) -> None:
    """Resolve projects from evidence in shadow-mode or apply mode."""
    resolve_all(
        market=market,
        apply=apply,
        clear_log=clear_log,
        limit=limit,
        batch_size=batch_size,
        start_after=start_after,
    )


def resolve_all(
    *,
    market: str | None = None,
    apply: bool = False,
    clear_log: bool = False,
    limit: int | None = None,
    batch_size: int = 100,
    start_after: uuid.UUID | None = None,
) -> None:
    """Resolve projects from evidence in shadow-mode or apply mode."""
    session_factory = get_session_factory()
    with session_factory() as session:
        _echo_developer_registry_bootstrap_warning_if_needed(session)
        if clear_log:
            _clear_resolution_log(session, market=market)
        session.commit()

    total_projects = 0
    total_changed_fields = 0
    total_log_rows = 0
    changed_projects = 0
    field_counts: Counter[str] = Counter()
    resolution_confidence_counts: Counter[str] = Counter()
    project_confidence_counts: Counter[str] = Counter()
    last_project_id = start_after
    batch_number = 0

    with session_factory() as session:
        project_ids_to_process = _fetch_project_ids(
            session,
            market=market,
            after_project_id=start_after,
            limit=limit,
        )

    for start_index in range(0, len(project_ids_to_process), batch_size):
        project_ids = project_ids_to_process[start_index : start_index + batch_size]
        if not project_ids:
            break
        with session_factory() as session:
            batch_number += 1
            batch_changed_projects = 0
            batch_changed_fields = 0
            batch_log_rows = 0
            project_confidence_by_id = dict(
                session.execute(
                    select(Project.id, Project.confidence).where(Project.id.in_(project_ids))
                ).all()
            )

            try:
                for project_id in project_ids:
                    session.execute(
                        delete(ResolutionLog).where(ResolutionLog.project_id == project_id)
                    )
                    result = resolve_project(
                        project_id,
                        session,
                        apply=apply,
                        write_resolution_log=True,
                    )
                    if result.changed_fields:
                        changed_projects += 1
                        batch_changed_projects += 1
                        project_confidence = project_confidence_by_id.get(project_id)
                        project_confidence_counts[
                            _confidence_label(project_confidence)
                        ] += 1

                    total_changed_fields += len(result.changed_fields)
                    batch_changed_fields += len(result.changed_fields)
                    total_log_rows += result.log_entries_created
                    batch_log_rows += result.log_entries_created
                    for field_name in result.changed_fields:
                        if field_name not in LOGGED_FIELDS:
                            continue
                        field_counts[field_name] += 1
                        resolution = result.field_resolutions[field_name]
                        resolution_confidence_counts[
                            _confidence_label(resolution.confidence)
                        ] += 1

                session.commit()
            except Exception:
                session.rollback()
                raise

            total_projects += len(project_ids)
            last_project_id = project_ids[-1]
            typer.echo(
                f"Batch {batch_number}: projects={len(project_ids)} "
                f"discrepancies={batch_changed_projects} "
                f"changed_fields={batch_changed_fields} "
                f"log_rows={batch_log_rows} "
                f"last_project_id={last_project_id}"
            )

    typer.echo(f"Projects resolved: {total_projects}")
    typer.echo(f"Projects with discrepancies: {changed_projects}")
    typer.echo(f"Changed fields detected: {total_changed_fields}")
    typer.echo(f"Resolution log rows written: {total_log_rows}")
    typer.echo(f"Last project id: {last_project_id or 'n/a'}")
    _echo_counter("Changed field counts", field_counts)
    _echo_counter("Resolution confidence counts", resolution_confidence_counts)
    _echo_counter("Current project confidence counts", project_confidence_counts)
    typer.echo(f"Apply mode: {apply}")
    if not apply:
        typer.echo(
            "Shadow mode note: resolution_log stores computed canonical developer values "
            "even though developer registry rows and aliases are not persisted."
        )


@app.command("detect-contradictions")
def detect_contradictions_command(
    market: str | None = typer.Option(
        None,
        help="Optional market slug filter, e.g. los_angeles.",
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Commit detected contradiction review items. Defaults to dry-run rollback.",
    ),
    limit: int | None = typer.Option(
        None,
        min=1,
        help="Optional max project count to scan.",
    ),
    batch_size: int = typer.Option(
        100,
        min=1,
        help="Projects to scan per transaction.",
    ),
    start_after: Annotated[
        uuid.UUID | None,
        typer.Option(help="Resume after this Project.id using keyset pagination."),
    ] = None,
    only_with_overrides: bool = typer.Option(
        False,
        "--only-with-overrides",
        "--only-projects-with-active-overrides",
        help="Scan only projects with at least one active researcher override.",
    ),
) -> None:
    """Detect override contradictions in dry-run or apply mode."""
    detect_contradictions_for_projects(
        market=market,
        apply=apply,
        limit=limit,
        batch_size=batch_size,
        start_after=start_after,
        only_with_overrides=only_with_overrides,
    )


def detect_contradictions_for_projects(
    *,
    market: str | None = None,
    apply: bool = False,
    limit: int | None = None,
    batch_size: int = 100,
    start_after: uuid.UUID | None = None,
    only_with_overrides: bool = False,
) -> None:
    """Detect override contradictions in dry-run or apply mode."""
    session_factory = get_session_factory()
    with session_factory() as session:
        project_ids_to_process = _fetch_project_ids(
            session,
            market=market,
            after_project_id=start_after,
            limit=limit,
            only_with_active_overrides=only_with_overrides,
        )

    total_projects = 0
    total_created = 0
    total_updated = 0
    total_invalidated = 0
    last_project_id = start_after
    batch_number = 0

    for start_index in range(0, len(project_ids_to_process), batch_size):
        project_ids = project_ids_to_process[start_index : start_index + batch_size]
        if not project_ids:
            break
        with session_factory() as session:
            batch_number += 1
            try:
                result = detect_override_contradictions(session, project_ids)
                session.flush()
                if apply:
                    session.commit()
                else:
                    session.rollback()
            except Exception:
                session.rollback()
                raise

        total_projects += len(project_ids)
        total_created += result.created_count
        total_updated += result.updated_count
        total_invalidated += result.invalidated_count
        last_project_id = project_ids[-1]
        typer.echo(
            f"Batch {batch_number}: projects={len(project_ids)} "
            f"created={result.created_count} "
            f"updated={result.updated_count} "
            f"invalidated={result.invalidated_count} "
            f"last_project_id={last_project_id}"
        )

    typer.echo(f"Projects scanned: {total_projects}")
    typer.echo(f"Contradiction review items created: {total_created}")
    typer.echo(f"Contradiction review items updated: {total_updated}")
    typer.echo(f"Contradiction review items invalidated: {total_invalidated}")
    typer.echo(f"Last project id: {last_project_id or 'n/a'}")
    typer.echo(f"Apply mode: {apply}")


def _clear_resolution_log(session, *, market: str | None) -> None:
    if market is None:
        session.execute(delete(ResolutionLog))
        return

    project_ids = select(Project.id).where(Project.market == market)
    session.execute(delete(ResolutionLog).where(ResolutionLog.project_id.in_(project_ids)))


def _fetch_project_ids(
    session,
    *,
    market: str | None,
    after_project_id: uuid.UUID | None,
    limit: int | None,
    only_with_active_overrides: bool = False,
) -> list[uuid.UUID]:
    statement = select(Project.id).order_by(Project.id)
    if only_with_active_overrides:
        statement = (
            statement.join(
                ResearcherOverride,
                ResearcherOverride.project_id == Project.id,
            )
            .where(ResearcherOverride.cleared_at.is_(None))
            .distinct()
        )
    if market is not None:
        statement = statement.where(Project.market == market)
    if after_project_id is not None:
        statement = statement.where(Project.id > after_project_id)
    if limit is not None:
        statement = statement.limit(limit)
    return list(session.execute(statement).scalars().all())


def _confidence_label(value) -> str:
    if value is None:
        return "unknown"
    return getattr(value, "value", str(value))


def _echo_counter(title: str, counts: Counter[str]) -> None:
    if not counts:
        typer.echo(f"{title}: none")
        return
    typer.echo(f"{title}:")
    for key, count in sorted(counts.items()):
        typer.echo(f"  {key}: {count}")


@app.command("canonicalize-developers")
def canonicalize_developers_command(
    market: str | None = typer.Option(
        None,
        help="Optional market slug filter, e.g. los_angeles.",
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply canonical developer names, alias merges, and registry updates.",
    ),
    limit: int | None = typer.Option(
        None,
        min=1,
        help="Optional max project count to canonicalize.",
    ),
) -> None:
    """Canonicalize developer names across the registry and project table."""
    canonicalize_developers(
        market=market,
        apply=apply,
        limit=limit,
    )


def canonicalize_developers(
    *,
    market: str | None = None,
    apply: bool = False,
    limit: int | None = None,
) -> None:
    """Canonicalize developer names across the registry and project table."""
    session_factory = get_session_factory()
    with session_factory() as session:
        initial_registry_empty = _developer_registry_is_empty(session)
        result = canonicalize_project_developers(
            session,
            market=market,
            apply=apply,
            limit=limit,
        )
        session.commit()

    typer.echo(f"Registry rows scanned: {result.registry_rows_scanned}")
    typer.echo(f"Registry rows merged: {result.registry_rows_merged}")
    typer.echo(f"Registry rows created: {result.registry_rows_created}")
    typer.echo(f"Aliases created: {result.aliases_created}")
    typer.echo(f"Projects scanned: {result.projects_scanned}")
    typer.echo(f"Projects changed: {result.projects_changed}")
    typer.echo(f"Exact matches: {result.exact_matches}")
    typer.echo(f"Fuzzy auto matches: {result.fuzzy_auto_matches}")
    typer.echo(f"Fuzzy review matches: {result.fuzzy_review_matches}")
    typer.echo(f"New registry entries: {result.new_registry_entries}")
    typer.echo(f"Apply mode: {apply}")
    if result.registry_rows_merged > 0:
        typer.echo(
            "Note: registry duplicates were merged during this sweep. Review merge counts "
            "before re-running after manual registry edits."
        )
    if not apply:
        typer.echo(
            "Shadow mode note: canonical developer targets are computed, but registry rows, "
            "aliases, and project developer values are not persisted until --apply."
        )
    if initial_registry_empty and result.projects_scanned > 0:
        typer.echo(
            "Developer registry bootstrap note: run `python scripts/backfill_developers.py` "
            "and then `python -m tcg_pipeline canonicalize-developers --apply` before "
            "normal collector or seed runs."
        )


def _resolve_incremental_cursor(
    session,
    *,
    market: str,
    source_name: str,
    overlap_hours: int,
):
    max_seen_updated_at = session.execute(
        select(func.max(SourceRun.source_max_updated_at)).where(
            SourceRun.market == market,
            SourceRun.source_name == source_name,
            SourceRun.source_max_updated_at.is_not(None),
        )
    ).scalar_one()
    if max_seen_updated_at is None:
        return None
    return max_seen_updated_at - timedelta(hours=overlap_hours)


def _developer_registry_is_empty(session) -> bool:
    return (
        session.execute(select(DeveloperRegistry.id).limit(1)).scalar_one_or_none()
        is None
    )


def _echo_developer_registry_bootstrap_warning_if_needed(session) -> None:
    if not _developer_registry_is_empty(session):
        return
    typer.echo(
        "Developer registry is empty. Bootstrap before normal collector or seed runs: "
        "`alembic upgrade head`, `python scripts/backfill_developers.py`, then "
        "`python -m tcg_pipeline canonicalize-developers --apply`."
    )


def _parse_cli_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise typer.BadParameter(
            f"Invalid datetime '{value}'. Use ISO-8601 format, for example 2026-04-16T00:00:00Z."
        ) from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _parse_json_mapping_option(
    value: str | None,
    *,
    option_name: str,
) -> dict[str, object] | None:
    if value is None:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"{option_name} must be valid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise typer.BadParameter(f"{option_name} must decode to a JSON object.")
    return parsed


def _echo_pipedream_import_summary(import_results: list[PipedreamImportResult]) -> None:
    total_imported = sum(result.imported_count for result in import_results)
    total_dismissed = sum(result.dismissed_count for result in import_results)
    total_relationships = sum(len(result.staged_relationships) for result in import_results)
    total_skipped = sum(len(result.skipped_project_ids) for result in import_results)
    total_missing_project_id = sum(result.missing_project_id_rows for result in import_results)
    total_issues = sum(result.issue_count for result in import_results)

    typer.echo(f"Workbooks: {len(import_results)}")
    typer.echo(f"Imported projects: {total_imported}")
    typer.echo(f"Dismissed records: {total_dismissed}")
    typer.echo(f"Staged relationships: {total_relationships}")
    typer.echo(f"Skipped project ids: {total_skipped}")
    typer.echo(f"Missing ProjectID rows: {total_missing_project_id}")
    typer.echo(f"Issues: {total_issues}")

    aggregated_issue_counts: dict[str, int] = {}
    for result in import_results:
        for issue_type, count in result.issue_counts.items():
            aggregated_issue_counts[issue_type] = aggregated_issue_counts.get(issue_type, 0) + count

    for issue_type, count in sorted(aggregated_issue_counts.items()):
        typer.echo(f"  {issue_type}: {count}")


def _echo_costar_import_summary(result: CoStarImportResult) -> None:
    typer.echo(f"Workbooks: {len(result.source_paths)}")
    typer.echo(f"Imported projects: {result.imported_count}")
    typer.echo(f"Duplicate property ids: {result.duplicate_count}")
    typer.echo(f"Skipped property ids: {len(result.skipped_property_ids)}")
    typer.echo(f"Missing PropertyID rows: {result.missing_property_id_rows}")
    typer.echo(f"Issues: {result.issue_count}")
    for issue_type, count in sorted(result.issue_counts.items()):
        typer.echo(f"  {issue_type}: {count}")


def _expand_workbook_inputs(paths: list[Path], *, suffix: str) -> list[Path]:
    expanded_paths: list[Path] = []
    for path in paths:
        if path.is_dir():
            expanded_paths.extend(
                sorted(
                    child
                    for child in path.glob(f"*{suffix}")
                    if child.is_file()
                )
            )
            continue
        expanded_paths.append(path)
    return expanded_paths

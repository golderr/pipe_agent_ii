from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from tcg_pipeline.cli import app
from tcg_pipeline.db.models import (
    LLMCostUsage,
    NewsArticle,
    NewsArticleChunk,
    NewsExtraction,
    NewsExtractionPass,
    NewsFetchStatus,
    NewsProjectReference,
    NewsReferenceAutoApplied,
    NewsSource,
    NewsTriageStatus,
    Priority,
    ReviewDecision,
    ReviewDecisionAction,
    ReviewItem,
    ReviewItemStatus,
    ReviewItemType,
)
from tcg_pipeline.news import embeddings
from tcg_pipeline.news.embeddings import (
    GATE_AUTO_APPLIED_CORROBORATING,
    GATE_REVIEW_ACCEPT,
    EmbeddingResponse,
    GatedNewsReference,
    NewsArticleChunkIndexResult,
    NewsArticleChunkSpec,
    OpenAINewsEmbeddingClient,
    build_news_article_chunk_specs,
    calculate_embedding_cost_usd,
    load_gated_news_references,
    persist_news_article_chunk_embeddings,
    run_news_article_chunk_indexing,
)
from tcg_pipeline.settings import get_settings

runner = CliRunner()


def test_openai_embedding_client_posts_embeddings_request() -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(
            {
                "url": str(request.url),
                "authorization": request.headers.get("Authorization"),
                "body": json.loads(request.content),
            }
        )
        return httpx.Response(
            200,
            json={
                "model": "text-embedding-3-small",
                "data": [
                    {"index": 1, "embedding": [0.3, 0.4]},
                    {"index": 0, "embedding": [0.1, 0.2]},
                ],
                "usage": {"prompt_tokens": 8, "total_tokens": 8},
            },
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = OpenAINewsEmbeddingClient(
        api_key="test-key",
        model="text-embedding-3-small",
        base_url="https://api.openai.com/v1",
        http_client=http_client,
    )

    response = client.embed_texts(["first", "second"])

    assert requests == [
        {
            "url": "https://api.openai.com/v1/embeddings",
            "authorization": "Bearer test-key",
            "body": {"model": "text-embedding-3-small", "input": ["first", "second"]},
        }
    ]
    assert response.embeddings == ([0.1, 0.2], [0.3, 0.4])
    assert response.input_tokens == 8
    assert response.provider == "openai"


def test_build_news_article_chunk_specs_creates_reference_and_whole_article_chunks() -> None:
    article_id = uuid.uuid4()
    references = (
        _gated_reference(
            article_id=article_id,
            reference_index=0,
            gate_source=GATE_AUTO_APPLIED_CORROBORATING,
            candidate_name="Fig Tower",
            passage="Fig Tower would include 120 apartments.",
            offset_start=10,
            offset_end=50,
        ),
        _gated_reference(
            article_id=article_id,
            reference_index=1,
            gate_source=GATE_REVIEW_ACCEPT,
            candidate_name="Olive Homes",
            passage="Olive Homes was accepted by a reviewer.",
            offset_start=70,
            offset_end=110,
        ),
    )

    chunks = build_news_article_chunk_specs(references, max_chars=2_000)

    assert len(chunks) == 3
    assert chunks[0].reference_index == 0
    assert chunks[0].chunk_offset_start == 10
    assert chunks[0].chunk_offset_end == 50
    assert "Project: Fig Tower" in chunks[0].chunk_text
    assert "Evidence passages:" in chunks[0].chunk_text
    whole_article_chunk = chunks[2]
    assert whole_article_chunk.reference_index is None
    assert whole_article_chunk.gate_source == GATE_REVIEW_ACCEPT
    assert "Whole article text:" in whole_article_chunk.chunk_text


def test_embedding_cost_uses_text_embedding_3_small_price() -> None:
    assert calculate_embedding_cost_usd("text-embedding-3-small", input_tokens=50_000) == Decimal(
        "0.001000"
    )


def test_load_gated_news_references_uses_latest_committed_accept(
    postgres_session: Session,
) -> None:
    _ensure_agent1_tables(postgres_session)
    source, article, _extraction_row, reference = _accepted_reference_fixture(postgres_session)

    references = load_gated_news_references(postgres_session, source_slug=source.slug)

    assert [item.reference_id for item in references if item.article_id == article.id] == [
        reference.id
    ]
    gated = next(item for item in references if item.article_id == article.id)
    assert gated.gate_source == GATE_REVIEW_ACCEPT
    assert gated.candidate_name == "Accepted Tower"


def test_load_gated_news_references_includes_auto_applied_and_prefers_review_accept(
    postgres_session: Session,
) -> None:
    _ensure_agent1_tables(postgres_session)
    source = _urbanize_source(postgres_session)
    article = _article(source)
    postgres_session.add(article)
    postgres_session.flush()
    extraction = _extraction(article)
    postgres_session.add(extraction)
    postgres_session.flush()
    article.current_extraction_id = extraction.id
    auto_reference = _reference(
        article=article,
        extraction=extraction,
        reference_index=0,
        candidate_name="Auto Tower",
    )
    _accepted_reference = _reference(
        article=article,
        extraction=extraction,
        reference_index=1,
        candidate_name="Accepted Tower",
        review_item_id=_committed_accept_review_item(postgres_session).id,
    )
    postgres_session.add_all([auto_reference, _accepted_reference])
    postgres_session.flush()
    postgres_session.add_all(
        [
            NewsReferenceAutoApplied(
                article_id=article.id,
                reference_index=auto_reference.reference_index,
                gate=GATE_AUTO_APPLIED_CORROBORATING,
            ),
            NewsReferenceAutoApplied(
                article_id=article.id,
                reference_index=_accepted_reference.reference_index,
                gate=GATE_AUTO_APPLIED_CORROBORATING,
            ),
        ]
    )
    postgres_session.flush()

    references = load_gated_news_references(postgres_session, article_id=article.id)
    gate_by_index = {reference.reference_index: reference.gate_source for reference in references}

    assert gate_by_index == {
        0: GATE_AUTO_APPLIED_CORROBORATING,
        1: GATE_REVIEW_ACCEPT,
    }


def test_run_news_article_chunk_indexing_skips_unchanged_chunks_on_rerun(
    postgres_session: Session,
) -> None:
    _ensure_agent1_tables(postgres_session)
    _source, article, _extraction_row, _reference = _accepted_reference_fixture(postgres_session)
    session_factory = _task_session_factory(postgres_session)
    settings = get_settings().model_copy(
        update={
            "news_embedding_batch_size": 4,
            "news_embedding_max_chars": 2_000,
        }
    )
    first_client = _StubEmbeddingClient()
    usage_before = _article_embedding_call_count(postgres_session)

    first_result = run_news_article_chunk_indexing(
        session_factory=session_factory,
        settings=settings,
        client=first_client,
        article_id=article.id,
        apply=True,
        now=datetime(2026, 5, 5, 12, 0, tzinfo=UTC),
    )

    postgres_session.expire_all()
    assert first_result.planned_chunk_count == 2
    assert first_result.indexed_chunk_count == 2
    assert first_result.skipped_unchanged_chunk_count == 0
    assert first_result.embedding_call_count == 1
    assert len(first_client.text_batches) == 1
    assert _active_chunk_count(postgres_session, article.id) == 2
    assert _article_embedding_call_count(postgres_session) == usage_before + 1

    second_client = _StubEmbeddingClient()
    second_result = run_news_article_chunk_indexing(
        session_factory=session_factory,
        settings=settings,
        client=second_client,
        article_id=article.id,
        apply=True,
        now=datetime(2026, 5, 5, 12, 5, tzinfo=UTC),
    )

    postgres_session.expire_all()
    assert second_result.planned_chunk_count == 2
    assert second_result.indexed_chunk_count == 0
    assert second_result.skipped_unchanged_chunk_count == 2
    assert second_result.embedding_call_count == 0
    assert second_client.text_batches == []
    assert _active_chunk_count(postgres_session, article.id) == 2
    assert _article_embedding_call_count(postgres_session) == usage_before + 1


def test_persist_news_article_chunk_embeddings_supersedes_active_chunks(
    postgres_session: Session,
) -> None:
    _ensure_agent1_tables(postgres_session)
    source = _urbanize_source(postgres_session)
    article = _article(source)
    postgres_session.add(article)
    postgres_session.flush()
    existing = NewsArticleChunk(
        article_id=article.id,
        reference_index=0,
        chunk_text="old",
        embedding=[0.0] * 1536,
        embedded_at=datetime(2026, 5, 5, 11, 0, tzinfo=UTC),
        model="text-embedding-3-small",
        gate_source=GATE_REVIEW_ACCEPT,
    )
    postgres_session.add(existing)
    postgres_session.flush()

    superseded_count = persist_news_article_chunk_embeddings(
        postgres_session,
        chunk_specs=[
            NewsArticleChunkSpec(
                article_id=article.id,
                reference_index=0,
                gate_source=GATE_REVIEW_ACCEPT,
                chunk_text="new",
                chunk_offset_start=0,
                chunk_offset_end=3,
            )
        ],
        embeddings=[[0.1] * 1536],
        model="text-embedding-3-small",
        now=datetime(2026, 5, 5, 12, 0, tzinfo=UTC),
    )
    postgres_session.flush()

    chunks = (
        postgres_session.execute(
            select(NewsArticleChunk).where(NewsArticleChunk.article_id == article.id)
        )
        .scalars()
        .all()
    )
    active_chunks = [chunk for chunk in chunks if chunk.superseded_at is None]
    assert superseded_count == 1
    assert len(active_chunks) == 1
    assert active_chunks[0].chunk_text == "new"


def test_news_index_articles_cli_invokes_indexer(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {}

    def fake_run_news_article_chunk_indexing(**kwargs):  # type: ignore[no-untyped-def]
        calls.update(kwargs)
        return NewsArticleChunkIndexResult(
            apply=True,
            gated_reference_count=2,
            planned_chunk_count=3,
            planned_reference_chunk_count=2,
            planned_whole_article_chunk_count=1,
            indexed_chunk_count=1,
            skipped_unchanged_chunk_count=2,
        )

    monkeypatch.setattr(
        embeddings,
        "run_news_article_chunk_indexing",
        fake_run_news_article_chunk_indexing,
    )

    result = runner.invoke(
        app,
        [
            "news",
            "index-articles",
            "--source-slug",
            "urbanize_la",
            "--limit",
            "2",
            "--apply",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls["source_slug"] == "urbanize_la"
    assert calls["limit"] == 2
    assert calls["apply"] is True
    assert "Gated references: 2" in result.output
    assert "Whole-article chunks: 1" in result.output
    assert "Skipped unchanged chunks: 2" in result.output


class _StubEmbeddingClient:
    model = "text-embedding-3-small"
    provider = "openai"

    def __init__(self) -> None:
        self.text_batches: list[list[str]] = []

    def embed_texts(self, texts):  # type: ignore[no-untyped-def]
        self.text_batches.append(list(texts))
        return EmbeddingResponse(
            embeddings=tuple([[0.1] * 1536 for _text in texts]),
            model=self.model,
            provider=self.provider,
            input_tokens=10 * len(texts),
            latency_ms=25,
        )


def _gated_reference(
    *,
    article_id: uuid.UUID,
    reference_index: int,
    gate_source: str,
    candidate_name: str,
    passage: str,
    offset_start: int,
    offset_end: int,
) -> GatedNewsReference:
    return GatedNewsReference(
        article_id=article_id,
        reference_id=uuid.uuid4(),
        extraction_id=uuid.uuid4(),
        reference_index=reference_index,
        gate_source=gate_source,
        article_title="Two projects move forward",
        article_url="https://example.com/article",
        article_body_text="This is the full article body about two accepted projects.",
        published_at=datetime(2026, 5, 5, 12, 0, tzinfo=UTC),
        candidate_name=candidate_name,
        candidate_address="123 Main Street",
        candidate_city="Los Angeles",
        candidate_developer="Acme Development",
        candidate_unit_total=120,
        candidate_unit_affordable=12,
        candidate_unit_market_rate=108,
        candidate_unit_workforce=None,
        candidate_product_type="apartment",
        candidate_age_restriction="non_age_restricted",
        candidate_status_signal="Proposed",
        candidate_delivery_year_text="late 2026",
        candidate_delivery_year_normalized=None,
        candidate_signal_flags={"proposed": True},
        candidate_identifiers={"case_number": ["DIR-2026-1"]},
        candidate_neighborhood="Downtown Los Angeles",
        candidate_confidence="high",
        passage_excerpts=[
            {
                "field": "candidate_name",
                "value": candidate_name,
                "passage": passage,
                "offset_start": offset_start,
                "offset_end": offset_end,
            }
        ],
    )


def _accepted_reference_fixture(
    postgres_session: Session,
) -> tuple[NewsSource, NewsArticle, NewsExtraction, NewsProjectReference]:
    source = _urbanize_source(postgres_session)
    article = _article(source)
    postgres_session.add(article)
    postgres_session.flush()
    extraction = _extraction(article)
    postgres_session.add(extraction)
    postgres_session.flush()
    article.current_extraction_id = extraction.id
    review_item = _committed_accept_review_item(postgres_session)
    reference = _reference(
        article=article,
        extraction=extraction,
        reference_index=0,
        candidate_name="Accepted Tower",
        review_item_id=review_item.id,
    )
    postgres_session.add(reference)
    postgres_session.flush()
    return source, article, extraction, reference


def _committed_accept_review_item(postgres_session: Session) -> ReviewItem:
    review_item = ReviewItem(
        item_type=ReviewItemType.NEW_CANDIDATE,
        status=ReviewItemStatus.ACCEPTED,
        state="committed",
        priority=Priority.MEDIUM,
    )
    postgres_session.add(review_item)
    postgres_session.flush()
    postgres_session.add(
        ReviewDecision(
            review_item_id=review_item.id,
            action=ReviewDecisionAction.ACCEPT,
            actor="researcher@example.com",
            state="committed",
            decision_type="accept_new",
            committed_at=datetime(2026, 5, 5, 12, 0, tzinfo=UTC),
        )
    )
    postgres_session.flush()
    return review_item


def _reference(
    *,
    article: NewsArticle,
    extraction: NewsExtraction,
    reference_index: int,
    candidate_name: str,
    review_item_id: uuid.UUID | None = None,
) -> NewsProjectReference:
    return NewsProjectReference(
        article_id=article.id,
        extraction_id=extraction.id,
        reference_index=reference_index,
        candidate_name=candidate_name,
        candidate_confidence="high",
        passage_excerpts=[
            {
                "field": "candidate_name",
                "value": candidate_name,
                "passage": f"{candidate_name} was proposed.",
                "offset_start": 0,
                "offset_end": len(f"{candidate_name} was proposed."),
            }
        ],
        review_item_id=review_item_id,
    )


def _ensure_agent1_tables(postgres_session: Session) -> None:
    inspector = inspect(postgres_session.bind)
    required_tables = {
        "news_sources",
        "news_articles",
        "news_extractions",
        "news_project_references",
        "news_article_chunks",
        "news_reference_auto_applied",
        "review_items",
        "review_decisions",
        "cost_caps",
        "llm_cost_usage",
    }
    missing = [table_name for table_name in required_tables if not inspector.has_table(table_name)]
    if missing:
        pytest.skip(f"Apply AGENT.1 migrations before running news embedding tests: {missing}")


def _urbanize_source(postgres_session: Session) -> NewsSource:
    source = postgres_session.execute(
        select(NewsSource).where(NewsSource.slug == "urbanize_la")
    ).scalar_one_or_none()
    if source is None:
        pytest.skip("Apply the Urbanize LA news-source seed before running news embedding tests.")
    return source


def _article(source: NewsSource) -> NewsArticle:
    return NewsArticle(
        news_source_id=source.id,
        url_canonical=f"https://example.com/news-embedding-{uuid.uuid4().hex}",
        url_original="https://example.com/news-embedding",
        url_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        fetch_status=NewsFetchStatus.FETCHED.value,
        triage_status=NewsTriageStatus.RELEVANT.value,
        body_text="Accepted Tower was proposed at 123 Main Street.",
        body_text_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        title="Accepted Tower proposed",
        published_at=datetime(2026, 5, 5, 12, 0, tzinfo=UTC),
        ingest_method="news_paste_a_link",
    )


def _extraction(article: NewsArticle) -> NewsExtraction:
    return NewsExtraction(
        article_id=article.id,
        pass_name=NewsExtractionPass.EXTRACTION.value,
        triggered_by="test",
        prompt_id="extract_v2",
        prompt_version="v2",
        prompt_hash=uuid.uuid4().hex,
        model="claude-opus-4-7",
        output_json={"project_references": []},
    )


def _task_session_factory(postgres_session: Session) -> sessionmaker[Session]:
    return sessionmaker(
        bind=postgres_session.bind,
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )


def _active_chunk_count(postgres_session: Session, article_id: uuid.UUID) -> int:
    return (
        postgres_session.scalar(
            select(func.count())
            .select_from(NewsArticleChunk)
            .where(
                NewsArticleChunk.article_id == article_id,
                NewsArticleChunk.superseded_at.is_(None),
            )
        )
        or 0
    )


def _article_embedding_call_count(postgres_session: Session) -> int:
    return (
        postgres_session.scalar(
            select(func.coalesce(func.sum(LLMCostUsage.call_count), 0)).where(
                LLMCostUsage.bucket == "news",
                LLMCostUsage.capability == "article_embedding",
                LLMCostUsage.provider == "openai",
                LLMCostUsage.model == "text-embedding-3-small",
            )
        )
        or 0
    )

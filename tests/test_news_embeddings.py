from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from tcg_pipeline.cli import app
from tcg_pipeline.db.models import (
    NewsArticle,
    NewsArticleChunk,
    NewsExtraction,
    NewsExtractionPass,
    NewsFetchStatus,
    NewsProjectReference,
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
    GatedNewsReference,
    NewsArticleChunkIndexResult,
    NewsArticleChunkSpec,
    OpenAINewsEmbeddingClient,
    build_news_article_chunk_specs,
    calculate_embedding_cost_usd,
    load_gated_news_references,
    persist_news_article_chunk_embeddings,
)

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
    source = _urbanize_source(postgres_session)
    article = _article(source)
    postgres_session.add(article)
    postgres_session.flush()
    extraction = _extraction(article)
    postgres_session.add(extraction)
    postgres_session.flush()
    article.current_extraction_id = extraction.id
    review_item = ReviewItem(
        item_type=ReviewItemType.NEW_CANDIDATE,
        status=ReviewItemStatus.ACCEPTED,
        state="committed",
        priority=Priority.MEDIUM,
    )
    postgres_session.add(review_item)
    postgres_session.flush()
    reference = NewsProjectReference(
        article_id=article.id,
        extraction_id=extraction.id,
        reference_index=0,
        candidate_name="Accepted Tower",
        candidate_confidence="high",
        passage_excerpts=[
            {
                "field": "candidate_name",
                "value": "Accepted Tower",
                "passage": "Accepted Tower was proposed.",
                "offset_start": 0,
                "offset_end": 28,
            }
        ],
        review_item_id=review_item.id,
    )
    postgres_session.add(reference)
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

    references = load_gated_news_references(postgres_session, source_slug=source.slug)

    assert [item.reference_id for item in references if item.article_id == article.id] == [
        reference.id
    ]
    gated = next(item for item in references if item.article_id == article.id)
    assert gated.gate_source == GATE_REVIEW_ACCEPT
    assert gated.candidate_name == "Accepted Tower"


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

    chunks = postgres_session.execute(
        select(NewsArticleChunk).where(NewsArticleChunk.article_id == article.id)
    ).scalars().all()
    active_chunks = [chunk for chunk in chunks if chunk.superseded_at is None]
    assert superseded_count == 1
    assert len(active_chunks) == 1
    assert active_chunks[0].chunk_text == "new"


def test_news_index_articles_cli_invokes_indexer(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {}

    def fake_run_news_article_chunk_indexing(**kwargs):  # type: ignore[no-untyped-def]
        calls.update(kwargs)
        return NewsArticleChunkIndexResult(
            apply=False,
            gated_reference_count=2,
            planned_chunk_count=3,
            planned_reference_chunk_count=2,
            planned_whole_article_chunk_count=1,
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
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls["source_slug"] == "urbanize_la"
    assert calls["limit"] == 2
    assert calls["apply"] is False
    assert "Gated references: 2" in result.output
    assert "Whole-article chunks: 1" in result.output


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
        candidate_developer="Acme Development",
        candidate_unit_total=120,
        candidate_unit_affordable=12,
        candidate_unit_market_rate=108,
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


from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from urllib.parse import urljoin

import httpx
from sqlalchemy import and_, func, literal, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from tcg_pipeline.db.connection import get_session_factory
from tcg_pipeline.db.models import (
    NewsArticle,
    NewsArticleChunk,
    NewsProjectReference,
    NewsReferenceAutoApplied,
    NewsSource,
    ReviewDecision,
    ReviewDecisionAction,
    ReviewItem,
)
from tcg_pipeline.db.review_workflow import (
    DECISION_ACCEPT_NEW,
    DECISION_CANDIDATE_PREFIX,
    REVIEW_DECISION_STATE_COMMITTED,
    REVIEW_ITEM_STATE_COMMITTED,
)
from tcg_pipeline.embedding_config import DEFAULT_NEWS_EMBEDDING_MODEL, NEWS_EMBEDDING_DIMENSIONS
from tcg_pipeline.news.costs import (
    NEWS_COST_BUCKET,
    record_llm_cost,
    release_llm_cost_reservation,
    reserve_llm_cost,
)
from tcg_pipeline.settings import Settings, get_settings

GATE_REVIEW_ACCEPT = "review_accept"
GATE_AUTO_APPLIED_CORROBORATING = "auto_applied_corroborating"
GATE_AUTO_APPLIED_HIGH_CONFIDENCE = "auto_applied_high_confidence"
NEWS_EMBEDDING_PROVIDER_OPENAI = "openai"
NEWS_ARTICLE_EMBEDDING_CAPABILITY = "article_embedding"
OPENAI_EMBEDDINGS_PATH = "embeddings"
OPENAI_EMBEDDING_PRICING_USD_PER_MILLION = {
    DEFAULT_NEWS_EMBEDDING_MODEL: Decimal("0.02"),
}
GATE_PRIORITY = {
    GATE_AUTO_APPLIED_CORROBORATING: 1,
    GATE_AUTO_APPLIED_HIGH_CONFIDENCE: 2,
    GATE_REVIEW_ACCEPT: 3,
}


@dataclass(frozen=True, slots=True)
class EmbeddingResponse:
    embeddings: tuple[list[float], ...]
    model: str
    provider: str
    input_tokens: int
    latency_ms: int


class NewsEmbeddingClient(Protocol):
    model: str
    provider: str

    def embed_texts(self, texts: Sequence[str]) -> EmbeddingResponse: ...


class OpenAINewsEmbeddingClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_NEWS_EMBEDDING_MODEL,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 60.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.model = model
        self.provider = NEWS_EMBEDDING_PROVIDER_OPENAI
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._http_client = http_client

    def embed_texts(self, texts: Sequence[str]) -> EmbeddingResponse:
        if not texts:
            return EmbeddingResponse(
                embeddings=(),
                model=self.model,
                provider=self.provider,
                input_tokens=0,
                latency_ms=0,
            )
        payload = {"model": self.model, "input": list(texts)}
        client = self._http_client or httpx.Client(timeout=self._timeout_seconds)
        close_client = self._http_client is None
        started_at = time.perf_counter()
        try:
            response = client.post(
                urljoin(self._base_url + "/", OPENAI_EMBEDDINGS_PATH),
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            response_json = response.json()
        finally:
            if close_client:
                client.close()
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        return _openai_embedding_response(
            response_json,
            fallback_model=self.model,
            latency_ms=latency_ms,
        )


@dataclass(frozen=True, slots=True)
class GatedNewsReference:
    article_id: uuid.UUID
    reference_id: uuid.UUID
    extraction_id: uuid.UUID
    reference_index: int
    gate_source: str
    article_title: str | None
    article_url: str
    article_body_text: str | None
    published_at: datetime | None
    candidate_name: str | None
    candidate_address: str | None
    candidate_developer: str | None
    candidate_unit_total: int | None
    candidate_unit_affordable: int | None
    candidate_unit_market_rate: int | None
    candidate_product_type: str | None
    candidate_age_restriction: str | None
    candidate_status_signal: str | None
    candidate_delivery_year_text: str | None
    candidate_delivery_year_normalized: Any
    candidate_signal_flags: dict | None
    candidate_identifiers: dict | None
    candidate_neighborhood: str | None
    candidate_confidence: str | None
    passage_excerpts: Any


@dataclass(frozen=True, slots=True)
class NewsArticleChunkSpec:
    article_id: uuid.UUID
    reference_index: int | None
    gate_source: str
    chunk_text: str
    chunk_offset_start: int | None
    chunk_offset_end: int | None


@dataclass(slots=True)
class NewsArticleChunkIndexResult:
    apply: bool
    gated_reference_count: int
    planned_chunk_count: int
    planned_reference_chunk_count: int
    planned_whole_article_chunk_count: int
    indexed_chunk_count: int = 0
    skipped_unchanged_chunk_count: int = 0
    superseded_chunk_count: int = 0
    embedding_call_count: int = 0
    input_tokens: int = 0
    cost_usd: Decimal = Decimal("0.000000")
    skipped_reason: str | None = None


def build_news_embedding_client(
    settings: Settings | None = None,
    *,
    http_client: httpx.Client | None = None,
) -> NewsEmbeddingClient:
    resolved_settings = settings or get_settings()
    provider = resolved_settings.news_embedding_provider.strip().lower().replace("-", "_")
    if provider != NEWS_EMBEDDING_PROVIDER_OPENAI:
        raise RuntimeError(
            "Only direct OpenAI embeddings are currently supported. "
            "Set NEWS_EMBEDDING_PROVIDER=openai."
        )
    if not resolved_settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required to index news article embeddings.")
    if resolved_settings.news_embedding_dimensions != NEWS_EMBEDDING_DIMENSIONS:
        raise RuntimeError(
            "NEWS_EMBEDDING_DIMENSIONS must match the news_article_chunks vector dimension "
            f"({NEWS_EMBEDDING_DIMENSIONS})."
        )
    return OpenAINewsEmbeddingClient(
        api_key=resolved_settings.openai_api_key,
        model=resolved_settings.news_embedding_model,
        base_url=resolved_settings.openai_base_url,
        timeout_seconds=resolved_settings.news_embedding_timeout_seconds,
        http_client=http_client,
    )


def run_news_article_chunk_indexing(
    *,
    session_factory: sessionmaker[Session] | None = None,
    settings: Settings | None = None,
    client: NewsEmbeddingClient | None = None,
    source_slug: str | None = None,
    article_id: uuid.UUID | None = None,
    limit: int | None = None,
    apply: bool = False,
    now: datetime | None = None,
) -> NewsArticleChunkIndexResult:
    resolved_settings = settings or get_settings()
    resolved_session_factory = session_factory or get_session_factory()
    current = now or datetime.now(UTC)
    with resolved_session_factory() as session:
        references = load_gated_news_references(
            session,
            source_slug=source_slug,
            article_id=article_id,
            limit=limit,
        )
    chunk_specs = build_news_article_chunk_specs(
        references,
        max_chars=resolved_settings.news_embedding_max_chars,
    )
    result = NewsArticleChunkIndexResult(
        apply=apply,
        gated_reference_count=len(references),
        planned_chunk_count=len(chunk_specs),
        planned_reference_chunk_count=sum(
            1 for spec in chunk_specs if spec.reference_index is not None
        ),
        planned_whole_article_chunk_count=sum(
            1 for spec in chunk_specs if spec.reference_index is None
        ),
    )
    if not apply or not chunk_specs:
        return result

    embedding_client = client or build_news_embedding_client(resolved_settings)
    with resolved_session_factory() as session:
        chunk_specs = filter_unchanged_active_chunk_specs(
            session,
            chunk_specs=chunk_specs,
            model=embedding_client.model,
        )
    result.skipped_unchanged_chunk_count = result.planned_chunk_count - len(chunk_specs)
    if not chunk_specs:
        return result

    for batch in _batched(chunk_specs, resolved_settings.news_embedding_batch_size):
        texts = [spec.chunk_text for spec in batch]
        reserved_cost_usd = calculate_embedding_cost_usd(
            embedding_client.model,
            input_tokens=estimate_embedding_tokens(texts),
        )
        with resolved_session_factory() as session:
            reservation = reserve_llm_cost(
                session,
                pass_name=NEWS_ARTICLE_EMBEDDING_CAPABILITY,
                provider=embedding_client.provider,
                model=embedding_client.model,
                estimated_cost_usd=reserved_cost_usd,
                bucket=NEWS_COST_BUCKET,
                now=current,
            )
            session.commit()
        if reservation is None:
            result.skipped_reason = "cost_cap"
            return result

        try:
            embedding_response = embedding_client.embed_texts(texts)
        except Exception:
            with resolved_session_factory() as session:
                release_llm_cost_reservation(
                    session,
                    reserved_cost_usd=reserved_cost_usd,
                    bucket=NEWS_COST_BUCKET,
                    now=current,
                )
                session.commit()
            raise

        if len(embedding_response.embeddings) != len(batch):
            with resolved_session_factory() as session:
                release_llm_cost_reservation(
                    session,
                    reserved_cost_usd=reserved_cost_usd,
                    bucket=NEWS_COST_BUCKET,
                    now=current,
                )
                session.commit()
            raise RuntimeError(
                "Embedding provider returned "
                f"{len(embedding_response.embeddings)} vectors for {len(batch)} texts."
            )
        try:
            actual_cost_usd = calculate_embedding_cost_usd(
                embedding_response.model,
                input_tokens=embedding_response.input_tokens,
            )
        except Exception:
            with resolved_session_factory() as session:
                release_llm_cost_reservation(
                    session,
                    reserved_cost_usd=reserved_cost_usd,
                    bucket=NEWS_COST_BUCKET,
                    now=current,
                )
                session.commit()
            raise
        try:
            with resolved_session_factory() as session:
                result.superseded_chunk_count += persist_news_article_chunk_embeddings(
                    session,
                    chunk_specs=batch,
                    embeddings=embedding_response.embeddings,
                    model=embedding_response.model,
                    now=current,
                )
                record_llm_cost(
                    session,
                    pass_name=NEWS_ARTICLE_EMBEDDING_CAPABILITY,
                    provider=embedding_response.provider,
                    model=embedding_response.model,
                    input_tokens_uncached=embedding_response.input_tokens,
                    input_tokens_cache_creation=0,
                    input_tokens_cached=0,
                    output_tokens=0,
                    cost_usd=actual_cost_usd,
                    reserved_cost_usd=reserved_cost_usd,
                    bucket=NEWS_COST_BUCKET,
                    now=current,
                )
                session.commit()
        except Exception:
            with resolved_session_factory() as session:
                release_llm_cost_reservation(
                    session,
                    reserved_cost_usd=reserved_cost_usd,
                    bucket=NEWS_COST_BUCKET,
                    now=current,
                )
                session.commit()
            raise
        result.indexed_chunk_count += len(batch)
        result.embedding_call_count += 1
        result.input_tokens += embedding_response.input_tokens
        result.cost_usd += actual_cost_usd
    result.cost_usd = result.cost_usd.quantize(Decimal("0.000001"))
    return result


def load_gated_news_references(
    session: Session,
    *,
    source_slug: str | None = None,
    article_id: uuid.UUID | None = None,
    limit: int | None = None,
) -> tuple[GatedNewsReference, ...]:
    rows: dict[tuple[uuid.UUID, int], GatedNewsReference] = {}
    for reference in _committed_accept_references(
        session,
        source_slug=source_slug,
        article_id=article_id,
    ):
        rows[(reference.article_id, reference.reference_index)] = reference
    for reference in _auto_applied_references(
        session,
        source_slug=source_slug,
        article_id=article_id,
    ):
        key = (reference.article_id, reference.reference_index)
        existing = rows.get(key)
        if (
            existing is None
            or GATE_PRIORITY[reference.gate_source] > GATE_PRIORITY[existing.gate_source]
        ):
            rows[key] = reference
    ordered = sorted(
        rows.values(),
        key=lambda row: (
            row.published_at or datetime.min.replace(tzinfo=UTC),
            str(row.article_id),
            row.reference_index,
        ),
        reverse=True,
    )
    if limit is not None:
        ordered = ordered[:limit]
    return tuple(ordered)


def build_news_article_chunk_specs(
    references: Sequence[GatedNewsReference],
    *,
    max_chars: int,
) -> tuple[NewsArticleChunkSpec, ...]:
    chunks: list[NewsArticleChunkSpec] = []
    article_references: dict[uuid.UUID, GatedNewsReference] = {}
    for reference in references:
        excerpts = normalize_passage_excerpts(reference.passage_excerpts)
        offset_start, offset_end = _excerpt_offset_span(excerpts)
        chunks.append(
            NewsArticleChunkSpec(
                article_id=reference.article_id,
                reference_index=reference.reference_index,
                gate_source=reference.gate_source,
                chunk_text=_reference_chunk_text(reference, excerpts=excerpts, max_chars=max_chars),
                chunk_offset_start=offset_start,
                chunk_offset_end=offset_end,
            )
        )
        existing = article_references.get(reference.article_id)
        if (
            existing is None
            or GATE_PRIORITY[reference.gate_source] > GATE_PRIORITY[existing.gate_source]
        ):
            article_references[reference.article_id] = reference

    for reference in article_references.values():
        body_text = _clean_text(reference.article_body_text)
        if not body_text:
            continue
        chunk_body = _truncate_text(body_text, max_chars=max(max_chars - 500, 500))
        chunks.append(
            NewsArticleChunkSpec(
                article_id=reference.article_id,
                reference_index=None,
                gate_source=reference.gate_source,
                chunk_text=_truncate_text(
                    "\n".join(
                        part
                        for part in (
                            f"Article: {_clean_text(reference.article_title) or 'Untitled'}",
                            f"URL: {reference.article_url}",
                            _published_line(reference.published_at),
                            "Whole article text:",
                            chunk_body,
                        )
                        if part
                    ),
                    max_chars=max_chars,
                ),
                chunk_offset_start=0,
                chunk_offset_end=min(len(body_text), len(chunk_body)),
            )
        )
    return tuple(chunks)


def persist_news_article_chunk_embeddings(
    session: Session,
    *,
    chunk_specs: Sequence[NewsArticleChunkSpec],
    embeddings: Sequence[Sequence[float]],
    model: str,
    now: datetime,
) -> int:
    superseded_count = 0
    for spec, embedding in zip(chunk_specs, embeddings, strict=True):
        vector = [float(value) for value in embedding]
        if len(vector) != NEWS_EMBEDDING_DIMENSIONS:
            raise RuntimeError(
                f"Embedding dimension mismatch for {model}: expected "
                f"{NEWS_EMBEDDING_DIMENSIONS}, got {len(vector)}."
            )
        superseded_count += _supersede_active_chunks(
            session,
            article_id=spec.article_id,
            reference_index=spec.reference_index,
            model=model,
            now=now,
        )
        session.add(
            NewsArticleChunk(
                article_id=spec.article_id,
                reference_index=spec.reference_index,
                chunk_text=spec.chunk_text,
                chunk_offset_start=spec.chunk_offset_start,
                chunk_offset_end=spec.chunk_offset_end,
                embedding=vector,
                embedded_at=now,
                model=model,
                gate_source=spec.gate_source,
            )
        )
    session.flush()
    return superseded_count


def filter_unchanged_active_chunk_specs(
    session: Session,
    *,
    chunk_specs: Sequence[NewsArticleChunkSpec],
    model: str,
) -> tuple[NewsArticleChunkSpec, ...]:
    active_chunk_texts = _active_chunk_text_by_key(
        session,
        chunk_specs=chunk_specs,
        model=model,
    )
    return tuple(
        spec
        for spec in chunk_specs
        if active_chunk_texts.get(_chunk_key(spec)) != spec.chunk_text
    )


def calculate_embedding_cost_usd(model: str, *, input_tokens: int) -> Decimal:
    pricing = OPENAI_EMBEDDING_PRICING_USD_PER_MILLION.get(_embedding_pricing_key(model))
    if pricing is None:
        raise RuntimeError(f"Unknown news embedding model pricing: {model}")
    return (Decimal(input_tokens) * pricing / Decimal(1_000_000)).quantize(Decimal("0.000001"))


def estimate_embedding_tokens(texts: Sequence[str]) -> int:
    return sum(max(1, len(text) // 4) for text in texts)


def normalize_passage_excerpts(raw: Any) -> tuple[dict[str, Any], ...]:
    if isinstance(raw, list):
        candidates = raw
    elif isinstance(raw, dict):
        nested = raw.get("passage_excerpts") or raw.get("excerpts") or raw.get("items")
        candidates = nested if isinstance(nested, list) else list(raw.values())
    else:
        candidates = []
    excerpts = [item for item in candidates if isinstance(item, dict)]
    return tuple(excerpts)


def _committed_accept_references(
    session: Session,
    *,
    source_slug: str | None,
    article_id: uuid.UUID | None,
) -> tuple[GatedNewsReference, ...]:
    ranked_decisions = (
        select(
            ReviewDecision.review_item_id.label("review_item_id"),
            ReviewDecision.action.label("action"),
            ReviewDecision.decision_type.label("decision_type"),
            func.row_number()
            .over(
                partition_by=ReviewDecision.review_item_id,
                order_by=(
                    ReviewDecision.committed_at.desc().nullslast(),
                    ReviewDecision.created_at.desc(),
                    ReviewDecision.id.desc(),
                ),
            )
            .label("rank"),
        )
        .where(ReviewDecision.state == REVIEW_DECISION_STATE_COMMITTED)
        .subquery()
    )
    stmt = (
        select(NewsProjectReference, NewsArticle, literal(GATE_REVIEW_ACCEPT))
        .join(NewsArticle, NewsArticle.id == NewsProjectReference.article_id)
        .join(ReviewItem, ReviewItem.id == NewsProjectReference.review_item_id)
        .join(ranked_decisions, ranked_decisions.c.review_item_id == ReviewItem.id)
        .where(
            ReviewItem.state == REVIEW_ITEM_STATE_COMMITTED,
            ranked_decisions.c.rank == 1,
            ranked_decisions.c.action == ReviewDecisionAction.ACCEPT,
            or_(
                ranked_decisions.c.decision_type == DECISION_ACCEPT_NEW,
                ranked_decisions.c.decision_type.like(f"{DECISION_CANDIDATE_PREFIX}%"),
            ),
            _current_extraction_predicate(),
        )
    )
    stmt = _apply_reference_filters(stmt, source_slug=source_slug, article_id=article_id)
    return tuple(
        _gated_reference_from_rows(reference, article, gate_source)
        for reference, article, gate_source in session.execute(stmt).all()
    )


def _auto_applied_references(
    session: Session,
    *,
    source_slug: str | None,
    article_id: uuid.UUID | None,
) -> tuple[GatedNewsReference, ...]:
    stmt = (
        select(NewsProjectReference, NewsArticle, NewsReferenceAutoApplied.gate)
        .join(NewsArticle, NewsArticle.id == NewsProjectReference.article_id)
        .join(
            NewsReferenceAutoApplied,
            and_(
                NewsReferenceAutoApplied.article_id == NewsProjectReference.article_id,
                NewsReferenceAutoApplied.reference_index == NewsProjectReference.reference_index,
            ),
        )
        .where(_current_extraction_predicate())
        .order_by(NewsReferenceAutoApplied.applied_at.desc())
    )
    stmt = _apply_reference_filters(stmt, source_slug=source_slug, article_id=article_id)
    return tuple(
        _gated_reference_from_rows(reference, article, gate_source)
        for reference, article, gate_source in session.execute(stmt).all()
        if gate_source in GATE_PRIORITY
    )


def _apply_reference_filters(
    statement: Any,
    *,
    source_slug: str | None,
    article_id: uuid.UUID | None,
) -> Any:
    if article_id is not None:
        statement = statement.where(NewsProjectReference.article_id == article_id)
    if source_slug is not None:
        statement = statement.join(NewsSource, NewsSource.id == NewsArticle.news_source_id).where(
            NewsSource.slug == source_slug
        )
    return statement


def _current_extraction_predicate() -> Any:
    return or_(
        NewsArticle.current_extraction_id.is_(None),
        NewsArticle.current_extraction_id == NewsProjectReference.extraction_id,
    )


def _gated_reference_from_rows(
    reference: NewsProjectReference,
    article: NewsArticle,
    gate_source: str,
) -> GatedNewsReference:
    return GatedNewsReference(
        article_id=article.id,
        reference_id=reference.id,
        extraction_id=reference.extraction_id,
        reference_index=reference.reference_index,
        gate_source=gate_source,
        article_title=article.title,
        article_url=article.url_canonical,
        article_body_text=article.body_text,
        published_at=article.published_at,
        candidate_name=reference.candidate_name,
        candidate_address=reference.candidate_address,
        candidate_developer=reference.candidate_developer,
        candidate_unit_total=reference.candidate_unit_total,
        candidate_unit_affordable=reference.candidate_unit_affordable,
        candidate_unit_market_rate=reference.candidate_unit_market_rate,
        candidate_product_type=reference.candidate_product_type,
        candidate_age_restriction=reference.candidate_age_restriction,
        candidate_status_signal=reference.candidate_status_signal,
        candidate_delivery_year_text=reference.candidate_delivery_year_text,
        candidate_delivery_year_normalized=reference.candidate_delivery_year_normalized,
        candidate_signal_flags=reference.candidate_signal_flags,
        candidate_identifiers=reference.candidate_identifiers,
        candidate_neighborhood=reference.candidate_neighborhood,
        candidate_confidence=reference.candidate_confidence,
        passage_excerpts=reference.passage_excerpts,
    )


def _reference_chunk_text(
    reference: GatedNewsReference,
    *,
    excerpts: Sequence[dict[str, Any]],
    max_chars: int,
) -> str:
    lines = [
        f"Article: {_clean_text(reference.article_title) or 'Untitled'}",
        f"URL: {reference.article_url}",
        _published_line(reference.published_at),
        f"Reference index: {reference.reference_index}",
        f"Index gate: {reference.gate_source}",
    ]
    lines.extend(_field_lines(reference))
    passage_lines = _passage_lines(excerpts)
    if passage_lines:
        lines.append("Evidence passages:")
        lines.extend(passage_lines)
    return _truncate_text("\n".join(line for line in lines if line), max_chars=max_chars)


def _field_lines(reference: GatedNewsReference) -> list[str]:
    raw_fields: list[tuple[str, Any]] = [
        ("Project", reference.candidate_name),
        ("Address", reference.candidate_address),
        ("Developer", reference.candidate_developer),
        ("Total units", reference.candidate_unit_total),
        ("Affordable units", reference.candidate_unit_affordable),
        ("Market-rate units", reference.candidate_unit_market_rate),
        ("Product type", reference.candidate_product_type),
        ("Age restriction", reference.candidate_age_restriction),
        ("Status signal", reference.candidate_status_signal),
        ("Delivery text", reference.candidate_delivery_year_text),
        ("Delivery normalized", reference.candidate_delivery_year_normalized),
        ("Neighborhood", reference.candidate_neighborhood),
        ("Confidence", reference.candidate_confidence),
    ]
    lines = [f"{label}: {value}" for label, value in raw_fields if _clean_text(value)]
    if reference.candidate_identifiers:
        lines.append(f"Identifiers: {_compact_json(reference.candidate_identifiers)}")
    if reference.candidate_signal_flags:
        lines.append(f"Signal flags: {_compact_json(reference.candidate_signal_flags)}")
    return lines


def _passage_lines(excerpts: Sequence[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for excerpt in excerpts:
        passage = _clean_text(excerpt.get("passage"))
        if not passage:
            continue
        field = _clean_text(excerpt.get("field")) or "passage"
        value = _clean_text(excerpt.get("value"))
        suffix = f" ({value})" if value else ""
        lines.append(f"- {field}{suffix}: {passage}")
    return lines


def _excerpt_offset_span(excerpts: Sequence[dict[str, Any]]) -> tuple[int | None, int | None]:
    starts: list[int] = []
    ends: list[int] = []
    for excerpt in excerpts:
        start = excerpt.get("offset_start")
        end = excerpt.get("offset_end")
        if isinstance(start, int):
            starts.append(start)
        if isinstance(end, int):
            ends.append(end)
    return (min(starts) if starts else None, max(ends) if ends else None)


def _supersede_active_chunks(
    session: Session,
    *,
    article_id: uuid.UUID,
    reference_index: int | None,
    model: str,
    now: datetime,
) -> int:
    statement = update(NewsArticleChunk).where(
        NewsArticleChunk.article_id == article_id,
        NewsArticleChunk.model == model,
        NewsArticleChunk.superseded_at.is_(None),
    )
    if reference_index is None:
        statement = statement.where(NewsArticleChunk.reference_index.is_(None))
    else:
        statement = statement.where(NewsArticleChunk.reference_index == reference_index)
    result = session.execute(statement.values(superseded_at=now))
    return int(result.rowcount or 0)


def _active_chunk_text_by_key(
    session: Session,
    *,
    chunk_specs: Sequence[NewsArticleChunkSpec],
    model: str,
) -> dict[tuple[uuid.UUID, int | None], str]:
    article_ids = {spec.article_id for spec in chunk_specs}
    if not article_ids:
        return {}
    rows = (
        session.execute(
            select(NewsArticleChunk)
            .where(
                NewsArticleChunk.article_id.in_(article_ids),
                NewsArticleChunk.model == model,
                NewsArticleChunk.superseded_at.is_(None),
            )
            .order_by(
                NewsArticleChunk.embedded_at.desc().nullslast(),
                NewsArticleChunk.created_at.desc(),
                NewsArticleChunk.id.desc(),
            )
        )
        .scalars()
        .all()
    )
    active: dict[tuple[uuid.UUID, int | None], str] = {}
    requested_keys = {_chunk_key(spec) for spec in chunk_specs}
    for chunk in rows:
        key = (chunk.article_id, chunk.reference_index)
        if key in requested_keys and key not in active:
            active[key] = chunk.chunk_text
    return active


def _chunk_key(spec: NewsArticleChunkSpec) -> tuple[uuid.UUID, int | None]:
    return (spec.article_id, spec.reference_index)


def _openai_embedding_response(
    response_json: dict[str, Any],
    *,
    fallback_model: str,
    latency_ms: int,
) -> EmbeddingResponse:
    data = response_json.get("data") or []
    if not isinstance(data, list):
        raise RuntimeError("OpenAI embeddings response did not include a data list.")
    indexed = sorted(
        (item for item in data if isinstance(item, dict)),
        key=lambda item: item.get("index", 0),
    )
    embeddings: list[list[float]] = []
    for item in indexed:
        embedding = item.get("embedding")
        if not isinstance(embedding, list):
            raise RuntimeError("OpenAI embeddings response contained a missing vector.")
        embeddings.append([float(value) for value in embedding])
    usage = response_json.get("usage") or {}
    input_tokens = int(usage.get("prompt_tokens") or usage.get("total_tokens") or 0)
    return EmbeddingResponse(
        embeddings=tuple(embeddings),
        model=str(response_json.get("model") or fallback_model),
        provider=NEWS_EMBEDDING_PROVIDER_OPENAI,
        input_tokens=input_tokens,
        latency_ms=latency_ms,
    )


def _embedding_pricing_key(model: str) -> str:
    suffix = model.rsplit("/", maxsplit=1)[-1]
    return suffix


def _published_line(published_at: datetime | None) -> str | None:
    if published_at is None:
        return None
    return f"Published: {published_at.isoformat()}"


def _compact_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return " ".join(text.split())


def _truncate_text(text: str, *, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    suffix = "\n[truncated]"
    return text[: max(max_chars - len(suffix), 0)].rstrip() + suffix


def _batched(
    items: Sequence[NewsArticleChunkSpec],
    batch_size: int,
) -> Iterable[tuple[NewsArticleChunkSpec, ...]]:
    for index in range(0, len(items), batch_size):
        yield tuple(items[index : index + batch_size])

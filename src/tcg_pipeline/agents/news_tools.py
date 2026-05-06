from __future__ import annotations

import enum
import re
import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import text

from tcg_pipeline.agents.runner import AgentRunRequest
from tcg_pipeline.agents.tools import AgentTool, AgentToolError, AgentToolResult
from tcg_pipeline.db.models import NewsArticle
from tcg_pipeline.news.embeddings import NewsEmbeddingClient, build_news_embedding_client

SEARCH_ARTICLES_SIMILAR_OUTPUT_TOKEN_BUDGET = 2500
SEARCH_ARTICLES_SIMILAR_DEFAULT_TOP_K = 5
SEARCH_ARTICLES_SIMILAR_MAX_TOP_K = 10
SEARCH_ARTICLES_SIMILAR_EXCERPT_CHARS = 200
GET_ARTICLE_BODY_OUTPUT_TOKEN_BUDGET = 3500
GET_ARTICLE_BODY_DEFAULT_MAX_CHARS = 6000
GET_ARTICLE_BODY_HARD_MAX_CHARS = 12000

SEARCH_ARTICLES_SIMILAR_SQL = text(
    """
    SELECT
        c.id AS chunk_id,
        c.article_id,
        c.reference_index,
        c.gate_source,
        c.chunk_text,
        c.chunk_offset_start,
        c.chunk_offset_end,
        a.title,
        a.url_canonical,
        a.published_at,
        s.slug AS source_slug,
        npr.candidate_name,
        npr.candidate_address,
        npr.candidate_developer,
        npr.match_status,
        npr.matched_project_id,
        npr.matched_evidence_id,
        (c.embedding <=> CAST(:query_embedding AS vector)) AS distance
    FROM news_article_chunks c
    JOIN news_articles a ON a.id = c.article_id
    JOIN news_sources s ON s.id = a.news_source_id
    LEFT JOIN news_project_references npr
      ON npr.article_id = c.article_id
     AND npr.reference_index = c.reference_index
    WHERE c.embedding IS NOT NULL
      AND c.superseded_at IS NULL
      AND c.model = :model
      AND (:include_whole_article_chunks OR c.reference_index IS NOT NULL)
      AND (
          CAST(:published_after AS timestamptz) IS NULL
          OR a.published_at >= CAST(:published_after AS timestamptz)
      )
    ORDER BY c.embedding <=> CAST(:query_embedding AS vector)
    LIMIT :limit
    """
)

SEARCH_ARTICLES_SIMILAR_COUNT_SQL = text(
    """
    SELECT count(*)
    FROM news_article_chunks c
    JOIN news_articles a ON a.id = c.article_id
    WHERE c.embedding IS NOT NULL
      AND c.superseded_at IS NULL
      AND c.model = :model
      AND (:include_whole_article_chunks OR c.reference_index IS NOT NULL)
      AND (
          CAST(:published_after AS timestamptz) IS NULL
          OR a.published_at >= CAST(:published_after AS timestamptz)
      )
    """
)


def handle_search_articles_similar(
    tool_input: dict[str, Any],
    request: AgentRunRequest,
) -> AgentToolResult:
    query_text = _required_text(tool_input.get("query_text"), field_name="query_text")
    top_k = _bounded_int(
        tool_input.get("top_k"),
        default=SEARCH_ARTICLES_SIMILAR_DEFAULT_TOP_K,
        maximum=SEARCH_ARTICLES_SIMILAR_MAX_TOP_K,
    )
    include_whole_article_chunks = bool(tool_input.get("include_whole_article_chunks") or False)
    published_after = _optional_datetime(
        tool_input.get("published_after"),
        field_name="published_after",
    )
    if request.session_factory is None:
        raise AgentToolError("Tool search_articles_similar requires a session_factory.")

    embedding_client = _embedding_client_for_request(request)
    embedding_response = embedding_client.embed_texts([query_text])
    if len(embedding_response.embeddings) != 1:
        raise AgentToolError(
            "Tool search_articles_similar expected one query embedding from provider."
        )
    query_embedding = _vector_literal(embedding_response.embeddings[0])
    with request.session_factory() as session:
        params = {
            "query_embedding": query_embedding,
            "model": embedding_response.model,
            "include_whole_article_chunks": include_whole_article_chunks,
            "published_after": published_after,
            "limit": top_k,
        }
        rows = session.execute(SEARCH_ARTICLES_SIMILAR_SQL, params).mappings().all()
        total_available = int(
            session.scalar(SEARCH_ARTICLES_SIMILAR_COUNT_SQL, params) or 0
        )

    matches = [_similar_article_payload(row) for row in rows]
    payload = {
        "query_text": query_text,
        "embedding_model": embedding_response.model,
        "top_k": top_k,
        "include_whole_article_chunks": include_whole_article_chunks,
        "published_after": _serialize(published_after),
        "total_available": total_available,
        "matches": matches,
        "query_embedding_cost_accounting": "ignored_negligible",
    }
    return AgentToolResult(
        payload=payload,
        summary=f"Found {len(matches)} similar accepted article chunks.",
        total_results=total_available,
    )


def handle_get_article_body(
    tool_input: dict[str, Any],
    request: AgentRunRequest,
) -> AgentToolResult:
    article_id = _required_uuid(tool_input.get("article_id"), field_name="article_id")
    max_chars = _bounded_int(
        tool_input.get("max_chars"),
        default=GET_ARTICLE_BODY_DEFAULT_MAX_CHARS,
        maximum=GET_ARTICLE_BODY_HARD_MAX_CHARS,
    )
    if request.session_factory is None:
        raise AgentToolError("Tool get_article_body requires a session_factory.")

    with request.session_factory() as session:
        article = session.get(NewsArticle, article_id)
        if article is None:
            return AgentToolResult(
                payload={"article_id": str(article_id), "found": False},
                summary=f"Article {article_id} was not found.",
                total_results=0,
            )
        source_slug = article.source.slug if article.source is not None else None
        body_text = article.body_text or ""
        excerpt, truncated = _excerpt(body_text, max_chars=max_chars)
        payload = {
            "article_id": str(article.id),
            "found": True,
            "title": article.title,
            "url": article.url_canonical,
            "source_slug": source_slug,
            "published_at": _serialize(article.published_at),
            "body_text": excerpt,
            "body_text_length": len(body_text),
            "truncated": truncated,
            "max_chars": max_chars,
        }
    return AgentToolResult(
        payload=payload,
        summary=f"Fetched article body for {payload['title'] or article_id}.",
        total_results=1,
    )


SEARCH_ARTICLES_SIMILAR_TOOL = AgentTool(
    name="search_articles_similar",
    description=(
        "Search accepted news article chunks by semantic similarity. Use this for recall when "
        "you need prior accepted articles about a project, address, developer, or source phrase. "
        "Input requires query_text. Optional top_k defaults to 5 and is capped at 10. By default "
        "whole-article context chunks are excluded; set include_whole_article_chunks only when "
        "reference-specific chunks are insufficient. Use published_after as an ISO date or "
        "datetime when freshness matters. Output returns compact chunk excerpts, article IDs, "
        "URLs, similarity, gate source, matched project/evidence IDs when available, and "
        "reference metadata. If a match matters, call get_article_body on the returned "
        "article_id before treating the full article as evidence."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query_text": {
                "type": "string",
                "description": "Search phrase, address, project name, or developer context.",
            },
            "top_k": {
                "type": "integer",
                "minimum": 1,
                "maximum": SEARCH_ARTICLES_SIMILAR_MAX_TOP_K,
                "description": "Maximum similar chunks to return. Defaults to 5.",
            },
            "include_whole_article_chunks": {
                "type": "boolean",
                "description": "Include broad whole-article chunks. Defaults to false.",
            },
            "published_after": {
                "type": "string",
                "description": "Optional ISO date/datetime lower bound for article published_at.",
            },
        },
        "required": ["query_text"],
        "additionalProperties": False,
    },
    output_token_budget=SEARCH_ARTICLES_SIMILAR_OUTPUT_TOKEN_BUDGET,
    handler=handle_search_articles_similar,
)

GET_ARTICLE_BODY_TOOL = AgentTool(
    name="get_article_body",
    description=(
        "Fetch the stored body text for a specific news article after search_articles_similar "
        "has narrowed the candidate article. Input requires article_id. Optional max_chars "
        "defaults to 6000 and is capped at 12000. Output returns article metadata and body text "
        "excerpt only; raw HTML is never returned."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "article_id": {
                "type": "string",
                "description": "UUID of the news article to fetch.",
            },
            "max_chars": {
                "type": "integer",
                "minimum": 1,
                "maximum": GET_ARTICLE_BODY_HARD_MAX_CHARS,
                "description": "Maximum body characters to return. Defaults to 6000.",
            },
        },
        "required": ["article_id"],
        "additionalProperties": False,
    },
    output_token_budget=GET_ARTICLE_BODY_OUTPUT_TOKEN_BUDGET,
    handler=handle_get_article_body,
)


def _embedding_client_for_request(request: AgentRunRequest) -> NewsEmbeddingClient:
    if request.embedding_client is not None:
        return request.embedding_client
    if request.settings is None:
        raise AgentToolError("Tool search_articles_similar requires settings.")
    try:
        return build_news_embedding_client(request.settings)
    except RuntimeError as exc:
        raise AgentToolError(str(exc)) from exc


def _similar_article_payload(row: Any) -> dict[str, Any]:
    distance = float(row["distance"])
    return {
        "chunk_id": str(row["chunk_id"]),
        "article_id": str(row["article_id"]),
        "reference_index": row["reference_index"],
        "similarity": round(1 - distance, 6),
        "distance": round(distance, 6),
        "title": row["title"],
        "url": row["url_canonical"],
        "source_slug": row["source_slug"],
        "published_at": _serialize(row["published_at"]),
        "gate_source": row["gate_source"],
        "excerpt": _compact_text(
            row["chunk_text"],
            max_chars=SEARCH_ARTICLES_SIMILAR_EXCERPT_CHARS,
        ),
        "chunk_offset_start": row["chunk_offset_start"],
        "chunk_offset_end": row["chunk_offset_end"],
        "candidate_name": row["candidate_name"],
        "candidate_address": row["candidate_address"],
        "candidate_developer": row["candidate_developer"],
        "match_status": row["match_status"],
        "matched_project_id": _serialize(row["matched_project_id"]),
        "matched_evidence_id": _serialize(row["matched_evidence_id"]),
    }


def _required_uuid(value: Any, *, field_name: str) -> uuid.UUID:
    if value in (None, ""):
        raise AgentToolError(f"Tool requires {field_name}.")
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise AgentToolError(f"Tool requires a valid {field_name}.") from exc


def _required_text(value: Any, *, field_name: str) -> str:
    text_value = str(value or "").strip()
    if not text_value:
        raise AgentToolError(f"Tool requires {field_name}.")
    return text_value


def _bounded_int(value: Any, *, default: int, maximum: int) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise AgentToolError("Tool integer parameter must be a valid integer.") from exc
    return max(1, min(parsed, maximum))


def _optional_datetime(value: Any, *, field_name: str) -> datetime | None:
    if value in (None, ""):
        return None
    text_value = str(value).strip()
    try:
        if "T" not in text_value and " " not in text_value:
            parsed = datetime.combine(date.fromisoformat(text_value), datetime.min.time())
        else:
            parsed = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AgentToolError(f"Tool requires {field_name} to be an ISO date or datetime.") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _vector_literal(embedding: list[float]) -> str:
    if not embedding:
        raise AgentToolError("Tool search_articles_similar received an empty query embedding.")
    return "[" + ",".join(f"{float(value):.12g}" for value in embedding) + "]"


def _excerpt(value: str, *, max_chars: int) -> tuple[str, bool]:
    compact = _compact_whitespace(value)
    if len(compact) <= max_chars:
        return compact, False
    return compact[: max(max_chars - 3, 0)].rstrip() + "...", True


def _compact_text(value: str | None, *, max_chars: int) -> str:
    excerpt, _truncated = _excerpt(value or "", max_chars=max_chars)
    return excerpt


def _compact_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _serialize(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (uuid.UUID, date, datetime)):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_serialize(item) for item in value]
    return value

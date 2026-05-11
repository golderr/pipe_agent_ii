from __future__ import annotations

import enum
import math
import re
import uuid
from collections import defaultdict
from datetime import date, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from tcg_pipeline.agents.runner import AgentRunRequest
from tcg_pipeline.agents.tools import AgentTool, AgentToolError, AgentToolResult
from tcg_pipeline.db.models import Evidence, NewsArticle, NewsProjectReference, Project
from tcg_pipeline.matching.normalizer import normalize_address

LADBS_PERMIT_SOURCE_TYPES = ("ladbs_permit", "ladbs_inspection", "ladbs_cofo")
GET_PERMITS_OUTPUT_TOKEN_BUDGET = 2200
GET_PERMITS_DEFAULT_LIMIT = 10
GET_PERMITS_MAX_LIMIT = 10
GET_ARTICLES_ABOUT_PARCEL_OR_ADDRESS_OUTPUT_TOKEN_BUDGET = 1500
GET_ARTICLES_DEFAULT_LIMIT = 10
GET_ARTICLES_MAX_LIMIT = 10
GET_ARTICLES_DEFAULT_RADIUS_FEET = 300
GET_ARTICLES_MAX_RADIUS_FEET = 1000
GET_ARTICLES_REFERENCE_SCAN_LIMIT = 250
GET_ARTICLES_EXCERPT_CHARS = 140
LADBS_APN_DIGIT_LENGTH = 10
FEET_PER_METER = 3.280839895
EARTH_RADIUS_METERS = 6_371_000.0


def handle_get_permits_for_project(
    tool_input: dict[str, Any],
    request: AgentRunRequest,
) -> AgentToolResult:
    project_id = _required_uuid(tool_input.get("project_id"), field_name="project_id")
    limit = _bounded_int(
        tool_input.get("limit"),
        default=GET_PERMITS_DEFAULT_LIMIT,
        maximum=GET_PERMITS_MAX_LIMIT,
    )
    if request.session_factory is None:
        raise AgentToolError("Tool get_permits_for_project requires a session_factory.")

    with request.session_factory() as session:
        evidence_rows = (
            session.execute(
                _base_permit_query()
                .where(Evidence.project_id == project_id)
                .limit(limit)
            )
            .scalars()
            .all()
        )

    permits = [_permit_payload(row) for row in evidence_rows]
    payload = {
        "project_id": str(project_id),
        "limit": limit,
        "permits": permits,
        "total_returned": len(permits),
    }
    return AgentToolResult(
        payload=payload,
        summary=f"Found {len(permits)} LADBS permit evidence rows for project.",
        total_results=len(permits),
    )


def handle_get_permits_for_parcel(
    tool_input: dict[str, Any],
    request: AgentRunRequest,
) -> AgentToolResult:
    parcel_id = _required_text(tool_input.get("parcel_id"), field_name="parcel_id")
    lookup_values = _parcel_lookup_values(parcel_id)
    limit = _bounded_int(
        tool_input.get("limit"),
        default=GET_PERMITS_DEFAULT_LIMIT,
        maximum=GET_PERMITS_MAX_LIMIT,
    )
    if request.session_factory is None:
        raise AgentToolError("Tool get_permits_for_parcel requires a session_factory.")

    with request.session_factory() as session:
        evidence_rows = (
            session.execute(
                _base_permit_query()
                .where(
                    or_(
                        Evidence.raw_data["apn"].astext.in_(lookup_values),
                        Evidence.extracted_fields["apn"]["value"].astext.in_(
                            lookup_values
                        ),
                    )
                )
                .limit(limit)
            )
            .scalars()
            .all()
        )

    permits = [_permit_payload(row) for row in evidence_rows]
    payload = {
        "parcel_id": parcel_id,
        "normalized_parcel_id": _normalize_parcel_id(parcel_id),
        "limit": limit,
        "permits": permits,
        "total_returned": len(permits),
    }
    return AgentToolResult(
        payload=payload,
        summary=f"Found {len(permits)} LADBS permit evidence rows for parcel.",
        total_results=len(permits),
    )


def handle_get_articles_about_parcel_or_address(
    tool_input: dict[str, Any],
    request: AgentRunRequest,
) -> AgentToolResult:
    parcel_id = _optional_text(tool_input.get("parcel_id"))
    address = _optional_text(tool_input.get("address"))
    if parcel_id is None and address is None:
        raise AgentToolError("Tool requires parcel_id or address.")
    limit = _bounded_int(
        tool_input.get("limit"),
        default=GET_ARTICLES_DEFAULT_LIMIT,
        maximum=GET_ARTICLES_MAX_LIMIT,
    )
    radius_feet = _bounded_int(
        tool_input.get("radius_feet"),
        default=GET_ARTICLES_DEFAULT_RADIUS_FEET,
        maximum=GET_ARTICLES_MAX_RADIUS_FEET,
    )
    if request.session_factory is None:
        raise AgentToolError(
            "Tool get_articles_about_parcel_or_address requires a session_factory."
        )

    normalized_address = _normalized_address(address)
    with request.session_factory() as session:
        project_match_reasons: dict[uuid.UUID, set[str]] = defaultdict(set)
        if parcel_id is not None:
            for project_id in _project_ids_for_parcel(session, parcel_id):
                project_match_reasons[project_id].add("parcel")
        if normalized_address is not None:
            for project_id in _project_ids_for_address(session, normalized_address):
                project_match_reasons[project_id].add("address")

        distance_feet_by_project_id = _nearby_project_distances(
            session,
            tuple(project_match_reasons),
            radius_feet=radius_feet,
        )
        for project_id in distance_feet_by_project_id:
            project_match_reasons.setdefault(project_id, set()).add("radius")

        project_evidence_rows = _news_evidence_for_projects(
            session,
            tuple(project_match_reasons),
        )
        reference_rows = _references_for_normalized_address(
            session,
            normalized_address=normalized_address,
        )
        project_reference_ids = _reference_ids_for_evidence(project_evidence_rows)
        address_reference_ids = [reference.id for reference in reference_rows]
        references_by_id = _news_references_by_id(
            session,
            tuple(set(project_reference_ids) | set(address_reference_ids)),
        )
        evidence_by_reference_id = _news_evidence_by_reference_id(
            session,
            tuple(set(project_reference_ids) | set(address_reference_ids)),
        )
        article_ids = {
            article_id
            for article_id in (
                _news_article_id_for_evidence(evidence)
                for evidence in project_evidence_rows
            )
            if article_id is not None
        }
        article_ids.update(reference.article_id for reference in references_by_id.values())
        articles_by_id = _news_articles_by_id(session, tuple(article_ids))

    matches: list[dict[str, Any]] = []
    seen_keys: set[tuple[str | None, str | None, str | None]] = set()
    for evidence in project_evidence_rows:
        reference = _reference_for_evidence(evidence, references_by_id)
        article = _article_for_evidence_or_reference(
            evidence=evidence,
            reference=reference,
            articles_by_id=articles_by_id,
        )
        match_basis = _project_news_match_basis(
            evidence.project_id,
            project_match_reasons=project_match_reasons,
        )
        payload = _article_payload(
            evidence=evidence,
            reference=reference,
            article=article,
            match_basis=match_basis,
            distance_feet=distance_feet_by_project_id.get(evidence.project_id),
        )
        _append_unique_article_payload(matches, payload, seen_keys)

    for reference in reference_rows:
        evidence = evidence_by_reference_id.get(reference.id)
        article = articles_by_id.get(reference.article_id)
        payload = _article_payload(
            evidence=evidence,
            reference=reference,
            article=article,
            match_basis="address_reference_exact",
            distance_feet=None,
        )
        _append_unique_article_payload(matches, payload, seen_keys)

    payload = {
        "parcel_id": parcel_id,
        "normalized_parcel_id": _normalize_parcel_id(parcel_id) if parcel_id else None,
        "address": address,
        "normalized_address": normalized_address,
        "radius_feet": radius_feet,
        "limit": limit,
        "total_available": len(matches),
        "total_returned": min(len(matches), limit),
        "matches": matches[:limit],
        "matching_note": (
            "Uses LADBS APN/address project crosswalks, project radius expansion when an "
            "anchor project has coordinates, and exact normalized news-reference address matches."
        ),
    }
    return AgentToolResult(
        payload=payload,
        summary=f"Found {payload['total_returned']} prior news article reference(s).",
        total_results=len(matches),
    )


GET_PERMITS_FOR_PROJECT_TOOL = AgentTool(
    name="get_permits_for_project",
    description=(
        "Fetch compact active LADBS permit, inspection, and CofO evidence rows for a known "
        "TCG project_id. Use this to compare a new permit against already-attributed permit "
        "history. Input requires project_id as a UUID. Optional limit defaults to 10 and is "
        "capped at 10."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "project_id": {
                "type": "string",
                "description": "UUID of the TCG project to inspect.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": GET_PERMITS_MAX_LIMIT,
                "description": "Maximum permit evidence rows to return. Defaults to 10.",
            },
        },
        "required": ["project_id"],
        "additionalProperties": False,
    },
    output_token_budget=GET_PERMITS_OUTPUT_TOKEN_BUDGET,
    handler=handle_get_permits_for_project,
)

GET_PERMITS_FOR_PARCEL_TOOL = AgentTool(
    name="get_permits_for_parcel",
    description=(
        "Fetch compact active LADBS permit, inspection, and CofO evidence rows whose raw or "
        "mapped APN matches a parcel ID. Use this when the intake row has an APN and project "
        "attribution is uncertain. Input requires parcel_id. Optional limit defaults to 10 "
        "and is capped at 10."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "parcel_id": {
                "type": "string",
                "description": "Assessor parcel number/APN, with or without separators.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": GET_PERMITS_MAX_LIMIT,
                "description": "Maximum permit evidence rows to return. Defaults to 10.",
            },
        },
        "required": ["parcel_id"],
        "additionalProperties": False,
    },
    output_token_budget=GET_PERMITS_OUTPUT_TOKEN_BUDGET,
    handler=handle_get_permits_for_parcel,
)

GET_ARTICLES_ABOUT_PARCEL_OR_ADDRESS_TOOL = AgentTool(
    name="get_articles_about_parcel_or_address",
    description=(
        "Fetch compact prior news article references related to an LADBS parcel/APN or address. "
        "Use this as supporting context when a permit may refer to an already covered project "
        "or nearby phase. Input requires parcel_id or address. Optional radius_feet defaults to "
        "300 and is capped at 1000; radius expansion applies when the APN/address resolves to "
        "a project with coordinates. Optional limit defaults to 10 and is capped at 10; output "
        "is a lean supporting-context summary, not full evidence detail."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "parcel_id": {
                "type": "string",
                "description": "Assessor parcel number/APN, with or without separators.",
            },
            "address": {
                "type": "string",
                "description": "Permit or project address to normalize and search in news refs.",
            },
            "radius_feet": {
                "type": "integer",
                "minimum": 1,
                "maximum": GET_ARTICLES_MAX_RADIUS_FEET,
                "description": "Nearby-project radius when an anchor project has coordinates.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": GET_ARTICLES_MAX_LIMIT,
                "description": "Maximum article references to return. Defaults to 10.",
            },
        },
        "anyOf": [{"required": ["parcel_id"]}, {"required": ["address"]}],
        "additionalProperties": False,
    },
    output_token_budget=GET_ARTICLES_ABOUT_PARCEL_OR_ADDRESS_OUTPUT_TOKEN_BUDGET,
    handler=handle_get_articles_about_parcel_or_address,
)


def _base_permit_query():
    return (
        select(Evidence)
        .where(
            Evidence.source_type.in_(LADBS_PERMIT_SOURCE_TYPES),
            Evidence.superseded_at.is_(None),
        )
        .order_by(
            Evidence.evidence_date.desc().nulls_last(),
            Evidence.collected_at.desc(),
            Evidence.id.desc(),
        )
    )


def _project_ids_for_parcel(session: Any, parcel_id: str) -> tuple[uuid.UUID, ...]:
    lookup_values = _parcel_lookup_values(parcel_id)
    return tuple(
        project_id
        for project_id in session.execute(
            select(Evidence.project_id)
            .where(
                Evidence.source_type.in_(LADBS_PERMIT_SOURCE_TYPES),
                Evidence.superseded_at.is_(None),
                Evidence.project_id.is_not(None),
                or_(
                    Evidence.raw_data["apn"].astext.in_(lookup_values),
                    Evidence.extracted_fields["apn"]["value"].astext.in_(lookup_values),
                ),
            )
            .order_by(
                Evidence.evidence_date.desc().nulls_last(),
                Evidence.collected_at.desc(),
                Evidence.id.desc(),
            )
        )
        .scalars()
        .all()
        if project_id is not None
    )


def _project_ids_for_address(session: Any, normalized_address: str) -> tuple[uuid.UUID, ...]:
    return tuple(
        session.execute(
            select(Project.id).where(Project.canonical_address == normalized_address)
        )
        .scalars()
        .all()
    )


def _nearby_project_distances(
    session: Any,
    anchor_project_ids: tuple[uuid.UUID, ...],
    *,
    radius_feet: int,
) -> dict[uuid.UUID, int]:
    if not anchor_project_ids:
        return {}
    anchor_project_id_set = set(anchor_project_ids)
    # LA scale is small enough for an in-memory haversine pass. At 25-market
    # scale, switch this to ST_DWithin against the existing Project.location
    # GIST index so radius expansion stays indexed.
    projects = (
        session.execute(
            select(Project.id, Project.lat, Project.lng).where(
                Project.lat.is_not(None),
                Project.lng.is_not(None),
            )
        )
        .all()
    )
    anchors = [
        (project_id, lat, lng)
        for project_id, lat, lng in projects
        if project_id in anchor_project_id_set and lat is not None and lng is not None
    ]
    if not anchors:
        return {}

    distances: dict[uuid.UUID, int] = {}
    for project_id, lat, lng in projects:
        if project_id in anchor_project_id_set or lat is None or lng is None:
            continue
        min_distance = min(
            _distance_feet(anchor_lat, anchor_lng, lat, lng)
            for _anchor_id, anchor_lat, anchor_lng in anchors
        )
        if 0 < min_distance <= radius_feet:
            distances[project_id] = int(round(min_distance))
    return distances


def _news_evidence_for_projects(
    session: Any,
    project_ids: tuple[uuid.UUID, ...],
) -> tuple[Evidence, ...]:
    if not project_ids:
        return ()
    return tuple(
        session.execute(
            select(Evidence)
            .where(
                Evidence.source_type == "news_article",
                Evidence.superseded_at.is_(None),
                Evidence.project_id.in_(project_ids),
            )
            .order_by(
                Evidence.evidence_date.desc().nulls_last(),
                Evidence.collected_at.desc(),
                Evidence.id.desc(),
            )
            .limit(GET_ARTICLES_REFERENCE_SCAN_LIMIT)
        )
        .scalars()
        .all()
    )


def _references_for_normalized_address(
    session: Any,
    *,
    normalized_address: str | None,
) -> tuple[NewsProjectReference, ...]:
    if normalized_address is None:
        return ()
    # First slice keeps this schema-free by normalizing recent references at
    # query time. At multi-market scale, persist normalized_candidate_address on
    # news_project_references and index it instead of scanning this capped set.
    candidates = (
        session.execute(
            select(NewsProjectReference)
            .join(NewsArticle, NewsArticle.id == NewsProjectReference.article_id)
            .where(NewsProjectReference.candidate_address.is_not(None))
            .order_by(
                NewsArticle.published_at.desc().nulls_last(),
                NewsProjectReference.created_at.desc(),
                NewsProjectReference.id.desc(),
            )
            .limit(GET_ARTICLES_REFERENCE_SCAN_LIMIT)
        )
        .scalars()
        .all()
    )
    return tuple(
        reference
        for reference in candidates
        if _normalized_address(reference.candidate_address, city=reference.candidate_city)
        == normalized_address
    )


def _news_references_by_id(
    session: Any,
    reference_ids: tuple[uuid.UUID, ...],
) -> dict[uuid.UUID, NewsProjectReference]:
    if not reference_ids:
        return {}
    return {
        reference.id: reference
        for reference in session.execute(
            select(NewsProjectReference).where(NewsProjectReference.id.in_(reference_ids))
        )
        .scalars()
        .all()
    }


def _news_evidence_by_reference_id(
    session: Any,
    reference_ids: tuple[uuid.UUID, ...],
) -> dict[uuid.UUID, Evidence]:
    if not reference_ids:
        return {}
    evidence_rows = (
        session.execute(
            select(Evidence)
            .where(
                Evidence.source_type == "news_article",
                Evidence.superseded_at.is_(None),
                Evidence.source_record_id.in_(
                    [str(reference_id) for reference_id in reference_ids]
                ),
            )
            .order_by(Evidence.collected_at.desc(), Evidence.id.desc())
        )
        .scalars()
        .all()
    )
    evidence_by_reference_id: dict[uuid.UUID, Evidence] = {}
    for evidence in evidence_rows:
        reference_id = _uuid_or_none(evidence.source_record_id)
        if reference_id is not None:
            evidence_by_reference_id.setdefault(reference_id, evidence)
    return evidence_by_reference_id


def _news_articles_by_id(
    session: Any,
    article_ids: tuple[uuid.UUID, ...],
) -> dict[uuid.UUID, NewsArticle]:
    if not article_ids:
        return {}
    return {
        article.id: article
        for article in session.execute(
            select(NewsArticle)
            .where(NewsArticle.id.in_(article_ids))
            .options(selectinload(NewsArticle.source))
        )
        .scalars()
        .all()
    }


def _reference_ids_for_evidence(evidence_rows: tuple[Evidence, ...]) -> tuple[uuid.UUID, ...]:
    return tuple(
        reference_id
        for reference_id in (_uuid_or_none(evidence.source_record_id) for evidence in evidence_rows)
        if reference_id is not None
    )


def _reference_for_evidence(
    evidence: Evidence,
    references_by_id: dict[uuid.UUID, NewsProjectReference],
) -> NewsProjectReference | None:
    reference_id = _uuid_or_none(evidence.source_record_id)
    if reference_id is None:
        return None
    return references_by_id.get(reference_id)


def _article_for_evidence_or_reference(
    *,
    evidence: Evidence | None,
    reference: NewsProjectReference | None,
    articles_by_id: dict[uuid.UUID, NewsArticle],
) -> NewsArticle | None:
    if reference is not None:
        return articles_by_id.get(reference.article_id)
    if evidence is None:
        return None
    article_id = _news_article_id_for_evidence(evidence)
    if article_id is None:
        return None
    return articles_by_id.get(article_id)


def _news_article_id_for_evidence(evidence: Evidence) -> uuid.UUID | None:
    raw_data = evidence.raw_data or {}
    return _uuid_or_none(raw_data.get("article_id"))


def _project_news_match_basis(
    project_id: uuid.UUID | None,
    *,
    project_match_reasons: dict[uuid.UUID, set[str]],
) -> str:
    if project_id is None:
        return "project_news_evidence"
    reasons = project_match_reasons.get(project_id, set())
    if "parcel" in reasons:
        return "parcel_project_news_evidence"
    if "address" in reasons:
        return "address_project_news_evidence"
    if "radius" in reasons:
        return "nearby_project_news_evidence"
    return "project_news_evidence"


def _article_payload(
    *,
    evidence: Evidence | None,
    reference: NewsProjectReference | None,
    article: NewsArticle | None,
    match_basis: str,
    distance_feet: int | None,
) -> dict[str, Any]:
    source_slug = article.source.slug if article is not None and article.source else None
    payload = {
        "reference_id": _serialize(reference.id if reference is not None else None),
        "title": article.title if article is not None else None,
        "source_slug": source_slug,
        "published_at": _serialize(article.published_at if article is not None else None),
        "match_status": reference.match_status if reference is not None else None,
        "candidate_name": reference.candidate_name if reference is not None else None,
        "candidate_address": reference.candidate_address if reference is not None else None,
        "match_basis": match_basis,
        "distance_feet": distance_feet,
        "excerpt": _article_excerpt(reference=reference, evidence=evidence, article=article),
    }
    return {key: value for key, value in payload.items() if value not in (None, "")}


def _append_unique_article_payload(
    matches: list[dict[str, Any]],
    payload: dict[str, Any],
    seen_keys: set[tuple[str | None, str | None, str | None]],
) -> None:
    key = (
        payload.get("reference_id"),
        payload.get("title"),
        payload.get("source_slug"),
    )
    if key in seen_keys:
        return
    seen_keys.add(key)
    matches.append(payload)


def _article_excerpt(
    *,
    reference: NewsProjectReference | None,
    evidence: Evidence | None,
    article: NewsArticle | None,
) -> str | None:
    if reference is not None:
        for item in _passage_excerpt_items(reference.passage_excerpts):
            passage = _compact_text(item.get("passage"), max_chars=GET_ARTICLES_EXCERPT_CHARS)
            if passage:
                return passage
    if evidence is not None:
        notes = _compact_text(evidence.notes, max_chars=GET_ARTICLES_EXCERPT_CHARS)
        if notes:
            return notes
    if article is not None:
        return _compact_text(article.body_text, max_chars=GET_ARTICLES_EXCERPT_CHARS)
    return None


def _passage_excerpt_items(value: Any) -> tuple[dict[str, Any], ...]:
    if isinstance(value, list):
        candidates = value
    elif isinstance(value, dict):
        nested = value.get("passage_excerpts") or value.get("excerpts") or value.get("items")
        candidates = nested if isinstance(nested, list) else list(value.values())
    else:
        candidates = []
    return tuple(item for item in candidates if isinstance(item, dict))


def _normalized_address(
    address: str | None,
    *,
    city: str | None = None,
) -> str | None:
    text_value = _optional_text(address)
    if text_value is None:
        return None
    # AGENT.3 currently routes LADBS-only permit intake. Future non-LA permit
    # profiles should pass jurisdiction/market into this helper instead of
    # relying on Los Angeles defaults.
    normalized = normalize_address(
        text_value,
        city=city or "Los Angeles",
        state="CA",
        market="los_angeles",
    )
    return normalized.canonical_address


def _distance_feet(
    lat_1: float,
    lng_1: float,
    lat_2: float,
    lng_2: float,
) -> float:
    phi_1 = math.radians(lat_1)
    phi_2 = math.radians(lat_2)
    delta_phi = math.radians(lat_2 - lat_1)
    delta_lambda = math.radians(lng_2 - lng_1)
    haversine = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi_1) * math.cos(phi_2) * math.sin(delta_lambda / 2) ** 2
    )
    meters = 2 * EARTH_RADIUS_METERS * math.atan2(
        math.sqrt(haversine),
        math.sqrt(1 - haversine),
    )
    return meters * FEET_PER_METER


def _permit_payload(evidence: Evidence) -> dict[str, Any]:
    payload = {
        "evidence_id": str(evidence.id),
        "project_id": _serialize(evidence.project_id),
        "source_type": evidence.source_type,
        "source_record_id": evidence.source_record_id,
        "evidence_date": _serialize(evidence.evidence_date),
        "collected_at": _serialize(evidence.collected_at),
        "permit_number": _first_value(evidence, "permit_number", "pcis_permit", "permit_nbr")
        or evidence.source_record_id,
        "apn": _first_value(evidence, "apn"),
        "permit_type": _first_value(evidence, "permit_type"),
        "permit_sub_type": _first_value(evidence, "permit_sub_type"),
        "status_evidence_type": _first_value(evidence, "status_evidence_type"),
        "status_desc": _first_value(evidence, "status_desc", "latest_status"),
        "issue_date": _first_value(evidence, "permit_issue_date", "issue_date"),
        "inspection_date": _first_value(evidence, "inspection_date"),
        "cofo_issue_date": _first_value(evidence, "cofo_issue_date"),
        "description": _compact_text(
            _first_value(evidence, "description", "work_desc"),
            max_chars=360,
        ),
        "valuation": _first_value(evidence, "valuation"),
        "applicant": _first_value(evidence, "applicant", "applicant_business_name"),
        "total_units": _first_value(evidence, "total_units", "of_residential_dwelling_units"),
        "notes": _compact_text(evidence.notes, max_chars=180),
    }
    return {key: value for key, value in payload.items() if value not in (None, "")}


def _first_value(evidence: Evidence, *field_names: str) -> Any:
    extracted_fields = evidence.extracted_fields or {}
    raw_data = evidence.raw_data or {}
    for field_name in field_names:
        extracted = extracted_fields.get(field_name)
        if isinstance(extracted, dict):
            value = extracted.get("value")
            if value not in (None, ""):
                return _serialize(value)
        value = raw_data.get(field_name)
        if value not in (None, ""):
            return _serialize(value)
    return None


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


def _optional_text(value: Any) -> str | None:
    text_value = str(value or "").strip()
    return text_value or None


def _bounded_int(value: Any, *, default: int, maximum: int) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise AgentToolError("Tool integer parameter must be a valid integer.") from exc
    return max(1, min(parsed, maximum))


def _parcel_lookup_values(parcel_id: str) -> list[str]:
    values = {parcel_id.strip()}
    normalized = _normalize_parcel_id(parcel_id)
    if normalized is not None:
        values.add(normalized)
    return sorted(value for value in values if value)


def _normalize_parcel_id(parcel_id: str) -> str | None:
    # LADBS currently uses LA County 10-digit APNs; future non-LADBS permit
    # profiles should own their own parcel normalizer.
    digits = re.sub(r"[^\d]", "", parcel_id)
    return digits if len(digits) == LADBS_APN_DIGIT_LENGTH else None


def _uuid_or_none(value: Any) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _compact_text(value: Any, *, max_chars: int = 240) -> str | None:
    text_value = str(value or "").strip()
    if not text_value:
        return None
    compact = re.sub(r"\s+", " ", text_value)
    if len(compact) <= max_chars:
        return compact
    return compact[: max(max_chars - 3, 0)].rstrip() + "..."


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

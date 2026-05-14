from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from tcg_pipeline.db.evidence import serialize_json
from tcg_pipeline.db.models import Evidence
from tcg_pipeline.permit_numbers import ladbs_permit_number_from_evidence


class SnippetFields(BaseModel):
    field_name: str | None = None
    extracted_value: Any = None
    extracted_confidence: Any = None


class SnippetSourceMetadata(BaseModel):
    evidence_id: uuid.UUID
    source_type: str
    source_tier: int
    source_record_id: str | None = None
    ingest_method: str
    collected_at: datetime
    evidence_date: date | None = None


class SnippetPayload(BaseModel):
    summary: str
    detail: str
    fields: SnippetFields
    source_metadata: SnippetSourceMetadata
    # Source-type-specific structured fields surfaced on review cards so the
    # reviewer can scan permit number / type / status / CoStar property ID /
    # upload date / etc. without parsing the prose summary. Populated per
    # source family (see render_ladbs_permit_snippet, render_costar_snippet).
    # Empty dict for source types that don't define structured fields yet.
    source_fields: dict[str, Any] = Field(default_factory=dict)
    external_link: str | None = None
    highlights: list[dict[str, Any]] = Field(default_factory=list)


SnippetRenderer = Callable[[Evidence, str | None], SnippetPayload]


def render_snippet(evidence: Evidence, field_name: str | None = None) -> SnippetPayload:
    renderer = SNIPPET_RENDERERS.get(evidence.source_type, render_generic_snippet)
    return renderer(evidence, field_name)


# LADBS summaries lead with permit/inspection metadata because that identifies
# the source record; requested field/value data is still returned under fields.
def render_ladbs_permit_snippet(
    evidence: Evidence,
    field_name: str | None = None,
) -> SnippetPayload:
    record_id = ladbs_permit_number_from_evidence(evidence) or "unknown"
    evidence_type = _text(_extracted_value(evidence, "status_evidence_type"))
    issue_date = _text(
        _first_extracted_value(evidence, "permit_issue_date", "status_evidence_date")
    )
    status = _text(
        _first_extracted_value(evidence, "status_desc", "latest_status", "permit_status")
    )
    # Pull structured permit fields directly from raw_data for the source-fields
    # subheader. Reviewer can scan permit_type and status_desc on the card
    # without parsing the prose summary.
    raw_status = _text(_raw_value(evidence, "status_desc"))
    permit_type = _text(_raw_value(evidence, "permit_type"))
    permit_sub_type = _text(_raw_value(evidence, "permit_sub_type"))
    work_desc = _text(_raw_value(evidence, "work_desc"))
    summary = _join_parts(f"PCIS {record_id}", evidence_type, _labeled("permit status", status))
    detail = _join_parts(
        f"Permit PCIS {record_id}",
        _labeled("type", permit_type),
        _labeled("issued", issue_date),
        _labeled("current status", status or raw_status),
    )
    return _payload(
        evidence,
        field_name,
        summary=summary,
        detail=detail,
        source_fields={
            "permit_number": record_id,
            "permit_type": permit_type,
            "permit_sub_type": permit_sub_type,
            "status_desc": status or raw_status,
            "issue_date": issue_date,
            "work_desc": work_desc,
        },
    )


def render_ladbs_inspection_snippet(
    evidence: Evidence,
    field_name: str | None = None,
) -> SnippetPayload:
    inspection_name = _text(_extracted_value(evidence, "inspection"))
    result = _text(_extracted_value(evidence, "inspection_result"))
    permit_status = _text(_extracted_value(evidence, "permit_status"))
    inspection_date = _text(_extracted_value(evidence, "inspection_date"))
    summary = _join_parts(
        inspection_name or "Inspection",
        _labeled("result", result),
        _labeled("permit", permit_status),
    )
    detail = _join_parts(
        _labeled("Inspection date", inspection_date),
        _labeled("result", result),
        _labeled("permit status", permit_status),
    )
    return _payload(evidence, field_name, summary=summary, detail=detail)


def render_ladbs_cofo_snippet(
    evidence: Evidence,
    field_name: str | None = None,
) -> SnippetPayload:
    cofo_number = (
        _text(_first_extracted_value(evidence, "cofo_number"))
        or evidence.source_record_id
    )
    issue_date = _text(_first_extracted_value(evidence, "cofo_issue_date", "status_evidence_date"))
    status = _text(_first_extracted_value(evidence, "latest_status", "cofo_status"))
    summary = _join_parts("CofO", _labeled("issued", issue_date), _labeled("status", status))
    detail = _join_parts(
        _labeled("CofO", cofo_number),
        _labeled("issued", issue_date),
        _labeled("status", status),
    )
    return _payload(evidence, field_name, summary=summary, detail=detail)


def render_costar_snippet(
    evidence: Evidence,
    field_name: str | None = None,
) -> SnippetPayload:
    value = _field_value(evidence, field_name)
    raw_data = _mapping(evidence.raw_data)
    costar_property_id = _text(
        _first_value(raw_data, "costar_property_id", "Property ID", "property_id")
    )
    upload_date = _text(
        _first_value(raw_data, "upload_date", "uploaded_at", "as_of_date")
    ) or _text(evidence.collected_at)
    summary = _field_summary(field_name, value, fallback="CoStar evidence")
    detail = _join_parts(
        "CoStar",
        _labeled("Property ID", costar_property_id or evidence.source_record_id),
        _labeled("uploaded", upload_date),
        summary if field_name else None,
    )
    return _payload(
        evidence,
        field_name,
        summary=summary,
        detail=detail,
        source_fields={
            "costar_property_id": costar_property_id or evidence.source_record_id,
            "upload_date": upload_date,
            "source_field": field_name,
        },
    )


def render_pipedream_snippet(
    evidence: Evidence,
    field_name: str | None = None,
) -> SnippetPayload:
    value = _field_value(evidence, field_name)
    last_editor = _text(_extracted_value(evidence, "last_editor"))
    last_edit_date = _text(_extracted_value(evidence, "last_edit_date"))
    summary = _field_summary(field_name, value, fallback="Pipedream snapshot")
    detail = _join_parts(
        summary,
        _labeled("last edited by", last_editor),
        _labeled("last edited", last_edit_date),
    )
    return _payload(evidence, field_name, summary=summary, detail=detail)


def render_news_article_snippet(
    evidence: Evidence,
    field_name: str | None = None,
) -> SnippetPayload:
    value = _field_value(evidence, field_name)
    raw_data = _mapping(evidence.raw_data)
    source_name = _text(_first_value(raw_data, "source_name", "publication", "publisher"))
    published_at = _text(_first_value(raw_data, "published_at", "publication_date", "date"))
    author = _text(_first_value(raw_data, "author", "byline"))
    summary = _field_summary(field_name, value, fallback="News article evidence")
    return _payload(
        evidence,
        field_name,
        summary=summary,
        detail=_join_parts(source_name or "News article", published_at, author),
        external_link=_external_link(evidence),
        highlights=_highlights(evidence, field_name),
    )


def render_developer_website_snippet(
    evidence: Evidence,
    field_name: str | None = None,
) -> SnippetPayload:
    value = _field_value(evidence, field_name)
    summary = _field_summary(field_name, value, fallback="Developer website evidence")
    detail = _join_parts(
        summary,
        _labeled("scraped", evidence.collected_at.isoformat()),
    )
    return _payload(
        evidence,
        field_name,
        summary=summary,
        detail=detail,
        external_link=_external_link(evidence),
    )


def render_override_snippet(
    evidence: Evidence,
    field_name: str | None = None,
) -> SnippetPayload:
    value = _field_value(evidence, field_name)
    raw_data = _mapping(evidence.raw_data)
    set_by = _text(_first_value(raw_data, "set_by", "actor", "reviewed_by"))
    set_at = _text(_first_value(raw_data, "set_at", "timestamp", "created_at"))
    mode = _text(_first_value(raw_data, "mode", "override_mode"))
    note = _text(_first_value(raw_data, "note", "notes"))
    summary = _field_summary(field_name, value, fallback="Researcher override")
    detail = _join_parts(summary, _labeled("set by", set_by), set_at, mode, note)
    return _payload(evidence, field_name, summary=summary, detail=detail)


def render_computed_snippet(
    evidence: Evidence,
    field_name: str | None = None,
) -> SnippetPayload:
    raw_data = _mapping(evidence.raw_data)
    rule = _text(_first_value(raw_data, "rule_applied", "rule"))
    inputs = _first_value(raw_data, "inputs", "input_values")
    detail = _join_parts(_labeled("Rule", rule), _labeled("inputs", _display_value(inputs)))
    return _payload(
        evidence,
        field_name,
        summary=_field_summary(field_name, _field_value(evidence, field_name), fallback="Computed"),
        detail=detail or "Computed evidence",
    )


def render_generic_snippet(
    evidence: Evidence,
    field_name: str | None = None,
) -> SnippetPayload:
    value = _field_value(evidence, field_name)
    summary = _field_summary(
        field_name,
        value,
        fallback=f"{_source_label(evidence.source_type)} evidence",
    )
    detail = _join_parts(
        _source_label(evidence.source_type),
        _labeled("record", evidence.source_record_id),
        _labeled("evidence date", _text(evidence.evidence_date)),
    )
    return _payload(
        evidence,
        field_name,
        summary=summary,
        detail=detail,
        external_link=_external_link(evidence),
        highlights=_highlights(evidence, field_name),
    )


SNIPPET_RENDERERS: dict[str, SnippetRenderer] = {
    "ladbs_permit": render_ladbs_permit_snippet,
    "ladbs_inspection": render_ladbs_inspection_snippet,
    "ladbs_cofo": render_ladbs_cofo_snippet,
    "zimas_pdis": render_generic_snippet,
    "zimas_arcgis": render_generic_snippet,
    "la_case_report": render_generic_snippet,
    "lahd_affordable": render_generic_snippet,
    "costar": render_costar_snippet,
    "pipedream": render_pipedream_snippet,
    "news_article": render_news_article_snippet,
    "developer_website": render_developer_website_snippet,
    "researcher_override": render_override_snippet,
    "computed": render_computed_snippet,
}


def _payload(
    evidence: Evidence,
    field_name: str | None,
    *,
    summary: str,
    detail: str,
    external_link: str | None = None,
    highlights: list[dict[str, Any]] | None = None,
    source_fields: dict[str, Any] | None = None,
) -> SnippetPayload:
    return SnippetPayload(
        summary=summary,
        detail=detail,
        fields=SnippetFields(
            field_name=field_name,
            extracted_value=serialize_json(_field_value(evidence, field_name)),
            extracted_confidence=serialize_json(_field_confidence(evidence, field_name)),
        ),
        source_metadata=SnippetSourceMetadata(
            evidence_id=evidence.id,
            source_type=evidence.source_type,
            source_tier=evidence.source_tier,
            source_record_id=evidence.source_record_id,
            ingest_method=evidence.ingest_method,
            collected_at=evidence.collected_at,
            evidence_date=evidence.evidence_date,
        ),
        source_fields={
            key: serialize_json(value)
            for key, value in (source_fields or {}).items()
            if value not in (None, "")
        },
        external_link=external_link,
        highlights=highlights or [],
    )


def _field_summary(field_name: str | None, value: Any, *, fallback: str) -> str:
    if field_name is None:
        return fallback
    return f"{field_name}: {_display_value(value)}"


def _field_value(evidence: Evidence, field_name: str | None) -> Any:
    if field_name is None:
        return None
    return _extracted_value(evidence, field_name)


def _field_confidence(evidence: Evidence, field_name: str | None) -> Any:
    if field_name is None:
        return None
    payload = _extracted_payload(evidence, field_name)
    return payload.get("confidence") if payload is not None else None


def _extracted_value(evidence: Evidence, field_name: str) -> Any:
    payload = _extracted_payload(evidence, field_name)
    if payload is None:
        return None
    return payload.get("value")


def _first_extracted_value(evidence: Evidence, *field_names: str) -> Any:
    for field_name in field_names:
        value = _extracted_value(evidence, field_name)
        if _has_value(value):
            return value
    return None


def _extracted_payload(evidence: Evidence, field_name: str) -> Mapping[str, Any] | None:
    extracted_fields = _mapping(evidence.extracted_fields)
    payload = extracted_fields.get(field_name)
    if isinstance(payload, Mapping):
        return payload
    if payload is not None:
        return {"value": payload, "confidence": None}
    return None


def _raw_value(evidence: Evidence, field_name: str) -> Any:
    return _mapping(evidence.raw_data).get(field_name)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first_value(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if _has_value(value):
            return value
    return None


def _external_link(evidence: Evidence) -> str | None:
    raw_data = _mapping(evidence.raw_data)
    extracted_fields = _mapping(evidence.extracted_fields)
    for value in (
        _first_value(raw_data, "source_url", "url", "link", "article_url"),
        _first_value(extracted_fields, "source_url", "url", "link"),
    ):
        if isinstance(value, Mapping):
            value = value.get("value")
        text = _text(value)
        if text:
            return text

    source_urls = _extracted_value(evidence, "source_urls")
    if isinstance(source_urls, list):
        return next((_text(item) for item in source_urls if _text(item)), None)
    return None


def _highlights(evidence: Evidence, field_name: str | None) -> list[dict[str, Any]]:
    extracted_fields = _mapping(evidence.extracted_fields)
    candidates: list[Any] = []
    # Phase D should write field-specific highlights here; top-level/raw
    # locations are compatibility fallbacks for older extracted rows.
    if field_name is not None:
        payload = extracted_fields.get(field_name)
        if isinstance(payload, Mapping):
            candidates.extend([payload.get("highlights"), payload.get("highlight")])
    candidates.extend(
        [
            extracted_fields.get("highlights"),
            _mapping(evidence.raw_data).get("highlights"),
        ]
    )
    for candidate in candidates:
        if isinstance(candidate, list):
            return [
                serialize_json(item)
                for item in candidate
                if isinstance(item, dict)
            ]
        if isinstance(candidate, dict):
            return [serialize_json(candidate)]
    return []


def _join_parts(*parts: str | None) -> str:
    return " · ".join(part for part in parts if part)


def _labeled(label: str, value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    return f"{label}: {text}"


def _source_label(source_type: str) -> str:
    return source_type.replace("_", " ").title()


def _display_value(value: Any) -> str:
    if value is None:
        return "n/a"
    serialized = serialize_json(value)
    if isinstance(serialized, list):
        return ", ".join(str(item) for item in serialized)
    if isinstance(serialized, dict):
        return ", ".join(f"{key}: {item}" for key, item in serialized.items())
    return str(serialized)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return True

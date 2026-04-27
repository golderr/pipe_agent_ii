from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from tcg_pipeline.db.evidence import serialize_json
from tcg_pipeline.db.models import Project, ResearcherOverride

_TABLE_EXISTS_CACHE_KEY = "researcher_overrides_table_exists"
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _NormalizedTablePayload:
    value: Any
    set_by_label: str | None
    set_at: datetime
    has_explicit_set_at: bool
    has_explicit_set_by_label: bool
    note: str | None
    source_url: str | None
    mode: str | None
    baseline: dict | None


def active_researcher_overrides_for_project(
    session: Session,
    project: Project,
) -> dict[str, dict[str, Any]]:
    legacy_overrides = normalize_legacy_researcher_overrides(project.researcher_override)
    if researcher_overrides_table_exists(session):
        rows = session.execute(
            select(ResearcherOverride)
            .where(
                ResearcherOverride.project_id == project.id,
                ResearcherOverride.cleared_at.is_(None),
            )
            .order_by(ResearcherOverride.field_name)
        ).scalars().all()
        table_overrides = {row.field_name: _override_payload_from_row(row) for row in rows}
        legacy_only_fields = sorted(set(legacy_overrides) - set(table_overrides))
        if legacy_only_fields:
            LOGGER.warning(
                "Project %s has legacy-only researcher_override keys not present in "
                "researcher_overrides: %s",
                project.id,
                ", ".join(legacy_only_fields),
            )
        return {**legacy_overrides, **table_overrides}

    return legacy_overrides


def project_has_active_researcher_override(
    session: Session,
    project: Project,
    field_name: str,
) -> bool:
    if researcher_overrides_table_exists(session):
        row_exists = session.execute(
            select(ResearcherOverride.id)
            .where(
                ResearcherOverride.project_id == project.id,
                ResearcherOverride.field_name == field_name,
                ResearcherOverride.cleared_at.is_(None),
            )
            .limit(1)
        ).scalar_one_or_none()
        if row_exists is not None:
            return True

    return field_name in normalize_legacy_researcher_overrides(project.researcher_override)


def upsert_researcher_overrides(
    session: Session,
    project: Project,
    incoming: Mapping[str, Mapping[str, Any]],
    *,
    set_by_user_id: Any | None = None,
) -> None:
    if not incoming:
        return

    project.researcher_override = _merge_legacy_researcher_overrides(
        project.researcher_override,
        incoming,
    )

    if not researcher_overrides_table_exists(session):
        return

    now = datetime.now(UTC)
    for field_name, payload in incoming.items():
        normalized_payload = _normalize_table_payload(payload, fallback_set_at=now)
        row = session.execute(
            select(ResearcherOverride).where(
                ResearcherOverride.project_id == project.id,
                ResearcherOverride.field_name == str(field_name),
                ResearcherOverride.cleared_at.is_(None),
            )
        ).scalar_one_or_none()
        if row is None:
            session.add(
                ResearcherOverride(
                    project_id=project.id,
                    field_name=str(field_name),
                    value=normalized_payload.value,
                    set_by_user_id=set_by_user_id,
                    set_by_label=normalized_payload.set_by_label,
                    set_at=normalized_payload.set_at,
                    note=normalized_payload.note,
                    source_url=normalized_payload.source_url,
                    mode=normalized_payload.mode,
                    baseline=normalized_payload.baseline,
                )
            )
            continue

        row.value = normalized_payload.value
        if set_by_user_id is not None:
            row.set_by_user_id = set_by_user_id
            if normalized_payload.has_explicit_set_by_label:
                row.set_by_label = normalized_payload.set_by_label
        elif normalized_payload.has_explicit_set_by_label and row.set_by_user_id is None:
            row.set_by_label = normalized_payload.set_by_label
        if normalized_payload.has_explicit_set_at:
            row.set_at = normalized_payload.set_at
        row.reaffirmed_at = now
        row.note = normalized_payload.note
        row.source_url = normalized_payload.source_url
        row.mode = normalized_payload.mode
        row.baseline = normalized_payload.baseline


def clear_researcher_override_fields(
    session: Session,
    project: Project,
    field_names: set[str],
    *,
    cleared_by_user_id: Any | None = None,
    cleared_at: datetime | None = None,
) -> None:
    if not field_names:
        return

    project.researcher_override = _remove_legacy_researcher_overrides(
        project.researcher_override,
        field_names,
    )

    if not researcher_overrides_table_exists(session):
        return

    timestamp = cleared_at or datetime.now(UTC)
    rows = session.execute(
        select(ResearcherOverride).where(
            ResearcherOverride.project_id == project.id,
            ResearcherOverride.field_name.in_(field_names),
            ResearcherOverride.cleared_at.is_(None),
        )
    ).scalars().all()
    for row in rows:
        row.cleared_at = timestamp
        row.cleared_by_user_id = cleared_by_user_id


def normalize_legacy_researcher_overrides(raw_override: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw_override, dict):
        return {}

    normalized: dict[str, dict[str, Any]] = {}
    for field_name, payload in raw_override.items():
        if isinstance(payload, dict) and "value" in payload:
            normalized[str(field_name)] = {
                "value": payload.get("value"),
                "set_by": payload.get("set_by"),
                "set_at": payload.get("set_at"),
                "note": payload.get("note"),
                "mode": payload.get("mode"),
                "baseline": payload.get("baseline"),
                "source_url": payload.get("source_url"),
            }
            continue
        normalized[str(field_name)] = {
            "value": payload,
            "set_by": "legacy",
            "set_at": None,
            "note": None,
            "mode": "sticky",
            "baseline": None,
            "source_url": None,
        }
    return normalized


def researcher_overrides_table_exists(session: Session) -> bool:
    cached = session.info.get(_TABLE_EXISTS_CACHE_KEY)
    if isinstance(cached, bool):
        return cached
    bind = session.get_bind()
    exists = inspect(bind).has_table("researcher_overrides")
    session.info[_TABLE_EXISTS_CACHE_KEY] = exists
    return exists


def _merge_legacy_researcher_overrides(
    existing: Any,
    incoming: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    merged = dict(existing) if isinstance(existing, dict) else {}
    for field_name, payload in incoming.items():
        merged[str(field_name)] = serialize_json(dict(payload))
    return merged


def _remove_legacy_researcher_overrides(
    existing: Any,
    field_names: set[str],
) -> dict[str, Any] | None:
    if not isinstance(existing, dict):
        return existing

    remaining = {
        field_name: payload
        for field_name, payload in existing.items()
        if field_name not in field_names
    }
    return remaining or None


def _override_payload_from_row(row: ResearcherOverride) -> dict[str, Any]:
    return {
        "value": row.value,
        "set_by": row.set_by_label,
        "set_at": row.set_at.isoformat() if row.set_at is not None else None,
        "note": row.note,
        "mode": row.mode,
        "baseline": row.baseline,
        "source_url": row.source_url,
    }


def _normalize_table_payload(
    payload: Mapping[str, Any],
    *,
    fallback_set_at: datetime,
) -> _NormalizedTablePayload:
    explicit_set_at = _coerce_datetime(payload.get("set_at")) if "set_at" in payload else None
    baseline = payload.get("baseline")
    return _NormalizedTablePayload(
        value=serialize_json(payload.get("value")),
        set_by_label=_coerce_text(payload.get("set_by")),
        set_at=explicit_set_at or fallback_set_at,
        has_explicit_set_at=explicit_set_at is not None,
        has_explicit_set_by_label="set_by" in payload,
        note=_coerce_text(payload.get("note")),
        source_url=_coerce_text(payload.get("source_url")),
        mode=_coerce_text(payload.get("mode")),
        baseline=serialize_json(baseline) if isinstance(baseline, Mapping) else None,
    )


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

from __future__ import annotations

import enum
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tcg_pipeline.db.models import (
    Evidence,
    Project,
    ResolutionLog,
    StatusHistory,
)
from tcg_pipeline.resolution.confidence import compute_overall_confidence
from tcg_pipeline.resolution.fields import FieldResolution, normalize_comparable
from tcg_pipeline.resolution.fields.age_restriction import resolve_age_restriction
from tcg_pipeline.resolution.fields.delivery_year import resolve_delivery_year
from tcg_pipeline.resolution.fields.developer import resolve_developer
from tcg_pipeline.resolution.fields.product_type import resolve_product_type
from tcg_pipeline.resolution.fields.status import resolve_status
from tcg_pipeline.resolution.fields.units import resolve_unit_split, resolve_units
from tcg_pipeline.resolution.likelihood import compute_likelihood

LOGGED_FIELDS = {
    "pipeline_status",
    "total_units",
    "affordable_units",
    "market_rate_units",
    "product_type",
    "date_delivery",
    "delivery_year_provenance",
    "age_restriction",
    "developer",
    "confidence",
    "likelihood",
    "last_evidence_date",
}


@dataclass(slots=True)
class ProjectResolutionResult:
    project_id: Any
    applied: bool
    changed_fields: list[str] = dataclass_field(default_factory=list)
    log_entries_created: int = 0
    field_resolutions: dict[str, FieldResolution] = dataclass_field(default_factory=dict)
    resolved_values: dict[str, Any] = dataclass_field(default_factory=dict)


def resolve_project(
    project_id,
    session: Session,
    *,
    apply: bool = False,
    write_resolution_log: bool = True,
) -> ProjectResolutionResult:
    project = session.get(Project, project_id)
    if project is None:
        raise ValueError(f"Project {project_id} does not exist.")

    evidence_rows = _load_project_evidence(session, project_id)
    overrides = _normalize_researcher_overrides(project.researcher_override)

    status_resolution = resolve_status(evidence_rows, project, overrides=overrides)
    total_units_resolution = resolve_units(
        evidence_rows,
        project,
        "total_units",
        overrides=overrides,
    )
    affordable_units_resolution = resolve_unit_split(
        evidence_rows,
        project,
        "affordable_units",
        overrides=overrides,
    )
    market_rate_units_resolution = resolve_unit_split(
        evidence_rows,
        project,
        "market_rate_units",
        overrides=overrides,
    )
    product_type_resolution = resolve_product_type(
        evidence_rows,
        project,
        overrides=overrides,
    )
    delivery_resolution = resolve_delivery_year(
        evidence_rows,
        project,
        resolved_status=status_resolution.value,
        resolved_total_units=total_units_resolution.value,
        overrides=overrides,
    )
    age_restriction_resolution = resolve_age_restriction(
        evidence_rows,
        project,
        overrides=overrides,
    )
    developer_resolution = resolve_developer(
        evidence_rows,
        project,
        overrides=overrides,
    )

    field_resolutions: dict[str, FieldResolution] = {
        "pipeline_status": status_resolution,
        "total_units": total_units_resolution,
        "affordable_units": affordable_units_resolution,
        "market_rate_units": market_rate_units_resolution,
        "product_type": product_type_resolution,
        "date_delivery": delivery_resolution,
        "delivery_year_provenance": FieldResolution(
            field_name="delivery_year_provenance",
            value=delivery_resolution.metadata.get("provenance"),
            confidence=delivery_resolution.confidence,
            evidence_ids=list(delivery_resolution.evidence_ids),
            rule_applied=delivery_resolution.rule_applied,
            evidence_date=delivery_resolution.evidence_date,
        ),
        "age_restriction": age_restriction_resolution,
        "developer": developer_resolution,
    }

    resolved_scalars = {
        field_name: resolution.value for field_name, resolution in field_resolutions.items()
    }
    likelihood, likelihood_breakdown = compute_likelihood(resolved_scalars, evidence_rows, session)
    overall_confidence, confidence_reason = compute_overall_confidence(field_resolutions)
    last_evidence_date = max(
        (evidence.evidence_date or evidence.collected_at.date() for evidence in evidence_rows),
        default=None,
    )

    field_resolutions["likelihood"] = FieldResolution(
        field_name="likelihood",
        value=likelihood,
        confidence=overall_confidence,
        rule_applied="base_rate_plus_signals",
        metadata=likelihood_breakdown,
    )
    field_resolutions["likelihood_breakdown"] = FieldResolution(
        field_name="likelihood_breakdown",
        value=likelihood_breakdown,
        confidence=overall_confidence,
        rule_applied="base_rate_plus_signals",
    )
    field_resolutions["confidence"] = FieldResolution(
        field_name="confidence",
        value=overall_confidence,
        confidence=overall_confidence,
        rule_applied="project_confidence_rollup",
        metadata=confidence_reason,
    )
    field_resolutions["confidence_reason"] = FieldResolution(
        field_name="confidence_reason",
        value=confidence_reason,
        confidence=overall_confidence,
        rule_applied="project_confidence_rollup",
    )
    field_resolutions["status_confidence"] = FieldResolution(
        field_name="status_confidence",
        value=overall_confidence,
        confidence=overall_confidence,
        rule_applied="dual_write_confidence",
    )
    field_resolutions["last_evidence_date"] = FieldResolution(
        field_name="last_evidence_date",
        value=last_evidence_date,
        confidence=overall_confidence,
        rule_applied="latest_evidence_date",
    )

    changed_fields: list[str] = []
    log_entries_created = 0
    for field_name, resolution in field_resolutions.items():
        current_value = getattr(project, field_name)
        if normalize_comparable(current_value) == normalize_comparable(resolution.value):
            continue

        changed_fields.append(field_name)
        if write_resolution_log and field_name in LOGGED_FIELDS:
            session.add(
                ResolutionLog(
                    project_id=project.id,
                    field=field_name,
                    current_value=normalize_comparable(current_value),
                    resolved_value=normalize_comparable(resolution.value),
                    evidence_ids=resolution.evidence_ids or None,
                    rule_applied=resolution.rule_applied,
                    confidence=resolution.confidence,
                )
            )
            log_entries_created += 1

    if apply:
        previous_status = project.pipeline_status
        for field_name, resolution in field_resolutions.items():
            setattr(project, field_name, resolution.value)
        if previous_status != project.pipeline_status:
            session.add(
                StatusHistory(
                    project_id=project.id,
                    status=project.pipeline_status,
                    status_date=status_resolution.evidence_date or date.today(),
                    source="resolution_engine",
                    notes=(
                        "Resolved from evidence. "
                        f"Rule: {status_resolution.rule_applied}. "
                        f"Confidence: {status_resolution.confidence.value}."
                    ),
                )
            )

    return ProjectResolutionResult(
        project_id=project.id,
        applied=apply,
        changed_fields=changed_fields,
        log_entries_created=log_entries_created,
        field_resolutions=field_resolutions,
        resolved_values={
            field_name: resolution.value
            for field_name, resolution in field_resolutions.items()
        },
    )


def _load_project_evidence(session: Session, project_id) -> list[Evidence]:
    return (
        session.execute(
            select(Evidence)
            .where(Evidence.project_id == project_id)
            .order_by(
                Evidence.evidence_date.desc().nullslast(),
                Evidence.collected_at.desc(),
                Evidence.source_tier.asc(),
            )
        )
        .scalars()
        .all()
    )


def _normalize_researcher_overrides(raw_override: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw_override, dict):
        return {}

    normalized: dict[str, dict[str, Any]] = {}
    for field_name, payload in raw_override.items():
        if isinstance(payload, dict) and "value" in payload:
            normalized[field_name] = {
                "value": payload.get("value"),
                "set_by": payload.get("set_by"),
                "set_at": payload.get("set_at"),
                "note": payload.get("note"),
            }
            continue
        normalized[field_name] = {
            "value": payload,
            "set_by": "legacy",
            "set_at": None,
            "note": None,
        }
    return normalized


def normalize_value_for_project(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value

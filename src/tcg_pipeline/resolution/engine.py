from __future__ import annotations

import enum
import logging
import uuid
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tcg_pipeline.db.models import (
    Evidence,
    Priority,
    Project,
    ResolutionLog,
    StatusHistory,
)
from tcg_pipeline.db.researcher_overrides import (
    active_researcher_overrides_for_project,
    clear_researcher_override_fields,
)
from tcg_pipeline.matching.differ import ReviewFlag
from tcg_pipeline.resolution.confidence import compute_overall_confidence
from tcg_pipeline.resolution.fields import (
    FieldResolution,
    evidence_effective_date,
    normalize_comparable,
)
from tcg_pipeline.resolution.fields.age_restriction import resolve_age_restriction
from tcg_pipeline.resolution.fields.delivery_year import resolve_delivery_year
from tcg_pipeline.resolution.fields.developer import resolve_developer
from tcg_pipeline.resolution.fields.product_type import resolve_product_type
from tcg_pipeline.resolution.fields.status import resolve_status
from tcg_pipeline.resolution.fields.stories import resolve_stories
from tcg_pipeline.resolution.fields.units import resolve_unit_split, resolve_units
from tcg_pipeline.resolution.likelihood import compute_likelihood
from tcg_pipeline.review.contradictions import detect_project_contradictions

logger = logging.getLogger(__name__)

LOGGED_FIELDS = {
    "pipeline_status",
    "total_units",
    "affordable_units",
    "market_rate_units",
    "workforce_units",
    "stories",
    "product_type",
    "date_delivery",
    "delivery_year_provenance",
    "age_restriction",
    "developer",
    "confidence",
    "confidence_reason",
    "likelihood",
    "likelihood_breakdown",
    "last_evidence_date",
    # status_confidence is intentionally excluded because it mirrors confidence during
    # the dual-write transition and would only duplicate the confidence log row.
}


@dataclass(slots=True)
class ProjectResolutionResult:
    project_id: Any
    applied: bool
    changed_fields: list[str] = dataclass_field(default_factory=list)
    log_entries_created: int = 0
    field_resolutions: dict[str, FieldResolution] = dataclass_field(default_factory=dict)
    review_flags: list[ReviewFlag] = dataclass_field(default_factory=list)
    resolved_values: dict[str, Any] = dataclass_field(default_factory=dict)


def resolve_project(
    project_id,
    session: Session,
    *,
    apply: bool = False,
    write_resolution_log: bool = True,
    skip_contradiction_review_item_ids: set[Any] | None = None,
) -> ProjectResolutionResult:
    project = session.get(Project, project_id)
    if project is None:
        raise ValueError(f"Project {project_id} does not exist.")

    evidence_rows = _load_project_evidence(session, project_id)
    overrides = active_researcher_overrides_for_project(session, project)

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
    workforce_units_resolution = resolve_unit_split(
        evidence_rows,
        project,
        "workforce_units",
        overrides=overrides,
    )
    stories_resolution = resolve_stories(
        evidence_rows,
        project,
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
        session=session,
        persist_registry=False,
        overrides=overrides,
    )

    field_resolutions: dict[str, FieldResolution] = {
        "pipeline_status": status_resolution,
        "total_units": total_units_resolution,
        "affordable_units": affordable_units_resolution,
        "market_rate_units": market_rate_units_resolution,
        "workforce_units": workforce_units_resolution,
        "stories": stories_resolution,
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
        (evidence_effective_date(evidence) for evidence in evidence_rows),
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
    review_flags = _build_review_flags(
        project,
        field_resolutions=field_resolutions,
        status_resolution=status_resolution,
        total_units_resolution=total_units_resolution,
        affordable_units_resolution=affordable_units_resolution,
        market_rate_units_resolution=market_rate_units_resolution,
        workforce_units_resolution=workforce_units_resolution,
        developer_resolution=developer_resolution,
    )

    changed_fields: list[str] = []
    log_entries_created = 0
    # Gate alert side effects on the same toggle that gates resolution_log writes:
    # both are persistence side effects, and shadow-mode / dry-run callers
    # intentionally avoid both. If we ever need an alert-but-don't-log mode,
    # split the gate.
    if write_resolution_log:
        _drain_pending_system_alerts(session, field_resolutions)
    for field_name, resolution in field_resolutions.items():
        current_value = getattr(project, field_name)
        if normalize_comparable(current_value) == normalize_comparable(resolution.value):
            if write_resolution_log and field_name == "pipeline_status":
                logged_status_regression = _log_preserved_status_regression(
                    session,
                    project=project,
                    resolution=resolution,
                    current_value=current_value,
                )
                if not logged_status_regression:
                    logged_status_regression = _log_suppressed_status_regression(
                        session,
                        project=project,
                        resolution=resolution,
                        current_value=current_value,
                    )
                if logged_status_regression:
                    log_entries_created += 1
            continue

        changed_fields.append(field_name)
        if write_resolution_log and field_name in LOGGED_FIELDS:
            log_kwargs = {
                "project_id": project.id,
                "field": field_name,
                "current_value": normalize_comparable(current_value),
                "resolved_value": normalize_comparable(resolution.value),
                "evidence_ids": resolution.evidence_ids or None,
                "rule_applied": resolution.rule_applied,
                "confidence": resolution.confidence,
            }
            metadata_json = _regression_log_metadata(resolution)
            if metadata_json is not None:
                log_kwargs["metadata_json"] = metadata_json
            session.add(
                ResolutionLog(
                    **log_kwargs,
                )
            )
            log_entries_created += 1

    if apply:
        _clear_superseded_system_overrides(session, project, field_resolutions)
        previous_status = project.pipeline_status
        for field_name, resolution in field_resolutions.items():
            setattr(project, field_name, resolution.value)
        detect_project_contradictions(
            session,
            project=project,
            field_resolutions=field_resolutions,
            skip_review_item_ids=skip_contradiction_review_item_ids,
        )
        if previous_status != project.pipeline_status:
            status_source = status_resolution.metadata.get("source_type") or "resolution_engine"
            evidence_type = status_resolution.metadata.get("evidence_type")
            session.add(
                StatusHistory(
                    project_id=project.id,
                    status=project.pipeline_status,
                    status_date=status_resolution.evidence_date or date.today(),
                    source=str(status_source),
                    notes=(
                        "Resolved from evidence. "
                        f"Upstream source: {status_source}. "
                        f"Evidence type: {evidence_type or 'n/a'}. "
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
        review_flags=review_flags,
        resolved_values={
            field_name: resolution.value for field_name, resolution in field_resolutions.items()
        },
    )


def _load_project_evidence(session: Session, project_id) -> list[Evidence]:
    return (
        session.execute(
            select(Evidence)
            .where(
                Evidence.project_id == project_id,
                Evidence.superseded_at.is_(None),
            )
            .order_by(
                Evidence.evidence_date.desc().nullslast(),
                Evidence.collected_at.desc(),
                Evidence.source_tier.asc(),
            )
        )
        .scalars()
        .all()
    )


def normalize_value_for_project(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _log_preserved_status_regression(
    session: Session,
    *,
    project: Project,
    resolution: FieldResolution,
    current_value: Any,
) -> bool:
    candidates = resolution.metadata.get("regression_candidates")
    if not isinstance(candidates, list) or not candidates:
        return False
    session.add(
        ResolutionLog(
            project_id=project.id,
            field="pipeline_status",
            current_value=normalize_comparable(current_value),
            resolved_value=normalize_comparable(resolution.value),
            evidence_ids=_regression_candidate_evidence_ids(candidates) or None,
            rule_applied=str(
                resolution.metadata.get("regression_audit_rule_applied")
                or resolution.rule_applied
            ),
            confidence=resolution.confidence,
            metadata_json=_json_safe_metadata(resolution.metadata),
        )
    )
    return True


def _log_suppressed_status_regression(
    session: Session,
    *,
    project: Project,
    resolution: FieldResolution,
    current_value: Any,
) -> bool:
    suppressed = resolution.metadata.get("suppressed_regression_candidates")
    if not isinstance(suppressed, list) or not suppressed:
        return False
    session.add(
        ResolutionLog(
            project_id=project.id,
            field="pipeline_status",
            current_value=normalize_comparable(current_value),
            resolved_value=normalize_comparable(resolution.value),
            evidence_ids=_regression_candidate_evidence_ids(suppressed) or None,
            rule_applied="regression_candidate_suppressed",
            confidence=resolution.confidence,
            metadata_json=_json_safe_metadata(resolution.metadata),
        )
    )
    return True


def _regression_log_metadata(resolution: FieldResolution) -> dict[str, Any] | None:
    candidates = resolution.metadata.get("regression_candidates")
    if not isinstance(candidates, list) or not candidates:
        return None
    return _json_safe_metadata(
        {
            "regression_candidates": candidates,
            "regression_candidate_count": resolution.metadata.get(
                "regression_candidate_count"
            ),
            "regression_audit_rule_applied": resolution.metadata.get(
                "regression_audit_rule_applied"
            ),
        }
    )


def _regression_candidate_evidence_ids(candidates: list[Any]) -> list[uuid.UUID]:
    evidence_ids: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        values = candidate.get("evidence_ids")
        if not isinstance(values, list):
            continue
        for value in values:
            try:
                evidence_id = uuid.UUID(str(value))
            except (TypeError, ValueError):
                continue
            if evidence_id in seen:
                continue
            evidence_ids.append(evidence_id)
            seen.add(evidence_id)
    return evidence_ids


def _drain_pending_system_alerts(
    session: Session,
    field_resolutions: dict[str, FieldResolution],
) -> None:
    # Local import keeps resolver/engine import paths free of worker
    # dependencies unless a side-effecting write is actually needed.
    from tcg_pipeline.workers.news_jobs import raise_system_alert

    for resolution in field_resolutions.values():
        pending_alerts = resolution.metadata.pop("pending_system_alerts", None)
        if not isinstance(pending_alerts, list):
            continue
        for pending_alert in pending_alerts:
            if not isinstance(pending_alert, dict):
                continue
            alert_key = pending_alert.get("alert_key")
            severity = pending_alert.get("severity")
            message = pending_alert.get("message")
            if not (
                isinstance(alert_key, str)
                and isinstance(severity, str)
                and isinstance(message, str)
            ):
                logger.warning("Skipping malformed pending system alert: %r", pending_alert)
                continue
            scope = pending_alert.get("scope")
            detail = pending_alert.get("detail")
            try:
                raise_system_alert(
                    session,
                    alert_key=alert_key,
                    severity=severity,
                    message=message,
                    scope=scope if isinstance(scope, dict) else None,
                    detail=detail if isinstance(detail, dict) else None,
                )
            except Exception:  # noqa: BLE001 - alert failure must not block resolve
                logger.exception("Failed to raise pending system alert %s", alert_key)


def _clear_superseded_system_overrides(
    session: Session,
    project: Project,
    field_resolutions: dict[str, FieldResolution],
) -> None:
    fields = {
        field_name
        for field_name, resolution in field_resolutions.items()
        if resolution.metadata.get("system_override_superseded")
    }
    if not fields:
        return
    clear_researcher_override_fields(
        session,
        project,
        fields,
        cleared_at=datetime.now(UTC),
    )


def _json_safe_metadata(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe_metadata(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe_metadata(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe_metadata(item) for item in value]
    return value


def _build_review_flags(
    project: Project,
    *,
    field_resolutions: dict[str, FieldResolution],
    status_resolution: FieldResolution,
    total_units_resolution: FieldResolution,
    affordable_units_resolution: FieldResolution,
    market_rate_units_resolution: FieldResolution,
    workforce_units_resolution: FieldResolution,
    developer_resolution: FieldResolution,
) -> list[ReviewFlag]:
    review_flags: list[ReviewFlag] = []
    if status_resolution.metadata.get("requires_review") and (
        project.pipeline_status != status_resolution.value
        or status_resolution.metadata.get("candidate_status") is not None
    ):
        review_flags.append(
            ReviewFlag(
                code=(
                    "status_transition_requires_review"
                    if status_resolution.metadata.get("candidate_status") is not None
                    else "permit_issued_requires_review"
                ),
                message=str(
                    status_resolution.metadata.get("review_reason")
                    or "Status change requires researcher review."
                ),
                priority=Priority.HIGH,
            )
        )

    resolved_total = total_units_resolution.value
    resolved_affordable = affordable_units_resolution.value
    resolved_market_rate = market_rate_units_resolution.value
    resolved_workforce = workforce_units_resolution.value
    total_changed = project.total_units != resolved_total
    split_unchanged = (
        project.affordable_units == resolved_affordable
        and project.market_rate_units == resolved_market_rate
        and project.workforce_units == resolved_workforce
    )
    bucket_sum, bucket_sum_is_complete = _unit_bucket_sum(
        affordable_units=resolved_affordable,
        market_rate_units=resolved_market_rate,
        workforce_units=resolved_workforce,
    )
    if (
        total_changed
        and split_unchanged
        and resolved_total is not None
        and bucket_sum is not None
        and (
            (bucket_sum_is_complete and abs(bucket_sum - resolved_total) > 2)
            or bucket_sum - resolved_total > 2
        )
    ):
        review_flags.append(
            ReviewFlag(
                code="unit_split_mismatch",
                message=(
                    f"Total units updated to {resolved_total}. Affordable/market-rate/workforce "
                    f"split ({_display_unit_bucket(resolved_affordable)}/"
                    f"{_display_unit_bucket(resolved_market_rate)}/"
                    f"{_display_unit_bucket(resolved_workforce)}) may need revision "
                    "because the split no longer sums to total."
                ),
                priority=Priority.MEDIUM,
            )
        )

    if developer_resolution.metadata.get("requires_review"):
        match_type = str(developer_resolution.metadata.get("match_type") or "")
        raw_value = developer_resolution.metadata.get("raw_value") or developer_resolution.value
        canonical_name = (
            developer_resolution.metadata.get("canonical_name") or developer_resolution.value
        )
        score = developer_resolution.metadata.get("score")
        if match_type == "fuzzy_review":
            review_flags.append(
                ReviewFlag(
                    code="developer_canonicalization_review",
                    message=(
                        f"Developer '{raw_value}' was auto-canonicalized to "
                        f"'{canonical_name}' with fuzzy score {score:.1f}. Review the alias."
                    ),
                    priority=Priority.MEDIUM,
                )
            )
        elif match_type == "new_registry_entry":
            review_flags.append(
                ReviewFlag(
                    code="developer_registry_new_name",
                    message=(
                        f"Developer '{canonical_name}' did not match the existing registry "
                        "and was treated as a new canonical developer."
                    ),
                    priority=Priority.MEDIUM,
                )
            )

    return review_flags


def _unit_bucket_sum(
    *,
    affordable_units: int | None,
    market_rate_units: int | None,
    workforce_units: int | None,
) -> tuple[int | None, bool]:
    buckets = (affordable_units, market_rate_units, workforce_units)
    known_values = [value for value in buckets if value is not None]
    if not known_values:
        return None, False
    return sum(known_values), len(known_values) == len(buckets)


def _display_unit_bucket(value: int | None) -> str:
    return "NULL" if value is None else str(value)

from __future__ import annotations

from collections import Counter
from typing import Any

from tcg_pipeline.db.models import Evidence, PipelineStatus, Project, StatusConfidence
from tcg_pipeline.resolution.fields import (
    FieldObservation,
    FieldResolution,
    build_resolution,
    infer_confidence,
    iter_field_observations,
    resolve_override,
)

STATUS_PROGRESS_ORDER = {
    PipelineStatus.CONCEPTUAL: 0,
    PipelineStatus.PROPOSED: 1,
    PipelineStatus.PENDING: 2,
    PipelineStatus.APPROVED: 3,
    PipelineStatus.UNDER_CONSTRUCTION: 4,
    PipelineStatus.PRE_LEASING_PRE_SELLING: 5,
    PipelineStatus.COMPLETE: 6,
}
STATUS_FROM_EVIDENCE_TYPE = {
    "building_permit_issued": PipelineStatus.APPROVED,
    "building_inspection_recorded": PipelineStatus.UNDER_CONSTRUCTION,
    "certificate_of_occupancy_issued": PipelineStatus.COMPLETE,
}


def resolve_status(
    evidence_rows: list[Evidence],
    project: Project,
    *,
    overrides: dict[str, Any] | None = None,
) -> FieldResolution:
    override = resolve_override("pipeline_status", overrides)
    if override is not None:
        override.value = _coerce_pipeline_status(override.value) or project.pipeline_status
        return override

    explicit_observations = iter_field_observations(evidence_rows, "pipeline_status")
    tier1_explicit = [
        observation
        for observation in explicit_observations
        if observation.evidence.source_tier == 1
        and _coerce_pipeline_status(observation.value)
        in {PipelineStatus.STALLED, PipelineStatus.INACTIVE}
    ]
    if tier1_explicit:
        stalled_observation = tier1_explicit[0]
        status = _coerce_pipeline_status(stalled_observation.value) or project.pipeline_status
        return build_resolution(
            "pipeline_status",
            status,
            confidence=StatusConfidence.HIGH,
            observations=[stalled_observation],
            rule_applied="tier1_explicit_status",
        )

    direct_signal_observations = _status_signal_observations(evidence_rows)
    permit_observations = direct_signal_observations.get("building_permit_issued", [])
    inspection_observations = direct_signal_observations.get("building_inspection_recorded", [])
    cofo_observations = direct_signal_observations.get("certificate_of_occupancy_issued", [])
    if cofo_observations:
        return build_resolution(
            "pipeline_status",
            PipelineStatus.COMPLETE,
            confidence=StatusConfidence.HIGH,
            observations=cofo_observations[:1],
            rule_applied="direct_cofo_evidence",
            metadata={"evidence_type": "certificate_of_occupancy_issued"},
        )

    candidate_observations: dict[PipelineStatus, list[FieldObservation]] = {}
    for observation in explicit_observations:
        status = _coerce_pipeline_status(observation.value)
        if status is None:
            continue
        if status == PipelineStatus.UNDER_CONSTRUCTION and not _can_promote_to_under_construction(
            observation,
            permit_observations=permit_observations,
            explicit_observations=explicit_observations,
        ):
            continue
        candidate_observations.setdefault(status, []).append(observation)

    if inspection_observations:
        candidate_observations.setdefault(PipelineStatus.UNDER_CONSTRUCTION, []).extend(
            inspection_observations[:1]
        )
    if permit_observations:
        candidate_observations.setdefault(PipelineStatus.APPROVED, []).extend(
            permit_observations[:1]
        )

    if not candidate_observations:
        return build_resolution(
            "pipeline_status",
            project.pipeline_status,
            confidence=StatusConfidence.LOW,
            rule_applied="no_status_evidence",
        )

    chosen_status, chosen_observations = max(
        candidate_observations.items(),
        key=lambda item: STATUS_PROGRESS_ORDER.get(item[0], -1),
    )
    confidence = _status_confidence(
        chosen_status,
        chosen_observations,
        explicit_observations=explicit_observations,
        permit_observations=permit_observations,
    )
    return build_resolution(
        "pipeline_status",
        chosen_status,
        confidence=confidence,
        observations=chosen_observations[:1],
        rule_applied="highest_status_wins",
        metadata={
            "candidate_count": len(candidate_observations),
            "evidence_type": _extract_status_evidence_type(chosen_observations),
        },
    )


def _status_signal_observations(
    evidence_rows: list[Evidence],
) -> dict[str, list[FieldObservation]]:
    observations_by_signal: dict[str, list[FieldObservation]] = {}
    for evidence in evidence_rows:
        extracted = evidence.extracted_fields or {}
        payload = extracted.get("status_evidence_type")
        if not isinstance(payload, dict):
            continue
        evidence_type = str(payload.get("value") or "").strip()
        if not evidence_type or evidence_type not in STATUS_FROM_EVIDENCE_TYPE:
            continue
        observations_by_signal.setdefault(evidence_type, []).append(
            FieldObservation(
                field_name="status_evidence_type",
                value=evidence_type,
                evidence=evidence,
                extracted_confidence=None,
            )
        )
    return observations_by_signal


def _can_promote_to_under_construction(
    observation: FieldObservation,
    *,
    permit_observations: list[FieldObservation],
    explicit_observations: list[FieldObservation],
) -> bool:
    if observation.evidence.source_tier == 1:
        return True
    if permit_observations:
        return True

    matching_non_gov_sources = {
        candidate.evidence.source_type
        for candidate in explicit_observations
        if _coerce_pipeline_status(candidate.value) == PipelineStatus.UNDER_CONSTRUCTION
        and candidate.evidence.source_tier > 1
    }
    return len(matching_non_gov_sources) >= 2


def _status_confidence(
    chosen_status: PipelineStatus,
    chosen_observations: list[FieldObservation],
    *,
    explicit_observations: list[FieldObservation],
    permit_observations: list[FieldObservation],
) -> StatusConfidence:
    if chosen_status == PipelineStatus.UNDER_CONSTRUCTION:
        if any(
            observation.evidence.source_tier == 1 for observation in chosen_observations
        ):
            return StatusConfidence.HIGH
        if permit_observations:
            return StatusConfidence.HIGH
        matching_sources = Counter(
            observation.evidence.source_type
            for observation in explicit_observations
            if _coerce_pipeline_status(observation.value) == PipelineStatus.UNDER_CONSTRUCTION
            and observation.evidence.source_tier > 1
        )
        if len(matching_sources) >= 2:
            return StatusConfidence.MEDIUM
        return StatusConfidence.LOW

    return infer_confidence(chosen_observations)


def _coerce_pipeline_status(value: Any) -> PipelineStatus | None:
    if isinstance(value, PipelineStatus):
        return value
    if value is None:
        return None
    try:
        return PipelineStatus(str(value))
    except ValueError:
        return None


def _extract_status_evidence_type(
    observations: list[FieldObservation],
) -> str | None:
    for observation in observations:
        if observation.field_name != "status_evidence_type":
            continue
        value = str(observation.value).strip()
        if value:
            return value
    return None

from __future__ import annotations

from collections import Counter
from typing import Any

from tcg_pipeline.db.models import Evidence, PipelineStatus, Project, StatusConfidence
from tcg_pipeline.resolution.fields import (
    FieldObservation,
    FieldResolution,
    apply_override,
    build_resolution,
    infer_confidence,
    iter_field_observations,
)
from tcg_pipeline.resolution.regression_filters import (
    is_benign_ladbs_additive_paperwork,
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
MANUAL_REVIEW_STATUSES = {PipelineStatus.STALLED, PipelineStatus.INACTIVE}


def resolve_status(
    evidence_rows: list[Evidence],
    project: Project,
    *,
    overrides: dict[str, Any] | None = None,
) -> FieldResolution:
    explicit_observations = iter_field_observations(evidence_rows, "pipeline_status")
    direct_signal_observations = _status_signal_observations(evidence_rows)
    permit_observations = direct_signal_observations.get("building_permit_issued", [])
    inspection_observations = direct_signal_observations.get("building_inspection_recorded", [])
    cofo_observations = direct_signal_observations.get("certificate_of_occupancy_issued", [])

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
    # CoFO is included for regression enumeration; this early-return path still
    # owns the actual Complete resolution decision.
    if cofo_observations:
        candidate_observations.setdefault(PipelineStatus.COMPLETE, []).extend(
            cofo_observations[:1]
        )

    if cofo_observations:
        regression_metadata = _status_regression_metadata(
            project.pipeline_status,
            candidate_observations,
        )
        candidate = build_resolution(
            "pipeline_status",
            PipelineStatus.COMPLETE,
            confidence=StatusConfidence.HIGH,
            observations=cofo_observations[:1],
            rule_applied="direct_cofo_evidence",
            metadata={
                "evidence_type": "certificate_of_occupancy_issued",
                "source_type": cofo_observations[0].evidence.source_type,
                **regression_metadata,
            },
        )
        return _apply_status_override(candidate, project=project, overrides=overrides)

    if not candidate_observations:
        candidate = build_resolution(
            "pipeline_status",
            project.pipeline_status,
            confidence=StatusConfidence.LOW,
            rule_applied="no_status_evidence",
        )
        return _apply_status_override(candidate, project=project, overrides=overrides)

    regression_metadata = _status_regression_metadata(
        project.pipeline_status,
        candidate_observations,
    )

    chosen_status, chosen_observations = max(
        candidate_observations.items(),
        key=lambda item: STATUS_PROGRESS_ORDER.get(item[0], -1),
    )
    if _requires_manual_status_review(project.pipeline_status, chosen_status):
        candidate = build_resolution(
            "pipeline_status",
            project.pipeline_status,
            confidence=StatusConfidence.LOW,
            observations=chosen_observations[:1],
            rule_applied="manual_status_review_preserve_current",
            metadata={
                "candidate_status": chosen_status.value,
                "evidence_type": _extract_status_evidence_type(chosen_observations),
                "source_type": _extract_source_type(chosen_observations),
                "requires_review": True,
                "review_reason": (
                    f"Evidence suggests {chosen_status.value}, but Stalled/Inactive "
                    "transitions are manual-review only."
                ),
                **regression_metadata,
            },
        )
        return _apply_status_override(candidate, project=project, overrides=overrides)

    current_rank = STATUS_PROGRESS_ORDER.get(project.pipeline_status)
    chosen_rank = STATUS_PROGRESS_ORDER.get(chosen_status)
    if (
        current_rank is not None
        and chosen_rank is not None
        and chosen_rank < current_rank
    ):
        candidate = build_resolution(
            "pipeline_status",
            project.pipeline_status,
            confidence=StatusConfidence.LOW,
            observations=chosen_observations[:1],
            rule_applied=(
                "terminal_regression_dropped"
                if _has_terminal_regression_drop(regression_metadata)
                else "forward_only_preserve_current"
            ),
            metadata={
                "candidate_status": chosen_status.value,
                "evidence_type": _extract_status_evidence_type(chosen_observations),
                "source_type": _extract_source_type(chosen_observations),
                "requires_review": False,
                **regression_metadata,
            },
        )
        return _apply_status_override(candidate, project=project, overrides=overrides)
    confidence = _status_confidence(
        chosen_status,
        chosen_observations,
        explicit_observations=explicit_observations,
        permit_observations=permit_observations,
    )
    review_required = _requires_review(
        chosen_status,
        chosen_observations,
        inspection_observations=inspection_observations,
    )
    candidate = build_resolution(
        "pipeline_status",
        chosen_status,
        confidence=confidence,
        observations=chosen_observations[:1],
        rule_applied="highest_status_wins",
        metadata={
            "candidate_count": len(candidate_observations),
            "evidence_type": _extract_status_evidence_type(chosen_observations),
            "source_type": _extract_source_type(chosen_observations),
            "requires_review": review_required,
            "review_reason": (
                "Permit issued alone supports Approved, but requires researcher review "
                "until corroborating construction evidence arrives."
                if review_required
                else None
            ),
            **regression_metadata,
        },
    )
    return _apply_status_override(candidate, project=project, overrides=overrides)


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


def _apply_status_override(
    candidate: FieldResolution,
    *,
    project: Project,
    overrides: dict[str, Any] | None,
) -> FieldResolution:
    resolution = apply_override(
        "pipeline_status",
        candidate,
        overrides,
        transform_value=lambda value: _coerce_pipeline_status(value) or project.pipeline_status,
    )
    if resolution is candidate:
        return resolution
    for key in (
        "regression_candidates",
        "regression_candidate_count",
        "regression_audit_rule_applied",
    ):
        if key in candidate.metadata:
            resolution.metadata[key] = candidate.metadata[key]
    return resolution


def _status_regression_metadata(
    current_status: PipelineStatus,
    candidate_observations: dict[PipelineStatus, list[FieldObservation]],
) -> dict[str, Any]:
    current_rank = STATUS_PROGRESS_ORDER.get(current_status)
    if current_rank is None:
        return {}

    candidates: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    for proposed_status, observations in candidate_observations.items():
        proposed_rank = STATUS_PROGRESS_ORDER.get(proposed_status)
        if proposed_rank is None or proposed_rank >= current_rank:
            continue
        for observation in observations:
            if is_benign_ladbs_additive_paperwork(observation.evidence):
                # LADBS additive paperwork (issued permit, plan-check progress,
                # CofO issued/pending) on an already-higher-status project is
                # noise, not regression. Record in audit metadata so the
                # suppression is visible without polluting the review queue.
                suppressed.append(
                    _status_regression_candidate_payload(
                        current_status=current_status,
                        current_rank=current_rank,
                        proposed_status=proposed_status,
                        proposed_rank=proposed_rank,
                        observation=observation,
                    )
                    | {"suppression_reason": "ladbs_additive_paperwork"}
                )
                continue
            candidates.append(
                _status_regression_candidate_payload(
                    current_status=current_status,
                    current_rank=current_rank,
                    proposed_status=proposed_status,
                    proposed_rank=proposed_rank,
                    observation=observation,
                )
            )
    if not candidates and not suppressed:
        return {}

    terminal_drop = current_status == PipelineStatus.COMPLETE
    metadata: dict[str, Any] = {
        "regression_candidates": candidates,
        "regression_candidate_count": len(candidates),
        "regression_audit_rule_applied": (
            "terminal_regression_dropped"
            if terminal_drop
            else "status_regression_candidate_preserve_current"
        ),
    }
    if suppressed:
        metadata["suppressed_regression_candidates"] = suppressed
        metadata["suppressed_regression_candidate_count"] = len(suppressed)
    return metadata


def _status_regression_candidate_payload(
    *,
    current_status: PipelineStatus,
    current_rank: int,
    proposed_status: PipelineStatus,
    proposed_rank: int,
    observation: FieldObservation,
) -> dict[str, Any]:
    payload = {
        "current_status": current_status.value,
        "proposed_status": proposed_status.value,
        "current_rank": current_rank,
        "proposed_rank": proposed_rank,
        "rank_delta": current_rank - proposed_rank,
        "evidence_ids": [str(observation.evidence.id)],
        "source_type": observation.evidence.source_type,
        "source_tier": observation.evidence.source_tier,
        "evidence_type": _status_evidence_type_for_observation(observation),
        "evidence_date": observation.effective_date.isoformat(),
        "collected_at": observation.evidence.collected_at.isoformat(),
        "semantic_reason_code": _semantic_reason_code_for_observation(observation),
        "terminal_state_dropped": current_status == PipelineStatus.COMPLETE,
    }
    payload.update(_source_descriptor_fields(observation.evidence))
    return payload


def _source_descriptor_fields(evidence: Evidence) -> dict[str, Any]:
    """Extract source-specific descriptor fields from an Evidence row for use
    in regression-candidate payloads and downstream narrative generation. The
    narrative templates name specific permit types / numbers / source slugs
    instead of generic phrases like "LADBS signal" — this helper pulls the
    raw fields out of the evidence's raw_data so the templates don't need DB
    access at narrative-generation time."""
    raw = evidence.raw_data if isinstance(evidence.raw_data, dict) else {}
    descriptor: dict[str, Any] = {}
    if evidence.source_type in {"ladbs_permit", "ladbs_permit_activity"}:
        permit_type = raw.get("permit_type")
        permit_number = raw.get("permit_nbr") or raw.get("permit") or raw.get("permit_number")
        permit_subtype = raw.get("permit_sub_type")
        status_desc = raw.get("status_desc")
        if isinstance(permit_type, str):
            descriptor["permit_type"] = permit_type
        if isinstance(permit_subtype, str):
            descriptor["permit_sub_type"] = permit_subtype
        if permit_number is not None:
            descriptor["permit_number"] = str(permit_number)
        if isinstance(status_desc, str):
            descriptor["status_desc"] = status_desc
    return descriptor


def _has_terminal_regression_drop(metadata: dict[str, Any]) -> bool:
    candidates = metadata.get("regression_candidates")
    return (
        isinstance(candidates, list)
        and bool(candidates)
        and all(candidate.get("terminal_state_dropped") is True for candidate in candidates)
    )


def _can_promote_to_under_construction(
    observation: FieldObservation,
    *,
    permit_observations: list[FieldObservation],
    explicit_observations: list[FieldObservation],
) -> bool:
    if observation.evidence.source_tier == 1:
        return True
    if _semantic_promotes_status_alone(observation):
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


def _status_evidence_type_for_observation(observation: FieldObservation) -> str | None:
    if observation.field_name == "status_evidence_type":
        value = str(observation.value).strip()
        return value or None
    extracted = observation.evidence.extracted_fields or {}
    payload = extracted.get("status_evidence_type")
    if not isinstance(payload, dict):
        return None
    value = str(payload.get("value") or "").strip()
    return value or None


def _semantic_reason_code_for_observation(observation: FieldObservation) -> str | None:
    extracted = observation.evidence.extracted_fields or {}
    field_payload = extracted.get(observation.field_name)
    if not isinstance(field_payload, dict):
        return None
    semantic_payload = field_payload.get("semantic")
    if not isinstance(semantic_payload, dict):
        return None
    value = str(semantic_payload.get("reason_code") or "").strip()
    return value or None


def _semantic_promotes_status_alone(observation: FieldObservation) -> bool:
    extracted = observation.evidence.extracted_fields or {}
    field_payload = extracted.get(observation.field_name)
    if not isinstance(field_payload, dict):
        return False
    semantic_payload = field_payload.get("semantic")
    if not isinstance(semantic_payload, dict):
        return False
    return semantic_payload.get("promotes_status_alone") is True


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
    text = str(value).strip()
    if not text:
        return None
    try:
        return PipelineStatus(text)
    except ValueError:
        pass
    normalized = _pipeline_status_key(text)
    for status in PipelineStatus:
        if normalized in {
            _pipeline_status_key(status.value),
            _pipeline_status_key(status.name),
        }:
            return status
    return None


def _pipeline_status_key(value: str) -> str:
    return "_".join(
        "".join(character.lower() if character.isalnum() else " " for character in value).split()
    )


def _requires_review(
    chosen_status: PipelineStatus,
    chosen_observations: list[FieldObservation],
    *,
    inspection_observations: list[FieldObservation],
) -> bool:
    if chosen_status != PipelineStatus.APPROVED:
        return False
    if inspection_observations:
        return False
    return _extract_status_evidence_type(chosen_observations) == "building_permit_issued"


def _requires_manual_status_review(
    current_status: PipelineStatus,
    chosen_status: PipelineStatus,
) -> bool:
    if current_status == chosen_status:
        return False
    return (
        current_status in MANUAL_REVIEW_STATUSES
        or chosen_status in MANUAL_REVIEW_STATUSES
    )


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


def _extract_source_type(
    observations: list[FieldObservation],
) -> str | None:
    for observation in observations:
        source_type = observation.evidence.source_type
        if source_type:
            return source_type
    return None

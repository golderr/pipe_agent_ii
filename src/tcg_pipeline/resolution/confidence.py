from __future__ import annotations

from collections import Counter

from tcg_pipeline.db.models import StatusConfidence
from tcg_pipeline.resolution.fields import FieldResolution


def compute_overall_confidence(
    field_resolutions: dict[str, FieldResolution],
) -> tuple[StatusConfidence, dict[str, object]]:
    confidence_by_field = {
        field_name: resolution.confidence.value
        for field_name, resolution in field_resolutions.items()
        if field_name
    }
    counts = Counter(resolution.confidence for resolution in field_resolutions.values())
    status_confidence = field_resolutions["pipeline_status"].confidence

    if status_confidence == StatusConfidence.HIGH and counts[StatusConfidence.LOW] == 0:
        overall = StatusConfidence.HIGH
    elif counts[StatusConfidence.LOW] >= 2 or status_confidence == StatusConfidence.LOW:
        overall = StatusConfidence.LOW
    else:
        overall = StatusConfidence.MEDIUM

    reason = {
        "status_confidence": status_confidence.value,
        "field_confidences": confidence_by_field,
        "counts": {confidence.value: counts[confidence] for confidence in StatusConfidence},
    }
    return overall, reason

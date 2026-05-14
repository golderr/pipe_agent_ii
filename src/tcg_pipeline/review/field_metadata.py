from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tcg_pipeline.db.models import AgeRestriction, PipelineStatus, ProductType

DESTRUCTIVE_PIPELINE_STATUSES: frozenset[PipelineStatus] = frozenset(
    {
        PipelineStatus.DELETE_DUPLICATE,
        PipelineStatus.DELETE_OUTSIDE_MARKET_AREA,
        PipelineStatus.DELETE_NOT_RESIDENTIAL,
    }
)
REVIEW_PIPELINE_STATUS_VALUES: tuple[str, ...] = tuple(
    member.value
    for member in PipelineStatus
    if member not in DESTRUCTIVE_PIPELINE_STATUSES
)


@dataclass(frozen=True, slots=True)
class ReviewFieldMetadata:
    field_name: str
    label: str
    field_type: str
    constraints: dict[str, Any]


REVIEW_FIELD_METADATA: dict[str, ReviewFieldMetadata] = {
    "pipeline_status": ReviewFieldMetadata(
        field_name="pipeline_status",
        label="Status",
        field_type="status_enum",
        constraints={"enum_values": list(REVIEW_PIPELINE_STATUS_VALUES)},
    ),
    "total_units": ReviewFieldMetadata(
        field_name="total_units",
        label="Total units",
        field_type="integer",
        constraints={"min": 0},
    ),
    "affordable_units": ReviewFieldMetadata(
        field_name="affordable_units",
        label="Affordable units",
        field_type="integer",
        constraints={"min": 0},
    ),
    "market_rate_units": ReviewFieldMetadata(
        field_name="market_rate_units",
        label="Market-rate units",
        field_type="integer",
        constraints={"min": 0},
    ),
    "workforce_units": ReviewFieldMetadata(
        field_name="workforce_units",
        label="Workforce units",
        field_type="integer",
        constraints={"min": 0},
    ),
    "stories": ReviewFieldMetadata(
        field_name="stories",
        label="Stories",
        field_type="integer",
        constraints={"min": 0},
    ),
    "developer": ReviewFieldMetadata(
        field_name="developer",
        label="Developer",
        field_type="developer",
        constraints={},
    ),
    "product_type": ReviewFieldMetadata(
        field_name="product_type",
        label="Product type",
        field_type="product_type",
        constraints={"enum_values": [member.value for member in ProductType]},
    ),
    "age_restriction": ReviewFieldMetadata(
        field_name="age_restriction",
        label="Age restriction",
        field_type="age_restriction",
        constraints={"enum_values": [member.value for member in AgeRestriction]},
    ),
    "date_delivery": ReviewFieldMetadata(
        field_name="date_delivery",
        label="Delivery date",
        field_type="date",
        constraints={},
    ),
}

REVIEW_VALUE_CHANGE_FIELD_NAMES: frozenset[str] = frozenset(REVIEW_FIELD_METADATA)
REVIEW_INTEGER_FIELD_NAMES: frozenset[str] = frozenset(
    field_name
    for field_name, metadata in REVIEW_FIELD_METADATA.items()
    if metadata.field_type == "integer"
)


def field_metadata_for_review(field_name: str) -> ReviewFieldMetadata:
    metadata = REVIEW_FIELD_METADATA.get(field_name)
    if metadata is not None:
        return metadata
    return ReviewFieldMetadata(
        field_name=field_name,
        label=_fallback_label(field_name),
        field_type="text",
        constraints={},
    )


def _fallback_label(field_name: str) -> str:
    return " ".join(
        word.capitalize() for word in field_name.replace("-", "_").split("_") if word
    )

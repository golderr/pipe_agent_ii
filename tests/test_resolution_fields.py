from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from tcg_pipeline.db.models import (
    AgeRestriction,
    Evidence,
    PipelineStatus,
    ProductType,
    Project,
)
from tcg_pipeline.resolution.fields.age_restriction import resolve_age_restriction
from tcg_pipeline.resolution.fields.delivery_year import resolve_delivery_year
from tcg_pipeline.resolution.fields.developer import resolve_developer
from tcg_pipeline.resolution.fields.product_type import resolve_product_type
from tcg_pipeline.resolution.fields.status import resolve_status
from tcg_pipeline.resolution.fields.units import resolve_units


def _build_project() -> Project:
    return Project(
        canonical_address="123 MAIN STREET LOS ANGELES CA 90012",
        raw_addresses=["123 Main St"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
    )


def _build_evidence(
    *,
    source_type: str,
    source_tier: int,
    evidence_date: date,
    extracted_fields: dict[str, dict[str, object]],
) -> Evidence:
    return Evidence(
        id=uuid.uuid4(),
        source_type=source_type,
        source_tier=source_tier,
        ingest_method="seed_import",
        collected_at=datetime.combine(evidence_date, datetime.min.time(), tzinfo=UTC),
        evidence_date=evidence_date,
        extracted_fields=extracted_fields,
    )


def test_resolve_status_promotes_to_under_construction_from_permit_and_inspection() -> None:
    project = _build_project()
    project.pipeline_status = PipelineStatus.APPROVED

    permit_evidence = _build_evidence(
        source_type="ladbs_permit",
        source_tier=1,
        evidence_date=date(2026, 3, 15),
        extracted_fields={
            "status_evidence_type": {"value": "building_permit_issued", "confidence": None},
        },
    )
    inspection_evidence = _build_evidence(
        source_type="ladbs_inspection",
        source_tier=1,
        evidence_date=date(2026, 4, 10),
        extracted_fields={
            "status_evidence_type": {
                "value": "building_inspection_recorded",
                "confidence": None,
            },
        },
    )

    resolution = resolve_status([permit_evidence, inspection_evidence], project)

    assert resolution.value == PipelineStatus.UNDER_CONSTRUCTION
    assert resolution.confidence.value == "high"


def test_resolve_units_uses_most_recent_evidence() -> None:
    project = _build_project()
    older = _build_evidence(
        source_type="costar",
        source_tier=3,
        evidence_date=date(2025, 12, 1),
        extracted_fields={"total_units": {"value": 200, "confidence": None}},
    )
    newer = _build_evidence(
        source_type="pipedream",
        source_tier=1,
        evidence_date=date(2026, 3, 1),
        extracted_fields={"total_units": {"value": 240, "confidence": None}},
    )

    resolution = resolve_units([older, newer], project, "total_units")

    assert resolution.value == 240


def test_resolve_delivery_year_estimates_midyear_when_explicit_date_missing() -> None:
    project = _build_project()

    resolution = resolve_delivery_year(
        [],
        project,
        resolved_status=PipelineStatus.PROPOSED,
        resolved_total_units=600,
    )

    expected_year = date.today().year + 7
    assert resolution.value == date(expected_year, 7, 1)
    assert resolution.metadata["provenance"] == "estimated_calc"


def test_resolve_developer_prefers_pipedream_when_dates_tie() -> None:
    project = _build_project()
    same_day = date(2026, 4, 1)
    pipedream_evidence = _build_evidence(
        source_type="pipedream",
        source_tier=1,
        evidence_date=same_day,
        extracted_fields={"developer": {"value": "TCG Research", "confidence": None}},
    )
    costar_evidence = _build_evidence(
        source_type="costar",
        source_tier=3,
        evidence_date=same_day,
        extracted_fields={"developer": {"value": "Different Dev", "confidence": None}},
    )

    resolution = resolve_developer([costar_evidence, pipedream_evidence], project)

    assert resolution.value == "TCG Research"


def test_resolve_age_restriction_defaults_to_unknown_without_explicit_evidence() -> None:
    project = _build_project()

    resolution = resolve_age_restriction([], project)

    assert resolution.value == AgeRestriction.UNKNOWN


def test_resolve_product_type_uses_most_recent_explicit_value() -> None:
    project = _build_project()
    project.product_type = ProductType.UNKNOWN
    older = _build_evidence(
        source_type="costar",
        source_tier=3,
        evidence_date=date(2025, 1, 1),
        extracted_fields={"product_type": {"value": ProductType.CONDO.value, "confidence": None}},
    )
    newer = _build_evidence(
        source_type="pipedream",
        source_tier=1,
        evidence_date=date(2026, 1, 1),
        extracted_fields={
            "product_type": {"value": ProductType.APARTMENT.value, "confidence": None}
        },
    )

    resolution = resolve_product_type([older, newer], project)

    assert resolution.value == ProductType.APARTMENT

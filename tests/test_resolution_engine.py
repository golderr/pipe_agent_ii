from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from tcg_pipeline.db.models import (
    AgeRestriction,
    Evidence,
    PipelineStatus,
    ProductType,
    Project,
    ResolutionLog,
    StatusHistory,
)
from tcg_pipeline.resolution import resolve_project


def test_resolve_project_logs_only_discrepancies_and_can_apply(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("evidence"):
        pytest.skip("Apply the evidence layer migration before running resolution tests.")

    project = Project(
        canonical_address="123 MAIN STREET LOS ANGELES CA 90012",
        raw_addresses=["123 Main St"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        pipeline_status=PipelineStatus.PROPOSED,
        total_units=120,
        product_type=ProductType.APARTMENT,
        age_restriction=AgeRestriction.UNKNOWN,
        developer="TCG Research",
    )
    postgres_session.add(project)
    postgres_session.flush()

    postgres_session.add_all(
        [
            Evidence(
                project_id=project.id,
                source_type="pipedream",
                source_tier=1,
                ingest_method="seed_import",
                collected_at=datetime(2026, 4, 1, tzinfo=UTC),
                evidence_date=date(2026, 4, 1),
                extracted_fields={
                    "pipeline_status": {"value": PipelineStatus.PROPOSED.value, "confidence": None},
                    "total_units": {"value": 120, "confidence": None},
                    "product_type": {"value": ProductType.APARTMENT.value, "confidence": None},
                    "age_restriction": {"value": AgeRestriction.UNKNOWN.value, "confidence": None},
                    "developer": {"value": "TCG Research", "confidence": None},
                },
            ),
            Evidence(
                project_id=project.id,
                source_type="ladbs_permit",
                source_tier=1,
                ingest_method="scheduled_collector",
                collected_at=datetime(2026, 4, 5, tzinfo=UTC),
                evidence_date=date(2026, 4, 5),
                extracted_fields={
                    "status_evidence_type": {"value": "building_permit_issued", "confidence": None}
                },
            ),
            Evidence(
                project_id=project.id,
                source_type="ladbs_inspection",
                source_tier=1,
                ingest_method="scheduled_collector",
                collected_at=datetime(2026, 4, 10, tzinfo=UTC),
                evidence_date=date(2026, 4, 10),
                extracted_fields={
                    "status_evidence_type": {
                        "value": "building_inspection_recorded",
                        "confidence": None,
                    }
                },
            ),
        ]
    )
    postgres_session.flush()

    dry_run = resolve_project(project.id, postgres_session, apply=False, write_resolution_log=True)
    postgres_session.flush()

    assert "pipeline_status" in dry_run.changed_fields

    logged_fields = postgres_session.execute(
        select(ResolutionLog.field).where(ResolutionLog.project_id == project.id)
    ).scalars().all()
    assert "pipeline_status" in logged_fields
    assert "confidence_reason" in logged_fields
    assert "likelihood_breakdown" in logged_fields
    assert "total_units" not in logged_fields

    apply_result = resolve_project(
        project.id,
        postgres_session,
        apply=True,
        write_resolution_log=False,
    )
    postgres_session.flush()

    assert "pipeline_status" in apply_result.changed_fields
    assert project.pipeline_status == PipelineStatus.UNDER_CONSTRUCTION

    status_history_rows = postgres_session.execute(
        select(StatusHistory).where(StatusHistory.project_id == project.id)
    ).scalars().all()
    assert len(status_history_rows) == 1
    assert status_history_rows[0].status == PipelineStatus.UNDER_CONSTRUCTION
    assert status_history_rows[0].source == "ladbs_inspection"
    assert "Evidence type: building_inspection_recorded" in (status_history_rows[0].notes or "")


def test_resolve_project_keeps_existing_values_when_partial_evidence_arrives(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("evidence"):
        pytest.skip("Apply the evidence layer migration before running resolution tests.")

    project = Project(
        canonical_address="815 SOUTH KINGSLEY DRIVE LOS ANGELES CA 90005",
        raw_addresses=["815 S Kingsley Dr"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        pipeline_status=PipelineStatus.COMPLETE,
        product_type=ProductType.APARTMENT,
        age_restriction=AgeRestriction.NON_AGE_RESTRICTED,
        developer="Jamison Services",
        date_delivery=date(2024, 9, 15),
    )
    postgres_session.add(project)
    postgres_session.flush()

    postgres_session.add(
        Evidence(
            project_id=project.id,
            source_type="ladbs_permit",
            source_tier=1,
            ingest_method="scheduled_collector",
            collected_at=datetime(2026, 4, 5, tzinfo=UTC),
            evidence_date=date(2021, 9, 22),
            extracted_fields={
                "status_evidence_type": {"value": "building_permit_issued", "confidence": None}
            },
        )
    )
    postgres_session.flush()

    resolution = resolve_project(
        project.id,
        postgres_session,
        apply=False,
        write_resolution_log=False,
    )

    assert resolution.field_resolutions["pipeline_status"].value == PipelineStatus.COMPLETE
    assert resolution.field_resolutions["product_type"].value == ProductType.APARTMENT
    assert (
        resolution.field_resolutions["age_restriction"].value
        == AgeRestriction.NON_AGE_RESTRICTED
    )
    assert resolution.field_resolutions["developer"].value == "Jamison Services"
    assert resolution.field_resolutions["date_delivery"].value == date(2024, 9, 15)

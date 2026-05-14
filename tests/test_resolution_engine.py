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
    SystemAlert,
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


def test_resolve_project_logs_terminal_status_regression_audit(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("evidence"):
        pytest.skip("Apply the evidence layer migration before running resolution tests.")
    column_names = {
        column["name"] for column in inspect(postgres_session.bind).get_columns("resolution_log")
    }
    if "metadata" not in column_names:
        pytest.skip("Apply the status regression metadata migration before running this test.")

    project = Project(
        canonical_address="900 TERMINAL WAY LOS ANGELES CA 90012",
        raw_addresses=["900 Terminal Way"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        pipeline_status=PipelineStatus.COMPLETE,
    )
    postgres_session.add(project)
    postgres_session.flush()
    evidence = Evidence(
        project_id=project.id,
        source_type="pipedream",
        source_tier=1,
        ingest_method="scheduled_collector",
        collected_at=datetime(2026, 4, 5, tzinfo=UTC),
        evidence_date=date(2026, 4, 5),
        extracted_fields={
            "pipeline_status": {
                "value": PipelineStatus.APPROVED.value,
                "confidence": None,
            }
        },
    )
    postgres_session.add(evidence)
    postgres_session.flush()

    result = resolve_project(
        project.id,
        postgres_session,
        apply=False,
        write_resolution_log=True,
    )
    postgres_session.flush()

    assert "pipeline_status" not in result.changed_fields
    row = postgres_session.execute(
        select(ResolutionLog).where(
            ResolutionLog.project_id == project.id,
            ResolutionLog.field == "pipeline_status",
        )
    ).scalar_one()
    assert row.current_value == PipelineStatus.COMPLETE.value
    assert row.resolved_value == PipelineStatus.COMPLETE.value
    assert row.evidence_ids == [evidence.id]
    assert row.rule_applied == "terminal_regression_dropped"
    assert row.metadata_json["regression_candidate_count"] == 1
    candidate = row.metadata_json["regression_candidates"][0]
    assert candidate["current_status"] == PipelineStatus.COMPLETE.value
    assert candidate["proposed_status"] == PipelineStatus.APPROVED.value
    assert candidate["terminal_state_dropped"] is True


def test_resolve_project_logs_suppressed_status_regression_audit(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("evidence"):
        pytest.skip("Apply the evidence layer migration before running resolution tests.")
    column_names = {
        column["name"] for column in inspect(postgres_session.bind).get_columns("resolution_log")
    }
    if "metadata" not in column_names:
        pytest.skip("Apply the status regression metadata migration before running this test.")

    project = Project(
        canonical_address="905 PERMIT WAY LOS ANGELES CA 90012",
        raw_addresses=["905 Permit Way"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        pipeline_status=PipelineStatus.UNDER_CONSTRUCTION,
    )
    postgres_session.add(project)
    postgres_session.flush()
    permit = Evidence(
        project_id=project.id,
        source_type="ladbs_permit",
        source_record_id="24010-10000-00001",
        source_tier=1,
        ingest_method="scheduled_collector",
        collected_at=datetime(2026, 4, 6, tzinfo=UTC),
        evidence_date=date(2026, 4, 6),
        raw_data={"permit_type": "Bldg-New", "status_desc": "Issued"},
        extracted_fields={
            "status_evidence_type": {"value": "building_permit_issued", "confidence": None}
        },
    )
    postgres_session.add(permit)
    postgres_session.flush()

    result = resolve_project(
        project.id,
        postgres_session,
        apply=False,
        write_resolution_log=True,
    )
    postgres_session.flush()

    assert "pipeline_status" not in result.changed_fields
    row = postgres_session.execute(
        select(ResolutionLog).where(
            ResolutionLog.project_id == project.id,
            ResolutionLog.field == "pipeline_status",
        )
    ).scalar_one()
    assert row.current_value == PipelineStatus.UNDER_CONSTRUCTION.value
    assert row.resolved_value == PipelineStatus.UNDER_CONSTRUCTION.value
    assert row.evidence_ids == [permit.id]
    assert row.rule_applied == "regression_candidate_suppressed"
    assert row.metadata_json["regression_candidate_count"] == 0
    assert row.metadata_json["suppressed_regression_candidate_count"] == 1
    suppressed = row.metadata_json["suppressed_regression_candidates"]
    assert len(suppressed) == 1
    assert suppressed[0]["suppression_reason"] == "ladbs_additive_paperwork"
    assert suppressed[0]["evidence_ids"] == [str(permit.id)]


def test_resolve_project_raises_system_alert_for_unknown_ladbs_status_desc(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("evidence"):
        pytest.skip("Apply the evidence layer migration before running resolution tests.")
    if not inspect(postgres_session.bind).has_table("system_alerts"):
        pytest.skip("Apply the system_alerts migration before running this test.")
    column_names = {
        column["name"] for column in inspect(postgres_session.bind).get_columns("resolution_log")
    }
    if "metadata" not in column_names:
        pytest.skip("Apply the status regression metadata migration before running this test.")

    unknown_status = "Beta Review Unknown Status"
    project = Project(
        canonical_address="906 ALERT WAY LOS ANGELES CA 90012",
        raw_addresses=["906 Alert Way"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        pipeline_status=PipelineStatus.UNDER_CONSTRUCTION,
    )
    postgres_session.add(project)
    postgres_session.flush()
    permit = Evidence(
        project_id=project.id,
        source_type="ladbs_permit",
        source_record_id="24010-10000-00002",
        source_tier=1,
        ingest_method="scheduled_collector",
        collected_at=datetime(2026, 4, 7, tzinfo=UTC),
        evidence_date=date(2026, 4, 7),
        raw_data={"permit_type": "Bldg-New", "status_desc": unknown_status},
        extracted_fields={
            "status_evidence_type": {"value": "building_permit_issued", "confidence": None}
        },
    )
    postgres_session.add(permit)
    postgres_session.flush()

    resolve_project(
        project.id,
        postgres_session,
        apply=False,
        write_resolution_log=True,
    )
    postgres_session.flush()

    alert = postgres_session.execute(
        select(SystemAlert).where(
            SystemAlert.alert_key == "ladbs_unknown_permit_status",
            SystemAlert.scope == {"status_desc": unknown_status},
        )
    ).scalar_one()
    assert alert.severity == "info"
    assert alert.detail == {"evidence_id": str(permit.id)}
    assert unknown_status in alert.message


def test_resolve_project_value_change_with_regression_candidate_persists_metadata(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("evidence"):
        pytest.skip("Apply the evidence layer migration before running resolution tests.")
    column_names = {
        column["name"] for column in inspect(postgres_session.bind).get_columns("resolution_log")
    }
    if "metadata" not in column_names:
        pytest.skip("Apply the status regression metadata migration before running this test.")

    project = Project(
        canonical_address="910 STATUS WAY LOS ANGELES CA 90012",
        raw_addresses=["910 Status Way"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        pipeline_status=PipelineStatus.UNDER_CONSTRUCTION,
    )
    postgres_session.add(project)
    postgres_session.flush()
    stale_report = Evidence(
        project_id=project.id,
        source_type="pipedream",
        source_tier=1,
        ingest_method="scheduled_collector",
        collected_at=datetime(2025, 4, 5, tzinfo=UTC),
        evidence_date=date(2025, 4, 5),
        extracted_fields={
            "pipeline_status": {
                "value": PipelineStatus.APPROVED.value,
                "confidence": None,
            }
        },
    )
    cofo = Evidence(
        project_id=project.id,
        source_type="ladbs_cofo",
        source_tier=1,
        ingest_method="scheduled_collector",
        collected_at=datetime(2026, 4, 5, tzinfo=UTC),
        evidence_date=date(2026, 4, 5),
        extracted_fields={
            "status_evidence_type": {
                "value": "certificate_of_occupancy_issued",
                "confidence": None,
            }
        },
    )
    postgres_session.add_all([stale_report, cofo])
    postgres_session.flush()

    result = resolve_project(
        project.id,
        postgres_session,
        apply=False,
        write_resolution_log=True,
    )
    postgres_session.flush()

    assert "pipeline_status" in result.changed_fields
    row = postgres_session.execute(
        select(ResolutionLog).where(
            ResolutionLog.project_id == project.id,
            ResolutionLog.field == "pipeline_status",
        )
    ).scalar_one()
    assert row.current_value == PipelineStatus.UNDER_CONSTRUCTION.value
    assert row.resolved_value == PipelineStatus.COMPLETE.value
    assert row.rule_applied == "direct_cofo_evidence"
    assert row.metadata_json["regression_candidate_count"] == 1
    candidate = row.metadata_json["regression_candidates"][0]
    assert candidate["current_status"] == PipelineStatus.UNDER_CONSTRUCTION.value
    assert candidate["proposed_status"] == PipelineStatus.APPROVED.value
    assert candidate["evidence_ids"] == [str(stale_report.id)]
    assert candidate["terminal_state_dropped"] is False


def test_resolve_project_ignores_superseded_evidence(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("evidence"):
        pytest.skip("Apply the evidence layer migration before running resolution tests.")

    project = Project(
        canonical_address="900 SUPERSEDED WAY LOS ANGELES CA 90012",
        raw_addresses=["900 Superseded Way"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        total_units=100,
        pipeline_status=PipelineStatus.PROPOSED,
    )
    postgres_session.add(project)
    postgres_session.flush()
    active = Evidence(
        project_id=project.id,
        source_type="news_article",
        source_tier=2,
        ingest_method="news_paste_a_link",
        collected_at=datetime(2026, 4, 29, tzinfo=UTC),
        evidence_date=date(2026, 4, 29),
        extracted_fields={"total_units": {"value": 120, "confidence": "high"}},
    )
    superseded = Evidence(
        project_id=project.id,
        source_type="news_article",
        source_tier=2,
        ingest_method="news_reextraction",
        collected_at=datetime(2026, 4, 30, tzinfo=UTC),
        evidence_date=date(2026, 4, 30),
        extracted_fields={"total_units": {"value": 240, "confidence": "high"}},
        superseded_at=datetime(2026, 4, 30, 1, tzinfo=UTC),
    )
    postgres_session.add_all([active, superseded])
    postgres_session.flush()

    resolution = resolve_project(
        project.id,
        postgres_session,
        apply=False,
        write_resolution_log=False,
    )

    assert resolution.field_resolutions["total_units"].value == 120
    assert resolution.field_resolutions["last_evidence_date"].value == date(2026, 4, 29)

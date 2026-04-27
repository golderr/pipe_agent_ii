from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from scripts.verify_researcher_overrides_migration import (
    OverrideMigrationSummary,
    SnapshotValidationError,
    build_resolution_snapshot,
    compare_resolution_snapshots,
    summarize_override_state,
)
from tcg_pipeline.db.models import PipelineStatus, Project, ResearcherOverride


def test_override_migration_summary_ok_allows_pre_migration_state() -> None:
    summary = OverrideMigrationSummary(
        legacy_project_count=23,
        legacy_pair_count=24,
        table_exists=False,
        active_table_row_count=0,
        legacy_only_pair_count=0,
        table_only_pair_count=0,
        mismatched_pair_count=0,
    )

    assert summary.ok is True


def test_override_migration_summary_fails_post_migration_divergence() -> None:
    summary = OverrideMigrationSummary(
        legacy_project_count=23,
        legacy_pair_count=24,
        table_exists=True,
        active_table_row_count=23,
        legacy_only_pair_count=1,
        table_only_pair_count=0,
        mismatched_pair_count=0,
    )

    assert summary.ok is False


def test_override_migration_summary_requires_post_migration_artifacts() -> None:
    summary = OverrideMigrationSummary(
        legacy_project_count=23,
        legacy_pair_count=24,
        table_exists=True,
        active_table_row_count=24,
        legacy_only_pair_count=0,
        table_only_pair_count=0,
        mismatched_pair_count=0,
        unique_active_index_exists=True,
        rls_enabled=True,
        read_policy_exists=False,
        authenticated_select_grant_exists=True,
    )

    assert summary.ok is False


def test_compare_resolution_snapshots_reports_field_changes() -> None:
    before = {
        "project_count": 1,
        "projects": [
            {
                "project_id": "project-1",
                "fields": {
                    "total_units": 120,
                    "developer": "Legacy Developer",
                },
            }
        ]
    }
    current = {
        "project_count": 1,
        "projects": [
            {
                "project_id": "project-1",
                "fields": {
                    "total_units": 121,
                    "developer": "Legacy Developer",
                },
            }
        ]
    }

    assert compare_resolution_snapshots(before, current) == [
        "project-1.total_units: 120 -> 121"
    ]


def test_compare_resolution_snapshots_rejects_malformed_input() -> None:
    current = {"project_count": 0, "projects": []}

    with pytest.raises(SnapshotValidationError, match="missing a projects list"):
        compare_resolution_snapshots({}, current, allow_empty=True)


def test_compare_resolution_snapshots_rejects_empty_snapshot_without_opt_in() -> None:
    empty = {"project_count": 0, "projects": []}

    with pytest.raises(SnapshotValidationError, match="has no projects"):
        compare_resolution_snapshots(empty, empty)


def test_summarize_override_state_counts_seeded_table_and_legacy_drift(
    postgres_session: Session,
) -> None:
    _ensure_researcher_overrides_table(postgres_session)
    base_summary = summarize_override_state(postgres_session)
    set_at = datetime(2026, 4, 24, 12, 0, tzinfo=UTC)

    legacy_only_project = _project(
        "900 LEGACY ONLY WAY LOS ANGELES CA 90012",
        researcher_override={
            "developer": {
                "value": "Legacy Only Dev",
                "set_by": "legacy",
                "set_at": set_at.isoformat(),
                "mode": "sticky",
            }
        },
    )
    table_only_project = _project("901 TABLE ONLY WAY LOS ANGELES CA 90012")
    matching_project = _project(
        "902 MATCHING OVERRIDE WAY LOS ANGELES CA 90012",
        researcher_override={
            "developer": {
                "value": "Matching Dev",
                "set_by": "researcher",
                "set_at": set_at.isoformat(),
                "note": "Matched metadata.",
                "source_url": "https://example.com/matching",
                "mode": "until_newer_evidence",
                "baseline": {"source_type": "costar"},
            }
        },
    )
    mismatched_project = _project(
        "903 MISMATCHED BASELINE WAY LOS ANGELES CA 90012",
        researcher_override={
            "total_units": {
                "value": 120,
                "set_by": "researcher",
                "set_at": set_at.isoformat(),
                "note": "Baseline should match.",
                "source_url": "https://example.com/mismatch",
                "mode": "until_newer_evidence",
                "baseline": {"source_type": "costar"},
            }
        },
    )
    postgres_session.add_all(
        [
            legacy_only_project,
            table_only_project,
            matching_project,
            mismatched_project,
        ]
    )
    postgres_session.flush()
    postgres_session.add_all(
        [
            ResearcherOverride(
                project_id=table_only_project.id,
                field_name="developer",
                value="Table Only Dev",
                set_by_label="table",
                set_at=set_at,
                mode="sticky",
            ),
            ResearcherOverride(
                project_id=matching_project.id,
                field_name="developer",
                value="Matching Dev",
                set_by_label="researcher",
                set_at=set_at,
                note="Matched metadata.",
                source_url="https://example.com/matching",
                mode="until_newer_evidence",
                baseline={"source_type": "costar"},
            ),
            ResearcherOverride(
                project_id=mismatched_project.id,
                field_name="total_units",
                value=120,
                set_by_label="researcher",
                set_at=set_at,
                note="Baseline should match.",
                source_url="https://example.com/mismatch",
                mode="until_newer_evidence",
                baseline={"source_type": "ladbs_permit"},
            ),
        ]
    )
    postgres_session.flush()

    summary = summarize_override_state(postgres_session)

    assert summary.legacy_pair_count == base_summary.legacy_pair_count + 3
    assert summary.active_table_row_count == base_summary.active_table_row_count + 3
    assert summary.legacy_only_pair_count == base_summary.legacy_only_pair_count + 1
    assert summary.table_only_pair_count == base_summary.table_only_pair_count + 1
    assert summary.mismatched_pair_count == base_summary.mismatched_pair_count + 1
    assert f"{legacy_only_project.id}.developer" in summary.legacy_only_pairs
    assert f"{table_only_project.id}.developer" in summary.table_only_pairs
    assert f"{mismatched_project.id}.total_units" in summary.mismatched_pairs


def test_build_resolution_snapshot_uses_pair_keys_for_override_fields(
    postgres_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_researcher_overrides_table(postgres_session)
    set_at = datetime(2026, 4, 24, 12, 0, tzinfo=UTC)
    project = _project(
        "904 SNAPSHOT OVERRIDE WAY LOS ANGELES CA 90012",
        researcher_override={
            "developer": {
                "value": "Legacy Snapshot Dev",
                "set_by": "researcher",
                "set_at": set_at.isoformat(),
                "mode": "sticky",
            }
        },
    )
    postgres_session.add(project)
    postgres_session.flush()
    postgres_session.add(
        ResearcherOverride(
            project_id=project.id,
            field_name="total_units",
            value=88,
            set_by_label="researcher",
            set_at=set_at,
            mode="sticky",
        )
    )
    postgres_session.flush()

    def fake_resolve_project(project_id, session, *, apply, write_resolution_log):
        assert project_id
        assert apply is False
        assert write_resolution_log is False
        return SimpleNamespace(
            field_resolutions={
                "developer": SimpleNamespace(value="Resolved Snapshot Dev"),
                "total_units": SimpleNamespace(value=88),
            }
        )

    monkeypatch.setattr(
        "scripts.verify_researcher_overrides_migration.resolve_project",
        fake_resolve_project,
    )

    snapshot = build_resolution_snapshot(postgres_session)
    snapshot_project = next(
        row for row in snapshot["projects"] if row["project_id"] == str(project.id)
    )

    assert snapshot_project["fields"] == {
        "developer": "Resolved Snapshot Dev",
        "total_units": 88,
    }


def _ensure_researcher_overrides_table(postgres_session: Session) -> None:
    if not inspect(postgres_session.bind).has_table("researcher_overrides"):
        ResearcherOverride.__table__.create(bind=postgres_session.connection())
        postgres_session.info.pop("researcher_overrides_table_exists", None)


def _project(canonical_address: str, **overrides) -> Project:
    defaults = {
        "raw_addresses": [canonical_address],
        "market": "los_angeles",
        "city": "Los Angeles",
        "state": "CA",
        "county": "Los Angeles",
        "pipeline_status": PipelineStatus.PROPOSED,
    }
    defaults.update(overrides)
    return Project(canonical_address=canonical_address, **defaults)

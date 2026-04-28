from __future__ import annotations

import uuid
from datetime import UTC, datetime
from importlib import util
from pathlib import Path

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from tcg_pipeline.db.models import PipelineStatus, Project, ResearcherOverride
from tcg_pipeline.db.researcher_overrides import (
    active_researcher_overrides_for_project,
    clear_researcher_override_fields,
    upsert_researcher_overrides,
)
from tcg_pipeline.resolution import resolve_project


def test_researcher_override_upsert_writes_table(
    postgres_session: Session,
) -> None:
    _ensure_researcher_overrides_table(postgres_session)
    project = _project("100 OVERRIDE TABLE WAY LOS ANGELES CA 90012")
    postgres_session.add(project)
    postgres_session.flush()

    upsert_researcher_overrides(
        postgres_session,
        project,
        {
            "total_units": {
                "value": 212,
                "set_by": "nate",
                "set_at": "2026-04-24T12:00:00Z",
                "note": "Confirmed by researcher.",
                "mode": "until_newer_evidence",
                "baseline": {"source_type": "costar"},
            }
        },
    )
    postgres_session.flush()

    table_override = postgres_session.execute(
        select(ResearcherOverride).where(
            ResearcherOverride.project_id == project.id,
            ResearcherOverride.field_name == "total_units",
        )
    ).scalar_one()
    assert table_override.value == 212
    assert table_override.set_by_label == "nate"
    assert table_override.set_at == datetime(2026, 4, 24, 12, 0, tzinfo=UTC)
    assert table_override.note == "Confirmed by researcher."
    assert table_override.mode == "until_newer_evidence"
    assert table_override.baseline == {"source_type": "costar"}

    active = active_researcher_overrides_for_project(postgres_session, project)
    assert active["total_units"]["value"] == 212
    assert active["total_units"]["set_by"] == "nate"


def test_researcher_override_reaffirm_preserves_first_set_metadata(
    postgres_session: Session,
) -> None:
    _ensure_researcher_overrides_table(postgres_session)
    project = _project("100 REAFFIRM TABLE WAY LOS ANGELES CA 90012")
    first_user_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    first_set_at = datetime(2026, 4, 20, 9, 30, tzinfo=UTC)
    postgres_session.add(project)
    postgres_session.flush()

    upsert_researcher_overrides(
        postgres_session,
        project,
        {
            "total_units": {
                "value": 212,
                "set_by": "original",
                "set_at": first_set_at.isoformat(),
                "mode": "until_newer_evidence",
            }
        },
        set_by_user_id=first_user_id,
    )
    postgres_session.flush()

    upsert_researcher_overrides(
        postgres_session,
        project,
        {
            "total_units": {
                "value": 215,
                "set_by": "reaffirming-script",
                "note": "Reaffirmed after review.",
                "mode": "until_newer_evidence",
            }
        },
    )
    postgres_session.flush()

    table_override = postgres_session.execute(
        select(ResearcherOverride).where(
            ResearcherOverride.project_id == project.id,
            ResearcherOverride.field_name == "total_units",
        )
    ).scalar_one()
    assert table_override.value == 215
    assert table_override.set_at == first_set_at
    assert table_override.set_by_user_id == first_user_id
    assert table_override.set_by_label == "original"
    assert table_override.reaffirmed_at is not None
    assert table_override.note == "Reaffirmed after review."


def test_researcher_override_clear_marks_table_row(
    postgres_session: Session,
) -> None:
    _ensure_researcher_overrides_table(postgres_session)
    project = _project("101 CLEAR OVERRIDE WAY LOS ANGELES CA 90012")
    postgres_session.add(project)
    postgres_session.flush()
    upsert_researcher_overrides(
        postgres_session,
        project,
        {"developer": {"value": "Reviewer Dev", "set_by": "nate"}},
    )
    postgres_session.flush()

    clear_researcher_override_fields(postgres_session, project, {"developer"})
    postgres_session.flush()
    postgres_session.refresh(project)

    table_override = postgres_session.execute(
        select(ResearcherOverride).where(
            ResearcherOverride.project_id == project.id,
            ResearcherOverride.field_name == "developer",
        )
    ).scalar_one()
    assert table_override.cleared_at is not None
    assert active_researcher_overrides_for_project(postgres_session, project) == {}


def test_active_overrides_read_active_table_rows(postgres_session: Session) -> None:
    _ensure_researcher_overrides_table(postgres_session)
    project = _project(
        "101 MIXED OVERRIDE WAY LOS ANGELES CA 90012",
        total_units=10,
    )
    postgres_session.add(project)
    postgres_session.flush()
    postgres_session.add(
        ResearcherOverride(
            project_id=project.id,
            field_name="total_units",
            value=222,
            set_by_label="table",
            set_at=datetime(2026, 4, 24, 12, 0, tzinfo=UTC),
            mode="until_newer_evidence",
        )
    )
    postgres_session.flush()

    active = active_researcher_overrides_for_project(postgres_session, project)
    result = resolve_project(project.id, postgres_session, apply=False, write_resolution_log=False)

    assert active["total_units"]["value"] == 222
    assert result.field_resolutions["total_units"].value == 222


def test_resolve_project_uses_table_override(
    postgres_session: Session,
) -> None:
    _ensure_researcher_overrides_table(postgres_session)
    project = _project(
        "102 TABLE PREFERRED WAY LOS ANGELES CA 90012",
        total_units=10,
    )
    postgres_session.add(project)
    postgres_session.flush()
    postgres_session.add(
        ResearcherOverride(
            project_id=project.id,
            field_name="total_units",
            value=222,
            set_by_label="table",
            set_at=datetime(2026, 4, 24, 12, 0, tzinfo=UTC),
            mode="until_newer_evidence",
        )
    )
    postgres_session.flush()

    result = resolve_project(project.id, postgres_session, apply=False, write_resolution_log=False)

    assert result.field_resolutions["total_units"].value == 222
    assert result.field_resolutions["total_units"].metadata["set_by"] == "table"


def test_researcher_override_migration_backfill_entry_normalizes_payloads() -> None:
    migration = _load_researcher_override_migration()
    project_id = uuid.UUID("22222222-2222-2222-2222-222222222222")
    fallback_set_at = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)

    plain = migration._backfill_entry(
        project_id=project_id,
        field_name="total_units",
        payload=120,
        fallback_set_at=fallback_set_at,
    )
    assert plain["value"] == 120
    assert plain["set_by_label"] == "legacy"
    assert plain["set_at"] == fallback_set_at
    assert plain["mode"] == "sticky"

    structured = migration._backfill_entry(
        project_id=project_id,
        field_name="developer",
        payload={
            "value": "Helio Capital",
            "set_by": " nate ",
            "set_at": "2026-04-24T12:00:00-07:00",
            "note": "Confirmed.",
            "source_url": "https://example.com",
            "mode": "until_newer_evidence",
            "baseline": {"source_type": "costar"},
        },
        fallback_set_at=fallback_set_at,
    )
    assert structured["value"] == "Helio Capital"
    assert structured["set_by_label"] == "nate"
    assert structured["set_at"] == datetime.fromisoformat("2026-04-24T12:00:00-07:00")
    assert structured["baseline"] == {"source_type": "costar"}

    malformed = migration._backfill_entry(
        project_id=project_id,
        field_name="pipeline_status",
        payload={
            "value": "Proposed",
            "set_by": " ",
            "set_at": "not-a-date",
            "baseline": "not-json-object",
        },
        fallback_set_at=fallback_set_at,
    )
    assert malformed["set_by_label"] is None
    assert malformed["set_at"] == fallback_set_at
    assert malformed["baseline"] is None


def _ensure_researcher_overrides_table(postgres_session: Session) -> None:
    if not inspect(postgres_session.bind).has_table("researcher_overrides"):
        ResearcherOverride.__table__.create(bind=postgres_session.connection())


def _load_researcher_override_migration():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "2026_04_26_0010_create_researcher_overrides.py"
    )
    spec = util.spec_from_file_location("researcher_override_migration", migration_path)
    assert spec is not None
    assert spec.loader is not None
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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

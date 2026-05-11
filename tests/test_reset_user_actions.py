from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from tcg_pipeline.db.models import (
    ChangeLog,
    ChangeType,
    PipelineStatus,
    Priority,
    Project,
    ProjectNote,
    ProjectRelationship,
    RelationshipType,
    ResearcherOverride,
    ReviewDecision,
    ReviewDecisionAction,
    ReviewItem,
    ReviewItemStatus,
    ReviewItemType,
    StatusHistory,
    SystemAlert,
)
from tcg_pipeline.ops import reset_user_actions as reset_ops
from tcg_pipeline.ops.reset_user_actions import (
    PgDumpBackup,
    assert_reset_user_actions_allowed,
    build_reset_user_actions_plan,
    create_pg_dump_backup,
    reset_user_actions,
)
from tcg_pipeline.settings import Settings


def test_reset_user_actions_clears_user_rows_and_preserves_system_rows(
    postgres_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_reset_tables(postgres_session)
    before_plan = build_reset_user_actions_plan(postgres_session)
    reviewer_id = uuid.uuid4()
    project = _project("100 RESET USER WAY LOS ANGELES CA 90012")
    human_related = _project("101 RESET USER WAY LOS ANGELES CA 90012")
    seed_related = _project("102 RESET USER WAY LOS ANGELES CA 90012")
    postgres_session.add_all([project, human_related, seed_related])
    postgres_session.flush()

    review_item = ReviewItem(
        project_id=project.id,
        item_type=ReviewItemType.STATUS_CHANGE,
        status=ReviewItemStatus.ACCEPTED,
        state="committed",
        priority=Priority.HIGH,
        field_name="pipeline_status",
        assigned_to="reviewer@example.com",
        resolved_at=datetime(2026, 5, 1, tzinfo=UTC),
        resolved_by="reviewer@example.com",
    )
    human_relationship = ProjectRelationship(
        project_id=project.id,
        related_project_id=human_related.id,
        relationship_type=RelationshipType.PHASE,
    )
    seed_relationship = ProjectRelationship(
        project_id=project.id,
        related_project_id=seed_related.id,
        relationship_type=RelationshipType.MASTER_PLAN,
    )
    postgres_session.add_all([review_item, human_relationship, seed_relationship])
    postgres_session.flush()
    postgres_session.add_all(
        [
            ReviewDecision(
                review_item_id=review_item.id,
                action=ReviewDecisionAction.ACCEPT,
                actor="reviewer@example.com",
                state="committed",
                decision_type="accept_existing",
            ),
            ResearcherOverride(
                project_id=project.id,
                field_name="pipeline_status",
                value=PipelineStatus.UNDER_CONSTRUCTION.value,
            ),
            ProjectNote(
                project_id=project.id,
                note_type="researcher_notes",
                body="Temporary note",
                created_by_user_id=reviewer_id,
                created_by_label="reviewer@example.com",
            ),
            ChangeLog(
                project_id=project.id,
                source="project_relationship",
                field="relationships",
                old_value=None,
                new_value={
                    "relationship_type": RelationshipType.PHASE.value,
                    "related_project_id": str(human_related.id),
                },
                change_type=ChangeType.RESEARCHER_CONFIRMED,
                priority=Priority.LOW,
                reviewed_by="reviewer@example.com",
                reviewed_by_user_id=reviewer_id,
                reviewed_by_email="reviewer@example.com",
            ),
            ChangeLog(
                project_id=project.id,
                source="inline_field",
                field="project_name",
                old_value=None,
                new_value="Edited name",
                change_type=ChangeType.RESEARCHER_CONFIRMED,
                priority=Priority.LOW,
                reviewed_by="reviewer@example.com",
                reviewed_by_user_id=reviewer_id,
                reviewed_by_email="reviewer@example.com",
            ),
            ChangeLog(
                project_id=project.id,
                source="ladbs_permit",
                field="pipeline_status",
                old_value=PipelineStatus.PROPOSED.value,
                new_value=PipelineStatus.APPROVED.value,
                change_type=ChangeType.AUTO_ACCEPTED,
                priority=Priority.LOW,
            ),
            StatusHistory(
                project_id=project.id,
                status=PipelineStatus.PROPOSED,
                status_date=date(2026, 5, 1),
                source="manual_project",
            ),
            StatusHistory(
                project_id=project.id,
                status=PipelineStatus.APPROVED,
                status_date=date(2026, 5, 2),
                source="ladbs_permit",
            ),
        ]
    )
    postgres_session.flush()

    plan = build_reset_user_actions_plan(postgres_session)

    assert plan.counts.review_decisions == before_plan.counts.review_decisions + 1
    assert plan.counts.review_items_to_reset == before_plan.counts.review_items_to_reset + 1
    assert plan.counts.researcher_overrides == before_plan.counts.researcher_overrides + 1
    assert plan.counts.project_notes == before_plan.counts.project_notes + 1
    assert plan.counts.human_change_log_rows == before_plan.counts.human_change_log_rows + 2
    assert (
        plan.counts.human_status_history_rows
        == before_plan.counts.human_status_history_rows + 1
    )
    assert (
        plan.counts.human_project_relationships
        == before_plan.counts.human_project_relationships + 1
    )
    assert plan.counts.projects_to_resolve == before_plan.counts.projects_to_resolve + 3

    backup = PgDumpBackup(
        path=reset_ops.Path("test-reset.dump"),
        sha256="0" * 64,
        size_bytes=1,
        completed_at=datetime(2026, 5, 11, tzinfo=UTC),
    )
    monkeypatch.setattr(
        reset_ops,
        "_re_resolve_all_projects",
        lambda session: {
            "projects_resolved": plan.counts.projects_to_resolve,
            "projects_with_discrepancies": 0,
            "changed_fields": 0,
            "resolution_log_rows": 0,
        },
    )
    result = reset_user_actions(
        postgres_session,
        plan=plan,
        backup=backup,
        actor="test-operator",
        reset_at=datetime(2026, 5, 11, 12, 0, tzinfo=UTC),
    )
    postgres_session.flush()

    postgres_session.refresh(review_item)
    remaining_relationships = postgres_session.execute(
        select(ProjectRelationship).where(ProjectRelationship.project_id == project.id)
    ).scalars().all()
    remaining_change_logs = postgres_session.execute(
        select(ChangeLog).where(ChangeLog.project_id == project.id)
    ).scalars().all()
    remaining_status_history = postgres_session.execute(
        select(StatusHistory).where(StatusHistory.project_id == project.id)
    ).scalars().all()
    alert = postgres_session.get(SystemAlert, result.system_alert_id)

    assert postgres_session.execute(select(ReviewDecision)).scalars().all() == []
    assert review_item.status == ReviewItemStatus.OPEN
    assert review_item.state == "open"
    assert review_item.assigned_to is None
    assert review_item.resolved_at is None
    assert review_item.resolved_by is None
    assert postgres_session.execute(select(ResearcherOverride)).scalars().all() == []
    assert postgres_session.execute(select(ProjectNote)).scalars().all() == []
    assert {relationship.related_project_id for relationship in remaining_relationships} == {
        seed_related.id
    }
    assert [change.source for change in remaining_change_logs] == ["ladbs_permit"]
    assert [history.source for history in remaining_status_history] == ["ladbs_permit"]
    assert alert is not None
    assert alert.alert_key == "reset_user_actions_completed"
    assert alert.detail["actor"] == "test-operator"
    assert alert.detail["counts"]["human_change_log_rows"] == plan.counts.human_change_log_rows
    assert result.projects_resolved == plan.counts.projects_to_resolve


def test_reset_user_actions_guards_require_flag_and_block_production_targets() -> None:
    with pytest.raises(RuntimeError, match="RESET_TOOLS_ENABLED=true"):
        assert_reset_user_actions_allowed(
            Settings(
                _env_file=None,
                database_url="postgresql://user:pass@example.test/tcg",
            )
        )

    with pytest.raises(RuntimeError, match="APP_ENV is production"):
        assert_reset_user_actions_allowed(
            Settings(
                _env_file=None,
                app_env="production",
                reset_tools_enabled=True,
                database_url="postgresql://user:pass@example.test/tcg",
            )
        )

    with pytest.raises(RuntimeError, match="protected database host"):
        assert_reset_user_actions_allowed(
            Settings(
                _env_file=None,
                reset_tools_enabled=True,
                database_url="postgresql://user:pass@example.test/tcg",
                reset_protected_database_hosts="example.test",
            )
        )

    with pytest.raises(RuntimeError, match="protected project ref"):
        assert_reset_user_actions_allowed(
            Settings(
                _env_file=None,
                reset_tools_enabled=True,
                database_url="postgresql://user:pass@staging.test/tcg",
                supabase_project_ref="prod-ref",
                reset_protected_project_refs="prod-ref",
            )
        )


def test_create_pg_dump_backup_writes_checksum_and_converts_sqlalchemy_scheme(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    observed: dict[str, object] = {}

    def fake_run(command, *, capture_output, check, env, text):
        observed["command"] = command
        observed["env"] = env
        assert capture_output is True
        assert check is False
        assert text is True
        destination = command[command.index("--file") + 1]
        reset_ops.Path(destination).write_bytes(b"reset dump")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(reset_ops.subprocess, "run", fake_run)
    backup = create_pg_dump_backup(
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://user:p%40ss@example.test:6543/tcg?sslmode=require",
        ),
        backup_dir=tmp_path,
        now=datetime(2026, 5, 11, 12, 0, tzinfo=UTC),
    )

    assert backup.path.name == "reset_user_actions_20260511_120000.dump"
    assert backup.size_bytes == len(b"reset dump")
    assert backup.sha256 == (
        "1ecda070a78e8f88c00753c8794adc89281349033f002d85a04a409c61924460"
    )
    assert observed["command"] == [
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--file",
        str(backup.path),
    ]
    assert observed["env"]["PGHOST"] == "example.test"
    assert observed["env"]["PGPORT"] == "6543"
    assert observed["env"]["PGUSER"] == "user"
    assert observed["env"]["PGPASSWORD"] == "p@ss"
    assert observed["env"]["PGDATABASE"] == "tcg"
    assert observed["env"]["PGSSLMODE"] == "require"


def _ensure_reset_tables(postgres_session: Session) -> None:
    inspector = inspect(postgres_session.bind)
    required_tables = {
        "change_log",
        "project_notes",
        "project_relationships",
        "projects",
        "researcher_overrides",
        "review_decisions",
        "review_items",
        "status_history",
        "system_alerts",
    }
    missing = sorted(table for table in required_tables if not inspector.has_table(table))
    if missing:
        pytest.skip(f"Apply the latest migrations before running reset tests: {missing}")


def _project(canonical_address: str) -> Project:
    return Project(
        canonical_address=canonical_address,
        raw_addresses=[canonical_address],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        pipeline_status=PipelineStatus.PROPOSED,
    )

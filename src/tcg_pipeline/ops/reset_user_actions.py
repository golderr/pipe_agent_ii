from __future__ import annotations

import hashlib
import os
import subprocess
import uuid
from collections import Counter
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlparse

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from tcg_pipeline.db.models import (
    ChangeLog,
    ChangeType,
    Project,
    ProjectNote,
    ProjectRelationship,
    ResearcherOverride,
    ResolutionLog,
    ReviewDecision,
    ReviewItem,
    ReviewItemStatus,
    StatusHistory,
    SystemAlert,
)
from tcg_pipeline.resolution import resolve_project
from tcg_pipeline.settings import Settings

PRODUCTION_ENV_NAMES = frozenset({"prod", "production"})
HUMAN_STATUS_HISTORY_SOURCES = frozenset({"manual_project"})


@dataclass(frozen=True, slots=True)
class ResetUserActionCounts:
    review_decisions: int = 0
    review_items_to_reset: int = 0
    review_items_conflict_invalidations: int = 0
    researcher_overrides: int = 0
    project_notes: int = 0
    human_change_log_rows: int = 0
    human_status_history_rows: int = 0
    human_project_relationships: int = 0
    projects_to_resolve: int = 0

    def as_dict(self) -> dict[str, int]:
        return {field.name: getattr(self, field.name) for field in fields(self)}


@dataclass(frozen=True, slots=True)
class ResetUserActionsPlan:
    counts: ResetUserActionCounts
    review_item_reopen_ids: tuple[uuid.UUID, ...] = ()
    review_item_conflict_invalidated_ids: tuple[uuid.UUID, ...] = ()
    human_project_relationship_ids: tuple[uuid.UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class PgDumpBackup:
    path: Path
    sha256: str
    size_bytes: int
    completed_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "completed_at": self.completed_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class ResetUserActionsResult:
    counts: ResetUserActionCounts
    backup: PgDumpBackup
    actor: str
    reset_at: datetime
    projects_resolved: int
    projects_with_discrepancies: int
    changed_fields: int
    resolution_log_rows: int
    system_alert_id: uuid.UUID

    def as_dict(self) -> dict[str, Any]:
        return {
            "counts": self.counts.as_dict(),
            "backup": self.backup.as_dict(),
            "actor": self.actor,
            "reset_at": self.reset_at.isoformat(),
            "projects_resolved": self.projects_resolved,
            "projects_with_discrepancies": self.projects_with_discrepancies,
            "changed_fields": self.changed_fields,
            "resolution_log_rows": self.resolution_log_rows,
            "system_alert_id": str(self.system_alert_id),
        }


def assert_reset_user_actions_allowed(settings: Settings) -> None:
    if not settings.reset_tools_enabled:
        raise RuntimeError("RESET_TOOLS_ENABLED=true is required for reset-user-actions.")

    app_env = settings.app_env.strip().lower()
    if app_env in PRODUCTION_ENV_NAMES:
        raise RuntimeError("reset-user-actions is blocked when APP_ENV is production.")

    database_url = settings.database_url or ""
    database_host = _database_host(database_url)
    protected_hosts = _csv_values(settings.reset_protected_database_hosts)
    if database_host and database_host.lower() in protected_hosts:
        raise RuntimeError(
            f"reset-user-actions is blocked for protected database host {database_host}."
        )

    protected_refs = _csv_values(settings.reset_protected_project_refs)
    project_ref = (settings.project_ref or "").strip().lower()
    if project_ref and project_ref in protected_refs:
        raise RuntimeError(
            f"reset-user-actions is blocked for protected project ref {settings.project_ref}."
        )


def create_pg_dump_backup(
    settings: Settings,
    *,
    backup_dir: Path | None = None,
    now: datetime | None = None,
) -> PgDumpBackup:
    raw_database_url = settings.database_url
    if not raw_database_url:
        raise RuntimeError("DATABASE_URL is required to create a pg_dump backup.")

    timestamp = (now or datetime.now(UTC)).strftime("%Y%m%d_%H%M%S")
    destination_dir = backup_dir or settings.reset_backup_dir
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"reset_user_actions_{timestamp}.dump"
    pg_env = _pg_dump_environment(raw_database_url)
    result = subprocess.run(
        [
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "--file",
            str(destination),
        ],
        capture_output=True,
        check=False,
        env=pg_env,
        text=True,
    )
    if result.returncode != 0:
        if destination.exists():
            destination.unlink()
        stderr = result.stderr.strip() or "pg_dump failed without stderr."
        raise RuntimeError(f"pg_dump backup failed: {stderr}")

    return PgDumpBackup(
        path=destination,
        sha256=_sha256_file(destination),
        size_bytes=destination.stat().st_size,
        completed_at=datetime.now(UTC),
    )


def build_reset_user_actions_plan(session: Session) -> ResetUserActionsPlan:
    review_item_selection = _review_item_reset_selection(session)
    human_relationship_ids = _human_project_relationship_ids(session)
    counts = ResetUserActionCounts(
        review_decisions=_count(session, ReviewDecision),
        review_items_to_reset=len(review_item_selection["all_ids"]),
        review_items_conflict_invalidations=len(review_item_selection["conflict_invalidated_ids"]),
        researcher_overrides=_count(session, ResearcherOverride),
        project_notes=_count(session, ProjectNote),
        human_change_log_rows=_count(session, ChangeLog, _human_change_log_filter()),
        human_status_history_rows=_count(
            session,
            StatusHistory,
            StatusHistory.source.in_(sorted(HUMAN_STATUS_HISTORY_SOURCES)),
        ),
        human_project_relationships=len(human_relationship_ids),
        projects_to_resolve=_count(session, Project),
    )
    return ResetUserActionsPlan(
        counts=counts,
        review_item_reopen_ids=review_item_selection["reopen_ids"],
        review_item_conflict_invalidated_ids=review_item_selection["conflict_invalidated_ids"],
        human_project_relationship_ids=human_relationship_ids,
    )


def reset_user_actions(
    session: Session,
    *,
    plan: ResetUserActionsPlan,
    backup: PgDumpBackup,
    actor: str,
    reset_at: datetime | None = None,
) -> ResetUserActionsResult:
    timestamp = reset_at or datetime.now(UTC)

    session.execute(delete(ReviewDecision))
    if plan.review_item_reopen_ids:
        session.execute(
            update(ReviewItem)
            .where(ReviewItem.id.in_(plan.review_item_reopen_ids))
            .values(
                status=ReviewItemStatus.OPEN,
                state="open",
                assigned_to=None,
                resolved_at=None,
                resolved_by=None,
                updated_at=timestamp,
            )
        )
    if plan.review_item_conflict_invalidated_ids:
        session.execute(
            update(ReviewItem)
            .where(ReviewItem.id.in_(plan.review_item_conflict_invalidated_ids))
            .values(
                status=ReviewItemStatus.OPEN,
                state="invalidated",
                assigned_to=None,
                resolved_at=None,
                resolved_by=None,
                updated_at=timestamp,
            )
        )
    session.execute(delete(ResearcherOverride))
    session.execute(delete(ProjectNote))
    if plan.human_project_relationship_ids:
        session.execute(
            delete(ProjectRelationship).where(
                ProjectRelationship.id.in_(plan.human_project_relationship_ids)
            )
        )
    session.execute(
        delete(StatusHistory).where(StatusHistory.source.in_(sorted(HUMAN_STATUS_HISTORY_SOURCES)))
    )
    session.execute(delete(ChangeLog).where(_human_change_log_filter()))

    resolution_counts = _re_resolve_all_projects(session)
    alert = _write_reset_system_alert(
        session,
        counts=plan.counts,
        backup=backup,
        actor=actor,
        reset_at=timestamp,
        resolution_counts=resolution_counts,
    )
    session.flush()

    return ResetUserActionsResult(
        counts=plan.counts,
        backup=backup,
        actor=actor,
        reset_at=timestamp,
        projects_resolved=resolution_counts["projects_resolved"],
        projects_with_discrepancies=resolution_counts["projects_with_discrepancies"],
        changed_fields=resolution_counts["changed_fields"],
        resolution_log_rows=resolution_counts["resolution_log_rows"],
        system_alert_id=alert.id,
    )


def _re_resolve_all_projects(session: Session) -> dict[str, int]:
    project_ids = session.execute(select(Project.id).order_by(Project.id.asc())).scalars().all()
    counts: Counter[str] = Counter()
    for project_id in project_ids:
        session.execute(delete(ResolutionLog).where(ResolutionLog.project_id == project_id))
        result = resolve_project(
            project_id,
            session,
            apply=True,
            write_resolution_log=True,
        )
        counts["projects_resolved"] += 1
        counts["changed_fields"] += len(result.changed_fields)
        counts["resolution_log_rows"] += result.log_entries_created
        if result.changed_fields:
            counts["projects_with_discrepancies"] += 1
    return {
        "projects_resolved": counts["projects_resolved"],
        "projects_with_discrepancies": counts["projects_with_discrepancies"],
        "changed_fields": counts["changed_fields"],
        "resolution_log_rows": counts["resolution_log_rows"],
    }


def _write_reset_system_alert(
    session: Session,
    *,
    counts: ResetUserActionCounts,
    backup: PgDumpBackup,
    actor: str,
    reset_at: datetime,
    resolution_counts: dict[str, int],
) -> SystemAlert:
    alert = SystemAlert(
        alert_key="reset_user_actions_completed",
        severity="info",
        scope={"reset_at": reset_at.isoformat()},
        message="Reset user actions completed.",
        detail={
            "actor": actor,
            "counts": counts.as_dict(),
            "backup": backup.as_dict(),
            "resolution": resolution_counts,
        },
        raised_at=reset_at,
        last_seen_at=reset_at,
    )
    session.add(alert)
    return alert


def _review_item_reset_filter():
    return or_(
        ReviewItem.state.in_(["staged", "committed"]),
        ReviewItem.status != ReviewItemStatus.OPEN,
        ReviewItem.assigned_to.is_not(None),
        ReviewItem.resolved_at.is_not(None),
        ReviewItem.resolved_by.is_not(None),
    )


def _review_item_reset_selection(session: Session) -> dict[str, tuple[uuid.UUID, ...]]:
    rows = session.execute(
        select(
            ReviewItem.id,
            ReviewItem.project_id,
            ReviewItem.field_name,
            ReviewItem.item_type,
            ReviewItem.state,
            ReviewItem.updated_at,
            ReviewItem.created_at,
        ).where(_review_item_reset_filter())
    ).all()
    candidate_ids = {row.id for row in rows}
    if not candidate_ids:
        return {
            "all_ids": (),
            "reopen_ids": (),
            "conflict_invalidated_ids": (),
        }

    existing_active_keys = set(
        session.execute(
            select(ReviewItem.project_id, ReviewItem.field_name, ReviewItem.item_type).where(
                ReviewItem.state.in_(["open", "staged"]),
                ReviewItem.project_id.is_not(None),
                ReviewItem.field_name.is_not(None),
                ReviewItem.id.not_in(candidate_ids),
            )
        ).all()
    )

    reopen_ids: list[uuid.UUID] = []
    conflict_invalidated_ids: list[uuid.UUID] = []
    reopened_keys: set[tuple[Any, Any, Any]] = set()
    for row in sorted(rows, key=_review_item_selection_sort_key):
        if row.project_id is None or row.field_name is None:
            reopen_ids.append(row.id)
            continue

        key = (row.project_id, row.field_name, row.item_type)
        if key in existing_active_keys or key in reopened_keys:
            conflict_invalidated_ids.append(row.id)
            continue

        reopen_ids.append(row.id)
        reopened_keys.add(key)

    return {
        "all_ids": tuple(sorted(candidate_ids, key=str)),
        "reopen_ids": tuple(sorted(reopen_ids, key=str)),
        "conflict_invalidated_ids": tuple(sorted(conflict_invalidated_ids, key=str)),
    }


def _review_item_selection_sort_key(row) -> tuple[int, float, float, str]:
    active_rank = 0 if row.state in {"open", "staged"} else 1
    updated_at = row.updated_at or datetime.min.replace(tzinfo=UTC)
    created_at = row.created_at or datetime.min.replace(tzinfo=UTC)
    return (
        active_rank,
        _descending_datetime_sort_value(updated_at),
        _descending_datetime_sort_value(created_at),
        str(row.id),
    )


def _descending_datetime_sort_value(value: datetime) -> float:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return -value.timestamp()


def _human_change_log_filter():
    return or_(
        ChangeLog.reviewed_by_user_id.is_not(None),
        ChangeLog.reviewed_by_email.is_not(None),
        ChangeLog.reviewed_by.is_not(None),
        ChangeLog.change_type.in_(
            [
                ChangeType.RESEARCHER_CONFIRMED,
                ChangeType.RESEARCHER_REJECTED,
                ChangeType.RESEARCHER_OVERRIDE,
            ]
        ),
        ChangeLog.source.in_(
            [
                "inline_field",
                "inline_override",
                "manual_geocode",
                "manual_project",
                "project_note",
                "project_relationship",
                "review_workflow",
            ]
        ),
    )


def _human_project_relationship_ids(session: Session) -> tuple[uuid.UUID, ...]:
    creation_rows = session.execute(
        select(ChangeLog.project_id, ChangeLog.old_value, ChangeLog.new_value).where(
            _human_change_log_filter(),
            ChangeLog.source == "project_relationship",
            ChangeLog.field == "relationships",
        )
    ).all()
    creation_keys: set[tuple[uuid.UUID, uuid.UUID, str]] = set()
    for project_id, old_value, payload in creation_rows:
        if old_value is not None:
            continue
        if not isinstance(payload, dict):
            continue
        related_project_id = _uuid_or_none(payload.get("related_project_id"))
        relationship_type = payload.get("relationship_type")
        if related_project_id is None or not relationship_type:
            continue
        creation_keys.add((project_id, related_project_id, str(relationship_type)))
    if not creation_keys:
        return ()

    project_ids = {key[0] for key in creation_keys}
    relationships = session.execute(
        select(ProjectRelationship).where(ProjectRelationship.project_id.in_(project_ids))
    ).scalars()
    relationship_ids: list[uuid.UUID] = []
    for relationship in relationships:
        relationship_type = getattr(relationship.relationship_type, "value", None)
        relationship_key = (
            relationship.project_id,
            relationship.related_project_id,
            relationship_type or str(relationship.relationship_type),
        )
        if relationship_key in creation_keys:
            relationship_ids.append(relationship.id)
    return tuple(sorted(relationship_ids, key=str))


def _count(session: Session, model, where_clause=None) -> int:
    statement = select(func.count()).select_from(model)
    if where_clause is not None:
        statement = statement.where(where_clause)
    return int(session.execute(statement).scalar_one())


def _uuid_or_none(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _csv_values(value: str | None) -> set[str]:
    if not value:
        return set()
    return {part.strip().lower() for part in value.split(",") if part.strip()}


def _database_host(database_url: str) -> str | None:
    if not database_url:
        return None
    parsed = urlparse(_pg_dump_database_url(database_url))
    return parsed.hostname


def _pg_dump_environment(database_url: str) -> dict[str, str]:
    parsed = urlparse(_pg_dump_database_url(database_url))
    if not parsed.hostname or not parsed.username or not parsed.path.lstrip("/"):
        raise RuntimeError("DATABASE_URL is not parseable for pg_dump environment variables.")

    pg_env = os.environ.copy()
    pg_env["PGHOST"] = parsed.hostname
    pg_env["PGUSER"] = unquote(parsed.username)
    pg_env["PGDATABASE"] = unquote(parsed.path.lstrip("/"))
    if parsed.port is not None:
        pg_env["PGPORT"] = str(parsed.port)
    if parsed.password is not None:
        pg_env["PGPASSWORD"] = unquote(parsed.password)
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() == "sslmode" and value:
            pg_env["PGSSLMODE"] = value
    return pg_env


def _pg_dump_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql+psycopg://"):
        return database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    return database_url


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

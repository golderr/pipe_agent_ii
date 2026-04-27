from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from tcg_pipeline.api.auth import AuthenticatedUser
from tcg_pipeline.api.schemas import ProjectRelationshipMutationResponse
from tcg_pipeline.db.evidence import serialize_json
from tcg_pipeline.db.models import (
    ChangeLog,
    ChangeType,
    Priority,
    Project,
    ProjectRelationship,
    RelationshipType,
)

_MISSING = object()


def add_project_relationship(
    session: Session,
    *,
    project_id: uuid.UUID,
    relationship_type: str,
    related_project_id: uuid.UUID,
    notes: str | None,
    user: AuthenticatedUser,
) -> ProjectRelationshipMutationResponse:
    project = _load_project(session, project_id)
    parsed_type = _coerce_relationship_type(relationship_type)
    related_project = _load_project(session, related_project_id)
    if project.id == related_project.id:
        raise HTTPException(status_code=422, detail="Cannot relate a project to itself.")

    normalized_notes = _clean_text(notes)
    existing = session.execute(
        select(ProjectRelationship).where(
            ProjectRelationship.project_id == project.id,
            ProjectRelationship.related_project_id == related_project.id,
            ProjectRelationship.relationship_type == parsed_type,
        )
    ).scalar_one_or_none()
    if existing is not None:
        now = datetime.now(UTC)
        actor = _actor_for_audit(user)
        updated = False
        change_log_entries_created = 0
        if normalized_notes is not None and normalized_notes != existing.notes:
            old_notes = existing.notes
            existing.notes = normalized_notes
            _mark_project_edited(project, actor=actor, timestamp=now)
            change_log_entries_created = _write_relationship_change_log(
                session,
                project=project,
                relationship=existing,
                related_project=related_project,
                actor=actor,
                timestamp=now,
                old_notes=old_notes,
            )
            updated = True
            session.flush()
        return ProjectRelationshipMutationResponse(
            project_id=project.id,
            relationship_id=existing.id,
            relationship_type=existing.relationship_type.value,
            related_project_id=existing.related_project_id,
            notes=existing.notes,
            created=False,
            updated=updated,
            change_log_entries_created=change_log_entries_created,
        )

    now = datetime.now(UTC)
    actor = _actor_for_audit(user)
    relationship = ProjectRelationship(
        project_id=project.id,
        related_project_id=related_project.id,
        relationship_type=parsed_type,
        notes=normalized_notes,
    )
    session.add(relationship)
    _mark_project_edited(project, actor=actor, timestamp=now)
    change_log_entries_created = _write_relationship_change_log(
        session,
        project=project,
        relationship=relationship,
        related_project=related_project,
        actor=actor,
        timestamp=now,
    )
    session.flush()
    return ProjectRelationshipMutationResponse(
        project_id=project.id,
        relationship_id=relationship.id,
        relationship_type=relationship.relationship_type.value,
        related_project_id=relationship.related_project_id,
        notes=relationship.notes,
        created=True,
        updated=False,
        change_log_entries_created=change_log_entries_created,
    )


def _load_project(session: Session, project_id: uuid.UUID) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    return project


def _coerce_relationship_type(value: str) -> RelationshipType:
    try:
        return RelationshipType(value.strip())
    except ValueError as exc:
        allowed = ", ".join(relationship_type.value for relationship_type in RelationshipType)
        raise HTTPException(
            status_code=422,
            detail=f"relationship_type must be one of: {allowed}.",
        ) from exc


def _actor_for_audit(user: AuthenticatedUser) -> str:
    return user.email or str(user.user_id)


def _mark_project_edited(project: Project, *, actor: str, timestamp: datetime) -> None:
    project.last_editor = actor[:50]
    project.last_edit_date = timestamp.date()


def _write_relationship_change_log(
    session: Session,
    *,
    project: Project,
    relationship: ProjectRelationship,
    related_project: Project,
    actor: str,
    timestamp: datetime,
    old_notes: str | None | object = _MISSING,
) -> int:
    session.add(
        ChangeLog(
            project_id=project.id,
            timestamp=timestamp,
            source="project_relationship",
            field="relationships",
            old_value=(
                None
                if old_notes is _MISSING
                else serialize_json(
                    _relationship_change_payload(
                        relationship,
                        related_project,
                        notes=old_notes,
                    )
                )
            ),
            new_value=serialize_json(_relationship_change_payload(relationship, related_project)),
            change_type=ChangeType.RESEARCHER_CONFIRMED,
            priority=Priority.LOW,
            reviewed_by=actor[:50],
        )
    )
    return 1


def _relationship_change_payload(
    relationship: ProjectRelationship,
    related_project: Project,
    *,
    notes: str | None | object = _MISSING,
) -> dict[str, str | None]:
    relationship_notes = relationship.notes if notes is _MISSING else notes
    return {
        "relationship_type": relationship.relationship_type.value,
        "related_project_id": str(related_project.id),
        "related_project_name": related_project.project_name
        or related_project.canonical_address,
        "notes": relationship_notes if isinstance(relationship_notes, str) else None,
    }


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None

"""create project notes table

Revision ID: 202604270012
Revises: 202604270011
Create Date: 2026-04-27 14:00:00.000000
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "202604270012"
down_revision = "202604270011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Supabase owns auth.users in the auth schema. Store auth user UUIDs without
    # cross-schema FKs; FastAPI validates actors before writes reach this table.
    op.create_table(
        "project_notes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("note_type", sa.String(length=50), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by_label", sa.String(length=120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_project_notes_project_id_type_created_at",
        "project_notes",
        ["project_id", "note_type", "created_at"],
        unique=False,
    )
    _backfill_project_notes()
    op.execute("GRANT SELECT ON TABLE project_notes TO authenticated")
    op.execute("ALTER TABLE project_notes ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY authenticated_read_project_notes
        ON project_notes
        FOR SELECT
        TO authenticated
        USING (true)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS authenticated_read_project_notes ON project_notes")
    op.execute("ALTER TABLE project_notes DISABLE ROW LEVEL SECURITY")
    op.execute("REVOKE SELECT ON TABLE project_notes FROM authenticated")
    op.drop_index("ix_project_notes_project_id_type_created_at", table_name="project_notes")
    op.drop_table("project_notes")


def _backfill_project_notes() -> None:
    bind = op.get_bind()
    projects = sa.table(
        "projects",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("researcher_notes", sa.Text()),
        sa.column("personal_notes", sa.Text()),
        sa.column("change_notes", sa.Text()),
    )
    project_notes = sa.table(
        "project_notes",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("project_id", postgresql.UUID(as_uuid=True)),
        sa.column("note_type", sa.String(length=50)),
        sa.column("body", sa.Text()),
        sa.column("created_by_label", sa.String(length=120)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    rows = bind.execute(
        sa.select(
            projects.c.id,
            projects.c.created_at,
            projects.c.researcher_notes,
            projects.c.personal_notes,
            projects.c.change_notes,
        ).where(
            sa.or_(
                projects.c.researcher_notes.is_not(None),
                projects.c.personal_notes.is_not(None),
                projects.c.change_notes.is_not(None),
            )
        )
    )
    for project_id, created_at, researcher_notes, personal_notes, change_notes in rows:
        for note_type, body in {
            "researcher_notes": researcher_notes,
            "personal_notes": personal_notes,
            "change_notes": change_notes,
        }.items():
            normalized_body = _coerce_text(body)
            if normalized_body is None:
                continue
            bind.execute(
                project_notes.insert().values(
                    id=uuid.uuid4(),
                    project_id=project_id,
                    note_type=note_type,
                    body=normalized_body,
                    created_by_label="legacy",
                    created_at=created_at,
                )
            )


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

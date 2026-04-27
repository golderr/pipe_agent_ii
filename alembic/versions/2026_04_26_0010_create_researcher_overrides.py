"""create researcher overrides table

Revision ID: 202604260010
Revises: 202604250009
Create Date: 2026-04-26 18:10:00.000000
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "202604260010"
down_revision = "202604250009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Supabase owns auth.users in the auth schema. Store auth user UUIDs here
    # without cross-schema FKs; FastAPI validates actors before write paths call
    # this table.
    op.create_table(
        "researcher_overrides",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field_name", sa.String(length=120), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("set_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("set_by_label", sa.String(length=120), nullable=True),
        sa.Column(
            "set_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("reaffirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleared_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("mode", sa.String(length=50), nullable=True),
        sa.Column("baseline", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        "ix_researcher_overrides_project_id_active",
        "researcher_overrides",
        ["project_id"],
        unique=False,
        postgresql_where=sa.text("cleared_at IS NULL"),
    )
    op.create_index(
        "uq_researcher_overrides_active_field",
        "researcher_overrides",
        ["project_id", "field_name"],
        unique=True,
        postgresql_where=sa.text("cleared_at IS NULL"),
    )
    _backfill_researcher_overrides()
    op.execute("GRANT SELECT ON TABLE researcher_overrides TO authenticated")
    op.execute("ALTER TABLE researcher_overrides ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY authenticated_read_researcher_overrides
        ON researcher_overrides
        FOR SELECT
        TO authenticated
        USING (true)
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS authenticated_read_researcher_overrides ON researcher_overrides"
    )
    op.execute("ALTER TABLE researcher_overrides DISABLE ROW LEVEL SECURITY")
    op.execute("REVOKE SELECT ON TABLE researcher_overrides FROM authenticated")
    op.drop_index("uq_researcher_overrides_active_field", table_name="researcher_overrides")
    op.drop_index("ix_researcher_overrides_project_id_active", table_name="researcher_overrides")
    op.drop_table("researcher_overrides")


def _backfill_researcher_overrides() -> None:
    bind = op.get_bind()
    projects = sa.table(
        "projects",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("researcher_override", postgresql.JSONB(astext_type=sa.Text())),
    )
    researcher_overrides = sa.table(
        "researcher_overrides",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("project_id", postgresql.UUID(as_uuid=True)),
        sa.column("field_name", sa.String(length=120)),
        sa.column("value", postgresql.JSONB(astext_type=sa.Text())),
        sa.column("set_by_label", sa.String(length=120)),
        sa.column("set_at", sa.DateTime(timezone=True)),
        sa.column("note", sa.Text()),
        sa.column("source_url", sa.Text()),
        sa.column("mode", sa.String(length=50)),
        sa.column("baseline", postgresql.JSONB(astext_type=sa.Text())),
    )
    rows = bind.execute(
        sa.select(projects.c.id, projects.c.researcher_override).where(
            projects.c.researcher_override.is_not(None)
        )
    )
    now = datetime.now(UTC)
    for project_id, raw_override in rows:
        if not isinstance(raw_override, Mapping):
            continue
        for field_name, payload in raw_override.items():
            normalized_field_name = str(field_name).strip()
            if not normalized_field_name:
                continue
            bind.execute(
                researcher_overrides.insert().values(
                    _backfill_entry(
                        project_id=project_id,
                        field_name=normalized_field_name,
                        payload=payload,
                        fallback_set_at=now,
                    )
                )
            )


def _backfill_entry(
    *,
    project_id: uuid.UUID,
    field_name: str,
    payload: Any,
    fallback_set_at: datetime,
) -> dict[str, Any]:
    if isinstance(payload, Mapping) and "value" in payload:
        baseline = payload.get("baseline")
        return {
            "id": uuid.uuid4(),
            "project_id": project_id,
            "field_name": field_name,
            "value": payload.get("value"),
            "set_by_label": _coerce_text(payload.get("set_by")),
            "set_at": _coerce_datetime(payload.get("set_at")) or fallback_set_at,
            "note": _coerce_text(payload.get("note")),
            "source_url": _coerce_text(payload.get("source_url")),
            "mode": _coerce_text(payload.get("mode")),
            "baseline": baseline if isinstance(baseline, Mapping) else None,
        }
    return {
        "id": uuid.uuid4(),
        "project_id": project_id,
        "field_name": field_name,
        "value": payload,
        "set_by_label": "legacy",
        "set_at": fallback_set_at,
        "note": None,
        "source_url": None,
        "mode": "sticky",
        "baseline": None,
    }


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

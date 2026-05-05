"""tighten agent run audit schema

Revision ID: 202605050030
Revises: 202605050029
Create Date: 2026-05-05 22:05:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "202605050030"
down_revision = "202605050029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE agent_runs SET evidence_consulted = '[]'::jsonb "
        "WHERE evidence_consulted IS NULL"
    )
    op.execute(
        "UPDATE agent_runs SET tool_calls_summary = '[]'::jsonb WHERE tool_calls_summary IS NULL"
    )
    op.execute(
        """
        UPDATE agent_runs
        SET completed_at = COALESCE(completed_at, started_at, created_at, now())
        WHERE completed_at IS NULL
        """
    )

    op.alter_column(
        "agent_runs",
        "evidence_consulted",
        existing_type=postgresql.JSONB(),
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    )
    op.alter_column(
        "agent_runs",
        "tool_calls_summary",
        existing_type=postgresql.JSONB(),
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    )
    op.alter_column(
        "agent_runs",
        "completed_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )
    op.create_check_constraint(
        op.f("ck_agent_runs_evidence_consulted_array"),
        "agent_runs",
        "jsonb_typeof(evidence_consulted) = 'array'",
    )
    op.create_check_constraint(
        op.f("ck_agent_runs_tool_calls_summary_array"),
        "agent_runs",
        "jsonb_typeof(tool_calls_summary) = 'array'",
    )
    op.execute(
        """
        COMMENT ON COLUMN agent_runs.intake_record_id IS
        'Source-specific intake identifier. For news, this is the stringified news_articles.id.'
        """
    )


def downgrade() -> None:
    op.execute("COMMENT ON COLUMN agent_runs.intake_record_id IS NULL")
    op.drop_constraint(
        op.f("ck_agent_runs_tool_calls_summary_array"),
        "agent_runs",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_agent_runs_evidence_consulted_array"),
        "agent_runs",
        type_="check",
    )
    op.alter_column(
        "agent_runs",
        "completed_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )
    op.alter_column(
        "agent_runs",
        "tool_calls_summary",
        existing_type=postgresql.JSONB(),
        nullable=True,
        server_default=None,
    )
    op.alter_column(
        "agent_runs",
        "evidence_consulted",
        existing_type=postgresql.JSONB(),
        nullable=True,
        server_default=None,
    )

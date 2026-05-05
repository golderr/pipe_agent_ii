"""add agent run audit tables

Revision ID: 202605050029
Revises: 202605040028
Create Date: 2026-05-05 21:10:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "202605050029"
down_revision = "202605040028"
branch_labels = None
depends_on = None


AGENT_RUN_OUTCOMES = (
    "completed",
    "escalated",
    "failed_timeout",
    "failed_budget",
    "failed_error",
    "killed_by_switch",
)


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("intake_source_type", sa.Text(), nullable=False),
        sa.Column("intake_record_id", sa.Text(), nullable=False),
        sa.Column("intake_extraction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("scrape_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("profile_name", sa.Text(), nullable=False),
        sa.Column("profile_version", sa.Text(), nullable=False),
        sa.Column("triggered_by", postgresql.JSONB(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("input_tokens_uncached", sa.Integer(), nullable=False),
        sa.Column("input_tokens_cache_creation", sa.Integer(), nullable=False),
        sa.Column("input_tokens_cached", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("reasoning_trace", sa.Text(), nullable=True),
        sa.Column("evidence_consulted", postgresql.JSONB(), nullable=True),
        sa.Column("tool_calls_summary", postgresql.JSONB(), nullable=True),
        sa.Column("matcher_original_verdict", postgresql.JSONB(), nullable=True),
        sa.Column("agent_revised_verdict", postgresql.JSONB(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column("budget_consumed_usd", sa.Numeric(10, 6), nullable=False),
        sa.Column("tool_calls_count", sa.Integer(), nullable=False),
        sa.Column("wallclock_seconds", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(triggered_by) = 'array' AND jsonb_array_length(triggered_by) > 0",
            name="triggered_by_nonempty_array",
        ),
        sa.CheckConstraint(
            "outcome IN ('" + "', '".join(AGENT_RUN_OUTCOMES) + "')",
            name="outcome",
        ),
        sa.CheckConstraint(
            "input_tokens_uncached >= 0 "
            "AND input_tokens_cache_creation >= 0 "
            "AND input_tokens_cached >= 0 "
            "AND output_tokens >= 0 "
            "AND cost_usd >= 0 "
            "AND latency_ms >= 0 "
            "AND budget_consumed_usd >= 0 "
            "AND tool_calls_count >= 0 "
            "AND wallclock_seconds >= 0",
            name="nonnegative_counters",
        ),
        sa.CheckConstraint(
            "(outcome LIKE 'failed_%' AND error_text IS NOT NULL) "
            "OR (outcome NOT LIKE 'failed_%')",
            name="failed_outcome_error_text",
        ),
        sa.ForeignKeyConstraint(
            ["intake_extraction_id"],
            ["news_extractions.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_run_id"], ["source_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["scrape_job_id"], ["scrape_jobs.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_agent_runs_intake",
        "agent_runs",
        ["intake_source_type", "intake_record_id"],
    )
    op.create_index(
        "ix_agent_runs_project",
        "agent_runs",
        ["project_id"],
        postgresql_where=sa.text("project_id IS NOT NULL"),
    )
    op.create_index(
        "ix_agent_runs_profile_outcome",
        "agent_runs",
        ["profile_name", "outcome", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_agent_runs_source_run",
        "agent_runs",
        ["source_run_id"],
        postgresql_where=sa.text("source_run_id IS NOT NULL"),
    )
    op.create_index(
        "ix_agent_runs_created_at",
        "agent_runs",
        [sa.text("created_at DESC")],
    )

    op.create_table(
        "agent_run_review_items",
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["review_item_id"], ["review_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("agent_run_id", "review_item_id"),
    )
    op.create_index(
        "ix_agent_run_review_items_review_item",
        "agent_run_review_items",
        ["review_item_id"],
    )

    for table_name in ("agent_runs", "agent_run_review_items"):
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table_name in ("agent_run_review_items", "agent_runs"):
        op.execute(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY")

    op.drop_table("agent_run_review_items")
    op.drop_table("agent_runs")

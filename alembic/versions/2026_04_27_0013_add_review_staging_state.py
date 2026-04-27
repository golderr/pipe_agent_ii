"""add review staging state

Revision ID: 202604270013
Revises: 202604270012
Create Date: 2026-04-27 16:30:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "202604270013"
down_revision = "202604270012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "review_items",
        sa.Column(
            "state",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'open'"),
        ),
    )
    op.execute(
        """
        UPDATE review_items
        SET state = CASE
            WHEN status = 'deferred' THEN 'staged'
            WHEN status IN ('accepted', 'rejected', 'auto_accepted') THEN 'committed'
            ELSE 'open'
        END
        """
    )
    op.create_check_constraint(
        op.f("ck_review_items_state"),
        "review_items",
        "state IN ('open', 'staged', 'committed', 'invalidated')",
    )
    op.create_index("ix_review_items_state_priority", "review_items", ["state", "priority"])
    op.create_index("ix_review_items_project_id_state", "review_items", ["project_id", "state"])

    op.add_column(
        "review_decisions",
        sa.Column(
            "state",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'staged'"),
        ),
    )
    op.add_column(
        "review_decisions",
        sa.Column("decision_type", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "review_decisions",
        sa.Column("staged_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "review_decisions",
        sa.Column("staged_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("review_decisions", sa.Column("staged_by_email", sa.Text(), nullable=True))
    op.add_column(
        "review_decisions",
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "review_decisions",
        sa.Column("committed_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("review_decisions", sa.Column("committed_by_email", sa.Text(), nullable=True))
    op.add_column(
        "review_decisions",
        sa.Column("decision_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("review_decisions", sa.Column("decision_notes", sa.Text(), nullable=True))
    op.add_column("review_decisions", sa.Column("source_url", sa.Text(), nullable=True))

    op.execute(
        """
        UPDATE review_decisions AS decision
        SET
            state = CASE
                WHEN item.status = 'deferred' OR decision.action = 'defer' THEN 'staged'
                ELSE 'committed'
            END,
            decision_type = CASE decision.action
                WHEN 'accept' THEN 'accept_new'
                WHEN 'reject' THEN 'keep_old'
                WHEN 'override' THEN 'custom'
                WHEN 'defer' THEN 'defer'
                ELSE decision.action::text
            END,
            staged_at = COALESCE(decision.created_at, now()),
            staged_by_email = decision.actor,
            committed_at = CASE
                WHEN item.status = 'deferred' OR decision.action = 'defer' THEN NULL
                ELSE COALESCE(decision.created_at, now())
            END,
            committed_by_email = CASE
                WHEN item.status = 'deferred' OR decision.action = 'defer' THEN NULL
                ELSE decision.actor
            END,
            decision_notes = decision.notes,
            decision_value = decision.field_overrides
        FROM review_items AS item
        WHERE item.id = decision.review_item_id
        """
    )
    op.create_check_constraint(
        op.f("ck_review_decisions_state"),
        "review_decisions",
        "state IN ('staged', 'committed')",
    )
    op.create_index(
        "ix_review_decisions_state_staged_by",
        "review_decisions",
        ["state", "staged_by"],
        postgresql_where=sa.text("state = 'staged'"),
    )
    op.create_index(
        "uq_review_decisions_one_staged_per_item",
        "review_decisions",
        ["review_item_id"],
        unique=True,
        postgresql_where=sa.text("state = 'staged'"),
    )

    op.add_column(
        "change_log",
        sa.Column("reviewed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("change_log", sa.Column("reviewed_by_email", sa.Text(), nullable=True))
    op.execute(
        """
        UPDATE change_log
        SET reviewed_by_email = reviewed_by
        WHERE reviewed_by IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("change_log", "reviewed_by_email")
    op.drop_column("change_log", "reviewed_by_user_id")

    op.drop_index("uq_review_decisions_one_staged_per_item", table_name="review_decisions")
    op.drop_index("ix_review_decisions_state_staged_by", table_name="review_decisions")
    op.drop_constraint(op.f("ck_review_decisions_state"), "review_decisions", type_="check")
    op.drop_column("review_decisions", "source_url")
    op.drop_column("review_decisions", "decision_notes")
    op.drop_column("review_decisions", "decision_value")
    op.drop_column("review_decisions", "committed_by_email")
    op.drop_column("review_decisions", "committed_by")
    op.drop_column("review_decisions", "committed_at")
    op.drop_column("review_decisions", "staged_by_email")
    op.drop_column("review_decisions", "staged_by")
    op.drop_column("review_decisions", "staged_at")
    op.drop_column("review_decisions", "decision_type")
    op.drop_column("review_decisions", "state")

    op.drop_index("ix_review_items_project_id_state", table_name="review_items")
    op.drop_index("ix_review_items_state_priority", table_name="review_items")
    op.drop_constraint(op.f("ck_review_items_state"), "review_items", type_="check")
    op.drop_column("review_items", "state")

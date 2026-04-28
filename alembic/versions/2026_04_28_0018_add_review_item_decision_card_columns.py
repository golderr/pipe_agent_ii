"""add review item decision-card columns

Revision ID: 202604280018
Revises: 202604280017
Create Date: 2026-04-28 21:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "202604280018"
down_revision = "202604280017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("review_items", sa.Column("field_name", sa.String(length=120), nullable=True))
    op.add_column(
        "review_items",
        sa.Column("winning_evidence_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "review_items",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_foreign_key(
        op.f("fk_review_items_winning_evidence_id_evidence"),
        "review_items",
        "evidence",
        ["winning_evidence_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        """
        UPDATE review_items
        SET field_name = COALESCE(
            NULLIF(BTRIM(payload->>'field_name'), ''),
            CASE
                WHEN payload ? 'status_suggestion'
                     AND payload->'status_suggestion' <> 'null'::jsonb
                THEN 'pipeline_status'
                ELSE NULL
            END,
            NULLIF(BTRIM(payload #>> '{changes,0,field}'), ''),
            NULLIF(BTRIM(payload #>> '{changes,0,field_name}'), '')
        )
        WHERE item_type IN ('status_change', 'override_contradiction')
          AND field_name IS NULL
        """
    )
    op.execute(
        """
        UPDATE review_items
        SET winning_evidence_id = NULLIF(
            COALESCE(
                payload #>> '{candidate,evidence_ids,0}',
                payload #>> '{evidence_ids,0}'
            ),
            ''
        )::uuid
        WHERE item_type = 'override_contradiction'
          AND winning_evidence_id IS NULL
          AND COALESCE(
                payload #>> '{candidate,evidence_ids,0}',
                payload #>> '{evidence_ids,0}'
              ) ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        """
    )
    op.create_index(
        "ix_review_items_project_field_state",
        "review_items",
        ["project_id", "field_name", "state"],
    )
    _assert_no_active_decision_card_duplicates()
    op.create_index(
        "uq_review_items_active_project_field_type",
        "review_items",
        ["project_id", "field_name", "item_type"],
        unique=True,
        postgresql_where=sa.text(
            "state IN ('open', 'staged') AND field_name IS NOT NULL AND project_id IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_review_items_active_project_field_type", table_name="review_items")
    op.drop_index("ix_review_items_project_field_state", table_name="review_items")
    op.drop_constraint(
        op.f("fk_review_items_winning_evidence_id_evidence"),
        "review_items",
        type_="foreignkey",
    )
    op.drop_column("review_items", "updated_at")
    op.drop_column("review_items", "winning_evidence_id")
    op.drop_column("review_items", "field_name")


def _assert_no_active_decision_card_duplicates() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM review_items
                WHERE state IN ('open', 'staged')
                  AND field_name IS NOT NULL
                  AND project_id IS NOT NULL
                  AND item_type IN ('status_change', 'override_contradiction')
                GROUP BY project_id, field_name, item_type
                HAVING COUNT(*) > 1
                LIMIT 1
            ) THEN
                RAISE EXCEPTION
                    'Active duplicate review decision cards exist. '
                    'Run scripts/collapse_duplicate_review_items.py --apply '
                    'before applying 202604280018.';
            END IF;
        END $$;
        """
    )

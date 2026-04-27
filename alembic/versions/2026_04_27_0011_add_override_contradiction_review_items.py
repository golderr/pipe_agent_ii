"""add override contradiction review items

Revision ID: 202604270011
Revises: 202604260010
Create Date: 2026-04-27 10:30:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "202604270011"
down_revision = "202604260010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE review_item_type_enum ADD VALUE IF NOT EXISTS 'override_contradiction'"
    )
    op.add_column(
        "review_items",
        sa.Column(
            "contradicted_override_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "review_items",
        sa.Column("contradiction_priority", sa.String(length=20), nullable=True),
    )
    op.create_foreign_key(
        "fk_review_items_contradicted_override_id_researcher_overrides",
        "review_items",
        "researcher_overrides",
        ["contradicted_override_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_review_items_contradicted_override_id_researcher_overrides",
        "review_items",
        type_="foreignkey",
    )
    op.drop_column("review_items", "contradiction_priority")
    op.drop_column("review_items", "contradicted_override_id")

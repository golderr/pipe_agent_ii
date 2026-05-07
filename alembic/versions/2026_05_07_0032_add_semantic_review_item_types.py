"""add semantic review item types

Revision ID: 202605070032
Revises: 202605060031
Create Date: 2026-05-07 12:00:00.000000
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "202605070032"
down_revision = "202605060031"
branch_labels = None
depends_on = None


SEMANTIC_REVIEW_ITEM_TYPES = (
    "news_status_uncorroborated",
    "multi_tenure_review",
    "project_cancellation_review",
)


def upgrade() -> None:
    for item_type in SEMANTIC_REVIEW_ITEM_TYPES:
        op.execute(f"ALTER TYPE review_item_type_enum ADD VALUE IF NOT EXISTS '{item_type}'")


def downgrade() -> None:
    # PostgreSQL enum values cannot be removed safely without rebuilding the type.
    pass

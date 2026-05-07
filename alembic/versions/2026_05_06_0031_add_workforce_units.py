"""add workforce units bucket

Revision ID: 202605060031
Revises: 202605050030
Create Date: 2026-05-06 18:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "202605060031"
down_revision = "202605050030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("workforce_units", sa.Integer(), nullable=True))
    op.add_column(
        "news_project_references",
        sa.Column("candidate_unit_workforce", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("news_project_references", "candidate_unit_workforce")
    op.drop_column("projects", "workforce_units")

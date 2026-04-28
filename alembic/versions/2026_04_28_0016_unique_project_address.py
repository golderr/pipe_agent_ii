"""enforce unique project addresses per market

Revision ID: 202604280016
Revises: 202604270015
Create Date: 2026-04-28 17:30:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "202604280016"
down_revision = "202604270015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM projects
            WHERE market_id IS NOT NULL
            GROUP BY market_id, canonical_address
            HAVING COUNT(*) > 1
          ) THEN
            RAISE EXCEPTION
              'Duplicate project addresses exist; cannot create unique project address index.';
          END IF;
        END $$;
        """
    )
    op.create_index(
        "uq_projects_market_id_canonical_address",
        "projects",
        ["market_id", "canonical_address"],
        unique=True,
        postgresql_where=sa.text("market_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_projects_market_id_canonical_address", table_name="projects")

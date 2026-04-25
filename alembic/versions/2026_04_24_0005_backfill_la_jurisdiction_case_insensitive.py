"""backfill la jurisdiction case insensitive

Revision ID: 202604240005
Revises: 202604240004
Create Date: 2026-04-24 19:18:00.000000
"""

from __future__ import annotations

import uuid

from alembic import op

# revision identifiers, used by Alembic.
revision = "202604240005"
down_revision = "202604240004"
branch_labels = None
depends_on = None


LA_JURISDICTION_ID = uuid.UUID("8dcd02af-0dfa-551d-948a-30c9f19add55")


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE projects
        SET jurisdiction_id = '{LA_JURISDICTION_ID}'::uuid
        WHERE jurisdiction_id IS NULL
          AND market = 'los_angeles'
          AND (
            lower(city) = 'los angeles'
            OR jurisdiction IN ('city_of_los_angeles', 'City of Los Angeles', 'Los Angeles')
          )
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE projects
        SET jurisdiction_id = NULL
        WHERE jurisdiction_id = '{LA_JURISDICTION_ID}'::uuid
          AND market = 'los_angeles'
          AND (
            lower(city) = 'los angeles'
            OR jurisdiction IN ('city_of_los_angeles', 'City of Los Angeles', 'Los Angeles')
          )
        """
    )

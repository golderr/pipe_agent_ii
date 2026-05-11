"""seed permits cost cap

Revision ID: 202605100036
Revises: 202605080035
Create Date: 2026-05-10 21:00:00.000000
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "202605100036"
down_revision = "202605080035"
branch_labels = None
depends_on = None

PERMITS_CAP_NOTE = "Default AGENT.3 permits cap."


def upgrade() -> None:
    op.execute(
        f"""
        INSERT INTO cost_caps (
            bucket,
            effective_from,
            effective_to,
            daily_warn_usd,
            daily_hard_usd,
            notes
        )
        VALUES (
            'permits',
            DATE '2026-05-10',
            NULL,
            50.00,
            75.00,
            '{PERMITS_CAP_NOTE}'
        )
        ON CONFLICT (bucket, effective_from) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DELETE FROM cost_caps
        WHERE bucket = 'permits'
          AND effective_from = DATE '2026-05-10'
          AND notes = '{PERMITS_CAP_NOTE}'
        """
    )

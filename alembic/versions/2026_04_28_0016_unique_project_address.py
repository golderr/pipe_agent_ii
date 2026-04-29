"""mark manual project create address locking

Revision ID: 202604280016
Revises: 202604270015
Create Date: 2026-04-28 17:30:00.000000
"""

# revision identifiers, used by Alembic.
revision = "202604280016"
down_revision = "202604270015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No schema object is needed. Manual project creation now uses
    # pg_advisory_xact_lock(market_id, canonical_address) to serialize
    # same-address create attempts while still permitting legitimate multi-phase
    # projects at one address.
    pass


def downgrade() -> None:
    pass

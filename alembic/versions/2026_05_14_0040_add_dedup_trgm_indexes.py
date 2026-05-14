"""add dedup candidate trigram indexes

Revision ID: 202605140040
Revises: 202605130039
Create Date: 2026-05-14 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "202605140040"
down_revision = "202605130039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_projects_canonical_address_trgm
        ON projects USING GIN (canonical_address gin_trgm_ops)
        WHERE canonical_address IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_projects_project_name_trgm
        ON projects USING GIN (project_name gin_trgm_ops)
        WHERE project_name IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_projects_location_gist
        ON projects USING GIST (location)
        WHERE location IS NOT NULL
        """
    )


def downgrade() -> None:
    # ix_projects_location_gist is owned by the AGENT.1 foundation migration.
    # This migration only verifies/repairs it idempotently for dedup retrieval.
    op.execute("DROP INDEX IF EXISTS ix_projects_project_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_projects_canonical_address_trgm")

"""create project latest evidence view

Revision ID: 202604250008
Revises: 202604240007
Create Date: 2026-04-25 10:45:00.000000
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "202604250008"
down_revision = "202604240007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_evidence_project_latest
        ON evidence (
            project_id,
            evidence_date DESC NULLS LAST,
            collected_at DESC,
            id DESC
        )
        WHERE project_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE OR REPLACE VIEW project_latest_evidence
        WITH (security_invoker = true) AS
        SELECT DISTINCT ON (project_id)
            project_id,
            id AS evidence_id,
            source_type,
            collected_at,
            evidence_date,
            extracted_fields,
            notes
        FROM evidence
        WHERE project_id IS NOT NULL
        ORDER BY
            project_id,
            evidence_date DESC NULLS LAST,
            collected_at DESC,
            id DESC
        """
    )
    op.execute("GRANT SELECT ON TABLE project_latest_evidence TO authenticated")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON TABLE project_latest_evidence FROM authenticated")
    op.execute("DROP VIEW IF EXISTS project_latest_evidence")
    op.execute("DROP INDEX IF EXISTS ix_evidence_project_latest")

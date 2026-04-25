"""create project field resolution view

Revision ID: 202604250009
Revises: 202604250008
Create Date: 2026-04-25 11:05:00.000000
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "202604250009"
down_revision = "202604250008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_resolution_log_project_field_latest
        ON resolution_log (
            project_id,
            field,
            created_at DESC,
            id DESC
        )
        """
    )
    op.execute(
        """
        CREATE OR REPLACE VIEW project_field_resolution
        WITH (security_invoker = true) AS
        SELECT DISTINCT ON (project_id, field)
            project_id,
            field,
            current_value,
            resolved_value,
            evidence_ids,
            rule_applied,
            confidence,
            created_at
        FROM resolution_log
        ORDER BY project_id, field, created_at DESC, id DESC
        """
    )
    op.execute("GRANT SELECT ON TABLE project_field_resolution TO authenticated")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON TABLE project_field_resolution FROM authenticated")
    op.execute("DROP VIEW IF EXISTS project_field_resolution")
    op.execute("DROP INDEX IF EXISTS ix_resolution_log_project_field_latest")

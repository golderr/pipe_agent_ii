"""add authenticated read rls

Revision ID: 202604240006
Revises: 202604240005
Create Date: 2026-04-24 20:05:00.000000
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "202604240006"
down_revision = "202604240005"
branch_labels = None
depends_on = None


READ_TABLES = (
    "markets",
    "jurisdictions",
    "source_registrations",
    "source_runs",
    "projects",
    "project_identifiers",
    "project_relationships",
    "project_source_records",
    "status_history",
    "evidence",
    "resolution_log",
    "change_log",
    "developer_registry",
    "developer_alias",
)


def upgrade() -> None:
    op.execute("GRANT USAGE ON SCHEMA public TO authenticated")
    for table_name in READ_TABLES:
        policy_name = f"authenticated_read_{table_name}"
        op.execute(f"GRANT SELECT ON TABLE {table_name} TO authenticated")
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {policy_name}
            ON {table_name}
            FOR SELECT
            TO authenticated
            USING (true)
            """
        )


def downgrade() -> None:
    for table_name in reversed(READ_TABLES):
        policy_name = f"authenticated_read_{table_name}"
        op.execute(f"DROP POLICY IF EXISTS {policy_name} ON {table_name}")
        op.execute(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE SELECT ON TABLE {table_name} FROM authenticated")

"""add review items read rls

Revision ID: 202604240007
Revises: 202604240006
Create Date: 2026-04-24 21:25:00.000000
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "202604240007"
down_revision = "202604240006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT SELECT ON TABLE review_items TO authenticated")
    op.execute("ALTER TABLE review_items ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY authenticated_read_review_items
        ON review_items
        FOR SELECT
        TO authenticated
        USING (true)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS authenticated_read_review_items ON review_items")
    op.execute("ALTER TABLE review_items DISABLE ROW LEVEL SECURITY")
    op.execute("REVOKE SELECT ON TABLE review_items FROM authenticated")

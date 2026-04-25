"""expand source runs for coverage

Revision ID: 202604240004
Revises: 202604240003
Create Date: 2026-04-24 19:08:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "202604240004"
down_revision = "202604240003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("source_runs", sa.Column("jurisdiction_id", sa.UUID(), nullable=True))
    op.add_column(
        "source_runs",
        sa.Column(
            "trigger_type",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'scheduled'"),
        ),
    )
    op.add_column("source_runs", sa.Column("initiated_by_user_id", sa.UUID(), nullable=True))
    op.add_column(
        "source_runs",
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("source_runs", sa.Column("rows_inserted", sa.Integer(), nullable=True))
    op.add_column("source_runs", sa.Column("rows_updated", sa.Integer(), nullable=True))
    op.add_column("source_runs", sa.Column("rows_unchanged", sa.Integer(), nullable=True))
    op.add_column("source_runs", sa.Column("error_text", sa.Text(), nullable=True))

    op.create_foreign_key(
        "fk_source_runs_jurisdiction_id_jurisdictions",
        "source_runs",
        "jurisdictions",
        ["jurisdiction_id"],
        ["id"],
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF to_regclass('auth.users') IS NOT NULL THEN
            ALTER TABLE source_runs
              ADD CONSTRAINT fk_source_runs_initiated_by_user_id_auth_users
              FOREIGN KEY (initiated_by_user_id)
              REFERENCES auth.users(id)
              ON DELETE SET NULL;
          END IF;
        END $$;
        """
    )
    op.create_index(
        "ix_source_runs_jurisdiction_id_source_name",
        "source_runs",
        ["jurisdiction_id", "source_name"],
    )
    op.create_index("ix_source_runs_finished_at", "source_runs", ["finished_at"])


def downgrade() -> None:
    op.drop_index("ix_source_runs_finished_at", table_name="source_runs")
    op.drop_index("ix_source_runs_jurisdiction_id_source_name", table_name="source_runs")
    op.execute(
        """
        ALTER TABLE source_runs
        DROP CONSTRAINT IF EXISTS fk_source_runs_initiated_by_user_id_auth_users
        """
    )
    op.drop_constraint(
        "fk_source_runs_jurisdiction_id_jurisdictions",
        "source_runs",
        type_="foreignkey",
    )
    op.drop_column("source_runs", "error_text")
    op.drop_column("source_runs", "rows_unchanged")
    op.drop_column("source_runs", "rows_updated")
    op.drop_column("source_runs", "rows_inserted")
    op.drop_column("source_runs", "finished_at")
    op.drop_column("source_runs", "initiated_by_user_id")
    op.drop_column("source_runs", "trigger_type")
    op.drop_column("source_runs", "jurisdiction_id")

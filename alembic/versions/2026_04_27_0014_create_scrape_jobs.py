"""create scrape jobs and costar uploads

Revision ID: 202604270014
Revises: 202604270013
Create Date: 2026-04-27 19:15:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "202604270014"
down_revision = "202604270013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    scrape_job_status_enum = postgresql.ENUM(
        "queued",
        "running",
        "completed",
        "failed",
        "cancelled",
        name="scrape_job_status_enum",
    )
    scrape_trigger_type_enum = postgresql.ENUM(
        "user_initiated",
        "scheduled",
        name="scrape_trigger_type_enum",
    )
    costar_upload_status_enum = postgresql.ENUM(
        "processing",
        "completed",
        "failed",
        name="costar_upload_status_enum",
    )
    bind = op.get_bind()
    scrape_job_status_enum.create(bind, checkfirst=True)
    scrape_trigger_type_enum.create(bind, checkfirst=True)
    costar_upload_status_enum.create(bind, checkfirst=True)

    op.create_table(
        "scrape_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("jurisdiction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_name", sa.Text(), nullable=False),
        sa.Column(
            "trigger_type",
            postgresql.ENUM(name="scrape_trigger_type_enum", create_type=False),
            nullable=False,
        ),
        sa.Column("initiated_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("initiated_by_email", sa.Text(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(name="scrape_job_status_enum", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "queued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column("progress", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["jurisdiction_id"], ["jurisdictions.id"]),
        sa.ForeignKeyConstraint(["source_run_id"], ["source_runs.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_scrape_jobs_jurisdiction_id_status",
        "scrape_jobs",
        ["jurisdiction_id", "status"],
    )
    op.create_index(
        "ix_scrape_jobs_status_queued_at",
        "scrape_jobs",
        ["status", "queued_at"],
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )

    op.create_table(
        "costar_uploads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("jurisdiction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("uploaded_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("uploaded_by_email", sa.Text(), nullable=True),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("file_name", sa.Text(), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("source_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(name="costar_upload_status_enum", create_type=False),
            nullable=False,
        ),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["jurisdiction_id"], ["jurisdictions.id"]),
        sa.ForeignKeyConstraint(["source_run_id"], ["source_runs.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_costar_uploads_jurisdiction_id",
        "costar_uploads",
        ["jurisdiction_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_costar_uploads_jurisdiction_id", table_name="costar_uploads")
    op.drop_table("costar_uploads")
    op.drop_index("ix_scrape_jobs_status_queued_at", table_name="scrape_jobs")
    op.drop_index("ix_scrape_jobs_jurisdiction_id_status", table_name="scrape_jobs")
    op.drop_table("scrape_jobs")

    bind = op.get_bind()
    postgresql.ENUM(name="costar_upload_status_enum").drop(bind, checkfirst=True)
    postgresql.ENUM(name="scrape_trigger_type_enum").drop(bind, checkfirst=True)
    postgresql.ENUM(name="scrape_job_status_enum").drop(bind, checkfirst=True)

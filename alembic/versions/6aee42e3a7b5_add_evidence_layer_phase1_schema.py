"""add evidence layer phase 1 schema

Revision ID: 6aee42e3a7b5
Revises: 1608e6422a65
Create Date: 2026-04-20 15:10:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "6aee42e3a7b5"
down_revision = "1608e6422a65"
branch_labels = None
depends_on = None


status_confidence_enum = sa.Enum(
    "high",
    "medium",
    "low",
    name="status_confidence_enum",
    create_type=False,
)


def upgrade() -> None:
    op.create_table(
        "developer_registry",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("canonical_name", sa.String(length=255), nullable=False),
        sa.Column(
            "is_top_tier",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canonical_name"),
    )

    op.create_table(
        "developer_alias",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("developer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alias_name", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["developer_id"],
            ["developer_registry.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("alias_name"),
    )
    op.create_index(
        "ix_developer_alias_developer_id",
        "developer_alias",
        ["developer_id"],
        unique=False,
    )

    op.create_table(
        "evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_type", sa.String(length=120), nullable=False),
        sa.Column("source_tier", sa.Integer(), nullable=False),
        sa.Column("ingest_method", sa.String(length=30), nullable=False),
        sa.Column("source_record_id", sa.String(length=255), nullable=True),
        sa.Column(
            "collected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("evidence_date", sa.Date(), nullable=True),
        sa.Column("raw_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("raw_data_hash", sa.String(length=64), nullable=True),
        sa.Column("extracted_fields", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("signal_flags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evidence_collected_at", "evidence", ["collected_at"], unique=False)
    op.create_index("ix_evidence_evidence_date", "evidence", ["evidence_date"], unique=False)
    op.create_index("ix_evidence_project_id", "evidence", ["project_id"], unique=False)
    op.create_index("ix_evidence_source_type", "evidence", ["source_type"], unique=False)
    op.create_index(
        "uq_evidence_source_type_source_record_id_raw_data_hash",
        "evidence",
        ["source_type", "source_record_id", "raw_data_hash"],
        unique=True,
        postgresql_where=sa.text("source_record_id IS NOT NULL AND raw_data_hash IS NOT NULL"),
    )

    op.create_table(
        "resolution_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field", sa.String(length=120), nullable=False),
        sa.Column("current_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("resolved_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("evidence_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=True),
        sa.Column("rule_applied", sa.String(length=120), nullable=True),
        sa.Column("confidence", status_confidence_enum, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_resolution_log_project_id_created_at",
        "resolution_log",
        ["project_id", "created_at"],
        unique=False,
    )

    op.add_column(
        "projects",
        sa.Column(
            "confidence",
            status_confidence_enum,
            nullable=False,
            server_default=sa.text("'low'"),
        ),
    )
    op.add_column(
        "projects",
        sa.Column(
            "confidence_reason",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column("projects", sa.Column("likelihood", sa.Float(), nullable=True))
    op.add_column(
        "projects",
        sa.Column(
            "likelihood_breakdown",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "projects",
        sa.Column("delivery_year_provenance", sa.String(length=30), nullable=True),
    )
    op.add_column("projects", sa.Column("last_evidence_date", sa.Date(), nullable=True))
    op.add_column(
        "projects",
        sa.Column(
            "inclusion_in_analysis",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "projects",
        sa.Column(
            "inclusion_in_exhibit",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column("projects", sa.Column("inclusion_note", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "inclusion_note")
    op.drop_column("projects", "inclusion_in_exhibit")
    op.drop_column("projects", "inclusion_in_analysis")
    op.drop_column("projects", "last_evidence_date")
    op.drop_column("projects", "delivery_year_provenance")
    op.drop_column("projects", "likelihood_breakdown")
    op.drop_column("projects", "likelihood")
    op.drop_column("projects", "confidence_reason")
    op.drop_column("projects", "confidence")

    op.drop_index(
        "ix_resolution_log_project_id_created_at",
        table_name="resolution_log",
    )
    op.drop_table("resolution_log")

    op.drop_index("ix_evidence_source_type", table_name="evidence")
    op.drop_index("ix_evidence_project_id", table_name="evidence")
    op.drop_index("ix_evidence_evidence_date", table_name="evidence")
    op.drop_index("ix_evidence_collected_at", table_name="evidence")
    op.drop_index(
        "uq_evidence_source_type_source_record_id_raw_data_hash",
        table_name="evidence",
    )
    op.drop_table("evidence")

    op.drop_index("ix_developer_alias_developer_id", table_name="developer_alias")
    op.drop_table("developer_alias")
    op.drop_table("developer_registry")

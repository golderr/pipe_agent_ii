"""create source registrations

Revision ID: 202604240003
Revises: 202604240002
Create Date: 2026-04-24 19:07:00.000000
"""

from __future__ import annotations

import json
import uuid

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "202604240003"
down_revision = "202604240002"
branch_labels = None
depends_on = None


LA_JURISDICTION_ID = uuid.UUID("8dcd02af-0dfa-551d-948a-30c9f19add55")


def _source_config(
    *,
    collector: str,
    schedule: str,
    role: str,
    endpoint: str | None = None,
    adapter: str | None = None,
    coverage_scope: str | None = None,
    create_new_candidates: bool | None = None,
    mode: str | None = None,
) -> dict[str, object]:
    config: dict[str, object] = {
        "collector": collector,
        "schedule": schedule,
        "role": role,
    }
    if endpoint is not None:
        config["endpoint"] = endpoint
    if adapter is not None:
        config["adapter"] = adapter
    if coverage_scope is not None:
        config["coverage_scope"] = coverage_scope
    if create_new_candidates is not None:
        config["create_new_candidates"] = create_new_candidates
    if mode is not None:
        config["mode"] = mode
    return config


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _insert_registration(
    *,
    registration_id: uuid.UUID,
    source_name: str,
    config: dict[str, object],
) -> None:
    op.execute(
        f"""
        INSERT INTO source_registrations (
          id,
          jurisdiction_id,
          source_name,
          source_class,
          active,
          schedule_cron,
          config
        )
        VALUES (
          '{registration_id}'::uuid,
          '{LA_JURISDICTION_ID}'::uuid,
          {_sql_literal(source_name)},
          'gov',
          true,
          NULL,
          {_sql_literal(json.dumps(config, sort_keys=True))}::jsonb
        )
        """
    )


def upgrade() -> None:
    op.create_table(
        "source_registrations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("jurisdiction_id", sa.UUID(), nullable=False),
        sa.Column("source_name", sa.Text(), nullable=False),
        sa.Column("source_class", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("schedule_cron", sa.Text(), nullable=True),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        sa.ForeignKeyConstraint(["jurisdiction_id"], ["jurisdictions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("jurisdiction_id", "source_name"),
    )
    op.create_index(
        "ix_source_registrations_jurisdiction_id",
        "source_registrations",
        ["jurisdiction_id"],
    )

    _insert_registration(
        registration_id=uuid.uuid5(uuid.NAMESPACE_DNS, "tcg-pipeline.source.ladbs_permits"),
        source_name="ladbs_permits",
        config=_source_config(
            collector="socrata",
            adapter="ladbs_permits_pi9x_tg5x",
            endpoint="https://data.lacity.org/resource/pi9x-tg5x.json",
            coverage_scope="city",
            schedule="weekly",
            role="update+discovery",
        ),
    )
    _insert_registration(
        registration_id=uuid.uuid5(
            uuid.NAMESPACE_DNS,
            "tcg-pipeline.source.ladbs_permit_activity",
        ),
        source_name="ladbs_permit_activity",
        config=_source_config(
            collector="socrata",
            adapter="ladbs_permit_activity_pi9x_tg5x",
            endpoint="https://data.lacity.org/resource/pi9x-tg5x.json",
            coverage_scope="city",
            create_new_candidates=False,
            schedule="weekly",
            role="update",
        ),
    )
    _insert_registration(
        registration_id=uuid.uuid5(uuid.NAMESPACE_DNS, "tcg-pipeline.source.ladbs_inspections"),
        source_name="ladbs_inspections",
        config=_source_config(
            collector="socrata",
            adapter="ladbs_inspections_9w5z_rg2h",
            endpoint="https://data.lacity.org/resource/9w5z-rg2h.json",
            coverage_scope="city",
            create_new_candidates=False,
            schedule="weekly",
            role="update",
        ),
    )
    _insert_registration(
        registration_id=uuid.uuid5(uuid.NAMESPACE_DNS, "tcg-pipeline.source.ladbs_cofo"),
        source_name="ladbs_cofo",
        config=_source_config(
            collector="socrata",
            adapter="ladbs_cofo",
            endpoint="https://data.lacity.org/resource/3f9m-afei.json",
            coverage_scope="city",
            schedule="weekly",
            role="update",
        ),
    )
    _insert_registration(
        registration_id=uuid.uuid5(uuid.NAMESPACE_DNS, "tcg-pipeline.source.lahd_affordable"),
        source_name="lahd_affordable",
        config=_source_config(
            collector="socrata",
            endpoint="https://data.lacity.org/resource/mymu-zi3s.json",
            schedule="monthly",
            role="discovery+update",
        ),
    )
    _insert_registration(
        registration_id=uuid.uuid5(uuid.NAMESPACE_DNS, "tcg-pipeline.source.la_case_reports"),
        source_name="la_case_reports",
        config=_source_config(
            collector="pdf_parser",
            endpoint="https://planning.lacity.gov/dcpapi/general/biweeklycase/doc/{id}",
            schedule="biweekly",
            role="discovery",
        ),
    )
    _insert_registration(
        registration_id=uuid.uuid5(uuid.NAMESPACE_DNS, "tcg-pipeline.source.zimas_pdis"),
        source_name="zimas_pdis",
        config=_source_config(
            collector="scraper",
            endpoint="https://planning.lacity.gov/pdiscaseinfo/Search/casenumber/{case_number}",
            mode="enrichment_only",
            schedule="on_demand",
            role="enrichment",
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_source_registrations_jurisdiction_id",
        table_name="source_registrations",
    )
    op.drop_table("source_registrations")

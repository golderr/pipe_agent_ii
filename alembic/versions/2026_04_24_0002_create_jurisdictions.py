"""create jurisdictions

Revision ID: 202604240002
Revises: 202604240001
Create Date: 2026-04-24 19:06:00.000000
"""

from __future__ import annotations

import uuid

import geoalchemy2
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "202604240002"
down_revision = "202604240001"
branch_labels = None
depends_on = None


LA_JURISDICTION_ID = uuid.UUID("8dcd02af-0dfa-551d-948a-30c9f19add55")
LA_MARKET_ID = uuid.UUID("def7b4d1-b655-5d4c-b1f4-02fbcebbfbd2")


def upgrade() -> None:
    op.create_table(
        "jurisdictions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("state", sa.String(length=2), nullable=False),
        sa.Column("market_id", sa.UUID(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=True),
        sa.Column(
            "geom",
            geoalchemy2.types.Geography(
                geometry_type="MULTIPOLYGON",
                srid=4326,
                dimension=2,
                from_text="ST_GeogFromText",
                name="geography",
            ),
            nullable=True,
        ),
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
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state", "slug"),
    )
    op.create_index("ix_jurisdictions_market_id", "jurisdictions", ["market_id"])
    op.create_index("ix_jurisdictions_slug", "jurisdictions", ["slug"])
    op.create_index("ix_jurisdictions_state", "jurisdictions", ["state"])

    jurisdictions_table = sa.table(
        "jurisdictions",
        sa.column("id", sa.UUID()),
        sa.column("slug", sa.Text()),
        sa.column("name", sa.Text()),
        sa.column("display_name", sa.Text()),
        sa.column("state", sa.String(length=2)),
        sa.column("market_id", sa.UUID()),
        sa.column("entity_type", sa.Text()),
    )
    op.bulk_insert(
        jurisdictions_table,
        [
            {
                "id": LA_JURISDICTION_ID,
                "slug": "city_of_los_angeles",
                "name": "City of Los Angeles",
                "display_name": "Los Angeles",
                "state": "CA",
                "market_id": LA_MARKET_ID,
                "entity_type": "city",
            }
        ],
    )

    op.add_column("projects", sa.Column("jurisdiction_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_projects_jurisdiction_id_jurisdictions",
        "projects",
        "jurisdictions",
        ["jurisdiction_id"],
        ["id"],
    )
    op.create_index("ix_projects_jurisdiction_id", "projects", ["jurisdiction_id"])
    op.execute(
        f"""
        UPDATE projects
        SET jurisdiction_id = '{LA_JURISDICTION_ID}'::uuid
        WHERE jurisdiction_id IS NULL
          AND market = 'los_angeles'
          AND (
            lower(city) = 'los angeles'
            OR jurisdiction IN ('city_of_los_angeles', 'City of Los Angeles', 'Los Angeles')
          )
        """
    )


def downgrade() -> None:
    op.drop_index("ix_projects_jurisdiction_id", table_name="projects")
    op.drop_constraint(
        "fk_projects_jurisdiction_id_jurisdictions",
        "projects",
        type_="foreignkey",
    )
    op.drop_column("projects", "jurisdiction_id")

    op.drop_index("ix_jurisdictions_state", table_name="jurisdictions")
    op.drop_index("ix_jurisdictions_slug", table_name="jurisdictions")
    op.drop_index("ix_jurisdictions_market_id", table_name="jurisdictions")
    op.drop_table("jurisdictions")

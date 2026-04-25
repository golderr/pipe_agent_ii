"""create markets

Revision ID: 202604240001
Revises: 6aee42e3a7b5
Create Date: 2026-04-24 19:05:00.000000
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "202604240001"
down_revision = "6aee42e3a7b5"
branch_labels = None
depends_on = None


LA_MARKET_ID = uuid.UUID("def7b4d1-b655-5d4c-b1f4-02fbcebbfbd2")


def upgrade() -> None:
    op.create_table(
        "markets",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("state", sa.String(length=2), nullable=False),
        sa.Column("market_type", sa.Text(), nullable=True),
        sa.Column("parent_market_id", sa.UUID(), nullable=True),
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
        sa.ForeignKeyConstraint(["parent_market_id"], ["markets.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index("ix_markets_parent_market_id", "markets", ["parent_market_id"])
    op.create_index("ix_markets_slug", "markets", ["slug"])
    op.create_index("ix_markets_state", "markets", ["state"])

    markets_table = sa.table(
        "markets",
        sa.column("id", sa.UUID()),
        sa.column("slug", sa.Text()),
        sa.column("name", sa.Text()),
        sa.column("display_name", sa.Text()),
        sa.column("state", sa.String(length=2)),
        sa.column("market_type", sa.Text()),
    )
    op.bulk_insert(
        markets_table,
        [
            {
                "id": LA_MARKET_ID,
                "slug": "los_angeles",
                "name": "Los Angeles County",
                "display_name": "Los Angeles County",
                "state": "CA",
                "market_type": "county",
            }
        ],
    )

    op.add_column("projects", sa.Column("market_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_projects_market_id_markets",
        "projects",
        "markets",
        ["market_id"],
        ["id"],
    )
    op.create_index("ix_projects_market_id", "projects", ["market_id"])
    op.execute(
        """
        UPDATE projects
        SET market_id = markets.id
        FROM markets
        WHERE projects.market = markets.slug
          AND projects.market_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_projects_market_id", table_name="projects")
    op.drop_constraint("fk_projects_market_id_markets", "projects", type_="foreignkey")
    op.drop_column("projects", "market_id")

    op.drop_index("ix_markets_state", table_name="markets")
    op.drop_index("ix_markets_slug", table_name="markets")
    op.drop_index("ix_markets_parent_market_id", table_name="markets")
    op.drop_table("markets")

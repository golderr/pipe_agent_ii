"""add candidate city to news references

Revision ID: 202605080035
Revises: 202605080034
Create Date: 2026-05-08 19:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "202605080035"
down_revision = "202605080034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "news_project_references",
        sa.Column("candidate_city", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_news_project_references_candidate_city",
        "news_project_references",
        ["candidate_city"],
    )
    _replace_summary_view(include_candidate_city=True)


def downgrade() -> None:
    _replace_summary_view(include_candidate_city=False)
    op.drop_index(
        "ix_news_project_references_candidate_city",
        table_name="news_project_references",
    )
    op.drop_column("news_project_references", "candidate_city")


def _replace_summary_view(*, include_candidate_city: bool) -> None:
    city_column = "candidate_city," if include_candidate_city else ""
    op.execute("DROP VIEW IF EXISTS news_project_references_summary")
    op.execute(
        f"""
        CREATE VIEW news_project_references_summary
        WITH (security_invoker = false) AS
        SELECT
            id,
            extraction_id,
            article_id,
            reference_index,
            candidate_name,
            candidate_address,
            {city_column}
            candidate_developer,
            candidate_unit_total,
            candidate_unit_affordable,
            candidate_unit_market_rate,
            candidate_unit_workforce,
            candidate_product_type,
            candidate_age_restriction,
            candidate_status_signal,
            candidate_delivery_year_text,
            candidate_delivery_year_normalized,
            candidate_signal_flags,
            candidate_identifiers,
            candidate_neighborhood,
            candidate_lat,
            candidate_lng,
            candidate_confidence,
            match_status,
            matched_project_id,
            match_confidence,
            match_reason,
            match_candidates,
            match_decision_at,
            matched_evidence_id,
            review_item_id,
            manual_relink_by_user_id,
            manual_relink_at,
            manual_relink_note,
            created_at,
            updated_at
        FROM news_project_references
        """
    )
    op.execute("GRANT SELECT ON TABLE news_project_references_summary TO authenticated")

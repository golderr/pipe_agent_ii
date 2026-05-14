"""add candidate stories to news references

Revision ID: 202605130039
Revises: 202605110038
Create Date: 2026-05-13
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202605130039"
down_revision = "202605110038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "news_project_references",
        sa.Column("candidate_stories", sa.Integer(), nullable=True),
    )
    _replace_summary_view(include_candidate_stories=True)


def downgrade() -> None:
    _replace_summary_view(include_candidate_stories=False)
    op.drop_column("news_project_references", "candidate_stories")


def _replace_summary_view(*, include_candidate_stories: bool) -> None:
    stories_column = "candidate_stories,\n            " if include_candidate_stories else ""
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
            candidate_city,
            candidate_developer,
            candidate_unit_total,
            candidate_unit_affordable,
            candidate_unit_market_rate,
            candidate_unit_workforce,
            {stories_column}candidate_product_type,
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

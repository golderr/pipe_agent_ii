"""drop legacy project override and note columns

Revision ID: 202604280017
Revises: 202604280016
Create Date: 2026-04-28 19:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "202604280017"
down_revision = "202604280016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _assert_override_rows_are_backfilled()
    _assert_project_notes_are_backfilled()
    op.drop_column("projects", "researcher_override")
    op.drop_column("projects", "researcher_notes")
    op.drop_column("projects", "personal_notes")
    op.drop_column("projects", "change_notes")


def downgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("researcher_override", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("projects", sa.Column("researcher_notes", sa.Text(), nullable=True))
    op.add_column("projects", sa.Column("personal_notes", sa.Text(), nullable=True))
    op.add_column("projects", sa.Column("change_notes", sa.Text(), nullable=True))
    _restore_legacy_override_json()
    _restore_legacy_note_columns()


def _assert_override_rows_are_backfilled() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            WITH legacy AS (
              SELECT
                p.id AS project_id,
                entry.key AS field_name,
                CASE
                  WHEN jsonb_typeof(entry.value) = 'object'
                    AND entry.value ? 'value'
                  THEN entry.value -> 'value'
                  ELSE entry.value
                END AS override_value
              FROM projects p
              CROSS JOIN LATERAL jsonb_each(
                CASE
                  WHEN jsonb_typeof(p.researcher_override) = 'object'
                  THEN p.researcher_override
                  ELSE '{}'::jsonb
                END
              ) AS entry
              WHERE p.researcher_override IS NOT NULL
            )
            SELECT 1
            FROM legacy l
            LEFT JOIN researcher_overrides ro
              ON ro.project_id = l.project_id
             AND ro.field_name = l.field_name
             AND ro.cleared_at IS NULL
            WHERE ro.id IS NULL
               OR l.override_value IS DISTINCT FROM ro.value
            LIMIT 1
          ) THEN
            RAISE EXCEPTION
              'Legacy project researcher_override data diverges from researcher_overrides.';
          END IF;
        END $$;
        """
    )


def _assert_project_notes_are_backfilled() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            WITH latest_notes AS (
              SELECT DISTINCT ON (project_id, note_type)
                project_id,
                note_type,
                body
              FROM project_notes
              ORDER BY project_id, note_type, created_at DESC, id DESC
            ),
            legacy_notes AS (
              SELECT
                id AS project_id,
                'researcher_notes'::text AS note_type,
                NULLIF(BTRIM(researcher_notes), '') AS body
              FROM projects
              UNION ALL
              SELECT
                id AS project_id,
                'personal_notes'::text AS note_type,
                NULLIF(BTRIM(personal_notes), '') AS body
              FROM projects
              UNION ALL
              SELECT
                id AS project_id,
                'change_notes'::text AS note_type,
                NULLIF(BTRIM(change_notes), '') AS body
              FROM projects
            )
            SELECT 1
            FROM legacy_notes legacy
            LEFT JOIN latest_notes latest
              ON latest.project_id = legacy.project_id
             AND latest.note_type = legacy.note_type
            WHERE legacy.body IS NOT NULL
              AND latest.body IS DISTINCT FROM legacy.body
            LIMIT 1
          ) THEN
            RAISE EXCEPTION
              'Legacy project note columns diverge from project_notes.';
          END IF;
        END $$;
        """
    )


def _restore_legacy_override_json() -> None:
    op.execute(
        """
        UPDATE projects p
        SET researcher_override = active_overrides.payload
        FROM (
          SELECT
            project_id,
            jsonb_object_agg(
              field_name,
              jsonb_build_object(
                'value', value,
                'set_by', set_by_label,
                'set_at', set_at,
                'note', note,
                'source_url', source_url,
                'mode', mode,
                'baseline', baseline
              )
            ) AS payload
          FROM researcher_overrides
          WHERE cleared_at IS NULL
          GROUP BY project_id
        ) AS active_overrides
        WHERE p.id = active_overrides.project_id
        """
    )


def _restore_legacy_note_columns() -> None:
    for column_name in ("researcher_notes", "personal_notes", "change_notes"):
        op.execute(
            f"""
            UPDATE projects p
            SET {column_name} = latest.body
            FROM (
              SELECT DISTINCT ON (project_id)
                project_id,
                body
              FROM project_notes
              WHERE note_type = '{column_name}'
              ORDER BY project_id, created_at DESC, id DESC
            ) AS latest
            WHERE p.id = latest.project_id
            """
        )

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from tcg_pipeline.db.connection import get_engine  # noqa: E402


@dataclass(slots=True)
class ReviewItemRow:
    id: str
    project_id: str
    item_type: str
    state: str
    created_at: Any
    payload: dict[str, Any]
    field_name: str
    proposed_value: Any


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Collapse duplicate active status/contradiction review items before "
            "the C.tail.11 unique index is applied."
        )
    )
    parser.add_argument("--apply", action="store_true", help="Write changes. Defaults to dry run.")
    parser.add_argument(
        "--migrate-staged",
        action="store_true",
        help="Allow one staged duplicate group by making the staged item the survivor.",
    )
    args = parser.parse_args(argv)

    engine = get_engine()
    with engine.begin() as connection:
        has_columns = _has_decision_card_columns(connection)
        rows = _load_active_rows(connection)
        groups: dict[tuple[str, str, str], list[ReviewItemRow]] = defaultdict(list)
        for row in rows:
            if not row.field_name:
                continue
            groups[(row.project_id, row.field_name, row.item_type)].append(row)

        duplicate_groups = {
            key: sorted(value, key=lambda item: (item.created_at, item.id))
            for key, value in groups.items()
            if len(value) > 1
        }
        blocked = _blocked_staged_groups(duplicate_groups, migrate_staged=args.migrate_staged)
        print(f"Active duplicate decision-card groups: {len(duplicate_groups)}")
        if blocked:
            print("Blocked staged duplicate groups:")
            for key, staged_count in blocked[:20]:
                print(f"  {key} staged_count={staged_count}")
            print("Re-run with --migrate-staged during a quiet window if this is expected.")
            return 2

        invalidated_count = 0
        for key, group in duplicate_groups.items():
            survivor = _choose_survivor(group, migrate_staged=args.migrate_staged)
            merged_evidence_ids = _merged_evidence_ids(group)
            proposed_values = {
                json.dumps(row.proposed_value, sort_keys=True, default=str) for row in group
            }
            print(
                f"{'APPLY' if args.apply else 'DRY'} {key}: "
                f"survivor={survivor.id} items={len(group)} proposed_values={len(proposed_values)} "
                f"evidence_ids={len(merged_evidence_ids)}"
            )
            if not args.apply:
                continue
            _update_survivor(
                connection,
                survivor,
                field_name=key[1],
                evidence_ids=merged_evidence_ids,
                has_columns=has_columns,
            )
            for row in group:
                if row.id == survivor.id:
                    continue
                _invalidate_duplicate(connection, row)
                invalidated_count += 1

        if args.apply:
            print(f"Invalidated duplicate review items: {invalidated_count}")
        else:
            print("Dry run only. Re-run with --apply to collapse duplicates.")
    return 0


def _load_active_rows(connection) -> list[ReviewItemRow]:
    result = connection.execute(
        text(
            """
            SELECT
                id::text,
                project_id::text,
                item_type::text,
                state,
                created_at,
                payload,
                COALESCE(
                    NULLIF(BTRIM(payload->>'field_name'), ''),
                    CASE
                        WHEN payload ? 'status_suggestion'
                             AND payload->'status_suggestion' <> 'null'::jsonb
                        THEN 'pipeline_status'
                        ELSE NULL
                    END,
                    NULLIF(BTRIM(payload #>> '{changes,0,field}'), ''),
                    NULLIF(BTRIM(payload #>> '{changes,0,field_name}'), '')
                ) AS inferred_field_name,
                COALESCE(
                    payload->'proposed_value',
                    payload #> '{candidate,value}',
                    payload #> '{status_suggestion,suggested_status}',
                    payload #> '{changes,0,new_value}'
                ) AS inferred_proposed_value
            FROM review_items
            WHERE state IN ('open', 'staged')
              AND project_id IS NOT NULL
              AND item_type IN ('status_change', 'override_contradiction')
            ORDER BY project_id, item_type, created_at, id
            """
        )
    )
    rows: list[ReviewItemRow] = []
    for raw in result.mappings():
        payload = _coerce_payload(raw["payload"])
        rows.append(
            ReviewItemRow(
                id=str(raw["id"]),
                project_id=str(raw["project_id"]),
                item_type=str(raw["item_type"]),
                state=str(raw["state"]),
                created_at=raw["created_at"],
                payload=payload,
                field_name=str(raw["inferred_field_name"] or ""),
                proposed_value=raw["inferred_proposed_value"],
            )
        )
    return rows


def _has_decision_card_columns(connection) -> bool:
    count = connection.execute(
        text(
            """
            SELECT COUNT(*)
            FROM information_schema.columns
            WHERE table_name = 'review_items'
              AND column_name IN ('field_name', 'winning_evidence_id')
            """
        )
    ).scalar_one()
    return int(count) == 2


def _blocked_staged_groups(
    groups: dict[tuple[str, str, str], list[ReviewItemRow]],
    *,
    migrate_staged: bool,
) -> list[tuple[tuple[str, str, str], int]]:
    blocked: list[tuple[tuple[str, str, str], int]] = []
    for key, group in groups.items():
        staged_count = sum(row.state == "staged" for row in group)
        if staged_count and (not migrate_staged or staged_count > 1):
            blocked.append((key, staged_count))
    return blocked


def _choose_survivor(group: list[ReviewItemRow], *, migrate_staged: bool) -> ReviewItemRow:
    if migrate_staged:
        staged = [row for row in group if row.state == "staged"]
        if len(staged) == 1:
            return staged[0]
    return group[-1]


def _update_survivor(
    connection,
    row: ReviewItemRow,
    *,
    field_name: str,
    evidence_ids: list[str],
    has_columns: bool,
) -> None:
    payload = dict(row.payload)
    payload["field_name"] = field_name
    payload["proposed_value"] = row.proposed_value
    payload["evidence_ids"] = evidence_ids
    if has_columns:
        connection.execute(
            text(
                """
                UPDATE review_items
                SET field_name = :field_name,
                    updated_at = NOW(),
                    payload = CAST(:payload AS jsonb)
                WHERE id = CAST(:id AS uuid)
                """
            ),
            {"id": row.id, "field_name": field_name, "payload": json.dumps(payload, default=str)},
        )
        return
    connection.execute(
        text(
            """
            UPDATE review_items
            SET payload = CAST(:payload AS jsonb)
            WHERE id = CAST(:id AS uuid)
            """
        ),
        {"id": row.id, "payload": json.dumps(payload, default=str)},
    )


def _invalidate_duplicate(connection, row: ReviewItemRow) -> None:
    now = datetime.now(UTC).isoformat()
    payload = dict(row.payload)
    payload["invalidated_at"] = now
    payload["invalidated_reason"] = "duplicate_decision_card_collapsed"
    connection.execute(
        text(
            """
            UPDATE review_items
            SET state = 'invalidated',
                status = 'open',
                updated_at = NOW(),
                resolved_at = CAST(:resolved_at AS timestamptz),
                resolved_by = 'decision_card_collapse',
                payload = CAST(:payload AS jsonb)
            WHERE id = CAST(:id AS uuid)
            """
        ),
        {"id": row.id, "resolved_at": now, "payload": json.dumps(payload, default=str)},
    )


def _merged_evidence_ids(rows: list[ReviewItemRow]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for value in _evidence_ids_from_payload(row.payload):
            if value in seen:
                continue
            seen.add(value)
            merged.append(value)
    return merged


def _evidence_ids_from_payload(payload: dict[str, Any]) -> list[str]:
    values: list[Any] = []
    raw_evidence_ids = payload.get("evidence_ids")
    if isinstance(raw_evidence_ids, list):
        values.extend(raw_evidence_ids)
    candidate = payload.get("candidate")
    if isinstance(candidate, dict) and isinstance(candidate.get("evidence_ids"), list):
        values.extend(candidate["evidence_ids"])
    return [str(value).strip() for value in values if str(value).strip()]


def _coerce_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}
    return {}


if __name__ == "__main__":
    raise SystemExit(main())

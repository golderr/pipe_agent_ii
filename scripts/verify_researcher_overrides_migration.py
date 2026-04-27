from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from tcg_pipeline.db.connection import get_session_factory
from tcg_pipeline.db.evidence import serialize_json
from tcg_pipeline.db.models import Project, ResearcherOverride
from tcg_pipeline.db.researcher_overrides import (
    normalize_legacy_researcher_overrides,
    researcher_overrides_table_exists,
)
from tcg_pipeline.resolution import resolve_project
from tcg_pipeline.resolution.fields import normalize_comparable


@dataclass(frozen=True, slots=True)
class OverrideMigrationSummary:
    legacy_project_count: int
    legacy_pair_count: int
    table_exists: bool
    active_table_row_count: int
    legacy_only_pair_count: int
    table_only_pair_count: int
    mismatched_pair_count: int
    unique_active_index_exists: bool = False
    rls_enabled: bool = False
    read_policy_exists: bool = False
    authenticated_select_grant_exists: bool = False
    legacy_only_pairs: tuple[str, ...] = field(default_factory=tuple)
    table_only_pairs: tuple[str, ...] = field(default_factory=tuple)
    mismatched_pairs: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        if not self.table_exists:
            return True
        return (
            self.legacy_only_pair_count == 0
            and self.table_only_pair_count == 0
            and self.mismatched_pair_count == 0
            and self.unique_active_index_exists
            and self.rls_enabled
            and self.read_policy_exists
            and self.authenticated_select_grant_exists
        )


class SnapshotValidationError(ValueError):
    pass


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify the C.c researcher_overrides migration/backfill."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    summary_parser = subparsers.add_parser(
        "summary",
        help="Print legacy/table override counts and mismatches.",
    )
    summary_parser.add_argument(
        "--verbose",
        action="store_true",
        help="List up to 50 offending legacy-only, table-only, and mismatched keys.",
    )

    snapshot_parser = subparsers.add_parser(
        "snapshot",
        help="Write a pre/post migration resolution snapshot for projects with overrides.",
    )
    snapshot_parser.add_argument("--output", required=True, type=Path)

    compare_parser = subparsers.add_parser(
        "compare",
        help="Compare current override resolutions to a snapshot file.",
    )
    compare_parser.add_argument("--before", required=True, type=Path)
    compare_parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Allow empty snapshots. This should not be used for the C.c production gate.",
    )

    args = parser.parse_args()
    session_factory = get_session_factory()
    with session_factory() as session:
        if args.command == "summary":
            summary = summarize_override_state(session)
            print_summary(summary, verbose=args.verbose)
            raise SystemExit(0 if summary.ok else 1)
        if args.command == "snapshot":
            snapshot = build_resolution_snapshot(session)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")
            print(f"Wrote {len(snapshot['projects'])} project snapshots to {args.output}")
            return
        if args.command == "compare":
            before = json.loads(args.before.read_text(encoding="utf-8"))
            current = build_resolution_snapshot(session)
            try:
                differences = compare_resolution_snapshots(
                    before,
                    current,
                    allow_empty=args.allow_empty,
                )
            except SnapshotValidationError as exc:
                print(f"Invalid snapshot: {exc}")
                raise SystemExit(1) from exc
            if differences:
                print(f"Found {len(differences)} resolution differences:")
                for difference in differences[:50]:
                    print(difference)
                if len(differences) > 50:
                    print(f"... {len(differences) - 50} additional differences omitted")
                raise SystemExit(1)
            print("Resolution snapshot comparison passed.")


def summarize_override_state(session: Session) -> OverrideMigrationSummary:
    legacy_pairs = _legacy_override_pairs(session)
    table_exists = researcher_overrides_table_exists(session)
    table_pairs = _active_table_override_pairs(session) if table_exists else {}
    legacy_keys = set(legacy_pairs)
    table_keys = set(table_pairs)
    mismatched_keys = tuple(sorted(
        key
        for key in legacy_keys & table_keys
        if _override_payload_mismatches(legacy_pairs[key], table_pairs[key])
    ))
    artifact_checks = _migration_artifact_checks(session) if table_exists else {}
    return OverrideMigrationSummary(
        legacy_project_count=len({project_id for project_id, _field_name in legacy_keys}),
        legacy_pair_count=len(legacy_pairs),
        table_exists=table_exists,
        active_table_row_count=len(table_pairs),
        legacy_only_pair_count=len(legacy_keys - table_keys) if table_exists else 0,
        table_only_pair_count=len(table_keys - legacy_keys) if table_exists else 0,
        mismatched_pair_count=len(mismatched_keys) if table_exists else 0,
        unique_active_index_exists=artifact_checks.get("unique_active_index_exists", False),
        rls_enabled=artifact_checks.get("rls_enabled", False),
        read_policy_exists=artifact_checks.get("read_policy_exists", False),
        authenticated_select_grant_exists=artifact_checks.get(
            "authenticated_select_grant_exists",
            False,
        ),
        legacy_only_pairs=tuple(_format_pair_key(key) for key in sorted(legacy_keys - table_keys))
        if table_exists
        else (),
        table_only_pairs=tuple(_format_pair_key(key) for key in sorted(table_keys - legacy_keys))
        if table_exists
        else (),
        mismatched_pairs=tuple(_format_pair_key(key) for key in mismatched_keys)
        if table_exists
        else (),
    )


def print_summary(summary: OverrideMigrationSummary, *, verbose: bool = False) -> None:
    print(f"Legacy override projects: {summary.legacy_project_count}")
    print(f"Legacy override field pairs: {summary.legacy_pair_count}")
    print(f"researcher_overrides table exists: {summary.table_exists}")
    print(f"Active table rows: {summary.active_table_row_count}")
    print(f"Legacy-only pairs: {summary.legacy_only_pair_count}")
    print(f"Table-only pairs: {summary.table_only_pair_count}")
    print(f"Mismatched pairs: {summary.mismatched_pair_count}")
    if summary.table_exists:
        print(f"Unique active-field index exists: {summary.unique_active_index_exists}")
        print(f"RLS enabled: {summary.rls_enabled}")
        print(f"Authenticated read policy exists: {summary.read_policy_exists}")
        print(f"Authenticated SELECT grant exists: {summary.authenticated_select_grant_exists}")
    if verbose:
        _print_pair_list("Legacy-only", summary.legacy_only_pairs)
        _print_pair_list("Table-only", summary.table_only_pairs)
        _print_pair_list("Mismatched", summary.mismatched_pairs)
    print(f"Status: {'ok' if summary.ok else 'needs investigation'}")


def build_resolution_snapshot(session: Session) -> dict[str, Any]:
    # Keep verifier metadata reads separate from the resolver's merged override
    # helper. During the 2026-04 production gate, a same-session ORM preload of
    # ResearcherOverride rows followed by per-project resolution intermittently
    # hung; the compare path only needs field keys, so it stays column-only here.
    override_fields_by_project = _override_fields_by_project(session)
    projects = _projects_with_any_override(session, override_fields_by_project)
    snapshots: list[dict[str, Any]] = []
    for project in projects:
        override_fields = override_fields_by_project.get(project.id, set())
        if not override_fields:
            continue
        resolution = resolve_project(project.id, session, apply=False, write_resolution_log=False)
        missing_fields = sorted(override_fields - set(resolution.field_resolutions))
        if missing_fields:
            print(
                "WARNING: "
                f"{project.id} has override keys without resolver fields: "
                f"{', '.join(missing_fields)}"
            )
        snapshots.append(
            {
                "project_id": str(project.id),
                "canonical_address": project.canonical_address,
                "fields": {
                    field_name: serialize_json(
                        resolution.field_resolutions[field_name].value
                    )
                    for field_name in sorted(override_fields)
                    if field_name in resolution.field_resolutions
                },
            }
        )
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "project_count": len(snapshots),
        "projects": snapshots,
    }


def compare_resolution_snapshots(
    before: dict[str, Any],
    current: dict[str, Any],
    *,
    allow_empty: bool = False,
) -> list[str]:
    _validate_resolution_snapshot(before, label="before", allow_empty=allow_empty)
    _validate_resolution_snapshot(current, label="current", allow_empty=allow_empty)
    before_projects = {
        project["project_id"]: project
        for project in before.get("projects", [])
        if isinstance(project, dict) and "project_id" in project
    }
    current_projects = {
        project["project_id"]: project
        for project in current.get("projects", [])
        if isinstance(project, dict) and "project_id" in project
    }
    differences: list[str] = []
    for project_id in sorted(set(before_projects) | set(current_projects)):
        before_project = before_projects.get(project_id)
        current_project = current_projects.get(project_id)
        if before_project is None:
            differences.append(f"{project_id}: missing from before snapshot")
            continue
        if current_project is None:
            differences.append(f"{project_id}: missing from current snapshot")
            continue
        before_fields = before_project.get("fields", {})
        current_fields = current_project.get("fields", {})
        for field_name in sorted(set(before_fields) | set(current_fields)):
            before_value = normalize_comparable(before_fields.get(field_name))
            current_value = normalize_comparable(current_fields.get(field_name))
            if before_value != current_value:
                differences.append(
                    f"{project_id}.{field_name}: {before_value!r} -> {current_value!r}"
                )
    return differences


def _validate_resolution_snapshot(
    snapshot: dict[str, Any],
    *,
    label: str,
    allow_empty: bool,
) -> None:
    if not isinstance(snapshot, dict):
        raise SnapshotValidationError(f"{label} snapshot is not an object")
    projects = snapshot.get("projects")
    if not isinstance(projects, list):
        raise SnapshotValidationError(f"{label} snapshot is missing a projects list")
    if not allow_empty and not projects:
        raise SnapshotValidationError(f"{label} snapshot has no projects")
    project_count = snapshot.get("project_count")
    if project_count is not None and project_count != len(projects):
        raise SnapshotValidationError(
            f"{label} snapshot project_count {project_count} does not match "
            f"{len(projects)} project rows"
        )
    for index, project in enumerate(projects, start=1):
        if not isinstance(project, dict):
            raise SnapshotValidationError(f"{label} project row {index} is not an object")
        if not project.get("project_id"):
            raise SnapshotValidationError(f"{label} project row {index} is missing project_id")
        if not isinstance(project.get("fields"), dict):
            raise SnapshotValidationError(
                f"{label} project row {index} is missing a fields object"
            )


def _projects_with_any_override(
    session: Session,
    override_fields_by_project: dict[Any, set[str]],
) -> list[Project]:
    project_ids = set(override_fields_by_project)
    if not project_ids:
        return []
    return (
        session.execute(select(Project).where(Project.id.in_(project_ids)).order_by(Project.id))
        .scalars()
        .all()
    )


def _override_fields_by_project(session: Session) -> dict[Any, set[str]]:
    override_fields_by_project: dict[Any, set[str]] = {}
    pair_keys = set(_legacy_override_pairs(session))
    if researcher_overrides_table_exists(session):
        pair_keys.update(_active_table_override_pairs(session))
    for project_id, field_name in pair_keys:
        override_fields_by_project.setdefault(project_id, set()).add(field_name)
    return override_fields_by_project


def _legacy_override_pairs(session: Session) -> dict[tuple[Any, str], dict[str, Any]]:
    rows = session.execute(
        select(Project.id, Project.researcher_override).where(
            Project.researcher_override.is_not(None)
        )
    ).all()
    pairs: dict[tuple[Any, str], dict[str, Any]] = {}
    for project_id, raw_override in rows:
        for field_name, payload in normalize_legacy_researcher_overrides(raw_override).items():
            pairs[(project_id, field_name)] = payload
    return pairs


def _active_table_override_pairs(session: Session) -> dict[tuple[Any, str], dict[str, Any]]:
    rows = session.execute(
        select(
            ResearcherOverride.project_id,
            ResearcherOverride.field_name,
            ResearcherOverride.value,
            ResearcherOverride.set_by_label,
            ResearcherOverride.set_at,
            ResearcherOverride.note,
            ResearcherOverride.source_url,
            ResearcherOverride.mode,
            ResearcherOverride.baseline,
        ).where(ResearcherOverride.cleared_at.is_(None))
    ).mappings().all()
    return {
        (row["project_id"], row["field_name"]): {
            "value": row["value"],
            "set_by": row["set_by_label"],
            "set_at": row["set_at"],
            "note": row["note"],
            "source_url": row["source_url"],
            "mode": row["mode"],
            "baseline": row["baseline"],
        }
        for row in rows
    }


def _override_payload_mismatches(
    legacy_payload: dict[str, Any],
    table_payload: dict[str, Any],
) -> bool:
    for field_name in ("value", "set_by", "mode", "note", "source_url", "baseline"):
        if _normalize_metadata_value(legacy_payload.get(field_name)) != _normalize_metadata_value(
            table_payload.get(field_name)
        ):
            return True

    legacy_set_at = _normalize_datetime_value(legacy_payload.get("set_at"))
    if legacy_set_at is None:
        return False
    return legacy_set_at != _normalize_datetime_value(table_payload.get("set_at"))


def _migration_artifact_checks(session: Session) -> dict[str, bool]:
    return {
        "unique_active_index_exists": bool(
            session.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM pg_indexes
                        WHERE schemaname = 'public'
                          AND tablename = 'researcher_overrides'
                          AND indexname = 'uq_researcher_overrides_active_field'
                    )
                    """
                )
            ).scalar_one()
        ),
        "rls_enabled": bool(
            session.execute(
                text(
                    """
                    SELECT COALESCE((
                        SELECT c.relrowsecurity
                        FROM pg_class c
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = 'public'
                          AND c.relname = 'researcher_overrides'
                    ), false)
                    """
                )
            ).scalar_one()
        ),
        "read_policy_exists": bool(
            session.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM pg_policies
                        WHERE schemaname = 'public'
                          AND tablename = 'researcher_overrides'
                          AND policyname = 'authenticated_read_researcher_overrides'
                    )
                    """
                )
            ).scalar_one()
        ),
        "authenticated_select_grant_exists": bool(
            session.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.role_table_grants
                        WHERE table_schema = 'public'
                          AND table_name = 'researcher_overrides'
                          AND grantee = 'authenticated'
                          AND privilege_type = 'SELECT'
                    )
                    """
                )
            ).scalar_one()
        ),
    }


def _print_pair_list(label: str, pairs: tuple[str, ...]) -> None:
    if not pairs:
        return
    print(f"{label} keys:")
    for pair in pairs[:50]:
        print(f"  {pair}")
    if len(pairs) > 50:
        print(f"  ... {len(pairs) - 50} additional keys omitted")


def _format_pair_key(key: tuple[Any, str]) -> str:
    project_id, field_name = key
    return f"{project_id}.{field_name}"


def _normalize_metadata_value(value: Any) -> Any:
    return normalize_comparable(serialize_json(value))


def _normalize_datetime_value(value: Any) -> str | None:
    if isinstance(value, datetime):
        parsed = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return parsed.isoformat()
    if not isinstance(value, str):
        return None
    text_value = value.strip()
    if not text_value:
        return None
    if text_value.endswith("Z"):
        text_value = f"{text_value[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text_value)
    except ValueError:
        return text_value
    return parsed.isoformat()


if __name__ == "__main__":
    main()

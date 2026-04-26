from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

try:
    from scripts.phase_a_review_common import DEFAULT_OUTPUT_DIR, serialize_csv_value
except ModuleNotFoundError:  # pragma: no cover - script entrypoint fallback
    from phase_a_review_common import (  # type: ignore[no-redef]
        DEFAULT_OUTPUT_DIR,
        serialize_csv_value,
    )
from tcg_pipeline.db.connection import get_session_factory
from tcg_pipeline.db.models import Project
from tcg_pipeline.db.review_workflow import _build_override_entry, _merge_researcher_overrides
from tcg_pipeline.resolution import resolve_project

REVIEW_INPUT_FILENAMES = (
    "status_review.csv",
    "units_review.csv",
    "delivery_review.csv",
    "developer_review.csv",
)
PHASE_A_BUCKET_INPUT_FILENAMES = (
    "status_review.csv",
    "units_review.csv",
    "delivery_review.csv",
    "delivery_estimate_spotcheck.csv",
    "developer_review.csv",
    "developer_category_cleanup.csv",
    "developer_canonical_cleanup.csv",
)
PHASE_A_BUCKET_PROFILE = "phase_a_2026_04_23"
VALID_DECISIONS = {"accept", "override", "defer", "bug", ""}
DEVELOPER_REVIEW_REQUIRED_COLUMNS = (
    "project_id",
    "current_value",
    "raw_value",
    "resolved_value",
)


@dataclass(slots=True)
class DecisionRow:
    source_file: Path
    row_number: int
    project_id: UUID
    field_name: str
    current_value: str
    resolved_value: str
    decision: str
    notes: str
    raw_row: dict[str, str]
    override_source: str | None = None


@dataclass(slots=True, frozen=True)
class ScopedDeveloperOverride:
    project_id: UUID
    raw_value: str
    expected_current_developer: str


# LA Phase A scoped exceptions. Extend per market instead of matching raw names globally.
SCOPED_ARCHITECTURE_FIRM_OVERRIDES = (
    ScopedDeveloperOverride(
        project_id=UUID("1b92ab85-9860-4e3d-97ff-c2868f2b986d"),
        raw_value="MVE + Partners",
        expected_current_developer="Appa Real Estate",
    ),
    ScopedDeveloperOverride(
        project_id=UUID("e49bd069-a6b4-486c-8aac-d71857105224"),
        raw_value="MVE + Partners",
        expected_current_developer="KMK Management",
    ),
    ScopedDeveloperOverride(
        project_id=UUID("c4eb2f29-2e69-40e2-be88-296dc98ccf2a"),
        raw_value="Three 6Ixty",
        expected_current_developer="Onni Group",
    ),
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Apply Phase A CSV override decisions by writing researcher_override entries."
        ),
    )
    parser.add_argument(
        "--input-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Directory containing review CSVs (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--input-file",
        action="append",
        default=[],
        help=(
            "Optional explicit CSV path. Repeat to apply a subset; otherwise the standard "
            "review CSVs in --input-dir are used."
        ),
    )
    parser.add_argument(
        "--actor",
        default="phase_a_validation",
        help="Actor value stored on generated overrides.",
    )
    parser.add_argument(
        "--decision-profile",
        choices=[PHASE_A_BUCKET_PROFILE],
        default=None,
        help="Optional named bucket-level decision profile.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and summarize decisions without writing overrides.",
    )
    args = parser.parse_args()

    input_paths = _resolve_input_paths(
        input_dir=Path(args.input_dir),
        explicit_paths=[Path(path) for path in args.input_file],
        decision_profile=args.decision_profile,
    )
    result = apply_phase_a_decisions(
        input_paths=input_paths,
        actor=args.actor,
        dry_run=args.dry_run,
        decision_profile=args.decision_profile,
    )
    for line in result:
        print(line)


def apply_phase_a_decisions(
    *,
    input_paths: list[Path],
    actor: str,
    dry_run: bool,
    decision_profile: str | None,
) -> list[str]:
    decision_rows = _load_decision_rows(
        input_paths,
        decision_profile=decision_profile,
    )
    decision_counts = Counter(row.decision or "pending" for row in decision_rows)
    file_decision_counts = Counter(
        f"{row.source_file.name}:{row.decision or 'pending'}" for row in decision_rows
    )
    output_lines = [
        f"Loaded CSV rows: {len(decision_rows)}",
        *(
            [f"Decision profile: {decision_profile}"]
            if decision_profile is not None
            else []
        ),
        *[
            f"{decision}: {count}"
            for decision, count in sorted(decision_counts.items())
        ],
        *[
            f"{label}: {count}"
            for label, count in sorted(file_decision_counts.items())
        ],
    ]

    blocking_rows = [
        row
        for row in decision_rows
        if row.decision in {"bug", ""} or row.decision not in VALID_DECISIONS
    ]
    if blocking_rows:
        output_lines.append(
            "Blocking rows present: "
            + ", ".join(
                f"{row.source_file.name}:{row.row_number}={row.decision or 'pending'}"
                for row in blocking_rows[:12]
            )
        )
        if not dry_run:
            raise SystemExit("\n".join(output_lines))
        return output_lines

    override_rows = [row for row in decision_rows if row.decision in {"override", "defer"}]
    if dry_run:
        output_lines.append(f"Would write overrides: {len(override_rows)}")
        override_breakdown = Counter(
            f"{row.source_file.name}:{row.override_source or 'current'}" for row in override_rows
        )
        for label, count in sorted(override_breakdown.items()):
            output_lines.append(f"{label}: {count}")
        return output_lines

    session_factory = get_session_factory()
    with session_factory() as session:
        now = datetime.now(UTC)
        overrides_written = 0
        overrides_by_project: dict[UUID, list[DecisionRow]] = defaultdict(list)
        for row in override_rows:
            overrides_by_project[row.project_id].append(row)

        for project_id, project_rows in overrides_by_project.items():
            project = session.get(Project, project_id)
            if project is None:
                raise ValueError(f"Project {project_id} does not exist.")

            resolution_result = resolve_project(
                project_id,
                session,
                apply=False,
                write_resolution_log=False,
            )
            incoming_overrides: dict[str, dict[str, Any]] = {}
            for row in project_rows:
                current_serialized = _serialize_for_csv_compare(
                    getattr(project, row.field_name)
                )
                resolved_serialized = _serialize_for_csv_compare(
                    resolution_result.field_resolutions[row.field_name].value
                )
                if current_serialized != row.current_value:
                    raise ValueError(
                        "Current value drift detected for "
                        f"{project_id} {row.field_name}: "
                        f"{current_serialized!r} != {row.current_value!r}"
                    )
                if resolved_serialized != row.resolved_value:
                    raise ValueError(
                        "Resolved value drift detected for "
                        f"{project_id} {row.field_name}: "
                        f"{resolved_serialized!r} != {row.resolved_value!r}"
                    )

                note = row.notes.strip() or "Phase A validation override"
                if row.decision == "defer":
                    note = f"Phase A defer: {note}"

                override_value = _override_value_for_row(project, row)
                incoming_overrides[row.field_name] = _build_override_entry(
                    raw_override={
                        "value": override_value,
                        "mode": "until_newer_evidence",
                        "note": note,
                    },
                    actor=actor,
                    note=note,
                    now=now,
                    candidate_resolution=resolution_result.field_resolutions[row.field_name],
                )
                overrides_written += 1

            project.researcher_override = _merge_researcher_overrides(
                project.researcher_override,
                incoming_overrides,
            )

        session.commit()
    output_lines.append(f"Overrides written: {overrides_written}")
    return output_lines


def _resolve_input_paths(
    *,
    input_dir: Path,
    explicit_paths: list[Path],
    decision_profile: str | None,
) -> list[Path]:
    if explicit_paths:
        return explicit_paths
    filenames = (
        PHASE_A_BUCKET_INPUT_FILENAMES
        if decision_profile == PHASE_A_BUCKET_PROFILE
        else REVIEW_INPUT_FILENAMES
    )
    return [input_dir / name for name in filenames]


def _load_decision_rows(
    paths: list[Path],
    *,
    decision_profile: str | None,
) -> list[DecisionRow]:
    rows: list[DecisionRow] = []
    for path in paths:
        with path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row_number, raw_row in enumerate(reader, start=2):
                if decision_profile == PHASE_A_BUCKET_PROFILE:
                    decision, notes, override_source = _phase_a_bucket_decision(
                        path.name,
                        raw_row,
                    )
                else:
                    decision = (raw_row.get("decision") or "").strip().lower()
                    notes = (raw_row.get("notes") or "").strip()
                    override_source = None
                    if decision not in VALID_DECISIONS:
                        raise ValueError(
                            f"Invalid decision '{decision}' in {path}:{row_number}"
                        )
                rows.append(
                    DecisionRow(
                        source_file=path,
                        row_number=row_number,
                        project_id=UUID((raw_row.get("project_id") or "").strip()),
                        field_name=(raw_row.get("field") or "").strip(),
                        current_value=(raw_row.get("current_value") or "").strip(),
                        resolved_value=(raw_row.get("resolved_value") or "").strip(),
                        decision=decision,
                        notes=notes,
                        raw_row=dict(raw_row),
                        override_source=override_source,
                    )
                )
    return rows


def _phase_a_bucket_decision(
    source_filename: str,
    raw_row: dict[str, str],
) -> tuple[str, str, str | None]:
    if source_filename == "status_review.csv":
        return ("accept", "Phase A bucket policy: accept all status deltas.", None)

    if source_filename == "units_review.csv":
        delta_abs_text = (raw_row.get("delta_abs") or "").strip()
        delta_abs = int(delta_abs_text) if delta_abs_text else None
        if delta_abs is not None and delta_abs <= 5:
            return (
                "accept",
                f"Phase A bucket policy: accept total_units delta <= 5 (delta={delta_abs}).",
                None,
            )
        return (
            "defer",
            "Phase A bucket policy: keep current total_units when delta > 5.",
            "current",
        )

    if source_filename == "delivery_review.csv":
        return ("accept", "Phase A bucket policy: accept explicit delivery-date overwrites.", None)

    if source_filename == "delivery_estimate_spotcheck.csv":
        return (
            "accept",
            "Phase A bucket policy: accept estimated_calc delivery fills for blank dates.",
            None,
        )

    if source_filename in {"developer_category_cleanup.csv", "developer_canonical_cleanup.csv"}:
        return (
            "accept",
            "Phase A bucket policy: accept developer cleanup rows as data hygiene.",
            None,
        )

    if source_filename == "developer_review.csv":
        _require_columns(
            source_filename,
            raw_row,
            required_columns=DEVELOPER_REVIEW_REQUIRED_COLUMNS,
        )
        raw_value = (raw_row.get("raw_value") or "").strip()
        resolved_value = (raw_row.get("resolved_value") or "").strip()
        architecture_override = _scoped_architecture_firm_override(raw_row)
        if architecture_override is not None:
            return (
                "override",
                "Phase A architecture-firm exception: "
                f"keep current developer instead of '{raw_value}'.",
                "current",
            )
        if raw_value and raw_value != resolved_value:
            return (
                "override",
                f"Phase A bucket policy: accept raw developer value '{raw_value}'.",
                "raw",
            )
        return (
            "accept",
            "Phase A bucket policy: accept developer row as resolved.",
            None,
        )

    raise ValueError(f"Unsupported CSV for {PHASE_A_BUCKET_PROFILE}: {source_filename}")


def _require_columns(
    source_filename: str,
    raw_row: dict[str, str],
    *,
    required_columns: tuple[str, ...],
) -> None:
    missing = [column for column in required_columns if column not in raw_row]
    if missing:
        raise ValueError(
            f"{source_filename} is missing required columns: {', '.join(missing)}"
        )


def _scoped_architecture_firm_override(
    raw_row: dict[str, str],
) -> ScopedDeveloperOverride | None:
    raw_value = (raw_row.get("raw_value") or "").strip()
    project_id_text = (raw_row.get("project_id") or "").strip()
    if not raw_value or not project_id_text:
        return None

    try:
        project_id = UUID(project_id_text)
    except ValueError:
        return None

    current_value = (raw_row.get("current_value") or "").strip()
    for override in SCOPED_ARCHITECTURE_FIRM_OVERRIDES:
        if project_id != override.project_id or raw_value != override.raw_value:
            continue
        if current_value != override.expected_current_developer:
            raise ValueError(
                "Scoped architecture-firm override drift for "
                f"{project_id}: current developer {current_value!r} != "
                f"{override.expected_current_developer!r}."
            )
        return override
    return None


def _override_value_for_row(project: Project, row: DecisionRow) -> Any:
    if row.override_source == "raw":
        raw_value = (row.raw_row.get("raw_value") or "").strip()
        return raw_value or None
    return getattr(project, row.field_name)


def _serialize_for_csv_compare(value: Any) -> str:
    serialized = serialize_csv_value(value)
    if serialized is None:
        return ""
    return str(serialized)


if __name__ == "__main__":
    main()

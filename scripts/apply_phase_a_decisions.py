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
    from phase_a_review_common import DEFAULT_OUTPUT_DIR, serialize_csv_value  # type: ignore[no-redef]
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
VALID_DECISIONS = {"accept", "override", "defer", "bug", ""}


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
        "--dry-run",
        action="store_true",
        help="Validate and summarize decisions without writing overrides.",
    )
    args = parser.parse_args()

    input_paths = _resolve_input_paths(
        input_dir=Path(args.input_dir),
        explicit_paths=[Path(path) for path in args.input_file],
    )
    result = apply_phase_a_decisions(
        input_paths=input_paths,
        actor=args.actor,
        dry_run=args.dry_run,
    )
    for line in result:
        print(line)


def apply_phase_a_decisions(
    *,
    input_paths: list[Path],
    actor: str,
    dry_run: bool,
) -> list[str]:
    decision_rows = _load_decision_rows(input_paths)
    decision_counts = Counter(row.decision or "pending" for row in decision_rows)
    output_lines = [
        f"Loaded CSV rows: {len(decision_rows)}",
        *[
            f"{decision}: {count}"
            for decision, count in sorted(decision_counts.items())
        ],
    ]

    blocking_rows = [
        row for row in decision_rows if row.decision in {"bug", ""} or row.decision not in VALID_DECISIONS
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
                current_serialized = serialize_csv_value(
                    getattr(project, row.field_name)
                )
                resolved_serialized = serialize_csv_value(
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

                incoming_overrides[row.field_name] = _build_override_entry(
                    raw_override={
                        "value": getattr(project, row.field_name),
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
) -> list[Path]:
    if explicit_paths:
        return explicit_paths
    return [input_dir / name for name in REVIEW_INPUT_FILENAMES]


def _load_decision_rows(paths: list[Path]) -> list[DecisionRow]:
    rows: list[DecisionRow] = []
    for path in paths:
        with path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row_number, raw_row in enumerate(reader, start=2):
                decision = (raw_row.get("decision") or "").strip().lower()
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
                        notes=(raw_row.get("notes") or "").strip(),
                        raw_row=dict(raw_row),
                    )
                )
    return rows


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import TextIO

from sqlalchemy import delete, select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from tcg_pipeline.cli import LOGGED_FIELDS, _confidence_label  # noqa: E402
from tcg_pipeline.db.connection import get_session_factory  # noqa: E402
from tcg_pipeline.db.models import Project, ResolutionLog  # noqa: E402
from tcg_pipeline.resolution import resolve_project  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stable batched resolver for Phase A shadow/apply validation.",
    )
    parser.add_argument("--market", default=None)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--clear-log", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--start-after", default=None)
    parser.add_argument(
        "--log-file",
        default=None,
        help="Optional path to append batch progress and summary lines.",
    )
    args = parser.parse_args()

    log_handle: TextIO | None = None
    try:
        if args.log_file:
            log_path = Path(args.log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_handle = log_path.open("w", encoding="utf-8")
        run_phase_a_resolve(
            market=args.market,
            apply=args.apply,
            clear_log=args.clear_log,
            limit=args.limit,
            batch_size=args.batch_size,
            start_after=args.start_after,
            log_handle=log_handle,
        )
    finally:
        if log_handle is not None:
            log_handle.close()


def run_phase_a_resolve(
    *,
    market: str | None,
    apply: bool,
    clear_log: bool,
    limit: int | None,
    batch_size: int,
    start_after: str | None,
    log_handle: TextIO | None,
) -> None:
    session_factory = get_session_factory()
    with session_factory() as session:
        statement = select(Project.id).order_by(Project.id)
        if market is not None:
            statement = statement.where(Project.market == market)
        if start_after is not None:
            statement = statement.where(Project.id > start_after)
        if limit is not None:
            statement = statement.limit(limit)
        project_ids = list(session.execute(statement).scalars().all())

    if clear_log:
        with session_factory() as session:
            project_id_statement = select(Project.id)
            if market is not None:
                project_id_statement = project_id_statement.where(Project.market == market)
            session.execute(
                delete(ResolutionLog).where(ResolutionLog.project_id.in_(project_id_statement))
            )
            session.commit()

    total_projects = 0
    total_changed_fields = 0
    total_log_rows = 0
    changed_projects = 0
    field_counts: Counter[str] = Counter()
    resolution_confidence_counts: Counter[str] = Counter()
    project_confidence_counts: Counter[str] = Counter()

    for batch_number, start_index in enumerate(range(0, len(project_ids), batch_size), start=1):
        batch_ids = project_ids[start_index : start_index + batch_size]
        if not batch_ids:
            continue

        with session_factory() as session:
            batch_changed_projects = 0
            batch_changed_fields = 0
            batch_log_rows = 0
            project_confidence_by_id = dict(
                session.execute(
                    select(Project.id, Project.confidence).where(Project.id.in_(batch_ids))
                ).all()
            )
            for project_id in batch_ids:
                session.execute(delete(ResolutionLog).where(ResolutionLog.project_id == project_id))
                result = resolve_project(
                    project_id,
                    session,
                    apply=apply,
                    write_resolution_log=True,
                )
                if result.changed_fields:
                    changed_projects += 1
                    batch_changed_projects += 1
                    project_confidence_counts[
                        _confidence_label(project_confidence_by_id.get(project_id))
                    ] += 1
                total_changed_fields += len(result.changed_fields)
                batch_changed_fields += len(result.changed_fields)
                total_log_rows += result.log_entries_created
                batch_log_rows += result.log_entries_created
                for field_name in result.changed_fields:
                    if field_name not in LOGGED_FIELDS:
                        continue
                    field_counts[field_name] += 1
                    resolution_confidence_counts[
                        _confidence_label(result.field_resolutions[field_name].confidence)
                    ] += 1
            session.commit()

        total_projects += len(batch_ids)
        _emit(
            f"Batch {batch_number}: projects={len(batch_ids)} "
            f"discrepancies={batch_changed_projects} "
            f"changed_fields={batch_changed_fields} "
            f"log_rows={batch_log_rows} "
            f"last_project_id={batch_ids[-1]}",
            log_handle=log_handle,
        )

    _emit(f"Projects resolved: {total_projects}", log_handle=log_handle)
    _emit(f"Projects with discrepancies: {changed_projects}", log_handle=log_handle)
    _emit(f"Changed fields detected: {total_changed_fields}", log_handle=log_handle)
    _emit(f"Resolution log rows written: {total_log_rows}", log_handle=log_handle)
    _emit("Changed field counts:", log_handle=log_handle)
    for key, value in sorted(field_counts.items()):
        _emit(f"  {key}: {value}", log_handle=log_handle)
    _emit("Resolution confidence counts:", log_handle=log_handle)
    for key, value in sorted(resolution_confidence_counts.items()):
        _emit(f"  {key}: {value}", log_handle=log_handle)
    _emit("Current project confidence counts:", log_handle=log_handle)
    for key, value in sorted(project_confidence_counts.items()):
        _emit(f"  {key}: {value}", log_handle=log_handle)
    _emit(f"Apply mode: {apply}", log_handle=log_handle)


def _emit(message: str, *, log_handle: TextIO | None) -> None:
    print(message, flush=True)
    if log_handle is not None:
        log_handle.write(message + "\n")
        log_handle.flush()


if __name__ == "__main__":
    main()

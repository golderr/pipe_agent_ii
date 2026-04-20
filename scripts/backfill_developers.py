from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from tcg_pipeline.db.connection import get_session_factory  # noqa: E402
from tcg_pipeline.db.models import DeveloperRegistry, Project  # noqa: E402


@dataclass(slots=True)
class BackfillDevelopersResult:
    inserted: int = 0
    skipped_existing: int = 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill developer_registry from distinct project developer names.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build developer registry rows without committing them.",
    )
    args = parser.parse_args()

    result = backfill_developers(dry_run=args.dry_run)
    print(f"Inserted developer registry rows: {result.inserted}")
    print(f"Skipped existing developer names: {result.skipped_existing}")
    print(f"Committed: {not args.dry_run}")


def backfill_developers(*, dry_run: bool = False) -> BackfillDevelopersResult:
    session_factory = get_session_factory()
    with session_factory() as session:
        result = _populate_developer_registry(session)
        if dry_run:
            session.rollback()
        else:
            session.commit()
        return result


def _populate_developer_registry(session: Session) -> BackfillDevelopersResult:
    result = BackfillDevelopersResult()
    developer_names = (
        session.execute(select(Project.developer).where(Project.developer.is_not(None)))
        .scalars()
        .all()
    )
    existing_names = {
        name
        for name in session.execute(select(DeveloperRegistry.canonical_name)).scalars()
        if name
    }
    normalized_names = {
        name.strip() for name in developer_names if name and name.strip()
    }
    for developer_name in sorted(normalized_names):
        if developer_name in existing_names:
            result.skipped_existing += 1
            continue
        session.add(DeveloperRegistry(canonical_name=developer_name))
        existing_names.add(developer_name)
        result.inserted += 1
    return result


if __name__ == "__main__":
    main()

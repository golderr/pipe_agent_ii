from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from tcg_pipeline.db.connection import get_session_factory  # noqa: E402
from tcg_pipeline.developer.audit import (  # noqa: E402
    audit_developer_registry_token_overlap,
    delete_developer_registry_audit_issues,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Audit high-alias developer registry rows for aliases that fail "
            "the meaningful-token overlap guard."
        )
    )
    parser.add_argument(
        "--min-aliases",
        type=int,
        default=3,
        help="Only audit canonical rows with at least this many aliases.",
    )
    parser.add_argument(
        "--apply-delete",
        action="store_true",
        help=(
            "Prune unsafe aliases and delete unsafe canonical rows. "
            "Default is shadow mode."
        ),
    )
    args = parser.parse_args()

    session_factory = get_session_factory()
    with session_factory() as session:
        issues = audit_developer_registry_token_overlap(
            session,
            min_aliases=args.min_aliases,
        )
        print(f"Rows audited with >= {args.min_aliases} aliases.")
        print(f"Flagged canonical rows: {len(issues)}")
        for issue in issues:
            unsafe_note = (
                " unsafe_canonical_name"
                if issue.unsafe_canonical_name
                else ""
            )
            print(
                f"- {issue.developer_id} | {issue.canonical_name} | "
                f"aliases={issue.alias_count} | "
                f"unsafe_aliases={issue.unsafe_alias_count}{unsafe_note}"
            )
            for alias_name in issue.unsafe_aliases:
                print(f"  - {alias_name}")

        if args.apply_delete:
            apply_result = delete_developer_registry_audit_issues(session, issues)
            session.commit()
            print(f"Pruned unsafe aliases: {apply_result.pruned_alias_count}")
            for alias in apply_result.pruned_aliases:
                print(
                    f"Pruned alias: {alias.developer_id} | "
                    f"{alias.canonical_name} | {alias.alias_name}"
                )
            print(f"Deleted canonical rows: {apply_result.deleted_canonical_count}")
            for issue in apply_result.deleted_canonical_rows:
                print(f"Deleted canonical: {issue.developer_id} | {issue.canonical_name}")
        else:
            session.rollback()
            print("Shadow mode: no registry rows or aliases changed.")


if __name__ == "__main__":
    main()

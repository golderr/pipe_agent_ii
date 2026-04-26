from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from tcg_pipeline.db.models import DeveloperAlias, DeveloperRegistry
from tcg_pipeline.developer.registry import (
    is_safe_developer_alias,
    is_safe_developer_registry_name,
)


@dataclass(slots=True, frozen=True)
class DeveloperRegistryAuditIssue:
    developer_id: uuid.UUID
    canonical_name: str
    alias_count: int
    unsafe_aliases: tuple[str, ...]
    unsafe_canonical_name: bool = False

    @property
    def unsafe_alias_count(self) -> int:
        return len(self.unsafe_aliases)


@dataclass(slots=True, frozen=True)
class DeveloperRegistryPrunedAlias:
    developer_id: uuid.UUID
    canonical_name: str
    alias_name: str


@dataclass(slots=True, frozen=True)
class DeveloperRegistryAuditApplyResult:
    deleted_canonical_rows: tuple[DeveloperRegistryAuditIssue, ...] = ()
    pruned_aliases: tuple[DeveloperRegistryPrunedAlias, ...] = ()

    @property
    def deleted_canonical_count(self) -> int:
        return len(self.deleted_canonical_rows)

    @property
    def pruned_alias_count(self) -> int:
        return len(self.pruned_aliases)


def audit_developer_registry_token_overlap(
    session: Session,
    *,
    min_aliases: int = 3,
) -> list[DeveloperRegistryAuditIssue]:
    registry_rows = (
        session.execute(
            select(DeveloperRegistry)
            .options(selectinload(DeveloperRegistry.aliases))
            .order_by(DeveloperRegistry.canonical_name)
        )
        .scalars()
        .all()
    )

    issues: list[DeveloperRegistryAuditIssue] = []
    for developer in registry_rows:
        aliases = sorted(alias.alias_name for alias in developer.aliases)
        if len(aliases) < min_aliases:
            continue

        unsafe_aliases = tuple(
            alias_name
            for alias_name in aliases
            if not is_safe_developer_alias(
                canonical_name=developer.canonical_name,
                alias_name=alias_name,
            )
        )
        unsafe_canonical_name = not is_safe_developer_registry_name(
            developer.canonical_name
        )
        if unsafe_aliases or unsafe_canonical_name:
            issues.append(
                DeveloperRegistryAuditIssue(
                    developer_id=developer.id,
                    canonical_name=developer.canonical_name,
                    alias_count=len(aliases),
                    unsafe_aliases=unsafe_aliases,
                    unsafe_canonical_name=unsafe_canonical_name,
                )
            )
    return issues


def delete_developer_registry_audit_issues(
    session: Session,
    issues: list[DeveloperRegistryAuditIssue],
) -> DeveloperRegistryAuditApplyResult:
    if not issues:
        return DeveloperRegistryAuditApplyResult()

    deleted_canonical_rows = tuple(
        issue for issue in issues if issue.unsafe_canonical_name
    )
    pruned_aliases: list[DeveloperRegistryPrunedAlias] = []
    for issue in issues:
        if issue.unsafe_canonical_name or not issue.unsafe_aliases:
            continue

        existing_aliases = (
            session.execute(
                select(DeveloperAlias.alias_name).where(
                    DeveloperAlias.developer_id == issue.developer_id,
                    DeveloperAlias.alias_name.in_(issue.unsafe_aliases),
                )
            )
            .scalars()
            .all()
        )
        if not existing_aliases:
            continue

        session.execute(
            delete(DeveloperAlias).where(
                DeveloperAlias.developer_id == issue.developer_id,
                DeveloperAlias.alias_name.in_(existing_aliases),
            )
        )
        pruned_aliases.extend(
            DeveloperRegistryPrunedAlias(
                developer_id=issue.developer_id,
                canonical_name=issue.canonical_name,
                alias_name=alias_name,
            )
            for alias_name in sorted(existing_aliases)
        )

    deleted_ids = [issue.developer_id for issue in deleted_canonical_rows]
    if deleted_ids:
        session.execute(
            delete(DeveloperAlias).where(DeveloperAlias.developer_id.in_(deleted_ids))
        )
        session.execute(
            delete(DeveloperRegistry).where(DeveloperRegistry.id.in_(deleted_ids))
        )

    return DeveloperRegistryAuditApplyResult(
        deleted_canonical_rows=deleted_canonical_rows,
        pruned_aliases=tuple(pruned_aliases),
    )

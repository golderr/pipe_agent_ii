from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from tcg_pipeline.db.models import DeveloperRegistry, Project
from tcg_pipeline.db.researcher_overrides import project_has_active_researcher_override
from tcg_pipeline.developer.registry import (
    DeveloperCanonicalizationResult,
    canonicalize_developer_name,
    canonicalize_registry_entry,
)


@dataclass(slots=True)
class DeveloperCanonicalizationSweepResult:
    registry_rows_scanned: int = 0
    registry_rows_merged: int = 0
    registry_rows_created: int = 0
    aliases_created: int = 0
    projects_scanned: int = 0
    projects_changed: int = 0
    exact_matches: int = 0
    fuzzy_auto_matches: int = 0
    fuzzy_review_matches: int = 0
    new_registry_entries: int = 0


def canonicalize_project_developers(
    session: Session,
    *,
    market: str | None = None,
    apply: bool = False,
    limit: int | None = None,
) -> DeveloperCanonicalizationSweepResult:
    result = DeveloperCanonicalizationSweepResult()

    registry_ids = session.execute(
        select(DeveloperRegistry.id).order_by(DeveloperRegistry.canonical_name)
    ).scalars().all()
    for developer_id in registry_ids:
        registry_result = canonicalize_registry_entry(
            session,
            developer_id,
            persist=apply,
        )
        result.registry_rows_scanned += 1
        _accumulate_result(result, registry_result)

    project_query = (
        select(Project)
        .where(Project.developer.is_not(None))
        .order_by(Project.id)
    )
    if market is not None:
        project_query = project_query.where(Project.market == market)
    if limit is not None:
        project_query = project_query.limit(limit)

    projects = session.execute(project_query).scalars().all()
    for project in projects:
        if project.developer is None:
            continue
        result.projects_scanned += 1
        canonicalization = canonicalize_developer_name(
            session,
            project.developer,
            persist=apply,
        )
        _accumulate_result(result, canonicalization)
        if (
            apply
            and canonicalization.match_type != "fuzzy_review"
            and canonicalization.canonical_name is not None
            and canonicalization.canonical_name != project.developer
            and not project_has_active_researcher_override(session, project, "developer")
        ):
            project.developer = canonicalization.canonical_name
            result.projects_changed += 1

    return result


def _accumulate_result(
    result: DeveloperCanonicalizationSweepResult,
    canonicalization: DeveloperCanonicalizationResult,
) -> None:
    if canonicalization.registry_merged:
        result.registry_rows_merged += 1
    if canonicalization.registry_created:
        result.registry_rows_created += 1
    if canonicalization.alias_created:
        result.aliases_created += 1

    if canonicalization.match_type in {"exact_canonical", "exact_alias"}:
        result.exact_matches += 1
    elif canonicalization.match_type == "fuzzy_auto":
        result.fuzzy_auto_matches += 1
    elif canonicalization.match_type == "fuzzy_review":
        result.fuzzy_review_matches += 1
    elif canonicalization.match_type == "new_registry_entry":
        result.new_registry_entries += 1

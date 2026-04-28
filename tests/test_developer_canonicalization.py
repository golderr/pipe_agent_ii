from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from tcg_pipeline.cli import app
from tcg_pipeline.db.models import (
    DeveloperAlias,
    DeveloperRegistry,
    Evidence,
    Project,
    ResearcherOverride,
)
from tcg_pipeline.developer import (
    audit_developer_registry_token_overlap,
    canonicalize_project_developers,
    delete_developer_registry_audit_issues,
    is_safe_developer_alias,
    is_safe_developer_registry_name,
    normalize_developer_name,
)
from tcg_pipeline.developer import registry as registry_module
from tcg_pipeline.developer.canonicalize import DeveloperCanonicalizationSweepResult
from tcg_pipeline.developer.registry import (
    canonicalize_developer_name,
    canonicalize_registry_entry,
)
from tcg_pipeline.resolution import resolve_project

runner = CliRunner()

TEST_JAMISON = "ZZZQXQ Jamisonic Services"
TEST_JAMISON_ALIAS = "ZZZQXQ Jamisonic Services LP"
TEST_CIM = "ZZZQXQ Cimmer Group"
TEST_CIM_ALIAS = "ZZZQXQ C1mmer Grp"
TEST_AJ_CANONICAL = "ZZZQXQ Xylor Delta Development"
TEST_AJ_DUPLICATE = "ZZZQXQ Xylor Delta Developmnt"
TEST_AJ_ALIAS = "ZZZQXQ Xylor Delta"
TEST_NEW_DEVELOPER = "ZZZQXQ Brand New Dev"


def test_normalize_developer_name_strips_legal_suffixes() -> None:
    assert normalize_developer_name("Jamison Services, LLC") == "JAMISON SERVICES"
    assert normalize_developer_name("The CIM Group LP") == "CIM GROUP"


def test_developer_registry_safety_guard_requires_meaningful_overlap() -> None:
    assert is_safe_developer_registry_name("Jamison Services")
    assert not is_safe_developer_registry_name("Capital")
    assert is_safe_developer_alias(
        canonical_name="Jamison Services",
        alias_name="Jamison Services LP",
    )
    assert not is_safe_developer_alias(
        canonical_name="Nimbleroot Capital",
        alias_name="Vellum Capital",
    )


def test_canonicalize_developer_name_matches_exact_alias(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("developer_registry"):
        pytest.skip("Apply the evidence layer migration before running developer tests.")

    developer = DeveloperRegistry(canonical_name=TEST_JAMISON)
    postgres_session.add(developer)
    postgres_session.flush()
    postgres_session.add(
        DeveloperAlias(
            developer_id=developer.id,
            alias_name=TEST_JAMISON_ALIAS,
        )
    )
    postgres_session.flush()

    result = canonicalize_developer_name(
        postgres_session,
        TEST_JAMISON_ALIAS,
        persist=False,
    )

    assert result.canonical_name == TEST_JAMISON
    assert result.match_type == "exact_alias"


def test_canonicalize_developer_name_reuses_session_cache(
    monkeypatch: pytest.MonkeyPatch,
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("developer_registry"):
        pytest.skip("Apply the evidence layer migration before running developer tests.")

    postgres_session.add(DeveloperRegistry(canonical_name=TEST_JAMISON))
    postgres_session.flush()

    call_count = 0
    original_loader = registry_module._load_registry_from_db

    def counting_loader(session: Session):
        nonlocal call_count
        call_count += 1
        return original_loader(session)

    monkeypatch.setattr(registry_module, "_load_registry_from_db", counting_loader)

    canonicalize_developer_name(postgres_session, TEST_JAMISON, persist=False)
    canonicalize_developer_name(postgres_session, TEST_JAMISON_ALIAS, persist=False)

    assert call_count == 1


def test_canonicalize_developer_name_uses_fuzzy_auto_threshold(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("developer_registry"):
        pytest.skip("Apply the evidence layer migration before running developer tests.")

    postgres_session.add(DeveloperRegistry(canonical_name=TEST_JAMISON))
    postgres_session.flush()

    result = canonicalize_developer_name(
        postgres_session,
        "ZZZQXQ Jamisonic Servics",
        persist=False,
    )

    assert result.canonical_name == TEST_JAMISON
    assert result.match_type == "fuzzy_auto"
    assert result.score is not None and result.score >= 90.0


def test_canonicalize_developer_name_uses_fuzzy_review_threshold(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("developer_registry"):
        pytest.skip("Apply the evidence layer migration before running developer tests.")

    postgres_session.add(DeveloperRegistry(canonical_name=TEST_CIM))
    postgres_session.flush()

    result = canonicalize_developer_name(
        postgres_session,
        TEST_CIM_ALIAS,
        persist=False,
    )

    assert result.canonical_name == TEST_CIM
    assert result.match_type == "fuzzy_review"
    assert result.score is not None and 75.0 <= result.score < 90.0
    assert result.requires_review is True


def test_canonicalize_developer_name_blocks_generic_token_fuzzy_match(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("developer_registry"):
        pytest.skip("Apply the evidence layer migration before running developer tests.")

    canonical_name = "ZZZSAFE Nimbleroot Capital"
    raw_name = "ZZZPAC Vellum Capital"
    postgres_session.add(DeveloperRegistry(canonical_name=canonical_name))
    postgres_session.flush()

    result = canonicalize_developer_name(
        postgres_session,
        raw_name,
        persist=False,
    )

    assert result.canonical_name == raw_name
    assert result.match_type == "new_registry_entry"


def test_canonicalize_developer_name_still_allows_meaningful_token_fuzzy_match(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("developer_registry"):
        pytest.skip("Apply the evidence layer migration before running developer tests.")

    postgres_session.add(DeveloperRegistry(canonical_name=TEST_CIM))
    postgres_session.flush()

    result = canonicalize_developer_name(
        postgres_session,
        TEST_CIM_ALIAS,
        persist=False,
    )

    assert result.canonical_name == TEST_CIM
    assert result.match_type == "fuzzy_review"


def test_canonicalize_developer_name_ignores_generic_category_registry_row(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("developer_registry"):
        pytest.skip("Apply the evidence layer migration before running developer tests.")

    category = postgres_session.execute(
        select(DeveloperRegistry).where(DeveloperRegistry.canonical_name == "Category")
    ).scalar_one_or_none()
    if category is None:
        category = DeveloperRegistry(canonical_name="Category")
        postgres_session.add(category)
        postgres_session.flush()
    postgres_session.add(
        DeveloperAlias(
            developer_id=category.id,
            alias_name="ZZZQXQ Nimbleroot",
        )
    )
    postgres_session.flush()

    result = canonicalize_developer_name(
        postgres_session,
        "ZZZQXQ Nimbleroot LLC",
        persist=False,
    )

    assert result.canonical_name == "ZZZQXQ Nimbleroot LLC"
    assert result.match_type == "new_registry_entry"


def test_canonicalize_developer_name_ignores_category_raw_name_and_does_not_persist(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("developer_registry"):
        pytest.skip("Apply the evidence layer migration before running developer tests.")

    existing_count = postgres_session.execute(
        select(DeveloperRegistry.id).where(DeveloperRegistry.canonical_name == "Category")
    ).scalars().all()

    result = canonicalize_developer_name(
        postgres_session,
        "Category",
        persist=True,
    )
    postgres_session.flush()

    updated_count = postgres_session.execute(
        select(DeveloperRegistry.id).where(DeveloperRegistry.canonical_name == "Category")
    ).scalars().all()

    assert result.canonical_name == "Category"
    assert result.match_type == "ignored_registry_entry"
    assert updated_count == existing_count


def test_canonicalize_developer_name_does_not_persist_generic_registry_name(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("developer_registry"):
        pytest.skip("Apply the evidence layer migration before running developer tests.")

    generic_name = "Capital Ventures Development Funds"
    result = canonicalize_developer_name(
        postgres_session,
        generic_name,
        persist=True,
    )
    postgres_session.flush()
    registry_rows = postgres_session.execute(
        select(DeveloperRegistry.id).where(DeveloperRegistry.canonical_name == generic_name)
    ).scalars().all()

    assert result.match_type == "new_registry_entry"
    assert result.registry_created is False
    assert registry_rows == []


def test_developer_registry_audit_prunes_unsafe_aliases_for_safe_canonical(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("developer_registry"):
        pytest.skip("Apply the evidence layer migration before running developer tests.")

    polluted = DeveloperRegistry(canonical_name="ZZZQXQ Prune Test Developer")
    safe = DeveloperRegistry(canonical_name=TEST_JAMISON)
    postgres_session.add_all([polluted, safe])
    postgres_session.flush()
    postgres_session.add_all(
        [
            DeveloperAlias(developer_id=polluted.id, alias_name="Advisor Associate Holdings"),
            DeveloperAlias(developer_id=polluted.id, alias_name="Funds Venture Properties"),
            DeveloperAlias(developer_id=polluted.id, alias_name="Realty Management Communities"),
            DeveloperAlias(developer_id=safe.id, alias_name=f"{TEST_JAMISON} LP"),
            DeveloperAlias(developer_id=safe.id, alias_name=f"{TEST_JAMISON} Inc"),
            DeveloperAlias(developer_id=safe.id, alias_name=f"The {TEST_JAMISON}"),
        ]
    )
    postgres_session.flush()

    issues = audit_developer_registry_token_overlap(postgres_session, min_aliases=3)

    matching_issues = [issue for issue in issues if issue.developer_id == polluted.id]
    assert len(matching_issues) == 1
    assert matching_issues[0].unsafe_canonical_name is False
    assert matching_issues[0].unsafe_alias_count == 3
    assert all(issue.developer_id != safe.id for issue in issues)

    apply_result = delete_developer_registry_audit_issues(postgres_session, matching_issues)
    postgres_session.flush()

    remaining_polluted = postgres_session.get(DeveloperRegistry, polluted.id)
    remaining_safe = postgres_session.get(DeveloperRegistry, safe.id)
    remaining_polluted_aliases = postgres_session.execute(
        select(DeveloperAlias.id).where(DeveloperAlias.developer_id == polluted.id)
    ).scalars().all()

    assert apply_result.deleted_canonical_count == 0
    assert apply_result.pruned_alias_count == 3
    assert remaining_polluted is not None
    assert remaining_safe is not None
    assert remaining_polluted_aliases == []


def test_developer_registry_audit_deletes_unsafe_canonical_rows(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("developer_registry"):
        pytest.skip("Apply the evidence layer migration before running developer tests.")

    polluted = DeveloperRegistry(canonical_name="Advisor Associate Holdings Fund")
    postgres_session.add(polluted)
    postgres_session.flush()
    postgres_session.add_all(
        [
            DeveloperAlias(developer_id=polluted.id, alias_name="Funds Venture Properties"),
            DeveloperAlias(developer_id=polluted.id, alias_name="Realty Management Communities"),
            DeveloperAlias(developer_id=polluted.id, alias_name="Residential Housing Partner"),
        ]
    )
    postgres_session.flush()

    issues = audit_developer_registry_token_overlap(postgres_session, min_aliases=3)

    matching_issues = [issue for issue in issues if issue.developer_id == polluted.id]
    assert len(matching_issues) == 1
    assert matching_issues[0].unsafe_canonical_name is True

    apply_result = delete_developer_registry_audit_issues(postgres_session, matching_issues)
    postgres_session.flush()

    remaining_polluted = postgres_session.get(DeveloperRegistry, polluted.id)
    remaining_polluted_aliases = postgres_session.execute(
        select(DeveloperAlias.id).where(DeveloperAlias.developer_id == polluted.id)
    ).scalars().all()

    assert apply_result.deleted_canonical_count == 1
    assert apply_result.pruned_alias_count == 0
    assert remaining_polluted is None
    assert remaining_polluted_aliases == []


def test_canonicalize_registry_entry_merges_duplicate_canonical_row(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("developer_registry"):
        pytest.skip("Apply the evidence layer migration before running developer tests.")

    canonical = DeveloperRegistry(canonical_name=TEST_CIM)
    duplicate = DeveloperRegistry(canonical_name=f"{TEST_CIM} LP", is_top_tier=True)
    postgres_session.add_all([canonical, duplicate])
    postgres_session.flush()

    result = canonicalize_registry_entry(
        postgres_session,
        duplicate.id,
        persist=True,
    )
    postgres_session.flush()

    registry_rows = postgres_session.execute(
        select(DeveloperRegistry)
        .where(
            DeveloperRegistry.canonical_name.in_(
                [
                    TEST_CIM,
                    f"{TEST_CIM} LP",
                ]
            )
        )
        .order_by(DeveloperRegistry.canonical_name)
    ).scalars().all()
    alias_rows = postgres_session.execute(
        select(DeveloperAlias.alias_name)
        .where(DeveloperAlias.alias_name == f"{TEST_CIM} LP")
        .order_by(DeveloperAlias.alias_name)
    ).scalars().all()

    assert result.canonical_name == TEST_CIM
    assert result.registry_merged is True
    assert [row.canonical_name for row in registry_rows] == [TEST_CIM]
    assert registry_rows[0].is_top_tier is True
    assert alias_rows == [f"{TEST_CIM} LP"]


def test_canonicalize_registry_entry_does_not_auto_merge_fuzzy_review_match(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("developer_registry"):
        pytest.skip("Apply the evidence layer migration before running developer tests.")

    canonical = DeveloperRegistry(canonical_name=TEST_CIM)
    fuzzy_duplicate = DeveloperRegistry(canonical_name=TEST_CIM_ALIAS)
    postgres_session.add_all([canonical, fuzzy_duplicate])
    postgres_session.flush()

    result = canonicalize_registry_entry(
        postgres_session,
        fuzzy_duplicate.id,
        persist=True,
    )
    postgres_session.flush()

    registry_rows = postgres_session.execute(
        select(DeveloperRegistry)
        .where(
            DeveloperRegistry.canonical_name.in_(
                [
                    TEST_CIM,
                    TEST_CIM_ALIAS,
                ]
            )
        )
        .order_by(DeveloperRegistry.canonical_name)
    ).scalars().all()

    assert result.match_type == "fuzzy_review"
    assert result.canonical_name == TEST_CIM
    assert result.registry_merged is False
    assert [row.canonical_name for row in registry_rows] == [TEST_CIM_ALIAS, TEST_CIM]


def test_canonicalize_registry_entry_merges_existing_aliases_from_duplicate_row(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("developer_registry"):
        pytest.skip("Apply the evidence layer migration before running developer tests.")

    canonical = DeveloperRegistry(canonical_name=TEST_AJ_CANONICAL)
    duplicate = DeveloperRegistry(canonical_name=TEST_AJ_DUPLICATE)
    postgres_session.add_all([canonical, duplicate])
    postgres_session.flush()
    postgres_session.add(
        DeveloperAlias(
            developer_id=duplicate.id,
            alias_name=TEST_AJ_ALIAS,
        )
    )
    postgres_session.flush()

    result = canonicalize_registry_entry(
        postgres_session,
        duplicate.id,
        persist=True,
    )
    postgres_session.flush()

    registry_rows = postgres_session.execute(
        select(DeveloperRegistry)
        .where(
            DeveloperRegistry.canonical_name.in_(
                [
                    TEST_AJ_CANONICAL,
                    TEST_AJ_DUPLICATE,
                ]
            )
        )
        .order_by(DeveloperRegistry.canonical_name)
    ).scalars().all()
    alias_rows = postgres_session.execute(
        select(DeveloperAlias.alias_name)
        .where(
            DeveloperAlias.alias_name.in_(
                [
                    TEST_AJ_ALIAS,
                    TEST_AJ_DUPLICATE,
                ]
            )
        )
        .order_by(DeveloperAlias.alias_name)
    ).scalars().all()

    assert result.canonical_name == TEST_AJ_CANONICAL
    assert result.registry_merged is True
    assert [row.canonical_name for row in registry_rows] == [TEST_AJ_CANONICAL]
    assert alias_rows == [TEST_AJ_ALIAS, TEST_AJ_DUPLICATE]


def test_canonicalize_project_developers_updates_projects_and_registry(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("developer_registry"):
        pytest.skip("Apply the evidence layer migration before running developer tests.")

    canonical = DeveloperRegistry(canonical_name=TEST_JAMISON)
    duplicate = DeveloperRegistry(canonical_name=TEST_JAMISON_ALIAS)
    project = Project(
        canonical_address="500 WEST TEST STREET LOS ANGELES CA 90012",
        raw_addresses=["500 W Test St"],
        market="test_market",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        developer=TEST_JAMISON_ALIAS,
    )
    postgres_session.add_all([canonical, duplicate, project])
    postgres_session.flush()

    result = canonicalize_project_developers(
        postgres_session,
        market="test_market",
        apply=True,
    )
    postgres_session.flush()

    postgres_session.refresh(project)
    alias_rows = postgres_session.execute(
        select(DeveloperAlias.alias_name).order_by(DeveloperAlias.alias_name)
    ).scalars().all()

    assert result.projects_changed >= 1
    assert project.developer == TEST_JAMISON
    assert TEST_JAMISON_ALIAS in alias_rows


def test_canonicalize_project_developers_does_not_apply_fuzzy_review_match(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("developer_registry"):
        pytest.skip("Apply the evidence layer migration before running developer tests.")

    postgres_session.add(DeveloperRegistry(canonical_name=TEST_CIM))
    project = Project(
        canonical_address="500 WEST REVIEW STREET LOS ANGELES CA 90012",
        raw_addresses=["500 W Review St"],
        market="test_market",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        developer=TEST_CIM_ALIAS,
    )
    postgres_session.add(project)
    postgres_session.flush()

    result = canonicalize_project_developers(
        postgres_session,
        market="test_market",
        apply=True,
    )
    postgres_session.flush()
    postgres_session.refresh(project)

    assert result.fuzzy_review_matches >= 1
    assert result.projects_changed == 0
    assert project.developer == TEST_CIM_ALIAS


def test_canonicalize_project_developers_preserves_researcher_override_value(
    postgres_session: Session,
) -> None:
    inspector = inspect(postgres_session.bind)
    if not inspector.has_table("developer_registry") or not inspector.has_table(
        "researcher_overrides"
    ):
        pytest.skip("Apply the evidence layer migration before running developer tests.")

    canonical = DeveloperRegistry(canonical_name=TEST_JAMISON)
    postgres_session.add(canonical)
    postgres_session.flush()
    postgres_session.add(
        DeveloperAlias(
            developer_id=canonical.id,
            alias_name=TEST_JAMISON_ALIAS,
        )
    )
    project = Project(
        canonical_address="502 WEST OVERRIDE STREET LOS ANGELES CA 90012",
        raw_addresses=["502 W Override St"],
        market="test_market",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        developer=TEST_JAMISON_ALIAS,
    )
    postgres_session.add(project)
    postgres_session.flush()
    postgres_session.add(
        ResearcherOverride(
            project_id=project.id,
            field_name="developer",
            value=TEST_JAMISON_ALIAS,
            mode="until_newer_evidence",
            note="Keep researcher-selected raw developer value.",
        )
    )
    postgres_session.flush()

    result = canonicalize_project_developers(
        postgres_session,
        market="test_market",
        apply=True,
    )
    postgres_session.flush()
    postgres_session.refresh(project)

    assert result.projects_changed == 0
    assert project.developer == TEST_JAMISON_ALIAS


def test_canonicalize_project_developers_does_not_recreate_ignored_category_row(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("developer_registry"):
        pytest.skip("Apply the evidence layer migration before running developer tests.")

    category_count_before = postgres_session.execute(
        select(DeveloperRegistry.id).where(DeveloperRegistry.canonical_name == "Category")
    ).scalars().all()
    project = Project(
        canonical_address="501 WEST TEST STREET LOS ANGELES CA 90012",
        raw_addresses=["501 W Test St"],
        market="test_market",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        developer="Category",
    )
    postgres_session.add(project)
    postgres_session.flush()

    result = canonicalize_project_developers(
        postgres_session,
        market="test_market",
        apply=True,
    )
    postgres_session.flush()
    postgres_session.refresh(project)

    category_count_after = postgres_session.execute(
        select(DeveloperRegistry.id).where(DeveloperRegistry.canonical_name == "Category")
    ).scalars().all()

    assert project.developer == "Category"
    assert result.new_registry_entries == 0
    assert category_count_after == category_count_before


def test_resolve_project_canonicalizes_developer_and_emits_review_flag(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("developer_registry"):
        pytest.skip("Apply the evidence layer migration before running developer tests.")

    postgres_session.add(DeveloperRegistry(canonical_name=TEST_CIM))
    project = Project(
        canonical_address="600 WEST TEST STREET LOS ANGELES CA 90012",
        raw_addresses=["600 W Test St"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        developer="Legacy Dev",
    )
    postgres_session.add(project)
    postgres_session.flush()
    postgres_session.add(
        Evidence(
            project_id=project.id,
            source_type="costar",
            source_tier=3,
            ingest_method="seed_import",
            collected_at=project.created_at,
            extracted_fields={"developer": {"value": TEST_CIM_ALIAS, "confidence": None}},
        )
    )
    postgres_session.flush()

    result = resolve_project(
        project.id,
        postgres_session,
        apply=False,
        write_resolution_log=False,
    )

    assert result.field_resolutions["developer"].value == TEST_CIM_ALIAS
    assert result.field_resolutions["developer"].metadata["canonical_name"] == TEST_CIM
    assert any(
        review_flag.code == "developer_canonicalization_review"
        for review_flag in result.review_flags
    )


def test_resolve_project_flags_fuzzy_review_even_without_developer_field_change(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("developer_registry"):
        pytest.skip("Apply the evidence layer migration before running developer tests.")

    postgres_session.add(DeveloperRegistry(canonical_name=TEST_CIM))
    project = Project(
        canonical_address="601 WEST TEST STREET LOS ANGELES CA 90012",
        raw_addresses=["601 W Test St"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        developer=TEST_CIM,
    )
    postgres_session.add(project)
    postgres_session.flush()
    postgres_session.add(
        Evidence(
            project_id=project.id,
            source_type="costar",
            source_tier=3,
            ingest_method="seed_import",
            collected_at=project.created_at,
            extracted_fields={"developer": {"value": TEST_CIM_ALIAS, "confidence": None}},
        )
    )
    postgres_session.flush()

    result = resolve_project(
        project.id,
        postgres_session,
        apply=False,
        write_resolution_log=False,
    )

    assert result.field_resolutions["developer"].value == TEST_CIM
    assert "developer" not in result.changed_fields
    assert any(
        review_flag.code == "developer_canonicalization_review"
        for review_flag in result.review_flags
    )


def test_resolve_project_apply_does_not_persist_registry_rows(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("developer_registry"):
        pytest.skip("Apply the evidence layer migration before running developer tests.")

    project = Project(
        canonical_address="602 WEST TEST STREET LOS ANGELES CA 90012",
        raw_addresses=["602 W Test St"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
    )
    postgres_session.add(project)
    postgres_session.flush()
    registry_count_before = postgres_session.execute(
        select(DeveloperRegistry.id).order_by(DeveloperRegistry.id)
    ).scalars().all()
    postgres_session.add(
        Evidence(
            project_id=project.id,
            source_type="costar",
            source_tier=3,
            ingest_method="seed_import",
            collected_at=project.created_at,
            extracted_fields={"developer": {"value": TEST_NEW_DEVELOPER, "confidence": None}},
        )
    )
    postgres_session.flush()

    result = resolve_project(
        project.id,
        postgres_session,
        apply=True,
        write_resolution_log=False,
    )
    postgres_session.flush()
    postgres_session.refresh(project)

    registry_count = postgres_session.execute(
        select(DeveloperRegistry.id).order_by(DeveloperRegistry.id)
    ).scalars().all()

    assert project.developer == TEST_NEW_DEVELOPER
    assert registry_count == registry_count_before
    assert any(
        review_flag.code == "developer_registry_new_name"
        for review_flag in result.review_flags
    )


def test_canonicalize_developers_command_reports_counts(
    monkeypatch: pytest.MonkeyPatch,
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("developer_registry"):
        pytest.skip("Apply the evidence layer migration before running developer tests.")

    @contextmanager
    def fake_session_factory() -> Session:
        yield postgres_session

    monkeypatch.setattr("tcg_pipeline.cli.get_session_factory", lambda: fake_session_factory)
    monkeypatch.setattr(
        "tcg_pipeline.cli.canonicalize_project_developers",
        lambda *args, **kwargs: DeveloperCanonicalizationSweepResult(
            registry_rows_scanned=1,
            projects_scanned=1,
            projects_changed=1,
            exact_matches=0,
            fuzzy_auto_matches=1,
            fuzzy_review_matches=0,
            new_registry_entries=0,
        ),
    )
    monkeypatch.setattr("tcg_pipeline.cli._developer_registry_is_empty", lambda session: False)

    result = runner.invoke(
        app,
        [
            "canonicalize-developers",
            "--market",
            "test_market",
            "--apply",
            "--limit",
            "1",
        ],
    )

    assert result.exit_code == 0
    assert "Projects scanned: 1" in result.stdout
    assert "Projects changed: 1" in result.stdout
    assert "Apply mode: True" in result.stdout


def test_canonicalize_developers_command_reports_merge_note(
    monkeypatch: pytest.MonkeyPatch,
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("developer_registry"):
        pytest.skip("Apply the evidence layer migration before running developer tests.")

    postgres_session.add_all(
        [
            DeveloperRegistry(canonical_name=TEST_JAMISON),
            DeveloperRegistry(canonical_name=TEST_JAMISON_ALIAS),
        ]
    )
    postgres_session.flush()

    @contextmanager
    def fake_session_factory() -> Session:
        yield postgres_session

    monkeypatch.setattr("tcg_pipeline.cli.get_session_factory", lambda: fake_session_factory)
    monkeypatch.setattr(
        "tcg_pipeline.cli.canonicalize_project_developers",
        lambda *args, **kwargs: DeveloperCanonicalizationSweepResult(
            registry_rows_scanned=2,
            registry_rows_merged=1,
            projects_scanned=0,
            projects_changed=0,
        ),
    )
    monkeypatch.setattr("tcg_pipeline.cli._developer_registry_is_empty", lambda session: False)

    result = runner.invoke(
        app,
        ["canonicalize-developers", "--market", "test_market", "--apply"],
    )

    assert result.exit_code == 0
    assert "Registry rows merged:" in result.stdout
    assert "Note: registry duplicates were merged during this sweep." in result.stdout


def test_canonicalize_developers_command_reports_shadow_and_bootstrap_notes(
    monkeypatch: pytest.MonkeyPatch,
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("developer_registry"):
        pytest.skip("Apply the evidence layer migration before running developer tests.")

    project = Project(
        canonical_address="703 WEST TEST STREET LOS ANGELES CA 90012",
        raw_addresses=["703 W Test St"],
        market="test_market",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        developer=TEST_CIM_ALIAS,
    )
    postgres_session.add(project)
    postgres_session.flush()

    @contextmanager
    def fake_session_factory() -> Session:
        yield postgres_session

    monkeypatch.setattr("tcg_pipeline.cli.get_session_factory", lambda: fake_session_factory)
    monkeypatch.setattr(
        "tcg_pipeline.cli.canonicalize_project_developers",
        lambda *args, **kwargs: DeveloperCanonicalizationSweepResult(
            registry_rows_scanned=0,
            projects_scanned=1,
            projects_changed=0,
            new_registry_entries=1,
        ),
    )
    monkeypatch.setattr("tcg_pipeline.cli._developer_registry_is_empty", lambda session: True)

    result = runner.invoke(
        app,
        ["canonicalize-developers", "--market", "test_market", "--limit", "1"],
    )

    assert result.exit_code == 0
    assert "Apply mode: False" in result.stdout
    assert "Shadow mode note: canonical developer targets are computed" in result.stdout
    assert "Developer registry bootstrap note:" in result.stdout

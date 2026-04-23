from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from tcg_pipeline.cli import app
from tcg_pipeline.db.models import DeveloperAlias, DeveloperRegistry, Evidence, Project
from tcg_pipeline.developer import canonicalize_project_developers, normalize_developer_name
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

    assert result.field_resolutions["developer"].value == TEST_CIM
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

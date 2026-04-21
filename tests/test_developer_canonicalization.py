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
from tcg_pipeline.developer.registry import (
    canonicalize_developer_name,
    canonicalize_registry_entry,
)
from tcg_pipeline.resolution import resolve_project

runner = CliRunner()


def test_normalize_developer_name_strips_legal_suffixes() -> None:
    assert normalize_developer_name("Jamison Services, LLC") == "JAMISON SERVICES"
    assert normalize_developer_name("The CIM Group LP") == "CIM GROUP"


def test_canonicalize_developer_name_matches_exact_alias(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("developer_registry"):
        pytest.skip("Apply the evidence layer migration before running developer tests.")

    developer = DeveloperRegistry(canonical_name="Jamison Services")
    postgres_session.add(developer)
    postgres_session.flush()
    postgres_session.add(
        DeveloperAlias(
            developer_id=developer.id,
            alias_name="Jamison Services LP",
        )
    )
    postgres_session.flush()

    result = canonicalize_developer_name(
        postgres_session,
        "Jamison Services LP",
        persist=False,
    )

    assert result.canonical_name == "Jamison Services"
    assert result.match_type == "exact_alias"


def test_canonicalize_developer_name_reuses_session_cache(
    monkeypatch: pytest.MonkeyPatch,
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("developer_registry"):
        pytest.skip("Apply the evidence layer migration before running developer tests.")

    postgres_session.add(DeveloperRegistry(canonical_name="Jamison Services"))
    postgres_session.flush()

    call_count = 0
    original_loader = registry_module._load_registry_from_db

    def counting_loader(session: Session):
        nonlocal call_count
        call_count += 1
        return original_loader(session)

    monkeypatch.setattr(registry_module, "_load_registry_from_db", counting_loader)

    canonicalize_developer_name(postgres_session, "Jamison Services", persist=False)
    canonicalize_developer_name(postgres_session, "Jamison Services LP", persist=False)

    assert call_count == 1


def test_canonicalize_developer_name_uses_fuzzy_auto_threshold(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("developer_registry"):
        pytest.skip("Apply the evidence layer migration before running developer tests.")

    postgres_session.add(DeveloperRegistry(canonical_name="Jamison Services"))
    postgres_session.flush()

    result = canonicalize_developer_name(
        postgres_session,
        "Jamison Servics",
        persist=False,
    )

    assert result.canonical_name == "Jamison Services"
    assert result.match_type == "fuzzy_auto"
    assert result.score is not None and result.score >= 90.0


def test_canonicalize_developer_name_uses_fuzzy_review_threshold(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("developer_registry"):
        pytest.skip("Apply the evidence layer migration before running developer tests.")

    postgres_session.add(DeveloperRegistry(canonical_name="CIM Group"))
    postgres_session.flush()

    result = canonicalize_developer_name(
        postgres_session,
        "CIM Grp",
        persist=False,
    )

    assert result.canonical_name == "CIM Group"
    assert result.match_type == "fuzzy_review"
    assert result.score is not None and 75.0 <= result.score < 90.0
    assert result.requires_review is True


def test_canonicalize_registry_entry_merges_duplicate_canonical_row(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("developer_registry"):
        pytest.skip("Apply the evidence layer migration before running developer tests.")

    canonical = DeveloperRegistry(canonical_name="CIM Group")
    duplicate = DeveloperRegistry(canonical_name="CIM Group LP", is_top_tier=True)
    postgres_session.add_all([canonical, duplicate])
    postgres_session.flush()

    result = canonicalize_registry_entry(
        postgres_session,
        duplicate.id,
        persist=True,
    )
    postgres_session.flush()

    registry_rows = postgres_session.execute(
        select(DeveloperRegistry).order_by(DeveloperRegistry.canonical_name)
    ).scalars().all()
    alias_rows = postgres_session.execute(
        select(DeveloperAlias.alias_name).order_by(DeveloperAlias.alias_name)
    ).scalars().all()

    assert result.canonical_name == "CIM Group"
    assert result.registry_merged is True
    assert [row.canonical_name for row in registry_rows] == ["CIM Group"]
    assert registry_rows[0].is_top_tier is True
    assert alias_rows == ["CIM Group LP"]


def test_canonicalize_project_developers_updates_projects_and_registry(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("developer_registry"):
        pytest.skip("Apply the evidence layer migration before running developer tests.")

    canonical = DeveloperRegistry(canonical_name="Jamison Services")
    duplicate = DeveloperRegistry(canonical_name="Jamison Services LP")
    project = Project(
        canonical_address="500 WEST TEST STREET LOS ANGELES CA 90012",
        raw_addresses=["500 W Test St"],
        market="test_market",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        developer="Jamison Services LP",
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
    assert project.developer == "Jamison Services"
    assert "Jamison Services LP" in alias_rows


def test_resolve_project_canonicalizes_developer_and_emits_review_flag(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("developer_registry"):
        pytest.skip("Apply the evidence layer migration before running developer tests.")

    postgres_session.add(DeveloperRegistry(canonical_name="CIM Group"))
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
            extracted_fields={"developer": {"value": "CIM Grp", "confidence": None}},
        )
    )
    postgres_session.flush()

    result = resolve_project(
        project.id,
        postgres_session,
        apply=False,
        write_resolution_log=False,
    )

    assert result.field_resolutions["developer"].value == "CIM Group"
    assert any(
        review_flag.code == "developer_canonicalization_review"
        for review_flag in result.review_flags
    )


def test_resolve_project_flags_fuzzy_review_even_without_developer_field_change(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("developer_registry"):
        pytest.skip("Apply the evidence layer migration before running developer tests.")

    postgres_session.add(DeveloperRegistry(canonical_name="CIM Group"))
    project = Project(
        canonical_address="601 WEST TEST STREET LOS ANGELES CA 90012",
        raw_addresses=["601 W Test St"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        developer="CIM Group",
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
            extracted_fields={"developer": {"value": "CIM Grp", "confidence": None}},
        )
    )
    postgres_session.flush()

    result = resolve_project(
        project.id,
        postgres_session,
        apply=False,
        write_resolution_log=False,
    )

    assert result.field_resolutions["developer"].value == "CIM Group"
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
    postgres_session.add(
        Evidence(
            project_id=project.id,
            source_type="costar",
            source_tier=3,
            ingest_method="seed_import",
            collected_at=project.created_at,
            extracted_fields={"developer": {"value": "Brand New Dev", "confidence": None}},
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
        select(DeveloperRegistry.id)
    ).scalars().all()

    assert project.developer == "Brand New Dev"
    assert registry_count == []
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

    postgres_session.add(DeveloperRegistry(canonical_name="CIM Group"))
    project = Project(
        canonical_address="700 WEST TEST STREET LOS ANGELES CA 90012",
        raw_addresses=["700 W Test St"],
        market="test_market",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        developer="CIM Grp",
    )
    postgres_session.add(project)
    postgres_session.flush()

    @contextmanager
    def fake_session_factory() -> Session:
        yield postgres_session

    monkeypatch.setattr("tcg_pipeline.cli.get_session_factory", lambda: fake_session_factory)

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

    postgres_session.refresh(project)
    assert result.exit_code == 0
    assert "Projects scanned: 1" in result.stdout
    assert "Apply mode: True" in result.stdout
    assert project.developer == "CIM Group"


def test_canonicalize_developers_command_reports_merge_note(
    monkeypatch: pytest.MonkeyPatch,
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("developer_registry"):
        pytest.skip("Apply the evidence layer migration before running developer tests.")

    postgres_session.add_all(
        [
            DeveloperRegistry(canonical_name="Jamison Services"),
            DeveloperRegistry(canonical_name="Jamison Services LP"),
        ]
    )
    postgres_session.flush()

    @contextmanager
    def fake_session_factory() -> Session:
        yield postgres_session

    monkeypatch.setattr("tcg_pipeline.cli.get_session_factory", lambda: fake_session_factory)

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
        developer="CIM Grp",
    )
    postgres_session.add(project)
    postgres_session.flush()

    @contextmanager
    def fake_session_factory() -> Session:
        yield postgres_session

    monkeypatch.setattr("tcg_pipeline.cli.get_session_factory", lambda: fake_session_factory)

    result = runner.invoke(
        app,
        ["canonicalize-developers", "--market", "test_market", "--limit", "1"],
    )

    assert result.exit_code == 0
    assert "Apply mode: False" in result.stdout
    assert "Shadow mode note: canonical developer targets are computed" in result.stdout
    assert "Developer registry bootstrap note:" in result.stdout

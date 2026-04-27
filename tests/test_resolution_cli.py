from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from tcg_pipeline.cli import _fetch_project_ids, app, resolve_all
from tcg_pipeline.db.models import Evidence, PipelineStatus, Project, ResearcherOverride

runner = CliRunner()


def test_resolve_all_command_runs_shadow_mode(
    monkeypatch: pytest.MonkeyPatch,
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("evidence"):
        pytest.skip("Apply the evidence layer migration before running resolution CLI tests.")

    project = Project(
        canonical_address="123 MAIN STREET LOS ANGELES CA 90012",
        raw_addresses=["123 Main St"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        pipeline_status=PipelineStatus.PROPOSED,
    )
    postgres_session.add(project)
    postgres_session.flush()
    postgres_session.add(
        Evidence(
            project_id=project.id,
            source_type="pipedream",
            source_tier=1,
            ingest_method="seed_import",
            collected_at=datetime(2026, 4, 1, tzinfo=UTC),
            evidence_date=date(2026, 4, 1),
            extracted_fields={
                "pipeline_status": {"value": PipelineStatus.PROPOSED.value, "confidence": None}
            },
        )
    )
    postgres_session.flush()

    @contextmanager
    def fake_session_factory() -> Session:
        yield postgres_session

    monkeypatch.setattr("tcg_pipeline.cli.get_session_factory", lambda: fake_session_factory)
    monkeypatch.setattr("tcg_pipeline.cli._developer_registry_is_empty", lambda session: False)

    result = runner.invoke(app, ["resolve-all", "--clear-log", "--limit", "1"])

    assert result.exit_code == 0
    assert "Projects resolved: 1" in result.stdout
    assert "Apply mode: False" in result.stdout
    assert (
        "Shadow mode note: resolution_log stores computed canonical developer values"
        in result.stdout
    )


def test_resolve_all_function_runs_shadow_mode(
    monkeypatch: pytest.MonkeyPatch,
    postgres_session: Session,
    capsys: pytest.CaptureFixture[str],
) -> None:
    if not inspect(postgres_session.bind).has_table("evidence"):
        pytest.skip("Apply the evidence layer migration before running resolution CLI tests.")

    project = Project(
        canonical_address="124 MAIN STREET LOS ANGELES CA 90012",
        raw_addresses=["124 Main St"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        pipeline_status=PipelineStatus.PROPOSED,
    )
    postgres_session.add(project)
    postgres_session.flush()
    postgres_session.add(
        Evidence(
            project_id=project.id,
            source_type="pipedream",
            source_tier=1,
            ingest_method="seed_import",
            collected_at=datetime(2026, 4, 2, tzinfo=UTC),
            evidence_date=date(2026, 4, 2),
            extracted_fields={
                "pipeline_status": {"value": PipelineStatus.PROPOSED.value, "confidence": None}
            },
        )
    )
    postgres_session.flush()

    @contextmanager
    def fake_session_factory() -> Session:
        yield postgres_session

    monkeypatch.setattr("tcg_pipeline.cli.get_session_factory", lambda: fake_session_factory)
    monkeypatch.setattr("tcg_pipeline.cli._developer_registry_is_empty", lambda session: False)

    resolve_all(market="los_angeles", clear_log=True, limit=1)
    captured = capsys.readouterr()

    assert "Projects resolved: 1" in captured.out
    assert "Apply mode: False" in captured.out
    assert (
        "Shadow mode note: resolution_log stores computed canonical developer values"
        in captured.out
    )


def test_detect_contradictions_command_runs_dry_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = UUID("11111111-2222-3333-4444-555555555555")

    class FakeSession:
        flush_calls = 0
        commit_calls = 0
        rollback_calls = 0

        def flush(self) -> None:
            self.flush_calls += 1

        def commit(self) -> None:
            self.commit_calls += 1

        def rollback(self) -> None:
            self.rollback_calls += 1

    fake_session = FakeSession()
    calls: dict[str, list[UUID]] = {}

    @contextmanager
    def fake_session_factory():
        yield fake_session

    def fake_fetch_project_ids(
        _session: FakeSession,
        *,
        market: str | None,
        after_project_id: UUID | None,
        limit: int | None,
        only_with_active_overrides: bool,
    ) -> list[UUID]:
        assert market == "los_angeles"
        assert after_project_id is None
        assert limit == 1
        assert only_with_active_overrides is False
        return [project_id]

    def fake_detect_contradictions(
        _session: FakeSession,
        project_ids: list[UUID],
    ) -> SimpleNamespace:
        calls["project_ids"] = list(project_ids)
        return SimpleNamespace(created_count=1, updated_count=2, invalidated_count=3)

    monkeypatch.setattr("tcg_pipeline.cli.get_session_factory", lambda: fake_session_factory)
    monkeypatch.setattr("tcg_pipeline.cli._fetch_project_ids", fake_fetch_project_ids)
    monkeypatch.setattr(
        "tcg_pipeline.cli.detect_override_contradictions",
        fake_detect_contradictions,
    )

    result = runner.invoke(
        app,
        [
            "detect-contradictions",
            "--market",
            "los_angeles",
            "--limit",
            "1",
            "--batch-size",
            "1",
        ],
    )

    assert result.exit_code == 0
    assert calls["project_ids"] == [project_id]
    assert fake_session.flush_calls == 1
    assert fake_session.commit_calls == 0
    assert fake_session.rollback_calls == 1
    assert "Contradiction review items created: 1" in result.stdout
    assert "Contradiction review items updated: 2" in result.stdout
    assert "Contradiction review items invalidated: 3" in result.stdout
    assert "Apply mode: False" in result.stdout


def test_detect_contradictions_command_runs_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = UUID("11111111-2222-3333-4444-666666666666")

    class FakeSession:
        flush_calls = 0
        commit_calls = 0
        rollback_calls = 0

        def flush(self) -> None:
            self.flush_calls += 1

        def commit(self) -> None:
            self.commit_calls += 1

        def rollback(self) -> None:
            self.rollback_calls += 1

    fake_session = FakeSession()

    @contextmanager
    def fake_session_factory():
        yield fake_session

    def fake_fetch_project_ids(
        _session: FakeSession,
        *,
        market: str | None,
        after_project_id: UUID | None,
        limit: int | None,
        only_with_active_overrides: bool,
    ) -> list[UUID]:
        assert market == "los_angeles"
        assert after_project_id is None
        assert limit == 1
        assert only_with_active_overrides is False
        return [project_id]

    def fake_detect_contradictions(
        _session: FakeSession,
        project_ids: list[UUID],
    ) -> SimpleNamespace:
        assert project_ids == [project_id]
        return SimpleNamespace(created_count=1, updated_count=0, invalidated_count=0)

    monkeypatch.setattr("tcg_pipeline.cli.get_session_factory", lambda: fake_session_factory)
    monkeypatch.setattr("tcg_pipeline.cli._fetch_project_ids", fake_fetch_project_ids)
    monkeypatch.setattr(
        "tcg_pipeline.cli.detect_override_contradictions",
        fake_detect_contradictions,
    )

    result = runner.invoke(
        app,
        [
            "detect-contradictions",
            "--market",
            "los_angeles",
            "--limit",
            "1",
            "--batch-size",
            "1",
            "--apply",
        ],
    )

    assert result.exit_code == 0
    assert fake_session.flush_calls == 1
    assert fake_session.commit_calls == 1
    assert fake_session.rollback_calls == 0
    assert "Apply mode: True" in result.stdout


def test_detect_contradictions_command_can_filter_to_active_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = UUID("11111111-2222-3333-4444-777777777777")

    class FakeSession:
        def flush(self) -> None:
            pass

        def commit(self) -> None:
            pass

        def rollback(self) -> None:
            pass

    fake_session = FakeSession()
    observed: dict[str, bool] = {}

    @contextmanager
    def fake_session_factory():
        yield fake_session

    def fake_fetch_project_ids(
        _session: FakeSession,
        *,
        market: str | None,
        after_project_id: UUID | None,
        limit: int | None,
        only_with_active_overrides: bool,
    ) -> list[UUID]:
        assert market == "los_angeles"
        assert after_project_id is None
        assert limit == 1
        observed["only_with_active_overrides"] = only_with_active_overrides
        return [project_id]

    def fake_detect_contradictions(
        _session: FakeSession,
        project_ids: list[UUID],
    ) -> SimpleNamespace:
        assert project_ids == [project_id]
        return SimpleNamespace(created_count=0, updated_count=0, invalidated_count=0)

    monkeypatch.setattr("tcg_pipeline.cli.get_session_factory", lambda: fake_session_factory)
    monkeypatch.setattr("tcg_pipeline.cli._fetch_project_ids", fake_fetch_project_ids)
    monkeypatch.setattr(
        "tcg_pipeline.cli.detect_override_contradictions",
        fake_detect_contradictions,
    )

    result = runner.invoke(
        app,
        [
            "detect-contradictions",
            "--market",
            "los_angeles",
            "--limit",
            "1",
            "--only-with-overrides",
        ],
    )

    assert result.exit_code == 0
    assert observed["only_with_active_overrides"] is True


def test_fetch_project_ids_can_filter_to_active_overrides(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("researcher_overrides"):
        pytest.skip("Apply the researcher overrides migration before running CLI filter tests.")

    active_project = Project(
        canonical_address="200 ACTIVE OVERRIDE STREET LOS ANGELES CA 90012",
        raw_addresses=["200 Active Override Street"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        pipeline_status=PipelineStatus.PROPOSED,
    )
    cleared_project = Project(
        canonical_address="201 CLEARED OVERRIDE STREET LOS ANGELES CA 90012",
        raw_addresses=["201 Cleared Override Street"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        pipeline_status=PipelineStatus.PROPOSED,
    )
    no_override_project = Project(
        canonical_address="202 NO OVERRIDE STREET LOS ANGELES CA 90012",
        raw_addresses=["202 No Override Street"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        pipeline_status=PipelineStatus.PROPOSED,
    )
    postgres_session.add_all([active_project, cleared_project, no_override_project])
    postgres_session.flush()
    postgres_session.add_all(
        [
            ResearcherOverride(
                project_id=active_project.id,
                field_name="pipeline_status",
                value=PipelineStatus.UNDER_CONSTRUCTION.value,
                mode="until_newer_evidence",
            ),
            ResearcherOverride(
                project_id=cleared_project.id,
                field_name="pipeline_status",
                value=PipelineStatus.UNDER_CONSTRUCTION.value,
                mode="until_newer_evidence",
                cleared_at=datetime(2026, 4, 27, tzinfo=UTC),
            ),
        ]
    )
    postgres_session.flush()

    project_ids = _fetch_project_ids(
        postgres_session,
        market="los_angeles",
        after_project_id=None,
        limit=None,
        only_with_active_overrides=True,
    )

    assert active_project.id in project_ids
    assert cleared_project.id not in project_ids
    assert no_override_project.id not in project_ids

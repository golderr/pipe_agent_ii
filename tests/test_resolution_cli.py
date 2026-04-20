from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from tcg_pipeline.cli import app, resolve_all
from tcg_pipeline.db.models import Evidence, PipelineStatus, Project

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

    result = runner.invoke(app, ["resolve-all", "--clear-log", "--limit", "1"])

    assert result.exit_code == 0
    assert "Projects resolved: 1" in result.stdout
    assert "Apply mode: False" in result.stdout


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

    resolve_all(market="los_angeles", clear_log=True, limit=1)
    captured = capsys.readouterr()

    assert "Projects resolved: 1" in captured.out
    assert "Apply mode: False" in captured.out

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import tcg_pipeline.cli as cli_module
from tcg_pipeline.api.routers import research
from tcg_pipeline.cli import app
from tcg_pipeline.db.models import NewsArticle, ScrapeJob
from tcg_pipeline.settings import Settings
from tcg_pipeline.workers import news_jobs

runner = CliRunner()


class _ScalarResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def scalars(self) -> _ScalarResult:
        return self

    def all(self) -> list[object]:
        return self._rows


class _FakeSession:
    def __init__(self, store: SimpleNamespace) -> None:
        self._store = store
        self._execute_count = 0
        self.committed = False

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def commit(self) -> None:
        self.committed = True

    def get(self, model: type[object], _id: uuid.UUID) -> object | None:
        if model is NewsArticle:
            return self._store.article
        if model is ScrapeJob:
            return self._store.job
        return None

    def execute(self, _statement: object) -> _ScalarResult:
        self._execute_count += 1
        if self._execute_count == 1:
            return _ScalarResult(self._store.references)
        if self._execute_count == 2:
            return _ScalarResult(self._store.agent_runs)
        if self._execute_count == 3:
            return _ScalarResult(self._store.review_items)
        return _ScalarResult([])


def test_news_paste_link_smoke_cli_invokes_api_and_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    article_id = uuid.uuid4()
    job_id = uuid.uuid4()
    force_project_id = uuid.uuid4()
    calls: dict[str, object] = {}
    store = SimpleNamespace(
        article=SimpleNamespace(
            id=article_id,
            title="CLI smoke article",
            fetch_status="fetched",
            triage_status="relevant",
            current_extraction_id=uuid.uuid4(),
            current_extraction_version=1,
        ),
        job=SimpleNamespace(
            id=job_id,
            status="completed",
            error_text=None,
        ),
        references=[SimpleNamespace(id=uuid.uuid4())],
        review_items=[SimpleNamespace(id=uuid.uuid4())],
        agent_runs=[
            SimpleNamespace(
                id=uuid.uuid4(),
                outcome="completed",
                agent_revised_verdict={"decision": "no_change"},
                cost_usd=Decimal("0.010000"),
            )
        ],
    )

    def fake_session_factory() -> _FakeSession:
        return _FakeSession(store)

    def fake_enqueue_paste_a_link_article(
        session: _FakeSession,
        *,
        payload: object,
        user: object,
    ) -> tuple[object, object, bool]:
        calls["payload_url"] = payload.url
        calls["payload_note"] = payload.note
        calls["force_project_id"] = payload.force_project_id
        calls["user_email"] = user.email
        calls["committed_before_return"] = session.committed
        return store.article, store.job, False

    def fake_run_news_paste_a_link_job(scrape_job_id: uuid.UUID) -> None:
        calls["worker_job_id"] = scrape_job_id

    monkeypatch.setattr(
        cli_module,
        "get_settings",
        lambda: Settings(
            database_url="postgresql://user:password@example.com/tcg",
            agent_enabled_for_news=True,
            agent_allow_live_llm=True,
            news_use_legacy_pass3=False,
        ),
    )
    monkeypatch.setattr(cli_module, "get_session_factory", lambda: fake_session_factory)
    monkeypatch.setattr(research, "enqueue_paste_a_link_article", fake_enqueue_paste_a_link_article)
    monkeypatch.setattr(news_jobs, "run_news_paste_a_link_job", fake_run_news_paste_a_link_job)

    result = runner.invoke(
        app,
        [
            "news",
            "paste-link-smoke",
            "https://la.urbanize.city/post/cli-smoke",
            "--note",
            "test note",
            "--force-project-id",
            str(force_project_id),
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls["payload_url"] == "https://la.urbanize.city/post/cli-smoke"
    assert calls["payload_note"] == "test note"
    assert calls["force_project_id"] == force_project_id
    assert calls["user_email"] == "codex-smoke@local"
    assert calls["worker_job_id"] == job_id
    assert "Running paste-link smoke against postgresql://user:***@example.com/tcg" in (
        result.output
    )
    assert (
        "Agent flags: AGENT_ENABLED_FOR_NEWS=True | "
        "AGENT_ALLOW_LIVE_LLM=True | NEWS_USE_LEGACY_PASS3=False"
    ) in result.output
    assert f"Article: {article_id}" in result.output
    assert f"Scrape job: {job_id}" in result.output
    assert "Job status: completed" in result.output
    assert "Agent runs: 1" in result.output

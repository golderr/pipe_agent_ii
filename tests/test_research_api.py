from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from tcg_pipeline.api.auth import AuthenticatedUser
from tcg_pipeline.api.deps import get_db_session
from tcg_pipeline.api.main import create_app
from tcg_pipeline.api.routers import research as research_router
from tcg_pipeline.db.models import (
    NewsArticle,
    NewsFetchStatus,
    NewsSource,
    ScrapeJob,
    ScrapeJobKind,
    ScrapeJobStatus,
)
from tcg_pipeline.settings import Settings

USER_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


class FakeVerifier:
    def verify(self, _token: str) -> AuthenticatedUser:
        return AuthenticatedUser(
            user_id=USER_ID,
            email="allowed@example.com",
            role="authenticated",
            claims={"sub": str(USER_ID), "email": "allowed@example.com", "role": "authenticated"},
        )


def test_create_research_article_writes_article_and_job(
    postgres_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_research_tables(postgres_session)
    enqueued_jobs: list[tuple[uuid.UUID, str]] = []
    monkeypatch.setattr(
        research_router,
        "enqueue_news_job_execution",
        lambda job_id, *, kind, settings: enqueued_jobs.append((job_id, kind)) or True,
    )
    client = _client(postgres_session)

    response = client.post(
        "/research/articles",
        json={
            "url": "https://example.com/story?utm_source=newsletter",
            "note": "Worth checking.",
        },
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["existing_article"] is False
    assert payload["status"] == "queued"
    assert payload["scrape_job_id"] is not None
    article = postgres_session.get(NewsArticle, uuid.UUID(payload["article_id"]))
    job = postgres_session.get(ScrapeJob, uuid.UUID(payload["scrape_job_id"]))
    assert article is not None
    assert job is not None
    assert article.url_canonical == "https://example.com/story"
    assert article.ingested_by_user_id == USER_ID
    assert article.notes == "Worth checking."
    assert job.kind == ScrapeJobKind.NEWS_PASTE_A_LINK.value
    assert job.source_name == "news_paste_a_link"
    assert job.target_payload["article_id"] == str(article.id)
    assert job.progress == {
        "message": "Queued for news article ingest.",
        "queue_backend": "rq",
    }
    assert enqueued_jobs == [(job.id, ScrapeJobKind.NEWS_PASTE_A_LINK.value)]


def test_create_research_article_returns_existing_without_job(
    postgres_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_research_tables(postgres_session)
    enqueue_calls: list[Any] = []
    monkeypatch.setattr(
        research_router,
        "enqueue_news_job_execution",
        lambda *args, **kwargs: enqueue_calls.append((args, kwargs)) or True,
    )
    client = _client(postgres_session)
    request = {
        "url": "https://example.com/duplicate?utm_source=newsletter",
    }
    first = client.post("/research/articles", json=request, headers=_auth_headers())
    second = client.post(
        "/research/articles",
        json={"url": "https://example.com/duplicate"},
        headers=_auth_headers(),
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["existing_article"] is True
    assert second.json()["article_id"] == first.json()["article_id"]
    assert second.json()["scrape_job_id"] is None
    assert len(enqueue_calls) == 1


def test_get_research_article_returns_body_not_raw_html(
    postgres_session: Session,
) -> None:
    _ensure_research_tables(postgres_session)
    client = _client(postgres_session)
    source = _news_source(postgres_session)
    article = NewsArticle(
        news_source_id=source.id,
        url_canonical="https://example.com/detail",
        url_original="https://example.com/detail",
        url_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        fetch_status="fetched",
        raw_html="<html>raw</html>",
        raw_html_hash="rawhash",
        body_text="Article body for admin review.",
        body_text_hash="bodyhash",
        title="Article title",
        ingest_method=ScrapeJobKind.NEWS_PASTE_A_LINK.value,
        ingested_by_user_id=USER_ID,
    )
    postgres_session.add(article)
    postgres_session.flush()

    response = client.get(f"/research/articles/{article.id}", headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["article"]["body_text"] == "Article body for admin review."
    assert payload["article"]["raw_html_hash"] == "rawhash"
    assert "raw_html" not in payload["article"]
    assert payload["scrape_jobs"] == []
    assert payload["extractions"] == []
    assert payload["references"] == []


def test_retry_research_article_fetch_queues_new_job(
    postgres_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_research_tables(postgres_session)
    enqueued_jobs: list[tuple[uuid.UUID, str]] = []
    monkeypatch.setattr(
        research_router,
        "enqueue_news_job_execution",
        lambda job_id, *, kind, settings: enqueued_jobs.append((job_id, kind)) or True,
    )
    client = _client(postgres_session)
    source = _news_source(postgres_session)
    assert source is not None
    article = NewsArticle(
        news_source_id=source.id,
        url_canonical="https://example.com/retry",
        url_original="https://example.com/retry?utm_source=email",
        url_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        fetch_status=NewsFetchStatus.FETCH_FAILED.value,
        fetch_error_text="503 Service Unavailable",
        http_status=503,
        ingest_method=ScrapeJobKind.NEWS_PASTE_A_LINK.value,
        ingested_by_user_id=USER_ID,
    )
    postgres_session.add(article)
    postgres_session.flush()
    original_job = ScrapeJob(
        kind=ScrapeJobKind.NEWS_PASTE_A_LINK.value,
        source_name=source.slug,
        status=ScrapeJobStatus.FAILED,
        target_payload={
            "article_id": str(article.id),
            "url": article.url_original,
            "url_canonical": article.url_canonical,
            "url_hash": article.url_hash,
            "force_project_id": str(uuid.UUID("22222222-2222-2222-2222-222222222222")),
        },
        error_text="503 Service Unavailable",
    )
    postgres_session.add(original_job)
    postgres_session.flush()

    response = client.post(
        f"/research/articles/{article.id}/refetch",
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["article_id"] == str(article.id)
    assert payload["status"] == "queued"
    assert payload["existing_active_job"] is False
    assert payload["scrape_job_id"] is not None
    retry_job = postgres_session.get(ScrapeJob, uuid.UUID(payload["scrape_job_id"]))
    assert retry_job is not None
    assert retry_job.id != original_job.id
    assert retry_job.kind == ScrapeJobKind.NEWS_PASTE_A_LINK.value
    assert retry_job.status == ScrapeJobStatus.QUEUED
    assert retry_job.target_payload == {
        "article_id": str(article.id),
        "url": article.url_original,
        "url_canonical": article.url_canonical,
        "url_hash": article.url_hash,
        "force_project_id": "22222222-2222-2222-2222-222222222222",
    }
    assert retry_job.progress == {
        "message": "Queued for news article refetch.",
        "queue_backend": "rq",
    }
    postgres_session.refresh(article)
    assert article.fetch_status == NewsFetchStatus.PENDING.value
    assert article.fetch_error_text is None
    assert article.http_status is None
    assert enqueued_jobs == [(retry_job.id, ScrapeJobKind.NEWS_PASTE_A_LINK.value)]


def test_retry_research_article_fetch_reuses_active_job(
    postgres_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_research_tables(postgres_session)
    enqueue_calls: list[Any] = []
    monkeypatch.setattr(
        research_router,
        "enqueue_news_job_execution",
        lambda *args, **kwargs: enqueue_calls.append((args, kwargs)) or True,
    )
    client = _client(postgres_session)
    source = _news_source(postgres_session)
    assert source is not None
    article = NewsArticle(
        news_source_id=source.id,
        url_canonical="https://example.com/retry-active",
        url_original="https://example.com/retry-active",
        url_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        fetch_status=NewsFetchStatus.FETCH_FAILED.value,
        fetch_error_text="timeout",
        ingest_method=ScrapeJobKind.NEWS_PASTE_A_LINK.value,
        ingested_by_user_id=USER_ID,
    )
    postgres_session.add(article)
    postgres_session.flush()
    active_job = ScrapeJob(
        kind=ScrapeJobKind.NEWS_PASTE_A_LINK.value,
        source_name=source.slug,
        status=ScrapeJobStatus.QUEUED,
        target_payload={
            "article_id": str(article.id),
            "url": article.url_original,
            "url_canonical": article.url_canonical,
            "url_hash": article.url_hash,
            "force_project_id": None,
        },
    )
    postgres_session.add(active_job)
    postgres_session.flush()

    response = client.post(
        f"/research/articles/{article.id}/refetch",
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "article_id": str(article.id),
        "scrape_job_id": str(active_job.id),
        "status": "queued",
        "existing_active_job": True,
    }
    assert enqueue_calls == []


def test_retry_research_article_fetch_rejects_non_terminal_status(
    postgres_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_research_tables(postgres_session)
    monkeypatch.setattr(
        research_router,
        "enqueue_news_job_execution",
        lambda *args, **kwargs: True,
    )
    client = _client(postgres_session)
    source = _news_source(postgres_session)
    assert source is not None
    article = NewsArticle(
        news_source_id=source.id,
        url_canonical="https://example.com/already-fetched",
        url_original="https://example.com/already-fetched",
        url_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        fetch_status=NewsFetchStatus.FETCHED.value,
        ingest_method=ScrapeJobKind.NEWS_PASTE_A_LINK.value,
        ingested_by_user_id=USER_ID,
    )
    postgres_session.add(article)
    postgres_session.flush()

    response = client.post(
        f"/research/articles/{article.id}/refetch",
        headers=_auth_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Article fetch is not in a retryable terminal state."


def _client(postgres_session: Session) -> TestClient:
    app = create_app(
        settings=Settings(
            app_env="test",
            database_url=None,
            allowed_emails="allowed@example.com",
            redis_url="redis://example.test:6379/0",
        ),
        jwt_verifier=FakeVerifier(),
    )
    app.dependency_overrides[get_db_session] = lambda: postgres_session
    return TestClient(app)


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer valid-token"}


def _ensure_research_tables(postgres_session: Session) -> None:
    inspector = inspect(postgres_session.bind)
    required_tables = {"news_articles", "news_sources", "scrape_jobs"}
    missing = [
        table_name for table_name in required_tables if not inspector.has_table(table_name)
    ]
    if missing:
        pytest.skip(f"Apply Phase D migrations before running research API tests: {missing}")
    if _news_source(postgres_session) is None:
        pytest.skip("Apply migration 202604290021 before running research API tests.")


def _news_source(postgres_session: Session) -> NewsSource | None:
    return postgres_session.execute(
        select(NewsSource).where(NewsSource.slug == "news_paste_a_link")
    ).scalar_one_or_none()

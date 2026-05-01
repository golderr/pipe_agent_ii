from __future__ import annotations

import argparse
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import func, select

from tcg_pipeline.db.connection import get_session_factory
from tcg_pipeline.db.models import (
    NewsArticle,
    NewsExtraction,
    NewsProjectReference,
    NewsSource,
    ScrapeJob,
    ScrapeJobKind,
    ScrapeJobStatus,
    ScrapeTriggerType,
    SourceRun,
)
from tcg_pipeline.news.collectors import DiscoveredArticleUrl, PoliteNewsCollector
from tcg_pipeline.news.urls import canonicalize_news_url
from tcg_pipeline.settings import get_settings
from tcg_pipeline.workers.news_jobs import run_news_scrape_job

FIXTURE_PATH = Path("tests/fixtures/news/urbanize_la/pass1_validation_articles.json")
DEFAULT_OUTPUT_DIR = Path("data/output")
SMOKE_QUERY_KEY = "tcg_d6_smoke"


class FixedUrbanizeSmokeCollector:
    def __init__(self, source: NewsSource, urls: list[str]) -> None:
        self._inner = PoliteNewsCollector(source)
        self._urls = urls

    def discover_incremental_urls(self, *, since: datetime | None = None):
        return [
            DiscoveredArticleUrl(
                url=url,
                discovered_via="d6_smoke_fixture",
                published_at=None,
            )
            for url in self._urls
        ]

    def fetch_article(self, url: str):
        return self._inner.fetch_article(url)

    def close(self) -> None:
        self._inner.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the D.6 Urbanize staging smoke test through the scheduled news path."
    )
    parser.add_argument("--source-slug", default="urbanize_la")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument(
        "--token",
        default=datetime.now(UTC).strftime("d6-smoke-%Y%m%d-%H%M%S"),
        help="Stable query token used to make smoke URLs distinct and rerunnable.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for the JSON smoke report.",
    )
    parser.add_argument(
        "--allow-non-staging",
        action="store_true",
        help="Allow running when APP_ENV is not 'staging'.",
    )
    args = parser.parse_args()

    settings = get_settings()
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required for the D.6 smoke test.")
    if settings.app_env != "staging" and not args.allow_non_staging:
        raise RuntimeError(
            "Refusing to run outside APP_ENV=staging without --allow-non-staging. "
            f"Current APP_ENV is {settings.app_env!r}."
        )

    fixture_rows = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    selected_rows = fixture_rows[: args.limit]
    smoke_urls = [_with_smoke_query(row["url"], args.token) for row in selected_rows]

    session_factory = get_session_factory()
    with session_factory() as session:
        source = session.execute(
            select(NewsSource).where(NewsSource.slug == args.source_slug)
        ).scalar_one_or_none()
        if source is None:
            raise RuntimeError(f"Missing news source: {args.source_slug}")
        if not source.active:
            raise RuntimeError(f"News source is inactive: {args.source_slug}")
        job = ScrapeJob(
            jurisdiction_id=source.jurisdiction_id,
            kind=ScrapeJobKind.NEWS_SCRAPE.value,
            source_name=source.slug,
            trigger_type=ScrapeTriggerType.SCHEDULED,
            status=ScrapeJobStatus.QUEUED,
            target_payload={
                "news_source_id": str(source.id),
                "scheduled_for": datetime.now(UTC).isoformat(),
                "d6_smoke_token": args.token,
                "d6_smoke_source_urls": [row["url"] for row in selected_rows],
            },
            progress={"message": "Queued D.6 Urbanize smoke test."},
        )
        session.add(job)
        session.commit()
        job_id = job.id

    def collector_factory(source_snapshot: NewsSource) -> FixedUrbanizeSmokeCollector:
        return FixedUrbanizeSmokeCollector(source_snapshot, smoke_urls)

    run_news_scrape_job(job_id, collector_factory=collector_factory)

    report = _build_report(
        job_id=job_id,
        source_slug=args.source_slug,
        token=args.token,
        fixture_rows=selected_rows,
        smoke_urls=smoke_urls,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / f"d6_urbanize_smoke_{args.token}.json"
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"Report written: {output_path}")


def _build_report(
    *,
    job_id: uuid.UUID,
    source_slug: str,
    token: str,
    fixture_rows: list[dict],
    smoke_urls: list[str],
) -> dict:
    session_factory = get_session_factory()
    with session_factory() as session:
        job = session.get(ScrapeJob, job_id)
        source_run = session.get(SourceRun, job.source_run_id) if job else None
        source = session.execute(
            select(NewsSource).where(NewsSource.slug == source_slug)
        ).scalar_one()
        article_reports = []
        for fixture, smoke_url in zip(fixture_rows, smoke_urls, strict=True):
            canonical = canonicalize_news_url(smoke_url, source_slug=source_slug)
            article = session.execute(
                select(NewsArticle).where(NewsArticle.url_hash == canonical.url_hash)
            ).scalar_one_or_none()
            article_reports.append(
                _article_report(
                    session,
                    fixture=fixture,
                    smoke_url=smoke_url,
                    source=source,
                    article=article,
                )
            )
        return {
            "app_env": get_settings().app_env,
            "job_id": str(job_id),
            "job_status": job.status.value if job else None,
            "job_error_text": job.error_text if job else None,
            "job_progress": job.progress if job else None,
            "source_run_id": str(source_run.id) if source_run else None,
            "source_run": (
                {
                    "records_pulled": source_run.records_pulled,
                    "rows_inserted": source_run.rows_inserted,
                    "rows_updated": source_run.rows_updated,
                    "rows_unchanged": source_run.rows_unchanged,
                    "block_like_failure_count": source_run.block_like_failure_count,
                    "transient_failure_count": source_run.transient_failure_count,
                    "cost_cap_skipped_count": source_run.cost_cap_skipped_count,
                    "new_matches": source_run.new_matches,
                    "updates_found": source_run.updates_found,
                    "new_candidates": source_run.new_candidates,
                    "errors": source_run.errors,
                }
                if source_run
                else None
            ),
            "source_slug": source_slug,
            "token": token,
            "articles": article_reports,
        }


def _article_report(
    session,
    *,
    fixture: dict,
    smoke_url: str,
    source: NewsSource,
    article: NewsArticle | None,
) -> dict:
    if article is None:
        return {
            "slug": fixture["slug"],
            "source_url": fixture["url"],
            "smoke_url": smoke_url,
            "article_id": None,
            "error": "article_not_created",
        }
    triage = _latest_extraction(session, article.id, "triage")
    extraction = _latest_extraction(session, article.id, "extraction")
    reextraction = _latest_extraction(session, article.id, "reextraction")
    reference_count = session.execute(
        select(func.count())
        .select_from(NewsProjectReference)
        .where(NewsProjectReference.article_id == article.id)
    ).scalar_one()
    linked_reference_count = session.execute(
        select(func.count())
        .select_from(NewsProjectReference)
        .where(
            NewsProjectReference.article_id == article.id,
            NewsProjectReference.review_item_id.is_not(None),
        )
    ).scalar_one()
    return {
        "slug": fixture["slug"],
        "source_url": fixture["url"],
        "smoke_url": smoke_url,
        "article_id": str(article.id),
        "source_slug": source.slug,
        "source_id_matches_urbanize": article.news_source_id == source.id,
        "fetch_status": article.fetch_status,
        "http_status": article.http_status,
        "title": article.title,
        "triage_status": article.triage_status,
        "triage_parse_status": triage.parse_status if triage else None,
        "triage_reason": (
            (triage.output_json or {}).get("reason")
            if triage and isinstance(triage.output_json, dict)
            else None
        ),
        "extraction_parse_status": extraction.parse_status if extraction else None,
        "extraction_relevance": (
            (extraction.output_json or {}).get("relevance")
            if extraction and isinstance(extraction.output_json, dict)
            else None
        ),
        "reextraction_parse_status": reextraction.parse_status if reextraction else None,
        "reference_count": reference_count,
        "linked_reference_count": linked_reference_count,
        "current_extraction_id": (
            str(article.current_extraction_id) if article.current_extraction_id else None
        ),
    }


def _latest_extraction(session, article_id: uuid.UUID, pass_name: str) -> NewsExtraction | None:
    return session.execute(
        select(NewsExtraction)
        .where(
            NewsExtraction.article_id == article_id,
            NewsExtraction.pass_name == pass_name,
        )
        .order_by(NewsExtraction.created_at.desc(), NewsExtraction.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _with_smoke_query(url: str, token: str) -> str:
    parsed = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)]
    query.append((SMOKE_QUERY_KEY, token))
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(query, doseq=True),
            "",
        )
    )


if __name__ == "__main__":
    main()

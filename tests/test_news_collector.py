from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from tcg_pipeline.db.models import NewsFetchStatus, NewsSource
from tcg_pipeline.news.collectors import (
    AdvancedFetchRequiredError,
    PoliteFetchError,
    PoliteNewsCollector,
    RobotsDisallowedError,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "news"


def test_polite_collector_discovers_rss_urls_from_source_config() -> None:
    feed_xml = (FIXTURE_DIR / "la_yimby" / "feed.xml").read_text(encoding="utf-8")
    collector = PoliteNewsCollector(
        _source(
            "la_yimby_fixture",
            {
                "hosts": ["layimby.com"],
                "rss_urls": ["https://layimby.com/feed"],
                "robots_url": "https://layimby.com/robots.txt",
                "rate_limit_seconds": 0,
            },
        ),
        client=_client(
            {
                "https://layimby.com/robots.txt": "User-agent: *\nAllow: /\n",
                "https://layimby.com/feed": feed_xml,
            }
        ),
    )

    discovered = collector.discover_incremental_urls(
        since=datetime(2026, 4, 1, tzinfo=UTC)
    )

    assert len(discovered) == 1
    assert discovered[0].url == (
        "https://layimby.com/2026/04/"
        "permits-filed-for-apartments-at-1234-sample-avenue-in-los-angeles.html"
    )
    assert discovered[0].discovered_via == "rss"
    assert discovered[0].published_at == datetime(2026, 4, 30, 13, 5, tzinfo=UTC)


def test_polite_collector_discovers_sitemap_backfill_urls_since_cutoff() -> None:
    sitemap_index = """
    <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://la.urbanize.city/sitemap.xml?page=1</loc></sitemap>
    </sitemapindex>
    """
    sitemap_page = """
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url>
        <loc>https://la.urbanize.city/post/recent-project</loc>
        <lastmod>2026-04-30T13:05:00Z</lastmod>
      </url>
      <url>
        <loc>https://la.urbanize.city/post/old-project</loc>
        <lastmod>2024-01-01T00:00:00Z</lastmod>
      </url>
    </urlset>
    """
    collector = PoliteNewsCollector(
        _source(
            "urbanize_la",
            {
                "hosts": ["la.urbanize.city"],
                "sitemap_urls": ["https://la.urbanize.city/sitemap.xml"],
                "robots_url": "https://la.urbanize.city/robots.txt",
                "rate_limit_seconds": 0,
            },
        ),
        client=_client(
            {
                "https://la.urbanize.city/robots.txt": "User-agent: *\nAllow: /\n",
                "https://la.urbanize.city/sitemap.xml": sitemap_index,
                "https://la.urbanize.city/sitemap.xml?page=1": sitemap_page,
            }
        ),
    )

    discovered = collector.discover_backfill_urls(
        since=datetime(2025, 5, 1, tzinfo=UTC)
    )

    assert [item.url for item in discovered] == ["https://la.urbanize.city/post/recent-project"]
    assert discovered[0].last_modified_at == datetime(2026, 4, 30, 13, 5, tzinfo=UTC)


def test_polite_collector_fetches_wordpress_like_article_fixture() -> None:
    article_html = (FIXTURE_DIR / "la_yimby" / "article.html").read_text(encoding="utf-8")
    collector = PoliteNewsCollector(
        _source(
            "la_yimby_fixture",
            {
                "hosts": ["layimby.com"],
                "fetch_path": "polite",
                "robots_url": "https://layimby.com/robots.txt",
                "rate_limit_seconds": 0,
            },
        ),
        client=_client(
            {
                "https://layimby.com/robots.txt": "User-agent: *\nAllow: /\n",
                "https://layimby.com/2026/04/sample.html": article_html,
            }
        ),
    )

    result = collector.fetch_article("https://layimby.com/2026/04/sample.html")

    assert result.fetch_status == NewsFetchStatus.FETCHED.value
    assert result.title == "Permits Filed For Apartments At 1234 Sample Avenue In Los Angeles"
    assert "88-unit apartment project" in (result.body_text or "")


def test_polite_collector_honors_robots_disallow() -> None:
    collector = PoliteNewsCollector(
        _source(
            "blocked_fixture",
            {
                "rss_urls": ["https://example.com/feed"],
                "robots_url": "https://example.com/robots.txt",
                "rate_limit_seconds": 0,
            },
        ),
        client=_client({"https://example.com/robots.txt": "User-agent: *\nDisallow: /\n"}),
    )

    with pytest.raises(RobotsDisallowedError):
        collector.discover_incremental_urls()


def test_polite_collector_surfaces_retry_after_for_block_like_status() -> None:
    collector = PoliteNewsCollector(
        _source(
            "rate_limited_fixture",
            {
                "rss_urls": ["https://example.com/feed"],
                "robots_url": "https://example.com/robots.txt",
                "rate_limit_seconds": 0,
            },
        ),
        client=_client(
            {
                "https://example.com/robots.txt": "User-agent: *\nAllow: /\n",
                "https://example.com/feed": httpx.Response(
                    429,
                    headers={"Retry-After": "120"},
                    text="rate limited",
                ),
            }
        ),
    )

    with pytest.raises(PoliteFetchError) as exc_info:
        collector.discover_incremental_urls()

    assert exc_info.value.block_like is True
    assert exc_info.value.status_code == 429
    assert exc_info.value.retry_after_seconds == 120


def test_polite_collector_rejects_deferred_advanced_fetch_path() -> None:
    collector = PoliteNewsCollector(
        _source("advanced_fixture", {"fetch_path": "advanced"}),
        client=_client({}),
    )

    with pytest.raises(AdvancedFetchRequiredError):
        collector.fetch_article("https://example.com/paywalled")


def _source(slug: str, config: dict) -> NewsSource:
    return NewsSource(
        slug=slug,
        name=slug.replace("_", " ").title(),
        base_url="https://example.com",
        collector_class="PoliteNewsCollector",
        active=True,
        config=config,
    )


def _client(responses_by_url: dict[str, str | httpx.Response]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        response = responses_by_url.get(str(request.url))
        if isinstance(response, httpx.Response):
            return response
        if response is None:
            return httpx.Response(404, text="missing")
        return httpx.Response(200, text=response)

    return httpx.Client(transport=httpx.MockTransport(handler))

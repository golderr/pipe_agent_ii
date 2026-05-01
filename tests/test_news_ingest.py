from __future__ import annotations

from datetime import UTC, datetime

import httpx

from tcg_pipeline.db.models import NewsFetchStatus
from tcg_pipeline.news.ingest import fetch_article_pass0
from tcg_pipeline.news.urls import canonicalize_news_url, source_slug_for_url


def test_canonicalize_news_url_strips_tracking_and_uses_fallback_source() -> None:
    canonical = canonicalize_news_url(
        "HTTPS://www.bizjournals.com/losangeles/news/story/?utm_source=x&b=2&a=1#top"
    )

    assert canonical.canonical_url == (
        "https://www.bizjournals.com/losangeles/news/story?a=1&b=2"
    )
    assert canonical.source_slug == "news_paste_a_link"
    assert len(canonical.url_hash) == 64


def test_source_slug_for_url_uses_configured_host_routes() -> None:
    assert (
        source_slug_for_url(
            "https://la.urbanize.city/post/project",
            host_routes={"la.urbanize.city": "urbanize_la"},
        )
        == "urbanize_la"
    )


def test_canonicalize_news_url_falls_back_to_paste_source() -> None:
    canonical = canonicalize_news_url("https://example.com/articles/1?utm_medium=email")

    assert canonical.canonical_url == "https://example.com/articles/1"
    assert canonical.source_slug == "news_paste_a_link"


def test_fetch_article_pass0_extracts_text_and_metadata() -> None:
    html = """
    <html lang="en">
      <head>
        <title>Fallback title</title>
        <meta property="article:section" content="Real Estate">
        <meta property="article:tag" content="Housing">
        <script type="application/ld+json">
        {
          "@type": "NewsArticle",
          "headline": "Developer breaks ground on Echo Park apartments",
          "author": {"name": "Ava Reporter"},
          "datePublished": "2026-04-28T13:30:00-07:00",
          "keywords": ["apartments", "Echo Park"],
          "identifier": "abc-123"
        }
        </script>
      </head>
      <body>
        <article>
          <p>Helio Group broke ground on a 120-unit apartment project in Echo Park.</p>
          <p>The project is expected to deliver in 2028 after city approvals.</p>
        </article>
      </body>
    </html>
    """
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, text=html))
    )

    result = fetch_article_pass0("https://example.com/story", client=client)

    assert result.fetch_status == NewsFetchStatus.FETCHED.value
    assert result.http_status == 200
    assert result.title == "Developer breaks ground on Echo Park apartments"
    assert result.byline_author == "Ava Reporter"
    assert result.published_at == datetime(2026, 4, 28, 20, 30, tzinfo=UTC)
    assert result.publication_section == "Real Estate"
    assert result.tags == ["apartments", "Echo Park", "Housing"]
    assert result.external_article_id == "abc-123"
    assert "120-unit apartment project" in (result.body_text or "")
    assert result.raw_html_hash is not None
    assert result.body_text_hash is not None


def test_fetch_article_pass0_marks_dead_link() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(404, text="missing"))
    )

    result = fetch_article_pass0("https://example.com/missing", client=client)

    assert result.fetch_status == NewsFetchStatus.DEAD_LINK.value
    assert result.http_status == 404
    assert result.error_text == "Article returned HTTP 404."

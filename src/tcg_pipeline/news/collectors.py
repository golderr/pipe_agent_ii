from __future__ import annotations

import time
import urllib.robotparser
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx

from tcg_pipeline.db.models import NewsSource
from tcg_pipeline.news.ingest import (
    DEFAULT_USER_AGENT,
    ArticleFetchResult,
    fetch_article_pass0,
)
from tcg_pipeline.news.urls import host_matches_route

POLITE_FETCH_PATH = "polite"
ADVANCED_FETCH_PATH = "advanced"
SUPPORTED_FETCH_PATHS = frozenset({POLITE_FETCH_PATH, ADVANCED_FETCH_PATH})


@dataclass(frozen=True, slots=True)
class DiscoveredArticleUrl:
    url: str
    discovered_via: str
    published_at: datetime | None = None
    last_modified_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PoliteFetchError(Exception):
    message: str
    status_code: int | None = None
    retry_after_seconds: int | None = None
    block_like: bool = False

    def __str__(self) -> str:
        return self.message


class AdvancedFetchRequiredError(RuntimeError):
    pass


class RobotsDisallowedError(RuntimeError):
    pass


class PoliteNewsCollector:
    def __init__(
        self,
        source: NewsSource,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.source = source
        self.config = source.config if isinstance(source.config, dict) else {}
        self.user_agent = str(self.config.get("user_agent") or DEFAULT_USER_AGENT)
        self.rate_limit_seconds = float(self.config.get("rate_limit_seconds") or 0.0)
        self._client = client or httpx.Client(headers={"User-Agent": self.user_agent})
        self._owns_client = client is None
        self._last_request_at_by_host: dict[str, float] = {}
        self._conditional_headers_by_url: dict[str, dict[str, str]] = {}
        self._robots_parser: urllib.robotparser.RobotFileParser | None = None
        self._robots_loaded_at: float | None = None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def discover_incremental_urls(
        self,
        *,
        since: datetime | None = None,
    ) -> list[DiscoveredArticleUrl]:
        discovered: list[DiscoveredArticleUrl] = []
        for feed_url in self._string_list_config("rss_urls"):
            feed_text = self._fetch_text(feed_url)
            if feed_text is None:
                continue
            discovered.extend(_parse_feed_urls(feed_text, discovered_via="rss", since=since))
        return _dedupe_discovered_urls(discovered)

    def discover_backfill_urls(
        self,
        *,
        since: datetime | None = None,
    ) -> list[DiscoveredArticleUrl]:
        discovered: list[DiscoveredArticleUrl] = []
        visited_sitemaps: set[str] = set()
        for sitemap_url in self._string_list_config("sitemap_urls"):
            discovered.extend(
                self._discover_sitemap_urls(
                    sitemap_url,
                    since=since,
                    visited_sitemaps=visited_sitemaps,
                )
            )
        return _dedupe_discovered_urls(discovered)

    def fetch_article(self, url: str) -> ArticleFetchResult:
        fetch_path = str(self.config.get("fetch_path") or POLITE_FETCH_PATH)
        if fetch_path == ADVANCED_FETCH_PATH:
            raise AdvancedFetchRequiredError(
                f"Source '{self.source.slug}' requires advanced fetching."
            )
        if fetch_path != POLITE_FETCH_PATH:
            raise ValueError(f"Unsupported fetch_path '{fetch_path}' for {self.source.slug}.")
        self._ensure_can_fetch(url)
        self._respect_rate_limit(url)
        # Conditional GET is discovery-only for now; article refetch/update policy is D.late work.
        return fetch_article_pass0(url, client=self._client, user_agent=self.user_agent)

    def _discover_sitemap_urls(
        self,
        sitemap_url: str,
        *,
        since: datetime | None,
        visited_sitemaps: set[str],
    ) -> list[DiscoveredArticleUrl]:
        if sitemap_url in visited_sitemaps:
            return []
        visited_sitemaps.add(sitemap_url)
        sitemap_text = self._fetch_text(sitemap_url)
        if sitemap_text is None:
            return []
        root = ElementTree.fromstring(sitemap_text)
        root_name = _local_name(root.tag)
        if root_name == "sitemapindex":
            discovered: list[DiscoveredArticleUrl] = []
            for sitemap in root:
                if _local_name(sitemap.tag) != "sitemap":
                    continue
                loc = _child_text(sitemap, "loc")
                if loc:
                    discovered.extend(
                        self._discover_sitemap_urls(
                            loc,
                            since=since,
                            visited_sitemaps=visited_sitemaps,
                        )
                    )
            return discovered
        if root_name != "urlset":
            return []
        return [
            discovered
            for discovered in _parse_sitemap_urlset(sitemap_text, since=since)
            if self._url_host_allowed(discovered.url)
        ]

    def _fetch_text(self, url: str) -> str | None:
        self._ensure_can_fetch(url)
        self._respect_rate_limit(url)
        headers = {"User-Agent": self.user_agent}
        headers.update(self._conditional_headers_by_url.get(url, {}))
        response = self._client.get(url, headers=headers, follow_redirects=True)
        if response.status_code == 304:
            return None
        if response.status_code in {429, 503}:
            raise PoliteFetchError(
                f"Source '{self.source.slug}' returned HTTP {response.status_code}.",
                status_code=response.status_code,
                retry_after_seconds=_parse_retry_after(response.headers.get("Retry-After")),
                block_like=True,
            )
        if response.status_code in {401, 403}:
            raise PoliteFetchError(
                f"Source '{self.source.slug}' returned HTTP {response.status_code}.",
                status_code=response.status_code,
                block_like=True,
            )
        if response.status_code >= 500:
            raise PoliteFetchError(
                f"Source '{self.source.slug}' returned HTTP {response.status_code}.",
                status_code=response.status_code,
            )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        self._store_conditional_headers(url, response)
        return response.text

    def _store_conditional_headers(self, url: str, response: httpx.Response) -> None:
        conditional_headers: dict[str, str] = {}
        if response.headers.get("ETag"):
            conditional_headers["If-None-Match"] = response.headers["ETag"]
        if response.headers.get("Last-Modified"):
            conditional_headers["If-Modified-Since"] = response.headers["Last-Modified"]
        if conditional_headers:
            self._conditional_headers_by_url[url] = conditional_headers

    def _ensure_can_fetch(self, url: str) -> None:
        robots_parser = self._load_robots_parser()
        if robots_parser is None:
            return
        if not robots_parser.can_fetch(self.user_agent, url):
            raise RobotsDisallowedError(f"Robots.txt disallows fetching {url}.")

    def _load_robots_parser(self) -> urllib.robotparser.RobotFileParser | None:
        robots_url = self.config.get("robots_url")
        if not isinstance(robots_url, str) or not robots_url:
            return None
        now = time.monotonic()
        ttl_seconds = float(self.config.get("robots_cache_ttl_seconds") or 86400)
        if (
            self._robots_parser is not None
            and self._robots_loaded_at is not None
            and now - self._robots_loaded_at < ttl_seconds
        ):
            return self._robots_parser
        response = self._client.get(
            robots_url,
            headers={"User-Agent": self.user_agent},
            follow_redirects=True,
        )
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(robots_url)
        if response.status_code == 404:
            parser.parse([])
        else:
            response.raise_for_status()
            parser.parse(response.text.splitlines())
        crawl_delay = parser.crawl_delay(self.user_agent)
        if crawl_delay is not None:
            self.rate_limit_seconds = max(self.rate_limit_seconds, float(crawl_delay))
        self._robots_parser = parser
        self._robots_loaded_at = now
        return parser

    def _respect_rate_limit(self, url: str) -> None:
        delay_seconds = self.rate_limit_seconds
        if delay_seconds <= 0:
            return
        hostname = urlsplit(url).hostname or ""
        if not hostname:
            return
        now = time.monotonic()
        last_request_at = self._last_request_at_by_host.get(hostname)
        if last_request_at is not None:
            wait_seconds = delay_seconds - (now - last_request_at)
            if wait_seconds > 0:
                time.sleep(wait_seconds)
                now = time.monotonic()
        self._last_request_at_by_host[hostname] = now

    def _url_host_allowed(self, url: str) -> bool:
        configured_hosts = self._string_list_config("hosts")
        if not configured_hosts:
            return True
        hostname = (urlsplit(url).hostname or "").lower()
        return any(host_matches_route(hostname, host) for host in configured_hosts)

    def _string_list_config(self, key: str) -> list[str]:
        values = self.config.get(key)
        if not isinstance(values, list):
            return []
        return [value for value in values if isinstance(value, str) and value.strip()]


def _parse_feed_urls(
    feed_text: str,
    *,
    discovered_via: str,
    since: datetime | None,
) -> list[DiscoveredArticleUrl]:
    root = ElementTree.fromstring(feed_text)
    discovered: list[DiscoveredArticleUrl] = []
    for item in root.iter():
        item_name = _local_name(item.tag)
        if item_name not in {"item", "entry"}:
            continue
        url = _feed_item_url(item)
        if not url:
            continue
        published_at = _parse_datetime(
            _child_text(item, "pubDate")
            or _child_text(item, "published")
            or _child_text(item, "updated")
        )
        if since is not None and published_at is not None and published_at < _aware(since):
            continue
        discovered.append(
            DiscoveredArticleUrl(
                url=url,
                discovered_via=discovered_via,
                published_at=published_at,
            )
        )
    return discovered


def _parse_sitemap_urlset(
    sitemap_text: str,
    *,
    since: datetime | None,
) -> list[DiscoveredArticleUrl]:
    root = ElementTree.fromstring(sitemap_text)
    discovered: list[DiscoveredArticleUrl] = []
    for url_node in root:
        if _local_name(url_node.tag) != "url":
            continue
        loc = _child_text(url_node, "loc")
        if not loc:
            continue
        last_modified_at = _parse_datetime(_child_text(url_node, "lastmod"))
        if since is not None and last_modified_at is not None and last_modified_at < _aware(since):
            continue
        discovered.append(
            DiscoveredArticleUrl(
                url=loc,
                discovered_via="sitemap",
                last_modified_at=last_modified_at,
            )
        )
    return discovered


def _dedupe_discovered_urls(
    discovered: Iterable[DiscoveredArticleUrl],
) -> list[DiscoveredArticleUrl]:
    seen: set[str] = set()
    deduped: list[DiscoveredArticleUrl] = []
    for item in discovered:
        if item.url in seen:
            continue
        seen.add(item.url)
        deduped.append(item)
    return deduped


def _feed_item_url(item: ElementTree.Element) -> str | None:
    link_text = _child_text(item, "link")
    if link_text:
        return link_text
    for child in item:
        if _local_name(child.tag) == "link" and child.attrib.get("href"):
            return child.attrib["href"]
    return None


def _child_text(element: ElementTree.Element, child_name: str) -> str | None:
    for child in element:
        if _local_name(child.tag) == child_name and child.text:
            return child.text.strip()
    return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", maxsplit=1)[-1]


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    stripped = value.strip()
    try:
        parsed = parsedate_to_datetime(stripped)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
        except ValueError:
            return None
    return _aware(parsed)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_retry_after(value: str | None) -> int | None:
    if not value:
        return None
    stripped = value.strip()
    if stripped.isdigit():
        return int(stripped)
    parsed = _parse_datetime(stripped)
    if parsed is None:
        return None
    return max(0, int((parsed - datetime.now(UTC)).total_seconds()))

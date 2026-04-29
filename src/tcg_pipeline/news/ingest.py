from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any

import dateparser
import httpx
import trafilatura

from tcg_pipeline.db.models import NewsFetchStatus

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; TCGPipelineTracker/0.1; +https://tcg-pipeline.vercel.app)"
)
# Generic D.7a fallback only. D.2 BizJournals auth must add source-specific
# login/paywall markers from news_sources.config.
PAYWALL_PATTERN = re.compile(
    r"\b(subscribe|subscription|sign in|log in|login|register to read|already a subscriber)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ArticleFetchResult:
    fetch_status: str
    final_url: str
    http_status: int | None
    raw_html: str | None = None
    raw_html_hash: str | None = None
    body_text: str | None = None
    body_text_hash: str | None = None
    title: str | None = None
    byline_author: str | None = None
    published_at: datetime | None = None
    publication_section: str | None = None
    tags: list[str] | None = None
    external_article_id: str | None = None
    language: str = "en"
    paywall_state: str | None = None
    error_text: str | None = None


def fetch_article_pass0(
    url: str,
    *,
    timeout_seconds: float = 20.0,
    client: httpx.Client | None = None,
) -> ArticleFetchResult:
    headers = {"User-Agent": DEFAULT_USER_AGENT}
    close_client = client is None
    active_client = client or httpx.Client(
        follow_redirects=True,
        timeout=httpx.Timeout(timeout_seconds),
        headers=headers,
    )
    try:
        response = active_client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        return ArticleFetchResult(
            fetch_status=NewsFetchStatus.FETCH_FAILED.value,
            final_url=url,
            http_status=None,
            error_text=str(exc),
        )
    finally:
        if close_client:
            active_client.close()

    final_url = str(response.url)
    raw_html = response.text
    raw_html_hash = _sha256(raw_html) if raw_html else None
    if response.status_code in {404, 410}:
        return ArticleFetchResult(
            fetch_status=NewsFetchStatus.DEAD_LINK.value,
            final_url=final_url,
            http_status=response.status_code,
            raw_html=raw_html,
            raw_html_hash=raw_html_hash,
            error_text=f"Article returned HTTP {response.status_code}.",
        )
    if response.status_code >= 400:
        return ArticleFetchResult(
            fetch_status=NewsFetchStatus.FETCH_FAILED.value,
            final_url=final_url,
            http_status=response.status_code,
            raw_html=raw_html,
            raw_html_hash=raw_html_hash,
            error_text=f"Article returned HTTP {response.status_code}.",
        )

    metadata = extract_article_metadata(raw_html)
    body_text = _extract_body_text(raw_html, url=final_url)
    body_text_hash = _sha256(body_text) if body_text else None
    paywall_state = _detect_paywall_state(body_text or raw_html)
    if paywall_state == "metered":
        fetch_status = NewsFetchStatus.PAYWALLED.value
    elif not body_text:
        fetch_status = NewsFetchStatus.PARSE_FAILED.value
    else:
        fetch_status = NewsFetchStatus.FETCHED.value

    return ArticleFetchResult(
        fetch_status=fetch_status,
        final_url=final_url,
        http_status=response.status_code,
        raw_html=raw_html,
        raw_html_hash=raw_html_hash,
        body_text=body_text,
        body_text_hash=body_text_hash,
        title=metadata.title,
        byline_author=metadata.byline_author,
        published_at=metadata.published_at,
        publication_section=metadata.publication_section,
        tags=metadata.tags,
        external_article_id=metadata.external_article_id,
        language=metadata.language or "en",
        paywall_state=paywall_state or "open",
        error_text="Article appears paywalled." if paywall_state == "metered" else None,
    )


@dataclass(frozen=True, slots=True)
class ArticleMetadata:
    title: str | None = None
    byline_author: str | None = None
    published_at: datetime | None = None
    publication_section: str | None = None
    tags: list[str] | None = None
    external_article_id: str | None = None
    language: str | None = None


def extract_article_metadata(raw_html: str) -> ArticleMetadata:
    parser = _MetadataParser()
    parser.feed(raw_html)
    json_ld = _metadata_from_json_ld(parser.json_ld_blocks)
    meta = parser.meta
    title = _first_text(
        json_ld.get("headline"),
        json_ld.get("name"),
        _first(meta, "og:title"),
        _first(meta, "twitter:title"),
        parser.title,
    )
    byline_author = _first_text(
        _author_text(json_ld.get("author")),
        _first(meta, "article:author"),
        _first(meta, "author"),
        _first(meta, "parsely-author"),
    )
    published_at = _parse_datetime(
        _first_text(
            json_ld.get("datePublished"),
            json_ld.get("dateCreated"),
            _first(meta, "article:published_time"),
            _first(meta, "date"),
            _first(meta, "pubdate"),
        )
    )
    section = _first_text(
        json_ld.get("articleSection"),
        _first(meta, "article:section"),
        _first(meta, "parsely-section"),
    )
    tags = _normalize_tags(
        json_ld.get("keywords"),
        meta.get("article:tag"),
        meta.get("keywords"),
        meta.get("news_keywords"),
    )
    external_article_id = _first_text(
        json_ld.get("identifier"),
        _first(meta, "parsely-post-id"),
        _first(meta, "article:id"),
    )
    return ArticleMetadata(
        title=title,
        byline_author=byline_author,
        published_at=published_at,
        publication_section=section,
        tags=tags,
        external_article_id=external_article_id,
        language=parser.language,
    )


class _MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, list[str]] = {}
        self.json_ld_blocks: list[str] = []
        self.title: str | None = None
        self.language: str | None = None
        self._in_title = False
        self._title_parts: list[str] = []
        self._script_type: str | None = None
        self._script_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key.lower(): value for key, value in attrs if value is not None}
        tag_name = tag.lower()
        if tag_name == "html":
            self.language = attr_map.get("lang")
        elif tag_name == "title":
            self._in_title = True
            self._title_parts = []
        elif tag_name == "meta":
            key = attr_map.get("property") or attr_map.get("name")
            content = attr_map.get("content")
            if key and content:
                self.meta.setdefault(key.lower(), []).append(content.strip())
        elif tag_name == "script":
            self._script_type = attr_map.get("type", "").lower()
            self._script_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
        if self._script_type == "application/ld+json":
            self._script_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag_name = tag.lower()
        if tag_name == "title" and self._in_title:
            self.title = _clean_text("".join(self._title_parts))
            self._in_title = False
        elif tag_name == "script" and self._script_type == "application/ld+json":
            block = "".join(self._script_parts).strip()
            if block:
                self.json_ld_blocks.append(block)
            self._script_type = None
            self._script_parts = []


def _metadata_from_json_ld(blocks: list[str]) -> dict[str, Any]:
    for block in blocks:
        try:
            payload = json.loads(block)
        except json.JSONDecodeError:
            continue
        for item in _iter_json_ld_items(payload):
            item_type = item.get("@type")
            item_types = item_type if isinstance(item_type, list) else [item_type]
            normalized_types = {str(value).lower() for value in item_types if value}
            if normalized_types & {"article", "newsarticle", "blogposting"}:
                return item
    return {}


def _iter_json_ld_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        items: list[dict[str, Any]] = [payload]
        graph = payload.get("@graph")
        if isinstance(graph, list):
            items.extend(item for item in graph if isinstance(item, dict))
        return items
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _extract_body_text(raw_html: str, *, url: str) -> str | None:
    body_text = trafilatura.extract(
        raw_html,
        url=url,
        output_format="txt",
        include_comments=False,
        include_tables=False,
    )
    cleaned = _clean_text(body_text)
    if cleaned:
        return cleaned
    fallback_parser = _TextParser()
    fallback_parser.feed(raw_html)
    return _clean_text(" ".join(fallback_parser.text_parts))


class _TextParser(HTMLParser):
    SKIP_TAGS = {"script", "style", "noscript"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self.SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self.SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self.text_parts.append(data.strip())


def _detect_paywall_state(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = _clean_text(text)
    if cleaned and len(cleaned) < 300 and PAYWALL_PATTERN.search(cleaned):
        return "metered"
    return None


def _normalize_tags(*values: Any) -> list[str] | None:
    tags: list[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            tags.extend(value.split(","))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    tags.extend(item.split(","))
                elif item is not None:
                    tags.append(str(item))
        else:
            tags.append(str(value))
    cleaned_tags = []
    seen = set()
    for tag in tags:
        cleaned = _clean_text(tag)
        key = cleaned.casefold() if cleaned else None
        if cleaned and key not in seen:
            cleaned_tags.append(cleaned)
            seen.add(key)
    return cleaned_tags or None


def _author_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return _first_text(value.get("name"), value.get("url"))
    if isinstance(value, list):
        return ", ".join(
            author
            for author in (_author_text(item) for item in value)
            if author
        ) or None
    return None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = dateparser.parse(
        value,
        settings={"RETURN_AS_TIMEZONE_AWARE": True, "TIMEZONE": "UTC"},
    )
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _first(meta: dict[str, list[str]], key: str) -> str | None:
    values = meta.get(key)
    if not values:
        return None
    return values[0]


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        if isinstance(value, list):
            value = ", ".join(str(item) for item in value if item is not None)
        text = _clean_text(str(value))
        if text:
            return text
    return None


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", value).strip()
    return cleaned or None


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

BIZJOURNALS_LA_SOURCE_SLUG = "bizjournals_la"
PASTE_A_LINK_SOURCE_SLUG = "news_paste_a_link"
TRACKING_QUERY_KEYS = {
    "_hsenc",
    "_hsmi",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "share",
    "source",
}


@dataclass(frozen=True, slots=True)
class CanonicalNewsUrl:
    original_url: str
    canonical_url: str
    url_hash: str
    source_slug: str


def canonicalize_news_url(url: str) -> CanonicalNewsUrl:
    original_url = url.strip()
    parsed = urlsplit(original_url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL must be an absolute http(s) URL.")

    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    query_items = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not _is_tracking_query_key(key)
    ]
    canonical_url = urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            urlencode(sorted(query_items), doseq=True),
            "",
        )
    )
    return CanonicalNewsUrl(
        original_url=original_url,
        canonical_url=canonical_url,
        url_hash=hashlib.sha256(canonical_url.encode("utf-8")).hexdigest(),
        source_slug=source_slug_for_url(canonical_url),
    )


def source_slug_for_url(url: str) -> str:
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    if hostname == "bizjournals.com" or hostname.endswith(".bizjournals.com"):
        if path.startswith("/losangeles"):
            return BIZJOURNALS_LA_SOURCE_SLUG
    return PASTE_A_LINK_SOURCE_SLUG


def _is_tracking_query_key(key: str) -> bool:
    normalized_key = key.strip().lower()
    return normalized_key.startswith("utm_") or normalized_key in TRACKING_QUERY_KEYS

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
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


def canonicalize_news_url(url: str, *, source_slug: str | None = None) -> CanonicalNewsUrl:
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
        source_slug=source_slug or source_slug_for_url(canonical_url),
    )


def source_slug_for_url(
    url: str,
    *,
    host_routes: Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
) -> str:
    source_slug = configured_source_slug_for_url(url, host_routes=host_routes)
    if source_slug is not None:
        return source_slug
    return PASTE_A_LINK_SOURCE_SLUG


def configured_source_slug_for_url(
    url: str,
    *,
    host_routes: Mapping[str, str] | Iterable[tuple[str, str]] | None,
) -> str | None:
    if not host_routes:
        return None
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return None
    route_items = host_routes.items() if isinstance(host_routes, Mapping) else host_routes
    for route_host, source_slug in route_items:
        if host_matches_route(hostname, route_host):
            return source_slug
    return None


def host_matches_route(hostname: str, route_host: str) -> bool:
    normalized_hostname = hostname.lower().strip(".")
    normalized_route = route_host.lower().strip()
    if normalized_route.startswith("*."):
        route_suffix = normalized_route[2:].strip(".")
        return normalized_hostname == route_suffix or normalized_hostname.endswith(
            f".{route_suffix}"
        )
    if normalized_route.startswith("."):
        route_suffix = normalized_route[1:].strip(".")
        return normalized_hostname == route_suffix or normalized_hostname.endswith(
            f".{route_suffix}"
        )
    normalized_route = normalized_route.strip(".")
    return normalized_hostname == normalized_route


def _is_tracking_query_key(key: str) -> bool:
    normalized_key = key.strip().lower()
    return normalized_key.startswith("utm_") or normalized_key in TRACKING_QUERY_KEYS

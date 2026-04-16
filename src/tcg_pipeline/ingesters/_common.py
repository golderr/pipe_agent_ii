from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import Any

from geoalchemy2.elements import WKTElement

from tcg_pipeline.db.models import GeocodeConfidence

NULL_SENTINELS = frozenset({"", "--"})


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if text in NULL_SENTINELS:
        return None
    return text


def clean_identifier_text(value: Any) -> str | None:
    cleaned = clean_text(value)
    if cleaned is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = parse_float(value)
        if numeric is None:
            return cleaned
        return str(int(numeric)) if numeric.is_integer() else str(numeric)
    return cleaned


def parse_int(value: Any) -> int | None:
    numeric = parse_float(value)
    if numeric is None:
        return None
    return int(round(numeric))


def parse_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    cleaned = clean_text(value)
    if cleaned is None:
        return None

    normalized = cleaned.replace(",", "").replace("%", "")
    try:
        return float(normalized)
    except ValueError:
        return None


def dedupe_strings(values: Iterable[str | None]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        if not value or value in deduped:
            continue
        deduped.append(value)
    return deduped


def build_location(lat_value: Any, lng_value: Any) -> WKTElement | None:
    lat = parse_float(lat_value)
    lng = parse_float(lng_value)
    if lat is None or lng is None:
        return None
    return WKTElement(f"POINT({lng} {lat})", srid=4326)


def determine_geocode_confidence(
    lat_value: Any,
    lng_value: Any,
) -> GeocodeConfidence:
    latitude = parse_float(lat_value)
    longitude = parse_float(lng_value)
    if latitude is not None and longitude is not None:
        return GeocodeConfidence.HIGH
    return GeocodeConfidence.NONE


def display_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def serialize_json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def row_has_values(payload: Mapping[str, Any]) -> bool:
    return any(has_value(value) for value in payload.values())


def has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() not in NULL_SENTINELS
    return True

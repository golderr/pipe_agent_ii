from __future__ import annotations

import re
import string
from dataclasses import dataclass

import usaddress

UNIT_LABELS = {
    "OccupancyType",
    "OccupancyIdentifier",
    "SubaddressType",
    "SubaddressIdentifier",
}

STATE_ABBREVIATIONS = {
    "ALABAMA": "AL",
    "ALASKA": "AK",
    "ARIZONA": "AZ",
    "ARKANSAS": "AR",
    "CALIFORNIA": "CA",
    "COLORADO": "CO",
    "CONNECTICUT": "CT",
    "DELAWARE": "DE",
    "FLORIDA": "FL",
    "GEORGIA": "GA",
    "HAWAII": "HI",
    "IDAHO": "ID",
    "ILLINOIS": "IL",
    "INDIANA": "IN",
    "IOWA": "IA",
    "KANSAS": "KS",
    "KENTUCKY": "KY",
    "LOUISIANA": "LA",
    "MAINE": "ME",
    "MARYLAND": "MD",
    "MASSACHUSETTS": "MA",
    "MICHIGAN": "MI",
    "MINNESOTA": "MN",
    "MISSISSIPPI": "MS",
    "MISSOURI": "MO",
    "MONTANA": "MT",
    "NEBRASKA": "NE",
    "NEVADA": "NV",
    "NEW HAMPSHIRE": "NH",
    "NEW JERSEY": "NJ",
    "NEW MEXICO": "NM",
    "NEW YORK": "NY",
    "NORTH CAROLINA": "NC",
    "NORTH DAKOTA": "ND",
    "OHIO": "OH",
    "OKLAHOMA": "OK",
    "OREGON": "OR",
    "PENNSYLVANIA": "PA",
    "RHODE ISLAND": "RI",
    "SOUTH CAROLINA": "SC",
    "SOUTH DAKOTA": "SD",
    "TENNESSEE": "TN",
    "TEXAS": "TX",
    "UTAH": "UT",
    "VERMONT": "VT",
    "VIRGINIA": "VA",
    "WASHINGTON": "WA",
    "WEST VIRGINIA": "WV",
    "WISCONSIN": "WI",
    "WYOMING": "WY",
    "DISTRICT OF COLUMBIA": "DC",
}

DIRECTIONAL_MAP = {
    "N": "NORTH",
    "NORTH": "NORTH",
    "S": "SOUTH",
    "SOUTH": "SOUTH",
    "E": "EAST",
    "EAST": "EAST",
    "W": "WEST",
    "WEST": "WEST",
    "NE": "NORTHEAST",
    "NORTHEAST": "NORTHEAST",
    "NW": "NORTHWEST",
    "NORTHWEST": "NORTHWEST",
    "SE": "SOUTHEAST",
    "SOUTHEAST": "SOUTHEAST",
    "SW": "SOUTHWEST",
    "SOUTHWEST": "SOUTHWEST",
}

STREET_SUFFIX_MAP = {
    "ALY": "ALLEY",
    "ALLEY": "ALLEY",
    "AVE": "AVENUE",
    "AV": "AVENUE",
    "AVENUE": "AVENUE",
    "BL": "BOULEVARD",
    "BLVD": "BOULEVARD",
    "BOULEVARD": "BOULEVARD",
    "CIR": "CIRCLE",
    "CIRCLE": "CIRCLE",
    "CT": "COURT",
    "COURT": "COURT",
    "DR": "DRIVE",
    "DRIVE": "DRIVE",
    "HWY": "HIGHWAY",
    "HIGHWAY": "HIGHWAY",
    "LN": "LANE",
    "LANE": "LANE",
    "PKWY": "PARKWAY",
    "PARKWAY": "PARKWAY",
    "PL": "PLACE",
    "PLACE": "PLACE",
    "PLZ": "PLAZA",
    "PLAZA": "PLAZA",
    "RD": "ROAD",
    "ROAD": "ROAD",
    "SQ": "SQUARE",
    "SQUARE": "SQUARE",
    "ST": "STREET",
    "STREET": "STREET",
    "TER": "TERRACE",
    "TERRACE": "TERRACE",
    "TRL": "TRAIL",
    "TRAIL": "TRAIL",
    "WAY": "WAY",
}

ORDINAL_WORD_MAP = {
    "FIRST": "1ST",
    "SECOND": "2ND",
    "THIRD": "3RD",
    "FOURTH": "4TH",
    "FIFTH": "5TH",
    "SIXTH": "6TH",
    "SEVENTH": "7TH",
    "EIGHTH": "8TH",
    "NINTH": "9TH",
    "TENTH": "10TH",
    "ELEVENTH": "11TH",
    "TWELFTH": "12TH",
    "THIRTEENTH": "13TH",
    "FOURTEENTH": "14TH",
    "FIFTEENTH": "15TH",
    "SIXTEENTH": "16TH",
    "SEVENTEENTH": "17TH",
    "EIGHTEENTH": "18TH",
    "NINETEENTH": "19TH",
    "TWENTIETH": "20TH",
}

LOS_ANGELES_CITY_ALIASES = {
    "LOS ANGELES CBD": "LOS ANGELES",
    "DOWNTOWN LOS ANGELES": "LOS ANGELES",
    "DTLA": "LOS ANGELES",
    "HOLLYWOOD": "LOS ANGELES",
}

ADDRESS_RANGE_RE = re.compile(r"^(?P<start>\d+)\s*-\s*(?P<end>\d+)$")
ZIP_RE = re.compile(r"(?P<zip>\d{5})")
ORDINAL_TOKEN_RE = re.compile(r"^(\d+)(ST|ND|RD|TH)$")


@dataclass(frozen=True, slots=True)
class NormalizedAddress:
    raw_address: str
    canonical_street_line: str | None
    canonical_address: str | None
    house_number: str | None
    house_number_start: int | None
    house_number_end: int | None
    street_predirectional: str | None
    street_name: str | None
    street_suffix: str | None
    street_postdirectional: str | None
    unit: str | None
    city: str | None
    state: str | None
    postal_code: str | None
    parser: str

    @property
    def has_range(self) -> bool:
        return (
            self.house_number is not None
            and self.house_number_start is not None
            and self.house_number_end is not None
            and self.house_number_start != self.house_number_end
        )


def normalize_address(
    raw_address: str,
    *,
    city: str | None = None,
    state: str | None = None,
    postal_code: str | None = None,
    market: str | None = None,
) -> NormalizedAddress:
    cleaned = _clean_input(raw_address)
    parsed = usaddress.parse(cleaned)

    grouped: dict[str, list[str]] = {}
    for token, label in parsed:
        normalized_token = _clean_token(token)
        if not normalized_token:
            continue
        grouped.setdefault(label, []).append(normalized_token)

    address_number = _join_label(grouped, "AddressNumber")
    pre_directional = _normalize_directional(_join_label(grouped, "StreetNamePreDirectional"))
    street_name = _normalize_street_name(_join_label(grouped, "StreetName"))
    street_suffix = _normalize_suffix(_join_label(grouped, "StreetNamePostType"))
    post_directional = _normalize_directional(_join_label(grouped, "StreetNamePostDirectional"))
    unit = _normalize_unit(grouped)

    normalized_city = normalize_city(_join_label(grouped, "PlaceName") or city, market=market)
    normalized_state = normalize_state(_join_label(grouped, "StateName") or state)
    normalized_zip = normalize_postal_code(_join_label(grouped, "ZipCode") or postal_code)

    range_start, range_end = parse_address_range(address_number)
    street_line = _build_street_line(
        address_number=address_number,
        pre_directional=pre_directional,
        street_name=street_name,
        street_suffix=street_suffix,
        post_directional=post_directional,
    )
    canonical_address = _join_parts(
        [
            street_line,
            normalized_city,
            normalized_state,
            normalized_zip,
        ]
    )

    return NormalizedAddress(
        raw_address=raw_address,
        canonical_street_line=street_line,
        canonical_address=canonical_address,
        house_number=address_number,
        house_number_start=range_start,
        house_number_end=range_end,
        street_predirectional=pre_directional,
        street_name=street_name,
        street_suffix=street_suffix,
        street_postdirectional=post_directional,
        unit=unit,
        city=normalized_city,
        state=normalized_state,
        postal_code=normalized_zip,
        parser="usaddress",
    )


def normalize_city(city: str | None, *, market: str | None = None) -> str | None:
    if not city:
        return None

    normalized = _clean_phrase(city)
    if market == "los_angeles":
        normalized = LOS_ANGELES_CITY_ALIASES.get(normalized, normalized)
    return normalized


def normalize_state(state: str | None) -> str | None:
    if not state:
        return None

    normalized = _clean_phrase(state)
    if len(normalized) == 2 and normalized.isalpha():
        return normalized
    return STATE_ABBREVIATIONS.get(normalized, normalized)


def normalize_postal_code(postal_code: str | None) -> str | None:
    if not postal_code:
        return None
    match = ZIP_RE.search(postal_code)
    return match.group("zip") if match else None


def parse_address_range(address_number: str | None) -> tuple[int | None, int | None]:
    if not address_number:
        return None, None

    match = ADDRESS_RANGE_RE.match(address_number)
    if match:
        start = int(match.group("start"))
        end = int(match.group("end"))
        return min(start, end), max(start, end)

    digits = re.sub(r"[^\d]", "", address_number)
    if digits:
        value = int(digits)
        return value, value
    return None, None


def _normalize_unit(grouped: dict[str, list[str]]) -> str | None:
    parts: list[str] = []
    for label in UNIT_LABELS:
        parts.extend(grouped.get(label, []))
    return _join_parts(parts)


def _normalize_street_name(street_name: str | None) -> str | None:
    if not street_name:
        return None

    normalized_tokens = []
    for token in _clean_phrase(street_name).split():
        normalized_tokens.append(_normalize_street_name_token(token))
    return _join_parts(normalized_tokens)


def _normalize_street_name_token(token: str) -> str:
    token = token.upper()
    token = ORDINAL_WORD_MAP.get(token, token)

    if ORDINAL_TOKEN_RE.match(token):
        return token

    return token


def _normalize_directional(value: str | None) -> str | None:
    if not value:
        return None
    token = _clean_token(value)
    return DIRECTIONAL_MAP.get(token, token)


def _normalize_suffix(value: str | None) -> str | None:
    if not value:
        return None
    token = _clean_token(value)
    return STREET_SUFFIX_MAP.get(token, token)


def _build_street_line(
    *,
    address_number: str | None,
    pre_directional: str | None,
    street_name: str | None,
    street_suffix: str | None,
    post_directional: str | None,
) -> str | None:
    return _join_parts(
        [
            address_number,
            pre_directional,
            street_name,
            street_suffix,
            post_directional,
        ]
    )


def _join_label(grouped: dict[str, list[str]], label: str) -> str | None:
    return _join_parts(grouped.get(label, []))


def _join_parts(parts: list[str | None]) -> str | None:
    values = [part.strip() for part in parts if part and part.strip()]
    return " ".join(values) if values else None


def _clean_input(value: str) -> str:
    text = value.replace("\n", " ").replace("\r", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _clean_phrase(value: str) -> str:
    cleaned = _clean_input(value).upper()
    cleaned = cleaned.translate(str.maketrans("", "", string.punctuation.replace("-", "")))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _clean_token(value: str) -> str:
    cleaned = value.upper().strip()
    cleaned = cleaned.translate(str.maketrans("", "", string.punctuation.replace("-", "")))
    return cleaned

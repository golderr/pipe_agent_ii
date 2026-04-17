from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import date, timedelta
from typing import Any

from tcg_pipeline.collectors.base import RawRecord
from tcg_pipeline.ingesters._common import (
    clean_identifier_text,
    clean_text,
    parse_float,
    parse_int,
)
from tcg_pipeline.matching.normalizer import normalize_address
from tcg_pipeline.permit_numbers import (
    extract_ladbs_pcis_permit_numbers,
    normalize_ladbs_permit_number,
)

RawRecordAdapter = Callable[[Mapping[str, Any]], RawRecord | None]

LADBS_CITY = "Los Angeles"
LADBS_COUNTY = "Los Angeles"
LADBS_STATE = "CA"
ASSESSOR_BOOK_LENGTH = 4
ASSESSOR_PAGE_LENGTH = 3
ASSESSOR_PARCEL_LENGTH = 3
LADBS_HOUSING_USE_DESCRIPTIONS = frozenset(
    {
        "Apartment",
        "Duplex",
        "Dwelling - Single Family",
    }
)
LADBS_INSPECTION_EVIDENCE_MAX_AGE_DAYS = 365
LADBS_INSPECTION_POSITIVE_RESULTS = frozenset(
    {
        "Approved",
        "Conditional Approval",
        "Completed",
        "Completed (special insp)",
        "Partial Approval",
    }
)
LADBS_INSPECTION_ACTIVE_PERMIT_STATUSES = frozenset(
    {
        "CofO in Progress",
        "Issued",
        "Pending CofO",
    }
)


# Deprecated legacy adapters retained for historical replay of the frozen `hbkd-qubn` and
# `cpkv-aajs` snapshots. Active Los Angeles config now points at the live replacement adapters
# further below. Revisit removal only after the repo has an explicit historical replay/backfill
# policy for the frozen pre-2023 LADBS rows.
def make_ladbs_permits_adapter(*, market: str, source_name: str) -> RawRecordAdapter:
    def adapter(row: Mapping[str, Any]) -> RawRecord | None:
        permit_number = clean_identifier_text(row.get("pcis_permit"))
        if permit_number is None:
            return None

        normalized = _normalize_ladbs_address(row, market=market)
        mapped_fields = {
            "status_evidence_type": "building_permit_issued",
            "status_evidence_date": _normalize_date_string(row.get("issue_date")),
            "council_district": clean_text(row.get("council_district")),
            **_build_common_ladbs_fields(row, normalized=normalized),
        }

        return RawRecord(
            source_name=source_name,
            source_record_id=permit_number,
            raw_payload=dict(row),
            canonical_address=normalized.canonical_address,
            project_name=None,
            identifiers=_build_ladbs_identifiers(permit_number=permit_number),
            mapped_fields={key: value for key, value in mapped_fields.items() if value is not None},
        )

    return adapter


def make_ladbs_permit_activity_adapter(*, market: str, source_name: str) -> RawRecordAdapter:
    def adapter(row: Mapping[str, Any]) -> RawRecord | None:
        permit_number = clean_identifier_text(row.get("pcis_permit"))
        if permit_number is None:
            return None

        normalized = _normalize_ladbs_address(row, market=market)
        mapped_fields = {
            "council_district": clean_text(row.get("council_district")),
            **_build_common_ladbs_fields(row, normalized=normalized),
        }

        return RawRecord(
            source_name=source_name,
            source_record_id=permit_number,
            raw_payload=dict(row),
            canonical_address=normalized.canonical_address,
            project_name=None,
            identifiers=_build_ladbs_identifiers(permit_number=permit_number),
            mapped_fields={key: value for key, value in mapped_fields.items() if value is not None},
        )

    return adapter


def make_ladbs_new_housing_adapter(*, market: str, source_name: str) -> RawRecordAdapter:
    def adapter(row: Mapping[str, Any]) -> RawRecord | None:
        permit_number = clean_identifier_text(row.get("pcis_permit"))
        if permit_number is None:
            return None

        normalized = _normalize_ladbs_address(row, market=market)
        apn = _build_assessor_apn(row)
        lat, lng = _extract_coordinates(row.get("location_1"))

        mapped_fields = {
            "status_evidence_type": "building_permit_issued",
            "status_evidence_date": _normalize_date_string(row.get("issue_date")),
            "permit_category": clean_text(row.get("permit_category")),
            "census_tract": clean_text(row.get("census_tract")),
            "tract": clean_text(row.get("tract")),
            "block": clean_text(row.get("block")),
            "lot": clean_text(row.get("lot")),
            "apn": apn,
            **_build_common_ladbs_fields(row, normalized=normalized),
        }

        return RawRecord(
            source_name=source_name,
            source_record_id=permit_number,
            raw_payload=dict(row),
            canonical_address=normalized.canonical_address,
            project_name=None,
            identifiers=_build_ladbs_identifiers(permit_number=permit_number, apn=apn),
            mapped_fields={key: value for key, value in mapped_fields.items() if value is not None},
            lat=lat,
            lng=lng,
        )

    return adapter


def make_ladbs_cofo_adapter(*, market: str, source_name: str) -> RawRecordAdapter:
    def adapter(row: Mapping[str, Any]) -> RawRecord | None:
        cofo_number = clean_identifier_text(row.get("cofo_number"))
        if cofo_number is None:
            return None
        permit_number = clean_identifier_text(row.get("pcis_permit"))

        normalized = _normalize_ladbs_address(row, market=market)
        apn = _build_assessor_apn(row)
        lat, lng = _extract_latitude_longitude(row.get("latitude_longitude"))
        cofo_issue_date = _normalize_date_string(row.get("cofo_issue_date"))
        status_date = _normalize_date_string(row.get("status_date")) or cofo_issue_date
        evidence_type = _build_cofo_evidence_type(row, cofo_issue_date=cofo_issue_date)

        mapped_fields = {
            "status_evidence_type": evidence_type,
            "status_evidence_date": cofo_issue_date if evidence_type is not None else None,
            "status_date": status_date,
            "date_delivery": cofo_issue_date,
            "cofo_number": cofo_number,
            "cofo_issue_date": cofo_issue_date,
            "latest_status": clean_text(row.get("latest_status")),
            "tract": clean_text(row.get("tract")),
            "block": clean_text(row.get("block")),
            "lot": clean_text(row.get("lot")),
            "apn": apn,
            **_build_common_ladbs_fields(row, normalized=normalized),
        }

        return RawRecord(
            source_name=source_name,
            source_record_id=cofo_number,
            raw_payload=dict(row),
            canonical_address=normalized.canonical_address,
            project_name=None,
            identifiers=_build_ladbs_identifiers(permit_number=permit_number, apn=apn),
            mapped_fields={key: value for key, value in mapped_fields.items() if value is not None},
            lat=lat,
            lng=lng,
        )

    return adapter


def make_ladbs_permits_pi9x_tg5x_adapter(
    *,
    market: str,
    source_name: str,
) -> RawRecordAdapter:
    """Map live LADBS permit rows from `pi9x-tg5x`.

    See `docs/source_specs/ladbs_socrata_completeness.md` under the LADBS source-bundle and
    query-strategy sections. This is the live replacement permit feed identified in the 2026-04-17
    coverage audit sections 3.5 and 3.6.
    """

    def adapter(row: Mapping[str, Any]) -> RawRecord | None:
        permit_number = _normalize_permit_number(row.get("permit_nbr"))
        if permit_number is None:
            return None

        normalized = _normalize_primary_address(row, market=market)
        apn = _normalize_apn(row.get("apn"))
        lat, lng = _extract_pi9x_coordinates(row)
        use_desc = clean_text(row.get("use_desc"))
        mapped_fields = {
            "status_evidence_type": "building_permit_issued",
            "status_evidence_date": _normalize_date_string(row.get("issue_date")),
            "council_district": clean_text(row.get("council_district")),
            "status_desc": clean_text(row.get("status_desc")),
            "use_desc": use_desc,
            "housing_use_desc": use_desc if _is_housing_use_desc(use_desc) else None,
            "apn": apn,
            **_build_common_pi9x_ladbs_fields(row, normalized=normalized),
        }

        return RawRecord(
            source_name=source_name,
            source_record_id=permit_number,
            raw_payload=dict(row),
            canonical_address=normalized.canonical_address,
            project_name=None,
            identifiers=_build_ladbs_identifiers(permit_number=permit_number, apn=apn),
            mapped_fields={key: value for key, value in mapped_fields.items() if value is not None},
            lat=lat,
            lng=lng,
        )

    return adapter


def make_ladbs_permit_activity_pi9x_tg5x_adapter(
    *,
    market: str,
    source_name: str,
) -> RawRecordAdapter:
    """Map live non-`Bldg-New` permit rows from `pi9x-tg5x`.

    See `docs/source_specs/ladbs_socrata_completeness.md` under the LADBS source-bundle and
    query-strategy sections. This adapter preserves the update-only permit-activity role on the
    live permit dataset identified in the 2026-04-17 coverage audit sections 3.5 and 3.6.
    """

    def adapter(row: Mapping[str, Any]) -> RawRecord | None:
        permit_number = _normalize_permit_number(row.get("permit_nbr"))
        if permit_number is None:
            return None

        normalized = _normalize_primary_address(row, market=market)
        apn = _normalize_apn(row.get("apn"))
        lat, lng = _extract_pi9x_coordinates(row)
        mapped_fields = {
            "council_district": clean_text(row.get("council_district")),
            "status_desc": clean_text(row.get("status_desc")),
            "use_desc": clean_text(row.get("use_desc")),
            "apn": apn,
            **_build_common_pi9x_ladbs_fields(row, normalized=normalized),
        }

        return RawRecord(
            source_name=source_name,
            source_record_id=permit_number,
            raw_payload=dict(row),
            canonical_address=normalized.canonical_address,
            project_name=None,
            identifiers=_build_ladbs_identifiers(permit_number=permit_number, apn=apn),
            mapped_fields={key: value for key, value in mapped_fields.items() if value is not None},
            lat=lat,
            lng=lng,
        )

    return adapter


def make_ladbs_inspections_9w5z_rg2h_adapter(
    *,
    market: str,
    source_name: str,
    as_of: date | None = None,
) -> RawRecordAdapter:
    """Map live LADBS inspection rows from `9w5z-rg2h`.

    See `docs/source_specs/ladbs_socrata_completeness.md` under the LADBS source-bundle and
    query-strategy sections. This is the live inspection feed profiled in the 2026-04-17 coverage
    audit sections 3.5 and 3.6. Only recent, substantive inspections on active permits emit direct
    `Under Construction` evidence; older or administrative rows persist as context only.
    """

    effective_as_of = as_of or date.today()

    def adapter(row: Mapping[str, Any]) -> RawRecord | None:
        source_record_id = clean_identifier_text(row.get(":id")) or _build_inspection_record_id(row)
        permit_number = _normalize_permit_number(row.get("permit"))
        if source_record_id is None or permit_number is None:
            return None

        normalized = _normalize_single_line_address(
            clean_text(row.get("address")),
            market=market,
        )
        lat, lng = _extract_latitude_longitude(row.get("lat_lon"))
        inspection_date = _normalize_date_string(row.get("inspection_date"))
        inspection_result = clean_text(row.get("inspection_result"))
        permit_status = clean_text(row.get("permit_status"))
        status_evidence_type, status_evidence_reason = _build_inspection_status_evidence(
            inspection_date=inspection_date,
            inspection_result=inspection_result,
            permit_status=permit_status,
            as_of=effective_as_of,
        )
        mapped_fields = {
            "status_evidence_type": status_evidence_type,
            "status_evidence_date": inspection_date if status_evidence_type else None,
            "status_evidence_reason": status_evidence_reason,
            "inspection": clean_text(row.get("inspection")),
            "inspection_date": inspection_date,
            "inspection_result": inspection_result,
            "permit_status": permit_status,
            "jurisdiction": "city_of_los_angeles",
            "city": LADBS_CITY,
            "county": LADBS_COUNTY,
            "state": LADBS_STATE,
            "zip": normalized.postal_code,
        }

        return RawRecord(
            source_name=source_name,
            source_record_id=source_record_id,
            raw_payload=dict(row),
            canonical_address=normalized.canonical_address,
            project_name=None,
            identifiers=_build_ladbs_identifiers(permit_number=permit_number),
            mapped_fields={key: value for key, value in mapped_fields.items() if value is not None},
            lat=lat,
            lng=lng,
        )

    return adapter


def extract_pcis_permit_numbers(source_urls: list[str] | tuple[str, ...]) -> list[str]:
    return extract_ladbs_pcis_permit_numbers(source_urls)


def _compose_name(
    business_name: Any,
    first_name: Any,
    last_name: Any,
) -> str | None:
    preferred = clean_text(business_name)
    if preferred:
        return preferred

    parts = [clean_text(first_name), clean_text(last_name)]
    name = " ".join(part for part in parts if part)
    return name or None


def _normalize_date_string(value: Any) -> str | None:
    raw = clean_text(value)
    if raw is None:
        return None
    return raw.split("T", 1)[0]


def _build_ladbs_identifiers(
    *,
    permit_number: str | None,
    apn: str | None = None,
) -> dict[str, list[str]]:
    identifiers: dict[str, list[str]] = {}
    if permit_number is not None:
        identifiers["permit_number"] = [permit_number]
    if apn is not None:
        identifiers["apn"] = [apn]
    return identifiers


def _build_common_ladbs_fields(
    row: Mapping[str, Any],
    *,
    normalized,
) -> dict[str, Any]:
    issue_date = _normalize_date_string(row.get("issue_date"))
    applicant_name = _compose_name(
        row.get("applicant_business_name"),
        row.get("applicant_first_name"),
        row.get("applicant_last_name"),
    )
    contractor_name = _compose_name(
        row.get("contractors_business_name"),
        row.get("principal_first_name"),
        row.get("principal_last_name"),
    )
    return {
        "permit_issue_date": issue_date,
        "total_units": parse_int(row.get("of_residential_dwelling_units")),
        "stories": parse_int(row.get("of_stories")),
        "description": clean_text(row.get("work_description")),
        "applicant": applicant_name,
        "contractor": contractor_name,
        "zoning": clean_text(row.get("zone")),
        "jurisdiction": "city_of_los_angeles",
        "permit_type": clean_text(row.get("permit_type")),
        "permit_sub_type": clean_text(row.get("permit_sub_type")),
        "valuation": parse_int(row.get("valuation")),
        "initiating_office": clean_text(row.get("initiating_office")),
        "city": LADBS_CITY,
        "county": LADBS_COUNTY,
        "state": LADBS_STATE,
        "zip": normalized.postal_code,
    }


def _build_common_pi9x_ladbs_fields(
    row: Mapping[str, Any],
    *,
    normalized,
) -> dict[str, Any]:
    issue_date = _normalize_date_string(row.get("issue_date"))
    applicant_name = _compose_name(
        row.get("applicant_business_name"),
        row.get("applicant_first_name"),
        row.get("applicant_last_name"),
    )
    contractor_name = _compose_name(
        row.get("contractors_business_name"),
        row.get("principal_first_name"),
        row.get("principal_last_name"),
    )
    return {
        "permit_issue_date": issue_date,
        "total_units": parse_int(row.get("of_residential_dwelling_units")),
        "stories": parse_int(row.get("of_stories")),
        "description": clean_text(row.get("work_desc")),
        "applicant": applicant_name,
        "contractor": contractor_name,
        "zoning": clean_text(row.get("zone")),
        "jurisdiction": "city_of_los_angeles",
        "permit_type": clean_text(row.get("permit_type")),
        "permit_sub_type": clean_text(row.get("permit_sub_type")),
        "valuation": parse_int(row.get("valuation")),
        "initiating_office": clean_text(row.get("initiating_office")),
        "city": LADBS_CITY,
        "county": LADBS_COUNTY,
        "state": LADBS_STATE,
        "zip": normalized.postal_code,
    }


def _normalize_ladbs_address(row: Mapping[str, Any], *, market: str):
    street_parts = [
        _build_address_number(row),
        clean_text(row.get("street_direction")),
        clean_text(row.get("street_name")),
        clean_text(row.get("street_suffix")),
    ]
    street_address = " ".join(part for part in street_parts if part)
    return normalize_address(
        street_address,
        city=LADBS_CITY,
        state=LADBS_STATE,
        postal_code=clean_text(row.get("zip_code")),
        market=market,
    )


def _normalize_primary_address(row: Mapping[str, Any], *, market: str):
    return _normalize_single_line_address(
        clean_text(row.get("primary_address")),
        market=market,
        postal_code=clean_text(row.get("zip_code")),
    )


def _normalize_single_line_address(
    street_address: str | None,
    *,
    market: str,
    postal_code: str | None = None,
):
    return normalize_address(
        street_address or "",
        city=LADBS_CITY,
        state=LADBS_STATE,
        postal_code=postal_code,
        market=market,
    )


def _build_address_number(row: Mapping[str, Any]) -> str | None:
    start = clean_identifier_text(row.get("address_start"))
    end = clean_identifier_text(row.get("address_end"))
    if start and end and start != end:
        return f"{start}-{end}"
    return start or end


def _build_assessor_apn(row: Mapping[str, Any]) -> str | None:
    book = clean_identifier_text(row.get("assessor_book"))
    page = clean_identifier_text(row.get("assessor_page"))
    parcel = clean_identifier_text(row.get("assessor_parcel"))
    if not book or not page or not parcel:
        return None
    if not all(part.isdigit() for part in [book, page, parcel]):
        return None
    if len(book) > ASSESSOR_BOOK_LENGTH:
        return None
    if len(page) > ASSESSOR_PAGE_LENGTH:
        return None
    if len(parcel) > ASSESSOR_PARCEL_LENGTH:
        return None
    return (
        f"{book.zfill(ASSESSOR_BOOK_LENGTH)}"
        f"{page.zfill(ASSESSOR_PAGE_LENGTH)}"
        f"{parcel.zfill(ASSESSOR_PARCEL_LENGTH)}"
    )


def _normalize_apn(value: Any) -> str | None:
    cleaned = clean_identifier_text(value)
    if cleaned is None:
        return None
    digits = re.sub(r"[^\d]", "", cleaned)
    if len(digits) != ASSESSOR_BOOK_LENGTH + ASSESSOR_PAGE_LENGTH + ASSESSOR_PARCEL_LENGTH:
        return None
    return digits


def _normalize_permit_number(value: Any) -> str | None:
    return normalize_ladbs_permit_number(value)


def _parse_iso_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _build_cofo_evidence_type(
    row: Mapping[str, Any],
    *,
    cofo_issue_date: str | None,
) -> str | None:
    latest_status = clean_text(row.get("latest_status"))
    # Only a final CofO issuance with a real issue date is treated as direct Complete evidence.
    if cofo_issue_date is None:
        return None
    if latest_status is not None and latest_status.casefold() == "cofo issued":
        return "certificate_of_occupancy_issued"
    return None


def _build_inspection_status_evidence(
    *,
    inspection_date: str | None,
    inspection_result: str | None,
    permit_status: str | None,
    as_of: date,
) -> tuple[str | None, str | None]:
    parsed_inspection_date = _parse_iso_date(inspection_date)
    if parsed_inspection_date is None:
        return None, None
    if parsed_inspection_date < as_of - timedelta(days=LADBS_INSPECTION_EVIDENCE_MAX_AGE_DAYS):
        return None, None
    if inspection_result not in LADBS_INSPECTION_POSITIVE_RESULTS:
        return None, None
    if permit_status not in LADBS_INSPECTION_ACTIVE_PERMIT_STATUSES:
        return None, None
    return (
        "building_inspection_recorded",
        (
            "Recent LADBS inspection with substantive result "
            f"'{inspection_result}' on active permit status '{permit_status}'."
        ),
    )


def _build_inspection_record_id(row: Mapping[str, Any]) -> str | None:
    permit_number = _normalize_permit_number(row.get("permit"))
    inspection_date = _normalize_date_string(row.get("inspection_date"))
    inspection_name = clean_text(row.get("inspection"))
    parts = [part for part in [permit_number, inspection_date, inspection_name] if part]
    if not parts:
        return None
    return "::".join(parts)


def _extract_coordinates(value: Any) -> tuple[float | None, float | None]:
    if not isinstance(value, Mapping):
        return None, None
    coordinates = value.get("coordinates")
    if not isinstance(coordinates, (list, tuple)) or len(coordinates) < 2:
        return None, None
    lng = parse_float(coordinates[0])
    lat = parse_float(coordinates[1])
    return lat, lng


def _extract_latitude_longitude(value: Any) -> tuple[float | None, float | None]:
    if not isinstance(value, Mapping):
        return None, None
    lat = parse_float(value.get("latitude"))
    lng = parse_float(value.get("longitude"))
    return lat, lng


def _extract_pi9x_coordinates(row: Mapping[str, Any]) -> tuple[float | None, float | None]:
    lat = parse_float(row.get("lat"))
    lng = parse_float(row.get("lon"))
    if lat is not None and lng is not None:
        return lat, lng
    return _extract_coordinates(row.get("geolocation"))


def _is_housing_use_desc(value: str | None) -> bool:
    if value is None:
        return False
    return value in LADBS_HOUSING_USE_DESCRIPTIONS

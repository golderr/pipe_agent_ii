from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from tcg_pipeline.collectors.base import RawRecord
from tcg_pipeline.ingesters._common import clean_identifier_text, clean_text, parse_int
from tcg_pipeline.matching.normalizer import normalize_address

RawRecordAdapter = Callable[[Mapping[str, Any]], RawRecord | None]

LADBS_CITY = "Los Angeles"
LADBS_COUNTY = "Los Angeles"
LADBS_STATE = "CA"


def make_ladbs_permits_adapter(*, market: str, source_name: str) -> RawRecordAdapter:
    def adapter(row: Mapping[str, Any]) -> RawRecord | None:
        permit_number = clean_identifier_text(row.get("pcis_permit"))
        if permit_number is None:
            return None

        street_parts = [
            clean_text(row.get("address_start")),
            clean_text(row.get("street_direction")),
            clean_text(row.get("street_name")),
            clean_text(row.get("street_suffix")),
        ]
        street_address = " ".join(part for part in street_parts if part)
        normalized = normalize_address(
            street_address,
            city=LADBS_CITY,
            state=LADBS_STATE,
            postal_code=clean_text(row.get("zip_code")),
            market=market,
        )

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
        mapped_fields = {
            "status_evidence_type": "building_permit_issued",
            "status_evidence_date": issue_date,
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
            "council_district": clean_text(row.get("council_district")),
            "initiating_office": clean_text(row.get("initiating_office")),
            "city": LADBS_CITY,
            "county": LADBS_COUNTY,
            "state": LADBS_STATE,
            "zip": normalized.postal_code,
        }

        return RawRecord(
            source_name=source_name,
            source_record_id=permit_number,
            raw_payload=dict(row),
            canonical_address=normalized.canonical_address,
            project_name=None,
            identifiers={"permit_number": [permit_number]},
            mapped_fields={key: value for key, value in mapped_fields.items() if value is not None},
        )

    return adapter


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

from __future__ import annotations

from tcg_pipeline.source_adapters.ladbs import (
    make_ladbs_cofo_adapter,
    make_ladbs_new_housing_adapter,
    make_ladbs_permit_activity_adapter,
    make_ladbs_permits_adapter,
)


def test_ladbs_adapter_maps_permit_row_to_raw_record() -> None:
    adapter = make_ladbs_permits_adapter(market="los_angeles", source_name="ladbs_permits")

    raw_record = adapter(
        {
            "pcis_permit": "11010-10000-02451",
            "permit_type": "Bldg-New",
            "permit_sub_type": "Apartment",
            "initiating_office": "METRO",
            "issue_date": "2013-01-02T00:00:00.000",
            "address_start": "7270",
            "street_name": "MANCHESTER",
            "street_suffix": "AVE",
            "zip_code": "90045",
            "work_description": "CONSTRUCT NEW MIXED-USE OF RETAILS AND 4-STORY 260-UNIT APT",
            "valuation": "33430000",
            "of_residential_dwelling_units": "260",
            "of_stories": "5",
            "contractors_business_name": "BERNARDS BROS INC",
            "applicant_first_name": "GARY",
            "applicant_last_name": "LEUS",
            "applicant_business_name": "VTBS",
            "zone": "(T)(Q)RAS4-1-CDO",
            "council_district": "11",
        }
    )

    assert raw_record is not None
    assert raw_record.source_record_id == "11010-10000-02451"
    assert raw_record.canonical_address == "7270 MANCHESTER AVENUE LOS ANGELES CA 90045"
    assert raw_record.identifiers == {"permit_number": ["11010-10000-02451"]}
    assert raw_record.mapped_fields["status_evidence_type"] == "building_permit_issued"
    assert raw_record.mapped_fields["status_evidence_date"] == "2013-01-02"
    assert raw_record.mapped_fields["permit_issue_date"] == "2013-01-02"
    assert "date_construction_start" not in raw_record.mapped_fields
    assert raw_record.mapped_fields["total_units"] == 260
    assert raw_record.mapped_fields["stories"] == 5
    assert raw_record.mapped_fields["applicant"] == "VTBS"
    assert raw_record.mapped_fields["zoning"] == "(T)(Q)RAS4-1-CDO"


def test_ladbs_new_housing_adapter_maps_apn_and_coordinates() -> None:
    adapter = make_ladbs_new_housing_adapter(
        market="los_angeles",
        source_name="ladbs_new_housing",
    )

    raw_record = adapter(
        {
            "assessor_book": "5521",
            "assessor_page": "021",
            "assessor_parcel": "012",
            "tract": "LA PALOMA ADDITION",
            "block": "15",
            "lot": "10",
            "pcis_permit": "21010-30000-06142",
            "permit_type": "Bldg-New",
            "permit_sub_type": "1 or 2 Family Dwelling",
            "permit_category": "Plan Check",
            "initiating_office": "WEST LA",
            "issue_date": "2022-05-10T00:00:00.000",
            "address_start": "4460",
            "street_direction": "W",
            "street_name": "MAPLEWOOD",
            "street_suffix": "AVE",
            "zip_code": "90004",
            "work_description": "new 3-story duplex w/ ADU (rear building)",
            "valuation": "450000",
            "of_residential_dwelling_units": "2",
            "of_stories": "3",
            "contractors_business_name": "OWNER-BUILDER",
            "applicant_first_name": "DAN",
            "applicant_last_name": "BIBAWI",
            "zone": "R3-1",
            "census_tract": "1925.20",
            "location_1": {
                "type": "Point",
                "coordinates": [-118.30163, 34.07981],
            },
        }
    )

    assert raw_record is not None
    assert raw_record.source_record_id == "21010-30000-06142"
    assert raw_record.canonical_address == "4460 WEST MAPLEWOOD AVENUE LOS ANGELES CA 90004"
    assert raw_record.identifiers == {
        "permit_number": ["21010-30000-06142"],
        "apn": ["5521021012"],
    }
    assert raw_record.lat == 34.07981
    assert raw_record.lng == -118.30163
    assert raw_record.mapped_fields["status_evidence_type"] == "building_permit_issued"
    assert raw_record.mapped_fields["status_evidence_date"] == "2022-05-10"
    assert raw_record.mapped_fields["total_units"] == 2
    assert raw_record.mapped_fields["stories"] == 3
    assert raw_record.mapped_fields["apn"] == "5521021012"
    assert raw_record.mapped_fields["permit_category"] == "Plan Check"


def test_ladbs_new_housing_adapter_drops_malformed_apn() -> None:
    adapter = make_ladbs_new_housing_adapter(
        market="los_angeles",
        source_name="ladbs_new_housing",
    )

    raw_record = adapter(
        {
            "assessor_book": "55210",
            "assessor_page": "021",
            "assessor_parcel": "012",
            "pcis_permit": "21010-30000-06142",
            "permit_type": "Bldg-New",
            "issue_date": "2022-05-10T00:00:00.000",
            "address_start": "4460",
            "street_direction": "W",
            "street_name": "MAPLEWOOD",
            "street_suffix": "AVE",
            "zip_code": "90004",
            "of_residential_dwelling_units": "2",
            "of_stories": "3",
        }
    )

    assert raw_record is not None
    assert raw_record.identifiers == {"permit_number": ["21010-30000-06142"]}
    assert "apn" not in raw_record.mapped_fields


def test_ladbs_permit_activity_adapter_keeps_permit_detail_without_status_evidence() -> None:
    adapter = make_ladbs_permit_activity_adapter(
        market="los_angeles",
        source_name="ladbs_permit_activity",
    )

    raw_record = adapter(
        {
            "pcis_permit": "23016-90000-16465",
            "permit_type": "Bldg-Alter/Repair",
            "permit_sub_type": "1 or 2 Family Dwelling",
            "initiating_office": "INTERNET",
            "issue_date": "2023-05-19T00:00:00.000",
            "address_start": "8317",
            "street_name": "DENISE",
            "street_suffix": "LANE",
            "zip_code": "91304",
            "work_description": "Replace 1 window(s). Same size, location, number, type.",
            "valuation": "501",
            "contractors_business_name": "HOME DEPOT THE",
            "applicant_first_name": "CA",
            "applicant_last_name": "PERMITS",
            "zone": "RE11-1",
            "council_district": "12",
        }
    )

    assert raw_record is not None
    assert raw_record.source_record_id == "23016-90000-16465"
    assert raw_record.canonical_address == "8317 DENISE LANE LOS ANGELES CA 91304"
    assert raw_record.identifiers == {"permit_number": ["23016-90000-16465"]}
    assert "status_evidence_type" not in raw_record.mapped_fields
    assert "status_evidence_date" not in raw_record.mapped_fields
    assert raw_record.mapped_fields["permit_issue_date"] == "2023-05-19"
    assert raw_record.mapped_fields["permit_type"] == "Bldg-Alter/Repair"
    assert raw_record.mapped_fields["permit_sub_type"] == "1 or 2 Family Dwelling"
    assert raw_record.mapped_fields["council_district"] == "12"


def test_ladbs_cofo_adapter_maps_completion_evidence() -> None:
    adapter = make_ladbs_cofo_adapter(
        market="los_angeles",
        source_name="ladbs_cofo",
    )

    raw_record = adapter(
        {
            "cofo_number": "131279",
            "cofo_issue_date": "2020-01-14T00:00:00.000",
            "latest_status": "CofO Issued",
            "status_date": "2020-01-14T00:00:00.000",
            "assessor_book": "2245",
            "assessor_page": "002",
            "assessor_parcel": "016",
            "tract": "TR 6142",
            "lot": "42",
            "pcis_permit": "13010-20000-00689",
            "permit_type": "Bldg-New",
            "permit_sub_type": "Apartment",
            "permit_category": "Plan Check",
            "initiating_office": "VAN NUYS",
            "issue_date": "2020-01-14T00:00:00.000",
            "address_start": "14409",
            "street_direction": "W",
            "street_name": "TIARA",
            "street_suffix": "ST",
            "zip_code": "91401",
            "work_description": (
                "New fully sprinklered three story, four unit townhouse apartment building"
            ),
            "valuation": "900000",
            "of_residential_dwelling_units": "4",
            "of_stories": "3",
            "contractors_business_name": "ROCKPORT DEVELOPMENT INC",
            "applicant_business_name": "APEL DESIGN INC",
            "zone": "[Q]RD1.5-1",
            "latitude_longitude": {
                "latitude": "34.1786",
                "longitude": "-118.44681",
            },
        }
    )

    assert raw_record is not None
    assert raw_record.source_record_id == "131279"
    assert raw_record.canonical_address == "14409 WEST TIARA STREET LOS ANGELES CA 91401"
    assert raw_record.identifiers == {
        "permit_number": ["13010-20000-00689"],
        "apn": ["2245002016"],
    }
    assert raw_record.lat == 34.1786
    assert raw_record.lng == -118.44681
    assert raw_record.mapped_fields["status_evidence_type"] == "certificate_of_occupancy_issued"
    assert raw_record.mapped_fields["status_evidence_date"] == "2020-01-14"
    assert raw_record.mapped_fields["status_date"] == "2020-01-14"
    assert raw_record.mapped_fields["date_delivery"] == "2020-01-14"
    assert raw_record.mapped_fields["cofo_number"] == "131279"
    assert raw_record.mapped_fields["latest_status"] == "CofO Issued"
    assert raw_record.mapped_fields["total_units"] == 4


def test_ladbs_cofo_adapter_requires_cofo_number() -> None:
    adapter = make_ladbs_cofo_adapter(
        market="los_angeles",
        source_name="ladbs_cofo",
    )

    raw_record = adapter(
        {
            "cofo_issue_date": "2020-01-14T00:00:00.000",
            "latest_status": "CofO Issued",
            "pcis_permit": "13010-20000-00689",
            "address_start": "14409",
            "street_direction": "W",
            "street_name": "TIARA",
            "street_suffix": "ST",
            "zip_code": "91401",
        }
    )

    assert raw_record is None


def test_ladbs_cofo_adapter_does_not_emit_final_evidence_for_corrected_status() -> None:
    adapter = make_ladbs_cofo_adapter(
        market="los_angeles",
        source_name="ladbs_cofo",
    )

    raw_record = adapter(
        {
            "cofo_number": "131279",
            "cofo_issue_date": "2020-01-14T00:00:00.000",
            "latest_status": "CofO Corrected",
            "pcis_permit": "13010-20000-00689",
            "address_start": "14409",
            "street_direction": "W",
            "street_name": "TIARA",
            "street_suffix": "ST",
            "zip_code": "91401",
        }
    )

    assert raw_record is not None
    assert "status_evidence_type" not in raw_record.mapped_fields
    assert "status_evidence_date" not in raw_record.mapped_fields
    assert raw_record.mapped_fields["cofo_issue_date"] == "2020-01-14"


def test_ladbs_cofo_adapter_does_not_emit_final_evidence_without_cofo_issue_date() -> None:
    adapter = make_ladbs_cofo_adapter(
        market="los_angeles",
        source_name="ladbs_cofo",
    )

    raw_record = adapter(
        {
            "cofo_number": "131279",
            "latest_status": "CofO Issued",
            "status_date": "2020-01-14T00:00:00.000",
            "pcis_permit": "13010-20000-00689",
            "address_start": "14409",
            "street_direction": "W",
            "street_name": "TIARA",
            "street_suffix": "ST",
            "zip_code": "91401",
        }
    )

    assert raw_record is not None
    assert "status_evidence_type" not in raw_record.mapped_fields
    assert "status_evidence_date" not in raw_record.mapped_fields
    assert raw_record.mapped_fields["status_date"] == "2020-01-14"

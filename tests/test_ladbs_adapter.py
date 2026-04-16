from __future__ import annotations

from tcg_pipeline.source_adapters.ladbs import (
    make_ladbs_new_housing_adapter,
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

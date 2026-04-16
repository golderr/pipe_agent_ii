from __future__ import annotations

from tcg_pipeline.source_adapters.ladbs import make_ladbs_permits_adapter


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

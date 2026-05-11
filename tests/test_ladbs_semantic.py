from __future__ import annotations

import pytest

from tcg_pipeline.db.models import ProductType
from tcg_pipeline.semantic.ladbs import (
    enrich_ladbs_mapped_fields,
    interpret_ladbs_mapped_fields,
    ladbs_semantic_metadata_by_field,
)


def test_ladbs_semantic_interprets_apartment_from_use_desc() -> None:
    fields = {
        "status_evidence_type": "building_permit_issued",
        "use_desc": "Apartment",
        "permit_sub_type": "Commercial",
    }

    interpretations = interpret_ladbs_mapped_fields(
        source_name="ladbs_permits",
        mapped_fields=fields,
    )

    assert len(interpretations) == 1
    interpretation = interpretations[0]
    assert interpretation.field_name == "product_type"
    assert interpretation.canonical_value == "Apartment"
    assert interpretation.confidence == "high"
    assert interpretation.reason_code == "ladbs_product_type_apartment"
    assert interpretation.source_anchors[0].field_name == "use_desc"


def test_ladbs_semantic_interprets_condo_from_description() -> None:
    fields = {
        "status_evidence_type": "building_permit_issued",
        "permit_sub_type": "Commercial",
        "description": "Construct new 42-unit condominium building.",
    }

    enriched = enrich_ladbs_mapped_fields(
        source_name="ladbs_permits",
        mapped_fields=fields,
    )

    assert enriched["product_type"] == "Condo"
    metadata = ladbs_semantic_metadata_by_field(
        source_name="ladbs_permits",
        mapped_fields=enriched,
    )
    assert metadata["product_type"].reason_code == "ladbs_product_type_condo"
    assert metadata["product_type"].confidence == "medium"


@pytest.mark.parametrize(
    ("source_field", "source_text", "expected_product_type", "reason_code"),
    [
        (
            "use_desc",
            "Apartment",
            ProductType.APARTMENT,
            "ladbs_product_type_apartment",
        ),
        (
            "description",
            "Construct new 42-unit condominium building.",
            ProductType.CONDO,
            "ladbs_product_type_condo",
        ),
        (
            "description",
            "Construct new 16-unit townhome community.",
            ProductType.TOWNHOME,
            "ladbs_product_type_townhome",
        ),
        (
            "permit_sub_type",
            "1 or 2 Family Dwelling",
            ProductType.SINGLE_FAMILY,
            "ladbs_product_type_single_family",
        ),
        (
            "description",
            "Construct new 50 micro-unit co-living apartment building.",
            ProductType.MICRO_CO_LIVING,
            "ladbs_product_type_micro_co_living",
        ),
        (
            "description",
            "Construct new 50 micro unit apartment building.",
            ProductType.MICRO_CO_LIVING,
            "ladbs_product_type_micro_co_living",
        ),
        (
            "description",
            "Construct new co-living apartment building.",
            ProductType.MICRO_CO_LIVING,
            "ladbs_product_type_micro_co_living",
        ),
    ],
)
def test_ladbs_semantic_interprets_product_type_variants(
    source_field: str,
    source_text: str,
    expected_product_type: ProductType,
    reason_code: str,
) -> None:
    fields = {
        "status_evidence_type": "building_permit_issued",
        source_field: source_text,
    }

    enriched = enrich_ladbs_mapped_fields(
        source_name="ladbs_permits",
        mapped_fields=fields,
    )
    interpretations = interpret_ladbs_mapped_fields(
        source_name="ladbs_permits",
        mapped_fields=enriched,
    )

    assert enriched["product_type"] == expected_product_type.value
    assert len(interpretations) == 1
    assert interpretations[0].canonical_value == expected_product_type.value
    assert interpretations[0].reason_code == reason_code
    assert interpretations[0].source_anchors[0].field_name == source_field


def test_ladbs_semantic_prefers_housing_use_desc_over_use_desc() -> None:
    fields = {
        "status_evidence_type": "building_permit_issued",
        "housing_use_desc": "Apartment",
        "use_desc": "Condominium",
    }

    enriched = enrich_ladbs_mapped_fields(
        source_name="ladbs_permits",
        mapped_fields=fields,
    )
    interpretations = interpret_ladbs_mapped_fields(
        source_name="ladbs_permits",
        mapped_fields=enriched,
    )

    assert enriched["product_type"] == ProductType.APARTMENT.value
    assert len(interpretations) == 1
    assert interpretations[0].canonical_value == ProductType.APARTMENT.value
    assert interpretations[0].source_anchors[0].field_name == "housing_use_desc"


def test_ladbs_semantic_does_not_interpret_permit_activity_without_direct_evidence() -> None:
    enriched = enrich_ladbs_mapped_fields(
        source_name="ladbs_permit_activity",
        mapped_fields={
            "permit_type": "Bldg-Alter/Repair",
            "use_desc": "Dwelling - Single Family",
            "permit_sub_type": "1 or 2 Family Dwelling",
        },
    )

    assert "product_type" not in enriched
    assert (
        interpret_ladbs_mapped_fields(
            source_name="ladbs_permit_activity",
            mapped_fields=enriched,
        )
        == ()
    )


def test_ladbs_semantic_ignores_non_ladbs_permit_sources() -> None:
    enriched = enrich_ladbs_mapped_fields(
        source_name="costar",
        mapped_fields={
            "status_evidence_type": "building_permit_issued",
            "use_desc": "Apartment",
        },
    )

    assert "product_type" not in enriched

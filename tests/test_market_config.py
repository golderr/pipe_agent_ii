from __future__ import annotations

from tcg_pipeline.market_config import load_market_config


def test_load_market_config_reads_ladbs_source_metadata() -> None:
    config = load_market_config("los_angeles")

    source = config.get_source("ladbs_permits")
    housing_source = config.get_source("ladbs_new_housing")

    assert config.market == "los_angeles"
    assert source.collector == "socrata"
    assert source.adapter_name == "ladbs_permits"
    assert source.jurisdiction == "city_of_los_angeles"
    assert source.coverage_scope == "city"
    assert source.matching_keys == ["permit_number", "canonical_address"]
    assert source.effective_where == "permit_type='Bldg-New'"
    assert housing_source.adapter_name == "ladbs_new_housing"
    assert housing_source.jurisdiction == "city_of_los_angeles"
    assert housing_source.coverage_scope == "city"
    assert housing_source.matching_keys == ["permit_number", "apn", "canonical_address"]

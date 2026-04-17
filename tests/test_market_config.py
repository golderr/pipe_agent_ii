from __future__ import annotations

import pytest

from tcg_pipeline.market_config import load_market_config


def test_load_market_config_reads_ladbs_source_metadata() -> None:
    config = load_market_config("los_angeles")

    permits_source = config.get_source("ladbs_permits")
    activity_source = config.get_source("ladbs_permit_activity")
    inspections_source = config.get_source("ladbs_inspections")
    cofo_source = config.get_source("ladbs_cofo")

    assert config.market == "los_angeles"

    assert permits_source.collector == "socrata"
    assert permits_source.adapter_name == "ladbs_permits_pi9x_tg5x"
    assert permits_source.endpoint == "https://data.lacity.org/resource/pi9x-tg5x.json"
    assert permits_source.jurisdiction == "city_of_los_angeles"
    assert permits_source.coverage_scope == "city"
    assert permits_source.matching_keys == ["permit_number", "apn", "canonical_address"]
    assert permits_source.effective_where == "permit_type='Bldg-New'"

    assert activity_source.adapter_name == "ladbs_permit_activity_pi9x_tg5x"
    assert activity_source.endpoint == "https://data.lacity.org/resource/pi9x-tg5x.json"
    assert activity_source.jurisdiction == "city_of_los_angeles"
    assert activity_source.coverage_scope == "city"
    assert activity_source.matching_keys == ["permit_number", "apn", "canonical_address"]
    assert activity_source.effective_where == "permit_type != 'Bldg-New'"
    assert activity_source.create_new_candidates is False

    assert inspections_source.adapter_name == "ladbs_inspections_9w5z_rg2h"
    assert inspections_source.endpoint == "https://data.lacity.org/resource/9w5z-rg2h.json"
    assert inspections_source.jurisdiction == "city_of_los_angeles"
    assert inspections_source.coverage_scope == "city"
    assert inspections_source.matching_keys == ["permit_number", "canonical_address"]
    assert inspections_source.create_new_candidates is False

    assert cofo_source.adapter_name == "ladbs_cofo"
    assert cofo_source.jurisdiction == "city_of_los_angeles"
    assert cofo_source.coverage_scope == "city"
    assert cofo_source.matching_keys == ["permit_number", "apn", "canonical_address"]


def test_load_market_config_no_longer_exposes_legacy_ladbs_new_housing_source() -> None:
    config = load_market_config("los_angeles")

    with pytest.raises(KeyError):
        config.get_source("ladbs_new_housing")

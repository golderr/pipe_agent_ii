from tcg_pipeline.matching.normalizer import (
    normalize_address,
    normalize_city,
    normalize_postal_code,
    normalize_state,
    parse_address_range,
)


def test_normalize_embedded_address_components_and_strip_unit() -> None:
    normalized = normalize_address("1437 7TH ST, APT 2B, Santa Monica, CA 90401")

    assert normalized.canonical_street_line == "1437 7TH STREET"
    assert normalized.canonical_address == "1437 7TH STREET SANTA MONICA CA 90401"
    assert normalized.unit == "APT 2B"
    assert normalized.house_number_start == 1437
    assert normalized.house_number_end == 1437


def test_normalize_directionals_and_suffixes() -> None:
    normalized = normalize_address("5939 W Sunset Blvd", city="Los Angeles", state="CA")

    assert normalized.canonical_street_line == "5939 WEST SUNSET BOULEVARD"
    assert normalized.canonical_address == "5939 WEST SUNSET BOULEVARD LOS ANGELES CA"


def test_normalize_s_figueroa_variants_to_same_result() -> None:
    first = normalize_address("601 S. Figueroa St", city="Los Angeles", state="CA")
    second = normalize_address("601 South Figueroa Street", city="Los Angeles", state="CA")

    assert first.canonical_address == second.canonical_address
    assert first.canonical_address == "601 SOUTH FIGUEROA STREET LOS ANGELES CA"


def test_preserve_directional_street_name_component() -> None:
    normalized = normalize_address("1718 N Las Palmas Ave", city="Los Angeles", state="CA")

    assert normalized.canonical_street_line == "1718 NORTH LAS PALMAS AVENUE"
    assert normalized.canonical_address == "1718 NORTH LAS PALMAS AVENUE LOS ANGELES CA"


def test_address_range_is_preserved_and_parsed() -> None:
    normalized = normalize_address("1435-1441 7th St", city="Santa Monica", state="CA")

    assert normalized.canonical_street_line == "1435-1441 7TH STREET"
    assert normalized.house_number_start == 1435
    assert normalized.house_number_end == 1441
    assert normalized.has_range is True


def test_numbered_street_word_variant_normalizes() -> None:
    normalized = normalize_address("1437 Seventh Street", city="Santa Monica", state="CA")

    assert normalized.canonical_street_line == "1437 7TH STREET"


def test_los_angeles_market_city_aliases() -> None:
    assert normalize_city("Los Angeles CBD", market="los_angeles") == "LOS ANGELES"
    assert normalize_city("Downtown Los Angeles", market="los_angeles") == "LOS ANGELES"
    assert normalize_city("DTLA", market="los_angeles") == "LOS ANGELES"
    assert normalize_city("Hollywood", market="los_angeles") == "LOS ANGELES"


def test_state_and_zip_normalization() -> None:
    assert normalize_state("California") == "CA"
    assert normalize_state("ca") == "CA"
    assert normalize_postal_code("90057-3106") == "90057"


def test_parse_address_range_helper() -> None:
    assert parse_address_range("1435-1441") == (1435, 1441)
    assert parse_address_range("1437") == (1437, 1437)

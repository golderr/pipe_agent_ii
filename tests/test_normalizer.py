import pytest

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


@pytest.mark.parametrize(
    ("raw_address", "expected_street_line"),
    [
        ("602 S Westlake Ave", "602 SOUTH WESTLAKE AVENUE"),
        ("549 S Harvard Blvd", "549 SOUTH HARVARD BOULEVARD"),
        ("407-413 E 5th St", "407-413 EAST 5TH STREET"),
        ("W 3rd St", "WEST 3RD STREET"),
        ("S La Brea Ave", "SOUTH LA BREA AVENUE"),
    ],
)
def test_known_los_angeles_edge_cases_are_stable(
    raw_address: str,
    expected_street_line: str,
) -> None:
    normalized = normalize_address(raw_address, city="Los Angeles", state="CA", market="los_angeles")

    assert normalized.canonical_street_line == expected_street_line
    assert normalized.canonical_address == f"{expected_street_line} LOS ANGELES CA"
    assert normalized.parser == "usaddress"


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


def test_normalize_address_falls_back_when_parser_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_parse_error(_raw: str) -> list[tuple[str, str]]:
        raise ValueError("synthetic parse failure")

    monkeypatch.setattr("tcg_pipeline.matching.normalizer.usaddress.parse", raise_parse_error)

    normalized = normalize_address("123 Main St, Apt 4, Los Angeles, CA 90012", market="los_angeles")

    assert normalized.parser == "fallback"
    assert normalized.canonical_street_line == "123 MAIN STREET"
    assert normalized.canonical_address == "123 MAIN STREET LOS ANGELES CA 90012"
    assert normalized.unit == "APT 4"
    assert normalized.house_number_start == 123
    assert normalized.house_number_end == 123

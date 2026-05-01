from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from tcg_pipeline.db.models import DeveloperAlias, DeveloperRegistry, Project
from tcg_pipeline.news.structural import (
    STRUCTURAL_EXTRACTOR_VERSION,
    apply_structural_signals,
    build_structural_signals_payload,
    extract_structural_signals,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "news"


def test_structural_regex_extractors_capture_core_article_signals() -> None:
    body_text = (
        "On April 28, 2026, Acme filed plans for a 310-unit apartment project at "
        "1234 West Sunset Boulevard, Los Angeles, CA 90026. Case ENV-2026-1234-EIR "
        "and permit 24A123-00000-00000 reference APN 1234-567-890. The project is "
        "expected to deliver in Q4 2027 with 32 affordable units, senior housing, "
        "and now leasing."
    )

    signals = extract_structural_signals(body_text, market_slug="los_angeles")
    by_extractor = {signal.extractor: signal for signal in signals}

    assert {
        "unit_count",
        "address",
        "case_number",
        "permit_number",
        "apn",
        "date",
        "status_phrase",
        "delivery_phrase",
        "product_type_phrase",
        "age_restriction_phrase",
        "affordable_split_phrase",
    }.issubset(by_extractor)
    assert by_extractor["unit_count"].canonical == 310
    assert by_extractor["address"].canonical["canonical_address"].startswith(
        "1234 WEST SUNSET"
    )
    assert by_extractor["address"].raw_match.endswith("CA 90026")
    assert by_extractor["address"].canonical["zip"] == "90026"
    assert by_extractor["case_number"].canonical == "ENV-2026-1234-EIR"
    assert by_extractor["permit_number"].canonical == "24A1230000000000"
    assert by_extractor["apn"].canonical == "1234-567-890"
    assert by_extractor["delivery_phrase"].canonical == "2027-11-01"
    assert by_extractor["age_restriction_phrase"].canonical == "senior"
    assert [signal.raw_match for signal in signals if signal.extractor == "date"] == [
        "On April 28, 2026"
    ]

    for signal in signals:
        assert body_text[signal.offset_start : signal.offset_end] == signal.raw_match


def test_structural_unit_count_ignores_partial_match_inside_price() -> None:
    signals = extract_structural_signals(
        "The report mentioned a $200,000 unit price and a 140-unit project.",
        market_slug="los_angeles",
    )

    unit_counts = [signal for signal in signals if signal.extractor == "unit_count"]
    assert [(signal.raw_match, signal.canonical) for signal in unit_counts] == [
        ("140-unit", 140)
    ]


def test_structural_unit_count_captures_comma_formatted_counts() -> None:
    signals = extract_structural_signals(
        "Work begins on a mixed-use plan with 2,250 residential units.",
        market_slug="los_angeles",
    )

    unit_signal = next(signal for signal in signals if signal.extractor == "unit_count")
    assert unit_signal.raw_match == "2,250 residential units"
    assert unit_signal.canonical == 2250


def test_structural_address_scans_title_text() -> None:
    title_text = "Affordable housing pitched for property at 2101 W. 8th Street in Westlake"
    body_text = "The proposal would replace a commercial building with apartments."

    signals = extract_structural_signals(
        body_text,
        title_text=title_text,
        market_slug="los_angeles",
    )

    address_signal = next(signal for signal in signals if signal.extractor == "address")
    assert address_signal.raw_match == "2101 W. 8th Street"
    assert address_signal.metadata["source"] == "title"
    assert title_text[address_signal.offset_start : address_signal.offset_end] == (
        address_signal.raw_match
    )
    assert address_signal.canonical["canonical_address"].startswith(
        "2101 WEST 8TH STREET"
    )


def test_structural_delivery_phrase_captures_completion_expected_form() -> None:
    signals = extract_structural_signals(
        "Completion is expected in Fall 2027, according to the developer.",
        market_slug="los_angeles",
    )

    delivery_signal = next(
        signal for signal in signals if signal.extractor == "delivery_phrase"
    )
    assert delivery_signal.raw_match == "Completion is expected in Fall 2027"
    assert delivery_signal.canonical == "2027-10-01"


def test_urbanize_validation_fixtures_cover_pass1_tuning_gaps() -> None:
    fixture_path = FIXTURE_ROOT / "urbanize_la" / "pass1_validation_articles.json"
    articles = {
        item["slug"]: item
        for item in json.loads(fixture_path.read_text(encoding="utf-8"))
    }

    westlake_signals = extract_structural_signals(
        articles["westlake_2101_w_8th"]["body_text"],
        title_text=articles["westlake_2101_w_8th"]["title"],
        market_slug="los_angeles",
    )
    title_address = next(
        signal
        for signal in westlake_signals
        if signal.extractor == "address" and signal.metadata["source"] == "title"
    )
    assert title_address.raw_match == "2101 W. 8th Street"

    womens_center_signals = extract_structural_signals(
        articles["downtown_womens_center_501_e_5th"]["body_text"],
        title_text=articles["downtown_womens_center_501_e_5th"]["title"],
        market_slug="los_angeles",
    )
    completion_signal = next(
        signal
        for signal in womens_center_signals
        if signal.extractor == "delivery_phrase"
    )
    assert completion_signal.raw_match == "Completion is expected in Fall 2027"
    assert completion_signal.canonical == "2027-10-01"

    westminster_signals = extract_structural_signals(
        articles["westminster_mall_2250_units"]["body_text"],
        title_text=articles["westminster_mall_2250_units"]["title"],
        market_slug="los_angeles",
    )
    assert any(
        signal.extractor == "unit_count"
        and signal.raw_match == "2,250 residential units"
        and signal.canonical == 2250
        for signal in westminster_signals
    )


def test_structural_date_signals_anchor_relative_dates_to_publication_date() -> None:
    signals = extract_structural_signals(
        "The city hearing is Tuesday.",
        published_at=datetime(2026, 4, 29, 12, 0, tzinfo=UTC),
    )

    date_signal = next(signal for signal in signals if signal.extractor == "date")
    assert date_signal.raw_match == "Tuesday"
    assert date_signal.canonical == "2026-04-28"


def test_structural_payload_has_version_timestamp_and_sorted_signals() -> None:
    now = datetime(2026, 4, 29, 12, 0, tzinfo=UTC)

    payload = build_structural_signals_payload(
        "Developer announced a 140-unit project on April 28, 2026.",
        now=now,
    )

    assert payload["extractor_version"] == STRUCTURAL_EXTRACTOR_VERSION
    assert payload["ran_at"] == now.isoformat()
    assert payload["signals"]
    offsets = [
        (signal["offset_start"], signal["offset_end"], signal["extractor"])
        for signal in payload["signals"]
    ]
    assert offsets == sorted(offsets)


def test_structural_dictionary_extractors_use_developer_and_project_registry(
    postgres_session: Session,
) -> None:
    unique_id = uuid.uuid4().hex
    developer = DeveloperRegistry(canonical_name=f"Atlas Development {unique_id}")
    postgres_session.add(developer)
    postgres_session.flush()
    alias = DeveloperAlias(
        developer_id=developer.id,
        alias_name=f"Atlas Devco {unique_id}",
    )
    project = Project(
        canonical_address=f"100 {unique_id[:8]} Main St",
        market=f"test-market-{unique_id}",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        project_name=f"Temple Yard {unique_id}",
        previous_names=[f"Temple Phase One {unique_id}"],
    )
    postgres_session.add_all([alias, project])
    postgres_session.flush()
    body_text = (
        f"Atlas Devco {unique_id} said Temple Phase One {unique_id} will add "
        "housing near downtown."
    )

    signals = extract_structural_signals(
        body_text,
        session=postgres_session,
        market_slug=project.market,
    )

    developer_signal = next(
        signal for signal in signals if signal.extractor == "developer_dict"
    )
    project_signal = next(signal for signal in signals if signal.extractor == "project_dict")
    assert developer_signal.canonical == str(developer.id)
    assert developer_signal.metadata["display_name"] == developer.canonical_name
    assert project_signal.canonical == str(project.id)
    assert project_signal.metadata["display_name"] == project.project_name


def test_apply_structural_signals_writes_article_payload(postgres_session: Session) -> None:
    article = type(
        "ArticleStub",
        (),
        {
            "body_text": "Developer announced a 140-unit project in Los Angeles.",
            "published_at": None,
            "structural_signals": None,
            "structural_signals_at": None,
        },
    )()
    now = datetime(2026, 4, 29, 12, 0, tzinfo=UTC)

    apply_structural_signals(
        postgres_session,
        article=article,
        market_slug="los_angeles",
        market_id=None,
        now=now,
    )

    assert article.structural_signals_at == now
    assert article.structural_signals["ran_at"] == now.isoformat()
    assert article.structural_signals["signals"][0]["extractor"] == "unit_count"

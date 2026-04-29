from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from tcg_pipeline.db.models import DeveloperAlias, DeveloperRegistry, Project
from tcg_pipeline.news.structural import (
    STRUCTURAL_EXTRACTOR_VERSION,
    apply_structural_signals,
    build_structural_signals_payload,
    extract_structural_signals,
)


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

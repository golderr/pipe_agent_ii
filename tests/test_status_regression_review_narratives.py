"""Focused tests for the structured-source regression-review narrative
generator (UX.narrative-detail). The generator lives in
``tcg_pipeline.db.status_regression_reviews`` as a private helper. These tests
exercise it directly because integration coverage in test_collect_source.py
verifies wiring but doesn't assert on the new permit-type / permit-number
phrasing introduced by UX.narrative-detail.
"""

from __future__ import annotations

from typing import Any

from tcg_pipeline.db.status_regression_reviews import (
    _structured_source_phrase,
)


def _candidate(**overrides: Any) -> dict[str, Any]:
    base = {
        "current_status": "Under Construction",
        "proposed_status": "Approved",
        "rank_delta": 1,
        "evidence_date": "2026-05-13",
        "evidence_ids": ["00000000-0000-0000-0000-000000000001"],
        "source_type": "ladbs_permit",
        "source_tier": 1,
        "evidence_type": "building_permit_issued",
    }
    base.update(overrides)
    return base


def test_ladbs_phrase_names_permit_type_and_number() -> None:
    phrase = _structured_source_phrase(
        source_label="LADBS",
        candidate_date="May 13, 2026",
        candidates=[
            _candidate(
                permit_type="Bldg-New",
                permit_number="19010-10000-00001",
                status_desc="Cancelled",
            ),
        ],
    )
    assert "LADBS" in phrase
    assert "Bldg-New" in phrase
    assert "#19010-10000-00001" in phrase
    assert "Cancelled" in phrase
    assert "May 13, 2026" in phrase


def test_ladbs_phrase_falls_back_when_permit_type_missing() -> None:
    """Older or partial LADBS evidence may not have permit_type populated.
    Phrase should still be useful; falls back to 'LADBS permit'."""
    phrase = _structured_source_phrase(
        source_label="LADBS",
        candidate_date="May 13, 2026",
        candidates=[_candidate()],
    )
    assert phrase.startswith("LADBS permit")
    assert "May 13, 2026" in phrase


def test_costar_phrase_names_upload_date() -> None:
    phrase = _structured_source_phrase(
        source_label="CoStar",
        candidate_date="May 13, 2026",
        candidates=[_candidate(source_type="costar")],
    )
    assert phrase == "CoStar upload from May 13, 2026"


def test_pipedream_phrase_uses_legacy_signal_format() -> None:
    """Pipedream regressions currently use the prior signal-phrase format
    because Pipedream sync isn't yet routing into this path in production."""
    phrase = _structured_source_phrase(
        source_label="Pipedream",
        candidate_date="May 13, 2026",
        candidates=[_candidate(source_type="pipedream")],
    )
    assert phrase == "Pipedream May 13, 2026 signal"

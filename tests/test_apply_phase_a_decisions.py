from __future__ import annotations

from scripts.apply_phase_a_decisions import (
    _phase_a_bucket_decision,
    _serialize_for_csv_compare,
)


def test_phase_a_bucket_decision_defers_large_unit_deltas() -> None:
    decision, notes, override_source = _phase_a_bucket_decision(
        "units_review.csv",
        {"delta_abs": "12"},
    )

    assert decision == "defer"
    assert override_source == "current"
    assert "delta > 5" in notes


def test_phase_a_bucket_decision_accepts_raw_developer_when_engine_canonicalizes() -> None:
    decision, notes, override_source = _phase_a_bucket_decision(
        "developer_review.csv",
        {
            "raw_value": "Walter J Company",
            "resolved_value": "Walter J. Samson",
        },
    )

    assert decision == "override"
    assert override_source == "raw"
    assert "Walter J Company" in notes


def test_phase_a_bucket_decision_keeps_current_for_architecture_firms() -> None:
    decision, notes, override_source = _phase_a_bucket_decision(
        "developer_review.csv",
        {
            "raw_value": "MVE + Partners",
            "resolved_value": "MVE + Partners",
        },
    )

    assert decision == "override"
    assert override_source == "current"
    assert "architecture-firm exception" in notes


def test_phase_a_bucket_decision_accepts_cleanup_rows() -> None:
    decision, notes, override_source = _phase_a_bucket_decision(
        "developer_canonical_cleanup.csv",
        {},
    )

    assert decision == "accept"
    assert override_source is None
    assert "data hygiene" in notes


def test_serialize_for_csv_compare_matches_csv_string_values() -> None:
    assert _serialize_for_csv_compare(293) == "293"
    assert _serialize_for_csv_compare(None) == ""

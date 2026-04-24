from __future__ import annotations

from scripts.export_phase_a_reviews import _is_developer_canonical_cleanup_row
from scripts.phase_a_review_common import (
    HELIO_CLUSTER_KEY,
    classify_delta_shape,
    developer_review_cluster,
    developer_review_sort_key,
    is_likely_alias_candidate,
    select_delivery_estimate_spotcheck,
)


def test_classify_delta_shape_covers_expected_transitions() -> None:
    assert classify_delta_shape(None, "value") == "null_to_value"
    assert classify_delta_shape("value", None) == "value_to_null"
    assert classify_delta_shape("old", "new") == "value_changed"
    assert classify_delta_shape("same", "same") == "unchanged"


def test_developer_review_sort_prioritizes_canonicalized_and_clusters_helio() -> None:
    canonicalized_row = {
        "rule_applied": "most_recent_wins_canonicalized",
        "review_cluster": "",
        "current_value": "Alpha",
        "resolved_value": "Beta",
        "canonical_address": "100 TEST ST",
    }
    helio_row = {
        "rule_applied": "most_recent_wins_canonicalized",
        "review_cluster": HELIO_CLUSTER_KEY,
        "current_value": "Helio / UCLA",
        "resolved_value": "SAFCO Capital",
        "canonical_address": "200 TEST ST",
    }
    review_required_row = {
        "rule_applied": "most_recent_wins_canonicalization_review_required",
        "review_cluster": "",
        "current_value": "Gamma",
        "resolved_value": "Delta",
        "canonical_address": "300 TEST ST",
    }

    ordered = sorted(
        [review_required_row, canonicalized_row, helio_row],
        key=developer_review_sort_key,
    )

    assert ordered[0] is helio_row
    assert ordered[1] is canonicalized_row
    assert ordered[2] is review_required_row


def test_developer_review_cluster_flags_helio_rows() -> None:
    assert developer_review_cluster("Helio / UCLA") == HELIO_CLUSTER_KEY
    assert developer_review_cluster("Jamison Services") == ""


def test_likely_alias_candidates_only_capture_expected_pairs() -> None:
    assert is_likely_alias_candidate(
        {
            "current_value": "Jamison Services",
            "resolved_value": "Jamison Properties",
        }
    )
    assert is_likely_alias_candidate(
        {
            "current_value": "Wiseman Development",
            "resolved_value": "Wiseman Residential",
        }
    )
    assert not is_likely_alias_candidate(
        {
            "current_value": "Helio / UCLA",
            "resolved_value": "SAFCO Capital",
        }
    )


def test_delivery_estimate_spotcheck_is_stable() -> None:
    rows = [
        {
            "project_id": f"00000000-0000-0000-0000-{index:012d}",
            "canonical_address": f"{index} TEST ST",
        }
        for index in range(20)
    ]

    first = select_delivery_estimate_spotcheck(rows, sample_size=5, seed=1234)
    second = select_delivery_estimate_spotcheck(rows, sample_size=5, seed=1234)

    assert first == second
    assert len(first) == 5


def test_developer_canonical_cleanup_row_captures_exact_alias_cleanup() -> None:
    assert _is_developer_canonical_cleanup_row(
        {
            "current_value": "Jamison Services",
            "resolved_value": "Jamison Properties",
            "canonical_name": "Jamison Properties",
            "match_type": "exact_alias",
        }
    )


def test_developer_canonical_cleanup_row_excludes_substantive_review_rows() -> None:
    assert not _is_developer_canonical_cleanup_row(
        {
            "current_value": "Helio / UCLA",
            "resolved_value": "Beach City Capital LLC",
            "canonical_name": "Beach City Capital LLC",
            "match_type": "new_registry_entry",
        }
    )
    assert not _is_developer_canonical_cleanup_row(
        {
            "current_value": "Walter J. Samson",
            "resolved_value": "Walter J. Samson",
            "canonical_name": "Walter J. Samson",
            "match_type": "exact_canonical",
        }
    )

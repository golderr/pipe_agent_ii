from __future__ import annotations

import pytest

from tcg_pipeline.db.models import ProductType
from tcg_pipeline.matching.similarity import (
    MatchSignal,
    developer_match_score,
    geographic_proximity_score,
    product_type_match_score,
    unit_count_proximity_score,
    weighted_match_likelihood,
)


def test_weighted_match_likelihood_rebalances_missing_subject_fields() -> None:
    signals = {
        "geographic": MatchSignal(
            score=0.0,
            contributed=False,
            searched=False,
            label="Location",
            weight=0.30,
        ),
        "address": MatchSignal(
            score=1.0,
            contributed=True,
            searched=True,
            label="Address",
            weight=0.25,
        ),
        "developer": MatchSignal(
            score=1.0,
            contributed=True,
            searched=True,
            label="Developer",
            weight=0.20,
        ),
    }

    assert weighted_match_likelihood(signals) == 1.0


def test_geographic_proximity_score_falls_to_zero_at_one_km() -> None:
    assert geographic_proximity_score(0.0) == 1.0
    assert geographic_proximity_score(1_000.0) == 0.0
    assert 0.0 < geographic_proximity_score(500.0) < 1.0


@pytest.mark.parametrize(
    ("subject_units", "project_units", "expected"),
    [
        (100, 104, 1.0),
        (100, 100, 1.0),
        (100, 200, 0.0),
    ],
)
def test_unit_count_proximity_score(
    subject_units: int,
    project_units: int,
    expected: float,
) -> None:
    assert unit_count_proximity_score(subject_units, project_units) == expected


def test_developer_match_score_weights_exact_above_name_similarity() -> None:
    assert developer_match_score("The Panorama Group LLC", "Panorama Group") == 1.0
    assert developer_match_score("Helio", "Related California") == 0.0


def test_product_type_match_score_accepts_enum_and_prompt_values() -> None:
    assert product_type_match_score("apartment", ProductType.APARTMENT) == 1.0
    assert product_type_match_score("condo", ProductType.APARTMENT) == 0.0

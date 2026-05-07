from __future__ import annotations

from pathlib import Path

import pytest

from tcg_pipeline.semantic.jurisdiction import (
    load_jurisdiction_policy,
    parse_jurisdiction_policy,
)


def test_missing_jurisdiction_policy_defaults_to_low_quality(tmp_path: Path) -> None:
    policy = load_jurisdiction_policy("new_city", config_dir=tmp_path)

    assert policy.slug == "new_city"
    assert policy.permit_data_quality == "low"
    assert policy.news_status_promotion_policy == "auto_promote_unverified"
    assert policy.is_default is True
    assert policy.as_prompt_payload()["policy_source"] == "default"


def test_current_jurisdiction_policies_load_expected_values() -> None:
    la_policy = load_jurisdiction_policy("city_of_los_angeles")
    sm_policy = load_jurisdiction_policy("city_of_santa_monica")

    assert la_policy.permit_data_quality == "high"
    assert la_policy.news_status_promotion_policy == "wait_for_permit_corroboration"
    assert sm_policy.permit_data_quality == "low"
    assert sm_policy.news_status_promotion_policy == "auto_promote_unverified"


def test_jurisdiction_policy_rejects_slug_mismatch() -> None:
    with pytest.raises(ValueError, match="declared slug"):
        parse_jurisdiction_policy(
            {
                "slug": "wrong_city",
                "permit_data_quality": "low",
                "news_status_promotion_policy": "auto_promote_unverified",
            },
            jurisdiction_slug="right_city",
        )


def test_jurisdiction_policy_rejects_invalid_quality() -> None:
    with pytest.raises(ValueError, match="permit_data_quality"):
        parse_jurisdiction_policy(
            {
                "permit_data_quality": "medium",
                "news_status_promotion_policy": "auto_promote_unverified",
            },
            jurisdiction_slug="new_city",
        )


def test_jurisdiction_policy_rejects_invalid_promotion_policy() -> None:
    with pytest.raises(ValueError, match="news_status_promotion_policy"):
        parse_jurisdiction_policy(
            {
                "permit_data_quality": "low",
                "news_status_promotion_policy": "manual_only",
            },
            jurisdiction_slug="new_city",
        )

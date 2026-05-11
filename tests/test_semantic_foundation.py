from __future__ import annotations

import pytest

from tcg_pipeline.semantic import (
    REASON_CODES_BY_CODE,
    REASON_CODES_BY_PROFILE_FIELD,
    InterpreterContext,
    PassageAnchor,
    ReasonCode,
    SemanticInterpretation,
    SourceObservations,
    build_reason_code_registry,
    reason_code_for,
    validate_reason_code_registry,
)


def test_reason_code_registry_validates() -> None:
    validate_reason_code_registry()


def test_reason_code_registry_contains_step7_critical_codes() -> None:
    expected = {
        "news_topped_out",
        "news_concrete_pour",
        "news_first_move_ins",
        "news_status_uncorroborated_high_quality_permit_jurisdiction",
        "news_groundbreaking_unverified_low_quality_permit_jurisdiction",
        "news_status_forward_looking_signal_flag_only",
        "news_units_workforce_explicit",
        "news_tenure_unstated_no_default",
        "news_tenure_mixed_split_observed",
        "news_product_type_hotel",
        "news_product_type_care_based_senior",
        "news_delivery_date_projected_season",
        "news_status_cancellation_review_required",
        "news_status_unmappable",
        "news_age_restriction_unmappable",
        "news_delivery_date_unmappable",
        "news_tenure_unmappable",
        "ladbs_product_type_apartment",
        "ladbs_product_type_condo",
        "ladbs_product_type_townhome",
        "ladbs_product_type_single_family",
        "ladbs_product_type_micro_co_living",
    }

    assert expected <= set(REASON_CODES_BY_CODE)


def test_reason_code_registry_contains_future_scope_placeholders() -> None:
    expected = {
        "news_stories_explicit",
        "news_retail_sf_explicit",
        "news_office_sf_explicit",
        "news_hotel_keys_explicit",
        "news_total_sf_explicit",
        "news_affordable_type_lihtc_observed",
        "news_affordable_type_ed1_observed",
        "news_affordable_type_toc_observed",
        "news_affordable_type_density_bonus_observed",
        "news_ceqa_status_draft_eir_released",
        "news_ceqa_status_final_eir_certified",
        "news_ceqa_status_exemption_observed",
        "news_appeal_status_filed",
        "news_appeal_status_denied",
        "news_appeal_status_challenge_observed",
    }

    assert expected <= set(REASON_CODES_BY_CODE)
    for code in expected:
        assert reason_code_for(code).signal_only is True


def test_reason_code_registry_count_is_stable() -> None:
    assert len(REASON_CODES_BY_CODE) == 80


def test_review_item_templates_stay_in_allowed_vocabulary() -> None:
    allowed = {
        "news_status_uncorroborated",
        "multi_tenure_review",
        "project_cancellation_review",
    }
    used = {
        reason.review_item_template
        for reason in REASON_CODES_BY_CODE.values()
        if reason.review_item_template is not None
    }

    assert used <= allowed
    assert used == allowed


def test_reason_code_metadata_captures_policy_decisions() -> None:
    strong = reason_code_for("news_concrete_pour")
    assert strong.promotes_status_alone is True
    assert strong.requires_corroboration is False
    assert strong.confidence_default == "high"

    ambiguous_high_quality = reason_code_for(
        "news_status_uncorroborated_high_quality_permit_jurisdiction"
    )
    assert ambiguous_high_quality.promotes_status_alone is False
    assert ambiguous_high_quality.requires_corroboration is True
    assert ambiguous_high_quality.review_item_template == "news_status_uncorroborated"

    tenure_unknown = reason_code_for("news_tenure_unstated_no_default")
    assert tenure_unknown.signal_only is True
    assert tenure_unknown.field_name == "rent_or_sale"


def test_reason_codes_group_by_source_profile_and_field() -> None:
    status_codes = REASON_CODES_BY_PROFILE_FIELD[("news_v1", "pipeline_status")]
    assert "news_topped_out" in status_codes
    assert "news_status_forward_looking_signal_flag_only" in status_codes

    workforce_codes = REASON_CODES_BY_PROFILE_FIELD[("news_v1", "workforce_units")]
    assert set(workforce_codes) == {"news_units_workforce_explicit"}

    permit_product_codes = REASON_CODES_BY_PROFILE_FIELD[("permit_v1", "product_type")]
    assert "ladbs_product_type_apartment" in permit_product_codes


def test_reason_code_registry_accepts_market_extensions() -> None:
    extension = ReasonCode(
        code="news_status_ulurp_certification",
        source_profile="news_v1",
        field_name="pipeline_status",
        label="ULURP certification",
        description="NYC market-specific status phrase.",
        confidence_default="medium",
    )

    registry = build_reason_code_registry([extension])

    assert registry.by_code["news_status_ulurp_certification"] is extension
    assert (
        "news_status_ulurp_certification"
        in registry.by_profile_field[("news_v1", "pipeline_status")]
    )
    validate_reason_code_registry(registry)


def test_reason_code_registry_rejects_colliding_market_extensions() -> None:
    with pytest.raises(ValueError, match="Duplicate semantic reason codes"):
        build_reason_code_registry(
            [
                ReasonCode(
                    code="news_topped_out",
                    source_profile="news_v1",
                    field_name="pipeline_status",
                    label="Duplicate",
                    description="Duplicate market extension.",
                    confidence_default="medium",
                )
            ]
        )


def test_semantic_interpretation_allows_signal_only_output() -> None:
    interpretation = SemanticInterpretation(
        field_name="pipeline_status",
        canonical_value=None,
        confidence="medium",
        reason_code="news_status_forward_looking_signal_flag_only",
        signal_flags={"groundbreaking_expected_at": "2026-07-15"},
        source_anchors=(
            PassageAnchor(
                text="The developer expects to break ground in Q3 2026.",
                offset_start=0,
                offset_end=51,
                field_name="pipeline_status",
            ),
        ),
    )

    assert interpretation.canonical_value is None
    assert interpretation.signal_flags["groundbreaking_expected_at"] == "2026-07-15"


def test_semantic_interpretation_rejects_empty_signal_only_output() -> None:
    with pytest.raises(ValueError, match="signal_flags"):
        SemanticInterpretation(
            field_name="pipeline_status",
            canonical_value=None,
            confidence="medium",
            reason_code="news_status_forward_looking_signal_flag_only",
        )


def test_source_observations_and_context_are_generic_containers() -> None:
    observations = SourceObservations(
        source_profile="news_v1",
        source_type="news_article",
        body_text="A 200-unit tower topped out.",
        reference_payload={"candidate_unit_total": 200},
    )
    context = InterpreterContext(
        jurisdiction_slug="city_of_los_angeles",
        jurisdiction_policy={"permit_data_quality": "high"},
        market_glossary={"slug": "los_angeles"},
        source_profile="news_v1",
    )

    assert observations.source_profile == "news_v1"
    assert observations.reference_payload["candidate_unit_total"] == 200
    assert context.jurisdiction_policy["permit_data_quality"] == "high"
    assert context.market_glossary["slug"] == "los_angeles"
    assert context.source_profile == "news_v1"

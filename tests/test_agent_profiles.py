from __future__ import annotations

import pytest

from tcg_pipeline.agents.profiles import (
    NEWS_AGENT_PROFILE,
    PERMIT_AGENT_PROFILE,
    AgentTrigger,
    get_source_profile,
    normalize_agent_triggers,
    validate_triggers_for_profile,
)


def test_news_agent_profile_contract() -> None:
    profile = get_source_profile("news_v1")

    assert profile is NEWS_AGENT_PROFILE
    assert profile.intake_source_type == "news_article"
    assert profile.cost_cap_bucket == "news"
    assert profile.kill_switch_setting == "agent_enabled_for_news"
    assert profile.capability_key == "agent.news_v1"
    assert profile.default_provider == "anthropic"
    assert profile.system_prompt_path.exists()
    assert "search_articles_similar" in profile.allowed_tools
    assert "get_permits_for_project" in profile.allowed_tools
    assert "pipeline_status" in profile.semantic_interpreters
    assert "date_delivery" in profile.semantic_interpreters
    assert profile.max_tool_calls == 15
    assert profile.required_intake_fields == frozenset({"extraction_id"})


def test_permit_agent_profile_contract() -> None:
    profile = get_source_profile("permit_v1")

    assert profile is PERMIT_AGENT_PROFILE
    assert profile.intake_source_type == "ladbs_permit"
    assert profile.triggers == frozenset(
        {
            "new_candidate",
            "unit_delta",
            "product_type_change",
            "status_regression_candidate",
        }
    )
    assert profile.cost_cap_bucket == "permits"
    assert profile.kill_switch_setting == "agent_enabled_for_permits"
    assert profile.capability_key == "agent.permit_v1"
    assert profile.system_prompt_path.exists()
    assert "get_permits_for_parcel" in profile.allowed_tools
    assert "get_permits_for_project" in profile.allowed_tools
    assert "get_article_body" not in profile.allowed_tools
    assert set(profile.semantic_interpreters) == {"product_type"}
    assert profile.semantic_interpreters["product_type"].deterministic_first is True
    assert (
        profile.semantic_interpreters["product_type"].llm_allowed_for_ambiguous_language
        is False
    )
    assert profile.required_intake_fields == frozenset()


def test_permit_agent_prompt_defines_trigger_contract() -> None:
    prompt = PERMIT_AGENT_PROFILE.system_prompt_path.read_text(encoding="utf-8")

    assert "For new_candidate triggers:" in prompt
    assert "For unit_delta triggers:" in prompt
    assert "greater than 10%" in prompt
    assert "For product_type_change triggers:" in prompt
    assert "For status_regression_candidate triggers:" in prompt
    assert "Do not promote Under Construction from permit issuance alone." in prompt


def test_news_agent_prompt_defines_pass1_conflict_combined_trigger_contract() -> None:
    prompt = NEWS_AGENT_PROFILE.system_prompt_path.read_text(encoding="utf-8")

    assert "For pass1_pass2_conflict triggers:" in prompt
    assert "use that trigger's verdict shapes" in prompt
    assert "the structural conflict is reasoning" in prompt


def test_news_agent_prompt_defines_material_contradiction_contract() -> None:
    prompt = NEWS_AGENT_PROFILE.system_prompt_path.read_text(encoding="utf-8")

    assert "For material_contradiction triggers:" in prompt
    assert "material_contradiction verdict shape" in prompt
    assert "unit delta or developer" in prompt
    assert "status regression, or developer mismatch" not in prompt
    assert "the other triggers are reasoning input" in prompt
    assert "downgrade_to_possible is the human-review path" in prompt
    assert "downgrade_to_possible" in prompt
    assert "get_project_state" in prompt


def test_news_agent_prompt_defines_status_regression_contract() -> None:
    prompt = NEWS_AGENT_PROFILE.system_prompt_path.read_text(encoding="utf-8")

    assert "For status_regression_candidate triggers:" in prompt
    assert "post-attribution lifecycle direction" in prompt
    assert "get_permits_for_project" in prompt
    assert "confirm_regression" in prompt
    assert "defer_to_review" in prompt
    assert "dismiss" in prompt
    assert "until_newer_evidence" in prompt


def test_news_agent_prompt_defines_override_contradiction_contract() -> None:
    prompt = NEWS_AGENT_PROFILE.system_prompt_path.read_text(encoding="utf-8")

    assert "For override_contradiction triggers:" in prompt
    assert "recommend_accept_new" in prompt
    assert "recommend_keep_override" in prompt
    assert "proposed alternatives" in prompt
    assert "full override_contradictions payload" in prompt
    assert '"field": "<field_name>"' not in prompt


def test_normalize_agent_triggers_accepts_enums_and_strings() -> None:
    assert normalize_agent_triggers(
        [AgentTrigger.STATUS_REGRESSION_CANDIDATE, "material_contradiction"]
    ) == (
        "status_regression_candidate",
        "material_contradiction",
    )


def test_normalize_agent_triggers_rejects_empty_list() -> None:
    with pytest.raises(ValueError, match="at least one"):
        normalize_agent_triggers([])


def test_validate_triggers_for_profile_rejects_unknown_trigger() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        validate_triggers_for_profile(
            profile=NEWS_AGENT_PROFILE,
            trigger_reasons=("not_a_trigger",),
        )

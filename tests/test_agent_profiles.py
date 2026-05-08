from __future__ import annotations

import pytest

from tcg_pipeline.agents.profiles import (
    NEWS_AGENT_PROFILE,
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
    assert "pipeline_status" in profile.semantic_interpreters
    assert "date_delivery" in profile.semantic_interpreters
    assert profile.max_tool_calls == 15
    assert profile.required_intake_fields == frozenset({"extraction_id"})


def test_news_agent_prompt_defines_pass1_conflict_combined_trigger_contract() -> None:
    prompt = NEWS_AGENT_PROFILE.system_prompt_path.read_text(encoding="utf-8")

    assert "For pass1_pass2_conflict triggers:" in prompt
    assert "use that trigger's verdict shapes" in prompt
    assert "the structural conflict is reasoning" in prompt


def test_news_agent_prompt_defines_material_contradiction_contract() -> None:
    prompt = NEWS_AGENT_PROFILE.system_prompt_path.read_text(encoding="utf-8")

    assert "For material_contradiction triggers:" in prompt
    assert "material_contradiction verdict shape" in prompt
    assert "the other triggers are reasoning input" in prompt
    assert "downgrade_to_possible is the human-review path" in prompt
    assert "downgrade_to_possible" in prompt
    assert "get_project_state" in prompt


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
        [AgentTrigger.NEW_CANDIDATE, "material_contradiction"]
    ) == (
        "new_candidate",
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

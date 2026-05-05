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

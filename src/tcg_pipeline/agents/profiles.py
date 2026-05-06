from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

from tcg_pipeline.news.llm import DEFAULT_EXTRACTION_MODEL, LLM_PROVIDER_ANTHROPIC

AGENT_PROMPT_ROOT = Path(__file__).parent / "prompts"
NEWS_AGENT_PROMPT_VERSION = "agent_news_v1"
NEWS_AGENT_PROFILE_VERSION = "1.0.0"
DEFAULT_AGENT_MAX_TOOL_CALLS = 15
DEFAULT_AGENT_MAX_COST_USD = Decimal("5.00")
DEFAULT_AGENT_MAX_WALLCLOCK_SECONDS = 300
DEFAULT_AGENT_MAX_OUTPUT_TOKENS = 4000


class AgentTrigger(enum.StrEnum):
    PASS1_PASS2_CONFLICT = "pass1_pass2_conflict"
    LOW_CONFIDENCE = "low_confidence"
    NEW_CANDIDATE = "new_candidate"
    POSSIBLE_MULTI_CANDIDATE = "possible_multi_candidate"
    MULTIPLE_DISTINCT_MENTIONS = "multiple_distinct_mentions"
    MATERIAL_CONTRADICTION = "material_contradiction"


@dataclass(frozen=True, slots=True)
class SemanticInterpreterProfile:
    field_name: str
    deterministic_first: bool = True
    llm_allowed_for_ambiguous_language: bool = True


@dataclass(frozen=True, slots=True)
class SourceProfile:
    name: str
    intake_source_type: str
    triggers: frozenset[str]
    allowed_tools: frozenset[str]
    system_prompt_path: Path
    cost_cap_bucket: str
    kill_switch_setting: str
    semantic_interpreters: Mapping[str, SemanticInterpreterProfile]
    capability_key: str
    profile_version: str
    prompt_version: str
    default_provider: str = LLM_PROVIDER_ANTHROPIC
    default_model: str = DEFAULT_EXTRACTION_MODEL
    max_tool_calls: int = DEFAULT_AGENT_MAX_TOOL_CALLS
    max_cost_usd: Decimal = DEFAULT_AGENT_MAX_COST_USD
    max_wallclock_seconds: int = DEFAULT_AGENT_MAX_WALLCLOCK_SECONDS
    max_output_tokens: int = DEFAULT_AGENT_MAX_OUTPUT_TOKENS
    required_intake_fields: frozenset[str] = frozenset()


CORE_AGENT_TOOLS = frozenset(
    {
        "get_project_state",
        "get_project_evidence",
        "search_projects",
        "search_articles_by_address",
    }
)
NEWS_AGENT_TOOLS = frozenset(
    {
        "get_article_body",
        "search_articles_by_project",
        "search_articles_similar",
    }
)
NEWS_SEMANTIC_INTERPRETERS = MappingProxyType(
    {
        "pipeline_status": SemanticInterpreterProfile("pipeline_status"),
        "product_type": SemanticInterpreterProfile("product_type"),
        "age_restriction": SemanticInterpreterProfile("age_restriction"),
        "date_delivery": SemanticInterpreterProfile("date_delivery"),
        "unit_buckets": SemanticInterpreterProfile("unit_buckets"),
    }
)

NEWS_AGENT_PROFILE = SourceProfile(
    name="news_v1",
    intake_source_type="news_article",
    triggers=frozenset(trigger.value for trigger in AgentTrigger),
    allowed_tools=CORE_AGENT_TOOLS | NEWS_AGENT_TOOLS,
    system_prompt_path=AGENT_PROMPT_ROOT / "news_v1" / "system.md",
    cost_cap_bucket="news",
    kill_switch_setting="agent_enabled_for_news",
    semantic_interpreters=NEWS_SEMANTIC_INTERPRETERS,
    capability_key="agent.news_v1",
    profile_version=NEWS_AGENT_PROFILE_VERSION,
    prompt_version=NEWS_AGENT_PROMPT_VERSION,
    required_intake_fields=frozenset({"extraction_id"}),
)
SOURCE_PROFILES: Mapping[str, SourceProfile] = MappingProxyType(
    {
        NEWS_AGENT_PROFILE.name: NEWS_AGENT_PROFILE,
    }
)


def get_source_profile(name: str) -> SourceProfile:
    try:
        return SOURCE_PROFILES[name]
    except KeyError as exc:
        raise KeyError(f"Unknown source profile: {name}") from exc


def normalize_agent_triggers(trigger_reasons: list[AgentTrigger | str]) -> tuple[str, ...]:
    normalized = tuple(
        trigger.value if isinstance(trigger, AgentTrigger) else str(trigger)
        for trigger in trigger_reasons
    )
    if not normalized:
        raise ValueError("Agent trigger_reasons must contain at least one trigger.")
    return normalized


def validate_triggers_for_profile(
    *,
    profile: SourceProfile,
    trigger_reasons: tuple[str, ...],
) -> None:
    unsupported = sorted(set(trigger_reasons) - set(profile.triggers))
    if unsupported:
        raise ValueError(
            f"Profile {profile.name} has unsupported trigger(s): {', '.join(unsupported)}"
        )

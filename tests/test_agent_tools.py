from __future__ import annotations

from dataclasses import replace

import pytest

from tcg_pipeline.agents.profiles import NEWS_AGENT_PROFILE
from tcg_pipeline.agents.tools import (
    AgentTool,
    AgentToolError,
    AgentToolRegistry,
    AgentToolResult,
)


def _tool(name: str = "search_articles_similar", *, budget: int = 1000) -> AgentTool:
    return AgentTool(
        name=name,
        description="Test tool",
        input_schema={"type": "object", "properties": {}},
        output_token_budget=budget,
        handler=lambda _args, _request: AgentToolResult(
            payload={"items": [{"title": "A" * 200}]},
            summary="summary",
            total_results=1,
        ),
    )


def test_tool_registry_exposes_only_profile_allowed_tools() -> None:
    registry = AgentToolRegistry(
        {
            "search_articles_similar": _tool("search_articles_similar"),
            "not_allowed": _tool("not_allowed"),
        }
    )

    specs = registry.tool_specs_for_profile(NEWS_AGENT_PROFILE)

    assert [spec["name"] for spec in specs] == ["search_articles_similar"]


def test_tool_dispatch_enforces_allowed_tool_set() -> None:
    registry = AgentToolRegistry({"not_allowed": _tool("not_allowed")})

    with pytest.raises(AgentToolError, match="not allowed"):
        registry.dispatch(
            tool_name="not_allowed",
            tool_input={},
            profile=NEWS_AGENT_PROFILE,
            request=None,
        )


def test_tool_dispatch_truncates_over_budget_payload() -> None:
    profile = replace(NEWS_AGENT_PROFILE, allowed_tools=frozenset({"search_articles_similar"}))
    registry = AgentToolRegistry({"search_articles_similar": _tool(budget=4)})

    result = registry.dispatch(
        tool_name="search_articles_similar",
        tool_input={"query_text": "Example"},
        profile=profile,
        request=None,
    )

    assert result.content["truncated"] is True
    assert result.content["total_results"] == 1
    assert result.content["hint"]
    assert result.summary["truncated"] is True

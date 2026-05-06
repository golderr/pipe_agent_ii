"""Source-agnostic agent runner primitives."""

from tcg_pipeline.agents.client import (
    AnthropicAgentClient,
    AnthropicAgentClientConfig,
    build_anthropic_agent_client,
)
from tcg_pipeline.agents.news_tools import (
    GET_ARTICLE_BODY_TOOL,
    SEARCH_ARTICLES_SIMILAR_TOOL,
    handle_get_article_body,
    handle_search_articles_similar,
)
from tcg_pipeline.agents.profiles import (
    NEWS_AGENT_PROFILE,
    AgentTrigger,
    SemanticInterpreterProfile,
    SourceProfile,
    get_source_profile,
)
from tcg_pipeline.agents.project_tools import (
    GET_PROJECT_STATE_TOOL,
    handle_get_project_state,
)
from tcg_pipeline.agents.registry import build_agent_tool_registry
from tcg_pipeline.agents.runner import (
    AgentClient,
    AgentClientResult,
    AgentRunResult,
    IntakeRecord,
    run_agent_for_intake,
)
from tcg_pipeline.agents.tools import (
    AgentTool,
    AgentToolError,
    AgentToolRegistry,
    AgentToolResult,
    ToolDispatchResult,
)

__all__ = [
    "AnthropicAgentClient",
    "AnthropicAgentClientConfig",
    "GET_ARTICLE_BODY_TOOL",
    "GET_PROJECT_STATE_TOOL",
    "NEWS_AGENT_PROFILE",
    "SEARCH_ARTICLES_SIMILAR_TOOL",
    "AgentClient",
    "AgentClientResult",
    "AgentRunResult",
    "AgentTool",
    "AgentToolError",
    "AgentToolRegistry",
    "AgentToolResult",
    "AgentTrigger",
    "IntakeRecord",
    "SemanticInterpreterProfile",
    "SourceProfile",
    "ToolDispatchResult",
    "build_agent_tool_registry",
    "build_anthropic_agent_client",
    "get_source_profile",
    "handle_get_article_body",
    "handle_get_project_state",
    "handle_search_articles_similar",
    "run_agent_for_intake",
]

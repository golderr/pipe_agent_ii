"""Source-agnostic agent runner primitives."""

from tcg_pipeline.agents.client import (
    AnthropicAgentClient,
    AnthropicAgentClientConfig,
    build_anthropic_agent_client,
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
    build_agent_tool_registry,
    handle_get_project_state,
)
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
    "GET_PROJECT_STATE_TOOL",
    "NEWS_AGENT_PROFILE",
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
    "handle_get_project_state",
    "run_agent_for_intake",
]

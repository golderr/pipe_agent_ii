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
    "build_anthropic_agent_client",
    "get_source_profile",
    "run_agent_for_intake",
]

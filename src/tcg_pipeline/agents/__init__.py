"""Source-agnostic agent runner primitives."""

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

__all__ = [
    "NEWS_AGENT_PROFILE",
    "AgentClient",
    "AgentClientResult",
    "AgentRunResult",
    "AgentTrigger",
    "IntakeRecord",
    "SemanticInterpreterProfile",
    "SourceProfile",
    "get_source_profile",
    "run_agent_for_intake",
]

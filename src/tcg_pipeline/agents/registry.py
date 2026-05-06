from __future__ import annotations

from tcg_pipeline.agents.news_tools import GET_ARTICLE_BODY_TOOL, SEARCH_ARTICLES_SIMILAR_TOOL
from tcg_pipeline.agents.project_tools import GET_PROJECT_STATE_TOOL
from tcg_pipeline.agents.tools import AgentToolRegistry


def build_agent_tool_registry() -> AgentToolRegistry:
    return AgentToolRegistry(
        {
            GET_PROJECT_STATE_TOOL.name: GET_PROJECT_STATE_TOOL,
            GET_ARTICLE_BODY_TOOL.name: GET_ARTICLE_BODY_TOOL,
            SEARCH_ARTICLES_SIMILAR_TOOL.name: SEARCH_ARTICLES_SIMILAR_TOOL,
        }
    )

from __future__ import annotations

from tcg_pipeline.agents.news_tools import GET_ARTICLE_BODY_TOOL, SEARCH_ARTICLES_SIMILAR_TOOL
from tcg_pipeline.agents.permit_tools import (
    GET_ARTICLES_ABOUT_PARCEL_OR_ADDRESS_TOOL,
    GET_PERMITS_FOR_PARCEL_TOOL,
    GET_PERMITS_FOR_PROJECT_TOOL,
)
from tcg_pipeline.agents.project_tools import GET_PROJECT_STATE_TOOL, SEARCH_PROJECTS_TOOL
from tcg_pipeline.agents.tools import AgentToolRegistry


def build_agent_tool_registry() -> AgentToolRegistry:
    return AgentToolRegistry(
        {
            GET_PROJECT_STATE_TOOL.name: GET_PROJECT_STATE_TOOL,
            SEARCH_PROJECTS_TOOL.name: SEARCH_PROJECTS_TOOL,
            GET_ARTICLE_BODY_TOOL.name: GET_ARTICLE_BODY_TOOL,
            SEARCH_ARTICLES_SIMILAR_TOOL.name: SEARCH_ARTICLES_SIMILAR_TOOL,
            GET_ARTICLES_ABOUT_PARCEL_OR_ADDRESS_TOOL.name: (
                GET_ARTICLES_ABOUT_PARCEL_OR_ADDRESS_TOOL
            ),
            GET_PERMITS_FOR_PARCEL_TOOL.name: GET_PERMITS_FOR_PARCEL_TOOL,
            GET_PERMITS_FOR_PROJECT_TOOL.name: GET_PERMITS_FOR_PROJECT_TOOL,
        }
    )

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from types import SimpleNamespace

from tcg_pipeline.agents.client import AnthropicAgentClient
from tcg_pipeline.agents.profiles import NEWS_AGENT_PROFILE
from tcg_pipeline.agents.runner import AgentRunRequest, IntakeRecord
from tcg_pipeline.agents.tools import AgentTool, AgentToolRegistry, AgentToolResult
from tcg_pipeline.db.models import AgentRunOutcome


class FakeMessages:
    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class FakeAnthropic:
    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self.messages = FakeMessages(responses)


def _usage(input_tokens: int, output_tokens: int) -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=input_tokens,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        output_tokens=output_tokens,
    )


def _response(*, stop_reason: str, content: list[SimpleNamespace], input_tokens=10):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=content,
        usage=_usage(input_tokens, 5),
    )


def _request() -> AgentRunRequest:
    return AgentRunRequest(
        intake=IntakeRecord(
            source_type="news_article",
            intake_record_id=str(uuid.uuid4()),
            extraction_id=uuid.uuid4(),
            payload={"title": "Example"},
        ),
        matcher_results=({"status": "possible"},),
        trigger_reasons=("new_candidate",),
        profile=NEWS_AGENT_PROFILE,
    )


def _tool_registry() -> AgentToolRegistry:
    return AgentToolRegistry(
        {
            "search_articles_similar": AgentTool(
                name="search_articles_similar",
                description="Search accepted article chunks",
                input_schema={
                    "type": "object",
                    "properties": {"query_text": {"type": "string"}},
                },
                output_token_budget=1000,
                handler=lambda args, _request: AgentToolResult(
                    payload={"matches": [{"title": "Similar", "query": args["query_text"]}]},
                    summary="one similar article",
                    total_results=1,
                ),
            )
        }
    )


def test_anthropic_agent_client_runs_tool_loop_and_parses_final_json() -> None:
    final_payload = {
        "outcome": AgentRunOutcome.COMPLETED.value,
        "reasoning_trace": "The deterministic match stands after checking similar articles.",
        "evidence_consulted": [{"source_type": "news_article", "record_id": "a", "role": "tool"}],
        "agent_revised_verdict": {"decision": "no_change"},
    }
    fake = FakeAnthropic(
        [
            _response(
                stop_reason="tool_use",
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        id="toolu_1",
                        name="search_articles_similar",
                        input={"query_text": "Example"},
                    )
                ],
                input_tokens=10,
            ),
            _response(
                stop_reason="end_turn",
                content=[SimpleNamespace(type="text", text=json.dumps(final_payload))],
                input_tokens=20,
            ),
        ]
    )
    profile = replace(NEWS_AGENT_PROFILE, max_output_tokens=1234)
    client = AnthropicAgentClient(
        api_key="test",
        profile=profile,
        anthropic_client=fake,
        tool_registry=_tool_registry(),
    )

    result = client.run(_request())

    assert result.outcome == AgentRunOutcome.COMPLETED.value
    assert result.reasoning_trace == final_payload["reasoning_trace"]
    assert result.agent_revised_verdict == {"decision": "no_change"}
    assert result.usage.input_tokens_uncached == 30
    assert result.usage.output_tokens == 10
    assert result.tool_calls_summary[0]["tool"] == "search_articles_similar"
    assert fake.messages.calls[0]["max_tokens"] == 1234
    assert fake.messages.calls[0]["tools"][0]["name"] == "search_articles_similar"
    tool_result_message = fake.messages.calls[1]["messages"][-1]
    assert tool_result_message["role"] == "user"
    assert tool_result_message["content"][0]["type"] == "tool_result"


def test_anthropic_agent_client_returns_failed_error_for_unknown_tool() -> None:
    fake = FakeAnthropic(
        [
            _response(
                stop_reason="tool_use",
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        id="toolu_1",
                        name="search_articles_similar",
                        input={"query_text": "Example"},
                    )
                ],
            )
        ]
    )
    client = AnthropicAgentClient(api_key="test", anthropic_client=fake)

    result = client.run(_request())

    assert result.outcome == AgentRunOutcome.FAILED_ERROR.value
    assert result.error_text == "Tool search_articles_similar is not registered."


def test_anthropic_agent_client_returns_failed_error_for_invalid_final_json() -> None:
    fake = FakeAnthropic(
        [
            _response(
                stop_reason="end_turn",
                content=[SimpleNamespace(type="text", text="not json")],
            )
        ]
    )
    client = AnthropicAgentClient(api_key="test", anthropic_client=fake)

    result = client.run(_request())

    assert result.outcome == AgentRunOutcome.FAILED_ERROR.value
    assert result.error_text == "Agent final response was not valid JSON."


def test_anthropic_agent_client_rejects_unrecognized_final_outcome() -> None:
    fake = FakeAnthropic(
        [
            _response(
                stop_reason="end_turn",
                content=[
                    SimpleNamespace(
                        type="text",
                        text=json.dumps({"outcome": "escalated_to_review"}),
                    )
                ],
            )
        ]
    )
    client = AnthropicAgentClient(api_key="test", anthropic_client=fake)

    result = client.run(_request())

    assert result.outcome == AgentRunOutcome.FAILED_ERROR.value
    assert result.error_text == "Agent emitted unrecognized outcome 'escalated_to_review'."


def test_anthropic_agent_client_stops_after_max_iterations() -> None:
    fake = FakeAnthropic(
        [
            _response(
                stop_reason="tool_use",
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        id="toolu_1",
                        name="search_articles_similar",
                        input={"query_text": "Example"},
                    )
                ],
            ),
            _response(
                stop_reason="tool_use",
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        id="toolu_2",
                        name="search_articles_similar",
                        input={"query_text": "Example again"},
                    )
                ],
            ),
        ]
    )
    client = AnthropicAgentClient(
        api_key="test",
        anthropic_client=fake,
        tool_registry=_tool_registry(),
        max_iterations=2,
    )

    result = client.run(_request())

    assert result.outcome == AgentRunOutcome.FAILED_ERROR.value
    assert result.error_text == "Agent exceeded max iterations 2."
    assert len(fake.messages.calls) == 2

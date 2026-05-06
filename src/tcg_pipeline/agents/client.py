from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import anthropic

from tcg_pipeline.agents.profiles import NEWS_AGENT_PROFILE, SourceProfile
from tcg_pipeline.agents.registry import build_agent_tool_registry
from tcg_pipeline.agents.runner import AgentClientResult, AgentRunRequest
from tcg_pipeline.agents.tools import AgentToolError, AgentToolRegistry
from tcg_pipeline.db.models import AgentRunOutcome
from tcg_pipeline.news.llm import (
    DEFAULT_EXTRACTION_MODEL,
    LLM_PROVIDER_ANTHROPIC,
    LLMUsage,
    anthropic_usage,
    create_anthropic_message,
)
from tcg_pipeline.settings import Settings, get_settings

AGENT_TEMPERATURE = 0
CLIENT_FINAL_OUTCOMES = frozenset(
    {
        AgentRunOutcome.COMPLETED.value,
        AgentRunOutcome.ESCALATED.value,
    }
)


@dataclass(frozen=True, slots=True)
class AnthropicAgentClientConfig:
    profile: SourceProfile = NEWS_AGENT_PROFILE
    model: str = DEFAULT_EXTRACTION_MODEL
    max_iterations: int | None = None


class AnthropicAgentClient:
    provider = LLM_PROVIDER_ANTHROPIC

    def __init__(
        self,
        *,
        api_key: str,
        profile: SourceProfile = NEWS_AGENT_PROFILE,
        model: str = DEFAULT_EXTRACTION_MODEL,
        tool_registry: AgentToolRegistry | None = None,
        anthropic_client: Any | None = None,
        max_iterations: int | None = None,
    ) -> None:
        self.profile = profile
        self.model = model
        self.prompt_version = profile.prompt_version
        self._tool_registry = tool_registry or AgentToolRegistry()
        self._client = anthropic_client or anthropic.Anthropic(api_key=api_key)
        self._max_iterations = max_iterations or profile.max_tool_calls + 2

    def run(self, request: AgentRunRequest) -> AgentClientResult:
        started_at = time.perf_counter()
        system_text = _load_system_prompt(self.profile)
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": _render_user_prompt(request),
            }
        ]
        usage = LLMUsage(0, 0, 0, 0)
        tool_calls_summary: list[dict[str, Any]] = []
        tool_specs = self._tool_registry.tool_specs_for_profile(self.profile)
        for _ in range(self._max_iterations):
            response = create_anthropic_message(
                self._client,
                model=self.model,
                max_tokens=self.profile.max_output_tokens,
                temperature=AGENT_TEMPERATURE,
                system=[
                    {
                        "type": "text",
                        "text": system_text,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=messages,
                tools=tool_specs,
            )
            usage = _add_usage(usage, anthropic_usage(response.usage))
            stop_reason = getattr(response, "stop_reason", None)
            if stop_reason == "tool_use":
                assistant_blocks = _assistant_content_blocks(response.content)
                tool_results = []
                try:
                    for block in _tool_use_blocks(response.content):
                        dispatch_result = self._tool_registry.dispatch(
                            tool_name=block["name"],
                            tool_input=block["input"],
                            profile=self.profile,
                            request=request,
                        )
                        tool_calls_summary.append(dispatch_result.summary)
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block["id"],
                                "content": json.dumps(dispatch_result.content, default=str),
                            }
                        )
                except AgentToolError as exc:
                    return _failed_result(
                        usage=usage,
                        latency_ms=_elapsed_ms(started_at),
                        error_text=str(exc),
                        tool_calls_summary=tool_calls_summary,
                    )
                messages.append({"role": "assistant", "content": assistant_blocks})
                messages.append({"role": "user", "content": tool_results})
                continue
            if stop_reason == "end_turn":
                text = _text_from_content(response.content)
                parsed = _parse_final_json(text)
                if parsed is None:
                    return _failed_result(
                        usage=usage,
                        latency_ms=_elapsed_ms(started_at),
                        error_text="Agent final response was not valid JSON.",
                        tool_calls_summary=tool_calls_summary,
                    )
                outcome = _client_final_outcome(parsed.get("outcome"))
                if outcome is None:
                    return _failed_result(
                        usage=usage,
                        latency_ms=_elapsed_ms(started_at),
                        error_text=(
                            "Agent emitted unrecognized outcome "
                            f"'{parsed.get('outcome')}'."
                        ),
                        tool_calls_summary=tool_calls_summary,
                    )
                return AgentClientResult(
                    outcome=outcome,
                    usage=usage,
                    latency_ms=_elapsed_ms(started_at),
                    reasoning_trace=_string_or_none(parsed.get("reasoning_trace")),
                    evidence_consulted=_list_of_dicts(parsed.get("evidence_consulted")),
                    tool_calls_summary=tool_calls_summary,
                    agent_revised_verdict=_dict_or_none(parsed.get("agent_revised_verdict")),
                    error_text=_string_or_none(parsed.get("error_text")),
                )
            return _failed_result(
                usage=usage,
                latency_ms=_elapsed_ms(started_at),
                error_text=f"Agent stopped before final decision: {stop_reason or 'unknown'}",
                tool_calls_summary=tool_calls_summary,
            )
        return _failed_result(
            usage=usage,
            latency_ms=_elapsed_ms(started_at),
            error_text=f"Agent exceeded max iterations {self._max_iterations}.",
            tool_calls_summary=tool_calls_summary,
        )


def build_anthropic_agent_client(
    *,
    settings: Settings | None = None,
    profile: SourceProfile = NEWS_AGENT_PROFILE,
    tool_registry: AgentToolRegistry | None = None,
) -> AnthropicAgentClient:
    resolved_settings = settings or get_settings()
    if not resolved_settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required for agent runs.")
    return AnthropicAgentClient(
        api_key=resolved_settings.anthropic_api_key,
        profile=profile,
        model=profile.default_model,
        tool_registry=tool_registry or build_agent_tool_registry(),
    )


def _failed_result(
    *,
    usage: LLMUsage,
    latency_ms: int,
    error_text: str,
    tool_calls_summary: list[dict[str, Any]],
) -> AgentClientResult:
    return AgentClientResult(
        outcome=AgentRunOutcome.FAILED_ERROR.value,
        usage=usage,
        latency_ms=latency_ms,
        tool_calls_summary=tool_calls_summary,
        error_text=error_text,
    )


def _load_system_prompt(profile: SourceProfile) -> str:
    return profile.system_prompt_path.read_text(encoding="utf-8")


def _render_user_prompt(request: AgentRunRequest) -> str:
    payload = {
        "intake": {
            "source_type": request.intake.source_type,
            "intake_record_id": request.intake.intake_record_id,
            "extraction_id": str(request.intake.extraction_id)
            if request.intake.extraction_id is not None
            else None,
            "project_id": str(request.intake.project_id) if request.intake.project_id else None,
            "source_run_id": str(request.intake.source_run_id)
            if request.intake.source_run_id
            else None,
            "scrape_job_id": str(request.intake.scrape_job_id)
            if request.intake.scrape_job_id
            else None,
            "payload": request.intake.payload,
        },
        "trigger_reasons": list(request.trigger_reasons),
        "matcher_results": list(request.matcher_results),
        "final_response_contract": {
            "outcome": "completed | escalated",
            "reasoning_trace": "100-500 character source-anchored explanation",
            "evidence_consulted": "list of {source_type, record_id, role}",
            "agent_revised_verdict": "object describing no_change, revised_match, or escalated",
        },
    }
    return json.dumps(payload, indent=2, sort_keys=True, default=str)


def _assistant_content_blocks(content: Any) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for block in content or []:
        block_type = _block_value(block, "type")
        if block_type == "text":
            blocks.append({"type": "text", "text": str(_block_value(block, "text") or "")})
        elif block_type == "tool_use":
            blocks.append(
                {
                    "type": "tool_use",
                    "id": str(_block_value(block, "id") or ""),
                    "name": str(_block_value(block, "name") or ""),
                    "input": _block_value(block, "input") or {},
                }
            )
    return blocks


def _tool_use_blocks(content: Any) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for block in content or []:
        if _block_value(block, "type") != "tool_use":
            continue
        tool_input = _block_value(block, "input") or {}
        if not isinstance(tool_input, dict):
            tool_input = {"value": tool_input}
        blocks.append(
            {
                "id": str(_block_value(block, "id") or ""),
                "name": str(_block_value(block, "name") or ""),
                "input": tool_input,
            }
        )
    return blocks


def _text_from_content(content: Any) -> str:
    parts: list[str] = []
    for block in content or []:
        if _block_value(block, "type") == "text":
            parts.append(str(_block_value(block, "text") or ""))
    return "\n".join(part for part in parts if part)


def _parse_final_json(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None
    parsed = _loads_json_object(stripped)
    if parsed is not None:
        return parsed
    decoder = json.JSONDecoder()
    for index, character in enumerate(stripped):
        if character != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            return candidate
    return None


def _loads_json_object(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _client_final_outcome(value: Any) -> str | None:
    if value is None:
        return AgentRunOutcome.COMPLETED.value
    outcome = str(value).strip().lower()
    if outcome in CLIENT_FINAL_OUTCOMES:
        return outcome
    return None


def _block_value(block: Any, key: str) -> Any:
    if isinstance(block, dict):
        return block.get(key)
    return getattr(block, key, None)


def _add_usage(left: LLMUsage, right: LLMUsage) -> LLMUsage:
    return LLMUsage(
        input_tokens_uncached=left.input_tokens_uncached + right.input_tokens_uncached,
        input_tokens_cache_creation=(
            left.input_tokens_cache_creation + right.input_tokens_cache_creation
        ),
        input_tokens_cached=left.input_tokens_cached + right.input_tokens_cached,
        output_tokens=left.output_tokens + right.output_tokens,
    )


def _elapsed_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _dict_or_none(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]

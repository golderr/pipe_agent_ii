from __future__ import annotations

import dataclasses
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from tcg_pipeline.agents.profiles import SourceProfile

DEFAULT_TOOL_OUTPUT_TOKEN_BUDGET = 1000
TRUNCATION_HINT = "results omitted; refine query to see more."


class AgentToolError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AgentToolResult:
    payload: dict[str, Any]
    summary: str | None = None
    total_results: int | None = None


@dataclass(frozen=True, slots=True)
class AgentTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_token_budget: int
    handler: Callable[[dict[str, Any], Any], AgentToolResult]

    @property
    def anthropic_spec(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


@dataclass(frozen=True, slots=True)
class ToolDispatchResult:
    tool_name: str
    content: dict[str, Any]
    summary: dict[str, Any]


class AgentToolRegistry:
    def __init__(self, tools: Mapping[str, AgentTool] | None = None) -> None:
        self._tools = dict(tools or {})

    def register(self, tool: AgentTool) -> None:
        self._tools[tool.name] = tool

    def tool_specs_for_profile(self, profile: SourceProfile) -> list[dict[str, Any]]:
        return [
            tool.anthropic_spec
            for name, tool in sorted(self._tools.items())
            if name in profile.allowed_tools
        ]

    def dispatch(
        self,
        *,
        tool_name: str,
        tool_input: dict[str, Any],
        profile: SourceProfile,
        request: Any,
    ) -> ToolDispatchResult:
        if tool_name not in profile.allowed_tools:
            raise AgentToolError(
                f"Tool {tool_name} is not allowed for source profile {profile.name}."
            )
        tool = self._tools.get(tool_name)
        if tool is None:
            raise AgentToolError(f"Tool {tool_name} is not registered.")
        started_at = time.perf_counter()
        result = tool.handler(tool_input, request)
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        content = _budgeted_tool_payload(result, output_token_budget=tool.output_token_budget)
        output_token_count = estimate_tokens(json.dumps(content, sort_keys=True, default=str))
        summary = {
            "tool": tool_name,
            "args_summary": _summarize_json(tool_input, max_chars=180),
            "result_summary": result.summary or _summarize_json(content, max_chars=240),
            "latency_ms": latency_ms,
            "output_token_count": output_token_count,
            "truncated": bool(content.get("truncated")),
        }
        if result.total_results is not None:
            summary["total_results"] = result.total_results
        return ToolDispatchResult(tool_name=tool_name, content=content, summary=summary)


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def _budgeted_tool_payload(
    result: AgentToolResult,
    *,
    output_token_budget: int,
) -> dict[str, Any]:
    payload = _json_safe(result.payload)
    serialized = json.dumps(payload, sort_keys=True, default=str)
    if estimate_tokens(serialized) <= output_token_budget:
        return payload
    max_chars = max(output_token_budget * 4, 80)
    return {
        "truncated": True,
        "total_results": result.total_results,
        "summary": _truncate_text(result.summary or serialized, max_chars=max_chars),
        "hint": TRUNCATION_HINT,
    }


def _summarize_json(value: Any, *, max_chars: int) -> str:
    return _truncate_text(
        json.dumps(_json_safe(value), sort_keys=True, default=str),
        max_chars=max_chars,
    )


def _truncate_text(value: str, *, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max(max_chars - 3, 0)] + "..."


def _json_safe(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _json_safe(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin

import httpx

DEFAULT_TRIAGE_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_EXTRACTION_MODEL = "claude-opus-4-7"
DEFAULT_SONNET_EXTRACTION_MODEL = "claude-sonnet-4-6"
DEFAULT_OPENAI_EXTRACTION_MODEL = "gpt-5.4"
LLM_PROVIDER_ANTHROPIC = "anthropic"
LLM_PROVIDER_OPENAI = "openai"
LLM_PROVIDER_VERCEL_AI_GATEWAY = "vercel_ai_gateway"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_VERCEL_AI_GATEWAY_BASE_URL = "https://ai-gateway.vercel.sh/v1"
OPENAI_RESPONSES_PATH = "responses"
OPENAI_COMPATIBLE_PROVIDERS = {
    LLM_PROVIDER_OPENAI,
    LLM_PROVIDER_VERCEL_AI_GATEWAY,
}

MODEL_PRICING_USD_PER_MILLION = {
    DEFAULT_TRIAGE_MODEL: {
        "input": Decimal("1.00"),
        "input_cache_creation": Decimal("1.25"),
        "input_cache_read": Decimal("0.10"),
        "output": Decimal("5.00"),
    },
    DEFAULT_EXTRACTION_MODEL: {
        "input": Decimal("5.00"),
        "input_cache_creation": Decimal("6.25"),
        "input_cache_read": Decimal("0.50"),
        "output": Decimal("25.00"),
    },
    DEFAULT_SONNET_EXTRACTION_MODEL: {
        "input": Decimal("3.00"),
        "input_cache_creation": Decimal("3.75"),
        "input_cache_read": Decimal("0.30"),
        "output": Decimal("15.00"),
    },
    DEFAULT_OPENAI_EXTRACTION_MODEL: {
        "input": Decimal("2.50"),
        "input_cache_creation": Decimal("2.50"),
        "input_cache_read": Decimal("2.50"),
        "output": Decimal("15.00"),
    },
}
MODEL_PRICING_ALIASES = {
    "anthropic/claude-opus-4-7": DEFAULT_EXTRACTION_MODEL,
    "anthropic/claude-sonnet-4-6": DEFAULT_SONNET_EXTRACTION_MODEL,
    "claude-opus-4.7": DEFAULT_EXTRACTION_MODEL,
    "claude-sonnet-4.6": DEFAULT_SONNET_EXTRACTION_MODEL,
    "gpt-5.4-2026-03-05": DEFAULT_OPENAI_EXTRACTION_MODEL,
}
MODEL_PRICING_ASSUMPTIONS = {
    DEFAULT_OPENAI_EXTRACTION_MODEL: (
        "GPT-5.4 cached-input tokens are priced at the full input rate for "
        "internal A/B accounting until an explicit cached-input rate is confirmed."
    ),
}


@dataclass(frozen=True, slots=True)
class LLMUsage:
    input_tokens_uncached: int
    input_tokens_cache_creation: int
    input_tokens_cached: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class LLMJSONResponse:
    text: str
    payload: dict[str, Any] | None
    model: str
    provider: str
    usage: LLMUsage
    latency_ms: int
    stop_reason: str | None = None


class OpenAIResponsesJSONClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        provider: str = LLM_PROVIDER_OPENAI,
        base_url: str = DEFAULT_OPENAI_BASE_URL,
        max_output_tokens: int,
        temperature: int | float | None = None,
        timeout_seconds: float = 60.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.model = model
        self.provider = normalize_llm_provider(provider)
        self._api_key = api_key
        self._base_url = _normalize_base_url(base_url)
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature
        self._timeout_seconds = timeout_seconds
        self._http_client = http_client

    def create_json_response(
        self,
        *,
        system_text: str,
        user_text: str,
        schema: dict[str, Any],
        schema_name: str,
    ) -> LLMJSONResponse:
        request_payload = _openai_json_response_payload(
            model=self.model,
            system_text=system_text,
            user_text=user_text,
            schema=schema,
            schema_name=schema_name,
            max_output_tokens=self._max_output_tokens,
            temperature=self._temperature,
        )
        started_at = time.perf_counter()
        response_json = _post_openai_response_json(
            base_url=self._base_url,
            api_key=self._api_key,
            payload=request_payload,
            timeout_seconds=self._timeout_seconds,
            http_client=self._http_client,
        )
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        text = openai_output_text(response_json)
        return LLMJSONResponse(
            text=text,
            payload=_json_object_or_none(text),
            model=str(response_json.get("model") or self.model),
            provider=self.provider,
            usage=openai_usage(response_json.get("usage") or {}),
            latency_ms=latency_ms,
            stop_reason=openai_stop_reason(response_json),
        )


def calculate_llm_cost_usd(
    model: str,
    *,
    input_tokens_uncached: int,
    input_tokens_cache_creation: int,
    input_tokens_cached: int,
    output_tokens: int,
) -> Decimal:
    pricing = pricing_for_model(model)
    cost = (
        Decimal(input_tokens_uncached) * pricing["input"]
        + Decimal(input_tokens_cache_creation) * pricing["input_cache_creation"]
        + Decimal(input_tokens_cached) * pricing["input_cache_read"]
        + Decimal(output_tokens) * pricing["output"]
    ) / Decimal(1_000_000)
    return cost.quantize(Decimal("0.000001"))


def pricing_for_model(model: str) -> dict[str, Decimal]:
    pricing = MODEL_PRICING_USD_PER_MILLION.get(_pricing_model_key(model))
    if pricing is None:
        raise RuntimeError(f"Unknown news LLM model pricing: {model}")
    return pricing


def pricing_assumption_for_model(model: str) -> str | None:
    return MODEL_PRICING_ASSUMPTIONS.get(_pricing_model_key(model))


def anthropic_usage(usage: Any) -> LLMUsage:
    cache_read = int(getattr(usage, "cache_read_input_tokens", None) or 0)
    cache_creation = int(getattr(usage, "cache_creation_input_tokens", None) or 0)
    input_tokens = int(getattr(usage, "input_tokens", None) or 0)
    return LLMUsage(
        input_tokens_uncached=input_tokens,
        input_tokens_cache_creation=cache_creation,
        input_tokens_cached=cache_read,
        output_tokens=int(getattr(usage, "output_tokens", None) or 0),
    )


def openai_usage(usage: dict[str, Any]) -> LLMUsage:
    input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    input_details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
    cached_tokens = int(input_details.get("cached_tokens") or 0)
    return LLMUsage(
        input_tokens_uncached=max(input_tokens - cached_tokens, 0),
        input_tokens_cache_creation=0,
        input_tokens_cached=cached_tokens,
        output_tokens=output_tokens,
    )


def openai_output_text(response_json: dict[str, Any]) -> str:
    output_text = response_json.get("output_text")
    if isinstance(output_text, str):
        return output_text
    text_parts: list[str] = []
    for item in response_json.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "function_call":
            arguments = item.get("arguments")
            if isinstance(arguments, str):
                text_parts.append(arguments)
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            if content.get("type") in {"output_text", "text"} and isinstance(
                content.get("text"),
                str,
            ):
                text_parts.append(content["text"])
            elif content.get("type") == "refusal" and isinstance(content.get("refusal"), str):
                text_parts.append(content["refusal"])
    return "\n".join(part for part in text_parts if part)


def openai_stop_reason(response_json: dict[str, Any]) -> str | None:
    if _openai_response_has_refusal(response_json):
        return "refusal"
    incomplete_details = response_json.get("incomplete_details") or {}
    if response_json.get("status") == "incomplete":
        reason = str(incomplete_details.get("reason") or "incomplete")
        if reason in {"max_output_tokens", "max_tokens"}:
            return "max_tokens"
        return reason
    return None


def normalize_llm_provider(provider: str | None) -> str:
    normalized = (provider or LLM_PROVIDER_ANTHROPIC).strip().lower().replace("-", "_")
    if normalized in {"vercel", "ai_gateway", "vercel_ai_gateway"}:
        return LLM_PROVIDER_VERCEL_AI_GATEWAY
    if normalized in {"openai", "openai_responses"}:
        return LLM_PROVIDER_OPENAI
    if normalized == LLM_PROVIDER_ANTHROPIC:
        return LLM_PROVIDER_ANTHROPIC
    raise RuntimeError(f"Unsupported LLM provider: {provider}")


def provider_api_key(settings: Any, provider: str) -> str | None:
    normalized = normalize_llm_provider(provider)
    if normalized == LLM_PROVIDER_ANTHROPIC:
        return getattr(settings, "anthropic_api_key", None)
    if normalized == LLM_PROVIDER_OPENAI:
        return getattr(settings, "openai_api_key", None)
    if normalized == LLM_PROVIDER_VERCEL_AI_GATEWAY:
        return getattr(settings, "ai_gateway_api_key", None)
    return None


def provider_base_url(settings: Any, provider: str) -> str:
    normalized = normalize_llm_provider(provider)
    if normalized == LLM_PROVIDER_OPENAI:
        return str(getattr(settings, "openai_base_url", DEFAULT_OPENAI_BASE_URL))
    if normalized == LLM_PROVIDER_VERCEL_AI_GATEWAY:
        return str(
            getattr(
                settings,
                "ai_gateway_base_url",
                DEFAULT_VERCEL_AI_GATEWAY_BASE_URL,
            )
        )
    return ""


def create_anthropic_message(
    client: Any,
    *,
    temperature: int | float | None,
    **kwargs: Any,
) -> Any:
    if temperature is None:
        return client.messages.create(**kwargs)
    try:
        return client.messages.create(temperature=temperature, **kwargs)
    except Exception as exc:  # noqa: BLE001 - Anthropic SDK error classes vary by version.
        if _is_temperature_deprecated_error(exc):
            return client.messages.create(**kwargs)
        raise


def _is_temperature_deprecated_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "temperature" in text and "deprecated" in text


def _post_openai_response_json(
    *,
    base_url: str,
    api_key: str,
    payload: dict[str, Any],
    timeout_seconds: float,
    http_client: httpx.Client | None,
) -> dict[str, Any]:
    client = http_client or httpx.Client(timeout=timeout_seconds)
    close_client = http_client is None
    url = urljoin(base_url + "/", OPENAI_RESPONSES_PATH)
    try:
        response = client.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        if (
            response.status_code >= 400
            and "temperature" in payload
            and _openai_temperature_unsupported(response.text)
        ):
            retry_payload = {key: value for key, value in payload.items() if key != "temperature"}
            response = client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=retry_payload,
            )
        response.raise_for_status()
        return response.json()
    finally:
        if close_client:
            client.close()


def _openai_json_response_payload(
    *,
    model: str,
    system_text: str,
    user_text: str,
    schema: dict[str, Any],
    schema_name: str,
    max_output_tokens: int,
    temperature: int | float | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "instructions": system_text,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": user_text,
                    }
                ],
            }
        ],
        "max_output_tokens": max_output_tokens,
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "schema": schema,
                "strict": False,
            }
        },
    }
    if temperature is not None:
        payload["temperature"] = temperature
    return payload


def _pricing_model_key(model: str) -> str:
    if model in MODEL_PRICING_USD_PER_MILLION:
        return model
    if model in MODEL_PRICING_ALIASES:
        return MODEL_PRICING_ALIASES[model]
    suffix = model.rsplit("/", maxsplit=1)[-1]
    if suffix in MODEL_PRICING_ALIASES:
        return MODEL_PRICING_ALIASES[suffix]
    return suffix


def _normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def _json_object_or_none(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _openai_response_has_refusal(response_json: dict[str, Any]) -> bool:
    for item in response_json.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and content.get("type") == "refusal":
                return True
    return False


def _openai_temperature_unsupported(text: str) -> bool:
    folded = text.lower()
    return "temperature" in folded and (
        "unsupported" in folded
        or "not support" in folded
        or "does not support" in folded
        or "invalid" in folded
    )

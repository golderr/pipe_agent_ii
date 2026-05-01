from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

DEFAULT_TRIAGE_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_EXTRACTION_MODEL = "claude-opus-4-7"

MODEL_PRICING_USD_PER_MILLION = {
    DEFAULT_TRIAGE_MODEL: {
        "input": Decimal("1.00"),
        "input_cache_creation": Decimal("1.25"),
        "input_cache_read": Decimal("0.10"),
        "output": Decimal("5.00"),
    },
    DEFAULT_EXTRACTION_MODEL: {
        "input": Decimal("15.00"),
        "input_cache_creation": Decimal("18.75"),
        "input_cache_read": Decimal("1.50"),
        "output": Decimal("75.00"),
    },
}


@dataclass(frozen=True, slots=True)
class LLMUsage:
    input_tokens_uncached: int
    input_tokens_cache_creation: int
    input_tokens_cached: int
    output_tokens: int


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
    pricing = MODEL_PRICING_USD_PER_MILLION.get(model)
    if pricing is None:
        raise RuntimeError(f"Unknown news LLM model pricing: {model}")
    return pricing


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

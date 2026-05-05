from __future__ import annotations

import json
from decimal import Decimal

import httpx

from tcg_pipeline.news.llm import (
    LLM_PROVIDER_VERCEL_AI_GATEWAY,
    OpenAIResponsesJSONClient,
    calculate_llm_cost_usd,
    create_anthropic_message,
    openai_stop_reason,
    openai_usage,
    pricing_for_model,
)


class TemperatureDeprecatedError(Exception):
    pass


class FakeMessages:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if "temperature" in kwargs:
            raise TemperatureDeprecatedError("`temperature` is deprecated for this model.")
        return {"ok": True}


class FakeClient:
    def __init__(self) -> None:
        self.messages = FakeMessages()


def test_create_anthropic_message_retries_without_deprecated_temperature() -> None:
    client = FakeClient()

    response = create_anthropic_message(
        client,
        model="claude-opus-4-7",
        max_tokens=100,
        temperature=0,
        messages=[],
    )

    assert response == {"ok": True}
    assert client.messages.calls == [
        {
            "model": "claude-opus-4-7",
            "max_tokens": 100,
            "temperature": 0,
            "messages": [],
        },
        {
            "model": "claude-opus-4-7",
            "max_tokens": 100,
            "messages": [],
        },
    ]


def test_openai_responses_json_client_posts_schema_request() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = request.read().decode("utf-8")
        assert request.url == "https://ai-gateway.vercel.sh/v1/responses"
        assert request.headers["authorization"] == "Bearer test-key"
        assert '"model":"openai/gpt-5.4"' in payload
        assert '"type":"json_schema"' in payload
        return httpx.Response(
            200,
            json={
                "model": "openai/gpt-5.4",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"relevant": true, "reason": "Project news."}',
                            }
                        ],
                    }
                ],
                "usage": {
                    "input_tokens": 100,
                    "input_tokens_details": {"cached_tokens": 20},
                    "output_tokens": 10,
                },
            },
        )

    client = OpenAIResponsesJSONClient(
        api_key="test-key",
        model="openai/gpt-5.4",
        provider=LLM_PROVIDER_VERCEL_AI_GATEWAY,
        base_url="https://ai-gateway.vercel.sh/v1",
        max_output_tokens=300,
        temperature=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    response = client.create_json_response(
        system_text="Classify the article.",
        user_text="A project broke ground.",
        schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["relevant", "reason"],
            "properties": {
                "relevant": {"type": "boolean"},
                "reason": {"type": "string"},
            },
        },
        schema_name="news_triage",
    )

    assert len(requests) == 1
    assert response.provider == LLM_PROVIDER_VERCEL_AI_GATEWAY
    assert response.model == "openai/gpt-5.4"
    assert response.payload == {"relevant": True, "reason": "Project news."}
    assert response.usage.input_tokens_uncached == 80
    assert response.usage.input_tokens_cached == 20
    assert response.usage.output_tokens == 10


def test_openai_responses_json_client_retries_without_unsupported_temperature() -> None:
    request_payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = request.read().decode("utf-8")
        request_payloads.append(json.loads(payload))
        if len(request_payloads) == 1:
            return httpx.Response(
                400,
                text="temperature is unsupported for this model",
            )
        return httpx.Response(
            200,
            json={
                "model": "gpt-5.4",
                "status": "completed",
                "output_text": '{"ok": true}',
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    client = OpenAIResponsesJSONClient(
        api_key="test-key",
        model="gpt-5.4",
        base_url="https://api.openai.com/v1",
        max_output_tokens=50,
        temperature=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    response = client.create_json_response(
        system_text="Return JSON.",
        user_text="ok",
        schema={"type": "object"},
        schema_name="test_schema",
    )

    assert "temperature" in request_payloads[0]
    assert "temperature" not in request_payloads[1]
    assert response.payload == {"ok": True}


def test_openai_usage_maps_cached_tokens() -> None:
    usage = openai_usage(
        {
            "input_tokens": 100,
            "input_tokens_details": {"cached_tokens": 25},
            "output_tokens": 10,
        }
    )

    assert usage.input_tokens_uncached == 75
    assert usage.input_tokens_cached == 25
    assert usage.input_tokens_cache_creation == 0
    assert usage.output_tokens == 10


def test_openai_stop_reason_maps_incomplete_and_refusal() -> None:
    assert openai_stop_reason(
        {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}
    ) == "max_tokens"
    assert openai_stop_reason(
        {
            "status": "completed",
            "output": [{"content": [{"type": "refusal", "refusal": "No."}]}],
        }
    ) == "refusal"


def test_pricing_supports_openai_gateway_model_suffix() -> None:
    assert pricing_for_model("openai/gpt-5.4") == pricing_for_model("gpt-5.4")
    cost = calculate_llm_cost_usd(
        "openai/gpt-5.4",
        input_tokens_uncached=1000,
        input_tokens_cache_creation=0,
        input_tokens_cached=0,
        output_tokens=100,
    )

    assert cost == Decimal("0.004000")

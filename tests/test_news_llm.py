from __future__ import annotations

from tcg_pipeline.news.llm import create_anthropic_message


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

from __future__ import annotations

from pathlib import Path

import yaml

from tcg_pipeline.semantic.constants import NEWS_SEMANTIC_CAPABILITY
from tcg_pipeline.settings import Settings


def test_news_use_legacy_semantic_defaults_to_new_path_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("NEWS_USE_LEGACY_SEMANTIC", raising=False)

    assert Settings(_env_file=None).news_use_legacy_semantic is False


def test_news_use_legacy_semantic_env_var_parses_boolean(monkeypatch) -> None:
    monkeypatch.setenv("NEWS_USE_LEGACY_SEMANTIC", "true")

    assert Settings(_env_file=None).news_use_legacy_semantic is True


def test_news_semantic_llm_settings_are_separate_from_extraction_settings() -> None:
    settings = Settings(
        _env_file=None,
        news_extract_model="claude-opus-4-7",
        news_semantic_model="claude-haiku-4-5-20251001",
    )

    assert NEWS_SEMANTIC_CAPABILITY == "semantic.news_v1"
    assert settings.news_extract_model == "claude-opus-4-7"
    assert settings.news_semantic_model == "claude-haiku-4-5-20251001"
    assert settings.news_semantic_provider == "anthropic"


def test_render_services_hold_legacy_semantic_path_until_smoke() -> None:
    render_config = yaml.safe_load(Path("render.yaml").read_text())
    service_env = {
        service["name"]: {entry["key"]: entry.get("value") for entry in service["envVars"]}
        for service in render_config["services"]
        if service["name"] in {"tcg-pipeline-api", "tcg-pipeline-worker"}
    }

    semantic_keys = {
        "NEWS_USE_LEGACY_SEMANTIC",
        "NEWS_SEMANTIC_PROVIDER",
        "NEWS_SEMANTIC_MODEL",
        "NEWS_SEMANTIC_MAX_TOKENS",
    }
    api_env = service_env["tcg-pipeline-api"]
    worker_env = service_env["tcg-pipeline-worker"]

    assert {key: api_env[key] for key in semantic_keys} == {
        key: worker_env[key] for key in semantic_keys
    }
    assert api_env["NEWS_USE_LEGACY_SEMANTIC"] == "true"
    assert api_env["NEWS_SEMANTIC_MODEL"] == "claude-opus-4-7"

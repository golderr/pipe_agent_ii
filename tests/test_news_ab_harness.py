from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tcg_pipeline.cli import app
from tcg_pipeline.news import ab_harness

runner = CliRunner()


def test_parse_candidate_specs_normalizes_provider_and_checks_pricing() -> None:
    candidates = ab_harness.parse_candidate_specs(
        "anthropic:claude-opus-4-7, vercel:anthropic/claude-sonnet-4-6, openai:gpt-5.4"
    )

    assert [candidate.key for candidate in candidates] == [
        "anthropic:claude-opus-4-7",
        "vercel_ai_gateway:anthropic/claude-sonnet-4-6",
        "openai:gpt-5.4",
    ]


def test_parse_candidate_specs_rejects_unknown_pricing() -> None:
    with pytest.raises(RuntimeError, match="Unknown news LLM model pricing"):
        ab_harness.parse_candidate_specs("openai:not-priced-yet")


def test_load_article_fixtures_validates_required_fields(tmp_path: Path) -> None:
    fixture_path = tmp_path / "articles.json"
    fixture_path.write_text(
        json.dumps(
            [
                {
                    "slug": "sample",
                    "url": "https://example.com/a",
                    "title": "Sample",
                    "body_text": "A sample article.",
                    "published_at": "2026-05-05T12:00:00Z",
                }
            ]
        ),
        encoding="utf-8",
    )

    fixtures = ab_harness.load_article_fixtures(fixture_path)

    assert len(fixtures) == 1
    assert fixtures[0].slug == "sample"
    assert fixtures[0].published_at is not None
    assert fixtures[0].published_at.tzinfo is not None


def test_summarize_candidate_results_counts_pipeline_metrics() -> None:
    summaries = ab_harness.summarize_candidate_results(
        {
            "anthropic:claude-opus-4-7": [
                {
                    "parse_status": "ok",
                    "reference_count": 2,
                    "matcher_status_counts": {"confirmed": 1, "new_candidate": 1},
                    "match_type_counts": {"address_composite": 1, "new_candidate": 1},
                    "review_item_counts": {
                        "possible_match": 0,
                        "new_candidate": 1,
                        "status_change": 1,
                        "total_projected": 2,
                    },
                    "agent_trigger": {"would_trigger": True},
                    "cost_usd": "0.125000",
                    "latency_ms": 1000,
                },
                {
                    "parse_status": "schema_invalid",
                    "reference_count": 0,
                    "matcher_status_counts": {},
                    "match_type_counts": {},
                    "review_item_counts": {
                        "possible_match": 0,
                        "new_candidate": 0,
                        "status_change": 0,
                        "total_projected": 0,
                    },
                    "agent_trigger": {"would_trigger": False},
                    "cost_usd": "0.025000",
                    "latency_ms": 500,
                },
            ]
        }
    )

    assert summaries == [
        {
            "candidate": "anthropic:claude-opus-4-7",
            "articles": 2,
            "parse_status_counts": {"ok": 1, "schema_invalid": 1},
            "references": 2,
            "matcher_status_counts": {"confirmed": 1, "new_candidate": 1},
            "match_type_counts": {"address_composite": 1, "new_candidate": 1},
            "agent_trigger_articles": 1,
            "agent_trigger_rate": 0.5,
            "review_item_counts": {
                "new_candidate": 1,
                "possible_match": 0,
                "status_change": 1,
                "total_projected": 2,
            },
            "total_cost_usd": "0.150000",
            "avg_cost_usd": "0.075000",
            "avg_latency_ms": 750.0,
            "errors": 0,
        }
    ]


def test_news_ab_extract_cli_invokes_harness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_path = tmp_path / "articles.json"
    output_path = tmp_path / "report.json"
    fixture_path.write_text("[]", encoding="utf-8")
    calls = {}

    def fake_run_extraction_ab_harness(**kwargs):  # type: ignore[no-untyped-def]
        calls.update(kwargs)
        return {
            "output_path": str(output_path),
            "candidate_summaries": [
                {
                    "candidate": "anthropic:claude-opus-4-7",
                    "articles": 1,
                    "parse_status_counts": {"ok": 1},
                    "references": 1,
                    "agent_trigger_rate": 0.0,
                    "total_cost_usd": "0.010000",
                }
            ],
        }

    monkeypatch.setattr(ab_harness, "run_extraction_ab_harness", fake_run_extraction_ab_harness)

    result = runner.invoke(
        app,
        [
            "news",
            "ab-extract",
            "--fixture",
            str(fixture_path),
            "--candidates",
            "anthropic:claude-opus-4-7",
            "--source-slug",
            "urbanize_la",
            "--output",
            str(output_path),
            "--limit",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls["fixture_path"] == fixture_path
    assert calls["candidates"] == "anthropic:claude-opus-4-7"
    assert calls["source_slug"] == "urbanize_la"
    assert calls["output_path"] == output_path
    assert calls["limit"] == 1
    assert f"Report: {output_path}" in result.output

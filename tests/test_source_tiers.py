from __future__ import annotations

from pathlib import Path

import pytest

from tcg_pipeline.source_tiers import (
    get_logical_source_type,
    load_source_tier_config,
)


def test_get_logical_source_type_maps_runtime_source_names() -> None:
    assert get_logical_source_type("ladbs_permits") == "ladbs_permit"
    assert get_logical_source_type("ladbs_permit_activity") == "ladbs_permit"
    assert get_logical_source_type("ladbs_inspections") == "ladbs_inspection"
    assert get_logical_source_type("costar") == "costar"
    assert get_logical_source_type("bizjournals_la") == "news_article"
    assert get_logical_source_type("news_paste_a_link") == "news_article"
    assert get_logical_source_type("news_backfill") == "news_article"
    assert get_logical_source_type("news_reextraction") == "news_article"
    assert get_logical_source_type("unknown_source") == "unknown_source"


def test_load_source_tier_config_reads_configured_tiers() -> None:
    config = load_source_tier_config()

    assert config.get_tier("ladbs_permit") == 1
    assert config.get_tier("pipedream") == 1
    assert config.get_tier("news_article") == 2
    assert config.get_tier("costar") == 3
    assert config.get_tier("forum") == 4


def test_load_source_tier_config_rejects_duplicate_assignments(tmp_path: Path) -> None:
    config_path = tmp_path / "source_tiers.yaml"
    config_path.write_text(
        "source_tiers:\n"
        "  tier_1:\n"
        "    - pipedream\n"
        "  tier_2:\n"
        "    - pipedream\n",
        encoding="utf-8",
    )

    config = load_source_tier_config(config_path)
    with pytest.raises(ValueError, match="assigned to multiple tiers"):
        _ = config.source_type_to_tier

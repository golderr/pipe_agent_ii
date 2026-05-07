from __future__ import annotations

from pathlib import Path

import pytest

from tcg_pipeline.semantic.constants import NEWS_SEMANTIC_CAPABILITY
from tcg_pipeline.semantic.glossary import (
    build_market_reason_code_registry,
    load_market_semantic_glossary,
)
from tcg_pipeline.semantic.news.prompting import (
    MARKET_GLOSSARY_DELIMITER,
    assemble_interpret_system_prompt,
)


def test_missing_market_semantic_glossary_returns_empty_addendum(tmp_path: Path) -> None:
    glossary = load_market_semantic_glossary("los_angeles", config_dir=tmp_path)

    assert glossary.slug == "los_angeles"
    assert glossary.has_addendum is False
    assert glossary.entries == ()
    assert glossary.as_prompt_addendum() == ""


def test_market_semantic_glossary_registers_reason_code_extensions(
    tmp_path: Path,
) -> None:
    glossary_dir = tmp_path / "new_york_city"
    glossary_dir.mkdir()
    (glossary_dir / "semantic_glossary.yaml").write_text(
        """
slug: new_york_city
notes: NYC entitlement terms.
status_phrases:
  - phrase: ULURP certification
    tcg_status: Pending
    reason_code_extension: news_status_ulurp_certification
    confidence_default: high
    promotes_status_alone: true
    notes: NYC public review certification.
unit_bucket_phrases:
  - phrase: workforce set-aside units
    field_name: workforce_units
    reason_code_extension: news_units_workforce_setaside_observed
    signal_only: true
""",
        encoding="utf-8",
    )

    glossary = load_market_semantic_glossary("new_york_city", config_dir=tmp_path)
    registry = build_market_reason_code_registry(glossary)

    assert glossary.has_addendum is True
    assert glossary.reason_code_extensions == (
        "news_status_ulurp_certification",
        "news_units_workforce_setaside_observed",
    )
    assert registry.by_code["news_status_ulurp_certification"].field_name == (
        "pipeline_status"
    )
    assert registry.by_code["news_status_ulurp_certification"].confidence_default == (
        "high"
    )
    assert registry.by_code["news_status_ulurp_certification"].promotes_status_alone is True
    assert registry.by_code["news_units_workforce_setaside_observed"].field_name == (
        "workforce_units"
    )
    assert registry.by_code["news_units_workforce_setaside_observed"].signal_only is True


def test_market_semantic_glossary_rejects_base_reason_code_collision(
    tmp_path: Path,
) -> None:
    glossary_dir = tmp_path / "new_york_city"
    glossary_dir.mkdir()
    (glossary_dir / "semantic_glossary.yaml").write_text(
        """
slug: new_york_city
status_phrases:
  - phrase: topping ceremony
    tcg_status: Under Construction
    reason_code_extension: news_topped_out
""",
        encoding="utf-8",
    )

    glossary = load_market_semantic_glossary("new_york_city", config_dir=tmp_path)
    with pytest.raises(ValueError, match="Duplicate semantic reason codes"):
        build_market_reason_code_registry(glossary)


def test_market_semantic_glossary_rejects_unknown_canonical_keys(
    tmp_path: Path,
) -> None:
    glossary_dir = tmp_path / "new_york_city"
    glossary_dir.mkdir()
    (glossary_dir / "semantic_glossary.yaml").write_text(
        """
slug: new_york_city
status_phrases:
  - phrase: ULURP certification
    tcg_stats: Pending
    reason_code_extension: news_status_ulurp_certification
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unknown canonical mapping keys"):
        load_market_semantic_glossary("new_york_city", config_dir=tmp_path)


def test_market_semantic_glossary_rejects_status_promotion_for_non_status_field(
    tmp_path: Path,
) -> None:
    glossary_dir = tmp_path / "new_york_city"
    glossary_dir.mkdir()
    (glossary_dir / "semantic_glossary.yaml").write_text(
        """
slug: new_york_city
unit_bucket_phrases:
  - phrase: workforce set-aside units
    field_name: workforce_units
    reason_code_extension: news_units_workforce_setaside_observed
    promotes_status_alone: true
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="promotes_status_alone"):
        load_market_semantic_glossary("new_york_city", config_dir=tmp_path)


def test_interpret_prompt_assembly_layers_base_registry_and_market_addendum(
    tmp_path: Path,
) -> None:
    glossary_dir = tmp_path / "new_york_city"
    glossary_dir.mkdir()
    (glossary_dir / "semantic_glossary.yaml").write_text(
        """
slug: new_york_city
status_phrases:
  - phrase: ULURP certification
    tcg_status: Pending
    reason_code_extension: news_status_ulurp_certification
""",
        encoding="utf-8",
    )

    prompt = assemble_interpret_system_prompt(
        "new_york_city",
        base_system_text="BASE TCG RUBRIC",
        glossary_config_dir=tmp_path,
    )

    assert prompt.capability_key == NEWS_SEMANTIC_CAPABILITY
    assert prompt.system_blocks[0] == "BASE TCG RUBRIC"
    assert prompt.system_blocks[1].startswith("Reason-code registry:")
    assert MARKET_GLOSSARY_DELIMITER in prompt.system_blocks
    assert prompt.system_text.index("BASE TCG RUBRIC") < prompt.system_text.index(
        "Reason-code registry:"
    )
    assert prompt.system_text.index("Reason-code registry:") < prompt.system_text.index(
        MARKET_GLOSSARY_DELIMITER
    )
    assert "news_topped_out" in prompt.system_text
    assert "news_status_ulurp_certification" in prompt.system_text
    assert "ULURP certification" in prompt.system_text
    assert (
        "news_status_ulurp_certification"
        in prompt.reason_code_registry.by_profile_field[("news_v1", "pipeline_status")]
    )

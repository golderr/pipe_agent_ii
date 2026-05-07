"""News-specific Pass 2c prompt assembly for semantic interpretation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from tcg_pipeline.news.prompts import PROMPT_ROOT
from tcg_pipeline.semantic.constants import NEWS_SEMANTIC_CAPABILITY
from tcg_pipeline.semantic.glossary import (
    MarketSemanticGlossary,
    build_market_reason_code_registry,
    load_market_semantic_glossary,
)
from tcg_pipeline.semantic.reason_codes import ReasonCode, ReasonCodeRegistry

INTERPRET_PROMPT_ID = "interpret_v1"
MARKET_GLOSSARY_DELIMITER = "MARKET-SPECIFIC TERMINOLOGY FOLLOWS"


@dataclass(frozen=True, slots=True)
class AssembledInterpretPrompt:
    prompt_id: str
    capability_key: str
    system_text: str
    system_hash: str
    reason_code_registry: ReasonCodeRegistry
    market_glossary: MarketSemanticGlossary
    system_blocks: tuple[str, ...]


def assemble_interpret_system_prompt(
    market_slug: str,
    *,
    base_system_text: str | None = None,
    base_system_path: Path | None = None,
    glossary_config_dir: Path | None = None,
) -> AssembledInterpretPrompt:
    base_text = _base_system_text(base_system_text, base_system_path)
    glossary = load_market_semantic_glossary(market_slug, config_dir=glossary_config_dir)
    registry = build_market_reason_code_registry(glossary)
    blocks = [
        base_text.strip(),
        "Reason-code registry:\n" + render_reason_code_registry_for_prompt(
            registry.by_code.values()
        ),
    ]
    glossary_text = glossary.as_prompt_addendum()
    if glossary_text:
        blocks.extend([MARKET_GLOSSARY_DELIMITER, glossary_text])
    system_text = "\n\n".join(block for block in blocks if block)
    return AssembledInterpretPrompt(
        prompt_id=INTERPRET_PROMPT_ID,
        capability_key=NEWS_SEMANTIC_CAPABILITY,
        system_text=system_text,
        system_hash=hashlib.sha256(system_text.encode("utf-8")).hexdigest(),
        reason_code_registry=registry,
        market_glossary=glossary,
        system_blocks=tuple(blocks),
    )


def load_interpret_schema(base_schema_path: Path | None = None) -> dict:
    path = base_schema_path or PROMPT_ROOT / INTERPRET_PROMPT_ID / "schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def render_reason_code_registry_for_prompt(reason_codes: Iterable[ReasonCode]) -> str:
    lines = []
    for reason in sorted(
        (reason for reason in reason_codes if reason.source_profile == "news_v1"),
        key=lambda item: (item.field_name, item.code),
    ):
        flags = []
        if reason.promotes_status_alone:
            flags.append("promotes_status_alone")
        if reason.requires_corroboration:
            flags.append("requires_corroboration")
        if reason.signal_only:
            flags.append("signal_only")
        if reason.review_item_template:
            flags.append(f"review_item_template={reason.review_item_template}")
        flags_text = f" | flags={', '.join(flags)}" if flags else ""
        lines.append(
            f"- {reason.code} | field={reason.field_name} | "
            f"confidence_default={reason.confidence_default}{flags_text} | "
            f"{reason.description}"
        )
    return "\n".join(lines)


def _base_system_text(
    base_system_text: str | None,
    base_system_path: Path | None,
) -> str:
    if base_system_text is not None:
        return base_system_text
    path = base_system_path or PROMPT_ROOT / INTERPRET_PROMPT_ID / "system.md"
    return path.read_text(encoding="utf-8")

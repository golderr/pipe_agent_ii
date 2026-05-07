from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from tcg_pipeline.semantic.reason_codes import (
    ReasonCode,
    ReasonCodeRegistry,
    build_reason_code_registry,
)
from tcg_pipeline.semantic.types import Confidence

GLOSSARY_FILENAME = "semantic_glossary.yaml"
REASON_CODE_RE = re.compile(r"[a-z][a-z0-9_]*")
SECTION_FIELD_NAMES: Mapping[str, str] = {
    "status_phrases": "pipeline_status",
    "product_type_phrases": "product_type",
    "age_restriction_phrases": "age_restriction",
    "delivery_timing_phrases": "date_delivery",
    "tenure_phrases": "rent_or_sale",
    "identifier_patterns": "candidate_identifiers",
}
UNIT_BUCKET_FIELDS = {
    "total_units",
    "affordable_units",
    "workforce_units",
    "market_rate_units",
}
SIGNAL_ONLY_EXTENSION_FIELDS = {
    "rent_or_sale",
    "candidate_identifiers",
}
GLOSSARY_SECTIONS = tuple(SECTION_FIELD_NAMES) + ("unit_bucket_phrases",)
CANONICAL_KEYS_BY_SECTION: Mapping[str, frozenset[str]] = {
    "status_phrases": frozenset({"tcg_status"}),
    "product_type_phrases": frozenset({"tcg_product_type"}),
    "age_restriction_phrases": frozenset({"tcg_age_restriction"}),
    "delivery_timing_phrases": frozenset({"tcg_delivery_timing", "normalized_date_rule"}),
    "tenure_phrases": frozenset({"tcg_tenure", "tcg_rent_or_sale"}),
    "identifier_patterns": frozenset({"tcg_identifier_type"}),
    "unit_bucket_phrases": frozenset({"tcg_unit_bucket"}),
}
ENTRY_METADATA_KEYS = frozenset(
    {
        "phrase",
        "pattern",
        "field_name",
        "tcg_field",
        "reason_code_extension",
        "confidence_default",
        "promotes_status_alone",
        "requires_corroboration",
        "signal_only",
        "notes",
    }
)


@dataclass(frozen=True, slots=True)
class MarketGlossaryEntry:
    section: str
    phrase: str
    field_name: str
    canonical_mapping: Mapping[str, Any] = field(default_factory=dict)
    reason_code_extension: str | None = None
    confidence_default: Confidence = "medium"
    promotes_status_alone: bool = False
    requires_corroboration: bool = False
    signal_only: bool | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class MarketSemanticGlossary:
    slug: str
    notes: str | None = None
    entries: tuple[MarketGlossaryEntry, ...] = ()
    path: Path | None = None

    @property
    def has_addendum(self) -> bool:
        return self.path is not None

    @property
    def reason_code_extensions(self) -> tuple[str, ...]:
        return tuple(
            entry.reason_code_extension
            for entry in self.entries
            if entry.reason_code_extension is not None
        )

    def as_prompt_addendum(self) -> str:
        if not self.entries and not self.notes:
            return ""
        lines = [f"Market semantic glossary: {self.slug}"]
        if self.notes:
            lines.extend(["", "Notes:", self.notes.strip()])
        for section in GLOSSARY_SECTIONS:
            section_entries = [entry for entry in self.entries if entry.section == section]
            if not section_entries:
                continue
            lines.extend(["", f"{section}:"])
            for entry in section_entries:
                lines.append(f"- phrase: {entry.phrase}")
                lines.append(f"  field: {entry.field_name}")
                if entry.canonical_mapping:
                    for key, value in sorted(entry.canonical_mapping.items()):
                        lines.append(f"  {key}: {value}")
                if entry.reason_code_extension:
                    lines.append(f"  reason_code_extension: {entry.reason_code_extension}")
                if entry.confidence_default != "medium":
                    lines.append(f"  confidence_default: {entry.confidence_default}")
                if entry.promotes_status_alone:
                    lines.append("  promotes_status_alone: true")
                if entry.requires_corroboration:
                    lines.append("  requires_corroboration: true")
                if entry.signal_only is not None:
                    lines.append(f"  signal_only: {str(entry.signal_only).lower()}")
                if entry.notes:
                    lines.append(f"  notes: {entry.notes}")
        return "\n".join(lines)


def default_market_glossary_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "markets"


def load_market_semantic_glossary(
    market_slug: str,
    *,
    config_dir: Path | None = None,
) -> MarketSemanticGlossary:
    base_dir = config_dir or default_market_glossary_dir()
    path = base_dir / market_slug / GLOSSARY_FILENAME
    if not path.exists():
        return MarketSemanticGlossary(slug=market_slug)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, Mapping):
        raise ValueError(f"Semantic glossary must be a mapping: {path}")
    return parse_market_semantic_glossary(data, market_slug=market_slug, path=path)


def parse_market_semantic_glossary(
    data: Mapping[str, Any],
    *,
    market_slug: str,
    path: Path | None = None,
) -> MarketSemanticGlossary:
    declared_slug = data.get("slug")
    if declared_slug is not None and declared_slug != market_slug:
        raise ValueError(
            f"Semantic glossary declared slug '{declared_slug}', expected '{market_slug}'."
        )
    entries: list[MarketGlossaryEntry] = []
    for section in GLOSSARY_SECTIONS:
        entries.extend(_parse_section(section, data.get(section)))
    _validate_unique_extensions(entries)
    return MarketSemanticGlossary(
        slug=market_slug,
        notes=_optional_string(data.get("notes")),
        entries=tuple(entries),
        path=path,
    )


def reason_code_extensions_for_glossary(
    glossary: MarketSemanticGlossary,
) -> tuple[ReasonCode, ...]:
    reason_codes: list[ReasonCode] = []
    for entry in glossary.entries:
        if entry.reason_code_extension is None:
            continue
        reason_codes.append(
            ReasonCode(
                code=entry.reason_code_extension,
                source_profile="news_v1",
                field_name=entry.field_name,
                label=f"Market glossary: {entry.phrase}",
                description=_extension_description(glossary, entry),
                confidence_default=entry.confidence_default,
                promotes_status_alone=entry.promotes_status_alone,
                requires_corroboration=entry.requires_corroboration,
                signal_only=_entry_signal_only(entry),
            )
        )
    return tuple(reason_codes)


def build_market_reason_code_registry(glossary: MarketSemanticGlossary) -> ReasonCodeRegistry:
    return build_reason_code_registry(reason_code_extensions_for_glossary(glossary))


def _parse_section(section: str, value: Any) -> tuple[MarketGlossaryEntry, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"Semantic glossary section '{section}' must be a list")
    entries: list[MarketGlossaryEntry] = []
    for raw_entry in value:
        if not isinstance(raw_entry, Mapping):
            raise ValueError(f"Semantic glossary entry in '{section}' must be a mapping")
        phrase = _entry_phrase(raw_entry)
        field_name = _entry_field_name(section, raw_entry)
        reason_code_extension = _optional_string(raw_entry.get("reason_code_extension"))
        if reason_code_extension is not None and not REASON_CODE_RE.fullmatch(
            reason_code_extension
        ):
            raise ValueError(f"Invalid reason_code_extension '{reason_code_extension}'")
        confidence_default = _confidence_default(raw_entry.get("confidence_default"))
        promotes_status_alone = _optional_bool(
            raw_entry.get("promotes_status_alone"),
            field_name="promotes_status_alone",
            default=False,
        )
        if promotes_status_alone and field_name != "pipeline_status":
            raise ValueError(
                "promotes_status_alone can only be true for pipeline_status glossary entries"
            )
        entries.append(
            MarketGlossaryEntry(
                section=section,
                phrase=phrase,
                field_name=field_name,
                canonical_mapping=_canonical_mapping(section, raw_entry),
                reason_code_extension=reason_code_extension,
                confidence_default=confidence_default,
                promotes_status_alone=promotes_status_alone,
                requires_corroboration=_optional_bool(
                    raw_entry.get("requires_corroboration"),
                    field_name="requires_corroboration",
                    default=False,
                ),
                signal_only=_optional_bool(
                    raw_entry.get("signal_only"),
                    field_name="signal_only",
                    default=None,
                ),
                notes=_optional_string(raw_entry.get("notes")),
            )
        )
    return tuple(entries)


def _entry_phrase(entry: Mapping[str, Any]) -> str:
    phrase = entry.get("phrase") or entry.get("pattern")
    if not isinstance(phrase, str) or not phrase.strip():
        raise ValueError("Semantic glossary entries require a non-empty phrase or pattern")
    return phrase.strip()


def _entry_field_name(section: str, entry: Mapping[str, Any]) -> str:
    explicit = entry.get("field_name") or entry.get("tcg_field")
    if isinstance(explicit, str) and explicit.strip():
        field_name = explicit.strip()
        if section == "unit_bucket_phrases" and field_name not in UNIT_BUCKET_FIELDS:
            raise ValueError(
                "unit_bucket_phrases field_name must be one of "
                f"{sorted(UNIT_BUCKET_FIELDS)}"
            )
        return field_name
    if section == "unit_bucket_phrases":
        raise ValueError("unit_bucket_phrases entries require field_name or tcg_field")
    return SECTION_FIELD_NAMES[section]


def _canonical_mapping(section: str, entry: Mapping[str, Any]) -> Mapping[str, Any]:
    allowed_keys = CANONICAL_KEYS_BY_SECTION[section]
    canonical_mapping = {
        key: value for key, value in entry.items() if key not in ENTRY_METADATA_KEYS
    }
    unknown_keys = {
        key
        for key in canonical_mapping
        if key.startswith("tcg_") and key not in allowed_keys
    }
    if unknown_keys:
        raise ValueError(
            f"Unknown canonical mapping keys in '{section}': {sorted(unknown_keys)}"
        )
    return canonical_mapping


def _validate_unique_extensions(entries: list[MarketGlossaryEntry]) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for entry in entries:
        extension = entry.reason_code_extension
        if extension is None:
            continue
        if extension in seen:
            duplicates.add(extension)
        seen.add(extension)
    if duplicates:
        raise ValueError(f"Duplicate glossary reason_code_extension values: {sorted(duplicates)}")


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Expected string value, got {type(value).__name__}")
    stripped = value.strip()
    return stripped or None


def _confidence_default(value: Any) -> Confidence:
    if value is None:
        return "medium"
    if value == "low":
        return "low"
    if value == "medium":
        return "medium"
    if value == "high":
        return "high"
    raise ValueError("confidence_default must be one of 'low', 'medium', or 'high'")


def _optional_bool(
    value: Any,
    *,
    field_name: str,
    default: bool | None,
) -> bool | None:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _entry_signal_only(entry: MarketGlossaryEntry) -> bool:
    if entry.signal_only is not None:
        return entry.signal_only
    return entry.field_name in SIGNAL_ONLY_EXTENSION_FIELDS


def _extension_description(
    glossary: MarketSemanticGlossary,
    entry: MarketGlossaryEntry,
) -> str:
    notes = f" {entry.notes}" if entry.notes else ""
    return f"Market-specific semantic glossary entry for {glossary.slug}: {entry.phrase}.{notes}"

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, computed_field


def _default_source_tier_config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "source_tiers.yaml"


LOGICAL_SOURCE_TYPE_BY_SOURCE_NAME = {
    "ladbs_permits": "ladbs_permit",
    "ladbs_permit_activity": "ladbs_permit",
    "ladbs_inspections": "ladbs_inspection",
    "ladbs_cofo": "ladbs_cofo",
    "pipedream": "pipedream",
    "costar": "costar",
}


class SourceTierConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_tiers: dict[str, list[str]] = Field(default_factory=dict)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def source_type_to_tier(self) -> dict[str, int]:
        mapping: dict[str, int] = {}
        for tier_name, source_types in self.source_tiers.items():
            tier = _parse_tier_name(tier_name)
            for source_type in source_types:
                if source_type in mapping:
                    raise ValueError(
                        f"Source type '{source_type}' is assigned to multiple tiers."
                    )
                mapping[source_type] = tier
        return mapping

    def get_tier(self, source_type: str) -> int:
        try:
            return self.source_type_to_tier[source_type]
        except KeyError as exc:
            raise KeyError(
                f"Source type '{source_type}' is not configured in source_tiers."
            ) from exc


def _parse_tier_name(tier_name: str) -> int:
    prefix = "tier_"
    if not tier_name.startswith(prefix):
        raise ValueError(f"Invalid source tier key '{tier_name}'. Expected keys like 'tier_1'.")
    try:
        return int(tier_name[len(prefix) :])
    except ValueError as exc:
        raise ValueError(
            f"Invalid source tier key '{tier_name}'. Expected keys like 'tier_1'."
        ) from exc


def get_logical_source_type(source_name: str) -> str:
    return LOGICAL_SOURCE_TYPE_BY_SOURCE_NAME.get(source_name, source_name)


def load_source_tier_config(
    config_path: Path | None = None,
) -> SourceTierConfig:
    path = config_path or _default_source_tier_config_path()
    if not path.exists():
        raise FileNotFoundError(f"Source tier config not found: {path}")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return SourceTierConfig.model_validate(data)


@lru_cache(maxsize=1)
def get_source_tier_config() -> SourceTierConfig:
    """Load and cache the shared source-tier config for runtime use.

    Tests that need to read a modified YAML file should use `load_source_tier_config(config_path)`
    directly, or clear this cache before reloading.
    """
    return load_source_tier_config()


def get_source_tier(source_type: str) -> int:
    return get_source_tier_config().get_tier(source_type)


def get_source_tier_for_source_name(source_name: str) -> int:
    return get_source_tier(get_logical_source_type(source_name))

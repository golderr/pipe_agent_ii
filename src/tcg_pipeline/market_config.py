from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


def _default_market_config_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "markets"


class MarketBounds(BaseModel):
    jurisdictions: list[str] = Field(default_factory=list)


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    collector: str
    endpoint: str
    adapter: str | None = None
    schedule: str | None = None
    role: str | None = None
    jurisdiction: str | None = None
    coverage_scope: str | None = None
    supported_markets: list[str] = Field(default_factory=list)
    matching_keys: list[str] = Field(default_factory=list)
    inclusion_rule: str | None = None
    soql_filter: str | None = None
    where: str | None = None
    select: str | None = None
    order_by: str | None = None
    page_size: int = 1000
    max_records: int | None = None
    timeout_seconds: float = 30.0
    incremental_overlap_hours: int = 24
    create_new_candidates: bool = True
    mode: str | None = None
    trigger: str | None = None

    @property
    def adapter_name(self) -> str:
        return self.adapter or self.name

    @property
    def effective_where(self) -> str | None:
        clauses = [clause for clause in [self.where, self.soql_filter] if clause]
        if not clauses:
            return None
        if len(clauses) == 1:
            return clauses[0]
        return " AND ".join(f"({clause})" for clause in clauses)


class MarketConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    market: str
    display_name: str
    bounds: MarketBounds = Field(default_factory=MarketBounds)
    sources: list[SourceConfig] = Field(default_factory=list)

    def get_source(self, source_name: str) -> SourceConfig:
        for source in self.sources:
            if source.name == source_name:
                return source
        raise KeyError(f"Source '{source_name}' is not defined for market '{self.market}'.")


def load_market_config(
    market: str,
    *,
    config_dir: Path | None = None,
) -> MarketConfig:
    base_dir = config_dir or _default_market_config_dir()
    config_path = base_dir / f"{market}.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"Market config not found: {config_path}")

    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    config = MarketConfig.model_validate(data)
    if config.market != market:
        raise ValueError(
            f"Market config '{config_path}' declared market '{config.market}', expected '{market}'."
        )
    return config


@lru_cache(maxsize=16)
def get_market_config(market: str) -> MarketConfig:
    return load_market_config(market)

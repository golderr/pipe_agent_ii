from __future__ import annotations

from tcg_pipeline.collectors.base import BaseCollector
from tcg_pipeline.collectors.socrata import SocrataCollector
from tcg_pipeline.market_config import SourceConfig
from tcg_pipeline.source_adapters import get_source_adapter


def build_collector(source_config: SourceConfig, *, market: str) -> BaseCollector:
    if source_config.collector == "socrata":
        return SocrataCollector(
            source_config.name,
            source_config,
            row_adapter=get_source_adapter(source_config, market=market),
        )
    raise KeyError(
        f"Collector type '{source_config.collector}' is not implemented for source "
        f"'{source_config.name}'."
    )

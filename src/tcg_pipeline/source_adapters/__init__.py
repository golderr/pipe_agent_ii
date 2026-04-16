from __future__ import annotations

from collections.abc import Callable

from tcg_pipeline.market_config import SourceConfig
from tcg_pipeline.source_adapters.ladbs import (
    RawRecordAdapter,
    make_ladbs_cofo_adapter,
    make_ladbs_new_housing_adapter,
    make_ladbs_permits_adapter,
)

AdapterBuilder = Callable[..., RawRecordAdapter]

ADAPTER_BUILDERS: dict[str, AdapterBuilder] = {
    "ladbs_permits": make_ladbs_permits_adapter,
    "ladbs_new_housing": make_ladbs_new_housing_adapter,
    "ladbs_cofo": make_ladbs_cofo_adapter,
}


def get_source_adapter(source_config: SourceConfig, *, market: str) -> RawRecordAdapter:
    adapter_name = source_config.adapter_name
    builder = ADAPTER_BUILDERS.get(adapter_name)
    if builder is None:
        raise KeyError(f"Source adapter '{adapter_name}' is not implemented.")
    return builder(market=market, source_name=source_config.name)


__all__ = ["ADAPTER_BUILDERS", "RawRecordAdapter", "get_source_adapter"]

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import httpx

from tcg_pipeline.collectors.base import BaseCollector, RawRecord
from tcg_pipeline.market_config import SourceConfig
from tcg_pipeline.settings import get_settings

RawRecordAdapter = Callable[[Mapping[str, Any]], RawRecord | None]


class SocrataCollector(BaseCollector):
    def __init__(
        self,
        source_name: str,
        config: SourceConfig,
        *,
        row_adapter: RawRecordAdapter,
    ) -> None:
        super().__init__(source_name, config.model_dump(mode="python"))
        self.source_config = config
        self.row_adapter = row_adapter

    async def collect(self) -> list[RawRecord]:
        collected: list[RawRecord] = []
        offset = 0
        settings = get_settings()
        headers = _build_headers(settings.socrata_app_token)
        timeout = httpx.Timeout(self.source_config.timeout_seconds)

        async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
            while True:
                params = _build_query_params(self.source_config, offset=offset)
                response = await client.get(self.source_config.endpoint, params=params)
                response.raise_for_status()
                rows = response.json()
                if not isinstance(rows, list):
                    raise ValueError("Socrata collector expected a JSON array response.")
                if not rows:
                    break

                for row in rows:
                    if not isinstance(row, Mapping):
                        continue
                    adapted = self.row_adapter(row)
                    if adapted is None:
                        continue
                    collected.append(adapted)
                    if (
                        self.source_config.max_records is not None
                        and len(collected) >= self.source_config.max_records
                    ):
                        return collected[: self.source_config.max_records]

                if len(rows) < self.source_config.page_size:
                    break
                offset += self.source_config.page_size

        return collected


def _build_headers(app_token: str | None) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if app_token:
        headers["X-App-Token"] = app_token
    return headers


def _build_query_params(config: SourceConfig, *, offset: int) -> dict[str, Any]:
    params: dict[str, Any] = {
        "$limit": config.page_size,
        "$offset": offset,
    }
    if config.effective_where:
        params["$where"] = config.effective_where
    if config.select:
        params["$select"] = config.select
    if config.order_by:
        params["$order"] = config.order_by
    return params

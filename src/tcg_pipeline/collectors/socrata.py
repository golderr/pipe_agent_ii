from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

import httpx

from tcg_pipeline.collectors.base import BaseCollector, CollectionMode, CollectionRequest, RawRecord
from tcg_pipeline.market_config import SourceConfig
from tcg_pipeline.settings import get_settings

RawRecordAdapter = Callable[[Mapping[str, Any]], RawRecord | None]

DEFAULT_PREVIEW_ORDER = ":updated_at DESC, :id DESC"
DEFAULT_PRODUCTION_ORDER = ":updated_at ASC, :id ASC"
DEFAULT_SELECT = ":*, *"


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

    async def collect(self, request: CollectionRequest | None = None) -> list[RawRecord]:
        request = request or CollectionRequest()
        collected: list[RawRecord] = []
        offset = 0
        settings = get_settings()
        headers = _build_headers(settings.socrata_app_token)
        timeout = httpx.Timeout(self.source_config.timeout_seconds)

        async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
            while True:
                params = _build_query_params(self.source_config, request=request, offset=offset)
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
                    _apply_source_metadata(adapted, row)
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


def _build_query_params(
    config: SourceConfig,
    *,
    request: CollectionRequest,
    offset: int,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "$limit": config.page_size,
        "$offset": offset,
    }
    effective_where = _build_effective_where(config, request=request)
    if effective_where:
        params["$where"] = effective_where
    params["$select"] = _build_select_clause(config.select)
    params["$order"] = _build_order_clause(config.order_by, request.mode)
    return params


def _build_effective_where(config: SourceConfig, *, request: CollectionRequest) -> str | None:
    clauses: list[str] = []
    if config.effective_where:
        clauses.append(config.effective_where)
    if request.mode == CollectionMode.INCREMENTAL and request.updated_since is not None:
        clauses.append(f":updated_at >= {_format_socrata_timestamp(request.updated_since)}")
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return " AND ".join(f"({clause})" for clause in clauses)


def _build_select_clause(select: str | None) -> str:
    if not select:
        return DEFAULT_SELECT
    if ":*" in select or all(field in select for field in [":id", ":created_at", ":updated_at"]):
        return select
    return f":id, :created_at, :updated_at, {select}"


def _build_order_clause(order_by: str | None, mode: CollectionMode) -> str:
    if order_by:
        return order_by
    if mode == CollectionMode.PREVIEW:
        return DEFAULT_PREVIEW_ORDER
    return DEFAULT_PRODUCTION_ORDER


def _apply_source_metadata(raw_record: RawRecord, row: Mapping[str, Any]) -> None:
    row_dict = dict(row)
    raw_record.source_row_id = _coerce_text(row_dict.get(":id"))
    raw_record.source_created_at = _parse_socrata_timestamp(row_dict.get(":created_at"))
    raw_record.source_updated_at = _parse_socrata_timestamp(row_dict.get(":updated_at"))
    raw_record.source_row_hash = _hash_row(row_dict)


def _parse_socrata_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _format_socrata_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    value = value.astimezone(UTC)
    return f"'{value.isoformat(timespec='milliseconds').replace('+00:00', 'Z')}'"


def _hash_row(row: dict[str, Any]) -> str:
    payload = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

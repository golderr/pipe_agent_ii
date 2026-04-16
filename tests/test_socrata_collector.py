from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
import respx

from tcg_pipeline.collectors.base import CollectionMode, CollectionRequest, RawRecord
from tcg_pipeline.collectors.socrata import (
    DEFAULT_PREVIEW_ORDER,
    DEFAULT_PRODUCTION_ORDER,
    SocrataCollector,
    _build_select_clause,
    _hash_row,
)
from tcg_pipeline.market_config import SourceConfig


def test_socrata_collector_paginates_until_short_page() -> None:
    source_config = SourceConfig(
        name="ladbs_permits",
        collector="socrata",
        endpoint="https://data.lacity.org/resource/hbkd-qubn.json",
        page_size=2,
        soql_filter="permit_type='Bldg-New'",
    )

    seen_offsets: list[str] = []
    seen_orders: list[str] = []
    seen_selects: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_offsets.append(request.url.params["$offset"])
        seen_orders.append(request.url.params["$order"])
        seen_selects.append(request.url.params["$select"])
        offset = int(request.url.params["$offset"])
        if offset == 0:
            payload = [
                {
                    ":id": "row-1",
                    ":created_at": "2020-05-04T09:18:09.965Z",
                    ":updated_at": "2020-05-04T09:18:23.851Z",
                    "pcis_permit": "P-1",
                },
                {
                    ":id": "row-2",
                    ":created_at": "2020-05-05T09:18:09.965Z",
                    ":updated_at": "2020-05-05T09:18:23.851Z",
                    "pcis_permit": "P-2",
                },
            ]
        else:
            payload = [
                {
                    ":id": "row-3",
                    ":created_at": "2020-05-06T09:18:09.965Z",
                    ":updated_at": "2020-05-06T09:18:23.851Z",
                    "pcis_permit": "P-3",
                }
            ]
        return httpx.Response(200, json=payload)

    with respx.mock(assert_all_called=True) as router:
        router.get("https://data.lacity.org/resource/hbkd-qubn.json").mock(side_effect=handler)

        collector = SocrataCollector(
            "ladbs_permits",
            source_config,
            row_adapter=lambda row: RawRecord(
                source_name="ladbs_permits",
                source_record_id=str(row["pcis_permit"]),
                raw_payload=dict(row),
            ),
        )
        records = asyncio.run(collector.collect())

    assert [record.source_record_id for record in records] == ["P-1", "P-2", "P-3"]
    assert seen_offsets == ["0", "2"]
    assert seen_orders == [DEFAULT_PRODUCTION_ORDER, DEFAULT_PRODUCTION_ORDER]
    assert seen_selects == [":*, *", ":*, *"]
    assert records[0].source_row_id == "row-1"
    assert records[0].source_created_at == datetime(2020, 5, 4, 9, 18, 9, 965000, tzinfo=UTC)
    assert records[0].source_updated_at == datetime(2020, 5, 4, 9, 18, 23, 851000, tzinfo=UTC)
    assert records[0].source_row_hash is not None


def test_socrata_collector_uses_preview_and_incremental_query_shapes() -> None:
    source_config = SourceConfig(
        name="ladbs_permits",
        collector="socrata",
        endpoint="https://data.lacity.org/resource/hbkd-qubn.json",
        page_size=2,
        soql_filter="permit_type='Bldg-New'",
    )

    preview_orders: list[str] = []
    incremental_wheres: list[str] = []

    def preview_handler(request: httpx.Request) -> httpx.Response:
        preview_orders.append(request.url.params["$order"])
        return httpx.Response(200, json=[])

    def incremental_handler(request: httpx.Request) -> httpx.Response:
        incremental_wheres.append(request.url.params["$where"])
        return httpx.Response(200, json=[])

    with respx.mock(assert_all_called=True) as router:
        route = router.get("https://data.lacity.org/resource/hbkd-qubn.json")
        route.mock(side_effect=preview_handler)

        collector = SocrataCollector(
            "ladbs_permits",
            source_config,
            row_adapter=lambda row: RawRecord(
                source_name="ladbs_permits",
                source_record_id=str(row["pcis_permit"]),
                raw_payload=dict(row),
            ),
        )
        asyncio.run(
            collector.collect(CollectionRequest(mode=CollectionMode.PREVIEW))
        )

        route.mock(side_effect=incremental_handler)
        asyncio.run(
            collector.collect(
                CollectionRequest(
                    mode=CollectionMode.INCREMENTAL,
                    updated_since=datetime(2026, 4, 15, 12, 30, tzinfo=UTC),
                )
            )
        )

    assert preview_orders == [DEFAULT_PREVIEW_ORDER]
    assert len(incremental_wheres) == 1
    assert "permit_type='Bldg-New'" in incremental_wheres[0]
    assert ":updated_at >=" in incremental_wheres[0]
    assert "2026-04-15T12:30:00.000Z" in incremental_wheres[0]


def test_hash_row_ignores_socrata_system_fields() -> None:
    row_with_old_metadata = {
        ":id": "row-1",
        ":created_at": "2020-05-04T09:18:09.965Z",
        ":updated_at": "2020-05-04T09:18:23.851Z",
        "pcis_permit": "P-1",
        "valuation": "1000",
    }
    row_with_new_metadata = {
        ":id": "row-99",
        ":created_at": "2020-05-01T00:00:00.000Z",
        ":updated_at": "2026-04-16T00:00:00.000Z",
        ":version": "42",
        "pcis_permit": "P-1",
        "valuation": "1000",
    }

    assert _hash_row(row_with_old_metadata) == _hash_row(row_with_new_metadata)


def test_build_select_clause_prepends_required_system_fields() -> None:
    assert (
        _build_select_clause("pcis_permit, permit_type")
        == ":id, :created_at, :updated_at, pcis_permit, permit_type"
    )

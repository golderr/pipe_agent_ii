from __future__ import annotations

import asyncio

import httpx
import respx

from tcg_pipeline.collectors.base import RawRecord
from tcg_pipeline.collectors.socrata import SocrataCollector
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

    def handler(request: httpx.Request) -> httpx.Response:
        seen_offsets.append(request.url.params["$offset"])
        offset = int(request.url.params["$offset"])
        if offset == 0:
            payload = [
                {"pcis_permit": "P-1"},
                {"pcis_permit": "P-2"},
            ]
        else:
            payload = [{"pcis_permit": "P-3"}]
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

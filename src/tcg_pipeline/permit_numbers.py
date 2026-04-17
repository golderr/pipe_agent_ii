from __future__ import annotations

import re
from typing import Any

from tcg_pipeline.ingesters._common import clean_identifier_text

LADBS_PERMIT_NUMBER_PARTS = 3
LADBS_PERMIT_NUMBER_SEGMENT_LENGTH = 5
LADBS_PCIS_PERMIT_URL_RE = re.compile(
    r"PcisPermitDetail\?[^#\s]*?\bid1=(?P<id1>\d+)&id2=(?P<id2>\d+)&id3=(?P<id3>\d+)",
    re.IGNORECASE,
)


def normalize_ladbs_permit_number(value: Any) -> str | None:
    cleaned = clean_identifier_text(value)
    if cleaned is None:
        return None

    parts = [part for part in re.split(r"[\s-]+", cleaned) if part]
    if len(parts) != LADBS_PERMIT_NUMBER_PARTS or not all(part.isdigit() for part in parts):
        return cleaned

    return "-".join(part.zfill(LADBS_PERMIT_NUMBER_SEGMENT_LENGTH) for part in parts)


def extract_ladbs_pcis_permit_numbers(source_urls: list[str] | tuple[str, ...]) -> list[str]:
    permit_numbers: list[str] = []
    for source_url in source_urls:
        if not source_url:
            continue
        for match in LADBS_PCIS_PERMIT_URL_RE.finditer(source_url):
            permit_number = normalize_ladbs_permit_number(
                f"{match.group('id1')}-{match.group('id2')}-{match.group('id3')}"
            )
            if permit_number and permit_number not in permit_numbers:
                permit_numbers.append(permit_number)
    return permit_numbers

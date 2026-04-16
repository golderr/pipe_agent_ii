from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class CollectionMode(enum.StrEnum):
    PREVIEW = "preview"
    FULL = "full"
    INCREMENTAL = "incremental"


@dataclass(slots=True)
class CollectionRequest:
    mode: CollectionMode = CollectionMode.FULL
    updated_since: datetime | None = None


@dataclass(slots=True)
class RawRecord:
    source_name: str
    source_record_id: str
    raw_payload: dict[str, Any]
    canonical_address: str | None = None
    project_name: str | None = None
    identifiers: dict[str, list[str]] = field(default_factory=dict)
    mapped_fields: dict[str, Any] = field(default_factory=dict)
    lat: float | None = None
    lng: float | None = None
    source_row_id: str | None = None
    source_created_at: datetime | None = None
    source_updated_at: datetime | None = None
    source_row_hash: str | None = None


class BaseCollector(ABC):
    def __init__(self, source_name: str, config: Mapping[str, Any]) -> None:
        self.source_name = source_name
        self.config = dict(config)

    @abstractmethod
    async def collect(self, request: CollectionRequest | None = None) -> list[RawRecord]:
        raise NotImplementedError

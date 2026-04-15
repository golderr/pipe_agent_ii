from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping


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


class BaseCollector(ABC):
    def __init__(self, source_name: str, config: Mapping[str, Any]) -> None:
        self.source_name = source_name
        self.config = dict(config)

    @abstractmethod
    async def collect(self) -> list[RawRecord]:
        raise NotImplementedError

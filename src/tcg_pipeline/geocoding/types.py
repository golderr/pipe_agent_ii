from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

from tcg_pipeline.db.models import GeocodeConfidence

GeocodeProvider = Literal["geocodio", "esri"]
GeocodeStatus = Literal["accepted", "skipped", "failed", "low_confidence"]


@dataclass(frozen=True, slots=True)
class GeocodeAddress:
    address: str
    city: str | None
    state: str | None
    zip_code: str | None

    def formatted_query(self) -> str:
        return ", ".join(
            part.strip()
            for part in [self.address, self.city, self.state, self.zip_code]
            if part and part.strip()
        )


@dataclass(frozen=True, slots=True)
class ProviderGeocodeResult:
    provider: GeocodeProvider
    latitude: float | None
    longitude: float | None
    formatted_address: str | None
    accuracy_type: str | None
    accuracy_score: float | None
    partial_match: bool = False
    confidence: GeocodeConfidence = GeocodeConfidence.NONE
    error: str | None = None

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    @property
    def is_usable(self) -> bool:
        return self.has_coordinates and self.confidence in {
            GeocodeConfidence.HIGH,
            GeocodeConfidence.MEDIUM,
        }


@dataclass(frozen=True, slots=True)
class GeocodeAttempt:
    provider: GeocodeProvider
    status: str
    confidence: GeocodeConfidence
    accuracy_type: str | None = None
    accuracy_score: float | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "status": self.status,
            "confidence": self.confidence.value,
            "accuracy_type": self.accuracy_type,
            "accuracy_score": self.accuracy_score,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class GeocodeResult:
    status: GeocodeStatus
    provider: GeocodeProvider | None = None
    latitude: float | None = None
    longitude: float | None = None
    formatted_address: str | None = None
    accuracy_type: str | None = None
    accuracy_score: float | None = None
    confidence: GeocodeConfidence = GeocodeConfidence.NONE
    fallback_used: bool = False
    fallback_reason: str | None = None
    message: str | None = None
    attempts: list[GeocodeAttempt] = field(default_factory=list)

    @property
    def is_accepted(self) -> bool:
        return (
            self.status == "accepted"
            and self.latitude is not None
            and self.longitude is not None
        )

    def as_audit_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "provider": self.provider,
            "confidence": self.confidence.value,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "formatted_address": self.formatted_address,
            "accuracy_type": self.accuracy_type,
            "accuracy_score": self.accuracy_score,
            "fallback_used": self.fallback_used,
            "fallback_reason": self.fallback_reason,
            "message": self.message,
            "attempts": [attempt.as_dict() for attempt in self.attempts],
        }


class ProjectGeocoder(Protocol):
    def geocode(self, address: GeocodeAddress) -> GeocodeResult:
        ...

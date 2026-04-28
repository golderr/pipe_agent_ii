from __future__ import annotations

from tcg_pipeline.db.models import GeocodeConfidence
from tcg_pipeline.geocoding.service import DefaultProjectGeocoder
from tcg_pipeline.geocoding.types import GeocodeAddress, ProviderGeocodeResult


def test_geocodio_high_confidence_skips_esri() -> None:
    geocodio = _FakeProvider(
        ProviderGeocodeResult(
            provider="geocodio",
            latitude=34.05,
            longitude=-118.25,
            formatted_address="123 W 1st St, Los Angeles, CA",
            accuracy_type="rooftop",
            accuracy_score=1.0,
            confidence=GeocodeConfidence.HIGH,
        )
    )
    esri = _FakeProvider(
        ProviderGeocodeResult(
            provider="esri",
            latitude=0,
            longitude=0,
            formatted_address=None,
            accuracy_type="PointAddress",
            accuracy_score=1.0,
            confidence=GeocodeConfidence.HIGH,
        )
    )

    result = DefaultProjectGeocoder(
        geocodio_client=geocodio,  # type: ignore[arg-type]
        esri_client=esri,  # type: ignore[arg-type]
    ).geocode(_address())

    assert result.status == "accepted"
    assert result.provider == "geocodio"
    assert result.confidence == GeocodeConfidence.HIGH
    assert len(geocodio.calls) == 1
    assert esri.calls == []


def test_geocodio_medium_confidence_uses_esri_fallback() -> None:
    geocodio = _FakeProvider(
        ProviderGeocodeResult(
            provider="geocodio",
            latitude=34.05,
            longitude=-118.25,
            formatted_address="123 W 1st St, Los Angeles, CA",
            accuracy_type="range_interpolation",
            accuracy_score=1.0,
            confidence=GeocodeConfidence.MEDIUM,
        )
    )
    esri = _FakeProvider(
        ProviderGeocodeResult(
            provider="esri",
            latitude=34.051,
            longitude=-118.251,
            formatted_address="123 W 1st St, Los Angeles, CA",
            accuracy_type="PointAddress",
            accuracy_score=0.96,
            confidence=GeocodeConfidence.HIGH,
        )
    )

    result = DefaultProjectGeocoder(
        geocodio_client=geocodio,  # type: ignore[arg-type]
        esri_client=esri,  # type: ignore[arg-type]
    ).geocode(_address())

    assert result.status == "accepted"
    assert result.provider == "esri"
    assert result.fallback_used is True
    assert result.fallback_reason == "geocodio_not_high_confidence"
    assert len(result.attempts) == 2


def test_esri_failure_keeps_usable_geocodio_result() -> None:
    geocodio = _FakeProvider(
        ProviderGeocodeResult(
            provider="geocodio",
            latitude=34.05,
            longitude=-118.25,
            formatted_address="123 W 1st St, Los Angeles, CA",
            accuracy_type="range_interpolation",
            accuracy_score=1.0,
            confidence=GeocodeConfidence.MEDIUM,
        )
    )
    esri = _FakeProvider(
        ProviderGeocodeResult(
            provider="esri",
            latitude=None,
            longitude=None,
            formatted_address=None,
            accuracy_type=None,
            accuracy_score=None,
            confidence=GeocodeConfidence.NONE,
            error="Esri returned no result.",
        )
    )

    result = DefaultProjectGeocoder(
        geocodio_client=geocodio,  # type: ignore[arg-type]
        esri_client=esri,  # type: ignore[arg-type]
    ).geocode(_address())

    assert result.status == "accepted"
    assert result.provider == "geocodio"
    assert result.confidence == GeocodeConfidence.MEDIUM
    assert result.fallback_used is True
    assert result.fallback_reason == "geocodio_not_high_confidence; esri_not_better"


def test_missing_provider_keys_skip_geocoding() -> None:
    result = DefaultProjectGeocoder(geocodio_client=None, esri_client=None).geocode(_address())

    assert result.status == "skipped"
    assert result.provider is None
    assert result.confidence == GeocodeConfidence.NONE
    assert result.message == "Geocoding API keys are not configured."


class _FakeProvider:
    def __init__(self, result: ProviderGeocodeResult) -> None:
        self.result = result
        self.calls: list[GeocodeAddress] = []

    def geocode(self, address: GeocodeAddress) -> ProviderGeocodeResult:
        self.calls.append(address)
        return self.result


def _address() -> GeocodeAddress:
    return GeocodeAddress(
        address="123 W 1st St",
        city="Los Angeles",
        state="CA",
        zip_code="90012",
    )

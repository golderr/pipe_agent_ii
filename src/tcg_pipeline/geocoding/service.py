from __future__ import annotations

from tcg_pipeline.db.models import GeocodeConfidence
from tcg_pipeline.geocoding.esri import EsriGeocodeClient
from tcg_pipeline.geocoding.geocodio import GeocodioClient
from tcg_pipeline.geocoding.types import (
    GeocodeAddress,
    GeocodeAttempt,
    GeocodeResult,
    GeocodeStatus,
    ProjectGeocoder,
    ProviderGeocodeResult,
)
from tcg_pipeline.settings import Settings


class DefaultProjectGeocoder:
    """Project geocoder that tries Geocodio first, then Esri for non-exact matches."""

    def __init__(
        self,
        *,
        geocodio_client: GeocodioClient | None,
        esri_client: EsriGeocodeClient | None,
    ) -> None:
        self._geocodio_client = geocodio_client
        self._esri_client = esri_client

    def geocode(self, address: GeocodeAddress) -> GeocodeResult:
        attempts: list[GeocodeAttempt] = []
        geocodio_result: ProviderGeocodeResult | None = None
        fallback_reason = "geocodio_not_configured"

        if self._geocodio_client is not None:
            geocodio_result = self._geocodio_client.geocode(address)
            attempts.append(_attempt_from_result(geocodio_result))
            if geocodio_result.confidence == GeocodeConfidence.HIGH:
                return _accepted_result(geocodio_result, attempts=attempts)
            fallback_reason = _fallback_reason(geocodio_result)

        if self._esri_client is not None:
            esri_result = self._esri_client.geocode(address)
            attempts.append(_attempt_from_result(esri_result))
            if esri_result.is_usable:
                return _accepted_result(
                    esri_result,
                    attempts=attempts,
                    fallback_used=True,
                    fallback_reason=fallback_reason,
                )

            if geocodio_result is not None and geocodio_result.is_usable:
                return _accepted_result(
                    geocodio_result,
                    attempts=attempts,
                    fallback_used=True,
                    fallback_reason=f"{fallback_reason}; esri_not_better",
                )
            return _not_accepted_result(
                attempts=attempts,
                status="low_confidence" if esri_result.has_coordinates else "failed",
                message=esri_result.error or "No reliable Geocodio or Esri geocode result.",
                fallback_used=True,
                fallback_reason=fallback_reason,
            )

        if geocodio_result is not None and geocodio_result.is_usable:
            return _accepted_result(
                geocodio_result,
                attempts=attempts,
                fallback_reason="esri_not_configured",
            )

        if not attempts:
            return _not_accepted_result(
                attempts=[],
                status="skipped",
                message="Geocoding API keys are not configured.",
                fallback_reason="geocoding_not_configured",
            )

        return _not_accepted_result(
            attempts=attempts,
            status="low_confidence"
            if geocodio_result and geocodio_result.has_coordinates
            else "failed",
            message=geocodio_result.error if geocodio_result else "No reliable geocode result.",
            fallback_reason=fallback_reason,
        )


def geocoder_from_settings(settings: Settings) -> ProjectGeocoder:
    geocodio_key = _clean(settings.geocodio_api_key)
    esri_key = _clean(settings.esri_api_key)
    return DefaultProjectGeocoder(
        geocodio_client=GeocodioClient(
            api_key=geocodio_key,
            base_url=settings.geocodio_base_url,
            timeout_seconds=settings.geocoding_timeout_seconds,
        )
        if geocodio_key
        else None,
        esri_client=EsriGeocodeClient(
            api_key=esri_key,
            base_url=settings.esri_geocode_base_url,
            timeout_seconds=settings.geocoding_timeout_seconds,
        )
        if esri_key
        else None,
    )


def _accepted_result(
    result: ProviderGeocodeResult,
    *,
    attempts: list[GeocodeAttempt],
    fallback_used: bool = False,
    fallback_reason: str | None = None,
) -> GeocodeResult:
    return GeocodeResult(
        status="accepted",
        provider=result.provider,
        latitude=result.latitude,
        longitude=result.longitude,
        formatted_address=result.formatted_address,
        accuracy_type=result.accuracy_type,
        accuracy_score=result.accuracy_score,
        confidence=result.confidence,
        fallback_used=fallback_used,
        fallback_reason=fallback_reason,
        attempts=attempts,
    )


def _not_accepted_result(
    *,
    attempts: list[GeocodeAttempt],
    status: GeocodeStatus,
    message: str | None,
    fallback_used: bool = False,
    fallback_reason: str | None = None,
) -> GeocodeResult:
    return GeocodeResult(
        status=status,
        message=message,
        fallback_used=fallback_used,
        fallback_reason=fallback_reason,
        attempts=attempts,
    )


def _attempt_from_result(result: ProviderGeocodeResult) -> GeocodeAttempt:
    return GeocodeAttempt(
        provider=result.provider,
        status="error"
        if result.error
        else "usable"
        if result.is_usable
        else "low_confidence"
        if result.has_coordinates
        else "no_result",
        confidence=result.confidence,
        accuracy_type=result.accuracy_type,
        accuracy_score=result.accuracy_score,
        error=result.error,
    )


def _fallback_reason(result: ProviderGeocodeResult) -> str:
    if result.error:
        return "geocodio_error"
    if not result.has_coordinates:
        return "geocodio_no_result"
    if result.confidence != GeocodeConfidence.HIGH:
        return "geocodio_not_high_confidence"
    return "geocodio_requires_esri_fallback"


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None

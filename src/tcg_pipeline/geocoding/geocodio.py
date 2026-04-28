from __future__ import annotations

import time
from typing import Any

import httpx

from tcg_pipeline.db.models import GeocodeConfidence
from tcg_pipeline.geocoding.types import GeocodeAddress, ProviderGeocodeResult

HIGH_CONFIDENCE_TYPES = {"rooftop", "nearest_rooftop_match"}
MEDIUM_CONFIDENCE_TYPES = {"range_interpolation"}


class GeocodioClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.geocod.io/v1.9",
        timeout_seconds: float = 8.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def geocode(self, address: GeocodeAddress) -> ProviderGeocodeResult:
        payload = [address.formatted_query()]
        url = f"{self._base_url}/geocode"
        try:
            response = self._post_with_retries(url, payload)
            if not response.is_success:
                return ProviderGeocodeResult(
                    provider="geocodio",
                    latitude=None,
                    longitude=None,
                    formatted_address=None,
                    accuracy_type=None,
                    accuracy_score=None,
                    error=f"Geocodio error {response.status_code}.",
                )
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return ProviderGeocodeResult(
                provider="geocodio",
                latitude=None,
                longitude=None,
                formatted_address=None,
                accuracy_type=None,
                accuracy_score=None,
                error=_safe_error("Geocodio request failed.", exc),
            )

        result = _best_result(data)
        if result is None:
            return ProviderGeocodeResult(
                provider="geocodio",
                latitude=None,
                longitude=None,
                formatted_address=None,
                accuracy_type=None,
                accuracy_score=None,
                error="Geocodio returned no result.",
            )

        location = _mapping(result.get("location"))
        lat = _float(location.get("lat"))
        lng = _float(location.get("lng"))
        accuracy_type = _text(result.get("accuracy_type"))
        accuracy_score = _float(result.get("accuracy"))
        partial_match = bool(result.get("partial_match"))
        return ProviderGeocodeResult(
            provider="geocodio",
            latitude=lat,
            longitude=lng,
            formatted_address=_text(result.get("formatted_address")),
            accuracy_type=accuracy_type,
            accuracy_score=accuracy_score,
            partial_match=partial_match,
            confidence=_confidence(accuracy_type, accuracy_score, partial_match, lat, lng),
        )

    def _post_with_retries(self, url: str, payload: object, retries: int = 2) -> httpx.Response:
        last_error: httpx.HTTPError | None = None
        for attempt in range(retries + 1):
            try:
                response = httpx.post(
                    url,
                    params={"api_key": self._api_key},
                    json=payload,
                    timeout=self._timeout_seconds,
                )
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                if response.status_code not in {429} and response.status_code < 500:
                    return response
                last_error = httpx.HTTPStatusError(
                    f"Retryable Geocodio status {response.status_code}",
                    request=response.request,
                    response=response,
                )

            if attempt < retries:
                time.sleep(1 if attempt == 0 else 3)

        if last_error is not None:
            raise last_error
        raise RuntimeError("Geocodio request failed.")


def _best_result(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, list):
        item = payload[0] if payload else None
    elif isinstance(payload, dict) and isinstance(payload.get("results"), list):
        item = payload
    elif isinstance(payload, dict) and isinstance(payload.get("results"), dict):
        values = list(payload["results"].values())
        item = values[0] if values else None
    else:
        item = None

    item_mapping = _mapping(item)
    response = _mapping(item_mapping.get("response"))
    results = response.get("results") if response else item_mapping.get("results")
    if not isinstance(results, list) or not results:
        return None
    first = results[0]
    return first if isinstance(first, dict) else None


def _confidence(
    accuracy_type: str | None,
    accuracy_score: float | None,
    partial_match: bool,
    lat: float | None,
    lng: float | None,
) -> GeocodeConfidence:
    if lat is None or lng is None or accuracy_score is None:
        return GeocodeConfidence.NONE
    if partial_match:
        return GeocodeConfidence.LOW

    normalized_type = (accuracy_type or "").strip().lower()
    if accuracy_score >= 0.999 and normalized_type in HIGH_CONFIDENCE_TYPES:
        return GeocodeConfidence.HIGH
    if accuracy_score >= 0.999 and normalized_type in MEDIUM_CONFIDENCE_TYPES:
        return GeocodeConfidence.MEDIUM
    if accuracy_score >= 0.8 and normalized_type in HIGH_CONFIDENCE_TYPES | MEDIUM_CONFIDENCE_TYPES:
        return GeocodeConfidence.LOW
    return GeocodeConfidence.NONE


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _safe_error(prefix: str, error: Exception) -> str:
    return f"{prefix} ({error.__class__.__name__})"

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from tcg_pipeline.db.models import GeocodeConfidence
from tcg_pipeline.geocoding.types import GeocodeAddress, ProviderGeocodeResult

HIGH_CONFIDENCE_TYPES = {"pointaddress"}
MEDIUM_CONFIDENCE_TYPES = {"streetaddress"}


class EsriGeocodeClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://geocode-api.arcgis.com/arcgis/rest/services/World/GeocodeServer",
        timeout_seconds: float = 8.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def geocode(self, address: GeocodeAddress) -> ProviderGeocodeResult:
        url = f"{self._base_url}/geocodeAddresses"
        params = {
            "f": "json",
            "token": self._api_key,
            "addresses": json.dumps(
                {
                    "records": [
                        {
                            "attributes": {
                                "OBJECTID": 1,
                                "Address": address.address,
                                "City": address.city or "",
                                "Region": address.state or "",
                                "Postal": address.zip_code or "",
                            }
                        }
                    ]
                }
            ),
            "category": "Point Address,Street Address",
        }
        try:
            response = self._post_with_retries(url, params)
            if not response.is_success:
                return ProviderGeocodeResult(
                    provider="esri",
                    latitude=None,
                    longitude=None,
                    formatted_address=None,
                    accuracy_type=None,
                    accuracy_score=None,
                    error=f"Esri geocode error {response.status_code}.",
                )
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return ProviderGeocodeResult(
                provider="esri",
                latitude=None,
                longitude=None,
                formatted_address=None,
                accuracy_type=None,
                accuracy_score=None,
                error=f"Esri geocode request failed. ({exc.__class__.__name__})",
            )

        location = _best_location(payload)
        if location is None:
            return ProviderGeocodeResult(
                provider="esri",
                latitude=None,
                longitude=None,
                formatted_address=None,
                accuracy_type=None,
                accuracy_score=None,
                error="Esri returned no result.",
            )

        attributes = _mapping(location.get("attributes"))
        coordinates = _mapping(location.get("location"))
        lat = _float(coordinates.get("y"))
        lng = _float(coordinates.get("x"))
        raw_score = _float(attributes.get("Score"))
        accuracy_score = raw_score / 100 if raw_score is not None else None
        accuracy_type = _text(attributes.get("Addr_type"))
        return ProviderGeocodeResult(
            provider="esri",
            latitude=lat,
            longitude=lng,
            formatted_address=_text(attributes.get("Match_addr")),
            accuracy_type=accuracy_type,
            accuracy_score=accuracy_score,
            partial_match=accuracy_score is not None and accuracy_score < 0.8,
            confidence=_confidence(accuracy_type, raw_score, lat, lng),
        )

    def _post_with_retries(
        self,
        url: str,
        params: dict[str, str],
        retries: int = 2,
    ) -> httpx.Response:
        last_error: httpx.HTTPError | None = None
        for attempt in range(retries + 1):
            try:
                response = httpx.post(
                    url,
                    data=params,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=self._timeout_seconds,
                )
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                if response.status_code not in {429} and response.status_code < 500:
                    return response
                last_error = httpx.HTTPStatusError(
                    f"Retryable Esri status {response.status_code}",
                    request=response.request,
                    response=response,
                )

            if attempt < retries:
                time.sleep(1 if attempt == 0 else 3)

        if last_error is not None:
            raise last_error
        raise RuntimeError("Esri geocode request failed.")


def _best_location(payload: Any) -> dict[str, Any] | None:
    locations = _mapping(payload).get("locations")
    if not isinstance(locations, list) or not locations:
        return None
    first = locations[0]
    return first if isinstance(first, dict) else None


def _confidence(
    accuracy_type: str | None,
    score: float | None,
    lat: float | None,
    lng: float | None,
) -> GeocodeConfidence:
    if lat is None or lng is None or score is None:
        return GeocodeConfidence.NONE

    normalized_type = (accuracy_type or "").strip().lower()
    if score >= 95 and normalized_type in HIGH_CONFIDENCE_TYPES:
        return GeocodeConfidence.HIGH
    if score >= 90 and normalized_type in HIGH_CONFIDENCE_TYPES | MEDIUM_CONFIDENCE_TYPES:
        return GeocodeConfidence.MEDIUM
    if score >= 80 and normalized_type in HIGH_CONFIDENCE_TYPES | MEDIUM_CONFIDENCE_TYPES:
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

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from rapidfuzz import fuzz

from tcg_pipeline.db.models import ProductType, Project
from tcg_pipeline.developer.registry import normalize_developer_name

# Identifier matches are Layer 1 hard signals, not weighted soft-score components.
MATCH_SIGNAL_WEIGHTS: dict[str, float] = {
    "geographic": 0.30,
    "address": 0.25,
    "developer": 0.20,
    "name": 0.10,
    "units": 0.10,
    "product_type": 0.05,
}

SIGNAL_LABELS: dict[str, str] = {
    "identifier": "Identifier",
    "geographic": "Location",
    "address": "Address",
    "developer": "Developer",
    "name": "Name",
    "units": "Units",
    "product_type": "Product type",
}

PRODUCT_TYPE_VALUES: dict[str, str] = {
    "apartment": ProductType.APARTMENT.value,
    "apartments": ProductType.APARTMENT.value,
    "condo": ProductType.CONDO.value,
    "condos": ProductType.CONDO.value,
    "townhome": ProductType.TOWNHOME.value,
    "townhomes": ProductType.TOWNHOME.value,
    "single_family": ProductType.SINGLE_FAMILY.value,
    "single-family": ProductType.SINGLE_FAMILY.value,
    "single family": ProductType.SINGLE_FAMILY.value,
    "micro_co_living": ProductType.MICRO_CO_LIVING.value,
    "micro/co-living": ProductType.MICRO_CO_LIVING.value,
    "other": ProductType.OTHER.value,
}


@dataclass(frozen=True, slots=True)
class MatchSignal:
    score: float
    contributed: bool
    searched: bool
    label: str
    detail: str | None = None
    weight: float = 0.0

    def as_payload(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "contributed": self.contributed,
            "searched": self.searched,
            "label": self.label,
            "detail": self.detail,
            "weight": self.weight,
        }


def build_match_signals(
    *,
    subject_project_name: str | None,
    subject_canonical_address: str | None,
    subject_developer: str | None,
    subject_total_units: int | None,
    subject_product_type: str | None,
    subject_lat: float | None,
    subject_lng: float | None,
    project: Project,
    address_similarity: float | None = None,
    name_similarity: float | None = None,
    identifier_detail: str | None = None,
) -> dict[str, MatchSignal]:
    distance_meters = distance_between_points(
        subject_lat,
        subject_lng,
        project.lat,
        project.lng,
    )
    signals = {
        "identifier": _identifier_signal(identifier_detail),
        "geographic": _geographic_signal(
            subject_lat=subject_lat,
            subject_lng=subject_lng,
            distance_meters=distance_meters,
        ),
        "address": _text_signal(
            "address",
            subject_canonical_address,
            project.canonical_address,
            score=address_similarity,
        ),
        "developer": _developer_signal(subject_developer, project.developer),
        "name": _text_signal(
            "name",
            subject_project_name,
            project.project_name,
            score=name_similarity,
        ),
        "units": _units_signal(subject_total_units, project.total_units),
        "product_type": _product_type_signal(subject_product_type, project.product_type),
    }
    return signals


def weighted_match_likelihood(
    signals: dict[str, MatchSignal],
    *,
    weights: dict[str, float] | None = None,
) -> float:
    active_weights = weights or MATCH_SIGNAL_WEIGHTS
    denominator = sum(
        weight
        for signal_name, weight in active_weights.items()
        if signals.get(signal_name) is not None and signals[signal_name].searched
    )
    if denominator <= 0:
        return 0.0
    numerator = sum(
        active_weights[signal_name] * signals[signal_name].score
        for signal_name in active_weights
        if signals.get(signal_name) is not None and signals[signal_name].searched
    )
    return clamp_score(numerator / denominator)


def distance_between_points(
    lat1: float | None,
    lng1: float | None,
    lat2: float | None,
    lng2: float | None,
) -> float | None:
    if None in {lat1, lng1, lat2, lng2}:
        return None
    assert lat1 is not None
    assert lng1 is not None
    assert lat2 is not None
    assert lng2 is not None
    radius_meters = 6_371_000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lng2 - lng1)
    haversine = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return radius_meters * 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))


def geographic_proximity_score(
    distance_meters: float | None,
    *,
    max_meters: float = 1_000.0,
) -> float:
    if distance_meters is None:
        return 0.0
    if distance_meters <= 0:
        return 1.0
    if distance_meters >= max_meters:
        return 0.0
    return _normalized_exponential_falloff(distance_meters / max_meters)


def unit_count_proximity_score(
    subject_units: int | None,
    project_units: int | None,
    *,
    exact_band: float = 0.05,
    max_difference: float = 0.50,
) -> float:
    if subject_units is None or project_units is None:
        return 0.0
    if subject_units == project_units:
        return 1.0
    denominator = max(abs(subject_units), abs(project_units))
    if denominator == 0:
        return 1.0
    relative_difference = abs(subject_units - project_units) / denominator
    if relative_difference <= exact_band:
        return 1.0
    if relative_difference >= max_difference:
        return 0.0
    scaled = (relative_difference - exact_band) / (max_difference - exact_band)
    return _normalized_exponential_falloff(scaled)


def developer_match_score(
    subject_developer: str | None,
    project_developer: str | None,
) -> float:
    subject = normalize_developer_name(subject_developer)
    project = normalize_developer_name(project_developer)
    if subject is None or project is None:
        return 0.0
    if subject == project:
        return 1.0
    ratio = fuzz.token_set_ratio(subject, project) / 100.0
    return 0.7 if ratio >= 0.85 else 0.0


def product_type_match_score(
    subject_product_type: str | None,
    project_product_type: ProductType | str | None,
) -> float:
    subject = normalize_product_type(subject_product_type)
    project = normalize_product_type(project_product_type)
    if subject is None or project is None:
        return 0.0
    return 1.0 if subject == project else 0.0


def normalize_product_type(value: ProductType | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, ProductType):
        return None if value == ProductType.UNKNOWN else value.value
    text = str(value).strip()
    if not text:
        return None
    try:
        product_type = ProductType(text)
    except ValueError:
        normalized = " ".join(text.strip().lower().replace("-", " ").split())
        normalized = normalized.replace(" ", "_")
        product_type_value = PRODUCT_TYPE_VALUES.get(normalized)
        return product_type_value if product_type_value != ProductType.UNKNOWN.value else None
    return None if product_type == ProductType.UNKNOWN else product_type.value


def text_similarity_score(value: str | None, comparison: str | None) -> float:
    if not value or not comparison:
        return 0.0
    return clamp_score(fuzz.token_set_ratio(value, comparison) / 100.0)


def clamp_score(value: float | None) -> float:
    if value is None:
        return 0.0
    if math.isnan(value):
        return 0.0
    return max(0.0, min(1.0, float(value)))


def _identifier_signal(identifier_detail: str | None) -> MatchSignal:
    searched = identifier_detail is not None
    return MatchSignal(
        score=1.0 if searched else 0.0,
        contributed=searched,
        searched=searched,
        label=SIGNAL_LABELS["identifier"],
        detail=identifier_detail,
        weight=0.0,
    )


def _geographic_signal(
    *,
    subject_lat: float | None,
    subject_lng: float | None,
    distance_meters: float | None,
) -> MatchSignal:
    searched = subject_lat is not None and subject_lng is not None
    score = geographic_proximity_score(distance_meters)
    detail = (
        f"{round(distance_meters)}m away"
        if distance_meters is not None
        else "Subject or candidate coordinates missing"
    )
    return MatchSignal(
        score=score,
        contributed=searched and score > 0,
        searched=searched,
        label=SIGNAL_LABELS["geographic"],
        detail=detail,
        weight=MATCH_SIGNAL_WEIGHTS["geographic"],
    )


def _text_signal(
    signal_name: str,
    subject_value: str | None,
    project_value: str | None,
    *,
    score: float | None = None,
) -> MatchSignal:
    searched = bool(subject_value and str(subject_value).strip())
    similarity = clamp_score(score) if score is not None else text_similarity_score(
        subject_value,
        project_value,
    )
    return MatchSignal(
        score=similarity,
        contributed=searched and similarity > 0,
        searched=searched,
        label=SIGNAL_LABELS[signal_name],
        detail=f"{round(similarity * 100)}% similar" if searched else "Subject value missing",
        weight=MATCH_SIGNAL_WEIGHTS[signal_name],
    )


def _developer_signal(
    subject_developer: str | None,
    project_developer: str | None,
) -> MatchSignal:
    searched = bool(subject_developer and str(subject_developer).strip())
    score = developer_match_score(subject_developer, project_developer)
    return MatchSignal(
        score=score,
        contributed=searched and score > 0,
        searched=searched,
        label=SIGNAL_LABELS["developer"],
        detail="canonical/fuzzy match" if score > 0 else "no match",
        weight=MATCH_SIGNAL_WEIGHTS["developer"],
    )


def _units_signal(subject_units: int | None, project_units: int | None) -> MatchSignal:
    searched = subject_units is not None
    score = unit_count_proximity_score(subject_units, project_units)
    return MatchSignal(
        score=score,
        contributed=searched and score > 0,
        searched=searched,
        label=SIGNAL_LABELS["units"],
        detail=(
            f"subject {subject_units}, candidate {project_units}"
            if searched
            else "Subject value missing"
        ),
        weight=MATCH_SIGNAL_WEIGHTS["units"],
    )


def _product_type_signal(
    subject_product_type: str | None,
    project_product_type: ProductType | str | None,
) -> MatchSignal:
    searched = normalize_product_type(subject_product_type) is not None
    score = product_type_match_score(subject_product_type, project_product_type)
    return MatchSignal(
        score=score,
        contributed=searched and score > 0,
        searched=searched,
        label=SIGNAL_LABELS["product_type"],
        detail="exact match" if score > 0 else "no match",
        weight=MATCH_SIGNAL_WEIGHTS["product_type"],
    )


def _normalized_exponential_falloff(position: float) -> float:
    position = clamp_score(position)
    floor = math.exp(-3.0)
    return clamp_score((math.exp(-3.0 * position) - floor) / (1.0 - floor))

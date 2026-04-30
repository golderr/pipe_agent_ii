from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from tcg_pipeline.db.models import (
    DeveloperRegistry,
    IdentifierType,
    NewsArticle,
    NewsMatchStatus,
    NewsProjectReference,
    PipelineStatus,
    Project,
    ProjectIdentifier,
)
from tcg_pipeline.developer.registry import (
    canonicalize_developer_name,
    normalize_developer_name,
)
from tcg_pipeline.matching.normalizer import normalize_address

IDENTIFIER_MATCH = "identifier"
FORCED_PROJECT_MATCH = "forced_project_id"
ADDRESS_COMPOSITE_MATCH = "address_composite"
FINGERPRINT_MATCH = "developer_neighborhood_unit_fingerprint"
PROJECT_NAME_MATCH = "project_name_fuzzy"
NEW_CANDIDATE_MATCH = "new_candidate"
DISCARDED_MATCH = "discarded"

CONFIRMED_THRESHOLD = 0.85
POSSIBLE_THRESHOLD = 0.65
FINGERPRINT_POSSIBLE_THRESHOLD = 0.80
PROJECT_NAME_RATIO_THRESHOLD = 85.0
DEVELOPER_CONFIDENT_MATCH_TYPES = {"exact_canonical", "exact_alias", "fuzzy_auto"}
DELETED_PROJECT_STATUSES = {
    PipelineStatus.DELETE_DUPLICATE,
    PipelineStatus.DELETE_NOT_RESIDENTIAL,
    PipelineStatus.DELETE_OUTSIDE_MARKET_AREA,
}


@dataclass(frozen=True, slots=True)
class ValidatedRegistryHints:
    developer_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    ignored_developer_id: uuid.UUID | None = None
    ignored_project_id: uuid.UUID | None = None

    @property
    def diagnostic(self) -> dict[str, str]:
        diagnostic: dict[str, str] = {}
        if self.ignored_developer_id is not None:
            diagnostic["ignored_registry_developer_id"] = str(self.ignored_developer_id)
        if self.ignored_project_id is not None:
            diagnostic["ignored_registry_project_id"] = str(self.ignored_project_id)
        return diagnostic


@dataclass(frozen=True, slots=True)
class NewsMatchCandidate:
    project_id: uuid.UUID
    score: float
    reasons: list[str] = field(default_factory=list)
    project_name_ratio: float | None = None

    def as_payload(self) -> dict[str, Any]:
        return {
            "project_id": str(self.project_id),
            "score": round(self.score, 4),
            "reasons": list(self.reasons),
            "project_name_ratio": self.project_name_ratio,
        }


@dataclass(frozen=True, slots=True)
class NewsMatchResult:
    status: NewsMatchStatus
    match_type: str
    confidence: float
    project_id: uuid.UUID | None = None
    candidate_project_ids: list[uuid.UUID] = field(default_factory=list)
    candidates: list[NewsMatchCandidate] = field(default_factory=list)
    reason: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def candidates_payload(self) -> dict[str, Any]:
        payload = {
            "match_type": self.match_type,
            "candidate_project_ids": [
                str(project_id) for project_id in self.candidate_project_ids
            ],
            "candidates": [candidate.as_payload() for candidate in self.candidates],
        }
        if self.diagnostics:
            payload["diagnostics"] = self.diagnostics
        return payload


def match_news_reference(
    session: Session,
    *,
    article: NewsArticle,
    reference: NewsProjectReference,
    force_project_id: uuid.UUID | None = None,
) -> NewsMatchResult:
    hints = validate_reference_registry_hints(
        session,
        article=article,
        reference=reference,
    )
    forced = _match_forced_project(
        session,
        force_project_id=force_project_id,
        article=article,
    )
    if forced is not None:
        return forced

    identifier_match = _match_identifiers(session, reference=reference)
    if identifier_match is not None:
        return _with_diagnostics(identifier_match, hints.diagnostic)

    if hints.project_id is not None:
        return _with_diagnostics(
            NewsMatchResult(
                status=NewsMatchStatus.CONFIRMED,
                match_type="registry_project_id",
                confidence=0.96,
                project_id=hints.project_id,
                reason="LLM project registry hint validated against a live project.",
            ),
            hints.diagnostic,
        )

    address_match = _match_address_composite(
        session,
        article=article,
        reference=reference,
        hints=hints,
    )
    if address_match is not None:
        return _with_diagnostics(address_match, hints.diagnostic)

    fingerprint_match = _match_developer_fingerprint(
        session,
        article=article,
        reference=reference,
        hints=hints,
    )
    if fingerprint_match is not None:
        return _with_diagnostics(fingerprint_match, hints.diagnostic)

    name_match = _match_project_name_fuzzy(
        session,
        article=article,
        reference=reference,
    )
    if name_match is not None:
        return _with_diagnostics(name_match, hints.diagnostic)

    if is_new_candidate(reference):
        return _with_diagnostics(
            NewsMatchResult(
                status=NewsMatchStatus.NEW_CANDIDATE,
                match_type=NEW_CANDIDATE_MATCH,
                confidence=_candidate_confidence_score(reference.candidate_confidence),
                reason=(
                    "No existing project matched, but the reference has strong "
                    "candidate signals."
                ),
            ),
            hints.diagnostic,
        )
    return _with_diagnostics(
        NewsMatchResult(
            status=NewsMatchStatus.DISCARDED,
            match_type=DISCARDED_MATCH,
            confidence=0.0,
            reason="No existing project matched and candidate signals were too weak.",
        ),
        hints.diagnostic,
    )


def validate_reference_registry_hints(
    session: Session,
    *,
    article: NewsArticle,
    reference: NewsProjectReference,
) -> ValidatedRegistryHints:
    raw_reference = _raw_reference_payload(reference)
    developer_id = _uuid_or_none(raw_reference.get("registry_developer_id"))
    project_id = _uuid_or_none(raw_reference.get("registry_project_id"))

    valid_developer_id: uuid.UUID | None = None
    ignored_developer_id: uuid.UUID | None = None
    if developer_id is not None:
        if session.get(DeveloperRegistry, developer_id) is None:
            ignored_developer_id = developer_id
        else:
            valid_developer_id = developer_id

    valid_project_id: uuid.UUID | None = None
    ignored_project_id: uuid.UUID | None = None
    if project_id is not None:
        project = session.get(Project, project_id)
        if project is None or _project_deleted(project) or not _project_in_article_scope(
            project,
            article,
        ):
            ignored_project_id = project_id
        else:
            valid_project_id = project_id

    return ValidatedRegistryHints(
        developer_id=valid_developer_id,
        project_id=valid_project_id,
        ignored_developer_id=ignored_developer_id,
        ignored_project_id=ignored_project_id,
    )


def is_new_candidate(reference: NewsProjectReference) -> bool:
    identifiers = _candidate_identifiers(reference)
    has_identifiers = any(identifiers.values())
    has_strong_signals = bool(
        _clean_text(reference.candidate_address)
        or has_identifiers
        or (_clean_text(reference.candidate_developer) and reference.candidate_unit_total)
    )
    confidence_ok = reference.candidate_confidence in {"high", "medium"}
    unit_count_ok = (
        reference.candidate_unit_total is None or reference.candidate_unit_total >= 10
    )
    return has_strong_signals and confidence_ok and unit_count_ok


def canonical_address_for_reference(
    article: NewsArticle,
    reference: NewsProjectReference,
) -> str | None:
    raw_address = _clean_text(reference.candidate_address)
    if raw_address is None:
        return None
    source = article.source
    jurisdiction = source.jurisdiction
    city = _source_default_city(article)
    state = jurisdiction.state if jurisdiction is not None and jurisdiction.state != "NA" else None
    postal_code = None
    normalized = normalize_address(
        raw_address,
        city=city,
        state=state,
        postal_code=postal_code,
        market=source.market.slug if source.market is not None else None,
    )
    return normalized.canonical_address or normalized.canonical_street_line


def _match_forced_project(
    session: Session,
    *,
    force_project_id: uuid.UUID | None,
    article: NewsArticle,
) -> NewsMatchResult | None:
    if force_project_id is None:
        return None
    project = session.get(Project, force_project_id)
    if project is None or _project_deleted(project) or not _project_in_article_scope(
        project,
        article,
    ):
        return None
    return NewsMatchResult(
        status=NewsMatchStatus.CONFIRMED,
        match_type=FORCED_PROJECT_MATCH,
        confidence=0.99,
        project_id=project.id,
        reason="Paste-a-link force_project_id points to a live project.",
    )


def _match_identifiers(
    session: Session,
    *,
    reference: NewsProjectReference,
) -> NewsMatchResult | None:
    matched_project_ids: set[uuid.UUID] = set()
    matched_identifier: tuple[str, str] | None = None
    for identifier_type_name, values in _candidate_identifiers(reference).items():
        identifier_type = _coerce_identifier_type(identifier_type_name)
        if identifier_type is None:
            continue
        cleaned_values = sorted({_clean_identifier(value) for value in values if value})
        cleaned_values = [value for value in cleaned_values if value]
        if not cleaned_values:
            continue
        rows = session.execute(
            select(ProjectIdentifier.project_id, ProjectIdentifier.value).where(
                ProjectIdentifier.identifier_type == identifier_type,
                ProjectIdentifier.value.in_(cleaned_values),
            )
        ).all()
        if not rows:
            continue
        matched_identifier = (identifier_type.value, rows[0].value)
        matched_project_ids.update(row.project_id for row in rows)

    if len(matched_project_ids) == 1:
        project_id = next(iter(matched_project_ids))
        return NewsMatchResult(
            status=NewsMatchStatus.CONFIRMED,
            match_type=IDENTIFIER_MATCH,
            confidence=0.97,
            project_id=project_id,
            reason=(
                f"Identifier matched {matched_identifier[0]}:{matched_identifier[1]}."
                if matched_identifier is not None
                else "Identifier matched exactly."
            ),
            candidates=[
                NewsMatchCandidate(
                    project_id=project_id,
                    score=0.97,
                    reasons=["identifier_match"],
                )
            ],
        )
    if len(matched_project_ids) > 1:
        candidates = [
            NewsMatchCandidate(
                project_id=project_id,
                score=0.70,
                reasons=["identifier_conflict"],
            )
            for project_id in sorted(matched_project_ids, key=str)
        ]
        return NewsMatchResult(
            status=NewsMatchStatus.POSSIBLE,
            match_type=IDENTIFIER_MATCH,
            confidence=0.70,
            candidate_project_ids=[candidate.project_id for candidate in candidates],
            candidates=candidates,
            reason="Identifiers matched multiple projects.",
        )
    return None


def _match_address_composite(
    session: Session,
    *,
    article: NewsArticle,
    reference: NewsProjectReference,
    hints: ValidatedRegistryHints,
) -> NewsMatchResult | None:
    canonical_address = canonical_address_for_reference(article, reference)
    if canonical_address is None:
        return None

    exact_matches = _load_address_matches(
        session,
        article=article,
        canonical_address=canonical_address,
    )
    address_match_type = "exact_address"
    address_score = 0.50
    matched_projects = exact_matches
    if not matched_projects and not _canonical_address_has_postal_code(canonical_address):
        matched_projects = _load_address_matches(
            session,
            article=article,
            canonical_address=canonical_address,
            allow_postal_code_suffix=True,
        )
        address_match_type = "zip_tolerant_address"
        address_score = 0.40

    candidates: list[NewsMatchCandidate] = []
    for project in matched_projects:
        score, reasons, name_ratio = _score_project_composite(
            session,
            project=project,
            reference=reference,
            hints=hints,
            base_score=address_score,
            base_reason=address_match_type,
        )
        candidates.append(
            NewsMatchCandidate(
                project_id=project.id,
                score=score,
                reasons=reasons,
                project_name_ratio=name_ratio,
            )
        )
    return _result_from_scored_candidates(
        candidates,
        match_type=ADDRESS_COMPOSITE_MATCH,
        confirmed_threshold=CONFIRMED_THRESHOLD,
        possible_threshold=POSSIBLE_THRESHOLD,
    )


def _match_developer_fingerprint(
    session: Session,
    *,
    article: NewsArticle,
    reference: NewsProjectReference,
    hints: ValidatedRegistryHints,
) -> NewsMatchResult | None:
    if _clean_text(reference.candidate_address):
        return None
    if not _clean_text(reference.candidate_developer):
        return None
    projects = _load_candidate_projects(session, article=article)
    candidates: list[NewsMatchCandidate] = []
    for project in projects:
        score = 0.0
        reasons: list[str] = []
        if _developer_matches(
            session,
            reference.candidate_developer,
            project.developer,
            reference_developer_id=hints.developer_id,
        ):
            score += 0.40
            reasons.append("developer_canonical")
        if _neighborhood_matches(reference.candidate_neighborhood, project):
            score += 0.20
            reasons.append("neighborhood")
        if _units_within_pct(reference.candidate_unit_total, project.total_units):
            score += 0.20
            reasons.append("unit_total_within_25pct")
        if _product_type_matches(reference.candidate_product_type, project):
            score += 0.05
            reasons.append("product_type")
        if _stories_within_one(_candidate_stories(reference), project.stories):
            score += 0.05
            reasons.append("stories_within_one")
        if _coordinates_within_meters(
            reference.candidate_lat,
            reference.candidate_lng,
            project.lat,
            project.lng,
            max_meters=75.0,
        ):
            score += 0.20
            reasons.append("coordinates_within_75m")
        if score > 0:
            candidates.append(
                NewsMatchCandidate(
                    project_id=project.id,
                    score=score,
                    reasons=reasons,
                )
            )

    return _result_from_scored_candidates(
        candidates,
        match_type=FINGERPRINT_MATCH,
        confirmed_threshold=1.01,
        possible_threshold=FINGERPRINT_POSSIBLE_THRESHOLD,
    )


def _match_project_name_fuzzy(
    session: Session,
    *,
    article: NewsArticle,
    reference: NewsProjectReference,
) -> NewsMatchResult | None:
    candidate_name = _clean_text(reference.candidate_name)
    if candidate_name is None:
        return None
    candidates: list[NewsMatchCandidate] = []
    for project in _load_candidate_projects(session, article=article):
        ratio = _project_name_ratio(candidate_name, project)
        if ratio is not None and ratio >= PROJECT_NAME_RATIO_THRESHOLD:
            candidates.append(
                NewsMatchCandidate(
                    project_id=project.id,
                    score=ratio / 100,
                    reasons=["project_name_fuzzy"],
                    project_name_ratio=ratio,
                )
            )
    if not candidates:
        return None
    candidates = sorted(
        candidates,
        key=lambda candidate: (-candidate.score, str(candidate.project_id)),
    )
    return NewsMatchResult(
        status=NewsMatchStatus.POSSIBLE,
        match_type=PROJECT_NAME_MATCH,
        confidence=candidates[0].score,
        candidate_project_ids=[candidate.project_id for candidate in candidates],
        candidates=candidates,
        reason="Project name fuzzy match requires review.",
    )


def _result_from_scored_candidates(
    candidates: list[NewsMatchCandidate],
    *,
    match_type: str,
    confirmed_threshold: float,
    possible_threshold: float,
) -> NewsMatchResult | None:
    if not candidates:
        return None
    ranked = sorted(
        candidates,
        key=lambda candidate: (-candidate.score, str(candidate.project_id)),
    )
    best = ranked[0]
    if best.score >= confirmed_threshold:
        same_score = [
            candidate for candidate in ranked if round(candidate.score, 6) == round(best.score, 6)
        ]
        if len(same_score) == 1:
            return NewsMatchResult(
                status=NewsMatchStatus.CONFIRMED,
                match_type=match_type,
                confidence=best.score,
                project_id=best.project_id,
                candidate_project_ids=[candidate.project_id for candidate in ranked],
                candidates=ranked,
                reason=f"{match_type} score {best.score:.2f} met confirmed threshold.",
            )
    if best.score >= possible_threshold:
        return NewsMatchResult(
            status=NewsMatchStatus.POSSIBLE,
            match_type=match_type,
            confidence=best.score,
            candidate_project_ids=[candidate.project_id for candidate in ranked],
            candidates=ranked,
            reason=f"{match_type} score {best.score:.2f} requires review.",
        )
    return None


def _score_project_composite(
    session: Session,
    *,
    project: Project,
    reference: NewsProjectReference,
    hints: ValidatedRegistryHints,
    base_score: float,
    base_reason: str,
) -> tuple[float, list[str], float | None]:
    score = base_score
    reasons = [base_reason]
    name_ratio = _project_name_ratio(reference.candidate_name, project)
    if name_ratio is not None and name_ratio >= PROJECT_NAME_RATIO_THRESHOLD:
        score += 0.15
        reasons.append("project_name_fuzzy")
    if _developer_matches(
        session,
        reference.candidate_developer,
        project.developer,
        reference_developer_id=hints.developer_id,
    ):
        score += 0.20
        reasons.append("developer_canonical")
    if _units_within_pct(reference.candidate_unit_total, project.total_units):
        score += 0.05
        reasons.append("unit_total_within_25pct")
    return score, reasons, name_ratio


def _load_address_matches(
    session: Session,
    *,
    article: NewsArticle,
    canonical_address: str,
    allow_postal_code_suffix: bool = False,
) -> list[Project]:
    address_filter = Project.canonical_address == canonical_address
    if allow_postal_code_suffix:
        address_filter = or_(
            address_filter,
            Project.canonical_address.like(f"{canonical_address} %"),
        )
    statement = select(Project).where(address_filter, _active_project_filter())
    statement = _scope_projects_to_article(statement, article)
    return session.execute(statement).scalars().all()


def _load_candidate_projects(
    session: Session,
    *,
    article: NewsArticle,
) -> list[Project]:
    statement = select(Project).where(_active_project_filter())
    statement = _scope_projects_to_article(statement, article)
    return session.execute(statement).scalars().all()


def _scope_projects_to_article(statement, article: NewsArticle):
    source_market_id = article.source.market_id
    source_market_slug = article.source.market.slug if article.source.market is not None else None
    if source_market_slug and source_market_slug != "unscoped":
        if source_market_id is not None:
            return statement.where(Project.market_id == source_market_id)
        return statement.where(Project.market == source_market_slug)
    return statement


def _active_project_filter():
    return Project.pipeline_status.notin_([status.value for status in DELETED_PROJECT_STATUSES])


def _project_in_article_scope(project: Project, article: NewsArticle) -> bool:
    source_market = article.source.market
    if source_market is None or source_market.slug == "unscoped":
        return True
    if article.source.market_id is not None:
        return project.market_id == article.source.market_id
    return project.market == source_market.slug


def _project_deleted(project: Project) -> bool:
    return project.pipeline_status in DELETED_PROJECT_STATUSES


def _project_name_ratio(candidate_name: str | None, project: Project) -> float | None:
    text = _clean_text(candidate_name)
    if text is None:
        return None
    names = [project.project_name, *list(project.previous_names or [])]
    scores = [
        fuzz.token_set_ratio(text, name)
        for name in names
        if _clean_text(name) is not None
    ]
    if not scores:
        return None
    return float(max(scores))


def _developer_matches(
    session: Session,
    reference_developer: str | None,
    project_developer: str | None,
    *,
    reference_developer_id: uuid.UUID | None,
) -> bool:
    reference_text = _clean_text(reference_developer)
    project_text = _clean_text(project_developer)
    if reference_text is None or project_text is None:
        return False
    reference_result = canonicalize_developer_name(
        session,
        reference_text,
        persist=False,
    )
    project_result = canonicalize_developer_name(
        session,
        project_text,
        persist=False,
    )
    if (
        reference_developer_id is not None
        and project_result.canonical_developer_id == reference_developer_id
    ):
        return True
    if (
        reference_result.canonical_developer_id is not None
        and project_result.canonical_developer_id is not None
    ):
        return reference_result.canonical_developer_id == project_result.canonical_developer_id
    if (
        reference_result.match_type in DEVELOPER_CONFIDENT_MATCH_TYPES
        and project_result.match_type in DEVELOPER_CONFIDENT_MATCH_TYPES
        and normalize_developer_name(reference_result.canonical_name)
        == normalize_developer_name(project_result.canonical_name)
    ):
        return True
    return normalize_developer_name(reference_text) == normalize_developer_name(project_text)


def _neighborhood_matches(candidate_neighborhood: str | None, project: Project) -> bool:
    candidate = _normalized_loose_text(candidate_neighborhood)
    if candidate is None:
        return False
    project_values = [
        project.tcg_region,
        project.city,
        project.costar_submarket,
        project.jurisdiction,
    ]
    return any(candidate == _normalized_loose_text(value) for value in project_values)


def _product_type_matches(candidate_product_type: str | None, project: Project) -> bool:
    mapped = _product_type_value(candidate_product_type)
    return mapped is not None and mapped == project.product_type.value


def _units_within_pct(candidate_units: int | None, project_units: int | None) -> bool:
    if candidate_units is None or project_units is None:
        return False
    if candidate_units == project_units:
        return True
    larger = max(abs(candidate_units), abs(project_units))
    if larger == 0:
        return True
    return abs(candidate_units - project_units) / larger <= 0.25


def _stories_within_one(candidate_stories: int | None, project_stories: int | None) -> bool:
    if candidate_stories is None or project_stories is None:
        return False
    return abs(candidate_stories - project_stories) <= 1


def _coordinates_within_meters(
    candidate_lat: float | None,
    candidate_lng: float | None,
    project_lat: float | None,
    project_lng: float | None,
    *,
    max_meters: float,
) -> bool:
    if None in {candidate_lat, candidate_lng, project_lat, project_lng}:
        return False
    assert candidate_lat is not None
    assert candidate_lng is not None
    assert project_lat is not None
    assert project_lng is not None
    return _haversine_meters(candidate_lat, candidate_lng, project_lat, project_lng) <= max_meters


def _haversine_meters(
    lat1: float,
    lng1: float,
    lat2: float,
    lng2: float,
) -> float:
    radius_m = 6_371_000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lng2 - lng1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return radius_m * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _candidate_identifiers(reference: NewsProjectReference) -> dict[str, list[str]]:
    identifiers = reference.candidate_identifiers or {}
    if not isinstance(identifiers, dict):
        return {}
    return {
        str(key): [str(value) for value in values if _clean_text(value)]
        for key, values in identifiers.items()
        if isinstance(values, list)
    }


def _raw_reference_payload(reference_row: NewsProjectReference) -> dict[str, Any]:
    extraction = reference_row.extraction
    if extraction is None or not isinstance(extraction.output_json, dict):
        return {}
    references = extraction.output_json.get("project_references")
    if not isinstance(references, list):
        return {}
    reference_index = reference_row.reference_index
    if reference_index < 0 or reference_index >= len(references):
        return {}
    reference = references[reference_index]
    return reference if isinstance(reference, dict) else {}


def _source_default_city(article: NewsArticle) -> str | None:
    source = article.source
    config = source.config if isinstance(source.config, dict) else {}
    configured = _clean_text(config.get("default_city"))
    if configured is not None:
        return configured
    jurisdiction = source.jurisdiction
    if jurisdiction is not None and jurisdiction.entity_type == "city":
        return _clean_text(jurisdiction.display_name or jurisdiction.name)
    return None


def _candidate_stories(reference: NewsProjectReference) -> int | None:
    payload = reference.candidate_signal_flags or {}
    if isinstance(payload, dict):
        for key in ("stories", "story_count", "floors"):
            value = payload.get(key)
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return None


def _product_type_value(value: str | None) -> str | None:
    normalized = _clean_text(value)
    if normalized is None:
        return None
    return {
        "apartment": "Apartment",
        "condo": "Condo",
        "townhome": "Townhome",
        "single_family": "Single-Family",
        "micro_co_living": "Micro/Co-Living",
        "other": "Other",
    }.get(normalized)


def _candidate_confidence_score(value: str | None) -> float:
    if value == "high":
        return 0.75
    if value == "medium":
        return 0.65
    return 0.35


def _with_diagnostics(
    result: NewsMatchResult,
    diagnostics: dict[str, Any],
) -> NewsMatchResult:
    if not diagnostics:
        return result
    return NewsMatchResult(
        status=result.status,
        match_type=result.match_type,
        confidence=result.confidence,
        project_id=result.project_id,
        candidate_project_ids=list(result.candidate_project_ids),
        candidates=list(result.candidates),
        reason=result.reason,
        diagnostics={**result.diagnostics, **diagnostics},
    )


def _canonical_address_has_postal_code(canonical_address: str) -> bool:
    street_and_city, separator, suffix = canonical_address.rpartition(" ")
    return bool(separator and street_and_city and suffix.isdigit() and len(suffix) == 5)


def _coerce_identifier_type(identifier_type_name: str) -> IdentifierType | None:
    try:
        return IdentifierType(identifier_type_name)
    except ValueError:
        return None


def _uuid_or_none(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_identifier(value: Any) -> str:
    return str(value).strip()


def _normalized_loose_text(value: Any) -> str | None:
    text = _clean_text(value)
    if text is None:
        return None
    return " ".join(text.upper().replace("&", " AND ").split())

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import desc, func, literal, or_, select
from sqlalchemy.orm import Session

from tcg_pipeline.db.models import (
    IdentifierType,
    NewsArticle,
    NewsProjectReference,
    PipelineStatus,
    Project,
    ProjectIdentifier,
    ReviewItem,
)
from tcg_pipeline.developer.registry import normalize_developer_name
from tcg_pipeline.matching.news_matcher import canonical_address_for_reference
from tcg_pipeline.matching.similarity import (
    MATCH_SIGNAL_WEIGHTS,
    MatchSignal,
    build_match_signals,
    distance_between_points,
    product_type_match_score,
    weighted_match_likelihood,
)
from tcg_pipeline.review.field_metadata import field_metadata_for_review

ACTIVE_REVIEW_STATES = ("open", "staged")
DELETED_PROJECT_STATUSES = (
    PipelineStatus.DELETE_DUPLICATE,
    PipelineStatus.DELETE_NOT_RESIDENTIAL,
    PipelineStatus.DELETE_OUTSIDE_MARKET_AREA,
)
HARD_GEOGRAPHIC_RADIUS_METERS = 250.0
DEVELOPER_SECONDARY_RADIUS_METERS = 1_000.0
SOFT_CANDIDATE_POOL_LIMIT = 75
DEFAULT_RESPONSE_LIMIT = 25
LAYER3_RESPONSE_LIMIT = 100
TRIGRAM_MIN_SCORE = 0.12


@dataclass(frozen=True, slots=True)
class DedupSubject:
    project_name: str | None = None
    canonical_address: str | None = None
    developer: str | None = None
    total_units: int | None = None
    market_rate_units: int | None = None
    affordable_units: int | None = None
    workforce_units: int | None = None
    product_type: str | None = None
    age_restriction: str | None = None
    pipeline_status: str | None = None
    building_height_stories: int | None = None
    lat: float | None = None
    lng: float | None = None
    market: str | None = None
    market_id: uuid.UUID | None = None
    jurisdiction_id: uuid.UUID | None = None
    identifiers: Mapping[str, list[str]] = field(default_factory=dict)

    def as_payload(self) -> dict[str, Any]:
        return {
            "project_name": self.project_name,
            "canonical_address": self.canonical_address,
            "developer": self.developer,
            "units_total": self.total_units,
            "units_market": self.market_rate_units,
            "units_affordable": self.affordable_units,
            "units_workforce": self.workforce_units,
            "product_type": self.product_type,
            "age_restriction": self.age_restriction,
            "pipeline_status": self.pipeline_status,
            "building_height_stories": self.building_height_stories,
            "lat": self.lat,
            "lng": self.lng,
            "identifiers": {
                identifier_type: list(values)
                for identifier_type, values in self.identifiers.items()
            },
        }


@dataclass(frozen=True, slots=True)
class DedupCandidate:
    project_id: uuid.UUID
    project_name: str | None
    canonical_address: str
    developer: str | None
    units_total: int | None
    units_market: int | None
    units_affordable: int | None
    units_workforce: int | None
    product_type: str | None
    age_restriction: str | None
    pipeline_status: str | None
    building_height_stories: int | None
    lat: float | None
    lng: float | None
    match_likelihood: float
    match_signals: dict[str, MatchSignal]
    match_layer: int
    distance_meters: float | None
    open_review_item_count: int

    def as_payload(self) -> dict[str, Any]:
        return {
            "project_id": str(self.project_id),
            "project_name": self.project_name,
            "canonical_address": self.canonical_address,
            "developer": self.developer,
            "units_total": self.units_total,
            "units_market": self.units_market,
            "units_affordable": self.units_affordable,
            "units_workforce": self.units_workforce,
            "product_type": self.product_type,
            "age_restriction": self.age_restriction,
            "pipeline_status": self.pipeline_status,
            "building_height_stories": self.building_height_stories,
            "lat": self.lat,
            "lng": self.lng,
            "match_likelihood": round(self.match_likelihood, 4),
            "match_signals": {
                name: signal.as_payload() for name, signal in self.match_signals.items()
            },
            "match_layer": self.match_layer,
            "distance_meters": (
                round(self.distance_meters, 2) if self.distance_meters is not None else None
            ),
            "open_review_item_count": self.open_review_item_count,
        }


@dataclass(frozen=True, slots=True)
class DedupCandidateSearchResult:
    subject: DedupSubject
    candidates: list[DedupCandidate]
    layer_3_available: bool
    searched: dict[str, Any]

    @property
    def new_candidate_probability(self) -> float:
        if not self.candidates:
            return 1.0
        best_match_likelihood = max(
            candidate.match_likelihood for candidate in self.candidates
        )
        return max(0.0, min(1.0, 1.0 - best_match_likelihood))

    def as_payload(self) -> dict[str, Any]:
        return {
            "subject": self.subject.as_payload(),
            "candidates": [candidate.as_payload() for candidate in self.candidates],
            "layer_3_available": self.layer_3_available,
            "new_candidate_probability": round(self.new_candidate_probability, 4),
            "searched": self.searched,
        }


@dataclass(frozen=True, slots=True)
class FieldDelta:
    field_name: str
    field_label: str
    field_type: str
    current_value: Any | None
    evidence_value: Any | None
    constraints: dict[str, Any]

    def as_payload(self) -> dict[str, Any]:
        return {
            "field_name": self.field_name,
            "field_label": self.field_label,
            "field_type": self.field_type,
            "current_value": self.current_value,
            "evidence_value": self.evidence_value,
            "constraints": dict(self.constraints),
        }


@dataclass(frozen=True, slots=True)
class _HardSignal:
    name: str
    detail: str


@dataclass(frozen=True, slots=True)
class _ScoredProject:
    project: Project
    address_similarity: float | None = None
    name_similarity: float | None = None


def subject_from_news_reference(
    article: NewsArticle,
    reference: NewsProjectReference,
) -> DedupSubject:
    return DedupSubject(
        project_name=_clean_text(reference.candidate_name),
        canonical_address=canonical_address_for_reference(article, reference),
        developer=_clean_text(reference.candidate_developer),
        total_units=reference.candidate_unit_total,
        market_rate_units=reference.candidate_unit_market_rate,
        affordable_units=reference.candidate_unit_affordable,
        workforce_units=reference.candidate_unit_workforce,
        product_type=_clean_text(reference.candidate_product_type),
        age_restriction=_clean_text(reference.candidate_age_restriction),
        pipeline_status=_clean_text(reference.candidate_status_signal),
        building_height_stories=reference.candidate_stories,
        lat=reference.candidate_lat,
        lng=reference.candidate_lng,
        market=article.source.market.slug if article.source and article.source.market else None,
        market_id=article.source.market_id if article.source else None,
        jurisdiction_id=article.source.jurisdiction_id if article.source else None,
        identifiers=_candidate_identifiers(reference.candidate_identifiers),
    )


def find_dedup_candidates(
    session: Session,
    subject: DedupSubject,
    *,
    include_layer3: bool = False,
    limit: int = DEFAULT_RESPONSE_LIMIT,
) -> DedupCandidateSearchResult:
    searched = searched_metadata(subject)
    if include_layer3:
        searched["layer_3"]["searched"] = True
    hard_signals = _load_layer1_hard_signals(session, subject)
    hard_ids = set(hard_signals)
    projects_by_id: dict[uuid.UUID, _ScoredProject] = {}
    project_layers: dict[uuid.UUID, int] = {}

    for project in _load_projects_by_id(session, hard_ids):
        projects_by_id[project.id] = _ScoredProject(project=project)
        project_layers[project.id] = 1

    layer2_scored_projects = _load_layer2_soft_projects(
        session,
        subject,
        exclude_project_ids=set(projects_by_id),
        limit=SOFT_CANDIDATE_POOL_LIMIT,
    )
    for scored in layer2_scored_projects:
        projects_by_id.setdefault(scored.project.id, scored)
        project_layers.setdefault(scored.project.id, 2)

    # Saturated Layer 2 strongly implies broader sweep availability and saves a
    # probe query on the hot path. Rare false positives are acceptable: clicking
    # "show more" may still return no extra rows after Layer 1/2 exclusions.
    layer3_available = len(layer2_scored_projects) >= SOFT_CANDIDATE_POOL_LIMIT
    if include_layer3:
        layer3_projects = _load_layer3_projects(
            session,
            subject,
            exclude_project_ids=set(projects_by_id),
            limit=LAYER3_RESPONSE_LIMIT,
        )
        layer3_available = bool(layer3_projects)
        for project in layer3_projects:
            projects_by_id.setdefault(project.id, _ScoredProject(project=project))
            project_layers.setdefault(project.id, 3)
    elif not layer3_available:
        layer3_available = _layer3_available(
            session,
            subject,
            exclude_project_ids=set(projects_by_id),
        )

    review_counts = _open_review_item_counts(session, set(projects_by_id))
    candidates = [
        _candidate_from_project(
            subject,
            scored_project.project,
            match_layer=project_layers.get(scored_project.project.id, 2),
            open_review_item_count=review_counts.get(scored_project.project.id, 0),
            hard_signals=hard_signals.get(scored_project.project.id, []),
            address_similarity=scored_project.address_similarity,
            name_similarity=scored_project.name_similarity,
        )
        for scored_project in projects_by_id.values()
    ]
    candidates.sort(
        key=lambda candidate: (
            candidate.match_layer,
            -candidate.match_likelihood,
            str(candidate.project_id),
        )
    )
    response_limit = LAYER3_RESPONSE_LIMIT if include_layer3 else limit
    return DedupCandidateSearchResult(
        subject=subject,
        candidates=candidates[:response_limit],
        layer_3_available=layer3_available,
        searched=searched,
    )


def compute_subject_candidate_deltas(
    subject: DedupSubject,
    candidate: DedupCandidate | Project,
) -> list[FieldDelta]:
    deltas: list[FieldDelta] = []
    for field_name, subject_value, candidate_value in _comparable_field_values(
        subject,
        candidate,
    ):
        normalized_subject = _delta_value(subject_value)
        if normalized_subject is None:
            continue
        normalized_candidate = _delta_value(candidate_value)
        if normalized_subject == normalized_candidate:
            continue
        metadata = field_metadata_for_review(field_name)
        deltas.append(
            FieldDelta(
                field_name=field_name,
                field_label=metadata.label,
                field_type=metadata.field_type,
                current_value=normalized_candidate,
                evidence_value=normalized_subject,
                constraints=dict(metadata.constraints),
            )
        )
    return deltas


def searched_metadata(subject: DedupSubject) -> dict[str, Any]:
    identifier_types = sorted(
        identifier_type
        for identifier_type, values in subject.identifiers.items()
        if values
    )
    return {
        "layer_1": [
            {
                "signal": "geographic",
                "searched": subject.lat is not None and subject.lng is not None,
                "criteria": f"projects within {int(HARD_GEOGRAPHIC_RADIUS_METERS)}m",
            },
            {
                "signal": "identifier",
                "searched": bool(identifier_types),
                "criteria": "shared APN or CoStar Property ID",
                "identifier_types": identifier_types,
            },
            {
                "signal": "address",
                "searched": subject.canonical_address is not None,
                "criteria": "exact canonical_address",
            },
            {
                "signal": "developer",
                "searched": subject.developer is not None,
                "criteria": "developer plus one secondary signal",
            },
        ],
        "layer_2": {
            "searched": (
                subject.project_name is not None or subject.canonical_address is not None
            ),
            "signals": [
                "geographic",
                "address_trigram",
                "name_trigram",
                "developer",
                "units",
                "product_type",
            ],
            "trigram_min_score": TRIGRAM_MIN_SCORE,
            "weights": dict(MATCH_SIGNAL_WEIGHTS),
        },
        "layer_3": {
            "searched": False,
            "available_when": "include_layer3=true",
            "criteria": "within 1km or low-threshold name/address trigram hit",
            "layer_3_radius_meters": DEVELOPER_SECONDARY_RADIUS_METERS,
        },
    }


def _load_layer1_hard_signals(
    session: Session,
    subject: DedupSubject,
) -> dict[uuid.UUID, list[_HardSignal]]:
    signals: dict[uuid.UUID, list[_HardSignal]] = defaultdict(list)
    _add_identifier_or_address_hard_signals(session, subject, signals)
    _add_geographic_hard_signals(session, subject, signals)
    _add_developer_secondary_hard_signals(session, subject, signals)
    return dict(signals)


def _add_identifier_or_address_hard_signals(
    session: Session,
    subject: DedupSubject,
    signals: dict[uuid.UUID, list[_HardSignal]],
) -> None:
    identifier_filters: list[Any] = []
    identifier_values_by_type: dict[IdentifierType, set[str]] = {}
    for identifier_type_name in ("apn", "costar_property_id"):
        values = _identifier_values(subject.identifiers, identifier_type_name)
        if not values:
            continue
        identifier_type = _coerce_identifier_type(identifier_type_name)
        if identifier_type is None:
            continue
        identifier_values_by_type[identifier_type] = set(values)
        identifier_filters.append(
            (ProjectIdentifier.identifier_type == identifier_type)
            & (ProjectIdentifier.value.in_(values))
        )
    filters: list[Any] = [*identifier_filters]
    if subject.canonical_address is not None:
        filters.append(Project.canonical_address == subject.canonical_address)
    if not filters:
        return
    statement = (
        select(
            Project.id,
            Project.canonical_address,
            ProjectIdentifier.identifier_type,
            ProjectIdentifier.value,
        )
        .outerjoin(ProjectIdentifier, ProjectIdentifier.project_id == Project.id)
        .where(or_(*filters), _active_project_filter())
    )
    statement = _scope_statement(statement, subject)
    seen_address_signals: set[uuid.UUID] = set()
    seen_identifier_signals: set[tuple[uuid.UUID, IdentifierType, str]] = set()
    for project_id, canonical_address, identifier_type, value in session.execute(statement).all():
        if (
            subject.canonical_address is not None
            and canonical_address == subject.canonical_address
            and project_id not in seen_address_signals
        ):
            signals[project_id].append(_HardSignal(name="address", detail="exact address"))
            seen_address_signals.add(project_id)
        values = identifier_values_by_type.get(identifier_type)
        if values is None or str(value) not in values:
            continue
        identifier_key = (project_id, identifier_type, str(value))
        if identifier_key in seen_identifier_signals:
            continue
        signals[project_id].append(
            _HardSignal(
                name="identifier",
                detail=f"{identifier_type.value}:{value}",
            )
        )
        seen_identifier_signals.add(identifier_key)


def _add_geographic_hard_signals(
    session: Session,
    subject: DedupSubject,
    signals: dict[uuid.UUID, list[_HardSignal]],
) -> None:
    if subject.lat is None or subject.lng is None:
        return
    statement = select(Project.id).where(
        Project.location.isnot(None),
        func.ST_DWithin(
            Project.location,
            func.ST_SetSRID(func.ST_MakePoint(subject.lng, subject.lat), 4326),
            HARD_GEOGRAPHIC_RADIUS_METERS,
        ),
        _active_project_filter(),
    )
    statement = _scope_statement(statement, subject)
    for project_id in session.execute(statement).scalars().all():
        signals[project_id].append(
            _HardSignal(
                name="geographic",
                detail=f"within {int(HARD_GEOGRAPHIC_RADIUS_METERS)}m",
            )
        )


def _add_developer_secondary_hard_signals(
    session: Session,
    subject: DedupSubject,
    signals: dict[uuid.UUID, list[_HardSignal]],
) -> None:
    subject_developer = normalize_developer_name(subject.developer)
    if subject_developer is None:
        return
    statement = select(Project).where(Project.developer.isnot(None), _active_project_filter())
    statement = _scope_statement(statement, subject)
    for project in session.execute(statement).scalars().all():
        if normalize_developer_name(project.developer) != subject_developer:
            continue
        secondary_reasons = _developer_secondary_reasons(subject, project)
        if secondary_reasons:
            signals[project.id].append(
                _HardSignal(
                    name="developer",
                    detail="developer plus " + ", ".join(secondary_reasons),
                )
            )


def _developer_secondary_reasons(subject: DedupSubject, project: Project) -> list[str]:
    reasons: list[str] = []
    distance = distance_between_points(subject.lat, subject.lng, project.lat, project.lng)
    if distance is not None and distance <= DEVELOPER_SECONDARY_RADIUS_METERS:
        reasons.append("nearby location")
    if product_type_match_score(subject.product_type, project.product_type) > 0:
        reasons.append("product type")
    if _partial_address_match(subject.canonical_address, project.canonical_address):
        reasons.append("partial address")
    if subject.total_units is not None and subject.total_units == project.total_units:
        reasons.append("unit count")
    return reasons


def _load_projects_by_id(session: Session, project_ids: set[uuid.UUID]) -> list[Project]:
    if not project_ids:
        return []
    return session.execute(
        select(Project).where(Project.id.in_(project_ids), _active_project_filter())
    ).scalars().all()


def _load_layer2_soft_projects(
    session: Session,
    subject: DedupSubject,
    *,
    exclude_project_ids: set[uuid.UUID],
    limit: int,
) -> list[_ScoredProject]:
    if subject.canonical_address is None and subject.project_name is None:
        return []
    address_similarity = (
        func.coalesce(func.similarity(Project.canonical_address, subject.canonical_address), 0.0)
        if subject.canonical_address is not None
        else literal(0.0)
    ).label("address_similarity")
    name_similarity = (
        func.coalesce(func.similarity(Project.project_name, subject.project_name), 0.0)
        if subject.project_name is not None
        else literal(0.0)
    ).label("name_similarity")
    statement = select(Project, address_similarity, name_similarity).where(
        _active_project_filter()
    )
    statement = _scope_statement(statement, subject)
    if exclude_project_ids:
        statement = statement.where(Project.id.notin_(exclude_project_ids))
    if subject.canonical_address is not None or subject.project_name is not None:
        statement = statement.where(
            or_(
                address_similarity >= TRIGRAM_MIN_SCORE,
                name_similarity >= TRIGRAM_MIN_SCORE,
            )
        )
    statement = statement.order_by(desc(address_similarity + name_similarity)).limit(limit)
    scored: list[_ScoredProject] = []
    for project, address_score, name_score in session.execute(statement).all():
        scored.append(
            _ScoredProject(
                project=project,
                address_similarity=float(address_score or 0.0),
                name_similarity=float(name_score or 0.0),
            )
        )
    return scored


def _layer3_available(
    session: Session,
    subject: DedupSubject,
    *,
    exclude_project_ids: set[uuid.UUID],
) -> bool:
    return bool(
        _load_layer3_projects(
            session,
            subject,
            exclude_project_ids=exclude_project_ids,
            limit=1,
        )
    )


def _load_layer3_projects(
    session: Session,
    subject: DedupSubject,
    *,
    exclude_project_ids: set[uuid.UUID],
    limit: int,
) -> list[Project]:
    filters: list[Any] = []
    if subject.lat is not None and subject.lng is not None:
        filters.append(
            func.ST_DWithin(
                Project.location,
                func.ST_SetSRID(func.ST_MakePoint(subject.lng, subject.lat), 4326),
                DEVELOPER_SECONDARY_RADIUS_METERS,
            )
        )
    if subject.canonical_address is not None:
        filters.append(
            func.similarity(Project.canonical_address, subject.canonical_address)
            >= TRIGRAM_MIN_SCORE
        )
    if subject.project_name is not None:
        filters.append(
            func.similarity(Project.project_name, subject.project_name) >= TRIGRAM_MIN_SCORE
        )
    if not filters:
        return []
    statement = select(Project).where(_active_project_filter(), or_(*filters))
    statement = _scope_statement(statement, subject)
    if exclude_project_ids:
        statement = statement.where(Project.id.notin_(exclude_project_ids))
    return session.execute(statement.limit(limit)).scalars().all()


def _candidate_from_project(
    subject: DedupSubject,
    project: Project,
    *,
    match_layer: int,
    open_review_item_count: int,
    hard_signals: list[_HardSignal],
    address_similarity: float | None = None,
    name_similarity: float | None = None,
) -> DedupCandidate:
    identifier_detail = _identifier_detail(hard_signals)
    signals = build_match_signals(
        subject_project_name=subject.project_name,
        subject_canonical_address=subject.canonical_address,
        subject_developer=subject.developer,
        subject_total_units=subject.total_units,
        subject_product_type=subject.product_type,
        subject_lat=subject.lat,
        subject_lng=subject.lng,
        project=project,
        address_similarity=address_similarity,
        name_similarity=name_similarity,
        identifier_detail=identifier_detail,
    )
    for hard_signal in hard_signals:
        signal = signals.get(hard_signal.name)
        if signal is not None and not signal.contributed:
            signals[hard_signal.name] = MatchSignal(
                score=max(signal.score, 1.0),
                contributed=True,
                searched=True,
                label=signal.label,
                detail=hard_signal.detail,
                weight=signal.weight,
            )
    distance_meters = distance_between_points(subject.lat, subject.lng, project.lat, project.lng)
    return DedupCandidate(
        project_id=project.id,
        project_name=project.project_name,
        canonical_address=project.canonical_address,
        developer=project.developer,
        units_total=project.total_units,
        units_market=project.market_rate_units,
        units_affordable=project.affordable_units,
        units_workforce=project.workforce_units,
        product_type=_enum_value(project.product_type),
        age_restriction=_enum_value(project.age_restriction),
        pipeline_status=_enum_value(project.pipeline_status),
        building_height_stories=project.stories,
        lat=project.lat,
        lng=project.lng,
        match_likelihood=weighted_match_likelihood(signals),
        match_signals=signals,
        match_layer=match_layer,
        distance_meters=distance_meters,
        open_review_item_count=open_review_item_count,
    )


def _open_review_item_counts(
    session: Session,
    project_ids: set[uuid.UUID],
) -> dict[uuid.UUID, int]:
    if not project_ids:
        return {}
    rows = session.execute(
        select(ReviewItem.project_id, func.count(ReviewItem.id))
        .where(
            ReviewItem.project_id.in_(project_ids),
            ReviewItem.state.in_(ACTIVE_REVIEW_STATES),
        )
        .group_by(ReviewItem.project_id)
    ).all()
    return {project_id: int(count) for project_id, count in rows if project_id is not None}


def _scope_statement(statement, subject: DedupSubject):
    if subject.market_id is not None:
        return statement.where(Project.market_id == subject.market_id)
    if subject.market is not None:
        return statement.where(Project.market == subject.market)
    return statement


def _active_project_filter():
    return Project.pipeline_status.notin_([status.value for status in DELETED_PROJECT_STATUSES])


def _identifier_values(
    identifiers: Mapping[str, list[str]],
    identifier_type: str,
) -> list[str]:
    values = identifiers.get(identifier_type) or []
    return sorted({str(value).strip() for value in values if str(value).strip()})


def _candidate_identifiers(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, list[str]] = {}
    for identifier_type, values in value.items():
        if not isinstance(values, list):
            continue
        cleaned_values = [str(item).strip() for item in values if str(item).strip()]
        if cleaned_values:
            result[str(identifier_type)] = sorted(set(cleaned_values))
    return result


def _coerce_identifier_type(identifier_type_name: str) -> IdentifierType | None:
    try:
        return IdentifierType(identifier_type_name)
    except ValueError:
        return None


def _partial_address_match(
    subject_address: str | None,
    project_address: str | None,
) -> bool:
    subject_tokens = _token_set(subject_address)
    project_tokens = _token_set(project_address)
    if not subject_tokens or not project_tokens:
        return False
    return len(subject_tokens & project_tokens) >= 3


def _token_set(value: str | None) -> set[str]:
    if value is None:
        return set()
    return {token for token in value.upper().replace(",", " ").split() if len(token) > 2}


def _identifier_detail(hard_signals: list[_HardSignal]) -> str | None:
    details = [signal.detail for signal in hard_signals if signal.name == "identifier"]
    return ", ".join(sorted(details)) if details else None


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    enum_value = getattr(value, "value", None)
    return str(enum_value if enum_value is not None else value)


def _comparable_field_values(
    subject: DedupSubject,
    candidate: DedupCandidate | Project,
) -> list[tuple[str, Any, Any]]:
    return [
        ("project_name", subject.project_name, _candidate_value(candidate, "project_name")),
        (
            "canonical_address",
            subject.canonical_address,
            _candidate_value(candidate, "canonical_address"),
        ),
        ("developer", subject.developer, _candidate_value(candidate, "developer")),
        ("total_units", subject.total_units, _candidate_value(candidate, "total_units")),
        (
            "market_rate_units",
            subject.market_rate_units,
            _candidate_value(candidate, "market_rate_units"),
        ),
        (
            "affordable_units",
            subject.affordable_units,
            _candidate_value(candidate, "affordable_units"),
        ),
        (
            "workforce_units",
            subject.workforce_units,
            _candidate_value(candidate, "workforce_units"),
        ),
        ("product_type", subject.product_type, _candidate_value(candidate, "product_type")),
        (
            "age_restriction",
            subject.age_restriction,
            _candidate_value(candidate, "age_restriction"),
        ),
        (
            "pipeline_status",
            subject.pipeline_status,
            _candidate_value(candidate, "pipeline_status"),
        ),
        ("stories", subject.building_height_stories, _candidate_value(candidate, "stories")),
    ]


def _candidate_value(candidate: DedupCandidate | Project, field_name: str) -> Any:
    if isinstance(candidate, DedupCandidate):
        candidate_field_names = {
            "total_units": "units_total",
            "market_rate_units": "units_market",
            "affordable_units": "units_affordable",
            "workforce_units": "units_workforce",
            "stories": "building_height_stories",
        }
        return getattr(candidate, candidate_field_names.get(field_name, field_name))
    return getattr(candidate, field_name)


def _delta_value(value: Any) -> Any:
    if value is None:
        return None
    enum_value = getattr(value, "value", None)
    if enum_value is not None:
        return enum_value
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return value


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from rapidfuzz import fuzz
from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from tcg_pipeline.db.models import DeveloperAlias, DeveloperRegistry

REGISTRY_CACHE_KEY = "developer_registry_rows"
FUZZY_AUTO_THRESHOLD = 90.0
FUZZY_REVIEW_THRESHOLD = 75.0
LEGAL_SUFFIX_TOKENS = {
    "CO",
    "COMPANY",
    "CORP",
    "CORPORATION",
    "INC",
    "INCORPORATED",
    "LC",
    "LLC",
    "LLP",
    "LP",
    "LTD",
    "LIMITED",
    "PLC",
}
GENERIC_DEVELOPER_TOKENS = {
    "ADVISOR",
    "ADVISORS",
    "AND",
    "ASSOCIATE",
    "ASSOCIATES",
    "CAPITAL",
    "COMMUNITIES",
    "COMMUNITY",
    "CONSTRUCTION",
    "DEVELOPMENT",
    "DEVELOPMENTS",
    "ESTATE",
    "FUND",
    "FUNDS",
    "GROUP",
    "GROUPS",
    "HOLDING",
    "HOLDINGS",
    "HOME",
    "HOMES",
    "HOUSING",
    "INVESTMENT",
    "INVESTMENTS",
    "MANAGEMENT",
    "PARTNER",
    "PARTNERS",
    "PROPERTIES",
    "PROPERTY",
    "REAL",
    "REALTY",
    "RESIDENTIAL",
    "SERVICE",
    "SERVICES",
    "TRUST",
    "VENTURE",
    "VENTURES",
}
MEANINGFUL_TOKEN_SIMILARITY_THRESHOLD = 80.0
NON_ALPHANUMERIC_PATTERN = re.compile(r"[^A-Z0-9]+")
IGNORED_REGISTRY_CANONICAL_NAMES = {
    # Data-quality guard: a polluted production registry row named "Category"
    # accumulated unrelated aliases and should never be a canonicalization target.
    "CATEGORY",
}


@dataclass(slots=True)
class DeveloperCanonicalizationResult:
    raw_name: str | None
    canonical_name: str | None
    match_type: str
    score: float | None = None
    canonical_developer_id: uuid.UUID | None = None
    source_registry_id: uuid.UUID | None = None
    alias_created: bool = False
    registry_created: bool = False
    registry_merged: bool = False
    is_top_tier: bool = False

    @property
    def requires_review(self) -> bool:
        return self.match_type in {"fuzzy_review", "new_registry_entry"}


@dataclass(slots=True, frozen=True)
class _CandidateMatch:
    developer_id: uuid.UUID
    canonical_name: str
    matched_name: str
    is_alias: bool
    is_top_tier: bool
    score: float | None = None


def normalize_developer_name(name: str | None) -> str | None:
    if name is None:
        return None
    raw_text = str(name).strip()
    if not raw_text:
        return None

    cleaned = raw_text.upper().replace("&", " AND ")
    cleaned = NON_ALPHANUMERIC_PATTERN.sub(" ", cleaned)
    tokens = cleaned.split()
    if tokens and tokens[0] == "THE":
        tokens = tokens[1:]
    while tokens and tokens[-1] in LEGAL_SUFFIX_TOKENS:
        tokens.pop()
    if not tokens:
        tokens = cleaned.split()
    normalized = " ".join(tokens).strip()
    return normalized or None


def canonicalize_developer_name(
    session: Session,
    raw_name: str | None,
    *,
    persist: bool = False,
    self_developer_id: uuid.UUID | None = None,
) -> DeveloperCanonicalizationResult:
    cleaned_name = _clean_name(raw_name)
    if cleaned_name is None:
        return DeveloperCanonicalizationResult(
            raw_name=None,
            canonical_name=None,
            match_type="empty",
        )

    normalized_name = normalize_developer_name(cleaned_name)
    if _is_ignored_registry_name(normalized_name):
        return DeveloperCanonicalizationResult(
            raw_name=cleaned_name,
            canonical_name=cleaned_name,
            match_type="ignored_registry_entry",
        )

    registry_rows = _load_registry(session)
    exact_matches = _find_exact_matches(registry_rows, normalized_name)
    result = _choose_match(
        cleaned_name,
        registry_rows,
        exact_matches=exact_matches,
        self_developer_id=self_developer_id,
    )

    source_registry_id = _find_source_registry_id(
        cleaned_name,
        exact_matches=exact_matches,
        self_developer_id=self_developer_id,
        canonical_developer_id=result.canonical_developer_id,
    )
    result.source_registry_id = source_registry_id
    if persist and result.match_type != "fuzzy_review":
        result = _persist_canonicalization(session, result)
    return result


def canonicalize_registry_entry(
    session: Session,
    developer_id: uuid.UUID,
    *,
    persist: bool = False,
) -> DeveloperCanonicalizationResult:
    developer = session.get(DeveloperRegistry, developer_id)
    if (
        developer is None
        or sqlalchemy_inspect(developer).deleted
        or developer in session.deleted
    ):
        return DeveloperCanonicalizationResult(
            raw_name=None,
            canonical_name=None,
            match_type="missing_registry_entry",
        )
    if not _is_usable_registry_row(developer, session):
        return DeveloperCanonicalizationResult(
            raw_name=developer.canonical_name,
            canonical_name=developer.canonical_name,
            canonical_developer_id=developer.id,
            match_type="ignored_registry_entry",
            is_top_tier=developer.is_top_tier,
        )
    return canonicalize_developer_name(
        session,
        developer.canonical_name,
        persist=persist,
        self_developer_id=developer.id,
    )


def _load_registry(session: Session) -> list[DeveloperRegistry]:
    cached_rows = session.info.get(REGISTRY_CACHE_KEY)
    if cached_rows is not None:
        return [
            row
            for row in cached_rows
            if _is_usable_registry_row(row, session)
        ]

    registry_rows = _load_registry_from_db(session)
    session.info[REGISTRY_CACHE_KEY] = registry_rows
    return registry_rows


def _load_registry_from_db(session: Session) -> list[DeveloperRegistry]:
    registry_rows = (
        session.execute(
            select(DeveloperRegistry)
            .options(selectinload(DeveloperRegistry.aliases))
            .order_by(DeveloperRegistry.canonical_name)
        )
        .scalars()
        .all()
    )
    return [
        row
        for row in registry_rows
        if _is_usable_registry_row(row, session)
    ]


def invalidate_registry_cache(session: Session) -> None:
    session.info.pop(REGISTRY_CACHE_KEY, None)


def _is_usable_registry_row(developer: DeveloperRegistry, session: Session) -> bool:
    if sqlalchemy_inspect(developer).deleted or developer in session.deleted:
        return False
    normalized = normalize_developer_name(developer.canonical_name)
    return not _is_ignored_registry_name(normalized)


def _is_ignored_registry_name(normalized_name: str | None) -> bool:
    return normalized_name in IGNORED_REGISTRY_CANONICAL_NAMES


def _find_exact_matches(
    registry_rows: list[DeveloperRegistry],
    normalized_name: str | None,
) -> list[_CandidateMatch]:
    if normalized_name is None:
        return []

    matches: list[_CandidateMatch] = []
    for developer in registry_rows:
        canonical_normalized = normalize_developer_name(developer.canonical_name)
        if canonical_normalized == normalized_name:
            matches.append(
                _CandidateMatch(
                    developer_id=developer.id,
                    canonical_name=developer.canonical_name,
                    matched_name=developer.canonical_name,
                    is_alias=False,
                    is_top_tier=developer.is_top_tier,
                )
            )
        for alias in developer.aliases:
            if normalize_developer_name(alias.alias_name) != normalized_name:
                continue
            matches.append(
                _CandidateMatch(
                    developer_id=developer.id,
                    canonical_name=developer.canonical_name,
                    matched_name=alias.alias_name,
                    is_alias=True,
                    is_top_tier=developer.is_top_tier,
                )
            )
    return matches


def _choose_match(
    raw_name: str,
    registry_rows: list[DeveloperRegistry],
    *,
    exact_matches: list[_CandidateMatch],
    self_developer_id: uuid.UUID | None,
) -> DeveloperCanonicalizationResult:
    non_self_exact_matches = [
        match for match in exact_matches if match.developer_id != self_developer_id
    ]
    if non_self_exact_matches:
        chosen = _preferred_match(exact_matches)
        return _build_result(
            raw_name,
            chosen=chosen,
            match_type=(
                "exact_alias"
                if chosen.is_alias or chosen.canonical_name.casefold() != raw_name.casefold()
                else "exact_canonical"
            ),
        )

    self_exact_match = _preferred_match(exact_matches) if exact_matches else None
    fuzzy_match = _best_fuzzy_match(
        raw_name,
        registry_rows,
        exclude_developer_id=self_developer_id,
    )
    if fuzzy_match is not None and fuzzy_match.score is not None:
        if fuzzy_match.score >= FUZZY_AUTO_THRESHOLD:
            return _build_result(raw_name, chosen=fuzzy_match, match_type="fuzzy_auto")
        if fuzzy_match.score >= FUZZY_REVIEW_THRESHOLD:
            return _build_result(raw_name, chosen=fuzzy_match, match_type="fuzzy_review")

    if self_exact_match is not None:
        return _build_result(raw_name, chosen=self_exact_match, match_type="exact_canonical")

    return DeveloperCanonicalizationResult(
        raw_name=raw_name,
        canonical_name=raw_name,
        canonical_developer_id=None,
        match_type="new_registry_entry",
    )


def _best_fuzzy_match(
    raw_name: str,
    registry_rows: list[DeveloperRegistry],
    *,
    exclude_developer_id: uuid.UUID | None,
) -> _CandidateMatch | None:
    normalized_name = normalize_developer_name(raw_name)
    if normalized_name is None:
        return None

    best_match_by_developer: dict[uuid.UUID, _CandidateMatch] = {}
    for developer in registry_rows:
        if developer.id == exclude_developer_id:
            continue
        for matched_name, is_alias in _iter_names(developer):
            normalized_candidate = normalize_developer_name(matched_name)
            if normalized_candidate is None:
                continue
            if not _has_meaningful_name_overlap(normalized_name, normalized_candidate):
                continue
            score = fuzz.token_set_ratio(normalized_name, normalized_candidate)
            existing = best_match_by_developer.get(developer.id)
            if existing is None or (existing.score or 0.0) < score:
                best_match_by_developer[developer.id] = _CandidateMatch(
                    developer_id=developer.id,
                    canonical_name=developer.canonical_name,
                    matched_name=matched_name,
                    is_alias=is_alias,
                    is_top_tier=developer.is_top_tier,
                    score=score,
                )

    if not best_match_by_developer:
        return None
    ranked_matches = sorted(
        best_match_by_developer.values(),
        key=lambda match: (
            -(match.score or 0.0),
            *_candidate_rank(match),
        ),
    )
    return ranked_matches[0]


def _iter_names(developer: DeveloperRegistry) -> list[tuple[str, bool]]:
    names = [(developer.canonical_name, False)]
    names.extend((alias.alias_name, True) for alias in developer.aliases)
    return names


def _preferred_match(matches: list[_CandidateMatch]) -> _CandidateMatch:
    return min(matches, key=_candidate_rank)


def _candidate_rank(match: _CandidateMatch) -> tuple[int, int, int, int, str]:
    normalized = normalize_developer_name(match.canonical_name) or match.canonical_name.upper()
    return (
        _suffix_count(match.canonical_name),
        len(normalized.split()),
        len(match.canonical_name),
        0 if match.is_top_tier else 1,
        match.canonical_name.casefold(),
    )


def _suffix_count(name: str) -> int:
    tokens = NON_ALPHANUMERIC_PATTERN.sub(" ", name.upper()).split()
    count = 0
    while tokens and tokens[-1] in LEGAL_SUFFIX_TOKENS:
        count += 1
        tokens.pop()
    return count


def _meaningful_tokens(name: str | None) -> set[str]:
    normalized = normalize_developer_name(name)
    if normalized is None:
        return set()
    return {
        token
        for token in normalized.split()
        if token and token not in GENERIC_DEVELOPER_TOKENS
    }


def _has_meaningful_name_overlap(left: str | None, right: str | None) -> bool:
    left_tokens = _meaningful_tokens(left)
    right_tokens = _meaningful_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    if left_tokens & right_tokens:
        return True
    for left_token in left_tokens:
        if len(left_token) < 4:
            continue
        for right_token in right_tokens:
            if len(right_token) < 4:
                continue
            if fuzz.ratio(left_token, right_token) >= MEANINGFUL_TOKEN_SIMILARITY_THRESHOLD:
                return True
    return False


def _build_result(
    raw_name: str,
    *,
    chosen: _CandidateMatch,
    match_type: str,
) -> DeveloperCanonicalizationResult:
    return DeveloperCanonicalizationResult(
        raw_name=raw_name,
        canonical_name=chosen.canonical_name,
        canonical_developer_id=chosen.developer_id,
        match_type=match_type,
        score=chosen.score,
        is_top_tier=chosen.is_top_tier,
    )


def _find_source_registry_id(
    raw_name: str,
    *,
    exact_matches: list[_CandidateMatch],
    self_developer_id: uuid.UUID | None,
    canonical_developer_id: uuid.UUID | None,
) -> uuid.UUID | None:
    for match in exact_matches:
        if match.is_alias:
            continue
        if match.matched_name.casefold() != raw_name.casefold():
            continue
        if match.developer_id == canonical_developer_id:
            return None
        return match.developer_id
    if self_developer_id is not None and self_developer_id != canonical_developer_id:
        return self_developer_id
    return None


def _persist_canonicalization(
    session: Session,
    result: DeveloperCanonicalizationResult,
) -> DeveloperCanonicalizationResult:
    canonical_name = _clean_name(result.canonical_name)
    if canonical_name is None:
        return result
    if _is_ignored_registry_name(normalize_developer_name(canonical_name)):
        return result

    canonical = None
    if result.canonical_developer_id is not None:
        canonical = session.get(DeveloperRegistry, result.canonical_developer_id)
    if canonical is None:
        canonical = DeveloperRegistry(canonical_name=canonical_name)
        session.add(canonical)
        session.flush()
        invalidate_registry_cache(session)
        result.canonical_developer_id = canonical.id
        result.registry_created = True

    if result.source_registry_id is not None and result.source_registry_id != canonical.id:
        result.registry_merged = _merge_registry_rows(
            session,
            source_id=result.source_registry_id,
            target_id=canonical.id,
        )
        canonical = session.get(DeveloperRegistry, canonical.id) or canonical

    raw_name = _clean_name(result.raw_name)
    if (
        raw_name is not None
        and canonical is not None
        and raw_name.casefold() != canonical.canonical_name.casefold()
    ):
        result.alias_created = _ensure_alias(
            session,
            developer_id=canonical.id,
            alias_name=raw_name,
        )

    result.is_top_tier = bool(canonical.is_top_tier) if canonical is not None else False
    return result


def _merge_registry_rows(
    session: Session,
    *,
    source_id: uuid.UUID,
    target_id: uuid.UUID,
) -> bool:
    if source_id == target_id:
        return False

    source = (
        session.execute(
            select(DeveloperRegistry)
            .options(selectinload(DeveloperRegistry.aliases))
            .where(DeveloperRegistry.id == source_id)
        )
        .scalars()
        .first()
    )
    target = (
        session.execute(
            select(DeveloperRegistry)
            .options(selectinload(DeveloperRegistry.aliases))
            .where(DeveloperRegistry.id == target_id)
        )
        .scalars()
        .first()
    )
    if source is None or target is None:
        return False

    if source.is_top_tier and not target.is_top_tier:
        target.is_top_tier = True
    if source.notes:
        target.notes = _merge_notes(target.notes, source.notes)

    for alias in list(source.aliases):
        cleaned_alias = _clean_name(alias.alias_name)
        if cleaned_alias is None or cleaned_alias.casefold() == target.canonical_name.casefold():
            session.delete(alias)
            continue
        alias.developer = target

    if source.canonical_name.casefold() != target.canonical_name.casefold():
        _ensure_alias(
            session,
            developer_id=target.id,
            alias_name=source.canonical_name,
        )

    session.flush()
    session.delete(source)
    session.flush()
    invalidate_registry_cache(session)
    return True


def _ensure_alias(
    session: Session,
    *,
    developer_id: uuid.UUID,
    alias_name: str,
) -> bool:
    cleaned_alias = _clean_name(alias_name)
    if cleaned_alias is None:
        return False

    target = session.get(DeveloperRegistry, developer_id)
    if target is None or cleaned_alias.casefold() == target.canonical_name.casefold():
        return False

    pending_alias = _find_alias_in_session(session, cleaned_alias)
    if pending_alias is not None:
        if pending_alias.developer_id != developer_id:
            _merge_registry_rows(
                session,
                source_id=pending_alias.developer_id,
                target_id=developer_id,
            )
        return False

    existing_alias = session.execute(
        select(DeveloperAlias).where(DeveloperAlias.alias_name == cleaned_alias)
    ).scalar_one_or_none()
    if existing_alias is not None:
        if existing_alias.developer_id != developer_id:
            _merge_registry_rows(
                session,
                source_id=existing_alias.developer_id,
                target_id=developer_id,
            )
        return False

    session.add(
        DeveloperAlias(
            developer_id=developer_id,
            alias_name=cleaned_alias,
        )
    )
    invalidate_registry_cache(session)
    return True


def _find_alias_in_session(
    session: Session,
    alias_name: str,
) -> DeveloperAlias | None:
    for candidate in session.new:
        if isinstance(candidate, DeveloperAlias) and candidate.alias_name == alias_name:
            return candidate
    for candidate in session.identity_map.values():
        if isinstance(candidate, DeveloperAlias) and candidate.alias_name == alias_name:
            return candidate
    return None


def _merge_notes(existing: str | None, incoming: str) -> str:
    incoming = incoming.strip()
    if not incoming:
        return existing or ""
    if not existing:
        return incoming
    if incoming in existing:
        return existing
    return f"{existing}\nMerged note: {incoming}"


def _clean_name(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None

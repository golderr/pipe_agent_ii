from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

import ahocorasick
import dateparser
from dateparser.search import search_dates
from sqlalchemy import select
from sqlalchemy.orm import Session

from tcg_pipeline.db.models import DeveloperAlias, DeveloperRegistry, NewsArticle, Project
from tcg_pipeline.matching.normalizer import normalize_address

STRUCTURAL_EXTRACTOR_VERSION = "v1"
MAX_DATE_SIGNALS = 30


@dataclass(frozen=True, slots=True)
class StructuralSignal:
    extractor: str
    raw_match: str
    offset_start: int
    offset_end: int
    canonical: Any
    confidence: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


UNIT_COUNT_RE = re.compile(
    r"(?<![\d.,])(?P<count>\d{2,5})[-\s]?"
    r"(?P<label>unit|units|apartment|apartments|residences|residential\s+units|"
    r"condos|condominium|condominiums|keys|rooms)\b",
    re.IGNORECASE,
)
ADDRESS_RE = re.compile(
    r"\b\d{2,6}(?:-\d{2,6})?\s+"
    r"(?:[NSEW]\.?\s+|North\s+|South\s+|East\s+|West\s+)?"
    r"[A-Z][A-Za-z0-9.'-]*(?:\s+[A-Z][A-Za-z0-9.'-]*){0,5}\s+"
    r"(?:Street|St\.?|Avenue|Ave\.?|Boulevard|Blvd\.?|Road|Rd\.?|Drive|Dr\.?|"
    r"Place|Pl\.?|Way|Lane|Ln\.?|Court|Ct\.?)"
    r"(?:,?\s+(?:Los Angeles|Hollywood|Downtown Los Angeles|DTLA))?"
    r"(?:,\s*CA|\s+CA)?(?:\s+\d{5})?\b",
)
CASE_NUMBER_RE = re.compile(
    r"\b(CPC|VTT|TT|ENV|DIR|ZA|APCC|APCSV|APCNV|APCS|APCH|APCE|APCW)"
    r"-\d{4}-\d+(?:-[A-Z0-9-]+)?\b",
    re.IGNORECASE,
)
PERMIT_NUMBER_RE = re.compile(r"\b\d{2}[A-Z]?\d{3}-?\d{5}-?\d{5}\b", re.IGNORECASE)
APN_RE = re.compile(r"\b(?P<first>\d{4})[-\s]?(?P<second>\d{3})[-\s]?(?P<third>\d{3})\b")
DATE_LIKE_RE = re.compile(
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|sept|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?|mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|thu(?:rsday)?|"
    r"fri(?:day)?|sat(?:urday)?|sun(?:day)?)\b"
    r"|\b\d{4}-\d{1,2}-\d{1,2}\b"
    r"|\b\d{1,2}/\d{1,2}/\d{2,4}\b",
    re.IGNORECASE,
)
DATE_STOPWORDS = {"now", "to", "in", "by", "at", "on"}
DELIVERY_PHRASE_RE = re.compile(
    r"\b(?P<prefix>expected to|scheduled to|will|set to|aiming to|projected to)\s+"
    r"(?P<verb>deliver|open|complete|finish)\s+(?:(?:in|by)\s+)?"
    r"(?P<target>(?:Q[1-4]\s+\d{4})|(?:(?:early|mid|late)\s+\d{4})|"
    r"(?:(?:spring|summer|fall|winter)\s+\d{4})|\d{4})\b",
    re.IGNORECASE,
)
PRODUCT_TYPE_RE = re.compile(
    r"\b(apartments?|condos?|condominiums?|townhomes?|build-to-rent|BTR|"
    r"single-family|micro[-\s]units?|co-living)\b",
    re.IGNORECASE,
)
AGE_RESTRICTION_RE = re.compile(
    r"\b(senior\s+(?:housing|living|apartments?)|55\+|62\+|student\s+housing|"
    r"university\s+housing)\b",
    re.IGNORECASE,
)
AFFORDABLE_SPLIT_RE = re.compile(
    r"\b(?P<count>\d{1,4})\s+"
    r"(?P<kind>affordable|low-income|workforce|moderate-income|market-rate|market\s+rate)\b"
    r"|\b(?P<pct>\d{1,3})%\s+(?P<pct_kind>affordable|inclusionary)\b",
    re.IGNORECASE,
)

STATUS_PHRASES: tuple[tuple[str, str, str], ...] = (
    ("under construction", "pipeline_status", "Under Construction"),
    ("construction is underway", "pipeline_status", "Under Construction"),
    ("vertical construction", "pipeline_status", "Under Construction"),
    ("broke ground", "signal_flag", "groundbreaking_announced"),
    ("groundbreaking", "signal_flag", "groundbreaking_announced"),
    ("construction began", "signal_flag", "groundbreaking_announced"),
    ("topped out", "signal_flag", "topped_out"),
    ("reached the top floor", "signal_flag", "topped_out"),
    ("structurally complete", "signal_flag", "topped_out"),
    ("opened", "pipeline_status", "Complete"),
    ("delivered", "pipeline_status", "Complete"),
    ("now open", "pipeline_status", "Complete"),
    ("residents are moving in", "pipeline_status", "Complete"),
    ("first occupancy", "pipeline_status", "Complete"),
    ("approved by city council", "pipeline_status", "Approved"),
    ("won approval", "pipeline_status", "Approved"),
    ("received approval", "pipeline_status", "Approved"),
    ("city approved", "pipeline_status", "Approved"),
    ("ENV cleared", "pipeline_status", "Approved"),
    ("filed plans", "pipeline_status", "Pending"),
    ("submitted application", "pipeline_status", "Pending"),
    ("filed for entitlement", "pipeline_status", "Pending"),
    ("applied to", "pipeline_status", "Pending"),
    ("proposed", "pipeline_status", "Proposed"),
    ("plans for", "pipeline_status", "Proposed"),
    ("is planning", "pipeline_status", "Proposed"),
    ("shelved", "signal_flag", "stalled_indicator"),
    ("on hold", "signal_flag", "stalled_indicator"),
    ("paused", "signal_flag", "stalled_indicator"),
    ("delayed indefinitely", "signal_flag", "stalled_indicator"),
    ("stalled", "signal_flag", "stalled_indicator"),
    ("lawsuit", "signal_flag", "lawsuit_filed"),
    ("sued", "signal_flag", "lawsuit_filed"),
    ("plaintiff", "signal_flag", "lawsuit_filed"),
    ("complaint filed", "signal_flag", "lawsuit_filed"),
    ("appeal filed", "signal_flag", "appeal_filed"),
    ("under appeal", "signal_flag", "appeal_filed"),
    ("appealed the decision", "signal_flag", "appeal_filed"),
    ("opposition", "signal_flag", "community_opposition"),
    ("opposed by", "signal_flag", "community_opposition"),
    ("residents protested", "signal_flag", "community_opposition"),
    ("community pushback", "signal_flag", "community_opposition"),
    ("NIMBY", "signal_flag", "community_opposition"),
    ("construction loan", "signal_flag", "construction_financing_announced"),
    ("financing closed", "signal_flag", "construction_financing_announced"),
    ("secured financing", "signal_flag", "construction_financing_announced"),
    ("refinanced", "signal_flag", "construction_financing_announced"),
    ("leasing center open", "signal_flag", "sales_or_leasing_center_open"),
    ("sales office open", "signal_flag", "sales_or_leasing_center_open"),
    ("now leasing", "signal_flag", "sales_or_leasing_center_open"),
    ("now selling", "signal_flag", "sales_or_leasing_center_open"),
    ("pre-leasing", "signal_flag", "sales_or_leasing_center_open"),
)
STATUS_PHRASE_RE = re.compile(
    "|".join(rf"\b{re.escape(phrase)}\b" for phrase, _kind, _canonical in STATUS_PHRASES),
    re.IGNORECASE,
)
STATUS_PHRASE_LOOKUP = {
    phrase.casefold(): (kind, canonical)
    for phrase, kind, canonical in STATUS_PHRASES
}


def build_structural_signals_payload(
    body_text: str,
    *,
    session: Session | None = None,
    market_slug: str | None = None,
    market_id: uuid.UUID | None = None,
    published_at: datetime | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    ran_at = now or datetime.now(UTC)
    signals = extract_structural_signals(
        body_text,
        session=session,
        market_slug=market_slug,
        market_id=market_id,
        published_at=published_at,
    )
    return {
        "extractor_version": STRUCTURAL_EXTRACTOR_VERSION,
        "ran_at": ran_at.isoformat(),
        "signals": [signal.to_dict() for signal in signals],
    }


def apply_structural_signals(
    session: Session,
    *,
    article: NewsArticle,
    market_slug: str | None,
    market_id: uuid.UUID | None,
    now: datetime | None = None,
) -> None:
    if not article.body_text:
        return
    ran_at = now or datetime.now(UTC)
    article.structural_signals = build_structural_signals_payload(
        article.body_text,
        session=session,
        market_slug=market_slug,
        market_id=market_id,
        published_at=article.published_at,
        now=ran_at,
    )
    article.structural_signals_at = ran_at


def extract_structural_signals(
    body_text: str,
    *,
    session: Session | None = None,
    market_slug: str | None = None,
    market_id: uuid.UUID | None = None,
    published_at: datetime | None = None,
) -> list[StructuralSignal]:
    signals: list[StructuralSignal] = []
    signals.extend(_unit_count_signals(body_text))
    signals.extend(_address_signals(body_text, market_slug=market_slug))
    signals.extend(_regex_identifier_signals(body_text))
    signals.extend(_date_signals(body_text, relative_base=published_at))
    signals.extend(_status_phrase_signals(body_text))
    signals.extend(_delivery_phrase_signals(body_text))
    signals.extend(_product_type_signals(body_text))
    signals.extend(_age_restriction_signals(body_text))
    signals.extend(_affordable_split_signals(body_text))
    if session is not None:
        signals.extend(_developer_dict_signals(session, body_text))
        signals.extend(
            _project_dict_signals(
                session,
                body_text,
                market_slug=market_slug,
                market_id=market_id,
            )
        )
    return _dedupe_and_sort_signals(signals)


def _unit_count_signals(body_text: str) -> list[StructuralSignal]:
    signals: list[StructuralSignal] = []
    for match in UNIT_COUNT_RE.finditer(body_text):
        if _is_money_context(body_text, match.start()):
            continue
        count = int(match.group("count").replace(",", ""))
        signals.append(
            StructuralSignal(
                extractor="unit_count",
                raw_match=match.group(0),
                offset_start=match.start(),
                offset_end=match.end(),
                canonical=count,
                confidence=0.95,
                metadata={"label": match.group("label").lower()},
            )
        )
    return signals


def _address_signals(body_text: str, *, market_slug: str | None) -> list[StructuralSignal]:
    signals: list[StructuralSignal] = []
    for match in ADDRESS_RE.finditer(body_text):
        raw_address = match.group(0).rstrip(".,;:")
        normalized = normalize_address(
            raw_address,
            city="Los Angeles" if market_slug == "los_angeles" else None,
            state="CA",
            market=market_slug,
        )
        if normalized.canonical_address is None:
            continue
        signals.append(
            StructuralSignal(
                extractor="address",
                raw_match=raw_address,
                offset_start=match.start(),
                offset_end=match.start() + len(raw_address),
                canonical={
                    "canonical_address": normalized.canonical_address,
                    "street_number": normalized.house_number,
                    "street_name": normalized.street_name,
                    "suffix": normalized.street_suffix,
                    "city": normalized.city,
                    "zip": normalized.postal_code,
                },
                confidence=0.8,
                metadata={"parser": normalized.parser},
            )
        )
    return signals


def _regex_identifier_signals(body_text: str) -> list[StructuralSignal]:
    signals: list[StructuralSignal] = []
    for match in CASE_NUMBER_RE.finditer(body_text):
        raw = match.group(0)
        signals.append(
            StructuralSignal(
                extractor="case_number",
                raw_match=raw,
                offset_start=match.start(),
                offset_end=match.end(),
                canonical=raw.upper(),
                confidence=0.99,
            )
        )
    for match in PERMIT_NUMBER_RE.finditer(body_text):
        raw = match.group(0)
        signals.append(
            StructuralSignal(
                extractor="permit_number",
                raw_match=raw,
                offset_start=match.start(),
                offset_end=match.end(),
                canonical=raw.upper().replace("-", ""),
                confidence=0.97,
            )
        )
    for match in APN_RE.finditer(body_text):
        canonical = f"{match.group('first')}-{match.group('second')}-{match.group('third')}"
        signals.append(
            StructuralSignal(
                extractor="apn",
                raw_match=match.group(0),
                offset_start=match.start(),
                offset_end=match.end(),
                canonical=canonical,
                confidence=0.98,
            )
        )
    return signals


def _date_signals(
    body_text: str,
    *,
    relative_base: datetime | None,
) -> list[StructuralSignal]:
    settings: dict[str, object] = {
        "RETURN_AS_TIMEZONE_AWARE": True,
        "TIMEZONE": "UTC",
    }
    if relative_base is not None:
        settings["RELATIVE_BASE"] = _as_utc_datetime(relative_base)
    matches = search_dates(
        body_text,
        settings=settings,
    )
    if not matches:
        return []
    signals: list[StructuralSignal] = []
    consumed_offsets: set[tuple[int, int]] = set()
    for raw, parsed in matches[:MAX_DATE_SIGNALS]:
        if not _is_plausible_date_match(raw):
            continue
        offset = _find_unconsumed_offset(body_text, raw, consumed_offsets)
        if offset is None:
            continue
        start, end = offset
        consumed_offsets.add(offset)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        parsed_date = parsed.astimezone(UTC).date().isoformat()
        signals.append(
            StructuralSignal(
                extractor="date",
                raw_match=raw,
                offset_start=start,
                offset_end=end,
                canonical=parsed_date,
                confidence=0.65,
                metadata={"surrounding_text": _surrounding_text(body_text, start, end)},
            )
        )
    return signals


def _is_plausible_date_match(raw: str) -> bool:
    cleaned = raw.strip()
    if cleaned.casefold() in DATE_STOPWORDS:
        return False
    if re.fullmatch(r"\d{1,4}", cleaned):
        return False
    return bool(DATE_LIKE_RE.search(cleaned))


def _as_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _status_phrase_signals(body_text: str) -> list[StructuralSignal]:
    signals: list[StructuralSignal] = []
    for match in STATUS_PHRASE_RE.finditer(body_text):
        raw = match.group(0)
        signal_kind, canonical = STATUS_PHRASE_LOOKUP[raw.casefold()]
        signals.append(
            StructuralSignal(
                extractor="status_phrase",
                raw_match=raw,
                offset_start=match.start(),
                offset_end=match.end(),
                canonical=canonical,
                confidence=0.9,
                metadata={"signal_kind": signal_kind},
            )
        )
    return signals


def _delivery_phrase_signals(body_text: str) -> list[StructuralSignal]:
    signals: list[StructuralSignal] = []
    for match in DELIVERY_PHRASE_RE.finditer(body_text):
        target = match.group("target")
        parsed = _parse_delivery_target(target)
        signals.append(
            StructuralSignal(
                extractor="delivery_phrase",
                raw_match=match.group(0),
                offset_start=match.start(),
                offset_end=match.end(),
                canonical=parsed,
                confidence=0.75 if parsed else 0.6,
                metadata={
                    "target": target,
                    "verb": match.group("verb").lower(),
                },
            )
        )
    return signals


def _product_type_signals(body_text: str) -> list[StructuralSignal]:
    signals: list[StructuralSignal] = []
    for match in PRODUCT_TYPE_RE.finditer(body_text):
        raw = match.group(0)
        signals.append(
            StructuralSignal(
                extractor="product_type_phrase",
                raw_match=raw,
                offset_start=match.start(),
                offset_end=match.end(),
                canonical=_canonical_product_type(raw),
                confidence=0.75,
            )
        )
    return signals


def _age_restriction_signals(body_text: str) -> list[StructuralSignal]:
    signals: list[StructuralSignal] = []
    for match in AGE_RESTRICTION_RE.finditer(body_text):
        raw = match.group(0)
        raw_folded = raw.casefold()
        canonical = "student" if "student" in raw_folded or "university" in raw_folded else "senior"
        signals.append(
            StructuralSignal(
                extractor="age_restriction_phrase",
                raw_match=raw,
                offset_start=match.start(),
                offset_end=match.end(),
                canonical=canonical,
                confidence=0.8,
            )
        )
    return signals


def _affordable_split_signals(body_text: str) -> list[StructuralSignal]:
    signals: list[StructuralSignal] = []
    for match in AFFORDABLE_SPLIT_RE.finditer(body_text):
        raw = match.group(0)
        count = match.group("count")
        pct = match.group("pct")
        canonical: dict[str, Any]
        if count is not None:
            kind = (match.group("kind") or "").replace(" ", "_").replace("-", "_").lower()
            canonical = {"count": int(count), "kind": kind}
        else:
            pct_kind = (match.group("pct_kind") or "").lower()
            canonical = {"percent": int(pct or "0"), "kind": pct_kind}
        signals.append(
            StructuralSignal(
                extractor="affordable_split_phrase",
                raw_match=raw,
                offset_start=match.start(),
                offset_end=match.end(),
                canonical=canonical,
                confidence=0.82,
            )
        )
    return signals


def _developer_dict_signals(session: Session, body_text: str) -> list[StructuralSignal]:
    entries: list[tuple[str, str, str]] = []
    for developer in session.execute(select(DeveloperRegistry)).scalars():
        entries.append((developer.canonical_name, str(developer.id), developer.canonical_name))
    alias_rows = session.execute(
        select(DeveloperAlias.alias_name, DeveloperRegistry.id, DeveloperRegistry.canonical_name)
        .join(DeveloperRegistry, DeveloperRegistry.id == DeveloperAlias.developer_id)
    )
    for alias_name, developer_id, canonical_name in alias_rows:
        entries.append((alias_name, str(developer_id), canonical_name))
    return _dictionary_signals(
        body_text,
        entries=entries,
        extractor="developer_dict",
        confidence=0.99,
        canonical_key="developer_id",
    )


def _project_dict_signals(
    session: Session,
    body_text: str,
    *,
    market_slug: str | None,
    market_id: uuid.UUID | None,
) -> list[StructuralSignal]:
    query = select(Project)
    if market_id is not None:
        query = query.where(Project.market_id == market_id)
    elif market_slug is not None:
        query = query.where(Project.market == market_slug)
    entries: list[tuple[str, str, str]] = []
    for project in session.execute(query).scalars():
        if project.project_name:
            entries.append((project.project_name, str(project.id), project.project_name))
        for previous_name in project.previous_names or []:
            entries.append((previous_name, str(project.id), project.project_name or previous_name))
    return _dictionary_signals(
        body_text,
        entries=entries,
        extractor="project_dict",
        confidence=0.82,
        canonical_key="project_id",
    )


def _dictionary_signals(
    body_text: str,
    *,
    entries: list[tuple[str, str, str]],
    extractor: str,
    confidence: float,
    canonical_key: str,
) -> list[StructuralSignal]:
    automaton = ahocorasick.Automaton()
    entry_count = 0
    for raw_name, canonical_id, display_name in entries:
        normalized_name = _clean_dictionary_name(raw_name)
        if normalized_name is None:
            continue
        automaton.add_word(
            normalized_name.casefold(),
            (normalized_name, canonical_id, display_name),
        )
        entry_count += 1
    if entry_count == 0:
        return []
    automaton.make_automaton()

    folded_text = body_text.casefold()
    signals: list[StructuralSignal] = []
    for end_index, (matched_name, canonical_id, display_name) in automaton.iter(folded_text):
        start_index = end_index - len(matched_name) + 1
        end_offset = end_index + 1
        if not _has_word_boundaries(body_text, start_index, end_offset):
            continue
        signals.append(
            StructuralSignal(
                extractor=extractor,
                raw_match=body_text[start_index:end_offset],
                offset_start=start_index,
                offset_end=end_offset,
                canonical=canonical_id,
                confidence=confidence,
                metadata={
                    canonical_key: canonical_id,
                    "matched_name": matched_name,
                    "display_name": display_name,
                },
            )
        )
    return signals


def _dedupe_and_sort_signals(signals: list[StructuralSignal]) -> list[StructuralSignal]:
    deduped: dict[tuple[str, int, int, str], StructuralSignal] = {}
    for signal in signals:
        key = (
            signal.extractor,
            signal.offset_start,
            signal.offset_end,
            str(signal.canonical),
        )
        existing = deduped.get(key)
        if existing is None or signal.confidence > existing.confidence:
            deduped[key] = signal
    return sorted(
        deduped.values(),
        key=lambda signal: (
            signal.offset_start,
            signal.offset_end,
            signal.extractor,
            str(signal.canonical),
        ),
    )


def _is_money_context(body_text: str, offset_start: int) -> bool:
    prefix = body_text[max(0, offset_start - 3) : offset_start]
    return "$" in prefix


def _find_unconsumed_offset(
    body_text: str,
    raw: str,
    consumed_offsets: set[tuple[int, int]],
) -> tuple[int, int] | None:
    for match in re.finditer(re.escape(raw), body_text):
        offset = (match.start(), match.end())
        if offset not in consumed_offsets:
            return offset
    return None


def _surrounding_text(body_text: str, offset_start: int, offset_end: int) -> str:
    return body_text[max(0, offset_start - 80) : min(len(body_text), offset_end + 80)]


def _parse_delivery_target(target: str) -> str | None:
    normalized = target.strip()
    quarter_match = re.fullmatch(r"Q([1-4])\s+(\d{4})", normalized, re.IGNORECASE)
    if quarter_match:
        quarter_month = {"1": "02", "2": "05", "3": "08", "4": "11"}[quarter_match.group(1)]
        return f"{quarter_match.group(2)}-{quarter_month}-01"
    season_match = re.fullmatch(
        r"(spring|summer|fall|winter)\s+(\d{4})",
        normalized,
        re.IGNORECASE,
    )
    if season_match:
        season_month = {
            "spring": "04",
            "summer": "07",
            "fall": "10",
            "winter": "01",
        }[season_match.group(1).casefold()]
        return f"{season_match.group(2)}-{season_month}-01"
    timing_match = re.fullmatch(r"(early|mid|late)\s+(\d{4})", normalized, re.IGNORECASE)
    if timing_match:
        timing_month = {"early": "03", "mid": "07", "late": "11"}[timing_match.group(1).casefold()]
        return f"{timing_match.group(2)}-{timing_month}-01"
    parsed = dateparser.parse(
        normalized,
        settings={"RETURN_AS_TIMEZONE_AWARE": True, "TIMEZONE": "UTC"},
    )
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).date().isoformat()


def _canonical_product_type(raw: str) -> str:
    normalized = raw.casefold().replace("-", " ")
    if "condo" in normalized:
        return "condo"
    if "townhome" in normalized:
        return "townhome"
    if "single" in normalized:
        return "single_family"
    if "micro" in normalized or "co living" in normalized:
        return "micro_co_living"
    if "build to rent" in normalized or normalized == "btr":
        return "build_to_rent"
    return "apartment"


def _clean_dictionary_name(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", value).strip()
    if len(cleaned) < 4:
        return None
    return cleaned


def _has_word_boundaries(body_text: str, start_index: int, end_offset: int) -> bool:
    before = body_text[start_index - 1] if start_index > 0 else " "
    after = body_text[end_offset] if end_offset < len(body_text) else " "
    return not before.isalnum() and not after.isalnum()

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from tcg_pipeline.db.models import ReviewItemType

MAX_HUMAN_SUMMARY_LENGTH = 500

_HTML_TAG_RE = re.compile(r"<[A-Za-z][^>]*>")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_human_summary(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = _WHITESPACE_RE.sub(" ", value).strip()
    if not text or len(text) > MAX_HUMAN_SUMMARY_LENGTH:
        return None
    if _HTML_TAG_RE.search(text):
        return None
    return text


def payload_with_human_summary(
    payload: Mapping[str, Any] | None,
    *,
    item_type: ReviewItemType | str,
    field_name: str | None = None,
    agent_revised_verdict: Mapping[str, Any] | None = None,
    existing_payload: Mapping[str, Any] | None = None,
    source_name: str | None = None,
) -> dict[str, Any]:
    result = dict(payload or {})
    existing_summary = normalize_human_summary(
        existing_payload.get("human_summary") if existing_payload is not None else None
    )
    if existing_summary is not None:
        result["human_summary"] = existing_summary
        return result

    current_summary = normalize_human_summary(result.get("human_summary"))
    if current_summary is not None:
        result["human_summary"] = current_summary
        return result

    agent_summary = normalize_human_summary(
        agent_revised_verdict.get("human_summary") if agent_revised_verdict is not None else None
    )
    if agent_summary is None:
        payload_verdict = _mapping(result.get("agent_revised_verdict"))
        agent_summary = normalize_human_summary(payload_verdict.get("human_summary"))
    if agent_summary is not None:
        result["human_summary"] = agent_summary
        return result

    result["human_summary"] = human_summary_for_payload(
        item_type=item_type,
        payload=result,
        field_name=field_name,
        source_name=source_name,
    )
    return result


def human_summary_for_payload(
    *,
    item_type: ReviewItemType | str,
    payload: Mapping[str, Any] | None,
    field_name: str | None = None,
    source_name: str | None = None,
) -> str:
    payload_mapping = dict(payload or {})
    current_summary = normalize_human_summary(payload_mapping.get("human_summary"))
    if current_summary is not None:
        return current_summary

    normalized_type = _item_type_value(item_type)
    template = _TEMPLATES.get(normalized_type)
    if template is not None:
        summary = normalize_human_summary(
            template(payload_mapping, field_name=field_name, source_name=source_name)
        )
        if summary is not None:
            return summary
    return _default_summary(payload_mapping, item_type=normalized_type, field_name=field_name)


def _new_candidate_summary(
    payload: Mapping[str, Any],
    *,
    field_name: str | None,
    source_name: str | None,
) -> str:
    source = _source_label(payload, source_name=source_name)
    address = _text(payload.get("canonical_address"))
    project_name = _text(_mapping(payload.get("mapped_fields")).get("project_name"))
    subject = address or project_name or "a project"
    return (
        f"{source} reported {subject}; no existing project matched confidently, "
        "so review whether to create a new candidate."
    )


def _possible_match_summary(
    payload: Mapping[str, Any],
    *,
    field_name: str | None,
    source_name: str | None,
) -> str:
    source = _source_label(payload, source_name=source_name)
    address = _text(payload.get("canonical_address")) or "this project"
    candidate_summaries = _mapping_list(payload.get("candidate_summaries"))
    candidate_count = len(candidate_summaries) or len(_candidate_project_ids(payload))
    candidate_text = (
        f"{candidate_count} possible existing projects"
        if candidate_count > 1
        else "a possible existing project"
    )
    lean_text = _possible_match_lean(candidate_summaries, payload=payload)
    if lean_text is not None:
        return (
            f"{source} reported {address}; the matcher found {candidate_text}, "
            f"{lean_text}, but confirm before attaching the evidence."
        )
    return (
        f"{source} reported {address}; the matcher found {candidate_text}, "
        "so confirm the right match before attaching the evidence."
    )


def _news_status_uncorroborated_summary(
    payload: Mapping[str, Any],
    *,
    field_name: str | None,
    source_name: str | None,
) -> str:
    source = _source_label(payload, source_name=source_name)
    label = _field_label(field_name or _text(payload.get("field_name")) or "pipeline_status")
    current = _format_value(payload.get("current_value"))
    proposed = _format_value(payload.get("proposed_value"))
    return (
        f"{source} suggests {label} should move from {current} to {proposed}, "
        "but corroboration is still needed; verify before applying."
    )


def _status_change_summary(
    payload: Mapping[str, Any],
    *,
    field_name: str | None,
    source_name: str | None,
) -> str:
    source = _source_label(payload, source_name=source_name)
    label = _field_label(field_name or _field_name_from_payload(payload) or "field")
    current = _format_value(_current_value(payload, field_name=field_name))
    proposed = _format_value(_proposed_value(payload, field_name=field_name))
    return (
        f"{source} suggests {label} should change from {current} to {proposed}; "
        "review before applying."
    )


def _override_contradiction_summary(
    payload: Mapping[str, Any],
    *,
    field_name: str | None,
    source_name: str | None,
) -> str:
    source = _source_label(payload, source_name=source_name)
    label = _field_label(field_name or _field_name_from_payload(payload) or "field")
    current = _format_value(_current_value(payload, field_name=field_name))
    proposed = _format_value(_proposed_value(payload, field_name=field_name))
    return (
        f"{source} suggests {label} should be {proposed}, which conflicts with the "
        f"active override at {current}; review whether to keep the override or accept "
        "the new signal."
    )


def _semantic_review_summary(
    payload: Mapping[str, Any],
    *,
    field_name: str | None,
    source_name: str | None,
) -> str:
    source = _source_label(payload, source_name=source_name)
    label = _field_label(field_name or _field_name_from_payload(payload) or "field")
    proposed = _format_value(_proposed_value(payload, field_name=field_name))
    reason = _text(payload.get("reason_label")) or "a source signal"
    return (
        f"{source} flags {reason} and suggests {label} should be {proposed}; "
        "verify before applying."
    )


def _default_summary(
    payload: Mapping[str, Any],
    *,
    item_type: str,
    field_name: str | None,
) -> str:
    label = _field_label(field_name or _field_name_from_payload(payload) or item_type)
    return f"{label} changed"


def _source_label(payload: Mapping[str, Any], *, source_name: str | None) -> str:
    news_context = _mapping(payload.get("news_context"))
    title = _text(news_context.get("article_title"))
    published_at = _date_label(news_context.get("published_at"))
    source = (
        _text(source_name)
        or _text(payload.get("source_name"))
        or _text(news_context.get("source_name"))
    )
    if source is not None and published_at is not None:
        return f"{source} ({published_at})"
    if source is not None:
        return source
    if title is not None and published_at is not None:
        return f'Article "{title}" ({published_at})'
    if title is not None:
        return f'Article "{title}"'
    return "The source"


def _candidate_project_ids(payload: Mapping[str, Any]) -> list[str]:
    match = _mapping(payload.get("match"))
    values = match.get("candidate_project_ids") or payload.get("candidate_project_ids")
    if not isinstance(values, list):
        return []
    return [value for item in values if (value := _text(item)) is not None]


def _possible_match_lean(
    candidate_summaries: list[Mapping[str, Any]],
    *,
    payload: Mapping[str, Any],
) -> str | None:
    if not candidate_summaries:
        return None
    candidate = candidate_summaries[0]
    label = _candidate_label(candidate)
    if label is None:
        return None
    factors = _candidate_lean_factors(candidate, payload=payload)
    if not factors:
        score = _format_score(candidate.get("score"))
        if score is None:
            return f"leaning toward {label}"
        return f"leaning toward {label} because it has the highest matcher score ({score})"
    return f"leaning toward {label} because {_join_factors(factors[:3])}"


def _candidate_label(candidate: Mapping[str, Any]) -> str | None:
    return (
        _text(candidate.get("label"))
        or _text(candidate.get("project_name"))
        or _text(candidate.get("canonical_address"))
        or _short_project_id(candidate.get("project_id"))
    )


def _candidate_lean_factors(
    candidate: Mapping[str, Any],
    *,
    payload: Mapping[str, Any],
) -> list[str]:
    mapped_fields = _mapping(payload.get("mapped_fields"))
    factors: list[str] = []
    reason_values = {
        reason
        for raw_reason in _list_values(candidate.get("reasons"))
        if (reason := _text(raw_reason)) is not None
    }
    if reason_values.intersection({"exact_address", "zip_tolerant_address"}):
        factors.append("address match")
    elif "coordinates_within_75m" in reason_values:
        factors.append("nearby coordinates")
    if "project_name_fuzzy" in reason_values:
        ratio = _format_score(candidate.get("project_name_ratio"))
        factors.append(f"project-name similarity ({ratio})" if ratio else "project-name similarity")
    if "identifier_match" in reason_values:
        factors.append("identifier match")
    if "developer_canonical" in reason_values:
        factors.append("developer match")
    if "neighborhood" in reason_values:
        factors.append("neighborhood match")

    source_units = mapped_fields.get("total_units")
    candidate_units = candidate.get("total_units")
    if (
        "unit_total_within_25pct" in reason_values
        or _numbers_within_pct(source_units, candidate_units, pct=0.25)
    ):
        if source_units not in (None, "") and candidate_units not in (None, ""):
            factors.append(f"unit count (source says {source_units}, TCG has {candidate_units})")
        else:
            factors.append("unit-count fit")

    for field_name, label in (
        ("product_type", "product type"),
        ("rent_or_sale", "rent/sale fit"),
    ):
        source_value = _text(mapped_fields.get(field_name))
        candidate_value = _text(candidate.get(field_name))
        if (
            source_value is not None
            and candidate_value is not None
            and source_value == candidate_value
        ):
            factors.append(f"{label} ({source_value})" if field_name == "product_type" else label)

    if "stories_within_one" in reason_values:
        factors.append("story count")
    return _dedupe_text(factors)


def _join_factors(factors: list[str]) -> str:
    if len(factors) <= 1:
        return factors[0] if factors else ""
    if len(factors) == 2:
        return f"{factors[0]} and {factors[1]}"
    return f"{', '.join(factors[:-1])}, and {factors[-1]}"


def _numbers_within_pct(left: Any, right: Any, *, pct: float) -> bool:
    try:
        left_number = float(left)
        right_number = float(right)
    except (TypeError, ValueError):
        return False
    if left_number == right_number:
        return True
    denominator = max(abs(left_number), abs(right_number), 1.0)
    return abs(left_number - right_number) / denominator <= pct


def _format_score(value: Any) -> str | None:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if score <= 1:
        return f"{score * 100:.0f}%"
    return f"{score:.0f}%"


def _short_project_id(value: Any) -> str | None:
    text = _text(value)
    if text is None:
        return None
    return f"project {text[:8]}"


def _field_name_from_payload(payload: Mapping[str, Any]) -> str | None:
    field_name = _text(payload.get("field_name"))
    if field_name is not None:
        return field_name
    if _mapping(payload.get("status_suggestion")):
        return "pipeline_status"
    for change in _mapping_list(payload.get("changes")):
        field_name = _text(change.get("field") or change.get("field_name"))
        if field_name is not None:
            return field_name
    current_override = _mapping(payload.get("current_override"))
    return _text(current_override.get("field_name"))


def _current_value(payload: Mapping[str, Any], *, field_name: str | None) -> Any:
    current_override = _mapping(payload.get("current_override"))
    if "value" in current_override:
        return current_override.get("value")
    if "current_value" in payload:
        return payload.get("current_value")
    status_suggestion = _mapping(payload.get("status_suggestion"))
    if "current_status" in status_suggestion:
        return status_suggestion.get("current_status")
    for change in _mapping_list(payload.get("changes")):
        if _change_matches_field(change, field_name) and "old_value" in change:
            return change.get("old_value")
    return None


def _proposed_value(payload: Mapping[str, Any], *, field_name: str | None) -> Any:
    if "proposed_value" in payload:
        return payload.get("proposed_value")
    alternatives = _mapping_list(payload.get("proposed_alternatives"))
    if alternatives and "value" in alternatives[0]:
        return alternatives[0].get("value")
    candidate = _mapping(payload.get("candidate"))
    if "value" in candidate:
        return candidate.get("value")
    status_suggestion = _mapping(payload.get("status_suggestion"))
    if "suggested_status" in status_suggestion:
        return status_suggestion.get("suggested_status")
    for change in _mapping_list(payload.get("changes")):
        if _change_matches_field(change, field_name) and "new_value" in change:
            return change.get("new_value")
    mapped_fields = _mapping(payload.get("mapped_fields"))
    if field_name is not None and field_name in mapped_fields:
        return mapped_fields.get(field_name)
    return payload.get("canonical_address")


def _change_matches_field(change: Mapping[str, Any], field_name: str | None) -> bool:
    return (
        field_name is None
        or change.get("field") == field_name
        or change.get("field_name") == field_name
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _mapping_list(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _list_values(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dedupe_text(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = value.strip()
        if not text or text in seen:
            continue
        deduped.append(text)
        seen.add(text)
    return deduped


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _format_value(value: Any) -> str:
    if value is None or value == "":
        return "unknown"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _date_label(value: Any) -> str | None:
    text = _text(value)
    if text is None:
        return None
    return text[:10] if len(text) >= 10 else text


def _field_label(value: str) -> str:
    return " ".join(part.capitalize() for part in re.split(r"[_.-]+", value) if part)


def _item_type_value(item_type: ReviewItemType | str) -> str:
    return item_type.value if isinstance(item_type, ReviewItemType) else str(item_type)


_TEMPLATES = {
    ReviewItemType.NEW_CANDIDATE.value: _new_candidate_summary,
    ReviewItemType.POSSIBLE_MATCH.value: _possible_match_summary,
    ReviewItemType.NEWS_STATUS_UNCORROBORATED.value: _news_status_uncorroborated_summary,
    ReviewItemType.STATUS_CHANGE.value: _status_change_summary,
    ReviewItemType.OVERRIDE_CONTRADICTION.value: _override_contradiction_summary,
    ReviewItemType.MULTI_TENURE_REVIEW.value: _semantic_review_summary,
    ReviewItemType.PROJECT_CANCELLATION_REVIEW.value: _semantic_review_summary,
}

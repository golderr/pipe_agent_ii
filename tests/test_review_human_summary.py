from __future__ import annotations

import pytest

from tcg_pipeline.db.models import ReviewItemType
from tcg_pipeline.review.human_summary import (
    human_summary_for_payload,
    normalize_human_summary,
    payload_with_human_summary,
)


def test_normalize_human_summary_rejects_blank_html_and_overlong() -> None:
    assert normalize_human_summary("  A concise summary.  ") == "A concise summary."
    assert normalize_human_summary("Line one\nline two") == "Line one line two"
    assert normalize_human_summary("") is None
    assert normalize_human_summary("<b>unsafe</b>") is None
    assert normalize_human_summary("x" * 501) is None


def test_agent_summary_takes_precedence() -> None:
    payload = payload_with_human_summary(
        {"canonical_address": "100 Main St"},
        item_type=ReviewItemType.NEW_CANDIDATE,
        agent_revised_verdict={
            "decision": "no_change",
            "human_summary": "Urbanize reports 100 Main; review whether to create a new candidate.",
        },
    )

    assert (
        payload["human_summary"]
        == "Urbanize reports 100 Main; review whether to create a new candidate."
    )


def test_existing_summary_is_preserved_on_refresh() -> None:
    payload = payload_with_human_summary(
        {"canonical_address": "200 Main St"},
        item_type=ReviewItemType.NEW_CANDIDATE,
        existing_payload={"human_summary": "Original summary stays stable."},
        agent_revised_verdict={"human_summary": "New summary should not replace it."},
    )

    assert payload["human_summary"] == "Original summary stays stable."


@pytest.mark.parametrize(
    ("item_type", "field_name", "payload", "expected_fragments"),
    [
        (
            ReviewItemType.NEW_CANDIDATE,
            None,
            {
                "source_name": "LADBS",
                "canonical_address": "100 Main St",
            },
            ("LADBS reported 100 Main St", "create a new candidate"),
        ),
        (
            ReviewItemType.POSSIBLE_MATCH,
            None,
            {
                "source_name": "Urbanize LA",
                "canonical_address": "123 Main St",
                "mapped_fields": {
                    "total_units": 85,
                    "product_type": "Apartment",
                    "rent_or_sale": "Rental",
                },
                "candidate_summaries": [
                    {
                        "project_id": "11111111-1111-1111-1111-111111111111",
                        "label": "Main Street Tower",
                        "canonical_address": "123 Main St",
                        "total_units": 83,
                        "product_type": "Apartment",
                        "rent_or_sale": "Rental",
                        "score": 0.91,
                        "reasons": [
                            "exact_address",
                            "unit_total_within_25pct",
                            "product_type",
                        ],
                    },
                    {
                        "project_id": "22222222-2222-2222-2222-222222222222",
                        "label": "125 Main Phase",
                        "score": 0.7,
                        "reasons": ["neighborhood"],
                    },
                ],
            },
            (
                "Urbanize LA reported 123 Main St",
                "2 possible existing projects",
                "leaning toward Main Street Tower",
                "address match",
                "unit count (source says 85, TCG has 83)",
                "product type (Apartment)",
            ),
        ),
        (
            ReviewItemType.NEWS_STATUS_UNCORROBORATED,
            "pipeline_status",
            {
                "current_value": "Approved",
                "proposed_value": "Under Construction",
                "news_context": {
                    "source_name": "Urbanize LA",
                    "published_at": "2026-04-29T12:00:00+00:00",
                },
            },
            (
                "Urbanize LA (2026-04-29)",
                "Pipeline Status should move from Approved to Under Construction",
                "corroboration is still needed",
            ),
        ),
        (
            ReviewItemType.STATUS_CHANGE,
            "total_units",
            {
                "source_name": "CoStar",
                "current_value": 120,
                "proposed_value": 155,
            },
            (
                "CoStar suggests Total Units should change from 120 to 155",
                "review before applying",
            ),
        ),
        (
            ReviewItemType.OVERRIDE_CONTRADICTION,
            "pipeline_status",
            {
                "source_name": "Urbanize LA",
                "current_override": {"field_name": "pipeline_status", "value": "Approved"},
                "candidate": {"value": "Under Construction"},
            },
            (
                "Urbanize LA suggests Pipeline Status should be Under Construction",
                "conflicts with the active override at Approved",
            ),
        ),
        (
            ReviewItemType.MULTI_TENURE_REVIEW,
            "rent_or_sale",
            {
                "source_name": "Semantic Pass 2c",
                "reason_label": "mixed rental and for-sale language",
                "proposed_value": "Both",
            },
            (
                "Semantic Pass 2c flags mixed rental and for-sale language",
                "Rent Or Sale should be Both",
            ),
        ),
        (
            ReviewItemType.PROJECT_CANCELLATION_REVIEW,
            "pipeline_status",
            {
                "source_name": "Semantic Pass 2c",
                "reason_label": "project cancellation language",
                "proposed_value": "Inactive",
            },
            (
                "Semantic Pass 2c flags project cancellation language",
                "Pipeline Status should be Inactive",
            ),
        ),
    ],
)
def test_registered_templates_render_representative_payloads(
    item_type: ReviewItemType,
    field_name: str | None,
    payload: dict,
    expected_fragments: tuple[str, ...],
) -> None:
    summary = human_summary_for_payload(
        item_type=item_type,
        field_name=field_name,
        payload=payload,
    )

    for fragment in expected_fragments:
        assert fragment in summary


def test_bad_agent_summary_falls_back_to_template() -> None:
    payload = payload_with_human_summary(
        {
            "source_name": "LADBS",
            "canonical_address": "100 Main St",
        },
        item_type=ReviewItemType.NEW_CANDIDATE,
        agent_revised_verdict={
            "decision": "no_change",
            "human_summary": "<b>unsafe</b>",
        },
    )

    assert payload["human_summary"] == (
        "LADBS reported 100 Main St; no existing project matched confidently, "
        "so review whether to create a new candidate."
    )


def test_registered_template_renders_news_status_uncorroborated() -> None:
    summary = human_summary_for_payload(
        item_type=ReviewItemType.NEWS_STATUS_UNCORROBORATED,
        field_name="pipeline_status",
        payload={
            "current_value": "Approved",
            "proposed_value": "Under Construction",
            "news_context": {
                "source_name": "Urbanize LA",
                "published_at": "2026-04-29T12:00:00+00:00",
            },
        },
    )

    assert summary == (
        "Urbanize LA (2026-04-29) suggests Pipeline Status should move from Approved "
        "to Under Construction, but corroboration is still needed; verify before applying."
    )


def test_default_template_uses_title_pattern() -> None:
    summary = human_summary_for_payload(
        item_type="unknown_type",
        field_name="total_units",
        payload={},
    )

    assert summary == "Total Units changed"

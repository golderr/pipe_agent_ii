from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from tcg_pipeline.db.models import (
    AgeRestriction,
    Evidence,
    PipelineStatus,
    ProductType,
    Project,
)
from tcg_pipeline.resolution.fields import (
    SYSTEM_STATUS_REGRESSION_OVERRIDE_ACTOR,
    iter_field_observations,
    sort_observations,
)
from tcg_pipeline.resolution.fields.age_restriction import resolve_age_restriction
from tcg_pipeline.resolution.fields.delivery_year import resolve_delivery_year
from tcg_pipeline.resolution.fields.developer import resolve_developer
from tcg_pipeline.resolution.fields.product_type import resolve_product_type
from tcg_pipeline.resolution.fields.status import resolve_status
from tcg_pipeline.resolution.fields.units import resolve_unit_split, resolve_units


def _build_project() -> Project:
    return Project(
        canonical_address="123 MAIN STREET LOS ANGELES CA 90012",
        raw_addresses=["123 Main St"],
        market="los_angeles",
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
    )


def _build_evidence(
    *,
    source_type: str,
    source_tier: int,
    evidence_date: date,
    extracted_fields: dict[str, dict[str, object]],
    collected_at: datetime | None = None,
    raw_data: dict[str, object] | None = None,
) -> Evidence:
    return Evidence(
        id=uuid.uuid4(),
        source_type=source_type,
        source_tier=source_tier,
        ingest_method="seed_import",
        collected_at=collected_at
        or datetime.combine(evidence_date, datetime.min.time(), tzinfo=UTC),
        evidence_date=evidence_date,
        extracted_fields=extracted_fields,
        raw_data=raw_data,
    )


def test_resolve_status_promotes_to_under_construction_from_permit_and_inspection() -> None:
    project = _build_project()
    project.pipeline_status = PipelineStatus.APPROVED

    permit_evidence = _build_evidence(
        source_type="ladbs_permit",
        source_tier=1,
        evidence_date=date(2026, 3, 15),
        extracted_fields={
            "status_evidence_type": {"value": "building_permit_issued", "confidence": None},
        },
    )
    inspection_evidence = _build_evidence(
        source_type="ladbs_inspection",
        source_tier=1,
        evidence_date=date(2026, 4, 10),
        extracted_fields={
            "status_evidence_type": {
                "value": "building_inspection_recorded",
                "confidence": None,
            },
        },
    )

    resolution = resolve_status([permit_evidence, inspection_evidence], project)

    assert resolution.value == PipelineStatus.UNDER_CONSTRUCTION
    assert resolution.confidence.value == "high"


def test_resolve_status_accepts_semantic_status_slug() -> None:
    project = _build_project()
    project.pipeline_status = PipelineStatus.PROPOSED
    news_evidence = _build_evidence(
        source_type="news_article",
        source_tier=2,
        evidence_date=date(2026, 5, 7),
        extracted_fields={
            "pipeline_status": {
                "value": "under_construction",
                "confidence": "high",
                "semantic": {
                    "reason_code": "news_topped_out",
                    "promotes_status_alone": True,
                },
            },
        },
    )

    resolution = resolve_status([news_evidence], project)

    assert resolution.value == PipelineStatus.UNDER_CONSTRUCTION
    assert resolution.rule_applied == "highest_status_wins"


def test_resolve_units_uses_most_recent_evidence() -> None:
    project = _build_project()
    older = _build_evidence(
        source_type="costar",
        source_tier=3,
        evidence_date=date(2025, 12, 1),
        extracted_fields={"total_units": {"value": 200, "confidence": None}},
    )
    newer = _build_evidence(
        source_type="pipedream",
        source_tier=1,
        evidence_date=date(2026, 3, 1),
        extracted_fields={"total_units": {"value": 240, "confidence": None}},
    )

    resolution = resolve_units([older, newer], project, "total_units")

    assert resolution.value == 240


def test_resolve_delivery_year_estimates_midyear_when_explicit_date_missing() -> None:
    project = _build_project()

    resolution = resolve_delivery_year(
        [],
        project,
        resolved_status=PipelineStatus.PROPOSED,
        resolved_total_units=600,
    )

    expected_year = date.today().year + 7
    assert resolution.value == date(expected_year, 7, 1)
    assert resolution.metadata["provenance"] == "estimated_calc"
    assert resolution.metadata["delivery_date_type"] == "estimated_calc"
    assert "Estimated delivery date" in resolution.metadata["description"]


def test_resolve_delivery_year_prefers_recent_news_over_costar() -> None:
    project = _build_project()
    today = date.today()
    costar_evidence = _build_evidence(
        source_type="costar",
        source_tier=3,
        evidence_date=today,
        extracted_fields={"date_delivery": {"value": "2030-01-01", "confidence": None}},
    )
    news_evidence = _build_evidence(
        source_type="news_article",
        source_tier=2,
        evidence_date=today - timedelta(days=30),
        extracted_fields={"date_delivery": {"value": "2028-07-01", "confidence": None}},
    )

    resolution = resolve_delivery_year(
        [costar_evidence, news_evidence],
        project,
        resolved_status=PipelineStatus.APPROVED,
        resolved_total_units=400,
    )

    assert resolution.value == date(2028, 7, 1)
    assert resolution.metadata["provenance"] == "explicit_news"
    assert resolution.evidence_ids == [news_evidence.id]


def test_resolve_delivery_year_does_not_prefer_stale_news_over_costar() -> None:
    project = _build_project()
    today = date.today()
    costar_evidence = _build_evidence(
        source_type="costar",
        source_tier=3,
        evidence_date=today,
        extracted_fields={"date_delivery": {"value": "2030-01-01", "confidence": None}},
    )
    stale_news = _build_evidence(
        source_type="news_article",
        source_tier=2,
        evidence_date=today - timedelta(days=365),
        extracted_fields={"date_delivery": {"value": "2028-07-01", "confidence": None}},
    )

    resolution = resolve_delivery_year(
        [costar_evidence, stale_news],
        project,
        resolved_status=PipelineStatus.APPROVED,
        resolved_total_units=400,
    )

    assert resolution.value == date(2030, 1, 1)
    assert resolution.metadata["provenance"] == "explicit_costar"
    assert resolution.evidence_ids == [costar_evidence.id]


def test_resolve_delivery_year_keeps_tcg_evidence_over_recent_news() -> None:
    project = _build_project()
    today = date.today()
    pipedream_evidence = _build_evidence(
        source_type="pipedream",
        source_tier=1,
        evidence_date=today,
        extracted_fields={"date_delivery": {"value": "2029-01-01", "confidence": None}},
    )
    news_evidence = _build_evidence(
        source_type="news_article",
        source_tier=2,
        evidence_date=today,
        collected_at=datetime.combine(today, datetime.min.time(), tzinfo=UTC),
        extracted_fields={"date_delivery": {"value": "2028-07-01", "confidence": None}},
    )

    resolution = resolve_delivery_year(
        [news_evidence, pipedream_evidence],
        project,
        resolved_status=PipelineStatus.APPROVED,
        resolved_total_units=400,
    )

    assert resolution.value == date(2029, 1, 1)
    assert resolution.metadata["provenance"] == "explicit_tcg"
    assert resolution.evidence_ids == [pipedream_evidence.id]


def test_resolve_delivery_year_for_complete_prefers_past_recent_news_over_costar() -> None:
    project = _build_project()
    today = date.today()
    costar_evidence = _build_evidence(
        source_type="costar",
        source_tier=3,
        evidence_date=today,
        extracted_fields={
            "date_delivery": {
                "value": (today - timedelta(days=120)).isoformat(),
                "confidence": None,
            }
        },
    )
    news_evidence = _build_evidence(
        source_type="news_article",
        source_tier=2,
        evidence_date=today - timedelta(days=30),
        extracted_fields={
            "date_delivery": {
                "value": (today - timedelta(days=90)).isoformat(),
                "confidence": None,
            }
        },
    )

    resolution = resolve_delivery_year(
        [costar_evidence, news_evidence],
        project,
        resolved_status=PipelineStatus.COMPLETE,
        resolved_total_units=400,
    )

    assert resolution.value == today - timedelta(days=90)
    assert resolution.metadata["provenance"] == "explicit_news"
    assert resolution.evidence_ids == [news_evidence.id]


def test_resolve_developer_prefers_pipedream_when_dates_tie() -> None:
    project = _build_project()
    same_day = date(2026, 4, 1)
    pipedream_evidence = _build_evidence(
        source_type="pipedream",
        source_tier=1,
        evidence_date=same_day,
        extracted_fields={"developer": {"value": "TCG Research", "confidence": None}},
    )
    costar_evidence = _build_evidence(
        source_type="costar",
        source_tier=3,
        evidence_date=same_day,
        extracted_fields={"developer": {"value": "Different Dev", "confidence": None}},
    )

    resolution = resolve_developer([costar_evidence, pipedream_evidence], project)

    assert resolution.value == "TCG Research"


def test_resolve_developer_prefers_newer_evidence_over_source_priority() -> None:
    project = _build_project()
    older_pipedream = _build_evidence(
        source_type="pipedream",
        source_tier=1,
        evidence_date=date(2020, 4, 1),
        extracted_fields={"developer": {"value": "Old TCG Research", "confidence": None}},
    )
    newer_news = _build_evidence(
        source_type="news_article",
        source_tier=2,
        evidence_date=date(2026, 4, 1),
        extracted_fields={"developer": {"value": "Newer News Dev", "confidence": None}},
    )

    resolution = resolve_developer([older_pipedream, newer_news], project)

    assert resolution.value == "Newer News Dev"


def test_resolve_developer_does_not_treat_future_projection_as_freshness() -> None:
    project = _build_project()
    pipedream_evidence = _build_evidence(
        source_type="pipedream",
        source_tier=1,
        evidence_date=date(2026, 4, 20),
        collected_at=datetime(2026, 4, 20, 12, 0, tzinfo=UTC),
        extracted_fields={"developer": {"value": "Researcher Developer", "confidence": None}},
    )
    costar_evidence = _build_evidence(
        source_type="costar",
        source_tier=3,
        evidence_date=date(2029, 1, 1),
        collected_at=datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
        extracted_fields={"developer": {"value": "Projected Future Dev", "confidence": None}},
    )

    resolution = resolve_developer([costar_evidence, pipedream_evidence], project)

    assert resolution.value == "Researcher Developer"


def test_resolve_developer_override_records_higher_priority_candidate_on_temporal_tie() -> None:
    project = _build_project()
    project.developer = "Current Dev"
    tied_timestamp = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)
    overrides = {
        "developer": {
            "value": "Reviewer Dev",
            "set_by": "nate",
            "set_at": "2026-04-22T10:00:00Z",
            "note": "Manual correction.",
            "mode": "until_newer_evidence",
            "baseline": {
                "evidence_date": "2026-04-01",
                "collected_at": "2026-04-01T12:00:00+00:00",
                "source_tier": 1,
                "source_type": "ladbs_permit",
                "evidence_ids": [],
                "rule_applied": "most_recent_wins",
            },
        }
    }
    tied_news_evidence = _build_evidence(
        source_type="news_article",
        source_tier=2,
        evidence_date=date(2026, 4, 1),
        collected_at=tied_timestamp,
        extracted_fields={"developer": {"value": "News Dev", "confidence": None}},
    )

    resolution = resolve_developer(
        [tied_news_evidence],
        project,
        overrides=overrides,
    )

    assert resolution.value == "Reviewer Dev"
    assert resolution.rule_applied == "researcher_override_until_newer_evidence"
    assert resolution.metadata["candidate_value"] == "News Dev"
    assert resolution.metadata["candidate_is_newer_than_baseline"] is True
    assert "override_superseded" not in resolution.metadata


def test_sort_observations_prefers_collection_time_before_source_priority() -> None:
    same_day = date(2026, 4, 1)
    earlier_pipedream = _build_evidence(
        source_type="pipedream",
        source_tier=1,
        evidence_date=same_day,
        collected_at=datetime(2026, 4, 1, 9, 0, tzinfo=UTC),
        extracted_fields={"developer": {"value": "Earlier Pipedream Dev", "confidence": None}},
    )
    later_news = _build_evidence(
        source_type="news_article",
        source_tier=2,
        evidence_date=same_day,
        collected_at=datetime(2026, 4, 1, 12, 0, tzinfo=UTC),
        extracted_fields={"developer": {"value": "Later News Dev", "confidence": None}},
    )

    observations = sort_observations(
        [
            *iter_field_observations([earlier_pipedream], "developer"),
            *iter_field_observations([later_news], "developer"),
        ],
        source_priority={
            "pipedream": 0,
            "news_article": 1,
        },
    )

    assert observations[0].value == "Later News Dev"


def test_resolve_age_restriction_defaults_to_unknown_without_explicit_evidence() -> None:
    project = _build_project()

    resolution = resolve_age_restriction([], project)

    assert resolution.value == AgeRestriction.UNKNOWN


def test_resolve_age_restriction_keeps_current_value_without_explicit_evidence() -> None:
    project = _build_project()
    project.age_restriction = AgeRestriction.NON_AGE_RESTRICTED

    resolution = resolve_age_restriction([], project)

    assert resolution.value == AgeRestriction.NON_AGE_RESTRICTED
    assert resolution.rule_applied == "no_age_restriction_evidence_keep_current"


def test_resolve_status_marks_permit_alone_as_review_required() -> None:
    project = _build_project()
    project.pipeline_status = PipelineStatus.PROPOSED
    permit_evidence = _build_evidence(
        source_type="ladbs_permit",
        source_tier=1,
        evidence_date=date(2026, 3, 15),
        extracted_fields={
            "status_evidence_type": {"value": "building_permit_issued", "confidence": None},
        },
    )

    resolution = resolve_status([permit_evidence], project)

    assert resolution.value == PipelineStatus.APPROVED
    assert resolution.metadata["requires_review"] is True
    assert "requires researcher review" in resolution.metadata["review_reason"]


def test_resolve_unit_split_ignores_disallowed_sources() -> None:
    project = _build_project()
    project.affordable_units = 18
    costar_evidence = _build_evidence(
        source_type="costar",
        source_tier=3,
        evidence_date=date(2026, 4, 1),
        extracted_fields={"affordable_units": {"value": 25, "confidence": None}},
    )

    resolution = resolve_unit_split([costar_evidence], project, "affordable_units")

    assert resolution.value == 18
    assert resolution.rule_applied == "no_allowed_split_evidence"


def test_resolve_unit_split_accepts_workforce_units_from_allowed_source() -> None:
    project = _build_project()
    news_evidence = _build_evidence(
        source_type="news_article",
        source_tier=2,
        evidence_date=date(2026, 5, 1),
        extracted_fields={"workforce_units": {"value": 12, "confidence": "high"}},
    )

    resolution = resolve_unit_split([news_evidence], project, "workforce_units")

    assert resolution.value == 12
    assert resolution.rule_applied == "most_recent_allowed_split_source_wins"


def test_resolve_product_type_uses_most_recent_explicit_value() -> None:
    project = _build_project()
    project.product_type = ProductType.UNKNOWN
    older = _build_evidence(
        source_type="costar",
        source_tier=3,
        evidence_date=date(2025, 1, 1),
        extracted_fields={"product_type": {"value": ProductType.CONDO.value, "confidence": None}},
    )
    newer = _build_evidence(
        source_type="pipedream",
        source_tier=1,
        evidence_date=date(2026, 1, 1),
        extracted_fields={
            "product_type": {"value": ProductType.APARTMENT.value, "confidence": None}
        },
    )

    resolution = resolve_product_type([older, newer], project)

    assert resolution.value == ProductType.APARTMENT


def test_resolve_product_type_keeps_current_value_without_evidence() -> None:
    project = _build_project()
    project.product_type = ProductType.APARTMENT

    resolution = resolve_product_type([], project)

    assert resolution.value == ProductType.APARTMENT
    assert resolution.rule_applied == "no_product_type_evidence_keep_current"


def test_resolve_developer_keeps_current_value_without_evidence() -> None:
    project = _build_project()
    project.developer = "Jamison Services"

    resolution = resolve_developer([], project)

    assert resolution.value == "Jamison Services"
    assert resolution.rule_applied == "no_developer_evidence_keep_current"


def test_resolve_delivery_year_keeps_existing_project_value_without_explicit_evidence() -> None:
    project = _build_project()
    project.date_delivery = date(2028, 6, 1)

    resolution = resolve_delivery_year(
        [],
        project,
        resolved_status=PipelineStatus.APPROVED,
        resolved_total_units=400,
    )

    assert resolution.value == date(2028, 6, 1)
    assert resolution.rule_applied == "no_explicit_delivery_evidence_keep_current"


def test_resolve_delivery_year_override_sets_researcher_override_provenance() -> None:
    project = _build_project()
    project.date_delivery = date(2028, 6, 1)
    overrides = {
        "date_delivery": {
            "value": "2029-01-01",
            "set_by": "nate",
            "set_at": "2026-04-22T11:00:00Z",
            "note": "Developer confirmed revised delivery.",
            "mode": "until_newer_evidence",
            "baseline": {
                "evidence_date": "2026-04-01",
                "collected_at": "2026-04-01T00:00:00+00:00",
                "source_tier": 1,
                "source_type": "pipedream",
                "evidence_ids": [],
                "rule_applied": "explicit_delivery_date",
            },
        }
    }

    resolution = resolve_delivery_year(
        [],
        project,
        resolved_status=PipelineStatus.APPROVED,
        resolved_total_units=400,
        overrides=overrides,
    )

    assert resolution.value == date(2029, 1, 1)
    assert resolution.metadata["provenance"] == "researcher_override"
    assert resolution.metadata["delivery_date_type"] == "researcher_override"


def test_resolve_delivery_year_override_records_newer_candidate_and_keeps_override() -> None:
    project = _build_project()
    overrides = {
        "date_delivery": {
            "value": "2029-01-01",
            "set_by": "nate",
            "set_at": "2026-04-22T11:00:00Z",
            "note": "Developer confirmed revised delivery.",
            "mode": "until_newer_evidence",
            "baseline": {
                "evidence_date": "2026-04-01",
                "collected_at": "2026-04-01T00:00:00+00:00",
                "source_tier": 2,
                "source_type": "news_article",
                "evidence_ids": [],
                "rule_applied": "explicit_delivery_date",
            },
        }
    }
    newer_evidence = _build_evidence(
        source_type="ladbs_permit",
        source_tier=1,
        evidence_date=date(2026, 5, 1),
        extracted_fields={"date_delivery": {"value": "2030-06-01", "confidence": None}},
    )

    resolution = resolve_delivery_year(
        [newer_evidence],
        project,
        resolved_status=PipelineStatus.APPROVED,
        resolved_total_units=400,
        overrides=overrides,
    )

    assert resolution.value == date(2029, 1, 1)
    assert resolution.rule_applied == "researcher_override_until_newer_evidence"
    assert resolution.metadata["candidate_value"] == "2030-06-01"
    assert resolution.metadata["candidate_is_newer_than_baseline"] is True
    assert resolution.metadata["provenance"] == "researcher_override"
    assert resolution.metadata["delivery_date_type"] == "researcher_override"
    assert "override_superseded" not in resolution.metadata


def test_resolve_delivery_year_for_complete_rejects_future_explicit_date() -> None:
    project = _build_project()
    project.date_delivery = date(2026, 3, 20)
    project.delivery_year_provenance = "explicit_costar"
    future_evidence = _build_evidence(
        source_type="costar",
        source_tier=3,
        evidence_date=date(2026, 4, 20),
        extracted_fields={"date_delivery": {"value": "2028-03-01", "confidence": None}},
    )

    resolution = resolve_delivery_year(
        [future_evidence],
        project,
        resolved_status=PipelineStatus.COMPLETE,
        resolved_total_units=250,
    )

    assert resolution.value == date(2026, 3, 20)
    assert resolution.rule_applied == "complete_reject_future_delivery_keep_current"
    assert resolution.metadata["provenance"] == "explicit_costar"


def test_resolve_delivery_year_for_complete_prefers_non_future_explicit_evidence() -> None:
    project = _build_project()
    future_costar = _build_evidence(
        source_type="costar",
        source_tier=3,
        evidence_date=date(2026, 4, 20),
        extracted_fields={"date_delivery": {"value": "2028-03-01", "confidence": None}},
    )
    past_government = _build_evidence(
        source_type="ladbs_cofo",
        source_tier=1,
        evidence_date=date(2026, 3, 15),
        extracted_fields={"date_delivery": {"value": "2026-03-15", "confidence": None}},
    )

    resolution = resolve_delivery_year(
        [future_costar, past_government],
        project,
        resolved_status=PipelineStatus.COMPLETE,
        resolved_total_units=250,
    )

    assert resolution.value == date(2026, 3, 15)
    assert resolution.rule_applied == "explicit_delivery_date"
    assert resolution.metadata["provenance"] == "explicit_government"


def test_resolve_status_does_not_regress_from_more_advanced_current_status() -> None:
    # Use a cancelled-status permit so the regression candidate path is exercised.
    # Issued/in-force permits on Complete projects are suppressed by
    # regression_filters.is_benign_ladbs_additive_paperwork.
    project = _build_project()
    project.pipeline_status = PipelineStatus.COMPLETE
    permit_evidence = _build_evidence(
        source_type="ladbs_permit",
        source_tier=1,
        evidence_date=date(2026, 3, 15),
        extracted_fields={
            "status_evidence_type": {"value": "building_permit_issued", "confidence": None},
        },
        raw_data={"status_desc": "Cancelled"},
    )

    resolution = resolve_status([permit_evidence], project)

    assert resolution.value == PipelineStatus.COMPLETE
    assert resolution.rule_applied == "terminal_regression_dropped"
    assert resolution.metadata["regression_candidate_count"] == 1
    candidate = resolution.metadata["regression_candidates"][0]
    assert candidate["current_status"] == PipelineStatus.COMPLETE.value
    assert candidate["proposed_status"] == PipelineStatus.APPROVED.value
    assert candidate["current_rank"] == 6
    assert candidate["proposed_rank"] == 3
    assert candidate["rank_delta"] == 3
    assert candidate["evidence_ids"] == [str(permit_evidence.id)]
    assert candidate["evidence_type"] == "building_permit_issued"
    assert candidate["terminal_state_dropped"] is True


def test_resolve_status_emits_regression_candidate_for_lower_ranked_evidence() -> None:
    # A cancelled permit IS a genuine regression signal. The companion
    # test_resolve_status_suppresses_benign_ladbs_additive_paperwork below
    # asserts the inverse — an Issued permit gets suppressed.
    project = _build_project()
    project.pipeline_status = PipelineStatus.UNDER_CONSTRUCTION
    permit_evidence = _build_evidence(
        source_type="ladbs_permit",
        source_tier=1,
        evidence_date=date(2026, 3, 15),
        extracted_fields={
            "status_evidence_type": {"value": "building_permit_issued", "confidence": None},
        },
        raw_data={"status_desc": "Cancelled"},
    )

    resolution = resolve_status([permit_evidence], project)

    assert resolution.value == PipelineStatus.UNDER_CONSTRUCTION
    assert resolution.rule_applied == "forward_only_preserve_current"
    assert resolution.metadata["regression_audit_rule_applied"] == (
        "status_regression_candidate_preserve_current"
    )
    assert resolution.metadata["regression_candidate_count"] == 1
    candidate = resolution.metadata["regression_candidates"][0]
    assert candidate["current_status"] == PipelineStatus.UNDER_CONSTRUCTION.value
    assert candidate["proposed_status"] == PipelineStatus.APPROVED.value
    assert candidate["rank_delta"] == 1
    assert candidate["terminal_state_dropped"] is False


def test_resolve_status_enumerates_lower_ranked_candidate_when_higher_evidence_wins() -> None:
    # Cancelled permit on a UC project (with inspection evidence supporting UC)
    # still emits a regression candidate — the higher-ranked inspection wins
    # for the chosen status but the cancellation is a real lifecycle signal.
    project = _build_project()
    project.pipeline_status = PipelineStatus.UNDER_CONSTRUCTION
    permit_evidence = _build_evidence(
        source_type="ladbs_permit",
        source_tier=1,
        evidence_date=date(2026, 5, 1),
        extracted_fields={
            "status_evidence_type": {"value": "building_permit_issued", "confidence": None},
        },
        raw_data={"status_desc": "Cancelled"},
    )
    inspection_evidence = _build_evidence(
        source_type="ladbs_inspection",
        source_tier=1,
        evidence_date=date(2026, 4, 1),
        extracted_fields={
            "status_evidence_type": {
                "value": "building_inspection_recorded",
                "confidence": None,
            },
        },
    )

    resolution = resolve_status([permit_evidence, inspection_evidence], project)

    assert resolution.value == PipelineStatus.UNDER_CONSTRUCTION
    assert resolution.rule_applied == "highest_status_wins"
    assert resolution.metadata["regression_candidate_count"] == 1
    candidate = resolution.metadata["regression_candidates"][0]
    assert candidate["proposed_status"] == PipelineStatus.APPROVED.value
    assert candidate["evidence_ids"] == [str(permit_evidence.id)]


def test_resolve_status_preserves_current_for_inactive_candidate() -> None:
    project = _build_project()
    project.pipeline_status = PipelineStatus.UNDER_CONSTRUCTION
    inactive_evidence = _build_evidence(
        source_type="costar",
        source_tier=3,
        evidence_date=date(2026, 4, 16),
        extracted_fields={
            "pipeline_status": {"value": PipelineStatus.INACTIVE.value, "confidence": None},
        },
    )

    resolution = resolve_status([inactive_evidence], project)

    assert resolution.value == PipelineStatus.UNDER_CONSTRUCTION
    assert resolution.rule_applied == "manual_status_review_preserve_current"
    assert resolution.metadata["candidate_status"] == PipelineStatus.INACTIVE.value
    assert resolution.metadata["requires_review"] is True


def test_resolve_status_preserves_inactive_until_manual_reactivation() -> None:
    project = _build_project()
    project.pipeline_status = PipelineStatus.INACTIVE
    proposed_evidence = _build_evidence(
        source_type="costar",
        source_tier=3,
        evidence_date=date(2026, 4, 16),
        extracted_fields={
            "pipeline_status": {"value": PipelineStatus.PROPOSED.value, "confidence": None},
        },
    )

    resolution = resolve_status([proposed_evidence], project)

    assert resolution.value == PipelineStatus.INACTIVE
    assert resolution.rule_applied == "manual_status_review_preserve_current"
    assert resolution.metadata["candidate_status"] == PipelineStatus.PROPOSED.value
    assert resolution.metadata["requires_review"] is True


def test_resolve_status_override_holds_until_newer_evidence() -> None:
    project = _build_project()
    project.pipeline_status = PipelineStatus.APPROVED
    overrides = {
        "pipeline_status": {
            "value": PipelineStatus.PROPOSED.value,
            "set_by": "nate",
            "set_at": "2026-04-22T10:00:00Z",
            "note": "Reject permit-only promotion.",
            "mode": "until_newer_evidence",
            "baseline": {
                "evidence_date": "2026-03-15",
                "collected_at": "2026-03-15T00:00:00+00:00",
                "source_tier": 1,
                "source_type": "ladbs_permit",
                "evidence_ids": [],
                "rule_applied": "highest_status_wins",
            },
        }
    }
    permit_evidence = _build_evidence(
        source_type="ladbs_permit",
        source_tier=1,
        evidence_date=date(2026, 3, 15),
        extracted_fields={
            "status_evidence_type": {"value": "building_permit_issued", "confidence": None},
        },
    )

    resolution = resolve_status([permit_evidence], project, overrides=overrides)

    assert resolution.value == PipelineStatus.PROPOSED
    assert resolution.rule_applied == "researcher_override_until_newer_evidence"


def test_resolve_status_override_records_newer_candidate_and_keeps_override() -> None:
    project = _build_project()
    project.pipeline_status = PipelineStatus.APPROVED
    overrides = {
        "pipeline_status": {
            "value": PipelineStatus.PROPOSED.value,
            "set_by": "nate",
            "set_at": "2026-04-22T10:00:00Z",
            "note": "Reject permit-only promotion.",
            "mode": "until_newer_evidence",
            "baseline": {
                "evidence_date": "2026-03-15",
                "collected_at": "2026-03-15T00:00:00+00:00",
                "source_tier": 1,
                "source_type": "ladbs_permit",
                "evidence_ids": [],
                "rule_applied": "highest_status_wins",
            },
        }
    }
    permit_evidence = _build_evidence(
        source_type="ladbs_permit",
        source_tier=1,
        evidence_date=date(2026, 3, 15),
        extracted_fields={
            "status_evidence_type": {"value": "building_permit_issued", "confidence": None},
        },
    )
    inspection_evidence = _build_evidence(
        source_type="ladbs_inspection",
        source_tier=1,
        evidence_date=date(2026, 4, 10),
        extracted_fields={
            "status_evidence_type": {
                "value": "building_inspection_recorded",
                "confidence": None,
            },
        },
    )

    resolution = resolve_status(
        [permit_evidence, inspection_evidence],
        project,
        overrides=overrides,
    )

    assert resolution.value == PipelineStatus.PROPOSED
    assert resolution.rule_applied == "researcher_override_until_newer_evidence"
    assert resolution.metadata["candidate_value"] == PipelineStatus.UNDER_CONSTRUCTION.value
    assert resolution.metadata["candidate_is_newer_than_baseline"] is True
    assert "override_superseded" not in resolution.metadata


def test_resolve_status_system_override_yields_to_newer_evidence() -> None:
    project = _build_project()
    project.pipeline_status = PipelineStatus.APPROVED
    overrides = {
        "pipeline_status": {
            "value": PipelineStatus.APPROVED.value,
            "set_by": SYSTEM_STATUS_REGRESSION_OVERRIDE_ACTOR,
            "set_at": "2026-04-30T12:00:00Z",
            "note": "Agent confirmed a status regression.",
            "mode": "until_newer_evidence",
            "baseline": {
                "evidence_date": "2026-04-30",
                "collected_at": "2026-04-30T12:00:00+00:00",
                "source_tier": 2,
                "source_type": "news_article",
                "evidence_ids": [],
                "rule_applied": "agent_status_regression_candidate",
            },
        }
    }
    inspection_evidence = _build_evidence(
        source_type="ladbs_inspection",
        source_tier=1,
        evidence_date=date(2026, 5, 5),
        extracted_fields={
            "status_evidence_type": {
                "value": "building_inspection_recorded",
                "confidence": None,
            },
        },
    )

    resolution = resolve_status(
        [inspection_evidence],
        project,
        overrides=overrides,
    )

    assert resolution.value == PipelineStatus.UNDER_CONSTRUCTION
    assert resolution.rule_applied == "highest_status_wins"
    assert resolution.metadata["system_override_superseded"] is True
    assert resolution.metadata["superseded_override"]["set_by"] == (
        SYSTEM_STATUS_REGRESSION_OVERRIDE_ACTOR
    )
    assert resolution.metadata["superseded_override"]["value"] == PipelineStatus.APPROVED.value


def test_resolve_status_suppresses_benign_ladbs_additive_paperwork() -> None:
    """A new Issued LADBS permit on an already-UC project is additive
    paperwork, not a regression — no candidate emitted."""
    project = _build_project()
    project.pipeline_status = PipelineStatus.UNDER_CONSTRUCTION
    permit_evidence = _build_evidence(
        source_type="ladbs_permit",
        source_tier=1,
        evidence_date=date(2026, 3, 15),
        extracted_fields={
            "status_evidence_type": {"value": "building_permit_issued", "confidence": None},
        },
        raw_data={"status_desc": "Issued"},
    )

    resolution = resolve_status([permit_evidence], project)

    assert resolution.value == PipelineStatus.UNDER_CONSTRUCTION
    # No active regression candidates — suppression worked.
    assert resolution.metadata.get("regression_candidate_count", 0) == 0
    # But the suppression is recorded in audit metadata so the trail isn't lost.
    suppressed = resolution.metadata.get("suppressed_regression_candidates", [])
    assert len(suppressed) == 1
    assert suppressed[0]["suppression_reason"] == "ladbs_additive_paperwork"
    assert suppressed[0]["proposed_status"] == PipelineStatus.APPROVED.value


def test_resolve_status_suppresses_benign_ladbs_paperwork_on_complete_project() -> None:
    """An Issued permit on a Complete project — still suppressed (additive)
    even though the resolver normally flags Complete-regressions as
    terminal-dropped."""
    project = _build_project()
    project.pipeline_status = PipelineStatus.COMPLETE
    permit_evidence = _build_evidence(
        source_type="ladbs_permit",
        source_tier=1,
        evidence_date=date(2026, 3, 15),
        extracted_fields={
            "status_evidence_type": {"value": "building_permit_issued", "confidence": None},
        },
        raw_data={"status_desc": "Issued"},
    )

    resolution = resolve_status([permit_evidence], project)

    assert resolution.value == PipelineStatus.COMPLETE
    assert resolution.metadata.get("regression_candidate_count", 0) == 0
    suppressed = resolution.metadata.get("suppressed_regression_candidates", [])
    assert len(suppressed) == 1


def test_resolve_status_suppresses_cofo_pending_paperwork() -> None:
    """CofO Pending is also additive — work is winding down but the project
    hasn't moved backwards."""
    project = _build_project()
    project.pipeline_status = PipelineStatus.UNDER_CONSTRUCTION
    permit_evidence = _build_evidence(
        source_type="ladbs_permit",
        source_tier=1,
        evidence_date=date(2026, 3, 15),
        extracted_fields={
            "status_evidence_type": {"value": "building_permit_issued", "confidence": None},
        },
        raw_data={"status_desc": "CofO Pending"},
    )

    resolution = resolve_status([permit_evidence], project)

    assert resolution.metadata.get("regression_candidate_count", 0) == 0


def test_resolve_status_does_not_suppress_news_regression_signals() -> None:
    """The LADBS additive-paperwork filter is scoped to LADBS source family.
    News regressions still emit candidates as before."""
    project = _build_project()
    project.pipeline_status = PipelineStatus.UNDER_CONSTRUCTION
    news_evidence = _build_evidence(
        source_type="news_article",
        source_tier=2,
        evidence_date=date(2026, 3, 15),
        extracted_fields={
            "pipeline_status": {"value": PipelineStatus.APPROVED.value, "confidence": None},
        },
        raw_data={"status_desc": "Issued"},  # status_desc has no effect for news evidence
    )

    resolution = resolve_status([news_evidence], project)

    # News regression IS emitted regardless of any raw_data status_desc value.
    assert resolution.metadata.get("regression_candidate_count", 0) >= 1


def test_resolve_status_unknown_ladbs_status_desc_treated_as_additive() -> None:
    """Fail-additive sentinel: unknown status_desc values are treated as
    additive (no candidate emitted) on the assumption that we'd rather miss
    a regression than spam the queue."""
    project = _build_project()
    project.pipeline_status = PipelineStatus.UNDER_CONSTRUCTION
    permit_evidence = _build_evidence(
        source_type="ladbs_permit",
        source_tier=1,
        evidence_date=date(2026, 3, 15),
        extracted_fields={
            "status_evidence_type": {"value": "building_permit_issued", "confidence": None},
        },
        raw_data={"status_desc": "Something New And Unrecognized"},
    )

    resolution = resolve_status([permit_evidence], project)

    # Unknown values fail-additive — no regression candidate.
    assert resolution.metadata.get("regression_candidate_count", 0) == 0
    suppressed = resolution.metadata.get("suppressed_regression_candidates", [])
    assert len(suppressed) == 1
    pending_alerts = resolution.metadata.get("pending_system_alerts", [])
    assert len(pending_alerts) == 1
    assert pending_alerts[0]["alert_key"] == "ladbs_unknown_permit_status"
    assert pending_alerts[0]["scope"] == {
        "status_desc": "Something New And Unrecognized"
    }



def test_regression_candidate_payload_includes_permit_descriptor_fields() -> None:
    """The regression-candidate payload carries permit_type / permit_number /
    status_desc when the underlying LADBS evidence has them, so downstream
    narrative templates can name the specific permit instead of saying
    'LADBS signal'."""
    project = _build_project()
    project.pipeline_status = PipelineStatus.UNDER_CONSTRUCTION
    permit_evidence = _build_evidence(
        source_type="ladbs_permit",
        source_tier=1,
        evidence_date=date(2026, 3, 15),
        extracted_fields={
            "status_evidence_type": {"value": "building_permit_issued", "confidence": None},
        },
        raw_data={
            "status_desc": "Cancelled",
            "permit_type": "Bldg-New",
            "permit_nbr": "19010-10000-00001",
        },
    )

    resolution = resolve_status([permit_evidence], project)

    candidates = resolution.metadata.get("regression_candidates", [])
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["permit_type"] == "Bldg-New"
    assert candidate["permit_number"] == "19010-10000-00001"
    assert candidate["status_desc"] == "Cancelled"


def test_regression_candidate_payload_uses_pcis_permit_number() -> None:
    project = _build_project()
    project.pipeline_status = PipelineStatus.UNDER_CONSTRUCTION
    permit_evidence = _build_evidence(
        source_type="ladbs_permit",
        source_tier=1,
        evidence_date=date(2026, 3, 15),
        extracted_fields={
            "status_evidence_type": {"value": "building_permit_issued", "confidence": None},
        },
        raw_data={
            "status_desc": "Cancelled",
            "permit_type": "Bldg-New",
            "pcis_permit": "24010-10000-00001",
        },
    )

    resolution = resolve_status([permit_evidence], project)

    candidates = resolution.metadata.get("regression_candidates", [])
    assert len(candidates) == 1
    assert candidates[0]["permit_number"] == "24010-10000-00001"


def test_regression_candidate_payload_falls_back_to_source_record_id() -> None:
    project = _build_project()
    project.pipeline_status = PipelineStatus.UNDER_CONSTRUCTION
    permit_evidence = _build_evidence(
        source_type="ladbs_permit",
        source_tier=1,
        evidence_date=date(2026, 3, 15),
        extracted_fields={
            "status_evidence_type": {"value": "building_permit_issued", "confidence": None},
        },
        raw_data={
            "status_desc": "Cancelled",
            "permit_type": "Bldg-New",
        },
    )
    permit_evidence.source_record_id = "source-record-24010"

    resolution = resolve_status([permit_evidence], project)

    candidates = resolution.metadata.get("regression_candidates", [])
    assert len(candidates) == 1
    assert candidates[0]["permit_number"] == "source-record-24010"


def test_regression_candidate_payload_omits_descriptor_fields_for_non_ladbs() -> None:
    """News evidence shouldn't get LADBS descriptor fields injected into its
    candidate payload."""
    project = _build_project()
    project.pipeline_status = PipelineStatus.UNDER_CONSTRUCTION
    news_evidence = _build_evidence(
        source_type="news_article",
        source_tier=2,
        evidence_date=date(2026, 3, 15),
        extracted_fields={
            "pipeline_status": {"value": PipelineStatus.APPROVED.value, "confidence": None},
        },
        raw_data={"permit_type": "should-be-ignored"},
    )

    resolution = resolve_status([news_evidence], project)

    candidates = resolution.metadata.get("regression_candidates", [])
    assert len(candidates) == 1
    candidate = candidates[0]
    assert "permit_type" not in candidate
    assert "permit_number" not in candidate

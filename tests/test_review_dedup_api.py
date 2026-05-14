from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from tcg_pipeline.api.auth import AuthenticatedUser
from tcg_pipeline.api.routers import review as review_router
from tcg_pipeline.db.models import (
    Evidence,
    Market,
    NewsArticle,
    NewsExtraction,
    NewsExtractionPass,
    NewsFetchStatus,
    NewsProjectReference,
    NewsReferenceConfidence,
    NewsSource,
    NewsTriageStatus,
    PipelineStatus,
    Priority,
    ProductType,
    Project,
    ReviewItem,
    ReviewItemStatus,
    ReviewItemType,
)


def test_review_item_candidates_returns_dedup_candidate_payload(
    postgres_session: Session,
) -> None:
    _ensure_review_dedup_test_schema(postgres_session, require_pg_trgm=True)
    fixture = _news_discovery_fixture(postgres_session)

    response = review_router.get_review_item_candidates(
        fixture["review_item"].id,
        user=_auth_user(),
        session=postgres_session,
    )

    assert response.subject["project_name"] == "Fig Tower"
    assert response.subject["units_total"] == 140
    assert response.searched["layer_2"]["trigram_min_score"] == 0.12
    candidates_by_id = {
        candidate["project_id"]: candidate for candidate in response.candidates
    }
    candidate = candidates_by_id[str(fixture["project"].id)]
    assert candidate["match_layer"] == 1
    assert candidate["match_signals"]["address"]["contributed"] is True
    assert 0.0 <= response.new_candidate_probability <= 1.0


def test_match_preview_counts_same_reference_siblings_and_uses_shared_deltas(
    postgres_session: Session,
) -> None:
    _ensure_review_dedup_test_schema(postgres_session)
    fixture = _news_discovery_fixture(postgres_session)
    reference = fixture["reference"]
    project = fixture["project"]
    sibling = ReviewItem(
        item_type=ReviewItemType.LOW_CONFIDENCE,
        status=ReviewItemStatus.OPEN,
        state="open",
        priority=Priority.LOW,
        payload={"source_record_id": str(reference.id)},
    )
    already_attached = _evidence(reference, project_id=project.id)
    to_reattach = _evidence(reference, project_id=None)
    postgres_session.add_all([sibling, already_attached, to_reattach])
    postgres_session.flush()

    response = review_router.get_review_item_match_preview(
        fixture["review_item"].id,
        candidate_id=project.id,
        user=_auth_user(),
        session=postgres_session,
    )

    assert response.review_items_to_close == 2
    assert response.evidence_rows_to_reattach == 1
    assert response.value_change_items_that_would_be_queued == [
        "developer",
        "total_units",
        "stories",
    ]


def test_same_reference_open_review_item_count_counts_subject_and_siblings(
    postgres_session: Session,
) -> None:
    _ensure_review_items_table(postgres_session)
    reference_id = uuid.uuid4()
    postgres_session.add_all(
        [
            ReviewItem(
                item_type=ReviewItemType.NEW_CANDIDATE,
                status=ReviewItemStatus.OPEN,
                state="open",
                priority=Priority.HIGH,
                payload={"source_record_id": str(reference_id)},
            ),
            ReviewItem(
                item_type=ReviewItemType.LOW_CONFIDENCE,
                status=ReviewItemStatus.OPEN,
                state="staged",
                priority=Priority.LOW,
                payload={"news_context": {"reference_id": str(reference_id)}},
            ),
            ReviewItem(
                item_type=ReviewItemType.LOW_CONFIDENCE,
                status=ReviewItemStatus.OPEN,
                state="committed",
                priority=Priority.LOW,
                payload={"source_record_id": str(reference_id)},
            ),
            ReviewItem(
                item_type=ReviewItemType.NEW_CANDIDATE,
                status=ReviewItemStatus.OPEN,
                state="open",
                priority=Priority.HIGH,
                payload={"source_record_id": str(uuid.uuid4())},
            ),
        ]
    )
    postgres_session.flush()

    assert (
        review_router._same_reference_open_review_item_count(  # noqa: SLF001
            postgres_session,
            reference_id,
        )
        == 2
    )


def _news_discovery_fixture(postgres_session: Session) -> dict[str, object]:
    market = Market(
        slug=f"dedup-api-{uuid.uuid4().hex[:8]}",
        name="Dedup API Market",
        state="CA",
    )
    source = NewsSource(
        slug=f"dedup-api-source-{uuid.uuid4().hex[:8]}",
        name="Dedup API Source",
        base_url="https://example.com",
        collector_class="PoliteNewsCollector",
        market=market,
    )
    article = NewsArticle(
        source=source,
        url_canonical=f"https://example.com/{uuid.uuid4().hex}",
        url_original=f"https://example.com/{uuid.uuid4().hex}",
        url_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        fetch_status=NewsFetchStatus.FETCHED.value,
        triage_status=NewsTriageStatus.RELEVANT.value,
        title="Fig Tower moves forward",
        fetched_at=datetime(2026, 5, 14, 12, 0, tzinfo=UTC),
        published_at=datetime(2026, 5, 14, 11, 0, tzinfo=UTC),
        ingest_method="news_paste_a_link",
    )
    postgres_session.add_all([market, source, article])
    postgres_session.flush()
    extraction = NewsExtraction(
        article_id=article.id,
        pass_name=NewsExtractionPass.EXTRACTION.value,
        triggered_by="test",
        prompt_id="extract_v2",
        prompt_version="v2",
        prompt_hash=uuid.uuid4().hex,
        model="claude-opus-4-7",
        output_json={},
    )
    postgres_session.add(extraction)
    postgres_session.flush()
    reference = NewsProjectReference(
        extraction_id=extraction.id,
        article_id=article.id,
        reference_index=0,
        candidate_name="Fig Tower",
        candidate_address="100 Fig St",
        candidate_city="Los Angeles",
        candidate_developer="Atlas Development",
        candidate_unit_total=140,
        candidate_stories=8,
        candidate_product_type=ProductType.APARTMENT.value,
        candidate_status_signal=PipelineStatus.PROPOSED.value,
        candidate_confidence=NewsReferenceConfidence.HIGH.value,
        candidate_identifiers={"apn": [], "costar_property_id": []},
    )
    project = Project(
        canonical_address="100 FIG ST LOS ANGELES CA",
        raw_addresses=["100 FIG ST LOS ANGELES CA"],
        market=market.slug,
        market_id=market.id,
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        project_name="Fig Tower",
        developer="Old Developer",
        total_units=100,
        stories=6,
        product_type=ProductType.APARTMENT,
        pipeline_status=PipelineStatus.PROPOSED,
    )
    postgres_session.add_all([reference, project])
    postgres_session.flush()
    review_item = ReviewItem(
        item_type=ReviewItemType.NEW_CANDIDATE,
        status=ReviewItemStatus.OPEN,
        state="open",
        priority=Priority.HIGH,
        payload={
            "source_record_id": str(reference.id),
            "news_context": {"reference_id": str(reference.id)},
        },
    )
    postgres_session.add(review_item)
    postgres_session.flush()
    reference.review_item_id = review_item.id
    postgres_session.flush()
    return {
        "market": market,
        "source": source,
        "article": article,
        "extraction": extraction,
        "reference": reference,
        "project": project,
        "review_item": review_item,
    }


def _evidence(reference: NewsProjectReference, *, project_id: uuid.UUID | None) -> Evidence:
    return Evidence(
        project_id=project_id,
        source_type="news_article",
        source_tier=2,
        ingest_method="news_paste_a_link",
        source_record_id=str(reference.id),
        collected_at=datetime(2026, 5, 14, 12, 30, tzinfo=UTC),
        evidence_date=date(2026, 5, 14),
        raw_data={"article_id": str(reference.article_id), "reference_id": str(reference.id)},
        raw_data_hash=uuid.uuid4().hex,
        extracted_fields={"total_units": {"value": reference.candidate_unit_total}},
    )


def _auth_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=uuid.uuid4(),
        email="reviewer@example.com",
        role="authenticated",
        claims={},
    )


def _ensure_review_dedup_test_schema(
    postgres_session: Session,
    *,
    require_pg_trgm: bool = False,
) -> None:
    inspector = inspect(postgres_session.bind)
    required_tables = {
        "evidence",
        "markets",
        "news_articles",
        "news_extractions",
        "news_project_references",
        "news_sources",
        "projects",
        "review_items",
    }
    missing = [table_name for table_name in required_tables if not inspector.has_table(table_name)]
    if missing:
        pytest.skip(f"Apply the latest migrations before running review dedup API tests: {missing}")
    reference_columns = {
        column["name"] for column in inspector.get_columns("news_project_references")
    }
    if "candidate_stories" not in reference_columns:
        pytest.skip("Apply migration 202605130039 before running review dedup API tests.")
    if require_pg_trgm:
        extension_exists = postgres_session.execute(
            text("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")
        ).scalar_one_or_none()
        if extension_exists is None:
            pytest.skip("pg_trgm extension is required for dedup candidate API tests.")


def _ensure_review_items_table(postgres_session: Session) -> None:
    inspector = inspect(postgres_session.bind)
    if not inspector.has_table("review_items"):
        pytest.skip("Apply review-item migrations before running review dedup API tests.")

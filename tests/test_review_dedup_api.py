from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import inspect, or_, select, text
from sqlalchemy.orm import Session

from tcg_pipeline.api.auth import AuthenticatedUser
from tcg_pipeline.api.routers import review as review_router
from tcg_pipeline.db.models import (
    ChangeLog,
    Evidence,
    IdentifierType,
    Jurisdiction,
    Market,
    NewsArticle,
    NewsExtraction,
    NewsExtractionPass,
    NewsFetchStatus,
    NewsMatchStatus,
    NewsProjectReference,
    NewsReferenceConfidence,
    NewsSource,
    NewsTriageStatus,
    PipelineStatus,
    Priority,
    ProductType,
    Project,
    ProjectIdentifier,
    ProjectRelationship,
    RelationshipType,
    ReviewItem,
    ReviewItemStatus,
    ReviewItemType,
    SourceRun,
)
from tcg_pipeline.matching import candidates as candidate_matching


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


def test_layer1_combined_identifier_address_query_dedupes_signals(
    postgres_session: Session,
) -> None:
    _ensure_review_dedup_test_schema(postgres_session)
    suffix = uuid.uuid4().hex[:8]
    market = Market(
        slug=f"dedup-layer1-{suffix}",
        name="Dedup Layer 1 Market",
        state="CA",
    )
    postgres_session.add(market)
    postgres_session.flush()

    subject_address = f"100 COMBINED {suffix.upper()} WAY LOS ANGELES CA 90012"
    address_only = _project_for_layer1_fixture(market, subject_address)
    identifier_only = _project_for_layer1_fixture(
        market,
        f"200 IDENTIFIER {suffix.upper()} WAY LOS ANGELES CA 90012",
    )
    address_and_identifiers = _project_for_layer1_fixture(market, subject_address)
    unrelated = _project_for_layer1_fixture(
        market,
        f"300 UNRELATED {suffix.upper()} WAY LOS ANGELES CA 90012",
    )
    postgres_session.add_all(
        [address_only, identifier_only, address_and_identifiers, unrelated]
    )
    postgres_session.flush()

    postgres_session.add_all(
        [
            ProjectIdentifier(
                project_id=identifier_only.id,
                identifier_type=IdentifierType.APN,
                value=f"APN-ONLY-{suffix}",
                source="test",
            ),
            ProjectIdentifier(
                project_id=address_and_identifiers.id,
                identifier_type=IdentifierType.APN,
                value=f"APN-BOTH-{suffix}",
                source="test",
            ),
            ProjectIdentifier(
                project_id=address_and_identifiers.id,
                identifier_type=IdentifierType.COSTAR_PROPERTY_ID,
                value=f"COSTAR-BOTH-{suffix}",
                source="test",
            ),
            ProjectIdentifier(
                project_id=address_and_identifiers.id,
                identifier_type=IdentifierType.CASE_NUMBER,
                value=f"IGNORED-{suffix}",
                source="test",
            ),
        ]
    )
    postgres_session.flush()

    subject = candidate_matching.DedupSubject(
        canonical_address=subject_address,
        market=market.slug,
        identifiers={
            "apn": [f"APN-ONLY-{suffix}", f"APN-BOTH-{suffix}"],
            "costar_property_id": [f"COSTAR-BOTH-{suffix}"],
        },
    )

    signals = candidate_matching._load_layer1_hard_signals(  # noqa: SLF001
        postgres_session,
        subject,
    )

    assert [signal.name for signal in signals[address_only.id]] == ["address"]
    assert [signal.detail for signal in signals[identifier_only.id]] == [
        f"apn:APN-ONLY-{suffix}"
    ]

    both_names = Counter(signal.name for signal in signals[address_and_identifiers.id])
    both_identifier_details = sorted(
        signal.detail
        for signal in signals[address_and_identifiers.id]
        if signal.name == "identifier"
    )
    assert both_names == Counter({"address": 1, "identifier": 2})
    assert both_identifier_details == [
        f"apn:APN-BOTH-{suffix}",
        f"costar_property_id:COSTAR-BOTH-{suffix}",
    ]
    assert unrelated.id not in signals


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


def test_match_review_item_to_project_updates_reference_and_queues_deltas(
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
    evidence = _evidence(reference, project_id=None)
    postgres_session.add_all([sibling, evidence])
    postgres_session.flush()

    response = review_router.match_review_item_to_project(
        fixture["review_item"].id,
        payload=review_router.ReviewDedupMatchRequest(
            matched_project_id=project.id,
            accept_deltas=["developer"],
        ),
        user=_auth_user(),
        session=postgres_session,
    )

    assert response.project_id == project.id
    assert response.reference_id == reference.id
    assert response.closed_review_items == 2
    assert response.evidence_rows_reattached == 1
    assert response.value_change_items_queued == ["total_units", "stories"]
    assert reference.matched_project_id == project.id
    assert reference.match_status == NewsMatchStatus.MANUAL_RELINK.value
    assert evidence.project_id == project.id
    assert project.developer == "Atlas Development"
    postgres_session.flush()
    queued_fields = set(
        postgres_session.execute(
            select(ReviewItem.field_name).where(
                ReviewItem.project_id == project.id,
                ReviewItem.item_type == ReviewItemType.STATUS_CHANGE,
                ReviewItem.state == "open",
            )
        ).scalars()
    )
    assert queued_fields == {"total_units", "stories"}
    source_reference_log = postgres_session.execute(
        select(ChangeLog).where(
            ChangeLog.project_id == project.id,
            ChangeLog.field == "source_reference",
        )
    ).scalar_one()
    assert "Absorbed reference" in source_reference_log.new_value["summary"]


def test_match_review_item_rejects_unknown_edit_fields(
    postgres_session: Session,
) -> None:
    _ensure_review_dedup_test_schema(postgres_session)
    fixture = _news_discovery_fixture(postgres_session)
    reference = fixture["reference"]
    project = fixture["project"]

    with pytest.raises(Exception) as exc_info:
        review_router.match_review_item_to_project(
            fixture["review_item"].id,
            payload=review_router.ReviewDedupMatchRequest(
                matched_project_id=project.id,
                edits={"junk_field": "value"},
            ),
            user=_auth_user(),
            session=postgres_session,
        )

    assert getattr(exc_info.value, "status_code", None) == 422
    assert "unsupported field" in str(getattr(exc_info.value, "detail", ""))
    assert reference.matched_project_id is None


def test_create_project_from_review_item_applies_edits_and_closes_item(
    postgres_session: Session,
) -> None:
    _ensure_review_dedup_test_schema(postgres_session)
    fixture = _news_discovery_fixture(postgres_session)
    reference = fixture["reference"]
    review_item = fixture["review_item"]

    response = review_router.create_project_from_review_item(
        review_item.id,
        payload=review_router.ReviewDedupCreateRequest(
            edits={
                "project_name": "Fig Tower Revised",
                "canonical_address": "200 Fig St",
                "total_units": "180",
            },
            project_fields={
                "project_name": "Fig Tower Revised",
                "canonical_address": "200 FIG ST LOS ANGELES CA",
                "city": "Los Angeles",
                "state": "CA",
                "county": "Los Angeles",
                "total_units": 180,
            },
        ),
        user=_auth_user(),
        session=postgres_session,
    )

    project = postgres_session.get(Project, response.project_id)
    assert project is not None
    assert project.project_name == "Fig Tower Revised"
    assert project.total_units == 180
    assert reference.candidate_name == "Fig Tower Revised"
    assert reference.candidate_unit_total == 180
    assert reference.matched_project_id == project.id
    assert reference.match_status == NewsMatchStatus.CONFIRMED.value
    assert review_item.state == "committed"
    assert response.closed_review_items == 1
    assert response.value_change_items_queued == []


def test_create_project_from_payload_subject_uses_source_run_market_context(
    postgres_session: Session,
) -> None:
    _ensure_review_dedup_test_schema(postgres_session)
    market = Market(slug=f"permit-market-{uuid.uuid4().hex[:8]}", name="Los Angeles", state="CA")
    jurisdiction = Jurisdiction(
        slug=f"permit-jurisdiction-{uuid.uuid4().hex[:8]}",
        name="Los Angeles",
        state="CA",
        market=market,
    )
    source_run = SourceRun(
        market=market.slug,
        jurisdiction=jurisdiction,
        source_name="ladbs_permit",
        collection_mode="incremental",
    )
    review_item = ReviewItem(
        source_run=source_run,
        item_type=ReviewItemType.NEW_CANDIDATE,
        status=ReviewItemStatus.OPEN,
        state="open",
        priority=Priority.HIGH,
        payload={
            "mapped_fields": {
                "project_name": "Permit Tower",
                "canonical_address": "300 FIG ST LOS ANGELES CA",
                "developer": "Atlas Development",
                "total_units": 88,
                "stories": 7,
                "pipeline_status": PipelineStatus.APPROVED.value,
            }
        },
    )
    postgres_session.add_all([market, jurisdiction, source_run, review_item])
    postgres_session.flush()

    with pytest.raises(Exception) as exc_info:
        review_router.create_project_from_review_item(
            review_item.id,
            payload=review_router.ReviewDedupCreateRequest(
                edits={"developer": "Edited Permit Developer"}
            ),
            user=_auth_user(),
            session=postgres_session,
        )

    assert getattr(exc_info.value, "status_code", None) == 400
    assert "backing news reference" in str(getattr(exc_info.value, "detail", ""))

    response = review_router.create_project_from_review_item(
        review_item.id,
        payload=review_router.ReviewDedupCreateRequest(),
        user=_auth_user(),
        session=postgres_session,
    )

    project = postgres_session.get(Project, response.project_id)
    assert project is not None
    assert project.market == market.slug
    assert project.market_id == market.id
    assert project.city == "Los Angeles"
    assert project.state == "CA"
    assert project.county == "Los Angeles"
    assert project.jurisdiction_id == jurisdiction.id
    assert project.project_name == "Permit Tower"
    assert project.total_units == 88
    assert project.stories == 7


def test_create_and_link_rejects_duplicate_relationship_type(
    postgres_session: Session,
) -> None:
    _ensure_review_dedup_test_schema(postgres_session)
    fixture = _news_discovery_fixture(postgres_session)

    with pytest.raises(Exception) as exc_info:
        review_router.create_project_and_link_from_review_item(
            fixture["review_item"].id,
            payload=review_router.ReviewDedupCreateAndLinkRequest(
                relationship_type=RelationshipType.DUPLICATE.value,
                related_project_id=fixture["project"].id,
                project_fields={"county": "Los Angeles"},
            ),
            user=_auth_user(),
            session=postgres_session,
        )

    assert getattr(exc_info.value, "status_code", None) == 422
    fixture_project_id = fixture["project"].id
    assert (
        postgres_session.execute(
            select(ProjectRelationship).where(
                or_(
                    ProjectRelationship.project_id == fixture_project_id,
                    ProjectRelationship.related_project_id == fixture_project_id,
                )
            )
        )
        .scalars()
        .all()
        == []
    )


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
        canonical_address="100 FIG STREET",
        raw_addresses=["100 FIG STREET"],
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


def _project_for_layer1_fixture(market: Market, canonical_address: str) -> Project:
    return Project(
        canonical_address=canonical_address,
        raw_addresses=[canonical_address],
        market=market.slug,
        market_id=market.id,
        city="Los Angeles",
        state="CA",
        county="Los Angeles",
        project_name="Layer 1 Fixture",
        product_type=ProductType.APARTMENT,
        pipeline_status=PipelineStatus.PROPOSED,
    )


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
        "change_log",
        "jurisdictions",
        "markets",
        "news_articles",
        "news_extractions",
        "news_project_references",
        "news_sources",
        "project_relationships",
        "projects",
        "review_items",
        "source_runs",
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

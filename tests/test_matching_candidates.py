from __future__ import annotations

import uuid

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from tcg_pipeline.db.models import (
    IdentifierType,
    PipelineStatus,
    Priority,
    ProductType,
    Project,
    ProjectIdentifier,
    ReviewItem,
    ReviewItemStatus,
    ReviewItemType,
)
from tcg_pipeline.ingesters._common import build_location
from tcg_pipeline.matching.candidates import DedupSubject, find_dedup_candidates


def test_find_dedup_candidates_returns_layered_signal_payloads(
    postgres_session: Session,
) -> None:
    _ensure_dedup_candidate_test_schema(postgres_session)
    market = f"dedup_test_{uuid.uuid4().hex[:8]}"
    subject_address = f"100 DEDUP ALPHA {uuid.uuid4().hex[:6]} WAY LOS ANGELES CA 90012"
    subject = DedupSubject(
        project_name="Sunset Vista",
        canonical_address=subject_address,
        developer="The Panorama Group LLC",
        total_units=100,
        product_type="apartment",
        lat=34.0500,
        lng=-118.2500,
        market=market,
        identifiers={"apn": ["5555-001-001"]},
    )
    exact_address_project = _project(
        subject_address,
        market=market,
        project_name="Different Name",
        developer="Other Developer",
        total_units=200,
        product_type=ProductType.CONDO,
    )
    identifier_project = _project(
        f"200 DEDUP BETA {uuid.uuid4().hex[:6]} WAY LOS ANGELES CA 90012",
        market=market,
        project_name="Identifier Match",
        developer="Unrelated",
        total_units=50,
        product_type=ProductType.APARTMENT,
    )
    soft_project = _project(
        f"300 DEDUP GAMMA {uuid.uuid4().hex[:6]} WAY LOS ANGELES CA 90012",
        market=market,
        project_name="Sunset Vista Apartments",
        developer="Unrelated Builder",
        total_units=150,
        product_type=ProductType.CONDO,
        lat=34.0520,
        lng=-118.2520,
        location=build_location(34.0520, -118.2520),
    )
    deleted_project = _project(
        f"400 DEDUP DELETE {uuid.uuid4().hex[:6]} WAY LOS ANGELES CA 90012",
        market=market,
        project_name="Sunset Vista Delete",
        pipeline_status=PipelineStatus.DELETE_DUPLICATE,
    )
    postgres_session.add_all(
        [exact_address_project, identifier_project, soft_project, deleted_project]
    )
    postgres_session.flush()
    postgres_session.add(
        ProjectIdentifier(
            project_id=identifier_project.id,
            identifier_type=IdentifierType.APN,
            value="5555-001-001",
            source="test",
        )
    )
    postgres_session.add(
        ReviewItem(
            project_id=exact_address_project.id,
            item_type=ReviewItemType.STATUS_CHANGE,
            status=ReviewItemStatus.OPEN,
            state="open",
            priority=Priority.MEDIUM,
            field_name="total_units",
        )
    )
    postgres_session.flush()

    result = find_dedup_candidates(postgres_session, subject)
    candidates_by_id = {candidate.project_id: candidate for candidate in result.candidates}

    assert exact_address_project.id in candidates_by_id
    assert identifier_project.id in candidates_by_id
    assert soft_project.id in candidates_by_id
    assert deleted_project.id not in candidates_by_id

    exact_candidate = candidates_by_id[exact_address_project.id]
    assert exact_candidate.match_layer == 1
    assert exact_candidate.match_signals["address"].contributed is True
    assert exact_candidate.open_review_item_count == 1

    identifier_candidate = candidates_by_id[identifier_project.id]
    assert identifier_candidate.match_layer == 1
    assert identifier_candidate.match_signals["identifier"].contributed is True
    assert "apn:5555-001-001" in (
        identifier_candidate.match_signals["identifier"].detail or ""
    )

    soft_candidate = candidates_by_id[soft_project.id]
    assert soft_candidate.match_layer == 2
    assert soft_candidate.match_signals["name"].contributed is True
    assert soft_candidate.match_signals["developer"].contributed is False
    assert result.as_payload()["searched"]["layer_2"]["weights"]["developer"] == 0.20
    assert result.new_candidate_probability < 1.0


def test_find_dedup_candidates_empty_result_reports_searched_metadata(
    postgres_session: Session,
) -> None:
    _ensure_dedup_candidate_test_schema(postgres_session)
    subject = DedupSubject(
        project_name="No Existing Match",
        canonical_address=f"999 DEDUP EMPTY {uuid.uuid4().hex[:6]} WAY LOS ANGELES CA 90012",
        market=f"dedup_empty_{uuid.uuid4().hex[:8]}",
    )

    result = find_dedup_candidates(postgres_session, subject)

    assert result.candidates == []
    assert result.new_candidate_probability == 1.0
    searched = result.as_payload()["searched"]
    layer_1 = searched["layer_1"]
    assert any(signal["signal"] == "address" and signal["searched"] for signal in layer_1)
    assert searched["layer_2"]["searched"] is True
    assert searched["layer_2"]["trigram_min_score"] == 0.12
    assert searched["layer_3"]["layer_3_radius_meters"] == 1_000.0


def test_find_dedup_candidates_skips_layer2_without_trigram_subject_signal(
    postgres_session: Session,
) -> None:
    _ensure_dedup_candidate_test_schema(postgres_session)
    market = f"dedup_no_trigram_{uuid.uuid4().hex[:8]}"
    postgres_session.add(
        _project(
            f"700 DEDUP NO TRIGRAM {uuid.uuid4().hex[:6]} WAY",
            market=market,
            project_name="Any Market Project",
            developer="Any Developer",
            total_units=100,
        )
    )
    postgres_session.flush()
    subject = DedupSubject(
        developer="Unmatched Developer",
        total_units=100,
        market=market,
    )

    result = find_dedup_candidates(postgres_session, subject)

    assert result.candidates == []
    assert result.as_payload()["searched"]["layer_2"]["searched"] is False


def test_identifier_only_layer1_candidate_sorts_ahead_of_layer2(
    postgres_session: Session,
) -> None:
    _ensure_dedup_candidate_test_schema(postgres_session)
    market = f"dedup_identifier_{uuid.uuid4().hex[:8]}"
    subject = DedupSubject(
        canonical_address="ALPHA",
        market=market,
        identifiers={"apn": ["7777-001-001"]},
    )
    identifier_project = _project(
        "ZZZ",
        market=market,
        project_name=None,
        developer=None,
    )
    layer2_project = _project(
        "ALPHA HOMES",
        market=market,
        project_name="Unrelated Layer Two",
    )
    postgres_session.add_all([identifier_project, layer2_project])
    postgres_session.flush()
    postgres_session.add(
        ProjectIdentifier(
            project_id=identifier_project.id,
            identifier_type=IdentifierType.APN,
            value="7777-001-001",
            source="test",
        )
    )
    postgres_session.flush()

    result = find_dedup_candidates(postgres_session, subject)
    candidates_by_id = {candidate.project_id: candidate for candidate in result.candidates}

    assert result.candidates[0].project_id == identifier_project.id
    assert candidates_by_id[identifier_project.id].match_layer == 1
    assert candidates_by_id[identifier_project.id].match_signals["identifier"].contributed
    assert candidates_by_id[layer2_project.id].match_layer == 2


def test_find_dedup_candidates_loads_layer3_only_when_requested(
    postgres_session: Session,
) -> None:
    _ensure_dedup_candidate_test_schema(postgres_session)
    market = f"dedup_layer3_{uuid.uuid4().hex[:8]}"
    subject = DedupSubject(
        project_name="Subject Alpha",
        canonical_address=f"111 SUBJECT ONLY {uuid.uuid4().hex[:6]}",
        lat=34.0500,
        lng=-118.2500,
        market=market,
    )
    layer3_project = _project(
        f"999 REMOTE TOKEN {uuid.uuid4().hex[:6]}",
        market=market,
        project_name="Different Omega",
        lat=34.0555,
        lng=-118.2500,
        location=build_location(34.0555, -118.2500),
    )
    postgres_session.add(layer3_project)
    postgres_session.flush()

    default_result = find_dedup_candidates(postgres_session, subject)
    assert layer3_project.id not in {
        candidate.project_id for candidate in default_result.candidates
    }
    assert default_result.layer_3_available is True

    expanded_result = find_dedup_candidates(
        postgres_session,
        subject,
        include_layer3=True,
    )
    candidates_by_id = {candidate.project_id: candidate for candidate in expanded_result.candidates}

    assert candidates_by_id[layer3_project.id].match_layer == 3
    assert expanded_result.as_payload()["searched"]["layer_3"]["searched"] is True


def test_dedup_trgm_indexes_exist_when_migration_applied(
    postgres_session: Session,
) -> None:
    _ensure_migration_applied(postgres_session, "202605140040")
    index_defs = {
        row.indexname: row.indexdef.lower()
        for row in postgres_session.execute(
            text(
                """
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename = 'projects'
                  AND indexname IN (
                    'ix_projects_canonical_address_trgm',
                    'ix_projects_project_name_trgm',
                    'ix_projects_location_gist'
                  )
                """
            )
        ).all()
    }

    assert "using gin" in index_defs["ix_projects_canonical_address_trgm"]
    assert "gin_trgm_ops" in index_defs["ix_projects_canonical_address_trgm"]
    assert "using gin" in index_defs["ix_projects_project_name_trgm"]
    assert "gin_trgm_ops" in index_defs["ix_projects_project_name_trgm"]
    assert "using gist" in index_defs["ix_projects_location_gist"]


def _project(
    canonical_address: str,
    *,
    market: str,
    **overrides,
) -> Project:
    defaults = {
        "raw_addresses": [canonical_address],
        "market": market,
        "city": "Los Angeles",
        "state": "CA",
        "county": "Los Angeles",
    }
    defaults.update(overrides)
    return Project(canonical_address=canonical_address, **defaults)


def _ensure_dedup_candidate_test_schema(postgres_session: Session) -> None:
    inspector = inspect(postgres_session.bind)
    missing = [
        table_name
        for table_name in ("projects", "project_identifiers", "review_items")
        if not inspector.has_table(table_name)
    ]
    if missing:
        pytest.skip(f"Apply the latest migrations before running dedup tests: {missing}")
    extension_exists = postgres_session.execute(
        text("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")
    ).scalar_one_or_none()
    if extension_exists is None:
        pytest.skip("pg_trgm extension is required for dedup candidate tests.")


def _ensure_migration_applied(postgres_session: Session, revision: str) -> None:
    inspector = inspect(postgres_session.bind)
    if not inspector.has_table("alembic_version"):
        pytest.skip("Apply Alembic migrations before running migration index tests.")
    applied = postgres_session.execute(
        text("SELECT 1 FROM alembic_version WHERE version_num = :revision"),
        {"revision": revision},
    ).scalar_one_or_none()
    if applied is None:
        pytest.skip(f"Apply migration {revision} before running migration index tests.")

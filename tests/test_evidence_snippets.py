from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from tcg_pipeline.api.auth import AuthenticatedUser
from tcg_pipeline.api.deps import get_db_session
from tcg_pipeline.api.main import create_app
from tcg_pipeline.db.models import Evidence
from tcg_pipeline.review.snippets import render_snippet
from tcg_pipeline.settings import Settings

USER_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


class FakeVerifier:
    def verify(self, token: str) -> AuthenticatedUser:
        return AuthenticatedUser(
            user_id=USER_ID,
            email="allowed@example.com",
            role="authenticated",
            claims={"sub": str(USER_ID), "email": "allowed@example.com", "role": "authenticated"},
        )


def test_ladbs_permit_snippet_uses_permit_status_and_field_value() -> None:
    evidence = _evidence(
        source_type="ladbs_permit",
        source_tier=1,
        source_record_id="11010-10000-02451",
        evidence_date=date(2013, 1, 2),
        raw_data={"pcis_permit": "11010-10000-02451"},
        extracted_fields={
            "status_evidence_type": {"value": "building_permit_issued", "confidence": None},
            "status_evidence_date": {"value": "2013-01-02", "confidence": None},
            "status_desc": {"value": "Issued", "confidence": None},
            "pipeline_status": {"value": "Approved", "confidence": "high"},
        },
    )

    snippet = render_snippet(evidence, field_name="pipeline_status")

    assert "PCIS 11010-10000-02451" in snippet.summary
    assert "building_permit_issued" in snippet.summary
    assert "permit status: Issued" in snippet.summary
    assert "issued: 2013-01-02" in snippet.detail
    assert snippet.fields.field_name == "pipeline_status"
    assert snippet.fields.extracted_value == "Approved"
    assert snippet.fields.extracted_confidence == "high"
    assert snippet.source_metadata.source_type == "ladbs_permit"


def test_costar_snippet_returns_raw_field_value_without_transformation() -> None:
    evidence = _evidence(
        source_type="costar",
        source_tier=3,
        source_record_id="CST-9901",
        extracted_fields={
            "total_units": {"value": 42, "confidence": None},
        },
    )

    snippet = render_snippet(evidence, field_name="total_units")

    assert snippet.summary == "total_units: 42"
    assert "CoStar" in snippet.detail
    assert snippet.fields.extracted_value == 42


def test_pipedream_snippet_includes_last_edit_metadata() -> None:
    evidence = _evidence(
        source_type="pipedream",
        source_tier=1,
        source_record_id="997.00001",
        extracted_fields={
            "pipeline_status": {"value": "Pending", "confidence": None},
            "last_editor": {"value": "Sarah Lee", "confidence": None},
            "last_edit_date": {"value": "2024-11-05", "confidence": None},
        },
    )

    snippet = render_snippet(evidence, field_name="pipeline_status")

    assert snippet.summary == "pipeline_status: Pending"
    assert "last edited by: Sarah Lee" in snippet.detail
    assert "last edited: 2024-11-05" in snippet.detail


def test_unknown_field_returns_na_without_error() -> None:
    evidence = _evidence(
        source_type="costar",
        source_tier=3,
        source_record_id="CST-9902",
        extracted_fields={
            "total_units": {"value": 42, "confidence": None},
        },
    )

    snippet = render_snippet(evidence, field_name="does_not_exist")

    assert snippet.summary == "does_not_exist: n/a"
    assert snippet.fields.field_name == "does_not_exist"
    assert snippet.fields.extracted_value is None
    assert snippet.fields.extracted_confidence is None


def test_news_article_snippet_returns_stored_highlights() -> None:
    evidence = _evidence(
        source_type="news_article",
        source_tier=2,
        source_record_id="article-1",
        raw_data={
            "publication": "BizJournals",
            "published_at": "2026-04-08",
            "author": "Jane Reporter",
            "article_url": "https://example.com/article",
        },
        extracted_fields={
            "developer": {
                "value": "Helio Capital",
                "confidence": "high",
                "highlights": [
                    {
                        "passage": "Helio Capital expects to start construction.",
                        "field": "developer",
                        "value": "Helio Capital",
                        "offset_start": 0,
                        "offset_end": 13,
                    }
                ],
            }
        },
    )

    snippet = render_snippet(evidence, field_name="developer")

    assert snippet.summary == "developer: Helio Capital"
    assert snippet.detail == "BizJournals · 2026-04-08 · Jane Reporter"
    assert snippet.external_link == "https://example.com/article"
    assert snippet.highlights == [
        {
            "passage": "Helio Capital expects to start construction.",
            "field": "developer",
            "value": "Helio Capital",
            "offset_start": 0,
            "offset_end": 13,
        }
    ]


def test_override_snippet_uses_actor_mode_and_note() -> None:
    evidence = _evidence(
        source_type="researcher_override",
        source_tier=0,
        source_record_id="override-1",
        raw_data={
            "actor": "ng@theconcordgroup.com",
            "set_at": "2026-04-15T12:00:00Z",
            "mode": "until_newer_evidence",
            "note": "Confirmed with developer call.",
        },
        extracted_fields={
            "total_units": {"value": 212, "confidence": "manual"},
        },
    )

    snippet = render_snippet(evidence, field_name="total_units")

    assert snippet.summary == "total_units: 212"
    assert "set by: ng@theconcordgroup.com" in snippet.detail
    assert "2026-04-15T12:00:00Z" in snippet.detail
    assert "until_newer_evidence" in snippet.detail
    assert "Confirmed with developer call." in snippet.detail
    assert snippet.fields.extracted_confidence == "manual"


def test_computed_snippet_uses_rule_and_inputs() -> None:
    evidence = _evidence(
        source_type="computed",
        source_tier=0,
        source_record_id="computed-1",
        raw_data={
            "rule_applied": "highest_status_wins",
            "inputs": {
                "CoStar": "Approved",
                "LADBS": "building_permit_issued",
            },
        },
        extracted_fields={
            "pipeline_status": {"value": "Approved", "confidence": None},
        },
    )

    snippet = render_snippet(evidence, field_name="pipeline_status")

    assert snippet.summary == "pipeline_status: Approved"
    assert "Rule: highest_status_wins" in snippet.detail
    assert "CoStar: Approved" in snippet.detail
    assert "LADBS: building_permit_issued" in snippet.detail


def test_evidence_snippet_api_returns_rendered_payload(postgres_session: Session) -> None:
    if not inspect(postgres_session.bind).has_table("evidence"):
        pytest.skip("Apply the evidence layer migration before running API evidence tests.")

    evidence = _evidence(
        source_type="costar",
        source_tier=3,
        source_record_id="CST-API-1",
        extracted_fields={"developer": {"value": "Costar Dev", "confidence": None}},
    )
    postgres_session.add(evidence)
    postgres_session.flush()

    app = create_app(
        settings=Settings(
            app_env="test",
            database_url=None,
            supabase_url="https://example.supabase.co",
            supabase_anon_key="anon",
            allowed_emails="allowed@example.com",
        ),
        jwt_verifier=FakeVerifier(),
        readiness_check=lambda: None,
    )

    def override_db_session() -> Iterator[Session]:
        yield postgres_session

    app.dependency_overrides[get_db_session] = override_db_session
    client = TestClient(app)

    response = client.get(
        f"/evidence/{evidence.id}/snippet?field=developer",
        headers={"Authorization": "Bearer valid-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == "developer: Costar Dev"
    assert body["fields"]["extracted_value"] == "Costar Dev"
    assert body["source_metadata"]["source_type"] == "costar"
    assert body["source_metadata"]["source_record_id"] == "CST-API-1"


def test_evidence_snippet_api_returns_404_for_missing_evidence(
    postgres_session: Session,
) -> None:
    if not inspect(postgres_session.bind).has_table("evidence"):
        pytest.skip("Apply the evidence layer migration before running API evidence tests.")

    app = create_app(
        settings=Settings(
            app_env="test",
            database_url=None,
            supabase_url="https://example.supabase.co",
            supabase_anon_key="anon",
            allowed_emails="allowed@example.com",
        ),
        jwt_verifier=FakeVerifier(),
        readiness_check=lambda: None,
    )

    def override_db_session() -> Iterator[Session]:
        yield postgres_session

    app.dependency_overrides[get_db_session] = override_db_session
    client = TestClient(app)

    response = client.get(
        f"/evidence/{uuid.uuid4()}/snippet?field=developer",
        headers={"Authorization": "Bearer valid-token"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Evidence row not found."


def _evidence(
    *,
    source_type: str,
    source_tier: int,
    source_record_id: str,
    raw_data: dict | None = None,
    extracted_fields: dict | None = None,
    evidence_date: date | None = None,
) -> Evidence:
    return Evidence(
        id=uuid.uuid4(),
        source_type=source_type,
        source_tier=source_tier,
        ingest_method="test",
        source_record_id=source_record_id,
        collected_at=datetime(2026, 4, 18, 8, 0, tzinfo=UTC),
        evidence_date=evidence_date,
        raw_data=raw_data,
        raw_data_hash=str(uuid.uuid4()),
        extracted_fields=extracted_fields,
    )


def test_ladbs_permit_snippet_surfaces_source_fields() -> None:
    """UX.card-source-detail: SnippetPayload.source_fields carries permit_number,
    permit_type, status_desc, etc. so the frontend can render a labeled inline
    list on the review card without parsing the prose summary."""
    evidence = _evidence(
        source_type="ladbs_permit",
        source_tier=1,
        source_record_id="11010-10000-02451",
        evidence_date=date(2026, 3, 15),
        raw_data={
            "pcis_permit": "11010-10000-02451",
            "permit_type": "Bldg-New",
            "permit_sub_type": "Apartment",
            "status_desc": "Issued",
        },
        extracted_fields={
            "status_evidence_type": {"value": "building_permit_issued", "confidence": None},
        },
    )

    snippet = render_snippet(evidence, field_name="pipeline_status")

    assert snippet.source_fields["permit_number"] == "11010-10000-02451"
    assert snippet.source_fields["permit_type"] == "Bldg-New"
    assert snippet.source_fields["permit_sub_type"] == "Apartment"
    assert snippet.source_fields["status_desc"] == "Issued"


def test_costar_snippet_surfaces_source_fields() -> None:
    """UX.card-source-detail: CoStar snippets carry costar_property_id and
    upload_date in source_fields."""
    evidence = _evidence(
        source_type="costar",
        source_tier=3,
        source_record_id="costar-row-7",
        evidence_date=date(2026, 5, 1),
        raw_data={"costar_property_id": "1234567"},
        extracted_fields={"total_units": {"value": 140, "confidence": None}},
    )

    snippet = render_snippet(evidence, field_name="total_units")

    assert snippet.source_fields["costar_property_id"] == "1234567"
    assert "upload_date" in snippet.source_fields


def test_source_fields_omits_null_or_empty_values() -> None:
    """LADBS evidence missing permit_type / status_desc shouldn't pollute the
    source_fields dict with nulls — frontend renders entries straight."""
    evidence = _evidence(
        source_type="ladbs_permit",
        source_tier=1,
        source_record_id="11010-10000-02452",
        evidence_date=date(2026, 3, 15),
        raw_data={"pcis_permit": "11010-10000-02452"},
        extracted_fields={
            "status_evidence_type": {"value": "building_permit_issued", "confidence": None},
        },
    )

    snippet = render_snippet(evidence, field_name="pipeline_status")

    # permit_number is present, other LADBS-specific fields are absent
    assert snippet.source_fields.get("permit_number") == "11010-10000-02452"
    assert "permit_type" not in snippet.source_fields
    assert "status_desc" not in snippet.source_fields


def test_generic_snippet_source_fields_empty() -> None:
    """Source types without a tailored renderer have source_fields = {} by default."""
    evidence = _evidence(
        source_type="news_article",
        source_tier=2,
        source_record_id="article-1",
        evidence_date=date(2026, 5, 1),
        raw_data={"source_name": "Urbanize LA"},
        extracted_fields={"pipeline_status": {"value": "Under Construction", "confidence": "high"}},
    )

    snippet = render_snippet(evidence, field_name="pipeline_status")

    assert snippet.source_fields == {}

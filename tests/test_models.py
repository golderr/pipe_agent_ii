from sqlalchemy.orm import configure_mappers

from tcg_pipeline.db.models import (
    DeveloperAlias,
    Evidence,
    Project,
    ProjectIdentifier,
    ProjectRelationship,
    RelationshipType,
    ResolutionLog,
)


def _build_project(canonical_address: str) -> Project:
    return Project(
        canonical_address=canonical_address,
        raw_addresses=[canonical_address],
        market="los_angeles",
        city="LOS ANGELES",
        state="CA",
        county="LOS ANGELES",
    )


def test_project_relationships_are_bidirectional() -> None:
    configure_mappers()

    primary = _build_project("123 MAIN STREET")
    related = _build_project("125 MAIN STREET")
    relationship = ProjectRelationship(
        project=primary,
        related_project=related,
        relationship_type=RelationshipType.PHASE,
    )

    assert relationship in primary.outgoing_relationships
    assert relationship in related.incoming_relationships


def test_identifier_value_lookup_index_is_declared() -> None:
    index_names = {index.name for index in ProjectIdentifier.__table__.indexes}

    assert "ix_project_identifiers_value" in index_names


def test_evidence_indexes_are_declared() -> None:
    index_names = {index.name for index in Evidence.__table__.indexes}

    assert "ix_evidence_project_id" in index_names
    assert "ix_evidence_source_type" in index_names
    assert "ix_evidence_evidence_date" in index_names
    assert "ix_evidence_collected_at" in index_names
    assert "uq_evidence_source_type_source_record_id_raw_data_hash" in index_names


def test_resolution_log_and_developer_alias_indexes_are_declared() -> None:
    resolution_index_names = {index.name for index in ResolutionLog.__table__.indexes}
    developer_alias_index_names = {index.name for index in DeveloperAlias.__table__.indexes}

    assert "ix_resolution_log_project_id_created_at" in resolution_index_names
    assert "ix_developer_alias_developer_id" in developer_alias_index_names

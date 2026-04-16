from sqlalchemy.orm import configure_mappers

from tcg_pipeline.db.models import Project, ProjectIdentifier, ProjectRelationship, RelationshipType


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

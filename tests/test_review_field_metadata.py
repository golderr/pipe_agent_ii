from __future__ import annotations

from tcg_pipeline.api.project_overrides import (
    EVIDENCE_DERIVED_OVERRIDE_FIELDS,
    INTEGER_FIELDS,
)
from tcg_pipeline.api.routers.activity import _field_label
from tcg_pipeline.db.review_workflow import CHANGELOG_PRIORITY_BY_FIELD
from tcg_pipeline.review.field_metadata import (
    REVIEW_FIELD_METADATA,
    REVIEW_INTEGER_FIELD_NAMES,
    REVIEW_VALUE_CHANGE_FIELD_NAMES,
)


def test_review_field_metadata_stays_aligned_with_override_and_audit_surfaces() -> None:
    assert REVIEW_VALUE_CHANGE_FIELD_NAMES == EVIDENCE_DERIVED_OVERRIDE_FIELDS
    assert REVIEW_INTEGER_FIELD_NAMES == INTEGER_FIELDS
    assert REVIEW_VALUE_CHANGE_FIELD_NAMES <= set(CHANGELOG_PRIORITY_BY_FIELD)
    assert {
        field_name: _field_label(field_name)
        for field_name in sorted(REVIEW_VALUE_CHANGE_FIELD_NAMES)
    } == {
        field_name: metadata.label
        for field_name, metadata in sorted(REVIEW_FIELD_METADATA.items())
    }

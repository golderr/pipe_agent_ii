"""Deterministic LADBS semantic interpretation for residential permit fields.

The current ProductType enum is residential-only, so hotel/commercial/industrial
permit language is intentionally left unclassified by this first LADBS pass.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from tcg_pipeline.db.models import ProductType
from tcg_pipeline.semantic.types import (
    Confidence,
    InterpreterContext,
    PassageAnchor,
    SemanticInterpretation,
    SemanticInterpreter,
    SourceObservations,
)
from tcg_pipeline.source_tiers import get_logical_source_type

PERMIT_PROFILE_NAME = "permit_v1"
LADBS_PRODUCT_SOURCE_TYPES = frozenset({"ladbs_permit"})
LADBS_DIRECT_PRODUCT_STATUS_EVIDENCE_TYPES = frozenset({"building_permit_issued"})
PRODUCT_TYPE_FIELD = "product_type"


@dataclass(frozen=True, slots=True)
class _ProductTypeMatch:
    product_type: ProductType
    reason_code: str
    confidence: Confidence
    source_field: str
    source_text: str


class LadbsProductTypeInterpreter(SemanticInterpreter):
    field_name = PRODUCT_TYPE_FIELD
    source_profile = PERMIT_PROFILE_NAME

    def interpret(
        self,
        observations: SourceObservations,
        context: InterpreterContext,
    ) -> Sequence[SemanticInterpretation]:
        del context
        if observations.source_type not in LADBS_PRODUCT_SOURCE_TYPES:
            return ()
        fields = observations.reference_payload or observations.native_payload
        if not _is_direct_product_row(fields):
            return ()
        product_match = _product_type_match(fields)
        if product_match is None:
            return ()
        return (
            SemanticInterpretation(
                field_name=PRODUCT_TYPE_FIELD,
                canonical_value=product_match.product_type.value,
                confidence=product_match.confidence,
                reason_code=product_match.reason_code,
                source_anchors=(
                    PassageAnchor(
                        text=product_match.source_text,
                        field_name=product_match.source_field,
                        metadata={
                            "source_profile": PERMIT_PROFILE_NAME,
                            "source_type": observations.source_type,
                        },
                    ),
                ),
                metadata={
                    "source_profile": PERMIT_PROFILE_NAME,
                    "source_type": observations.source_type,
                    "source_field": product_match.source_field,
                },
            ),
        )


LADBS_PRODUCT_TYPE_INTERPRETER = LadbsProductTypeInterpreter()


def enrich_ladbs_mapped_fields(
    *,
    source_name: str,
    mapped_fields: Mapping[str, Any],
) -> dict[str, Any]:
    enriched = dict(mapped_fields)
    if _has_value(enriched.get(PRODUCT_TYPE_FIELD)):
        return enriched
    for interpretation in interpret_ladbs_mapped_fields(
        source_name=source_name,
        mapped_fields=enriched,
    ):
        if interpretation.field_name == PRODUCT_TYPE_FIELD and _has_value(
            interpretation.canonical_value
        ):
            enriched[PRODUCT_TYPE_FIELD] = interpretation.canonical_value
            break
    return enriched


def interpret_ladbs_mapped_fields(
    *,
    source_name: str,
    mapped_fields: Mapping[str, Any],
) -> tuple[SemanticInterpretation, ...]:
    source_type = get_logical_source_type(source_name)
    observations = SourceObservations(
        source_profile=PERMIT_PROFILE_NAME,
        source_type=source_type,
        native_payload=mapped_fields,
        reference_payload=mapped_fields,
    )
    context = InterpreterContext(source_profile=PERMIT_PROFILE_NAME)
    interpretations = LADBS_PRODUCT_TYPE_INTERPRETER.interpret(observations, context)
    return tuple(interpretations)


def ladbs_semantic_metadata_by_field(
    *,
    source_name: str,
    mapped_fields: Mapping[str, Any],
) -> dict[str, SemanticInterpretation]:
    metadata: dict[str, SemanticInterpretation] = {}
    for interpretation in interpret_ladbs_mapped_fields(
        source_name=source_name,
        mapped_fields=mapped_fields,
    ):
        if not _same_value(
            mapped_fields.get(interpretation.field_name),
            interpretation.canonical_value,
        ):
            continue
        metadata[interpretation.field_name] = interpretation
    return metadata


def _is_direct_product_row(fields: Mapping[str, Any]) -> bool:
    return (
        _clean_text(fields.get("status_evidence_type"))
        in LADBS_DIRECT_PRODUCT_STATUS_EVIDENCE_TYPES
    )


def _product_type_match(fields: Mapping[str, Any]) -> _ProductTypeMatch | None:
    for source_field in ("housing_use_desc", "use_desc", "permit_sub_type", "description"):
        source_text = _clean_text(fields.get(source_field))
        if not source_text:
            continue
        product_type = _product_type_from_text(source_text)
        if product_type is None:
            continue
        confidence: Confidence = "high" if source_field != "description" else "medium"
        return _ProductTypeMatch(
            product_type=product_type,
            reason_code=_reason_code_for_product_type(product_type),
            confidence=confidence,
            source_field=source_field,
            source_text=source_text,
        )
    return None


def _product_type_from_text(value: str) -> ProductType | None:
    normalized = _normalized_text(value)
    # Specific residential signals come before generic apartment/family language.
    # Condo precedes apartment because LADBS descriptions can say "condominium apartment."
    if _contains_any(normalized, ("co living", "coliving", "micro unit", "micro-unit")):
        return ProductType.MICRO_CO_LIVING
    if _contains_any(normalized, ("townhome", "townhomes", "townhouse", "townhouses")):
        return ProductType.TOWNHOME
    if _contains_any(normalized, ("condo", "condos", "condominium", "condominiums")):
        return ProductType.CONDO
    if _contains_any(normalized, ("apartment", "apartments", "multi family", "multifamily")):
        return ProductType.APARTMENT
    if re.search(r"\bapt\b", normalized):
        return ProductType.APARTMENT
    if _contains_any(
        normalized,
        ("dwelling single family", "single family", "1 or 2 family dwelling"),
    ):
        return ProductType.SINGLE_FAMILY
    return None


def _reason_code_for_product_type(product_type: ProductType) -> str:
    return {
        ProductType.APARTMENT: "ladbs_product_type_apartment",
        ProductType.CONDO: "ladbs_product_type_condo",
        ProductType.TOWNHOME: "ladbs_product_type_townhome",
        ProductType.SINGLE_FAMILY: "ladbs_product_type_single_family",
        ProductType.MICRO_CO_LIVING: "ladbs_product_type_micro_co_living",
    }[product_type]


def _contains_any(value: str, needles: tuple[str, ...]) -> bool:
    return any(needle in value for needle in needles)


def _normalized_text(value: str) -> str:
    return " ".join(
        "".join(character.lower() if character.isalnum() else " " for character in value).split()
    )


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _same_value(left: Any, right: Any) -> bool:
    if isinstance(left, ProductType):
        left = left.value
    if isinstance(right, ProductType):
        right = right.value
    return str(left) == str(right)

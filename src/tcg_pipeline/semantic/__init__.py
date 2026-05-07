"""Shared semantic interpretation framework."""

from tcg_pipeline.semantic.reason_codes import (
    REASON_CODES_BY_CODE,
    REASON_CODES_BY_PROFILE_FIELD,
    ReasonCode,
    reason_code_for,
    validate_reason_code_registry,
)
from tcg_pipeline.semantic.types import (
    Confidence,
    InterpreterContext,
    PassageAnchor,
    SemanticInterpretation,
    SemanticInterpreter,
    SourceObservations,
)

__all__ = [
    "Confidence",
    "InterpreterContext",
    "PassageAnchor",
    "REASON_CODES_BY_CODE",
    "REASON_CODES_BY_PROFILE_FIELD",
    "ReasonCode",
    "SemanticInterpretation",
    "SemanticInterpreter",
    "SourceObservations",
    "reason_code_for",
    "validate_reason_code_registry",
]

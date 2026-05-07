from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from uuid import UUID

Confidence = Literal["low", "medium", "high"]


@dataclass(frozen=True, slots=True)
class PassageAnchor:
    """Text span or structured source detail supporting an interpretation."""

    text: str
    offset_start: int | None = None
    offset_end: int | None = None
    field_name: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SemanticInterpretation:
    """Canonical semantic output emitted by source-profile interpreters."""

    field_name: str
    canonical_value: Any | None
    confidence: Confidence
    reason_code: str
    signal_flags: Mapping[str, Any] = field(default_factory=dict)
    source_anchors: tuple[PassageAnchor, ...] = ()
    requires_corroboration: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.field_name:
            raise ValueError("field_name is required")
        if not self.reason_code:
            raise ValueError("reason_code is required")
        if self.canonical_value is None and not self.signal_flags:
            raise ValueError("signal-only interpretations must include signal_flags")


@dataclass(frozen=True, slots=True)
class SourceObservations:
    """Source-profile-shaped inputs normalized enough for shared interpreters."""

    source_profile: str
    source_type: str
    native_payload: Mapping[str, Any] = field(default_factory=dict)
    body_text: str | None = None
    structural_signals: Sequence[Mapping[str, Any]] = ()
    reference_payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class InterpreterContext:
    """Read-only project and policy context shared across interpreters."""

    project_id: UUID | None = None
    project_state: Mapping[str, Any] = field(default_factory=dict)
    jurisdiction_slug: str | None = None
    jurisdiction_policy: Mapping[str, Any] = field(default_factory=dict)
    recent_evidence: Sequence[Mapping[str, Any]] = ()


class SemanticInterpreter(Protocol):
    field_name: str
    source_profile: str

    def interpret(
        self,
        observations: SourceObservations,
        context: InterpreterContext,
    ) -> Sequence[SemanticInterpretation]: ...

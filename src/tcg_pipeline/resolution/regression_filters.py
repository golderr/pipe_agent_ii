"""Suppression rules for status-regression-candidate emission.

The resolver's slice-2 regression-candidate enumeration emits one candidate per
lower-ranked observation, which catches genuine regressions but produces false
positives when a new LADBS permit issuance lands on a project that is already
Under Construction or Complete from earlier LADBS evidence. That is normal
additive paperwork, not a lifecycle regression.

This module owns the allowlists and the suppression predicate so the rule lives
in one place and can be extended as new LADBS `status_desc` values appear in
production.

Naming note: this filter is scoped to LADBS evidence today. The pattern
generalizes (e.g., CoStar might need its own additive/regression split), but
each source family's value-set is different enough that a per-source predicate
is clearer than a single generic one.
"""

from __future__ import annotations

import logging
from typing import Any

from tcg_pipeline.db.models import Evidence

logger = logging.getLogger(__name__)

# Source types treated as LADBS source family for this filter.
LADBS_SOURCE_TYPES: frozenset[str] = frozenset(
    {
        "ladbs_permit",
        "ladbs_permit_activity",
    }
)

# `status_desc` values that indicate the permit is in force or progressing
# normally. A new LADBS row with any of these on an already-higher-status
# project is additive paperwork, not a regression.
LADBS_ADDITIVE_STATUS_DESC: frozenset[str] = frozenset(
    {
        "Issued",
        "Permit Finaled",
        "Ready to Issue",
        "Plan Check Submitted",
        "Plans Approved",
        "Pending Inspection",
        "CofO Issued",
        "CofO Pending",
        "",  # empty/None coerces here
    }
)

# `status_desc` values that indicate the permit is no longer in force.
# These ARE genuine regression signals worth surfacing for review.
LADBS_REGRESSION_STATUS_DESC: frozenset[str] = frozenset(
    {
        "Cancelled",
        "Permit Cancelled",
        "Void",
        "Revoked",
        "Expired",
        "Withdrawn",
        "Plan Check Cancelled",
    }
)

LADBS_UNKNOWN_STATUS_ALERT_KEY = "ladbs_unknown_permit_status"


def is_benign_ladbs_additive_paperwork(
    evidence: Evidence,
) -> tuple[bool, dict[str, Any] | None]:
    """Return whether this LADBS evidence is benign additive paperwork.

    Benign additive paperwork should not emit a status-regression candidate even
    when the evidence maps to a lower-ranked status than the project's current
    status. Example: a `Bldg-New` permit with `status_desc='Issued'` lands on an
    Under Construction project; without this filter it would emit a
    UC-to-Approved regression candidate, which is a false positive.

    Unknown ``status_desc`` values are treated as additive (fail-additive
    sentinel) and return a pending `ladbs_unknown_permit_status` alert payload
    so the session-owning engine can raise the alert without making this
    predicate perform database side effects.
    """
    if evidence.source_type not in LADBS_SOURCE_TYPES:
        return False, None
    raw = evidence.raw_data if isinstance(evidence.raw_data, dict) else {}
    status_desc = (raw.get("status_desc") or "").strip()
    if status_desc in LADBS_REGRESSION_STATUS_DESC:
        return False, None
    if status_desc in LADBS_ADDITIVE_STATUS_DESC:
        return True, None
    return True, _unknown_ladbs_status_alert(
        status_desc=status_desc,
        evidence_id=evidence.id,
    )


def _unknown_ladbs_status_alert(
    *,
    status_desc: str,
    evidence_id: Any,
) -> dict[str, Any]:
    logger.warning(
        "LADBS unknown status_desc=%r treated as additive (evidence_id=%s)",
        status_desc,
        evidence_id,
    )
    return {
        "alert_key": LADBS_UNKNOWN_STATUS_ALERT_KEY,
        "severity": "info",
        "message": (
            f"LADBS evidence has unknown status_desc={status_desc!r}; "
            "treating as additive paperwork (no regression candidate emitted). "
            "Extend the allowlist in "
            "src/tcg_pipeline/resolution/regression_filters.py if this value "
            "should drive a regression."
        ),
        "scope": {"status_desc": status_desc},
        "detail": {"evidence_id": str(evidence_id)},
    }

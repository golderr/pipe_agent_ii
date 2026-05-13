"""Suppression rules for status-regression-candidate emission.

The resolver's slice-2 regression-candidate enumeration emits one candidate per
lower-ranked observation, which catches genuine regressions but produces false
positives when a new LADBS permit issuance lands on a project that is already
Under Construction or Complete from earlier LADBS evidence — that's normal
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
import uuid
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from tcg_pipeline.db.models import Evidence

if TYPE_CHECKING:  # pragma: no cover - import-time only
    pass

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
    *,
    session: Session | None = None,
) -> bool:
    """Return True when this LADBS evidence is benign additive paperwork.

    Benign additive paperwork should NOT emit a status-regression candidate even
    when the evidence maps to a lower-ranked status than the project's current
    status. Example: a `Bldg-New` permit with `status_desc='Issued'` lands on an
    Under Construction project; without this filter it would emit a UC→Approved
    regression candidate, which is a false positive.

    Unknown ``status_desc`` values are treated as additive (fail-additive
    sentinel) and raise a `ladbs_unknown_permit_status` system_alert scoped by
    the unknown value so operators can extend the allowlist. The alert is
    upserted on `(alert_key, scope)` so duplicate raises within the same day
    merge into a single active row.
    """
    if evidence.source_type not in LADBS_SOURCE_TYPES:
        return False
    raw = evidence.raw_data if isinstance(evidence.raw_data, dict) else {}
    status_desc = (raw.get("status_desc") or "").strip()
    if status_desc in LADBS_REGRESSION_STATUS_DESC:
        return False
    if status_desc in LADBS_ADDITIVE_STATUS_DESC:
        return True
    _log_unknown_ladbs_status(
        status_desc=status_desc,
        evidence_id=evidence.id,
        session=session,
    )
    return True


def _log_unknown_ladbs_status(
    *,
    status_desc: str,
    evidence_id: uuid.UUID,
    session: Session | None,
) -> None:
    """Emit a per-value system_alert for an unknown LADBS status_desc.

    The alert is scoped by the unknown value, so repeated occurrences of the
    same unknown value merge into a single active alert via the existing
    upsert-on-conflict pattern in ``raise_system_alert``.
    """
    logger.warning(
        "LADBS unknown status_desc=%r treated as additive (evidence_id=%s)",
        status_desc,
        evidence_id,
    )
    if session is None:
        return
    # Local import to avoid a regression-filters -> workers import cycle.
    from tcg_pipeline.workers.news_jobs import raise_system_alert

    try:
        raise_system_alert(
            session,
            alert_key=LADBS_UNKNOWN_STATUS_ALERT_KEY,
            severity="info",
            message=(
                f"LADBS evidence has unknown status_desc={status_desc!r}; "
                "treating as additive paperwork (no regression candidate "
                "emitted). Extend the allowlist in "
                "src/tcg_pipeline/resolution/regression_filters.py if this "
                "value should drive a regression."
            ),
            scope={"status_desc": status_desc},
            detail={"evidence_id": str(evidence_id)},
        )
    except Exception:  # noqa: BLE001 - alert failure must not block resolve
        logger.exception("Failed to raise ladbs_unknown_permit_status alert")

# UI Requirements

Updated: 2026-04-23

This document captures UI requirements discovered during Phase A validation and should be treated as required reading before Phase B work begins. It complements `ARCHITECTURE.md`, `ROADMAP.md`, and the evidence-layer specs.

## 1. Status-Change Display

When the UI shows any status progression, it must clearly surface the specific evidence that drove the change.

Requirements:

- Show the sole winning evidence row for the status change:
  - source
  - evidence date
  - collected-at timestamp
  - rule applied
- If the status change is driven by a single low-tier source, show that prominently and flag it for researcher review.
- Example pattern:
  - "Promoted to Approved based on CoStar `pipeline_status` alone."
- The detail view should make it obvious whether the change came from:
  - direct Tier 1 status evidence
  - indirect promotion logic
  - a single-source Tier 3 inference

Rationale:

- Phase A included a CoStar-only `Pending -> Approved` status change. The UI must not present that as equivalent to a government-backed promotion.

## 2. Delivery Estimation Controls

The `estimated_calc` delivery-date formula needs to be tunable in the product, not hardcoded forever.

Requirements:

- Global controls:
  - operators can adjust the default year offsets by status
  - operators can adjust any size-based modifiers
- Per-export controls:
  - an export workflow can choose a more conservative or more aggressive assumption set without changing the global default
- The UI must always distinguish:
  - explicit date
  - estimated date
  - researcher override date

Implementation note:

- Externalize the delivery-estimation coefficients alongside the planned likelihood YAML work in Phase E.3.

## 3. Units-Change Review UI

The units review interface must let a reviewer compare the old value and the proposed overwrite without leaving the screen.

Requirements:

- Show side-by-side:
  - current value
  - evidence that established the current value
  - resolved value
  - evidence that would overwrite it
- For each side, surface:
  - source
  - evidence date
  - raw record link or raw record snippet
  - rule applied
- Show `delta_abs` and `delta_shape` directly in the review row.

Rationale:

- Phase A established a policy split at `abs(delta) <= 5` vs. larger deltas. The UI should make that threshold obvious and reviewable.

## 4. Same-Project Verification UI

Larger unit-count changes can mean the sources are talking about different projects or different phases of the same project.

Requirements:

- Show a mini-map with the geolocation of each source record contributing to the comparison.
- Surface phase markers from raw fields, including terms like:
  - `phase`
  - `tower`
  - `building`
  - numbered subcomponents
- Show same-project matching context:
  - matcher confidence
  - what matched
  - what did not match
  - address / identifier / APN agreement or disagreement
- Trigger this verification affordance on:
  - any `total_units` delta greater than 5
  - any address shift
  - any project-name shift

## 5. Evidence-Focused Developer Review

Developer review needs enough context to distinguish real developers from architecture firms, general contractors, and noisy source values.

Requirements:

- Show both:
  - raw developer value
  - resolved / canonical target
- Show the canonicalization rule used:
  - exact canonical
  - exact alias
  - fuzzy auto
  - fuzzy review
  - new registry entry
- Make architecture-firm exceptions easy to hold at the current value.

Rationale:

- Phase A surfaced several rows where CoStar supplied an architecture firm instead of a developer.

## 6. Cross-References

Phase B and later UI work should be read alongside:

- `ROADMAP.md` Phase B and Phase C
- `ARCHITECTURE.md` Section 3d
- `ARCHITECTURE.md` Section 3e
- `ARCHITECTURE.md` Section 6
- `docs/specs/EVIDENCE_LAYER_INTEGRATION_GUIDE.md`
- `docs/specs/EVIDENCE_LAYER_DECISIONS.md`

# Phase A Validation

Generated: 2026-04-23

## Current State

Phase A is in review/validation. Shadow outputs have been generated from live Los Angeles `resolution_log` data and split into decision-bearing review CSVs plus FYI packets.

Review packet directory:

- `docs/notes/phase_a_review/status_review.csv`
- `docs/notes/phase_a_review/units_review.csv`
- `docs/notes/phase_a_review/delivery_review.csv`
- `docs/notes/phase_a_review/developer_review.csv`
- `docs/notes/phase_a_review/developer_category_cleanup.csv`
- `docs/notes/phase_a_review/developer_helio_ucla_cluster.csv`
- `docs/notes/phase_a_review/developer_alias_candidates.csv`
- `docs/notes/phase_a_review/delivery_estimate_spotcheck.csv`

## Review Counts

- Status review rows: `9`
- Units review rows: `27`
- Delivery explicit-overwrite review rows: `35`
- Delivery estimated-fill population: `218`
- Delivery estimate spot-check rows: `10`
- Developer review rows (non-`Category`): `44`
- Developer `Category` cleanup FYI rows: `84`
- `Helio / UCLA` developer cluster rows: `10`
- Likely alias-add candidates: `2`

## Current Findings

- Status packet is small and internally consistent:
  - `8` rows are `Approved/Proposed -> Under Construction` backed by recent substantive `ladbs_inspection` evidence.
  - `1` row is `Pending -> Approved` from CoStar.
  - No remaining status deltas involve `Stalled` or `Inactive`.
- Developer review has been split so the `84` `Category -> ...` cleanup rows are out of the judgment queue and tracked separately as FYI.
- The remaining developer review queue is `44` rows, sorted with `most_recent_wins_canonicalized` first and `most_recent_wins_canonicalization_review_required` last.
- The `Helio / UCLA` rows are tagged with `review_cluster = helio_ucla`, and the full 10-row group is exported separately for cluster-level review.
- Two likely missing registry aliases were isolated:
  - `Jamison Services -> Jamison Properties`
  - `Wiseman Development -> Wiseman Residential`

## Tooling Added

- `scripts/export_phase_a_reviews.py`
  - Regenerates the Phase A review packet from live shadow data.
- `scripts/apply_phase_a_decisions.py`
  - Reads filled review CSVs and writes `researcher_override` entries for `override` / `defer` decisions.
  - Dry-run currently reports all rows as pending because no decisions have been filled yet.

## Pending Decisions

- Delivery estimate-fill policy is not yet recorded. A 10-row spot-check sample has been exported in `delivery_estimate_spotcheck.csv` for the one-time policy decision on whether `estimated_calc` should fill blank `date_delivery` values in Phase A.
- No override CSV decisions have been applied yet.

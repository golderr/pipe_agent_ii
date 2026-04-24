# Phase A Validation

Generated: 2026-04-23
Updated: 2026-04-23

## Final Status

Phase A is complete for the Los Angeles market.

- A.5 shadow validation completed from the final LA rerun in `docs/notes/phase_a_review/reshadow_run_postfix.log`.
- A.6 review decisions were applied through the bucket profile in `scripts/apply_phase_a_decisions.py`.
- A.7 shadow canonicalization completed and the post-fix review packet was regenerated from the current `resolution_log`.
- A.8 apply completed across all 1,362 LA projects.
- A.9 spot checks passed after apply.

## Review Artifacts

Review packet directory: `docs/notes/phase_a_review/`

- `status_review.csv`: `9`
- `units_review.csv`: `27`
- `delivery_review.csv`: `34`
- `delivery_estimate_spotcheck.csv`: `10`
- `developer_review.csv`: `161`
- `developer_category_cleanup.csv`: `84`
- `developer_canonical_cleanup.csv`: `59`
- `developer_helio_ucla_cluster.csv`: `10`
- `developer_alias_candidates.csv`: `0`

Packet integrity checks after the final shadow rerun:

- No Jamison / Wiseman alias-cleanup rows remained in `developer_review.csv`; those moved into `developer_canonical_cleanup.csv`.
- No `Complete` plus future `date_delivery` rows remained.
- The earlier `Capital` / `Investment` pollution targets no longer dominated the developer review queue.

## Phase A Decisions

Bucket-level decisions used for A.8:

- `status_review.csv`: accept all `9`.
- `delivery_estimate_spotcheck.csv`: accept all `218` represented estimate fills as policy.
- `delivery_review.csv`: accept all `34` explicit delivery-date overwrites.
- `units_review.csv`: accept `7` rows with `abs(delta) <= 5`; defer `20` larger deltas via `until_newer_evidence` overrides.
- `developer_category_cleanup.csv`: accept all `84` as data hygiene.
- `developer_canonical_cleanup.csv`: accept all `59` as exact alias / canonical cleanup.
- `developer_review.csv`: accept `157`; write `4` overrides.

Developer override handling:

- `3` rows kept the current developer because the raw value was an architecture firm rather than a developer.
- `1` row forced the raw CoStar value instead of the canonicalized target.

Relevant policy decisions were recorded in `docs/specs/EVIDENCE_LAYER_DECISIONS.md`.

## Apply Results

Decision-profile dry-run:

- Command: `python scripts/apply_phase_a_decisions.py --decision-profile phase_a_2026_04_23 --dry-run`
- Loaded CSV rows: `384`
- `accept`: `360`
- `defer`: `20`
- `override`: `4`
- Would write overrides: `24`

Override write:

- Command: `python scripts/apply_phase_a_decisions.py --decision-profile phase_a_2026_04_23`
- Overrides written: `24`

Project apply:

- `resolve-all --apply` completed for all `1,362` LA projects using the stable batched runner in `scripts/run_phase_a_resolve.py`.
- The final resume segment is recorded in `docs/notes/phase_a_review/phase_a_apply_resume.log`.
- Resume summary:
  - Projects resolved: `1,286`
  - Projects with discrepancies: `1,285`
  - Changed fields detected: `8,254`
  - Resolution log rows written: `7,234`

Field counts from the resume segment:

- `confidence`: `280`
- `date_delivery`: `233`
- `developer`: `281`
- `pipeline_status`: `9`
- `total_units`: `6`

Developer canonicalization apply:

- Registry rows scanned: `316`
- Registry rows merged: `1`
- Registry rows created: `172`
- Aliases created: `5`
- Projects scanned: `967`
- Projects changed: `26`
- Exact matches: `1,046`
- Fuzzy auto matches: `6`
- Fuzzy review matches: `6`
- New registry entries: `172`

Current DB sanity checks after A.8 / A.9:

- `resolution_log` field counts: `pipeline_status = 9`, `total_units = 7`, `date_delivery = 252`, `developer = 302`
- Projects with any researcher override: `23`
- Projects with `total_units` override: `20`
- Projects with `developer` override: `4`
- Override/value mismatches: `0`

## Post-Apply Fixes

One project (`Lake on Wilshire`) surfaced a real bug during A.8:

- `canonicalize-developers --apply` rewrote a raw developer override value after `resolve-all --apply`.
- The project was immediately re-resolved and corrected.
- Code now prevents future canonicalization sweeps from rewriting `project.developer` when `researcher_override.developer` is present.

## Verification

Test status:

- `python -m pytest -q`: `173 passed`

Representative spot checks:

- `Miles at Highland` is `Complete` with a non-future `date_delivery`.
- Large-unit defer case held the current `total_units` value.
- Small-unit accept case applied the resolved `total_units` value.
- Developer override exceptions held for:
  - `NOW`
  - `Pico Gateway Apartments`
  - `2023 WESTWOOD BOULEVARD`

## Follow-On References

- Tracker: `ROADMAP.md`
- Policy decisions: `docs/specs/EVIDENCE_LAYER_DECISIONS.md`
- Phase B+ UI requirements: `docs/specs/ui_requirements.md`

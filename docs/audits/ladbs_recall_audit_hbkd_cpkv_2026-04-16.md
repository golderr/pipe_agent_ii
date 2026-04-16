# LADBS Recall Audit Supplement: `hbkd-qubn` + `cpkv-aajs`

Date run: 2026-04-16

Related docs:

- [ladbs_recall_audit_plan.md](./ladbs_recall_audit_plan.md)
- [ladbs_recall_audit_results_2026-04-16.md](./ladbs_recall_audit_results_2026-04-16.md)

This supplement reruns the LADBS recall audit **without** `3f9m-afei` so we can attribute what
`cpkv-aajs` contributes on its own before CofO evidence enters the picture.

## Scope of This Supplement

Sources included:

- `hbkd-qubn`
- `cpkv-aajs`

Source intentionally excluded:

- `3f9m-afei`

This pass is narrower than the original family audit. It is meant to answer:

- how much recall comes from the current `hbkd-qubn` slice
- how much broader `hbkd-qubn` activity recovers
- how much `cpkv-aajs` alone rescues when `hbkd-qubn` misses entirely

It is **not** a full replacement for the original audit because:

- it does not include CofO coverage
- it does not attempt the earlier manual "credible explanation" split for not-found projects
- it uses the current `los_angeles` project snapshot as of 2026-04-16, so cohort sizes differ from
  the earlier audit document

## Method

For each `los_angeles` project in the current `Approved`, `Under Construction`, and `Complete`
cohorts, the audit checked:

1. `hbkd-qubn` under the current `permit_type='Bldg-New'` logic
2. broader `hbkd-qubn` activity at the same address
3. `cpkv-aajs` by address
4. `cpkv-aajs` by APN if address did not match and an APN was available

Primary outcome buckets in this supplement are:

1. found under current `hbkd-qubn` filter
2. found only in broader `hbkd-qubn`
3. found only in `cpkv-aajs`
4. not found in either source

## Current Cohort Sizes

- `Approved`: 83 projects
- `Under Construction`: 253 projects
- `Complete`: 200 projects

## Executive Summary

`cpkv-aajs` is valuable, but on the current database snapshot it is **not** a major standalone
recall rescuer.

Its main contribution in this pass is:

- strengthening housing-specific enrichment
- providing APN-driven recovery when address matching misses
- supplying coordinates and assessor identity fields that `hbkd-qubn` does not expose as cleanly

Its direct recall lift over `hbkd-qubn` alone was:

- `Approved`: 0 projects
- `Under Construction`: 3 projects
- `Complete`: 0 projects

That means the main justification for building `cpkv-aajs` was still correct, but the justification
is mostly **enrichment and identifier quality**, not "this source closes the majority of LADBS recall
gaps by itself."

## Results by Cohort

### `Approved` (83 projects)

- found under current `Bldg-New` filter: 3 (`3.6%`)
- found only in broader `hbkd-qubn`: 40 (`48.2%`)
- found only in `cpkv-aajs`: 0 (`0.0%`)
- not found in either source: 40 (`48.2%`)

Independent `cpkv-aajs` coverage:

- 2/83 (`2.4%`) projects matched in `cpkv-aajs`
- both were address matches
- none were `cpkv-aajs`-only recoveries

Interpretation:

- `cpkv-aajs` adds almost nothing to `Approved` recall on its own
- the main `Approved` gap remains upstream of this source and will need other source families or
  better early-lifecycle matching

### `Under Construction` (253 projects)

- found under current `Bldg-New` filter: 66 (`26.1%`)
- found only in broader `hbkd-qubn`: 97 (`38.3%`)
- found only in `cpkv-aajs`: 3 (`1.2%`)
- not found in either source: 87 (`34.4%`)

Independent `cpkv-aajs` coverage:

- 78/253 (`30.8%`) projects matched in `cpkv-aajs`
- 70 matched by address
- 8 matched only by APN
- all 3 `cpkv-aajs`-only recoveries were APN-based

Examples of `cpkv-aajs`-only recoveries:

- `Hotel La Fleur Los Angeles, Outset Collection by Hilton` at
  `1318 SOUTH FLOWER STREET LOS ANGELES CA 90015`
- `502 NORTH OXFORD AVENUE LOS ANGELES CA 90004`
- `Hudson` at `640 SOUTH ST ANDREWS PLACE LOS ANGELES CA 90005`

Interpretation:

- `cpkv-aajs` overlaps heavily with `hbkd-qubn`, but it still matters because APN-based recovery
  found projects that the address-based `hbkd-qubn` pass missed entirely
- this is a strong argument for keeping APN capture in the adapter even though the standalone recall
  bucket is small

Broader-only `hbkd-qubn` permit-type distribution in this cohort:

- broader-only projects in current snapshot: 81
- top permit types by project presence:
  - `Electrical`: 52 projects
  - `Plumbing`: 51 projects
  - `Bldg-Demolition`: 38 projects
  - `Bldg-Alter/Repair`: 31 projects
  - `HVAC`: 23 projects
  - `Fire Sprinkler`: 16 projects
  - `Elevator`: 15 projects
  - `Grading`: 12 projects

Interpretation:

- the broader-only recovery is **not** mainly a hidden pool of additional `Bldg-New` records
- it is mostly general permit activity attached to projects we already care about
- that argues against widening the current `ladbs_permits` source and reusing the same
  `building_permit_issued` evidence semantics
- the cleaner next design is a second `hbkd-qubn` activity source, or equivalent chaining logic,
  that captures broader permit activity without pretending it means the same thing as a new-building
  issuance

### `Complete` (200 projects)

- found under current `Bldg-New` filter: 129 (`64.5%`)
- found only in broader `hbkd-qubn`: 45 (`22.5%`)
- found only in `cpkv-aajs`: 0 (`0.0%`)
- not found in either source: 26 (`13.0%`)

Independent `cpkv-aajs` coverage:

- 134/200 (`67.0%`) projects matched in `cpkv-aajs`
- all were address matches
- none were `cpkv-aajs`-only recoveries

Interpretation:

- `cpkv-aajs` covers many completed projects, but in this pass it did not rescue any that broader
  `hbkd-qubn` had missed
- that reinforces the earlier plan: `3f9m-afei` is still the next source to build if we want
  completion-specific attribution

## What Changed in the Plan

This supplement supports the sequencing change:

1. audit `hbkd-qubn + cpkv-aajs`
2. fix and validate `3f9m-afei` completion semantics
3. decide how to model broader `hbkd-qubn` activity separately from the narrow `Bldg-New` slice
4. then rerun the broader LADBS family audit with clean per-source attribution

## Decisions Supported by This Supplement

1. Keep `cpkv-aajs` in the LADBS bundle.
2. Treat `cpkv-aajs` primarily as a housing-enrichment and identifier-quality source, not as the
   main standalone recall fix for LADBS gaps.
3. Preserve APN matching in the adapter and matcher path because the only `cpkv-aajs`-only
   recoveries in this pass came from APN-based matching.
4. Keep `ladbs_permits` as the narrow `Bldg-New` evidence slice rather than widening it blindly.
5. Model broader `hbkd-qubn` recovery as permit activity, not as equivalent permit-issuance
   evidence.

# LADBS Recall Audit Results

Date run: 2026-04-16

Related plan: [ladbs_recall_audit_plan.md](./ladbs_recall_audit_plan.md)

This document records the first recall audit of the current Los Angeles LADBS permit strategy.

The audited source configuration was:

- source: `ladbs_permits`
- dataset: `hbkd-qubn`
- current inclusion rule: `permit_type='Bldg-New'`

## Executive Summary

The current `Bldg-New` filter is too narrow to serve as the main LADBS lifecycle entry point.

It is reasonably strong for later-stage completed projects, but it misses a large share of seeded `Under Construction` projects and almost all seeded `Approved` projects. A large number of those projects still show up in LADBS, but only through broader permit activity at the same address or through sibling datasets such as `cpkv-aajs`.

The audit does **not** support simply widening `hbkd-qubn` to all permit types. Many broader matches are demolition, plumbing, electrical, or alteration permits. Those records are useful as project-level permit activity, but they are not equivalent to "new building permit issued" evidence.

The practical conclusion is:

- keep `Bldg-New` as a narrow, high-signal slice
- stop treating it as the full LADBS answer
- continue with the LADBS bundle strategy
- make the next implementation step collector-level completeness work, not a blind filter expansion

## Method

The audit checked all seeded `los_angeles` projects in three cohorts:

- `Under Construction`: 224 projects
- `Complete`: 200 projects
- `Approved`: 74 projects

For each project, the audit attempted to match in this order:

1. current `hbkd-qubn` inclusion rule
2. broader `hbkd-qubn` search without the current filter
3. sibling LADBS datasets:
   - `cpkv-aajs`
   - `3f9m-afei`

The first-pass search keys were:

- canonical address decomposed into `address_start`, `street_name`, and ZIP
- APN for `cpkv-aajs` and `3f9m-afei` where available
- permit numbers if present in the database

## Important Caveats

- This was a conservative, deterministic first-pass audit, not a fuzzy matching exercise.
- A broader `hbkd-qubn` match means "some permit activity was found at the project address." It does **not** mean that row alone is sufficient status evidence.
- Permit-number coverage in the seeded cohorts was effectively zero, so the audit depended mainly on address matching.
- APN coverage was uneven:
  - `Under Construction`: 169/224
  - `Approved`: 26/74
  - `Complete`: 1/200
- Because `Complete` had almost no APN coverage, `3f9m-afei` recall is likely understated.
- `ydma-y4hd` returned `404` during direct endpoint checks in this session, so it was not included in the results below.

## Result Buckets

### `Approved` (74 projects)

- found under current `Bldg-New` filter: 3 (`4.1%`)
- found only in broader `hbkd-qubn`: 36 (`48.6%`)
- found only in sibling dataset: 0 (`0.0%`)
- not found, but with a credible explanation: 35 (`47.3%`)

Interpretation:

- `Approved` is the weakest cohort for a strict permit-recall expectation.
- Nearly half still show some LADBS permit activity at the address, but almost none show up under `Bldg-New`.
- The other half may still be legitimately pre-permit, may have stale seeded statuses, or may need non-permit sources for earlier lifecycle tracking.

### `Under Construction` (224 projects)

- found under current `Bldg-New` filter: 62 (`27.7%`)
- found only in broader `hbkd-qubn`: 83 (`37.1%`)
- found only in sibling dataset: 3 (`1.3%`)
- not found, but plausibly should be found: 75 (`33.5%`)
- not found, credible explanation: 1 (`0.4%`)

Interpretation:

- This is the clearest signal that the current filter is insufficient.
- Only about one quarter of seeded `Under Construction` projects were recovered by the current `Bldg-New` logic.
- Another 37% showed some broader LADBS permit activity, which means the current slice is missing many projects that LADBS does know about.
- One third were still not found in `hbkd-qubn`, `cpkv-aajs`, or `3f9m-afei`, which suggests a combination of:
  - matching/key gaps
  - lifecycle ambiguity in the seeded data
  - incompleteness in the queried LADBS datasets
  - the need for additional source chaining

### `Complete` (200 projects)

- found under current `Bldg-New` filter: 130 (`65.0%`)
- found only in broader `hbkd-qubn`: 45 (`22.5%`)
- found only in sibling dataset: 0 (`0.0%`)
- not found, but plausibly should be found: 24 (`12.0%`)
- not found, credible explanation: 1 (`0.5%`)

Interpretation:

- `Bldg-New` performs materially better here than it does for `Under Construction`.
- Even so, over one fifth of `Complete` projects were only visible through broader permit activity.
- The remaining misses are too large to dismiss as noise.

## Independent Sibling-Dataset Coverage

Because the first pass stopped once a project matched in `hbkd`, a second pass checked sibling datasets independently.

### `cpkv-aajs` coverage

- `Approved`: 2/74 (`2.7%`)
- `Under Construction`: 74/224 (`33.0%`)

Interpretation:

- `cpkv-aajs` is not a replacement for `hbkd-qubn`, but it is material enrichment for active projects.
- It is especially useful for housing-specific detail and should be treated as a real part of the LADBS bundle, not a nice-to-have extra.

### `3f9m-afei` coverage

- `Complete`: 46/200 (`23.0%`)

Interpretation:

- `3f9m-afei` is useful, but it is not comprehensive enough to stand alone as the sole completion source.
- Because APN coverage in the `Complete` cohort was almost nonexistent, this coverage figure likely understates the dataset's true value.

## Qualitative Pattern in Broader `hbkd-qubn` Matches

Many projects that missed the current filter were still found in `hbkd-qubn` through permit rows such as:

- `Bldg-Demolition`
- `Bldg-Alter/Repair`
- `Electrical`
- `Plumbing`
- `HVAC`

That is useful evidence that LADBS is aware of the project address, but it does not justify redefining all of those permit types as equivalent to new-building evidence.

This is the central result of the audit:

- the current filter is too narrow for recall
- a fully broad permit pull would be too noisy if treated as the same semantic signal

That is exactly why the LADBS bundle needs field-authority and status-evidence rules.

## Examples

Examples that matched only in broader `hbkd-qubn`:

- `Superior Court Adaptive Reuse` at `600 SOUTH COMMONWEALTH AVENUE LOS ANGELES CA 90005`
  broader hit: `Bldg-Alter/Repair`
- `Junction Gateway` at `4100 SUNSET BOULEVARD LOS ANGELES CA 90029`
  broader hit: `Bldg-Demolition`
- `The Foreman & Clark Building Apartments` at `404 WEST 7TH STREET LOS ANGELES CA 90014`
  broader hit: `Electrical`

Examples that matched in the current filter:

- `Hanover Hollywood` at `6200 WEST SUNSET BOULEVARD LOS ANGELES CA 90028`
- `Overland and Ayres` at `2455 OVERLAND AVENUE LOS ANGELES CA 90064`
- `Olympic + Westlake` at `1925 WEST OLYMPIC BOULEVARD LOS ANGELES CA 90006`

Examples that were not found in the first-pass LADBS family audit:

- `Common Fountain` at `1276 NORTH WESTERN AVENUE LOS ANGELES CA 90029`
- `Hollywood Arts Collective` at `1633 NORTH WILCOX AVENUE LOS ANGELES CA 90028`
- `Linea` at `2455 SOUTH SEPULVEDA BOULEVARD LOS ANGELES CA 90064`

These misses should be treated as audit targets, not as proof that the projects are absent from public records.

## Decisions Supported by This Audit

This audit supports the following decisions:

1. Do not widen the current `ladbs_permits` source blindly from `Bldg-New` to all permit types.
2. Treat `hbkd-qubn` as one slice of the LADBS bundle, not the whole lifecycle answer.
3. Build collector-level completeness features next:
   - `:updated_at` incremental cursor
   - overlap-window re-reads
   - source-row change metadata
   - reconciliation reporting
4. Continue LADBS bundle build-out with:
   - `cpkv-aajs`
   - `3f9m-afei`
5. Revisit matching quality and identifier enrichment, especially for projects with no APN or permit-number support.

## Recommended Next Step

The next implementation step should be:

- add Socrata row-state support and `:updated_at` incremental collection primitives

The next source-adapter step after that should be:

- build `cpkv-aajs`
- build `3f9m-afei`

Do not spend the next cycle merely tweaking the current `hbkd-qubn` filter in isolation.

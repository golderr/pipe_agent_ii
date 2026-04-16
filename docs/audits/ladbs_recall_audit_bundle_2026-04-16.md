# LADBS Recall Audit Rerun: Full Bundle

Date run: 2026-04-16

Related docs:

- [ladbs_recall_audit_plan.md](./ladbs_recall_audit_plan.md)
- [ladbs_recall_audit_results_2026-04-16.md](./ladbs_recall_audit_results_2026-04-16.md)
- [ladbs_recall_audit_hbkd_cpkv_2026-04-16.md](./ladbs_recall_audit_hbkd_cpkv_2026-04-16.md)

This rerun checks the current Los Angeles project snapshot against the full LADBS bundle now wired in the
codebase:

- narrow `hbkd-qubn` (`ladbs_permits`, `permit_type='Bldg-New'`)
- broader `hbkd-qubn` permit activity (`ladbs_permit_activity` semantics)
- `cpkv-aajs`
- `3f9m-afei`

## Scope and Objective

The goal of this pass is not to relitigate whether `Bldg-New` is too narrow. That is already settled.

This rerun answers the next question:

- after adding the broader `hbkd` activity slice, `cpkv-aajs`, and `3f9m-afei`, how much seeded LADBS-family
  recall is still missing?

It also clarifies what the remaining work should be:

- more source wiring
- better matching
- or targeted investigation of the residual miss bucket

## Method

This pass used the current `los_angeles` project snapshot on 2026-04-16:

- `Approved`: 74 projects
- `Under Construction`: 224 projects
- `Complete`: 200 projects

For each project, the audit checked in this order:

1. current `hbkd-qubn` inclusion rule (`permit_type='Bldg-New'`)
2. broader `hbkd-qubn` activity at the same normalized address
3. `cpkv-aajs` by normalized address, then APN when available
4. `3f9m-afei` by normalized address, then APN when available

Implementation note:

- exact SoQL address clauses on LADBS endpoints were brittle, especially on some numeric street-name cases
- this rerun instead fetched rows by `address_start` plus ZIP and then matched locally on normalized street
  components
- `hbkd-qubn` does not expose `street_direction` in the public row shape, so the broad `hbkd` portion of the
  audit ignores direction for address matching the same way the current source effectively does

Primary outcome buckets in this rerun are:

1. found under current `hbkd-qubn` filter
2. found only in broader `hbkd-qubn`
3. found only in `cpkv-aajs`
4. found only in `3f9m-afei`
5. not found in the current LADBS bundle

## Executive Summary

The broader LADBS bundle is directionally correct, but it does **not** eliminate the main recall gap.

Key results:

- the narrow `Bldg-New` slice is still weak for `Approved` and only partial for `Under Construction`
- broader `hbkd-qubn` activity remains the largest single recall lift outside the current narrow filter
- `cpkv-aajs` still helps, but mostly as enrichment and occasional APN rescue
- `3f9m-afei` adds important completion evidence semantics, but in this deterministic recall pass it did not
  rescue any projects that broader `hbkd` and `cpkv-aajs` both missed
- the remaining `Under Construction` miss bucket is still large: `75 / 224` projects (`33.5%`)

Practical conclusion:

- keep the full LADBS bundle
- keep `ladbs_permits` narrow
- keep broader `hbkd` as activity, not status proof
- do **not** assume the remaining `Under Construction` misses are solved by current LADBS sources
- make the next validation step a targeted investigation of the 75-project UC not-found bucket

## Results by Cohort

### `Approved` (74 projects)

- found under current `Bldg-New` filter: 3 (`4.1%`)
- found only in broader `hbkd-qubn`: 38 (`51.4%`)
- found only in `cpkv-aajs`: 0 (`0.0%`)
- found only in `3f9m-afei`: 0 (`0.0%`)
- not found in current LADBS bundle: 33 (`44.6%`)

Independent source coverage:

- broader `hbkd-qubn` coverage: 41 / 74 (`55.4%`)
- current `Bldg-New` coverage: 3 / 74 (`4.1%`)
- `cpkv-aajs` coverage: 2 / 74 (`2.7%`)
- `3f9m-afei` coverage: 1 / 74 (`1.4%`)

Interpretation:

- `Approved` remains mostly an early-lifecycle problem, not a LADBS-family completeness problem that `cpkv` or
  `cofo` can solve on their own

### `Under Construction` (224 projects)

- found under current `Bldg-New` filter: 61 (`27.2%`)
- found only in broader `hbkd-qubn`: 86 (`38.4%`)
- found only in `cpkv-aajs`: 2 (`0.9%`)
- found only in `3f9m-afei`: 0 (`0.0%`)
- not found in current LADBS bundle: 75 (`33.5%`)

Independent source coverage:

- broader `hbkd-qubn` coverage: 147 / 224 (`65.6%`)
- current `Bldg-New` coverage: 61 / 224 (`27.2%`)
- `cpkv-aajs` coverage: 67 / 224 (`29.9%`)
- `3f9m-afei` coverage: 7 / 224 (`3.1%`)

Interpretation:

- the biggest lift still comes from broader permit activity, not from new-building issuance rows
- `cpkv-aajs` matters, but it is not a major standalone rescuer
- `3f9m-afei` is not materially relevant to the unresolved `Under Construction` recall problem
- the remaining 75-project bucket is now the core LADBS completeness question

Examples still not found in the current LADBS bundle:

- `Burlington Place`
- `3303 W Sunset Blvd`
- `1165-1167 WEST 37TH PLACE LOS ANGELES CA 90007`
- `555 Harvard`
- `The Standard at Los Angeles`

### `Complete` (200 projects)

- found under current `Bldg-New` filter: 134 (`67.0%`)
- found only in broader `hbkd-qubn`: 46 (`23.0%`)
- found only in `cpkv-aajs`: 1 (`0.5%`)
- found only in `3f9m-afei`: 0 (`0.0%`)
- not found in current LADBS bundle: 19 (`9.5%`)

Independent source coverage:

- broader `hbkd-qubn` coverage: 180 / 200 (`90.0%`)
- current `Bldg-New` coverage: 134 / 200 (`67.0%`)
- `cpkv-aajs` coverage: 141 / 200 (`70.5%`)
- `3f9m-afei` coverage: 48 / 200 (`24.0%`)

Interpretation:

- LADBS-family recall is strongest here
- `3f9m-afei` still matters as direct `Complete` evidence, but under current deterministic matching it behaves
  more like a semantic confirmation source than a major recall-rescue source

Example `cpkv-aajs`-only recovery:

- `701 Hudson`

## What This Changes

This rerun narrows the next step materially.

It does **not** suggest:

- widening `ladbs_permits`
- adding more LADBS sibling sources blindly
- assuming CofO will fix the remaining `Under Construction` misses

It **does** suggest:

1. preserve the current LADBS bundle design
2. keep the broader `hbkd` activity source
3. treat the remaining UC miss bucket as a focused investigation target
4. test whether the remaining misses are mostly:
   - matching gaps
   - stale seeded statuses
   - genuinely absent from current LADBS public data
   - recoverable only through a different source family

## Note on `2w4b-a48u`

A quick exploratory check on 2026-04-16 suggests the public `2w4b-a48u` endpoint is **not** an immediate
drop-in address-based construction-activity source:

- the simple public row shape surfaced only `permit` and `permit_status`
- direct address-field lookups failed because address columns were not exposed in that view

That does not prove the inspections family is useless. It does mean the next inspection-source step should begin
with a source-profile pass, not with immediate adapter implementation.

## Recommended Next Step

Run a targeted investigation on the remaining `75` `Under Construction` projects not found in the current LADBS
bundle, and treat `2w4b-a48u` as a profiling task rather than an implementation task until its usable field
shape is confirmed.

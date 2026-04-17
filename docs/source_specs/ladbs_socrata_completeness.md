# LADBS Socrata Completeness Spec

Last updated: 2026-04-17

This document defines how the Los Angeles Department of Building and Safety (LADBS) Socrata datasets should be treated for completeness, incremental collection, reconciliation, and lifecycle inference.

The goal is not to make one endpoint "comprehensive." The goal is to use the LADBS dataset family as a coordinated source bundle so the pipeline does not miss:

- relevant rows
- meaningful field changes
- status-relevant lifecycle evidence
- source-side corrections that arrive after the first pull

## Why This Spec Exists

LADBS is not one source in practice. It is a family of related Socrata datasets, each authoritative for different parts of the permit and construction lifecycle.

Comprehensiveness has four separate dimensions:

1. Row coverage: did we pull every relevant source row?
2. Field coverage: did we capture the fields that actually matter?
3. Change coverage: did we notice when a source row changed?
4. Lifecycle coverage: are we using the right combination of datasets to infer status conservatively?

Each dimension needs its own strategy. A "more recent query" only helps preview usefulness; it does not solve production completeness.

## Operating Principles

### Preview vs production

Preview and production have different goals:

- Preview should be representative and recent so a human can sanity-check live behavior.
- Production should be loss-resistant and auditable.

That means preview queries should prefer the newest rows, while production queries should be deterministic and cursor-driven.

### Full backfill plus incremental sync

Every Socrata source should support:

- initial full historical backfill
- recurring incremental sync using Socrata system timestamps
- overlap-window re-reads to catch late edits and edge cases
- periodic full reconciliation to catch source-side corrections and re-run improved logic over historical rows

### System timestamps, not business dates

Business dates such as `issue_date` are not safe incremental cursors. They can miss corrections, backfills, and updates to older rows.

Use Socrata system fields for change detection:

- `:id`
- `:created_at`
- `:updated_at`

`:updated_at` is the primary incremental cursor for LADBS Socrata datasets.

### Conservative delete handling

Row disappearance from a source must not immediately remove identifiers or downgrade canonical projects.

Until a more mature source-state model exists:

- detect absence during full reconciliation
- record it as a source-state event
- require repeated absence or stronger confirmation before changing canonical project state

## LADBS Source Bundle

### 1. `pi9x-tg5x` - Building Permits Issued 2020-Present

- Dataset: `https://data.lacity.org/resource/pi9x-tg5x.json`
- Current role: live permit discovery plus permit-activity enrichment
- Active config roles:
  - `ladbs_permits`: `permit_type='Bldg-New'`, `update+discovery`
  - `ladbs_permit_activity`: `permit_type != 'Bldg-New'`, `update`, `create_new_candidates: false`
- Primary business key: `permit_nbr`
- Incremental cursor: `:updated_at`

Authoritative or preferred for:

- current permit issuance evidence
- permit type and subtype
- work description via `work_desc`
- explicit publisher status via `status_desc`
- APN via `apn`
- units and stories when present
- geometry via `lat`, `lon`, or `geolocation`
- housing-oriented enrichment via `use_desc`

Important operating note:

- The pipeline intentionally runs two logical sources against the same dataset. This duplicates HTTP
  work during collection, but it keeps the semantics clean: `ladbs_permits` remains a narrow
  `Bldg-New` discovery slice, while `ladbs_permit_activity` handles broader non-`Bldg-New` update
  evidence without flooding the review queue with unmatched rows.

Status use:

- building permit issuance is strong `Approved` evidence
- permit issuance is not proof of `Under Construction`
- `use_desc` currently uses the minimum safe housing allowlist from the rewire plan:
  `Apartment`, `Duplex`, `Dwelling - Single Family`; widening that set remains a deliberate follow-up

### 2. `9w5z-rg2h` - Building and Safety Inspections

- Dataset: `https://data.lacity.org/resource/9w5z-rg2h.json`
- Current role: update-only construction-activity evidence
- Active config role: `ladbs_inspections`, `update`, `create_new_candidates: false`
- Primary business keys: Socrata `:id` for row identity plus normalized `permit` for matching
- Incremental cursor: `:updated_at`

Preferred for:

- inspection detail via `inspection`, `inspection_date`, and `inspection_result`
- permit workflow state via `permit_status`
- address-level matching context via `address`
- per-row coordinates via `lat_lon`

Status use:

- normalize the source permit format from spaces to dashes on read
- persist all inspection rows as source context
- emit direct `Under Construction` evidence only when all of the following are true:
  - `inspection_date` is present and recent
  - `inspection_result` is a substantive positive outcome
  - `permit_status` is still an active in-progress state, not a terminal/completed one

This keeps inspection evidence calibrated with the project status definitions. Old, cancelled,
scheduled, correction-only, or terminal-permit inspections are useful context but not safe direct
status signals.

### 3. `3f9m-afei` - Certificate of Occupancy

- Dataset: `https://data.lacity.org/resource/3f9m-afei.json`
- Current role: completion evidence
- Primary business keys: `cofo_number` and `pcis_permit`
- Incremental cursor: `:updated_at`

Likely authoritative for:

- CofO issuance
- completion-related status fields
- `cofo_issue_date`
- `latest_status`
- `status_date`

Known limitations:

- not a broad discovery feed
- geared toward later lifecycle stages rather than early discovery

Status use:

- primary LADBS evidence for `Complete`

### 4. `hbkd-qubn` - Building Permits (deprecated frozen feed)

- Dataset: `https://data.lacity.org/resource/hbkd-qubn.json`
- Publisher state: frozen since `2023-05-22T09:33:30.736Z`
- Current role: deprecated historical replay only
- Primary business key: `pcis_permit`
- Incremental cursor: `:updated_at`

Known limitations:

- no row updates after the freeze timestamp
- zero observed 2024, 2025, or 2026 row growth in the coverage audit
- materially worse field shape than `pi9x-tg5x`

Status use:

- none for live collection cutover
- legacy adapters are retained only so historical snapshots can still be replayed deliberately

### 5. `cpkv-aajs` - Building Permits: New Housing Units (deprecated frozen feed)

- Dataset: `https://data.lacity.org/resource/cpkv-aajs.json`
- Publisher state: frozen since `2023-05-22T09:33:30.736Z`
- Current role: deprecated historical replay only
- Primary business key: `pcis_permit`
- Incremental cursor: `:updated_at`

Formerly useful for:

- residential unit count
- stories
- reconstructed APN fragments
- location geometry via `location_1`

Current treatment:

- its useful enrichment has been folded into the live `pi9x-tg5x` adapter path
- it should no longer be used as an active housing-enrichment source

### 6. `ydma-y4hd` - New Building Permits (removed from catalog)

- Publisher state: removed from the live LADBS Socrata catalog during the 2026-04-17 coverage audit
- Current role: none
- Status use: none

### 7. `2w4b-a48u` - Inspections (superseded sparse feed)

- Dataset: `https://data.lacity.org/resource/2w4b-a48u.json`
- Current role: none
- Reason retired: the public row shape is too sparse for defensible matching or lifecycle inference,
  and `9w5z-rg2h` supersedes it with address, inspection, date, result, and coordinate fields
- Status use: none

## Field Authority Rules

Where multiple LADBS datasets expose the same concept, authority must be explicit.

Initial direction:

- permit issuance detail: prefer `pi9x-tg5x`
- residential unit detail: prefer `pi9x-tg5x`
- APN or assessor fields: prefer direct `pi9x-tg5x.apn`; keep legacy `cpkv-aajs` values only for historical replay
- completion evidence: prefer `3f9m-afei`
- construction activity: prefer filtered `9w5z-rg2h` inspections; do not use `2w4b-a48u`

When a non-authoritative dataset disagrees with the authoritative one:

- keep the authoritative value for canonical projection
- persist the non-authoritative value in source payload
- log or surface the discrepancy once discrepancy reporting exists

Do not silently let "last source wins" determine canonical values.

## Query Strategy

### Preview mode

Use recent, human-checkable results:

- order: `:updated_at DESC, :id DESC`
- optional recent window when needed for debugging
- small limit acceptable

### Production backfill mode

Use deterministic, replayable order:

- order: `:updated_at ASC, :id ASC`
- no time filter
- pull full historical population for the configured inclusion rule

### Production incremental mode

Use cursor-driven collection:

- filter on `:updated_at`
- re-read an overlap window on every run
- order: `:updated_at ASC, :id ASC`
- dedupe by source row identity and hash during persistence

The overlap window exists to catch:

- clock edge cases
- late source edits
- out-of-order ingestion retries
- source-side corrections to rows near the previous cursor boundary

### Full reconciliation mode

Run periodically to:

- detect rows that disappeared from the source
- catch source-side corrections outside the overlap window
- re-apply improved filters and adapter logic to historical rows
- validate row counts and field completeness against the live source

Full reconciliation is required operationally, not a nice-to-have.

## Source-Row Change Tracking

Project-level diffs are not enough. A source row can change without changing the canonical project projection.

At minimum, each source record should eventually retain:

- Socrata system row id
- source `:updated_at`
- source `:created_at` when available
- row hash of the raw source payload
- first seen timestamp in our system
- last seen timestamp in our system
- last pulled timestamp in our system

Longer term, the system may need full source-row history rather than just current row state plus hashes.

## Required Audits

The current live-state references for this source family are:

- [ladbs_socrata_coverage_2026-04-17.md](../audits/ladbs_socrata_coverage_2026-04-17.md)
- [ladbs_socrata_rewire_verification_2026-04-17.md](../audits/ladbs_socrata_rewire_verification_2026-04-17.md)

Any future LADBS source changes should re-answer the same questions those audits covered:

- is the configured dataset still live and updating?
- does the active row shape still support the adapter assumptions?
- does the rewired source bundle still recover the audit calibration cohorts at roughly the same rate?
- are status-evidence rules still conservative relative to the current source taxonomy?

## Known Open Items

- Decide whether source-row history requires a dedicated version table or lighter row-state metadata first
- Define explicit discrepancy-reporting behavior when sibling datasets disagree
- Define operational policy for repeated source disappearance before canonical data is changed
- Revisit whether the `use_desc` housing allowlist should expand beyond the current minimum safe set
- Re-profile `9w5z-rg2h` inspection outcomes if the publisher changes the result/status taxonomy

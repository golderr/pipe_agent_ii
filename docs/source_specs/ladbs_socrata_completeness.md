# LADBS Socrata Completeness Spec

Last updated: 2026-04-16

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

### 1. `hbkd-qubn` - Building Permits

- Dataset: `https://data.lacity.org/resource/hbkd-qubn.json`
- Current role: broad permit discovery and permit-detail tracking
- Current config role: `update+discovery`
- Primary business key: `pcis_permit`
- Incremental cursor: `:updated_at`
- Current inclusion rule under evaluation: `permit_type='Bldg-New'`

Likely authoritative for:

- permit existence
- permit issuance evidence
- permit type and subtype
- work description
- valuation
- applicant and contractor names

Known limitations:

- the visible row shape is relatively sparse
- no obvious APN in the current row shape
- no visible geocode in the sampled row shape
- the current `Bldg-New` filter has not yet been recall-audited against seeded LA projects

Status use:

- building permit issuance is strong `Approved` evidence
- permit issuance is not proof of `Under Construction`

### 2. `ydma-y4hd` - New Building Permits

- Dataset: `https://data.lacity.org/resource/ydma-y4hd.json`
- Current role: comparison and recall-validation feed
- Primary business key: expected to include `pcis_permit` or equivalent permit identifier
- Incremental cursor: `:updated_at`

Planned use:

- validate whether `hbkd-qubn` plus the current filter is missing relevant rows
- compare coverage, field shape, and refresh behavior against `hbkd-qubn`

Known limitations:

- currently unverified in the live collection workflow
- may be redundant or differently prefiltered in ways we do not fully control

Status use:

- none until validated

### 3. `cpkv-aajs` - Building Permits: New Housing Units

- Dataset: `https://data.lacity.org/resource/cpkv-aajs.json`
- Current role: housing-specific enrichment
- Primary business key: `pcis_permit`
- Incremental cursor: `:updated_at`

Sampled fields indicate likely authority for:

- residential unit count
- stories
- assessor book/page/parcel
- location geometry or coordinates via `location_1`
- permit details for housing-specific rows

Known limitations:

- overlap with `hbkd-qubn` means field-authority rules must be explicit
- field-level disagreements between datasets should be tracked, not silently ignored

Status use:

- permit issuance can support `Approved`
- housing-specific permit data may improve candidate prioritization and matching

### 4. `3f9m-afei` - Certificate of Occupancy

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

### 5. `2w4b-a48u` - Inspections

- Dataset: `https://data.lacity.org/resource/2w4b-a48u.json`
- Current role: experimental construction-activity feed
- Visible sample fields: `permit`, `permit_status`
- Incremental cursor: `:updated_at`

Planned use:

- evaluate whether inspections provide defensible evidence for `Under Construction`

Known limitations:

- currently too sparse to treat as authoritative for lifecycle transitions
- needs validation before it is allowed to drive status suggestions

Status use:

- none until field shape and evidentiary value are validated against the status definitions

## Field Authority Rules

Where multiple LADBS datasets expose the same concept, authority must be explicit.

Initial direction:

- permit issuance detail: prefer `hbkd-qubn`
- residential unit detail: prefer `cpkv-aajs`
- APN or assessor fields: prefer `cpkv-aajs` when present, otherwise retain non-authoritative values as supporting source detail
- completion evidence: prefer `3f9m-afei`
- construction activity: do not use `2w4b-a48u` for canonical status until validated

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

Before building more LADBS adapters, run a recall audit of the current `hbkd-qubn` inclusion rule against seeded Los Angeles projects.

See [ladbs_recall_audit_plan.md](../audits/ladbs_recall_audit_plan.md) for the execution checklist.

The recall audit should answer:

- are seeded LA projects showing up under the current `Bldg-New` filter?
- if not, do they appear elsewhere in `hbkd-qubn`?
- if not, do they appear in sibling LADBS datasets?
- what miss categories explain the gap?

## Known Open Items

- Validate the actual field shape and usefulness of `ydma-y4hd`
- Validate whether `2w4b-a48u` supports defensible `Under Construction` evidence
- Decide whether source-row history requires a dedicated version table or lighter row-state metadata first
- Define explicit discrepancy-reporting behavior when sibling datasets disagree
- Define operational policy for repeated source disappearance before canonical data is changed

# LADBS Recall Audit Plan

Last updated: 2026-04-16

This plan audits whether the current LADBS permit collection strategy is actually finding the projects it should find.

The immediate target is the current `hbkd-qubn` source configuration using:

- source: `ladbs_permits`
- dataset: `hbkd-qubn`
- current inclusion rule: `permit_type='Bldg-New'`

The audit should be completed before building more LADBS adapters so we do not harden the wrong inclusion logic.

## Audit Objective

Measure whether the current LADBS permit filter and matching approach recover the seeded Los Angeles projects that should plausibly appear in LADBS data.

The audit is about recall, not precision.

## Audit Cohorts

Split the seeded Los Angeles projects into separate cohorts so the results are interpretable.

### Cohort A: `Under Construction`

Expectation:

- should have strong LADBS-family coverage
- useful for testing whether active construction projects are visible in LADBS permits and sibling datasets

### Cohort B: `Complete`

Expectation:

- should have strong LADBS-family coverage, especially through CofO
- useful for validating later-stage lifecycle coverage

### Cohort C: `Approved`

Expectation:

- mixed results are normal
- not every approved project will yet have LADBS permit evidence
- useful for learning where the current filter is too narrow, but not a strict pass/fail group

Optional later cohort:

- `Pending` projects with clear permit-related notes or known by-right paths

## Candidate Sample Size

Preferred:

- all Los Angeles seeded projects in Cohorts A and B

If the population is too large for the first pass:

- minimum 50 projects per cohort
- stratify by unit count and geography if sampling

## What To Query

For each audit project, check in this order:

1. current `hbkd-qubn` inclusion rule
2. broader `hbkd-qubn` search without the current inclusion rule
3. sibling LADBS datasets:
   - `cpkv-aajs`
   - `3f9m-afei`
   - `ydma-y4hd` if available
   - `2w4b-a48u` only for exploratory evidence

Search keys to try:

- canonical address
- address variants from `raw_addresses`
- APN if available
- permit number if already known

## Match Classification

Each audited project should land in exactly one primary outcome bucket:

1. Found under current `hbkd-qubn` filter
2. Found in `hbkd-qubn`, but only outside the current filter
3. Found only in a sibling LADBS dataset
4. Not found in LADBS family, but plausibly should be found
5. Not found in LADBS family, with a credible explanation

Also capture a secondary diagnostic reason when possible.

Examples:

- address normalization issue
- filter too narrow
- project not yet at permit stage
- project outside City of Los Angeles jurisdiction
- source data appears incomplete
- seeded status likely stale or inaccurate

## Questions The Audit Must Answer

- What share of `Under Construction` seeded LA projects are found under the current `Bldg-New` filter?
- What share are recoverable only by broadening `hbkd-qubn`?
- What share are recoverable only from sibling LADBS datasets?
- Which miss categories are most common?
- Is `permit_type='Bldg-New'` a valid primary inclusion rule, a partial rule, or the wrong rule?

## Deliverables

The audit output should include:

- total projects checked by cohort
- count and percentage in each outcome bucket
- top miss reasons
- 10-20 example projects illustrating the main failure modes
- a concrete recommendation:
  - keep current filter
  - broaden current filter
  - replace with a bundle strategy
  - separate discovery and update logic by dataset

## Recommended Execution Sequence

1. Query seeded Los Angeles projects in Cohorts A and B.
2. Build an audit worksheet with:
   - project id
   - project name
   - canonical address
   - current canonical status
   - unit count
   - APN if present
3. Test each project against current `hbkd-qubn` logic.
4. Re-test misses against broader `hbkd-qubn`.
5. Re-test remaining misses against sibling LADBS datasets.
6. Classify outcomes and reasons.
7. Summarize findings and update the architecture decision log.

## Success Criteria

The audit is successful if it produces a defensible answer to both of these:

- what the current LADBS permit filter misses
- what ingestion strategy should replace or supplement it

The audit is not successful if it only produces anecdotal examples without quantified outcome buckets.

## Follow-On Actions

Depending on results, the likely next implementation steps are:

1. update LADBS source inclusion rules
2. implement `:updated_at` incremental collection with overlap windows
3. add source-row change metadata
4. formalize field-authority rules across LADBS datasets
5. continue adapter work with `cpkv-aajs` and `3f9m-afei`

# LADBS Socrata Rewire Plan

Date: 2026-04-17

This plan rewires the active Los Angeles LADBS Socrata sources away from the frozen legacy datasets
`hbkd-qubn` and `cpkv-aajs` and onto the live replacements `pi9x-tg5x` and `9w5z-rg2h`, while
keeping `3f9m-afei` unchanged. The plan is driven by the deterministic freeze evidence in the
coverage audit: `hbkd-qubn` and `cpkv-aajs` stopped updating at
`2023-05-22T09:33:30.736Z`; all 13 sampled missing Pipedream-cited `Bldg-New` permits are present
in `pi9x-tg5x`; the new datasets have a materially different row shape; and the recommended next
step is to rewire before more seed cleanup or matcher tuning. Citations:
[`ladbs_socrata_coverage_2026-04-17.md`, sections 3.4, 3.5, 3.6, 5](../audits/ladbs_socrata_coverage_2026-04-17.md).

## Scope

1. Rewire `ladbs_permits` from `hbkd-qubn` to `pi9x-tg5x` with the existing narrow
   `permit_type='Bldg-New'` slice.
2. Rewire `ladbs_permit_activity` from `hbkd-qubn` to `pi9x-tg5x` with
   `permit_type!='Bldg-New'` and `create_new_candidates: false`.
3. Retire standalone `ladbs_new_housing`; fold its useful enrichment (`apn`, units, stories,
   geometry where available) into the new `pi9x-tg5x` permit adapters and use `use_desc` for the
   former housing-specific slice.
4. Add `ladbs_inspections` against `9w5z-rg2h` as an update-only source with permit normalization
   from space-separated to dash-separated form.
5. Harvest LADBS PCIS permit numbers from Pipedream `source_urls` into `project_identifiers`.

## File-Level Change List

| File | Planned change |
|---|---|
| `docs/specs/ladbs_socrata_rewire_plan.md` | Phase A planning artifact. |
| `src/tcg_pipeline/source_adapters/ladbs.py` | Add new `pi9x-tg5x` permit/activity adapter factories and a new `9w5z-rg2h` inspections adapter factory. Leave current `hbkd-qubn` / `cpkv-aajs` factories in place with deprecation comments. |
| `src/tcg_pipeline/source_adapters/__init__.py` | Register the new adapter factory names and point active config adapters at them. |
| `config/markets/los_angeles.yaml` | Repoint `ladbs_permits` and `ladbs_permit_activity` to `pi9x-tg5x`; remove active `ladbs_new_housing`; add `ladbs_inspections`; keep `ladbs_cofo` untouched. |
| `src/tcg_pipeline/status_rules.py` | Add an inspection-based evidence rule so only recent, substantive inspections on active permits can suggest `Under Construction` rather than any dated inspection row. |
| `src/tcg_pipeline/ingesters/pipedream.py` | Extract PCIS permit identifiers from Pipedream `source_urls` and emit them as `permit_number` identifiers during seed import. |
| `tests/test_ladbs_adapter.py` | Add `pi9x-tg5x` permit/activity mapping coverage and `9w5z-rg2h` inspection mapping coverage. |
| `tests/test_market_config.py` | Update expectations for the rewired endpoints, retired `ladbs_new_housing`, and new `ladbs_inspections` source. |
| `tests/test_pipedream_ingester.py` | Add unit coverage for PCIS permit extraction from `source_urls`. |
| `tests/test_status_rules.py` | Cover the new inspection evidence rule. |
| `docs/audits/ladbs_socrata_rewire_verification_2026-04-17.md` | Phase E verification of the audit recovery claim against the rewired sources. |
| `docs/source_specs/ladbs_socrata_completeness.md` | Deprecate the frozen datasets and document the new live sources plus cursor policy. |
| `ARCHITECTURE.md` | Add Phase 3 row `3.0f` and, if Phase E lands in range, mark `3.0c` done. |

Notes:

- No schema or Alembic migration is expected for this rewire. `permit_number` already exists as an
  identifier type, `SourceRun` already stores `source_max_updated_at`, and the Socrata collector
  already persists `:id`, `:created_at`, and `:updated_at`.
- No changes are planned in `src/tcg_pipeline/collectors/socrata.py` or `src/tcg_pipeline/db/collect.py`.
  Their current `:updated_at` ordering, metadata capture, and cursor persistence are sufficient for
  the rewire.

## Row-Shape Mapping

### A. `hbkd-qubn` -> `pi9x-tg5x` for `ladbs_permits`

| Concept | Old (`hbkd-qubn`) | New (`pi9x-tg5x`) | Planned handling |
|---|---|---|---|
| Permit number | `pcis_permit` | `permit_nbr` | Read `permit_nbr`, normalize with `clean_identifier_text`, persist as `permit_number`. |
| Address | `address_start`, `address_end`, `street_direction`, `street_name`, `street_suffix` | `primary_address` | Pass the full single-line address into `normalize_address()`. This intentionally tolerates unit ranges such as `329 S BONNIE BRAE ST 1-30`. |
| Work description | `work_description` | `work_desc` | Map to canonical `description`. |
| Explicit status | not exposed | `status_desc` | Persist in `mapped_fields` for review context and future profiling. |
| APN | not exposed | `apn` | Persist directly as canonical 10-digit APN; no `_build_assessor_apn()` reconstruction. |
| Geometry | none on `hbkd-qubn` | `geolocation`, `lat`, `lon` | Capture coordinates when present so the rewired permit source absorbs the useful enrichment `cpkv-aajs` used to provide. |
| Housing descriptor | not exposed | `use_desc` | Persist for matching/debugging and use it to replace the old housing-only slice. |
| Issue / permit typing | `issue_date`, `permit_type`, `permit_sub_type`, `valuation`, likely unit/story fields | same concept, with audited renames limited to the columns listed above | Reuse existing common field mapping where the column names still match; adjust only the renamed fields. |

### B. `hbkd-qubn` -> `pi9x-tg5x` for `ladbs_permit_activity`

| Concept | Old (`hbkd-qubn`) | New (`pi9x-tg5x`) | Planned handling |
|---|---|---|---|
| Dataset / filter | `hbkd-qubn` with `permit_type!='Bldg-New'` | `pi9x-tg5x` with `permit_type!='Bldg-New'` | Same source role, same `create_new_candidates: false`, different row mapping. |
| Permit number | `pcis_permit` | `permit_nbr` | Same dash-normalized identifier behavior as `ladbs_permits`. |
| Address | split street fields | `primary_address` | Same single-line normalization as `ladbs_permits`. |
| Work description | `work_description` | `work_desc` | Map to canonical `description`. |
| Explicit status | not exposed | `status_desc` | Persist as non-authoritative permit context. |
| Use descriptor | not exposed | `use_desc` | Persist so adaptive-reuse / hotel / non-`Bldg-New` cases remain explainable in review. |

### C. `cpkv-aajs` retirement -> `pi9x-tg5x` enrichment fold-in

| Concept | Old (`cpkv-aajs`) | New (`pi9x-tg5x`) | Planned handling |
|---|---|---|---|
| Permit number | `pcis_permit` | `permit_nbr` | Fold into the rewired permit/activity adapters. |
| APN | `assessor_book` + `assessor_page` + `assessor_parcel` | `apn` | Stop reconstructing APNs from three fields; use the live 10-digit value directly. |
| Geometry | `location_1` | `geolocation`, `lat`, `lon` | Capture geometry on the primary permit feed so the retired housing source does not remain the only place with coordinates. |
| Residential segmentation | standalone dataset implies housing | `use_desc` | Replace standalone `ladbs_new_housing` with an adapter-level `use_desc` allowlist/profile. |
| Units / stories | useful enrichment in `cpkv-aajs` | expected on `pi9x-tg5x` permit rows where present | Keep the existing `total_units` / `stories` enrichment in the new permit adapters so the `cpkv-aajs` retirement is not a data regression. |

Implementation note: the exact `use_desc` allowlist is the only intentional hold point before
Phase B. The minimum safe seed list from the audit/user brief is `Apartment`, `Duplex`, and
`Dwelling - Single Family`; any broader `Dwelling%` / townhouse / condo expansion should be
confirmed before code lands.

### D. `2w4b-a48u` -> `9w5z-rg2h` for inspections

| Concept | Old (`2w4b-a48u`) | New (`9w5z-rg2h`) | Planned handling |
|---|---|---|---|
| Permit identifier | `permit` | `permit` | Normalize `'18010 10000 03620'` -> `'18010-10000-03620'` on read and persist the dash form as `permit_number`. |
| Inspection detail | not meaningfully exposed | `inspection`, `inspection_date`, `inspection_result` | Persist inspection detail and only emit direct UC evidence after adapter-level filtering on recency, substantive result, and active permit status. |
| Permit status | `permit_status` | `permit_status` | Persist as supporting context. |
| Address | not exposed | `address` | Normalize the single-line address via `normalize_address()`. |
| Coordinates | not exposed | `lat_lon` | Capture lat/lng when present. |
| Source record identity | unclear / too sparse | multi-row inspection feed | Use a row-unique source record key from Socrata row identity rather than permit number alone, because one permit can have many inspections. |
| Source role | not adapter-ready | `update` only | Set `create_new_candidates: false`; inspections should update tracked projects or surface possible matches, not create discovery candidates. |

## Incremental Cursor Strategy

Use Socrata `:updated_at` as the only production incremental cursor for `pi9x-tg5x` and
`9w5z-rg2h`.

Justification:

1. The repo already treats Socrata system timestamps as the source of truth for incremental reads:
   `collectors/socrata.py` filters on `:updated_at`, `db/collect.py` persists `source_max_updated_at`,
   and `cli.py` resolves the next lower bound from prior `SourceRun` rows.
2. The LADBS completeness spec explicitly says LADBS incremental collection should use system
   fields, not business dates or source-specific refresh metadata.
3. The coverage audit notes that `pi9x-tg5x` exposes both `refresh_time` and `:updated_at`, but the
   deterministic freeze/live comparison in section 3.4 is stated in terms of `:updated_at`, and the
   row shape differences in section 3.6 do not establish `refresh_time` as a safe monotonic change
   cursor.
4. Keeping `:updated_at` avoids collector and persistence churn while still capturing row edits,
   backfills, and late corrections, which is exactly why the current completeness spec prefers
   system timestamps.

Planned treatment of `refresh_time`:

- Do not use it for cursoring.
- Leave it in raw payload and optionally surface it in `mapped_fields` later if operators want to
  debug LADBS publisher refresh behavior.

## Rollout Strategy For Cursor Re-Anchor

Problem:

- The current persisted `SourceRun` cursor for `ladbs_permits` / `ladbs_permit_activity` is from the
  frozen `hbkd-qubn` source family, so the default incremental resolver would reuse
  `2023-05-22T09:33:30.736Z` and unintentionally trigger a multi-year catch-up on the new dataset.

Plan:

1. Keep the source names stable (`ladbs_permits`, `ladbs_permit_activity`) so downstream matching,
   review items, and CLI usage do not fork.
2. Do not rely on `_resolve_incremental_cursor()` for the first live non-dry-run after the rewire.
3. Re-anchor with an explicit operator-supplied `--updated-since` on the first live run, using the
   new dataset's observed `MAX(:updated_at)` minus the configured overlap window:
   - `pi9x-tg5x`: audit observed `MAX(:updated_at) = 2026-04-13T15:34:41.210Z`
   - `9w5z-rg2h`: audit observed `MAX(:updated_at) = 2026-04-13T12:59:14.624Z`
4. That first live run persists a new `SourceRun.source_max_updated_at` from the live dataset and
   becomes the new cursor anchor for subsequent default incremental runs.
5. Historical catch-up for 2023-2025 permits is intentionally separated from the first re-anchor.
   The first run is for safe cutover; any broader backfill can be scheduled intentionally later
   rather than happening accidentally because the legacy cursor points into a dead dataset.

Operational consequence:

- Phase D remains read-only and uses `preview-source` plus `collect-source --dry-run`, so it must
  not advance or reset cursor state.
- The first cursor-advancing run should be a deliberate post-review operation, not part of Phase D.

## Rollout Order

1. Land the new adapter factories and config rewiring while keeping the legacy factories in place as
   deprecated code paths.
2. Add the new inspections source and status evidence rule.
3. Add Pipedream permit harvesting so LA seeds finally contribute `permit_number` identifiers to the
   matcher.
4. Run unit tests, `ruff check`, and `ruff format`.
5. Run read-only live previews and `collect-source --dry-run` against each rewired source.
6. Run the targeted verification against the 15 Pipedream permits and 10 CoStar APNs from the audit.
7. Only after review, perform the first live cursor re-anchor with an explicit `--updated-since`.

## Explicit Non-Goals In This Slice

- No change to `ladbs_cofo` / `3f9m-afei`.
- No default automatic historical backfill on the first live run.
- No new market-config schema keys for cursoring; the existing CLI override is enough.
- No Alembic migration unless implementation proves the inspections source needs a schema change,
  which is not expected from current code structure.

## Review Hold Point

Before Phase B, confirm the intended `use_desc` allowlist for the retired housing slice. The plan
assumes the minimal safe starting set is:

- `Apartment`
- `Duplex`
- `Dwelling - Single Family`

If broader residential `use_desc` values should be in scope, that choice should be made explicitly
before the `ladbs_new_housing` retirement logic is coded.

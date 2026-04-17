# LADBS Socrata Coverage / Completeness Investigation

Date run: 2026-04-17

Related docs:

- [ladbs_uc_triage_sample_2026-04-16.md](./ladbs_uc_triage_sample_2026-04-16.md) — the triage that motivated this investigation.
- [ladbs_recall_audit_bundle_2026-04-16.md](./ladbs_recall_audit_bundle_2026-04-16.md)
- [ladbs_socrata_completeness.md](../source_specs/ladbs_socrata_completeness.md) — the spec this document significantly updates.

## 1. Executive Summary

**The LADBS Socrata datasets we pull from — `hbkd-qubn` (`ladbs_permits`, `ladbs_permit_activity`) and `cpkv-aajs` (`ladbs_new_housing`) — were frozen by the publisher on 2026-05-22 and have not received a single row update since.** This is not a cursor lag, an incremental-sync bug, a status filter, or a scope question. The Socrata-side `:updated_at` clock on both datasets literally stops at `2023-05-22T09:33:30.736Z`. LADBS migrated the underlying data to a new set of dataset IDs — `pi9x-tg5x` for permits, `9w5z-rg2h` for inspections, and existing `3f9m-afei` for CofO — and the old IDs are effectively tombstoned without a visible deprecation banner in the Socrata catalog.

Practical implications:

1. Every LA UC project in our `Under Construction` cohort whose `Bldg-New` permit was issued after 2023-05-19 is **structurally invisible** to the current LADBS bundle. No amount of matcher tuning, seed cleanup, or APN-first matching will recover them, because the rows are not in the public endpoints we query.
2. `3f9m-afei` (CofO) is genuinely live — last `:updated_at` is 2026-04-13 — so `ladbs_cofo` is the only LADBS source in our current bundle that still works as intended.
3. The direct replacement for the permit feed is `pi9x-tg5x` ("Building and Safety - Building Permits Issued from 2020 to Present (N)"), updated through 2026-04-13 with 385,028 rows. All 13 missing Pipedream-cited permits land in it.
4. The inspections dataset `9w5z-rg2h` ("Building and Safety Inspections"), which replaces the shallow `2w4b-a48u` that the source spec flagged as sparse, is live, 11.3M rows, and carries per-inspection address + permit-status data. This is a viable first-class source for `Under Construction` evidence.
5. The top-level recommendation changes from the prior triage doc's "seed cleanup pass" to **"wire the `pi9x-tg5x` and `9w5z-rg2h` adapters before any further work on the 71 not-found bucket."** Seed cleanup without the new source wiring would still leave the majority of the bucket invisible.

## 2. Scope and Method

This investigation tested four hypotheses against a concrete population: the 18 Pipedream-seeded projects in the 71-project UC not-found bucket from [ladbs_uc_triage_sample_2026-04-16.md](./ladbs_uc_triage_sample_2026-04-16.md). Those 18 are a naturally cleaner test population than the 53 CoStar-only projects because the Pipedream researcher notes cite specific LADBS permit numbers via deep links to `https://www.ladbsservices2.lacity.org/OnlineServices/PermitReport/PcisPermitDetail?id1=...`, so we can verify on the LADBS portal itself that each permit is real and active, then check whether it's in our Socrata feeds.

Hypotheses:

1. **Query / encoding issue** — our SoQL form is subtly broken. Rule-out test: look up known-good permits from the existing cache by `pcis_permit` and confirm they return.
2. **Status-based filtering** — Socrata excludes permits currently in Void / Expired status. Rule-out test: fetch the portal page for each missing permit and check current status plus Void/Reactivate history.
3. **Collection lag / data freshness** — the incremental cursor is behind. Rule-out test: query `MAX(:updated_at)` on each dataset.
4. **Dataset scope difference** — Socrata publishes only a subset (e.g., only some issuing offices, only some permit sub-types). Rule-out test: look up each missing permit's portal fields (issuing office, permit sub-type, use description) and check for a scope pattern.

The investigation also spot-checked Socrata catalog metadata to identify whether a replacement dataset exists for any deprecated feeds.

Phased execution:

- **Phase 1** — sanity-check the lookup format on 3 known-good `pcis_permit`s from the existing `ladbs_targeted.json` cache.
- **Phase 2** — lookup each of 15 Pipedream-cited `pcis_permit`s (3 of 18 projects have no PCIS-specific deep link in `source_urls`) on `hbkd-qubn`, `cpkv-aajs`, `3f9m-afei`, and `ydma-y4hd`.
- **Phase 3** — `web_fetch` the LADBS Online Services PermitReport page for each miss; extract permit type, sub-type, issue date, issuing office, current status, Void/Reactivate event count, CofO status, latest inspection date.
- **Phase 4** — attempt to pull a known-good comparison set of Bldg-New permits from `hbkd-qubn` issued in 2023+.
- **Phase 5** — query Socrata catalog for any LADBS permit-related dataset; capture metadata and `:updated_at`.
- **Phase 6** — if a replacement is identified, verify all 13 missing permits land in it.

All Socrata queries, catalog reads, and portal fetches were read-only and anonymous. No Supabase writes. No `src/` / `config/` / `alembic/` changes. No commits.

## 3. Evidence

### 3.1 Lookup format is fine (Phase 1)

Three permits selected from our existing `hbkd-qubn`-cached `ladbs_targeted.json` all returned correctly on a direct `pcis_permit='...'` lookup:

| permit | dataset | result |
|---|---|---|
| `13014-10000-00641` | hbkd-qubn | 1 row, Bldg-Addition at 2310 2ND, 90057, issue_date 2013-03-07 |
| `13042-20000-08823` | hbkd-qubn | 1 row, Plumbing at 831 GRAND VIEW, 90057, issue_date 2013-05-08 |
| `13041-20000-32661` | hbkd-qubn | 1 row, Electrical at 827 CARONDELET, 90057, issue_date 2013-11-21 |

Hypothesis 1 refuted.

### 3.2 Coverage test on 15 Pipedream-cited permits (Phase 2)

Each lookup was exact: `GET https://data.lacity.org/resource/<dataset>.json?$where=pcis_permit='<N>'&$limit=5&$select=*` for the three `*_permit`-keyed datasets, and the equivalent with `permit='<N>'` for `ydma-y4hd`.

| permit | hbkd-qubn | cpkv-aajs | 3f9m-afei | ydma-y4hd | project (short id) |
|---|---|---|---|---|---|
| 18010-10000-03620 | miss | miss | miss | 404 | 329 S Bonnie Brae (2b70f723) |
| 23010-10000-00516 | miss | miss | miss | 404 | 1925 Montrose (2cb1c01b) |
| 21010-10000-04285 | miss | miss | miss | 404 | 1402 S Veteran (38b0b827) |
| 19010-20000-05733 | miss | miss | miss | 404 | 3547 S Overland (39735360) |
| 19010-10000-00654 | miss | miss | miss | 404 | 668 S Coronado (4b33492d) |
| 22010-10000-06040 | miss | miss | miss | 404 | Asterix (53721a21) |
| 21010-10000-00744 | miss | miss | miss | 404 | 1000 N Alfred (6a70fb79) |
| 18010-10000-01517 | miss | miss | miss | 404 | 684 S New Hampshire (883c8279) |
| 21010-10000-04317 | miss | miss | miss | 404 | Echo 55 (96999f8b) |
| 20010-10000-04305 | miss | miss | miss | 404 | 555 Harvard (98e4ef00) |
| 19010-10000-05729 | **hit (1)** | **hit (1)** | miss | 404 | 700 N Virgil (c16613e2) |
| 22010-10000-04890 | miss | miss | miss | 404 | 10608 W Pico (db559007) |
| 20010-10000-03127 | **hit (1)** | **hit (1)** | miss | 404 | 2121 Westwood (dc5ea392) |
| 19010-10000-02601 | miss | miss | miss | 404 | Burlington Place (e1a06b1f) |
| 23010-10000-00914 | miss | miss | miss | 404 | 10505 Washington (fab731c6) |

13 of 15 permits are absent from all three of `hbkd-qubn`, `cpkv-aajs`, and `3f9m-afei`. `ydma-y4hd` returns HTTP 404 at `/resource/ydma-y4hd.json` — the dataset has been removed entirely.

The 2 hits (`19010-10000-05729` for 700 N Virgil; `20010-10000-03127` for 2121 Westwood) were issued **before** the 2023-05-22 freeze (issue_date 2022-05-17 and 2022-08-11 respectively). Those are the only reason they made it into the legacy snapshot.

### 3.3 Portal fields for the 13 missing permits (Phase 3)

Fetched from `https://www.ladbsservices2.lacity.org/OnlineServices/PermitReport/PcisPermitDetail?id1=...&id2=...&id3=...` for each. All 13 pages rendered successfully with full detail.

| permit | type | sub_type | issue_date | office | current status | cofo | void/reactivate events | last inspection |
|---|---|---|---|---|---|---|---|---|
| 18010-10000-01517 | Bldg-New | Commercial | 8/9/2023 | Metro | Issued | Pending | 0 / 0 | 3/13/2026 |
| 18010-10000-03620 | Bldg-New | Apartment | 5/23/2023 | Metro | Issued | Pending | 3 / 3 | 3/25/2026 |
| 19010-10000-00654 | Bldg-New | Apartment | 7/18/2023 | Metro | Issued | Pending | 0 / 0 | 4/13/2026 |
| 19010-10000-02601 | Bldg-New | Commercial | 11/16/2023 | West | Issued | Pending | 0 / 0 | 8/8/2025 |
| 19010-20000-05733 | Bldg-New | Commercial | 8/23/2024 | Metro | Issued | Pending | 0 / 0 | 3/4/2026 |
| 20010-10000-04305 | Bldg-New | Commercial | 5/30/2023 | Metro | Issued | Pending | 0 / 0 | 3/26/2026 |
| 21010-10000-00744 | Bldg-New | Apartment | 3/13/2024 | West | Issued | Pending | 0 / 0 | 1/23/2026 |
| 21010-10000-04285 | Bldg-New | Apartment | 9/30/2024 | West | Issued | Pending | 0 / 0 | 3/9/2026 |
| 21010-10000-04317 | Bldg-New | Apartment | 2/28/2024 | Valley | Issued | Pending | 0 / 0 | 4/2/2026 |
| 22010-10000-04890 | Bldg-New | Commercial | 9/25/2024 | Metro | Issued | Pending | 0 / 0 | 3/25/2026 |
| 22010-10000-06040 | Bldg-New | Apartment | 3/4/2025 | West | Issued | Pending | 0 / 0 | 4/7/2026 |
| 23010-10000-00516 | Bldg-New | Apartment | 5/2/2025 | Current | Issued | Pending | 0 / 0 | 4/2/2026 |
| 23010-10000-00914 | Bldg-New | Commercial | 8/8/2025 | Metro | Issued | Pending | 0 / 0 | 4/14/2026 |

Observations:

- **All 13 are `Bldg-New`**, which is the permit_type `hbkd-qubn` and `cpkv-aajs` are supposed to publish (and `ladbs_permits`'s configured `soql_filter: "permit_type='Bldg-New'"` should match).
- **All 13 are currently `Issued`** (not Void). Only 1 of 13 has any Void/Reactivate history in its audit log. Hypothesis 2 (status filtering) refuted.
- **All have Pending CofO and recent inspection activity.** 11 of 13 have a last inspection in 2026; the other 2 are 8/8/2025 and 3/13/2026. These are actively-under-construction buildings.
- **Issuing offices are mixed**: Metro (7), West (4), Valley (1), Current (1). No obvious office-based pattern.
- **Sub-types are split**: Apartment (8) and Commercial (5). No obvious sub-type filtering pattern.
- **Issue dates span 2023-05-23 through 2025-08-08.** The oldest, 18010-10000-03620 at 329 S Bonnie Brae, was issued on 5/23/2023 — four days after `hbkd-qubn`'s last `:updated_at` of `2023-05-22T09:33:30.736Z`. Every permit issued after that date is invisible. Hypothesis 3 confirmed below in 3.4.

### 3.4 The freeze is real (Phase 5)

For each relevant Socrata dataset, `MAX(:updated_at)` and `MAX(issue_date)`:

| dataset | total rows | MAX(:updated_at) | MAX(issue_date) | status |
|---|---|---|---|---|
| `hbkd-qubn` | 1,635,148 | `2023-05-22T09:33:30.736Z` | 2023-05-19 | **frozen** |
| `cpkv-aajs` | 25,715 | `2023-05-22T09:33:30.736Z` | 2023-05-19 | **frozen** |
| `3f9m-afei` | 54,110 | `2026-04-13T13:04:51.040Z` | 2026-04-10 | live |
| `pi9x-tg5x` (new) | 385,028 | `2026-04-13T15:34:41.210Z` | 2026-04-12 | live |
| `9w5z-rg2h` (new) | 11,329,970 | `2026-04-13T12:59:14.624Z` | 2026-04-13 | live |

Note that `hbkd-qubn` and `cpkv-aajs` have **identical** `:updated_at` values down to the millisecond (`2023-05-22T09:33:30.736Z`), which is a strong signal that the publisher snapshotted both datasets at the same moment and stopped updating them together. Their `min(:updated_at)` is also identical at `2019-08-13T17:32:51.270Z`, indicating both were also bulk-refreshed from the same upstream process at the same earlier point.

By-year `issue_date` distribution corroborates:

| year | hbkd-qubn | cpkv-aajs | 3f9m-afei | pi9x-tg5x (Bldg-New) |
|---|---|---|---|---|
| 2026 | 0 | 0 | 1,331 | 1,452 |
| 2025 | 0 | 0 | 5,093 | 4,436 |
| 2024 | 0 | 0 | 4,746 | 3,687 |
| 2023 | 65,115 | 847 | 4,552 | 3,838 |
| 2022 | 175,800 | 2,364 | 4,338 | 4,628 |
| 2021 | 158,039 | 2,059 | 4,075 | 3,518 |
| 2020 | 140,019 | 1,971 | 4,474 | 3,075 |

`hbkd-qubn` and `cpkv-aajs` have zero rows for 2024, 2025, and 2026. `3f9m-afei` and the new `pi9x-tg5x` both show healthy 2024-2026 volume.

Hypothesis 3 (freeze / collection lag) confirmed. Hypothesis 4 (scope difference) is also partially in play but secondary: the primary mechanism is the freeze, and the "scope" difference is simply that one family of dataset IDs has stopped publishing.

### 3.5 All 13 missing permits land in `pi9x-tg5x` (Phase 6)

Lookups against the new dataset used the schema's renamed permit column: `$where=permit_nbr='<N>'`. All 13 hit.

| permit | pi9x-tg5x address | pi9x-tg5x issue_date | pi9x-tg5x status | seed address (mismatch delta) |
|---|---|---|---|---|
| 18010-10000-03620 | 329 S BONNIE BRAE ST 1-30 90057 | 2023-05-23 | Issued | 329 S BONNIE BRAE ST 90057 (unit range added) |
| 23010-10000-00516 | 1925 W MONTROSE ST 1-19 90026 | 2025-05-02 | Issued | 1925 W MONTROSE ST 90026 (unit range added) |
| 21010-10000-04285 | **10978 W WILKINS AVE** 1-23 90024 | 2024-09-30 | Issued | 1402 S VETERAN AVE 90024 (**different street**) |
| 19010-20000-05733 | 3555 S OVERLAND AVE 90034 | 2024-08-23 | Issued | 3555 OVERLAND AVE 90034 |
| 19010-10000-00654 | 668 S CORONADO ST 90057 | 2023-07-18 | Issued | 668 S CORONADO ST 90057 |
| 22010-10000-06040 | **6066 W OLYMPIC BLVD** 1-120 90036 | 2025-03-04 | Issued | 6052 W OLYMPIC BLVD 90036 (**14 off**) |
| 21010-10000-00744 | **1002 N ALFRED ST** 90069 | 2024-03-13 | Issued | 1000 N ALFRED ST 90069 (**2 off**) |
| 18010-10000-01517 | 684 S NEW HAMPSHIRE AVE 1-170 90005 | 2023-08-09 | Issued | 684 S NEW HAMPSHIRE AVE 90010 (seed ZIP wrong) |
| 21010-10000-04317 | 1655 N ALLESANDRO ST 1-42 90026 | 2024-02-28 | Issued | 1655 N ALLESANDRO ST 90026 |
| 20010-10000-04305 | **549 S HARVARD BLVD** 90020 | 2023-05-30 | Issued | 555 S HARVARD BLVD 90020 (**6 off**) |
| 22010-10000-04890 | **10610 W PICO BLVD** 1-50 90064 | 2024-09-25 | Issued | 10608 W PICO BLVD 90064 (**2 off**) |
| 19010-10000-02601 | **255 S BURLINGTON AVE** 1-130 90057 | 2023-11-16 | Issued | 261 S BURLINGTON AVE 90057 (**6 off**) |
| 23010-10000-00914 | 10505 W WASHINGTON BLVD 1-184 90232 | 2025-08-08 | Issued | 10505 WASHINGTON BLVD 90232 |

Separately, `9w5z-rg2h` inspections were spot-checked for 9 of the 13; 7 returned active inspection histories (53 to 207 inspections each, latest 2025-08 to 2026-04). The 9w5z permit column uses spaces instead of dashes as the inter-segment separator (`'18010 10000 03620'` vs `'18010-10000-03620'`), a format difference any new adapter will need to normalize.

Observations on the `pi9x-tg5x` addresses:

- 7 of 13 addresses in the new permit feed **differ** from the seed — by a small number of houses (±2 to ±14), by a ZIP code, or by the street name entirely (`1402 S VETERAN AVE` → `10978 W WILKINS AVE` for permit `21010-10000-04285`; that's a major corner-lot or address-reassignment discrepancy, not a rounding error).
- 7 of 13 include a unit range in `primary_address` (`... 1-30`, `... 1-184`) that our seed's `canonical_address` does not. The production normalizer would need to handle this.
- These address mismatches are independent of the freeze. Even with `pi9x-tg5x` wired, a strict canonical-address match against these permits would fail for the 7 with address discrepancies. Matching must use `permit_number` or `apn` to reliably link.

### 3.6 Schema differences across old and new datasets

The new permit feed `pi9x-tg5x` uses a different row shape than the old `hbkd-qubn`:

| concept | `hbkd-qubn` column | `pi9x-tg5x` column |
|---|---|---|
| permit number | `pcis_permit` | `permit_nbr` |
| address | split into `address_start`, `address_end`, `street_direction`, `street_name`, `street_suffix` | single `primary_address` string |
| APN | not present | single `apn` string (10-digit, book+page+parcel concatenated) |
| ZIP | `zip_code` | `zip_code` |
| permit category / group | `permit_type` (e.g., `Bldg-New`) | `permit_type` (same values) |
| sub-type | `permit_sub_type` | `permit_sub_type` (same values) |
| valuation | `valuation` | `valuation` |
| work description | `work_description` | `work_desc` |
| status | (not exposed) | `status_desc` (values `Issued`, `Permit Finaled`, ...) |
| inspections timeline | not present | not present (available separately in `9w5z-rg2h`) |
| geometry | `location_1` (cpkv-aajs), not on `hbkd-qubn` | `geolocation`, `lat`, `lon` |
| use descriptor | not present | `use_desc` (e.g. `Dwelling - Single Family`, `Apartment`) |
| lifecycle refresh time | `:updated_at` | `refresh_time` plus `:updated_at` |

The `pi9x-tg5x` APN is already in the 10-digit concatenated shape that `_build_assessor_apn` in `src/tcg_pipeline/source_adapters/ladbs.py` produces. That simplifies APN-based matching against the new dataset.

The `primary_address` string must be parsed rather than read field-by-field. The project's existing `normalize_address()` in `src/tcg_pipeline/matching/normalizer.py` is `usaddress`-backed and should handle single-line addresses well; it would just be called with the whole string rather than reassembled from pieces.

The `use_desc` field effectively encodes the housing-vs-other distinction that `cpkv-aajs` provided as a separate dataset. A `$where=permit_type='Bldg-New' AND use_desc LIKE '%Apartment%'` query on `pi9x-tg5x` would likely reproduce most of what `ladbs_new_housing` is currently meant to capture, without a second dataset pull.

### 3.7 Catalog-level deprecation signaling

`https://data.lacity.org/api/catalog/v1` metadata snapshot (2026-04-17):

| dataset | name | provenance | createdAt | last catalog metadata edit | rows updated |
|---|---|---|---|---|---|
| `hbkd-qubn` | LADBS-Permits | community | 2017-09-03 | 2025-01-14 | frozen at 2023-05-22 |
| `cpkv-aajs` | Building Permits: New Housing Units | official | 2015-01-08 | 2025-01-29 | frozen at 2023-05-22 |
| `3f9m-afei` | Building and Safety Certificate of Occupancy | official | 2014-04-18 | 2026-04-13 | live |
| `pi9x-tg5x` | Building and Safety - Building Permits Issued from 2020 to Present (N) | official | **2023-03-22** | 2026-04-13 | live |
| `gwh9-jnip` | Building and Safety - Building Permits Submitted from 2020 to Present (N) | official | 2023-03-22 | 2026-04-13 | live |
| `dyxf-7hc4` | Building and Safety - Building Permits Issued Between 2010 and 2019 (N) | official | 2023-03-22 | 2026-04-13 | live |
| `e67z-kt2n` | Building and Safety - Building Permits Issued Before 2010 (N) | official | 2023-03-22 | 2026-04-13 | live |
| `9w5z-rg2h` | Building and Safety Inspections | official | 2014-04-21 | 2026-04-13 | live |

The new "from 2020 to Present (N)" series was created on 2023-03-22, two months before the old feeds stopped. LADBS spent that window running both in parallel, then stopped updating the old ones on 2023-05-22. Notable detail: `hbkd-qubn`'s provenance is `community` while the rest are `official` — the old dataset was a community-maintained mirror, which is consistent with its not getting a maintained replacement in place. The `(N)` suffix in the new dataset names likely denotes "New" (the post-2023 schema).

The hbkd-qubn dataset description on Socrata contains **no deprecation banner** — a new user discovering it via Socrata search would see a description that reads as if it were current. That's an operational hazard for anyone building against this.

## 4. Conclusion

Hypotheses revisited:

1. **Query / encoding issue** — ruled out. Format works on known-good permits.
2. **Status-based filtering** — ruled out. 12 of 13 missing permits are currently `Issued` with no Void events. The 1 with Void/Reactivate cycles is currently `Issued` and would be in-scope for any sane filter.
3. **Collection lag / data freshness** — **confirmed as the dominant mechanism.** `hbkd-qubn` and `cpkv-aajs` are frozen at `2023-05-22T09:33:30.736Z`. No row has been updated in ~35 months.
4. **Dataset scope difference** — confirmed as a secondary mechanism. LADBS replaced the old feeds with a new series of "from 2020 to Present" / "Between 2010 and 2019" / "Before 2010" datasets under different IDs, with a different schema, slightly different field names, and a different provenance tag. The old IDs were simply abandoned rather than retired formally.
5. **Alternate Socrata view (`ydma-y4hd`)** — deleted from the catalog entirely; returns 404 at `/resource/ydma-y4hd.json`.

Mechanism summary: the project is pulling from two LADBS dataset IDs that the City of Los Angeles stopped updating 35 months ago and replaced with four new dataset IDs on the same Socrata domain. The new IDs have been available for more than two years and are actively maintained, but the project's `config/markets/los_angeles.yaml` still points at the deprecated IDs. Every permit issued after 2023-05-19 is therefore not in the collected bundle.

## 5. Recommendation

The primary next lever is **not** seed cleanup and **not** matcher work — those were the recommendations of the prior triage doc, and both are still worthwhile, but they are downstream of the Socrata-coverage gap. **Wire the new dataset IDs first**, then re-run the recall audit on the updated bundle, then judge whether the residual gap still warrants seed cleanup or matcher work.

Concrete, prioritized next steps:

1. **Replace `ladbs_permits` with a new adapter against `pi9x-tg5x`.** The configuration change in `config/markets/los_angeles.yaml` is small (endpoint URL + SoQL filter + matching keys). The adapter change in `src/tcg_pipeline/source_adapters/ladbs.py` is larger because the row shape differs — single-line `primary_address` instead of split fields, single-string `apn` instead of `book`/`page`/`parcel`, `permit_nbr` instead of `pcis_permit`, `work_desc` instead of `work_description`, `status_desc` available where the old dataset had no explicit status column. Keep `permit_type='Bldg-New'` as the narrow slice.

2. **Replace `ladbs_permit_activity` with a second `pi9x-tg5x` source filtered to `permit_type != 'Bldg-New'`.** Same adapter as (1), different filter. Retain the `create_new_candidates: false` behavior.

3. **Retire `ladbs_new_housing` (`cpkv-aajs`) as a standalone source.** The housing-specific slice is derivable from `pi9x-tg5x` via `$where=use_desc LIKE '%Apartment%' OR use_desc LIKE '%Dwelling%' OR ...` (the exact predicate should be profiled against `use_desc` values). Folding it into the primary `pi9x-tg5x` adapter avoids maintaining two parallel sources and removes the field-authority ambiguity that the spec flagged. Note: the 2 cpkv-aajs-only rescues (Hudson ZIP mismatch, 700 N Virgil/Melrose) from the original audits were based on pre-2023 permits that are in the legacy feed. Any post-2023 housing discovery had no way to come through `cpkv-aajs` anyway.

4. **Keep `ladbs_cofo` (`3f9m-afei`) as-is.** It is live and functioning.

5. **Investigate and likely add `9w5z-rg2h` as a new inspections source.** The project's `docs/source_specs/ladbs_socrata_completeness.md` currently treats `2w4b-a48u` as a "not ready" inspection feed because its schema only has `permit` and `permit_status`. `9w5z-rg2h` has `permit`, `inspection`, `inspection_date`, `inspection_result`, `permit_status`, `address`, `lat_lon` and 11.3M rows. Inspection activity after a permit's issue date is direct `Under Construction` evidence, which the current bundle has no source for. That said, the permit column uses space-separated instead of dash-separated format — `'18010 10000 03620'` vs `'18010-10000-03620'` — which any new adapter must normalize.

6. **Harvest `permit_number` identifiers from Pipedream `source_urls`** during seed processing. Even before the new adapters land, recording the PCIS permit numbers the Pipedream researchers have already bookmarked gives the matcher a strong key to link against the day those rows become collectible. This is a small enrichment change to the seed ingestion path.

7. **Update `docs/source_specs/ladbs_socrata_completeness.md`** to mark `hbkd-qubn` and `cpkv-aajs` as deprecated, document `pi9x-tg5x`, `9w5z-rg2h`, and the deprecation of `ydma-y4hd`, and capture the `:updated_at` snapshot dates from section 3.4 as reproducible evidence.

The prior triage doc's recommendation (seed cleanup as primary lever) is still correct in intent but insufficient on its own. Approximately 13 of the 18 Pipedream projects sampled here have correct seeds and real active LADBS permits — they can only be recovered by wiring the new dataset. Seed cleanup's role shifts to second-priority: after the new sources are wired, address-number and ZIP mismatches (5 of 13 Pipedream permits have a material seed-vs-permit-address delta) would need a small seed-enrichment pass to convert Socrata rows into linked project records. At that point the residual unexplained gap should be much smaller than the current 71-project bucket, and the right question for the team will be how small.

## 5a. Calibration: CoStar-only Spot-Check (n=10)

The Phase 2 systematic test covered only the 18 Pipedream-seeded projects (15 with extractable PCIS
numbers) because those are the cases where Pipedream researchers had already verified the LADBS
permit via Online Services deep links. That left the dominant bucket of the 71 — **53 CoStar-only
projects, ~75% of the not-found set** — untested. Before committing to "wire the new datasets and
the gap closes," this spot-check queries a deterministic sample of 10 CoStar-only projects against
`pi9x-tg5x` by APN and by (zip, leading address number) to see whether LADBS has them.

Sample selection: sort the 53 CoStar-only projects by `project_id` ascending; pick evenly-spaced
indices `[int(step*(i+0.5)) for i in range(10)]` where `step = 53/10`.

| # | short id | project name | canonical_address | seeded APN | Bldg-New 2023+ by APN | Bldg-New 2023+ by addr (leading #, zip) | other pi9x-tg5x hits worth noting | outcome |
|---|---|---|---|---|---|---|---|---|
| 1 | `07fa917c` | The Clark on 54th | 5353 CRENSHAW BLVD 90043 | 5006-006-007 | 1/4 | 0/2 | — | RECOVERS |
| 2 | `147c8b14` | (no name) | 265 N BURLINGTON AVE 90026 | 5159-006-029 | 0/0 | 0/4 | 4 hits are all on 265 S Columbia / 265 S Lucas (different streets) | no LADBS record at seeded address |
| 3 | `254c935a` | (no name) | 11967 MAYFIELD AVE 90049 | 4265-009-175 | 0/3 | 1/2 | — | RECOVERS |
| 4 | `68fd77c1` | Alveare Senior Housing | 1421 S BROADWAY 90015 | none | 0/0 | 0/1 | 1 addr hit is garage permit at 1421 S Albany (different street) | no LADBS record; no APN seeded |
| 5 | `7ea4d763` | (no name) | 1734 S BARRINGTON AVE 90025 | 4262-018-026 | 1/6 | 1/7 | — | RECOVERS |
| 6 | `98012028` | The Standard at Los Angeles | 3900 S FIGUEROA ST 90037 | 5037-032-003, -048, -049 | 0/11 | 1/12 | — | RECOVERS |
| 7 | `ad437b72` | TenTen Alfred | 1010 N ALFRED ST 90069 | 5529-007-061 | 0/0 | 0/0 | zero hits by APN OR address | no LADBS record at seeded APN or address |
| 8 | `b83b1aea` | Mama Shelter DTLA | 124 E OLYMPIC BLVD 90015 | 5139-015-041 | 0/15 | 0/15 | 8 `Bldg-Alter/Repair` permits with `use_desc='Hotel'` at the exact APN, 2023-2026 | adaptive reuse — recoverable via broader activity source, not `Bldg-New` |
| 9 | `d17f26f2` | Steps on St. Andrews | 1808 S ST ANDREWS PL 90019 | 5073-014-900 | 0/6 | 1/12 | — | RECOVERS |
| 10 | `efafad9b` | Peak Plaza Apartments | 316 E WASHINGTON BLVD 90015 | 5127-029-042 | 1/17 | 1/11 | — | RECOVERS |

**Tally:**

- 6/10 recover cleanly via `Bldg-New 2023+` in `pi9x-tg5x` (by APN or address).
- 1/10 (Mama Shelter DTLA) is an **adaptive-reuse** case: LADBS has 8 `Bldg-Alter/Repair` permits with `use_desc='Hotel'` at the exact seeded APN across 2023-2026, but no `Bldg-New`. The "Under Construction" seed status is accurate for the renovation; the permit family is just different. This case is in scope for a `ladbs_permit_activity` source rewired to `pi9x-tg5x` with `permit_type != 'Bldg-New'`, so it is also recoverable under the recommended adapter rewiring — just through the `update`-role source rather than the discovery source.
- 3/10 don't recover even with the new dataset: two have no LADBS record at the seeded (zip, leading number) and no APN match (265 N Burlington, TenTen Alfred), and one has no APN seeded and nothing at the seeded street (Alveare at 1421 S Broadway). These look like seed-address errors, genuinely pre-permit status, or truly absent-from-LADBS cases. No source-coverage change helps them.

**Extrapolation across the full 71, combining Pipedream (13/15 ≈ 87%) and CoStar (7/10 ≈ 70%) recovery rates, weighted by bucket size:**

| bucket | size | recovery rate (sample) | est. recoverable | est. residual |
|---|---|---|---|---|
| Pipedream-seeded with PCIS deep link | 15 | 13/15 | ~13 | ~2 |
| Pipedream-seeded without PCIS (Cursonair + 2 VA Buildings) | 3 | n/a (2 of 3 are wrong-jurisdiction federal land; 1 unknown) | 0 | 3 |
| CoStar-only | 53 | 7/10 | ~37 | ~16 |
| **Total (of 71)** | **71** | | **~50** | **~21** |

Confidence: 15 + 10 = 25 sampled permits is still directional, not precise. The ~50-of-71 recovery figure is the most defensible point estimate from the evidence collected; the true number could plausibly be in the 42-60 range given sample noise. The residual ~21 is where seed cleanup, matcher work, and jurisdiction-scope questions still matter.

**Revised confidence on the section 5 recommendation:**

- Wiring `pi9x-tg5x` (and the broader activity source on the same dataset with `permit_type != 'Bldg-New'`) is **necessary** to recover anywhere near the full bulk of the gap. Nothing else can touch post-2023 permits.
- It is **not sufficient** on its own. Even with perfect source coverage, roughly 21 of 71 projects in the current snapshot look like they would remain absent from a direct `(pi9x-tg5x x seeded APN union seeded address)` match, split across three causes:
  - **wrong-jurisdiction** (federal VA land, 90232 Culver City): ~3 projects. No LADBS source helps.
  - **seed-stale or adaptive-reuse mismatch** where the seed status doesn't match the LADBS permit family that exists: ~1-5 projects (Restorative Care Village completed 2022; Mama Shelter adaptive reuse is solved by the broader activity source).
  - **genuinely absent from LADBS, or seed-address-wrong in ways the current keys can't bridge**: ~13-17 projects. These are where seed cleanup (fixing wrong addresses, adding alternate frontages, validating APNs) and matcher work (APN-first matching, +/-N house-number tolerance for corner lots, project-name-as-address lookup for marketing names) remain the right levers — after the adapter rewire is done.
- The prior triage doc's recommendation (seed cleanup as primary lever) was not wrong about the work being needed; it was wrong about the sequencing and the size of the slice it would close. Seed cleanup would likely recover ~5-8 additional projects on top of the ~50 the adapter rewire unlocks, bringing total recovery to maybe ~55-58 of 71.

## 6. Reproducibility

All queries are deterministic and read-only. Representative commands:

```
# Confirm dataset freeze on the legacy feeds
GET https://data.lacity.org/resource/hbkd-qubn.json?$select=MAX(:updated_at)
GET https://data.lacity.org/resource/cpkv-aajs.json?$select=MAX(:updated_at)

# Confirm 3f9m-afei is live
GET https://data.lacity.org/resource/3f9m-afei.json?$select=MAX(:updated_at)

# Confirm a missing permit is in the new feed
GET https://data.lacity.org/resource/pi9x-tg5x.json?$where=permit_nbr='18010-10000-03620'&$select=*

# Confirm inspections exist in 9w5z-rg2h (note SPACE-separated permit format)
GET https://data.lacity.org/resource/9w5z-rg2h.json?$where=permit='18010 10000 03620'&$select=count(*),MAX(inspection_date)

# Confirm ydma-y4hd is gone
GET https://data.lacity.org/resource/ydma-y4hd.json   # HTTP 404

# Socrata catalog metadata per dataset
GET https://data.lacity.org/api/catalog/v1?ids=hbkd-qubn
GET https://data.lacity.org/api/catalog/v1?ids=pi9x-tg5x
```

Portal fetches used the LADBS Online Services URL pattern `https://www.ladbsservices2.lacity.org/OnlineServices/PermitReport/PcisPermitDetail?id1=<seg1>&id2=<seg2>&id3=<seg3>` where `<seg1>-<seg2>-<seg3>` is the `pcis_permit`. Portal responses are HTML; the fields in section 3.3's table were built by regex-extracting `Application/Permit`, `Group Building Type`, `Sub-Type`, `Issued on`, `Issuing Office`, `Current Status`, `Certificate of Occupancy`, and `Permit Closed-Status Void` / `Re-Activate Permit` event dates from each page.

Supabase reads used the same PostgREST path as prior audits: `GET https://qqnlbfncqwqkvsdufjwa.supabase.co/rest/v1/projects` filtered by `market=eq.los_angeles` and `pipeline_status=eq.Under Construction`, plus `GET .../rest/v1/project_identifiers` for seed identifiers.

The CoStar-only spot-check sample (section 5a) was selected deterministically by sorting the 53 CoStar-only not-found projects from the 71-bucket by `project_id` ascending and picking indices `[int(step*(i+0.5)) for i in range(10)]` where `step = 53/10`. Each project was queried against `pi9x-tg5x` by (a) seeded APN in the 10-digit concatenated form, and (b) `(zip, leading address number)` via `starts_with(primary_address, '<num> ')`. A project was counted as RECOVERS if any returned row was `permit_type='Bldg-New'` with `issue_date >= '2023-01-01'`.

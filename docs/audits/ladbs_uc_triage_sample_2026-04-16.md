# LADBS UC "Not-Found" Bucket — Stratified Triage Sample

Date run: 2026-04-16

Related docs:

- [ladbs_recall_audit_bundle_2026-04-16.md](./ladbs_recall_audit_bundle_2026-04-16.md)
- [ladbs_recall_audit_hbkd_cpkv_2026-04-16.md](./ladbs_recall_audit_hbkd_cpkv_2026-04-16.md)
- [ladbs_recall_audit_results_2026-04-16.md](./ladbs_recall_audit_results_2026-04-16.md)
- [ladbs_socrata_completeness.md](../source_specs/ladbs_socrata_completeness.md)

## 1. Purpose and Scope

The full-bundle LADBS recall audit on 2026-04-16 ([bundle audit](./ladbs_recall_audit_bundle_2026-04-16.md))
left `75 / 224` seeded `los_angeles` `Under Construction` projects outside the current LADBS bundle
(`hbkd-qubn` broad + `cpkv-aajs` + `3f9m-afei`). A follow-up identifier-based check in that same audit
found `0 / 75` of those projects recoverable through existing permit/APN-based production matching,
so widening the matcher's existing primitives was not the obvious next lever.

The question this document answers is: **which lever should the team pull next** — matcher /
normalization work, seed data cleanup, an LA Case Reports (DCP) adapter, a market-boundary rescope,
or some combination? It does so by re-deriving the not-found bucket under this session's
methodology, drawing a stratified sample of 15 projects, and running a bounded per-project
investigation that classifies each into one of six triage buckets.

This is a sizing pass. The output is directional, not definitive: `n=15` from `N=71` gives roughly
±25pp confidence on any single bucket proportion at the 95% level, and many of the judgment calls
here would benefit from a human analyst spending 20 minutes per project rather than the 5-8
minutes this automated pass spent.

## 2. Method

### 2.1 Environment notes

This audit ran read-only against the Supabase database (table `projects` where `market='los_angeles'`
and `pipeline_status='Under Construction'`; also read `project_identifiers` and
`project_source_records`). The project does not seed any `permit_number` identifiers for LA UC
projects; identifier coverage is APN from costar, `tcg_pipedream_id` from pipedream, and a handful
of `costar_property_id`s. Column names used here correspond to the real Supabase schema
(`projects.id`, `projects.pipeline_status`, `projects.canonical_address`, `projects.raw_addresses`,
`projects.zip`), not the naming used in the prior audit narrative.

Live Socrata data was pulled directly from `https://data.lacity.org/resource/` for datasets
`hbkd-qubn`, `cpkv-aajs`, and `3f9m-afei`. No `SOCRATA_APP_TOKEN` was configured in the `.env`,
so requests were anonymous and subject to rate-limiting and a default per-call row cap. All SoQL
`$where` clauses and result paths are cited inline in the per-project findings.

### 2.2 Reproducing the not-found list (Step 1)

Pull all LA UC projects via Supabase PostgREST:

```
GET https://qqnlbfncqwqkvsdufjwa.supabase.co/rest/v1/projects
    ?select=id,project_name,canonical_address,raw_addresses,zip,city,county,
            pipeline_status,previous_names,status_source,status_date,
            jurisdiction,market
    &market=eq.los_angeles
    &pipeline_status=eq.Under%20Construction
```

Result: 224 projects (matches the bundle audit's 224 UC denominator).

For each project compute the set of candidate `(zip, address_start)` pairs across
`canonical_address` plus every entry in `raw_addresses`. Ranges of the form
`\d+\s*[-\u2013]\s*\d+` are expanded to every integer in the range (capped at 40 for
pathologically wide ranges). The union of these pairs across all 224 UC projects is used as a
filter on each Socrata endpoint:

```
GET https://data.lacity.org/resource/{dataset}.json
    ?$where=zip_code='{ZIP}' AND address_start in('N1','N2',...)
    &$limit=5000
    &$select=*
```

Per-dataset row counts returned by this targeted pass: hbkd-qubn 7606, cpkv-aajs 143,
3f9m-afei 128.

A local street-name overlap match is then run per project. For each `(zip, address_start)` key,
a project is considered **found** if any Socrata row shares the ZIP, the address_start integer
(after range expansion), and at least one street-name token (direction-independent, suffix-
independent, ordinals normalized as in `src/tcg_pipeline/matching/normalizer.py`'s
`ORDINAL_WORD_MAP`). In addition, for `cpkv-aajs` and `3f9m-afei`, a project is also **found** if a
seeded APN matches any row's `assessor_book`/`assessor_page`/`assessor_parcel` after zero-padding
to the `4/3/3` shape used by `_build_assessor_apn` in `src/tcg_pipeline/source_adapters/ladbs.py`.

Result: **found = 153, not_found = 71, total = 224**.

The bundle audit reported 75 not-found; this pass reports 71. The 4-project drift is within the
`±minor drift expected` envelope called out in the task, and is attributable to (a) the bundle
audit's explicit address-start-only matching against a ZIP-capped hbkd sample versus this pass's
(zip, address_start)-pair query with in-memory street-name tokenization, and (b) minor street-
name tokenization differences between this pass and the original audit. The 71 derived here are
used as the working not-found denominator throughout the rest of this doc; the extrapolation
section scales to the 75 reported in the bundle audit for cross-document comparability.

### 2.3 Stratified sample (Step 2)

Heuristic buckets are computed from `canonical_address`:

- **A** — address range in the leading number (regex `^\s*\d+\s*[-\u2013]\s*\d+`).
- **B** — project name / low digit content (no leading number, or fewer than 3 non-ZIP digits).
- **C** — clean specific address (leading number + `LOS ANGELES CA \d{5}` suffix).
- **D** — other / ambiguous.

Observed distribution across the 71: **A=4, B=0, C=67, D=0**.

Targets are A=3, B=4, C=5, D=3 (15 total). Per the redistribution rule ("if a bucket has fewer
than its target, take all available and redistribute the remainder to the largest adjacent
bucket, not by oversampling a full one"): B's 4-project deficit shifts to C (largest adjacent);
D's 3-project deficit shifts to C (only adjacent). Final targets: A=3, B=0, C=12, D=0 — total 15.

Within each bucket, sort ascending by `project_id`, then pick evenly-spaced indices
`[int(step*(i+0.5)) for i in range(n)]` where `step = len(bucket)/n`. This produces a
deterministic sample given the same input and sort order.

The resulting 15 are listed in §4 below.

### 2.4 Per-project deep check (Step 3)

For each of the 15 sampled projects, the following were collected:

1. `raw_addresses`, `project_identifiers`, `project_source_records` from Supabase.
2. All LADBS rows at the project's `(zip, address_start-range)` across the three datasets
   (already in the §2.2 targeted pull).
3. cpkv-aajs and 3f9m-afei APN lookups per seeded APN:
   `$where=assessor_book='{B}' AND assessor_page='{P}' AND assessor_parcel='{R}'`.
4. Street-name-within-ZIP lookups across all three datasets, applied locally against the
   ZIP-scoped broad pull (`hbkd-qubn` was capped at 5000 rows/ZIP by Socrata; for the street-name
   surfacing purpose here this is acceptable because each project's distance-to-seeded inspection
   is anchored on the top-50 closest rows by address_start).
5. For 5 of the 15 sampled projects where project-name context suggested ambiguity, targeted
   WebSearch queries against Urbanize LA, LA YIMBY, CBRE, and similar trade-press sources. No
   WebFetch against `zimas.lacity.org` (JS-heavy, not WebFetch-friendly, as flagged in the task).
   DCP best-effort scan: where web results surfaced a DCP case number, it is noted; otherwise a
   manual-check URL (`https://planning.lacity.gov/development-services`) is logged.
6. Jurisdiction check: ZIP compared against typical City of LA ZIP bands (90001-90089,
   91040-91043, 91303-91316, etc.) to flag Beverly Hills (902xx excl. 90024-90025), Culver City
   (90232), or Santa Monica (904xx).
7. Stale-status check: any 3f9m-afei row returned during the APN or address lookups that covers
   the seeded project would indicate "seeded UC but LADBS CofO evidence exists."

Caps observed: ≤ 7 Socrata queries per project, ≤ 2 web searches per project.
## 3. Full 71-Project Not-Found List

Sorted by `project_id` ascending. `apns`, `pipedream_ids`, and `case_numbers` are drawn from `project_identifiers` (no `permit_number` identifiers are seeded for any LA UC project — this matches the bundle audit's observation). `costar_property_id`s are omitted from the table for brevity; they are in the seed for all costar-sourced rows.

| # | project_id | project_name | canonical_address | zip | raw_addresses | seeded APNs | seed source | pipedream id |
|---|-----------|--------------|-------------------|-----|---------------|-------------|-------------|-------------|
| 1 | `04cf01c9-c551-47f9-bfef-e48105db47e7` | Echo 55 | 1655 ALLESANDRO STREET LOS ANGELES CA 90026 | 90026 | 1655 Allesandro St | 5423-010-033 | costar | — |
| 2 | `0505359c-e9e8-44e0-b6ec-e4342713783c` | — | 1011 NORTH SYCAMORE AVENUE LOS ANGELES CA 90038 | 90038 | 1011 N Sycamore Ave | 5531-014-014 | costar | — |
| 3 | `07fa917c-3e50-4a96-b1c8-0f5be90cdfc8` | The Clark on 54th | 5353 CRENSHAW BOULEVARD LOS ANGELES CA 90043 | 90043 | 5353 Crenshaw Blvd | 5006-006-007 | costar | — |
| 4 | `0a381729-ee0d-42bf-9fc2-5956dc45b118` | — | 1408 WEST 62ND STREET LOS ANGELES CA 90044 | 90044 | 1408 W 62nd St | 6002-026-025 | costar | — |
| 5 | `0b41930c-d2cd-4125-b515-716286a8a5d0` | Alma Apartments | 3524 EAST 1ST STREET LOS ANGELES CA 90063 | 90063 | 3524 E 1st St | 5232-020-047 | costar | — |
| 6 | `0de7bfde-5fd8-4138-b2af-78d1197de5c2` | — | 1059 SOUTH HOLT AVENUE LOS ANGELES CA 90035 | 90035 | 1059 S Holt Ave | 4332-024-025 | costar | — |
| 7 | `0e73f466-dc25-4c14-b4c5-02ea2122eae4` | North Mathews | 121 NORTH MATHEWS STREET LOS ANGELES CA 90033 | 90033 | 121 N Mathews St | 5180-002-009 | costar | — |
| 8 | `147c8b14-14d5-4872-b813-ea3f97012a44` | — | 265 NORTH BURLINGTON AVENUE LOS ANGELES CA 90026 | 90026 | 265 N Burlington Ave | 5159-006-029 | costar | — |
| 9 | `18c3a172-c64e-4078-9697-a50d4c003998` | Step Apartments | 2735 EAST 6TH STREET LOS ANGELES CA 90023 | 90023 | 2735 E 6th St | 5185-017-033 | costar | — |
| 10 | `1ca77896-5a90-4126-83b4-635451312ffc` | East End Studios – Mission Campus | 2233-2251 JESSE STREET LOS ANGELES CA 90021 | 90021 | 2233-2251 Jesse St | 5171-016-010 | costar | — |
| 11 | `1e4674b0-88cc-4065-920a-d68148a6c31e` | Normandie | 3565 SOUTH NORMANDIE AVENUE LOS ANGELES CA 90007 | 90007 | 3565 S Normandie Ave | 5041-011-002, 5041-011-041 | costar | — |
| 12 | `218720e5-d4f5-44d6-b225-b8e275bedb21` | — | 6239 BANNER AVENUE LOS ANGELES CA 90038 | 90038 | 6239 Banner Ave | 5534-006-003 | costar | — |
| 13 | `2258026c-9d9b-4f38-b8e6-09412527e4a1` | — | 3344 MEDFORD STREET LOS ANGELES CA 90063 | 90063 | 3344 Medford St | 5224-010-006 | costar | — |
| 14 | `254c935a-d4eb-4b5d-9209-35abfc7ddf77` | — | 11967 MAYFIELD AVENUE LOS ANGELES CA 90049 | 90049 | 11967 Mayfield Ave | 4265-009-175 | costar | — |
| 15 | `2b70f723-94a9-4254-b14d-25673e0d61ae` | 329 S Bonnie Brae St | 329 SOUTH BONNIE BRAE STREET LOS ANGELES CA 90057 | 90057 | 329 S Bonnie Brae St | 5154-027-008 | pipedream,costar | 23.00075 |
| 16 | `2cb1c01b-2ecd-4cf4-9180-1ecc2a7d59cb` | 1925 Montrose | 1925 WEST MONTROSE STREET LOS ANGELES CA 90026 | 90026 | 1925 W Montrose Street | — | pipedream | 23.00296 |
| 17 | `354a6aac-3b88-47a3-9109-754d04a84eec` | Pico & Curson | 5566 PICO BOULEVARD LOS ANGELES CA 90019 | 90019 | 5566 Pico Boulevard | — | pipedream | 23.00360 |
| 18 | `38b0b827-57b5-4dcf-897c-5eb22d905400` | 1402 S Veteran Ave | 1402 SOUTH VETERAN AVENUE LOS ANGELES CA 90024 | 90024 | 1402 S Veteran Ave | — | pipedream | 24.00205 |
| 19 | `39735360-f786-4db5-9a62-2502e7e7ae4f` | 3547 S Overland Ave | 3555 OVERLAND AVENUE LOS ANGELES CA 90034 | 90034 | 3555 Overland Avenue; 3555 Overland Ave | 4252-035-041, 4252-035-042 | pipedream,costar | 24.00051 |
| 20 | `4000e272-60ea-4789-8fad-3938b614aa4a` | West 5th LA | 1441 5TH STREET LOS ANGELES CA 90071 | 90071 | 1441 5th St | 5153-024-030 | costar | — |
| 21 | `4b33492d-2d8e-4465-92dc-1514c09a25b3` | 668 Coronado | 668 SOUTH CORONADO STREET LOS ANGELES CA 90057 | 90057 | 668 S Coronado St | 5141-007-016 | pipedream,costar | 23.00082 |
| 22 | `53721a21-8479-4591-a614-4e7865b0395b` | Asterix | 6052 WEST OLYMPIC BOULEVARD LOS ANGELES CA 90036 | 90036 | 6052 W Olympic Blvd | — | pipedream | 24.00192 |
| 23 | `56452799-af8c-48b4-8018-d69e95508195` | — | 1700 ZONAL AVENUE LOS ANGELES CA 90033 | 90033 | 1700 Zonal Avenue | 5201-001-901 | costar | — |
| 24 | `56e43e92-1bb8-4dd4-87f8-773886e91c4a` | — | 684 SOUTH NEW HAMPSHIRE AVENUE LOS ANGELES CA 90005 | 90005 | 684 S New Hampshire Ave | 5094-008-048 | costar | — |
| 25 | `63064b99-3554-4036-a686-1b851a0df911` | Alveare Phase I | 1405 SOUTH BROADWAY LOS ANGELES CA 90015 | 90015 | 1405 S Broadway | — | costar | — |
| 26 | `68fd77c1-89e2-42da-96e1-b7d0b2b3f476` | Alveare Senior Housing | 1421 SOUTH BROADWAY LOS ANGELES CA 90015 | 90015 | 1421 S Broadway | — | costar | — |
| 27 | `698cf257-e60a-47cb-90e3-fb26af329ce5` | Construction Technology Building | 2100-2214 SOUTH GRAND AVENUE LOS ANGELES CA 90007 | 90007 | 2100-2214 S Grand Ave | 5126-022-900, 5126-023-906 | costar | — |
| 28 | `6a70fb79-7888-49af-8e81-7e0a543e9588` | 1000 N Alfred Street | 1000 NORTH ALFRED STREET LOS ANGELES CA 90069 | 90069 | 1000 N Alfred Street | — | pipedream | 24.00274 |
| 29 | `6c154775-061c-47f1-b8bc-6bb0d3263208` | VA Buildings 206, 210, 256, and 257 | 206 VANDERGRIFT AVENUE LOS ANGELES CA 90049 | 90049 | 206 Vandergrift Ave | — | pipedream | 24.00198 |
| 30 | `70c5e20e-8c22-4073-8c94-2fff3acc50a3` | — | 1747 STONER AVENUE LOS ANGELES CA 90025 | 90025 | 1747 Stoner Ave | 4262-016-011 | costar | — |
| 31 | `720d9077-6756-44d2-b83d-06f8313e17c3` | — | 836 WEST 42ND PLACE LOS ANGELES CA 90037 | 90037 | 836 W 42nd Pl | 5019-006-032 | costar | — |
| 32 | `7c19d98a-b735-4815-9ddc-6e5a5d0de640` | Rosa's Place | 501 EAST 5TH STREET LOS ANGELES CA 90013 | 90013 | 501 E 5th St | 5147-007-901 | costar | — |
| 33 | `7ea4d763-58e9-4ade-b7b9-06a04a810f23` | — | 1734 SOUTH BARRINGTON AVENUE LOS ANGELES CA 90025 | 90025 | 1734 S Barrington Ave | 4262-018-026 | costar | — |
| 34 | `8336a001-9623-4dcc-9eb4-e69a43bd0572` | Hotel La Fleur Los Angeles, Outset… | 1318 SOUTH FLOWER STREET LOS ANGELES CA 90015 | 90015 | 1318 S Flower St | 5134-011-018 | costar | — |
| 35 | `875b3f9d-eaaa-40d0-ab6a-ae2e1fc7dbf3` | Burlington Place | 255 SOUTH BURLINGTON AVENUE LOS ANGELES CA 90057 | 90057 | 255 S Burlington Ave | 5154-021-034 | costar | — |
| 36 | `883c8279-4a1d-496e-b8a5-1e59e42c19df` | 684 S New Hampshire | 684 SOUTH NEW HAMPSHIRE AVENUE LOS ANGELES CA 90010 | 90010 | 684 South New Hampshire Avenue | — | pipedream | 23.00316 |
| 37 | `8d0fbf2f-b4d0-4402-bff1-2f7999fd5080` | — | 4129 SOUTH CENTINELA AVENUE LOS ANGELES CA 90066 | 90066 | 4129 S Centinela Ave | 4231-018-020 | costar | — |
| 38 | `8ee31923-bbb8-4233-b17f-66f14418ad91` | Grandview Apartments | 714 SOUTH GRAND VIEW STREET LOS ANGELES CA 90057 | 90057 | 714 S Grand View St | — | costar | — |
| 39 | `962288b0-2605-4440-bb66-7be13e442c2a` | Metro at Florence | 1642 EAST FLORENCE AVENUE LOS ANGELES CA 90001 | 90001 | 1642 E Florence Ave | 6021-019-013 | costar | — |
| 40 | `96999f8b-b8be-4234-8260-58fe3bc1bbb8` | Echo 55 | 1655 NORTH ALLESANDRO STREET LOS ANGELES CA 90026 | 90026 | 1655 North Allesandro Street | — | pipedream | 23.00233 |
| 41 | `98012028-2a36-48fe-8337-47df6207064e` | The Standard at Los Angeles | 3900 SOUTH FIGUEROA STREET LOS ANGELES CA 90037 | 90037 | 3900 S Figueroa St | 5037-032-003, 5037-032-048, 5037-032-049 | costar | — |
| 42 | `98e4ef00-0e39-41db-b5c7-762dda5c60b6` | 555 Harvard | 555 SOUTH HARVARD BOULEVARD LOS ANGELES CA 90020 | 90020 | 555 South Harvard Blvd | — | pipedream | 23.00211 |
| 43 | `a6729b1e-48bc-45b5-bbde-7e4754c27c9b` | Manchester Urban Homes | 8727 SOUTH BROADWAY LOS ANGELES CA 90003 | 90003 | 8727 S Broadway | 6040-019-003, 6040-019-019 | costar | — |
| 44 | `a7bac194-bf85-4805-85b9-fbf535f40417` | — | 502 NORTH OXFORD AVENUE LOS ANGELES CA 90004 | 90004 | 502 N Oxford Ave | 5521-013-012 | costar | — |
| 45 | `ac920d1f-af2d-4e8a-84ff-6d2b676a4698` | — | 4429 SOUTH VERMONT AVENUE LOS ANGELES CA 90037 | 90037 | 4429 S Vermont Ave | 5017-032-030 | costar | — |
| 46 | `ace985d6-595a-4ddb-8241-2f3291346bc0` | — | 11835 TENNESSEE PLACE LOS ANGELES CA 90064 | 90064 | 11835 Tennessee Pl | 4259-037-003 | costar | — |
| 47 | `ad437b72-accf-46e5-95d9-82f4128ca466` | TenTen Alfred | 1010 NORTH ALFRED STREET LOS ANGELES CA 90069 | 90069 | 1010 N Alfred St | 5529-007-061 | costar | — |
| 48 | `af624b8a-6a38-4751-8935-ea8cc436bca0` | — | 8100 SOUTH FIGUEROA STREET LOS ANGELES CA 90003 | 90003 | 8100 S Figueroa St | 6032-031-015 | costar | — |
| 49 | `b074000f-376e-4a3b-84a6-de04f886270a` | — | 232 JUDGE JOHN AISO STREET LOS ANGELES CA 90012 | 90012 | 232 Judge John Aiso St; 230 N Judge John Ais… | 5161-012-901 | costar | — |
| 50 | `b4562a75-9f64-49d9-b622-4617e71f59e9` | Orion 1408 Jefferson | 1408 WEST JEFFERSON BOULEVARD LOS ANGELES CA 90007 | 90007 | 1408 W Jefferson Blvd | 5040-021-006 | costar | — |
| 51 | `b69ae629-a258-4ddd-94c1-c44017622475` | Cursonair | 5566-5566 WEST PICO BOULEVARD LOS ANGELES CA 90019 | 90019 | 5566-5566 W Pico Blvd | 5069-019-002 | costar | — |
| 52 | `b83b1aea-2e5c-4ae5-9f41-4d18bc174091` | Mama Shelter DTLA | 124 EAST OLYMPIC BOULEVARD LOS ANGELES CA 90015 | 90015 | 124 E Olympic Blvd | 5139-015-041 | costar | — |
| 53 | `babeb310-dd15-41a1-b19c-22039bf28de7` | — | 343 SOUTH 20 LOS ANGELES CA 90031 | 90031 | 343 S Avenue 20 | 5447-031-020 | costar | — |
| 54 | `c16613e2-c6fa-44b8-b7dd-3f11964acf16` | 700 North Virgil Avenue | 3981 MELROSE AVENUE LOS ANGELES CA 90029 | 90029 | 3981 Melrose Avenue | — | pipedream | 23.00338 |
| 55 | `c44d8d4c-512b-45cf-8ab1-eae3aecf0120` | Brynhurst | 6018 BRYNHURST AVENUE LOS ANGELES CA 90043 | 90043 | 6018 Brynhurst Ave | 4006-005-007 | costar | — |
| 56 | `c5cb2472-ac4b-4618-8327-8e5b323b867c` | — | 3455 WEST SLAUSON AVENUE LOS ANGELES CA 90043 | 90043 | 3455 W Slauson Ave | 5006-003-025 | costar | — |
| 57 | `c8dcd0ff-dbb6-41e2-9a8d-a746ad37ae24` | Hudson | 640 SOUTH ST ANDREWS PLACE LOS ANGELES CA 90005 | 90005 | 640 S St Andrews Pl | 5503-032-010, 5503-032-011 | costar | — |
| 58 | `ce115fcd-b237-4a62-a949-64931897edc0` | — | 4670 BEVERLY BOULEVARD LOS ANGELES CA 90004 | 90004 | 4670 Beverly Blvd | 5516-027-022 | costar | — |
| 59 | `d17f26f2-eeb8-4a11-91ef-b7bd2bbfdfb6` | Steps on St. Andrews | 1808 SOUTH ST ANDREWS PLACE LOS ANGELES CA 90019 | 90019 | 1808 S St Andrews Pl | 5073-014-900 | costar | — |
| 60 | `d1c5c3f7-b8b2-42ba-a61b-c4b6ad9fa1f9` | VA Buildings 156, 157, 158, and ne… | 158 PATTON AVENUE LOS ANGELES CA 90049 | 90049 | 158 Patton Ave | — | pipedream | 24.00197 |
| 61 | `d6145224-a1a1-4e0f-8a01-95b775de6f6b` | Alveare Supportive Housing | 1465 SOUTH BROADWAY LOS ANGELES CA 90015 | 90015 | 1465 S Broadway | — | costar | — |
| 62 | `db559007-8628-462f-bb08-b10cec69e768` | 10608 West Pico Blvd | 10608 WEST PICO BOULEVARD LOS ANGELES CA 90064 | 90064 | 10608 West Pico Blvd; 10608 W Pico Blvd | 4318-003-010 | pipedream,costar | 24.00223 |
| 63 | `dbda14ce-4d34-4b70-9500-4b83e21d70b6` | Haven on Amherst | 2245 AMHERST AVENUE LOS ANGELES CA 90064 | 90064 | 2245 Amherst Ave | 4259-030-017 | costar | — |
| 64 | `dc5ea392-f412-4292-a395-bf57bd85a811` | 2121 Westwood | 2121 SOUTH WESTWOOD BOULEVARD LOS ANGELES CA 90024 | 90024 | 2121 S Westwood Blvd | — | pipedream | 24.00188 |
| 65 | `dfc48bef-5931-442e-b664-2ebc516901d1` | Crenshaw Crossing | 3606 WEST EXPOSITIONS BOULEVARD LOS ANGELES CA 90016 | 90016 | 3606 W Expositions Blvd | 5044-002-901, 5046-022-900 | costar | — |
| 66 | `e1a06b1f-d575-461d-9949-33e0a7bdebaa` | Burlington Place | 261 SOUTH BURLINGTON AVENUE LOS ANGELES CA 90057 | 90057 | 261 S Burlington Ave | — | pipedream | 23.00076 |
| 67 | `e26d54af-7c12-4218-9168-87499efbb6ab` | U.S. VETS – WLAVA Building 210 | 790 BONSALL AVENUE LOS ANGELES CA 90049 | 90049 | 790 Bonsall Ave | 4365-008-906 | costar | — |
| 68 | `efafad9b-7807-4539-bbce-624f67984180` | Peak Plaza Apartments | 316 EAST WASHINGTON BOULEVARD LOS ANGELES CA 90015 | 90015 | 316 E Washington Blvd | 5127-029-042 | costar | — |
| 69 | `f9f51dc0-7247-4b02-86f0-19dc2d26603c` | Restorative Care Village | 1321-1381 NORTH MISSION ROAD LOS ANGELES CA 90033 | 90033 | 1321-1381 N Mission Rd | 5210-015-902, 5210-015-906 | costar | — |
| 70 | `fab731c6-2d1f-46c0-8c9b-bd4946e570cc` | 10505 Washington Blvd | 10505 WASHINGTON BOULEVARD LOS ANGELES CA 90232 | 90232 | 10505 Washington Boulevard | — | pipedream | 24.00281 |
| 71 | `fcacb85d-6ed4-4f4c-8b5b-ed44f8b16b82` | SoLa Vermont | 11001 SOUTH VERMONT AVENUE LOS ANGELES CA 90044 | 90044 | 11001 S Vermont Ave | 6076-013-028 | costar | — |
## 4. Stratified Sample (15)

Final sample after redistribution rule (section 2.3): A=3, B=0, C=12, D=0. Sorted within each bucket by `project_id` ascending; picked at evenly-spaced indices.

| # | heuristic | project_id | project_name | canonical_address | zip |
|---|-----------|-----------|--------------|-------------------|-----|
| 1 | A | `1ca77896-5a90-4126-83b4-635451312ffc` | East End Studios – Mission Campus | 2233-2251 JESSE STREET LOS ANGELES CA 90021 | 90021 |
| 2 | A | `b69ae629-a258-4ddd-94c1-c44017622475` | Cursonair | 5566-5566 WEST PICO BOULEVARD LOS ANGELES CA 90019 | 90019 |
| 3 | A | `f9f51dc0-7247-4b02-86f0-19dc2d26603c` | Restorative Care Village | 1321-1381 NORTH MISSION ROAD LOS ANGELES CA 90033 | 90033 |
| 4 | C | `07fa917c-3e50-4a96-b1c8-0f5be90cdfc8` | The Clark on 54th | 5353 CRENSHAW BOULEVARD LOS ANGELES CA 90043 | 90043 |
| 5 | C | `18c3a172-c64e-4078-9697-a50d4c003998` | Step Apartments | 2735 EAST 6TH STREET LOS ANGELES CA 90023 | 90023 |
| 6 | C | `2b70f723-94a9-4254-b14d-25673e0d61ae` | 329 S Bonnie Brae St | 329 SOUTH BONNIE BRAE STREET LOS ANGELES CA 90057 | 90057 |
| 7 | C | `4b33492d-2d8e-4465-92dc-1514c09a25b3` | 668 Coronado | 668 SOUTH CORONADO STREET LOS ANGELES CA 90057 | 90057 |
| 8 | C | `6a70fb79-7888-49af-8e81-7e0a543e9588` | 1000 N Alfred Street | 1000 NORTH ALFRED STREET LOS ANGELES CA 90069 | 90069 |
| 9 | C | `7ea4d763-58e9-4ade-b7b9-06a04a810f23` | --- | 1734 SOUTH BARRINGTON AVENUE LOS ANGELES CA 90025 | 90025 |
| 10 | C | `962288b0-2605-4440-bb66-7be13e442c2a` | Metro at Florence | 1642 EAST FLORENCE AVENUE LOS ANGELES CA 90001 | 90001 |
| 11 | C | `a7bac194-bf85-4805-85b9-fbf535f40417` | --- | 502 NORTH OXFORD AVENUE LOS ANGELES CA 90004 | 90004 |
| 12 | C | `b4562a75-9f64-49d9-b622-4617e71f59e9` | Orion 1408 Jefferson | 1408 WEST JEFFERSON BOULEVARD LOS ANGELES CA 90007 | 90007 |
| 13 | C | `c8dcd0ff-dbb6-41e2-9a8d-a746ad37ae24` | Hudson | 640 SOUTH ST ANDREWS PLACE LOS ANGELES CA 90005 | 90005 |
| 14 | C | `db559007-8628-462f-bb08-b10cec69e768` | 10608 West Pico Blvd | 10608 WEST PICO BOULEVARD LOS ANGELES CA 90064 | 90064 |
| 15 | C | `efafad9b-7807-4539-bbce-624f67984180` | Peak Plaza Apartments | 316 EAST WASHINGTON BOULEVARD LOS ANGELES CA 90015 | 90015 |

## 5. Per-Project Findings

### 5.1  `1ca77896` - East End Studios – Mission Campus - `2233-2251 JESSE STREET LOS ANGELES CA 90021`

- **Heuristic bucket**: A
- **Seeded identifiers**: APN=['5171-016-010']; pipedream_id=---; costar_property_id=['19851259']; case_number=---
- **project_source_records**: costar `19851259`
- **raw_addresses**: ['2233-2251 Jesse St']
- **Jurisdiction check**: OK (City of LA ZIP)
- **Stale-CofO check**: no - 3f9m rows at the same address_start exist but none on the same street_name (cross-street coincidences)

**LADBS deep-check queries issued:**

- hbkd-qubn / cpkv-aajs / 3f9m-afei by `(zip, address_start)`: `GET https://data.lacity.org/resource/{hbkd-qubn,cpkv-aajs,3f9m-afei}.json?$where=zip_code='90021' AND address_start in('2233-2251')` (with range expansion for Bucket A projects)
- cpkv-aajs and 3f9m-afei APN lookup: `$where=assessor_book='5171' AND assessor_page='016' AND assessor_parcel='010'`
- street_name fuzzy (within-ZIP): rows where `zip_code='90021' AND upper(street_name) LIKE '%JESSE%'`
- DCP best-effort: WebSearch for project-name and canonical-address keywords; manual-check URL if nothing surfaces: `https://planning.lacity.gov/development-services` (address search) and ZIMAS deep-link `https://zimas.lacity.org/?address=2233-2251+JESSE+STREET+LOS+ANGELES+CA+90021`

**Key deep-check findings:**

- hbkd-qubn rows in ZIP 90021 with matching street_name token: 10.
- cpkv-aajs rows in ZIP 90021 with matching street_name token: 0.
- 3f9m-afei rows in ZIP 90021 with matching street_name token: 0.
- cpkv APN match: 0 APN(s) returned rows. 
- 3f9m APN match: 0 APN(s) returned rows.

**Triage bucket**: `matcher-fixable`

**One-sentence recommendation**: Real $230M East End Studios studio complex at 2233-2251 E Jesse Street (Boyle Heights, 90021). hbkd has 19 rows at address_start in the 2233-2251 range but all on 2251 E WASHINGTON BLVD - the same lot/intersection. The seed's raw_addresses is missing the Washington frontage. A project-name-as-address lookup, or a seed-side alternate-address enrichment for corner-lot multi-frontage sites, would have caught it.

---

### 5.2  `b69ae629` - Cursonair - `5566-5566 WEST PICO BOULEVARD LOS ANGELES CA 90019`

- **Heuristic bucket**: A
- **Seeded identifiers**: APN=['5069-019-002']; pipedream_id=---; costar_property_id=['10589494']; case_number=---
- **project_source_records**: costar `10589494`
- **raw_addresses**: ['5566-5566 W Pico Blvd']
- **Jurisdiction check**: OK (City of LA ZIP)
- **Stale-CofO check**: no - no 3f9m-afei row at the exact (zip, address_start)

**LADBS deep-check queries issued:**

- hbkd-qubn / cpkv-aajs / 3f9m-afei by `(zip, address_start)`: `GET https://data.lacity.org/resource/{hbkd-qubn,cpkv-aajs,3f9m-afei}.json?$where=zip_code='90019' AND address_start in('5566-5566')` (with range expansion for Bucket A projects)
- cpkv-aajs and 3f9m-afei APN lookup: `$where=assessor_book='5069' AND assessor_page='019' AND assessor_parcel='002'`
- street_name fuzzy (within-ZIP): rows where `zip_code='90019' AND upper(street_name) LIKE '%PICO%'`
- DCP best-effort: WebSearch for project-name and canonical-address keywords; manual-check URL if nothing surfaces: `https://planning.lacity.gov/development-services` (address search) and ZIMAS deep-link `https://zimas.lacity.org/?address=5566-5566+WEST+PICO+BOULEVARD+LOS+ANGELES+CA+90019`

**Key deep-check findings:**

- hbkd-qubn rows in ZIP 90019 with matching street_name token: 50.
- cpkv-aajs rows in ZIP 90019 with matching street_name token: 6.
- 3f9m-afei rows in ZIP 90019 with matching street_name token: 29.
- cpkv APN match: 0 APN(s) returned rows. 
- 3f9m APN match: 0 APN(s) returned rows.

**Triage bucket**: `genuine-ladbs-gap`

**One-sentence recommendation**: Real 7-story APPA Real Estate mixed-use project at 5566 W Pico Blvd, confirmed 'pushing dirt' per Urbanize LA in May 2025. No hbkd / cpkv-aajs / 3f9m-afei row at 5566 W Pico. Nearest cpkv Bldg-New is 5550 W Pico (a different, 2022-completed building). Most likely the Bldg-New structural permit has not yet been issued or has not yet landed in the LADBS public Socrata snapshot; the project may be operating under earlier-lifecycle permits (demolition, grading, excavation) absent from the three-source bundle queried here.

---

### 5.3  `f9f51dc0` - Restorative Care Village - `1321-1381 NORTH MISSION ROAD LOS ANGELES CA 90033`

- **Heuristic bucket**: A
- **Seeded identifiers**: APN=['5210-015-902', '5210-015-906']; pipedream_id=---; costar_property_id=['21065910']; case_number=---
- **project_source_records**: costar `21065910`
- **raw_addresses**: ['1321-1381 N Mission Rd']
- **Jurisdiction check**: OK (City of LA ZIP)
- **Stale-CofO check**: no - no 3f9m-afei row at the exact (zip, address_start)

**LADBS deep-check queries issued:**

- hbkd-qubn / cpkv-aajs / 3f9m-afei by `(zip, address_start)`: `GET https://data.lacity.org/resource/{hbkd-qubn,cpkv-aajs,3f9m-afei}.json?$where=zip_code='90033' AND address_start in('1321-1381')` (with range expansion for Bucket A projects)
- cpkv-aajs and 3f9m-afei APN lookup: `$where=assessor_book='5210' AND assessor_page='015' AND assessor_parcel='902'`
- cpkv-aajs and 3f9m-afei APN lookup: `$where=assessor_book='5210' AND assessor_page='015' AND assessor_parcel='906'`
- street_name fuzzy (within-ZIP): rows where `zip_code='90033' AND upper(street_name) LIKE '%MISSION%'`
- DCP best-effort: WebSearch for project-name and canonical-address keywords; manual-check URL if nothing surfaces: `https://planning.lacity.gov/development-services` (address search) and ZIMAS deep-link `https://zimas.lacity.org/?address=1321-1381+NORTH+MISSION+ROAD+LOS+ANGELES+CA+90033`

**Key deep-check findings:**

- hbkd-qubn rows in ZIP 90033 with matching street_name token: 50.
- cpkv-aajs rows in ZIP 90033 with matching street_name token: 0.
- 3f9m-afei rows in ZIP 90033 with matching street_name token: 6.
- cpkv APN match: 0 APN(s) returned rows. 
- 3f9m APN match: 0 APN(s) returned rows.

**Triage bucket**: `seed-stale`

**One-sentence recommendation**: LAC+USC Medical Center Restorative Care Village at 1321-1381 N Mission Rd. CannonDesign and Urbanize LA both report Phase 1 completed July 2022; project-name search surfaces no ongoing Phase 2 at this address. The seeded pipeline_status='Under Construction' is stale. Separately, the 1321-1381 Mission range corresponds to internal campus streets (MONO, VANEGAS, GABRIEL GARCIA MARQUEZ, ZONAL) where hbkd does have permits under those street names on the LAC+USC campus - but the primary issue is the stale status, not the address-level mismatch.

---

### 5.4  `07fa917c` - The Clark on 54th - `5353 CRENSHAW BOULEVARD LOS ANGELES CA 90043`

- **Heuristic bucket**: C
- **Seeded identifiers**: APN=['5006-006-007']; pipedream_id=---; costar_property_id=['21460107']; case_number=---
- **project_source_records**: costar `21460107`
- **raw_addresses**: ['5353 Crenshaw Blvd']
- **Jurisdiction check**: OK (City of LA ZIP)
- **Stale-CofO check**: no - no 3f9m-afei row at the exact (zip, address_start)

**LADBS deep-check queries issued:**

- hbkd-qubn / cpkv-aajs / 3f9m-afei by `(zip, address_start)`: `GET https://data.lacity.org/resource/{hbkd-qubn,cpkv-aajs,3f9m-afei}.json?$where=zip_code='90043' AND address_start in('5353')` (with range expansion for Bucket A projects)
- cpkv-aajs and 3f9m-afei APN lookup: `$where=assessor_book='5006' AND assessor_page='006' AND assessor_parcel='007'`
- street_name fuzzy (within-ZIP): rows where `zip_code='90043' AND upper(street_name) LIKE '%CRENSHAW%'`
- DCP best-effort: WebSearch for project-name and canonical-address keywords; manual-check URL if nothing surfaces: `https://planning.lacity.gov/development-services` (address search) and ZIMAS deep-link `https://zimas.lacity.org/?address=5353+CRENSHAW+BOULEVARD+LOS+ANGELES+CA+90043`

**Key deep-check findings:**

- hbkd-qubn rows in ZIP 90043 with matching street_name token: 50.
- cpkv-aajs rows in ZIP 90043 with matching street_name token: 10.
- 3f9m-afei rows in ZIP 90043 with matching street_name token: 14.
- cpkv APN match: 0 APN(s) returned rows. 
- 3f9m APN match: 0 APN(s) returned rows.

**Triage bucket**: `matcher-fixable`

**One-sentence recommendation**: The Clark on 54th: Urbanize LA, LISC LA, Bisnow, and Yield PRO all give the actual address as 5365 S Crenshaw Blvd, not 5353. Broke ground December 2024, opens Spring 2026 - UC status is correct but the seeded leading address number is wrong. A project-name-as-address lookup ('The Clark on 54th' -> 5365 S Crenshaw) would have caught it; alternatively a seed-side address correction.

---

### 5.5  `18c3a172` - Step Apartments - `2735 EAST 6TH STREET LOS ANGELES CA 90023`

- **Heuristic bucket**: C
- **Seeded identifiers**: APN=['5185-017-033']; pipedream_id=---; costar_property_id=['21913692']; case_number=---
- **project_source_records**: costar `21913692`
- **raw_addresses**: ['2735 E 6th St']
- **Jurisdiction check**: OK (City of LA ZIP)
- **Stale-CofO check**: no - no 3f9m-afei row at the exact (zip, address_start)

**LADBS deep-check queries issued:**

- hbkd-qubn / cpkv-aajs / 3f9m-afei by `(zip, address_start)`: `GET https://data.lacity.org/resource/{hbkd-qubn,cpkv-aajs,3f9m-afei}.json?$where=zip_code='90023' AND address_start in('2735')` (with range expansion for Bucket A projects)
- cpkv-aajs and 3f9m-afei APN lookup: `$where=assessor_book='5185' AND assessor_page='017' AND assessor_parcel='033'`
- street_name fuzzy (within-ZIP): rows where `zip_code='90023' AND upper(street_name) LIKE '%6TH%'`
- DCP best-effort: WebSearch for project-name and canonical-address keywords; manual-check URL if nothing surfaces: `https://planning.lacity.gov/development-services` (address search) and ZIMAS deep-link `https://zimas.lacity.org/?address=2735+EAST+6TH+STREET+LOS+ANGELES+CA+90023`

**Key deep-check findings:**

- hbkd-qubn rows in ZIP 90023 with matching street_name token: 50.
- cpkv-aajs rows in ZIP 90023 with matching street_name token: 3.
- 3f9m-afei rows in ZIP 90023 with matching street_name token: 4.
- cpkv APN match: 0 APN(s) returned rows. 
- 3f9m APN match: 0 APN(s) returned rows.

**Triage bucket**: `cant-tell`

**One-sentence recommendation**: No hbkd/cpkv/3f9m permit at 2735 E 6th St. Closest cpkv Bldg-News are 2831/2833 E 6th (adjacent block, different parcel). Without manual research (skipped to stay within 10-minute-per-project budget) cannot determine whether LADBS has the row under a different number or the project is pre-permit.

---

### 5.6  `2b70f723` - 329 S Bonnie Brae St - `329 SOUTH BONNIE BRAE STREET LOS ANGELES CA 90057`

- **Heuristic bucket**: C
- **Seeded identifiers**: APN=['5154-027-008']; pipedream_id=['23.00075']; costar_property_id=['10953019']; case_number=---
- **project_source_records**: pipedream `23.00075`, costar `10953019`
- **raw_addresses**: ['329 S Bonnie Brae St']
- **Jurisdiction check**: OK (City of LA ZIP)
- **Stale-CofO check**: no - no 3f9m-afei row at the exact (zip, address_start)

**LADBS deep-check queries issued:**

- hbkd-qubn / cpkv-aajs / 3f9m-afei by `(zip, address_start)`: `GET https://data.lacity.org/resource/{hbkd-qubn,cpkv-aajs,3f9m-afei}.json?$where=zip_code='90057' AND address_start in('329')` (with range expansion for Bucket A projects)
- cpkv-aajs and 3f9m-afei APN lookup: `$where=assessor_book='5154' AND assessor_page='027' AND assessor_parcel='008'`
- street_name fuzzy (within-ZIP): rows where `zip_code='90057' AND upper(street_name) LIKE '%BONNIE BRAE%'`
- DCP best-effort: WebSearch for project-name and canonical-address keywords; manual-check URL if nothing surfaces: `https://planning.lacity.gov/development-services` (address search) and ZIMAS deep-link `https://zimas.lacity.org/?address=329+SOUTH+BONNIE+BRAE+STREET+LOS+ANGELES+CA+90057`

**Key deep-check findings:**

- hbkd-qubn rows in ZIP 90057 with matching street_name token: 50.
- cpkv-aajs rows in ZIP 90057 with matching street_name token: 4.
- 3f9m-afei rows in ZIP 90057 with matching street_name token: 3.
- cpkv APN match: 0 APN(s) returned rows. 
- 3f9m APN match: 0 APN(s) returned rows.

**Triage bucket**: `genuine-ladbs-gap`

**One-sentence recommendation**: No hbkd / cpkv / 3f9m row at 329 S Bonnie Brae St. Closest cpkv Bldg-New is 273 S Bonnie Brae (56 off). APN 5154-027-008 query returned 0 rows in cpkv and 3f9m. pipedream-seeded (23.00075) and costar-cross-referenced, so the address is almost certainly correct; LADBS simply has no new-construction row here.

---

### 5.7  `4b33492d` - 668 Coronado - `668 SOUTH CORONADO STREET LOS ANGELES CA 90057`

- **Heuristic bucket**: C
- **Seeded identifiers**: APN=['5141-007-016']; pipedream_id=['23.00082']; costar_property_id=['10118045']; case_number=---
- **project_source_records**: pipedream `23.00082`, costar `10118045`
- **raw_addresses**: ['668 S Coronado St']
- **Jurisdiction check**: OK (City of LA ZIP)
- **Stale-CofO check**: no - no 3f9m-afei row at the exact (zip, address_start)

**LADBS deep-check queries issued:**

- hbkd-qubn / cpkv-aajs / 3f9m-afei by `(zip, address_start)`: `GET https://data.lacity.org/resource/{hbkd-qubn,cpkv-aajs,3f9m-afei}.json?$where=zip_code='90057' AND address_start in('668')` (with range expansion for Bucket A projects)
- cpkv-aajs and 3f9m-afei APN lookup: `$where=assessor_book='5141' AND assessor_page='007' AND assessor_parcel='016'`
- street_name fuzzy (within-ZIP): rows where `zip_code='90057' AND upper(street_name) LIKE '%CORONADO%'`
- DCP best-effort: WebSearch for project-name and canonical-address keywords; manual-check URL if nothing surfaces: `https://planning.lacity.gov/development-services` (address search) and ZIMAS deep-link `https://zimas.lacity.org/?address=668+SOUTH+CORONADO+STREET+LOS+ANGELES+CA+90057`

**Key deep-check findings:**

- hbkd-qubn rows in ZIP 90057 with matching street_name token: 50.
- cpkv-aajs rows in ZIP 90057 with matching street_name token: 5.
- 3f9m-afei rows in ZIP 90057 with matching street_name token: 6.
- cpkv APN match: 0 APN(s) returned rows. 
- 3f9m APN match: 0 APN(s) returned rows.

**Triage bucket**: `genuine-ladbs-gap`

**One-sentence recommendation**: No hbkd / cpkv / 3f9m row at 668 S Coronado St. hbkd has many permits at 671 Coronado (odd-side - different parcel) but 668 has nothing. APN 5141-007-016 returned 0 rows in cpkv/3f9m. pipedream-seeded (23.00082) so address is likely correct.

---

### 5.8  `6a70fb79` - 1000 N Alfred Street - `1000 NORTH ALFRED STREET LOS ANGELES CA 90069`

- **Heuristic bucket**: C
- **Seeded identifiers**: APN=---; pipedream_id=['24.00274']; costar_property_id=---; case_number=---
- **project_source_records**: pipedream `24.00274`
- **raw_addresses**: ['1000 N Alfred Street']
- **Jurisdiction check**: OK (City of LA ZIP)
- **Stale-CofO check**: no - no 3f9m-afei row at the exact (zip, address_start)

**LADBS deep-check queries issued:**

- hbkd-qubn / cpkv-aajs / 3f9m-afei by `(zip, address_start)`: `GET https://data.lacity.org/resource/{hbkd-qubn,cpkv-aajs,3f9m-afei}.json?$where=zip_code='90069' AND address_start in('1000')` (with range expansion for Bucket A projects)
- street_name fuzzy (within-ZIP): rows where `zip_code='90069' AND upper(street_name) LIKE '%ALFRED%'`
- DCP best-effort: WebSearch for project-name and canonical-address keywords; manual-check URL if nothing surfaces: `https://planning.lacity.gov/development-services` (address search) and ZIMAS deep-link `https://zimas.lacity.org/?address=1000+NORTH+ALFRED+STREET+LOS+ANGELES+CA+90069`

**Key deep-check findings:**

- hbkd-qubn rows in ZIP 90069 with matching street_name token: 50.
- cpkv-aajs rows in ZIP 90069 with matching street_name token: 3.
- 3f9m-afei rows in ZIP 90069 with matching street_name token: 3.
- cpkv APN match: 0 APN(s) returned rows. 
- 3f9m APN match: 0 APN(s) returned rows.

**Triage bucket**: `genuine-ladbs-gap`

**One-sentence recommendation**: No hbkd / cpkv / 3f9m row at 1000 N Alfred St. Closest cpkv Bldg-News are 932 (different parcel), 755, 715 - all materially off. No APN seeded; pipedream-seeded only (24.00274), recent entry.

---

### 5.9  `7ea4d763` - (no project_name) - `1734 SOUTH BARRINGTON AVENUE LOS ANGELES CA 90025`

- **Heuristic bucket**: C
- **Seeded identifiers**: APN=['4262-018-026']; pipedream_id=---; costar_property_id=['21575798']; case_number=---
- **project_source_records**: costar `21575798`
- **raw_addresses**: ['1734 S Barrington Ave']
- **Jurisdiction check**: OK (City of LA ZIP)
- **Stale-CofO check**: no - no 3f9m-afei row at the exact (zip, address_start)

**LADBS deep-check queries issued:**

- hbkd-qubn / cpkv-aajs / 3f9m-afei by `(zip, address_start)`: `GET https://data.lacity.org/resource/{hbkd-qubn,cpkv-aajs,3f9m-afei}.json?$where=zip_code='90025' AND address_start in('1734')` (with range expansion for Bucket A projects)
- cpkv-aajs and 3f9m-afei APN lookup: `$where=assessor_book='4262' AND assessor_page='018' AND assessor_parcel='026'`
- street_name fuzzy (within-ZIP): rows where `zip_code='90025' AND upper(street_name) LIKE '%BARRINGTON%'`
- DCP best-effort: WebSearch for project-name and canonical-address keywords; manual-check URL if nothing surfaces: `https://planning.lacity.gov/development-services` (address search) and ZIMAS deep-link `https://zimas.lacity.org/?address=1734+SOUTH+BARRINGTON+AVENUE+LOS+ANGELES+CA+90025`

**Key deep-check findings:**

- hbkd-qubn rows in ZIP 90025 with matching street_name token: 50.
- cpkv-aajs rows in ZIP 90025 with matching street_name token: 8.
- 3f9m-afei rows in ZIP 90025 with matching street_name token: 9.
- cpkv APN match: 0 APN(s) returned rows. 
- 3f9m APN match: 0 APN(s) returned rows.

**Triage bucket**: `cant-tell`

**One-sentence recommendation**: hbkd has 1733 S Barrington (1 number off) with Bldg-Alter permits; cpkv has 1729 Bldg-New (5 off); 3f9m has 1731 Bldg-New (3 off). All are on the odd side; seeded 1734 is even. Could be a combined-parcel indexed under odd-side addresses, or a distinct even-side lot. APN 4262-018-026 returned 0 rows. Needs parcel-level inspection to resolve.

---

### 5.10  `962288b0` - Metro at Florence - `1642 EAST FLORENCE AVENUE LOS ANGELES CA 90001`

- **Heuristic bucket**: C
- **Seeded identifiers**: APN=['6021-019-013']; pipedream_id=---; costar_property_id=['19262930']; case_number=---
- **project_source_records**: costar `19262930`
- **raw_addresses**: ['1642 E Florence Ave']
- **Jurisdiction check**: OK (City of LA ZIP)
- **Stale-CofO check**: no - no 3f9m-afei row at the exact (zip, address_start)

**LADBS deep-check queries issued:**

- hbkd-qubn / cpkv-aajs / 3f9m-afei by `(zip, address_start)`: `GET https://data.lacity.org/resource/{hbkd-qubn,cpkv-aajs,3f9m-afei}.json?$where=zip_code='90001' AND address_start in('1642')` (with range expansion for Bucket A projects)
- cpkv-aajs and 3f9m-afei APN lookup: `$where=assessor_book='6021' AND assessor_page='019' AND assessor_parcel='013'`
- street_name fuzzy (within-ZIP): rows where `zip_code='90001' AND upper(street_name) LIKE '%FLORENCE%'`
- DCP best-effort: WebSearch for project-name and canonical-address keywords; manual-check URL if nothing surfaces: `https://planning.lacity.gov/development-services` (address search) and ZIMAS deep-link `https://zimas.lacity.org/?address=1642+EAST+FLORENCE+AVENUE+LOS+ANGELES+CA+90001`

**Key deep-check findings:**

- hbkd-qubn rows in ZIP 90001 with matching street_name token: 50.
- cpkv-aajs rows in ZIP 90001 with matching street_name token: 1.
- 3f9m-afei rows in ZIP 90001 with matching street_name token: 2.
- cpkv APN match: 0 APN(s) returned rows. 
- 3f9m APN match: 0 APN(s) returned rows.

**Triage bucket**: `genuine-ladbs-gap`

**One-sentence recommendation**: Metro at Florence - confirmed as a leased apartment complex at 1642 E Florence Ave 90001 per Rent.com/ApartmentGuide. hbkd has no FLORENCE permit anywhere near 1642 (nearest is 1020, 622 off). cpkv only has 860. The targeted address_start IN (1642) pull returned 0 rows across all three datasets, so this is not a ZIP-cap artifact. APN 6021-019-013 returned 0 rows.

---

### 5.11  `a7bac194` - (no project_name) - `502 NORTH OXFORD AVENUE LOS ANGELES CA 90004`

- **Heuristic bucket**: C
- **Seeded identifiers**: APN=['5521-013-012']; pipedream_id=---; costar_property_id=['12617413']; case_number=---
- **project_source_records**: costar `12617413`
- **raw_addresses**: ['502 N Oxford Ave']
- **Jurisdiction check**: OK (City of LA ZIP)
- **Stale-CofO check**: no - no 3f9m-afei row at the exact (zip, address_start)

**LADBS deep-check queries issued:**

- hbkd-qubn / cpkv-aajs / 3f9m-afei by `(zip, address_start)`: `GET https://data.lacity.org/resource/{hbkd-qubn,cpkv-aajs,3f9m-afei}.json?$where=zip_code='90004' AND address_start in('502')` (with range expansion for Bucket A projects)
- cpkv-aajs and 3f9m-afei APN lookup: `$where=assessor_book='5521' AND assessor_page='013' AND assessor_parcel='012'`
- street_name fuzzy (within-ZIP): rows where `zip_code='90004' AND upper(street_name) LIKE '%OXFORD%'`
- DCP best-effort: WebSearch for project-name and canonical-address keywords; manual-check URL if nothing surfaces: `https://planning.lacity.gov/development-services` (address search) and ZIMAS deep-link `https://zimas.lacity.org/?address=502+NORTH+OXFORD+AVENUE+LOS+ANGELES+CA+90004`

**Key deep-check findings:**

- hbkd-qubn rows in ZIP 90004 with matching street_name token: 50.
- cpkv-aajs rows in ZIP 90004 with matching street_name token: 15.
- 3f9m-afei rows in ZIP 90004 with matching street_name token: 5.
- cpkv APN match: 1 APN(s) returned rows. (see finding)
- 3f9m APN match: 0 APN(s) returned rows.

**Triage bucket**: `cant-tell`

**One-sentence recommendation**: Seeded APN 5521-013-012 for 502 N Oxford Ave actually points to a Bldg-New triplex at 4711-4715 W MAPLEWOOD AVE in cpkv - completely different site. The seeded APN is wrong for this project. cpkv does have a Bldg-Alter at 503 N Oxford (odd-side neighboring parcel) and Bldg-News at 474/476 N Oxford (26-28 off). Without the correct APN or a permit number, cannot definitively tell whether this is a pre-permit active project or a seeding error. A seed-data pass that validates APNs against parcel addresses would disambiguate.

---

### 5.12  `b4562a75` - Orion 1408 Jefferson - `1408 WEST JEFFERSON BOULEVARD LOS ANGELES CA 90007`

- **Heuristic bucket**: C
- **Seeded identifiers**: APN=['5040-021-006']; pipedream_id=---; costar_property_id=['21661165']; case_number=---
- **project_source_records**: costar `21661165`
- **raw_addresses**: ['1408 W Jefferson Blvd']
- **Jurisdiction check**: OK (City of LA ZIP)
- **Stale-CofO check**: no - no 3f9m-afei row at the exact (zip, address_start)

**LADBS deep-check queries issued:**

- hbkd-qubn / cpkv-aajs / 3f9m-afei by `(zip, address_start)`: `GET https://data.lacity.org/resource/{hbkd-qubn,cpkv-aajs,3f9m-afei}.json?$where=zip_code='90007' AND address_start in('1408')` (with range expansion for Bucket A projects)
- cpkv-aajs and 3f9m-afei APN lookup: `$where=assessor_book='5040' AND assessor_page='021' AND assessor_parcel='006'`
- street_name fuzzy (within-ZIP): rows where `zip_code='90007' AND upper(street_name) LIKE '%JEFFERSON%'`
- DCP best-effort: WebSearch for project-name and canonical-address keywords; manual-check URL if nothing surfaces: `https://planning.lacity.gov/development-services` (address search) and ZIMAS deep-link `https://zimas.lacity.org/?address=1408+WEST+JEFFERSON+BOULEVARD+LOS+ANGELES+CA+90007`

**Key deep-check findings:**

- hbkd-qubn rows in ZIP 90007 with matching street_name token: 50.
- cpkv-aajs rows in ZIP 90007 with matching street_name token: 6.
- 3f9m-afei rows in ZIP 90007 with matching street_name token: 12.
- cpkv APN match: 0 APN(s) returned rows. 
- 3f9m APN match: 0 APN(s) returned rows.

**Triage bucket**: `genuine-ladbs-gap`

**One-sentence recommendation**: Orion 1408 W Jefferson Blvd 90007: no hbkd / cpkv / 3f9m row at 1408. Nearest cpkv Bldg-News are 1320, 1710, 1714, 1716 (all materially off). APN 5040-021-006 returned 0 rows in cpkv/3f9m.

---

### 5.13  `c8dcd0ff` - Hudson - `640 SOUTH ST ANDREWS PLACE LOS ANGELES CA 90005`

- **Heuristic bucket**: C
- **Seeded identifiers**: APN=['5503-032-010', '5503-032-011']; pipedream_id=---; costar_property_id=['10888161']; case_number=---
- **project_source_records**: costar `10888161`
- **raw_addresses**: ['640 S St Andrews Pl']
- **Jurisdiction check**: OK (City of LA ZIP)
- **Stale-CofO check**: no - no 3f9m-afei row at the exact (zip, address_start)

**LADBS deep-check queries issued:**

- hbkd-qubn / cpkv-aajs / 3f9m-afei by `(zip, address_start)`: `GET https://data.lacity.org/resource/{hbkd-qubn,cpkv-aajs,3f9m-afei}.json?$where=zip_code='90005' AND address_start in('640')` (with range expansion for Bucket A projects)
- cpkv-aajs and 3f9m-afei APN lookup: `$where=assessor_book='5503' AND assessor_page='032' AND assessor_parcel='010'`
- cpkv-aajs and 3f9m-afei APN lookup: `$where=assessor_book='5503' AND assessor_page='032' AND assessor_parcel='011'`
- street_name fuzzy (within-ZIP): rows where `zip_code='90005' AND upper(street_name) LIKE '%ST ANDREWS%'`
- DCP best-effort: WebSearch for project-name and canonical-address keywords; manual-check URL if nothing surfaces: `https://planning.lacity.gov/development-services` (address search) and ZIMAS deep-link `https://zimas.lacity.org/?address=640+SOUTH+ST+ANDREWS+PLACE+LOS+ANGELES+CA+90005`

**Key deep-check findings:**

- hbkd-qubn rows in ZIP 90005 with matching street_name token: 50.
- cpkv-aajs rows in ZIP 90005 with matching street_name token: 3.
- 3f9m-afei rows in ZIP 90005 with matching street_name token: 1.
- cpkv APN match: 1 APN(s) returned rows. (see finding)
- 3f9m APN match: 0 APN(s) returned rows.

**Triage bucket**: `matcher-fixable`

**One-sentence recommendation**: Hudson at 640 S St Andrews Pl: cpkv-aajs has the exact project - an 8-story, 230-unit Bldg-New apartment issued 2022-05-20, pcis_permit 19010-10000-02374 - but indexed under ZIP 90010, while the seed has ZIP 90005. Seeded APN 5503-032-010 matches the cpkv row's assessor_book/assessor_page/assessor_parcel exactly. APN-first matching, or ZIP-flexible canonical-address comparison, would have caught it. Alternatively a seed ZIP correction. Note: this matches the cpkv-aajs-only recovery example in the hbkd+cpkv audit's 'Examples of cpkv-aajs-only recoveries' list for Hudson at 640 S ST ANDREWS PLACE LOS ANGELES CA 90005 - the cpkv row exists but is not flowing into the live matcher path for this project.

---

### 5.14  `db559007` - 10608 West Pico Blvd - `10608 WEST PICO BOULEVARD LOS ANGELES CA 90064`

- **Heuristic bucket**: C
- **Seeded identifiers**: APN=['4318-003-010']; pipedream_id=['24.00223']; costar_property_id=['20810465']; case_number=---
- **project_source_records**: pipedream `24.00223`, costar `20810465`
- **raw_addresses**: ['10608 West Pico Blvd', '10608 W Pico Blvd']
- **Jurisdiction check**: OK (City of LA ZIP)
- **Stale-CofO check**: no - no 3f9m-afei row at the exact (zip, address_start)

**LADBS deep-check queries issued:**

- hbkd-qubn / cpkv-aajs / 3f9m-afei by `(zip, address_start)`: `GET https://data.lacity.org/resource/{hbkd-qubn,cpkv-aajs,3f9m-afei}.json?$where=zip_code='90064' AND address_start in('10608')` (with range expansion for Bucket A projects)
- cpkv-aajs and 3f9m-afei APN lookup: `$where=assessor_book='4318' AND assessor_page='003' AND assessor_parcel='010'`
- street_name fuzzy (within-ZIP): rows where `zip_code='90064' AND upper(street_name) LIKE '%PICO%'`
- DCP best-effort: WebSearch for project-name and canonical-address keywords; manual-check URL if nothing surfaces: `https://planning.lacity.gov/development-services` (address search) and ZIMAS deep-link `https://zimas.lacity.org/?address=10608+WEST+PICO+BOULEVARD+LOS+ANGELES+CA+90064`

**Key deep-check findings:**

- hbkd-qubn rows in ZIP 90064 with matching street_name token: 50.
- cpkv-aajs rows in ZIP 90064 with matching street_name token: 5.
- 3f9m-afei rows in ZIP 90064 with matching street_name token: 33.
- cpkv APN match: 0 APN(s) returned rows. 
- 3f9m APN match: 0 APN(s) returned rows.

**Triage bucket**: `genuine-ladbs-gap`

**One-sentence recommendation**: 10608 W Pico Blvd 90064: no hbkd/cpkv/3f9m row at 10608. hbkd has lots of activity at nearby Pico numbers (10521, 10556, 10592, 10618) but not 10608 specifically. cpkv Bldg-News start at 11588 and up (different block). APN 4318-003-010 returned 0 rows.

---

### 5.15  `efafad9b` - Peak Plaza Apartments - `316 EAST WASHINGTON BOULEVARD LOS ANGELES CA 90015`

- **Heuristic bucket**: C
- **Seeded identifiers**: APN=['5127-029-042']; pipedream_id=---; costar_property_id=['20882713']; case_number=---
- **project_source_records**: costar `20882713`
- **raw_addresses**: ['316 E Washington Blvd']
- **Jurisdiction check**: OK (City of LA ZIP)
- **Stale-CofO check**: no - no 3f9m-afei row at the exact (zip, address_start)

**LADBS deep-check queries issued:**

- hbkd-qubn / cpkv-aajs / 3f9m-afei by `(zip, address_start)`: `GET https://data.lacity.org/resource/{hbkd-qubn,cpkv-aajs,3f9m-afei}.json?$where=zip_code='90015' AND address_start in('316')` (with range expansion for Bucket A projects)
- cpkv-aajs and 3f9m-afei APN lookup: `$where=assessor_book='5127' AND assessor_page='029' AND assessor_parcel='042'`
- street_name fuzzy (within-ZIP): rows where `zip_code='90015' AND upper(street_name) LIKE '%WASHINGTON%'`
- DCP best-effort: WebSearch for project-name and canonical-address keywords; manual-check URL if nothing surfaces: `https://planning.lacity.gov/development-services` (address search) and ZIMAS deep-link `https://zimas.lacity.org/?address=316+EAST+WASHINGTON+BOULEVARD+LOS+ANGELES+CA+90015`

**Key deep-check findings:**

- hbkd-qubn rows in ZIP 90015 with matching street_name token: 50.
- cpkv-aajs rows in ZIP 90015 with matching street_name token: 3.
- 3f9m-afei rows in ZIP 90015 with matching street_name token: 1.
- cpkv APN match: 0 APN(s) returned rows. 
- 3f9m APN match: 0 APN(s) returned rows.

**Triage bucket**: `genuine-ladbs-gap`

**One-sentence recommendation**: Peak Plaza Apartments at 316 E Washington Blvd 90015: no hbkd/cpkv/3f9m row at 316. cpkv has Bldg-Alter at 300 E Washington (16 off, not Bldg-New) and Bldg-New at 200 E Washington (116 off, different block). APN 5127-029-042 returned 0 rows.

---
## 6. Cross-Tab: Heuristic Bucket x Triage Bucket

Rows: heuristic buckets from section 2.3. Columns: triage buckets from section 2.4. Cell values are project counts.

| heuristic bucket | matcher-fixable | seed-stale | wrong-jurisdiction | dcp-covers-it | genuine-ladbs-gap | cant-tell | row total |
|------------------|----|----|----|----|----|----|-----------|
| A | 1 | 1 | 0 | 0 | 1 | 0 | 3 |
| B | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| C | 2 | 0 | 0 | 0 | 7 | 3 | 12 |
| D | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| col total | 3 | 1 | 0 | 0 | 8 | 3 | 15 |

## 7. Extrapolated Sizing

**Caveat**: n=15 from N=71 (or the 75 in the bundle audit). The 95% Wilson confidence interval on a point estimate of p=8/15=53% has a half-width of roughly pm 24pp. Treat all the percentages below as directional shape only, not definitive sizing.

Sample proportions extrapolated to N=75 (the bundle audit denominator):

| triage bucket | sample count (n=15) | sample proportion | extrapolated count at N=75 |
|---------------|---------------------|-------------------|----------------------------|
| `matcher-fixable` | 3 | 20.0% | ~15 |
| `seed-stale` | 1 | 6.7% | ~5 |
| `wrong-jurisdiction` | 0 | 0.0% | ~0 |
| `dcp-covers-it` | 0 | 0.0% | ~0 |
| `genuine-ladbs-gap` | 8 | 53.3% | ~40 |
| `cant-tell` | 3 | 20.0% | ~15 |

**Known supplementary signal not captured in the n=15 sample**: the full 71-project not-found list contains one project (`fab731c6`, `10505 Washington Blvd` in ZIP `90232`) that sits in Culver City, not City of LA. That alone implies at least one `wrong-jurisdiction` case in the full 71, which the 15-project sample's 0/15 does not reflect. The full 71 is otherwise dominated by City of LA ZIPs (90001-90089, 90232 is the sole outlier). A full-list jurisdiction pass would almost certainly add at most 1-3 `wrong-jurisdiction` cases, not change the overall shape.

**What the extrapolation means in practice**:

- The dominant failure mode is not a single matcher bug. It is a mix of (i) addresses that LADBS genuinely does not have indexed at the seeded (zip, address_start) - even broad hbkd-qubn activity is absent - and (ii) ambiguous cases that need human inspection to resolve.
- The `matcher-fixable` slice is real and concrete: it includes at least one project (Hudson, `c8dcd0ff`) whose cpkv-aajs row already exists under a different ZIP and whose seeded APN matches that row; at least one project (The Clark on 54th, `07fa917c`) with a verifiably wrong seeded address number; and at least one (East End Studios, `1ca77896`) where a lot's alternate frontage address (`WASHINGTON` vs `JESSE`) is missing from `raw_addresses`.
- The `seed-stale` slice is small but unambiguous: Restorative Care Village (`f9f51dc0`) was completed in 2022 per multiple public sources. A focused seed-status review likely surfaces more of these once a full 71-project pass is run.
- The `cant-tell` bucket is as large as the `matcher-fixable` bucket. Most of those cases are either seed-APN-wrong (like `a7bac194`) or seed-address-wrong (like `7ea4d763` or `18c3a172`); a seed cleanup pass would collapse most of `cant-tell` into either `matcher-fixable` or a remaining `genuine-gap`.
- `dcp-covers-it` cannot be measured from Socrata alone; the DCP scan was best-effort and did not surface a DCP case record for any of the 15 in the bounded budget. That is not evidence that DCP lacks coverage - it is evidence that DCP case-record discovery from Socrata-only data is hard without an adapter.

## 8. Recommendation

**Primary next lever: (b) seed data cleanup pass, narrowly targeted at the 71-project UC not-found bucket.**

Rationale:

- The three clearly matcher-fixable cases in the sample (Hudson ZIP mismatch; Clark on 54th wrong street number; East End Studios missing alternate Washington frontage) are all rooted in seed data that is incomplete or incorrect. The matcher would work once the seed is right. Even the production matcher's declared `matching_keys: [permit_number, apn, canonical_address]` for `ladbs_new_housing` (cpkv-aajs) should find Hudson via APN today - so the lever is not a new matcher primitive but the removal of a blocker somewhere between the collected row and the matcher output. Seed cleanup is the forcing function that exposes those blockers.
- `cant-tell` (3/15, ~15/75 extrapolated) is also largely resolvable via seed inspection (bad APN, address typo, alternate frontage), not by new code.
- `seed-stale` (1/15) is definitionally in the seed-cleanup bucket.
- Seed cleanup is the cheapest lever: no adapter work, no migrations, no new Socrata endpoint wiring. It's bounded to ~71 projects and can be done by an analyst in parallel with engineering work.
- The team has already decided not to build more LADBS sibling sources, so the adapter-building alternatives are narrowly (a) matcher work and (c) a DCP / LA Case Reports adapter. Option (c) is premature because DCP's primary value is earlier-lifecycle discovery - it would not change the LADBS-permit picture for projects already in the seed at UC status, and building an adapter for discovery should be justified on discovery-lift evidence, not on closing this gap.

**Secondary lever: (a) matcher pass - specifically an APN-first matching improvement for cpkv-aajs and 3f9m-afei.**

The Hudson case proves APN matching against cpkv-aajs already-ingested rows could close at least one case with no seed change. A narrow fix - 'if a project has an APN identifier and a cpkv-aajs or 3f9m-afei row exists with the same (book, page, parcel) after zero-padding, link them regardless of ZIP divergence' - is low-risk, testable, and complements seed cleanup. An additional ±5 house-number tolerance check for Bucket-C-like clean addresses could be evaluated against the remaining not-found projects, but should be gated on false-positive risk and run after the seed pass.

**Not recommended as the primary next lever:**

- (c) LA Case Reports (DCP) adapter: premature. This adapter already has a `not_started` placeholder in `config/markets/los_angeles.yaml` (Step 3.2). Build it on discovery-coverage evidence, not on evidence from this sample - which finds no DCP-only recoveries because the DCP data is not queryable from this audit's method. The DCP adapter may still be the right next thing after seed cleanup and the APN-first matcher fix expose what remains of the residual gap.
- (d) Rescope the `los_angeles` market boundary: the full 71 contains at most ~1-3 wrong-jurisdiction projects (one confirmed, 90232). Not a material lever.

---

## Appendix A. Reproducibility checklist

- Supabase reads: PostgREST on tables `projects`, `project_identifiers`, `project_source_records`. Read-only; filters `market=eq.los_angeles`, `pipeline_status=eq.Under Construction`.
- Socrata reads: `hbkd-qubn`, `cpkv-aajs`, `3f9m-afei`. SoQL `$where` forms used are captured verbatim in each per-project subsection of section 5.
- Not-found derivation: targeted `(zip, address_start IN (...))` pull, local street-name token overlap check, direction-independent, suffix-independent, ordinals normalized per `src/tcg_pipeline/matching/normalizer.py`'s `ORDINAL_WORD_MAP`. APN zero-padding per `_build_assessor_apn` in `src/tcg_pipeline/source_adapters/ladbs.py`.
- Sample determinism: sort by `project_id` asc; pick evenly-spaced indices `[int(step*(i+0.5)) for i in range(n)]`; redistribute deficits per task rule.
- The final not-found list used by this doc is 71 projects; the bundle audit's 75 is the scaling denominator in section 7, with the 4-project drift attributed to methodology differences noted in section 2.2.

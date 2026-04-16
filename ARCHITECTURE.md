# TCG Pipeline Tracker — Architecture & Build Spec

> **Living document.** This file is the single source of truth for the project. Claude Code / Codex should read this at the start of every session, update it when the plan changes, and mark build steps complete as code is committed. Use commits as checkpoints to review the plan and evaluate if anything changed and should be recorded in this file.

**Last updated:** 2026-04-15T17:15
**Status:** Build in progress — foundation scaffolded, initial Supabase schema applied, pre-ingester hardening complete.
- ✅ Pipedream field mapping (81 fields)
- ✅ CoStar field mapping (287 columns, MF + non-MF)
- ✅ Master schema finalized
- ✅ Source workflow analysis complete (all 6 Compound sources + LADBS verified)
- ✅ Direct verification of all API endpoints via browser (LADBS SODA, LAHD SODA, PDIS, ZIMAS ArcGIS, LA Case Reports PDF API, SM Dev Tracking PDF, SM Ministerial PDF)

---

## 1. Project Overview

### What this is
An automated system for building and maintaining comprehensive real estate development pipeline data across US markets. The system ingests baseline data from CoStar and internal TCG research (Pipedream reports), then continuously enriches, updates, and expands the pipeline using public data sources — city planning portals, permit databases, affordable housing catalogs, and environmental review filings.

### Who it's for
TCG researchers. The system handles the data collection and change detection; researchers handle the judgment calls — confirming new projects, validating status changes, and investigating ambiguous cases.

### Core workflow
```
Seed (once per market) → Collect (scheduled) → Match → Diff → Review (human) → Update
```

### Design principles
- **CoStar is the baseline, not the ceiling.** Every market gets seeded with CoStar. Public sources fill the gaps CoStar misses and keep things current.
- **TCG research is the gold standard.** Where Pipedream or other TCG-verified data exists, it takes priority over all other sources. Researcher overrides are never clobbered by automated updates.
- **Automate collection, not judgment.** The system presents findings; researchers decide. Low-confidence items are flagged, not silently ingested.
- **Source types are reusable, source instances are per-market.** A Socrata collector works for any Socrata-based open data portal. Each market just configures which endpoints to hit.
- **Start with LA, design for any market.** Every architectural decision should consider whether it generalizes.

---

## 2. System Architecture

### Layer 0: Market Seeder
Runs once when standing up a new market. Two jobs:

**Job A — Ingest baseline data:**
- Accepts CoStar CSV/Excel exports and TCG Pipedream reports
- Normalizes addresses, deduplicates across sources
- Loads into master database as the starting project set
- Where CoStar and Pipedream cover the same project, Pipedream fields take priority (human-verified)
- Preserves source provenance — every field records where it came from

**Job B — Register market sources:**
- Each market has a config file defining which collectors to run
- Config specifies: source name, collector type, endpoint/URL, geographic bounds, query parameters, update frequency
- On first run, executes all collectors and runs a full match/diff cycle to catch projects the baseline missed

**Example market config — Los Angeles (verified endpoints):**
```yaml
market: los_angeles
display_name: "Los Angeles"
bounds:
  jurisdictions:
    - city_of_los_angeles

sources:
  # ── DISCOVERY + UPDATE (Socrata — bulk lists) ──
  - name: ladbs_permits
    collector: socrata
    endpoint: "https://data.lacity.org/resource/hbkd-qubn.json"
    schedule: weekly
    soql_filter: "permit_type='Bldg-New'"
    role: update + discovery

  - name: ladbs_new_housing
    collector: socrata
    endpoint: "https://data.lacity.org/resource/cpkv-aajs.json"
    schedule: weekly
    role: discovery (by-right projects)

  - name: ladbs_cofo
    collector: socrata
    endpoint: "https://data.lacity.org/resource/3f9m-afei.json"
    schedule: weekly
    role: update (completion detection)

  - name: lahd_affordable
    collector: socrata
    endpoint: "https://data.lacity.org/resource/mymu-zi3s.json"
    schedule: monthly
    role: discovery + update (affordable pipeline)

  - name: la_county_planning
    collector: socrata
    endpoint: "https://data.lacounty.gov/resource/ccmr-xemc.json"
    schedule: monthly
    role: discovery (unincorporated areas — future expansion)

  # ── DISCOVERY (PDF parsing) ──
  - name: la_case_reports
    collector: pdf_parser
    endpoint: "https://planning.lacity.gov/dcpapi/general/biweeklycase/doc/{id}"
    schedule: biweekly
    role: discovery (primary — new planning filings)
    notes: "PDF format. IDs are numeric, increment biweekly. Filter for housing request types: HCA, VHCA, DB, TOC, QPSH, 100% Affordable."

  # ── ENRICHMENT (scraper — lookup only) ──
  - name: zimas_pdis
    collector: scraper
    endpoint: "https://planning.lacity.gov/pdiscaseinfo/Search/casenumber/{case_number}"
    mode: enrichment_only
    trigger: on_new_case_number
    role: enrichment (full case detail once case number is known)

  - name: zimas_arcgis
    collector: arcgis
    endpoint: "https://zimas.lacity.org/arcgis/rest/services/D_CASES_WDI_PWA/MapServer/0/query"
    mode: enrichment_only
    role: enrichment (geometry + limited fields for known cases)

  # ── EARLY WARNING ──
  - name: ceqanet
    collector: ceqa_scraper
    endpoint: "https://ceqanet.lci.ca.gov/..."
    schedule: monthly
    role: discovery (large projects in environmental review)
```

**Example market config — Santa Monica (verified endpoints):**
```yaml
market: santa_monica
display_name: "Santa Monica"
bounds:
  jurisdictions:
    - city_of_santa_monica

sources:
  # ── DISCOVERY + UPDATE (PDF parsing — comprehensive lists) ──
  - name: sm_dev_tracking
    collector: pdf_parser
    endpoint: "https://www.santamonica.gov/media/Document%20Library/Topic%20Explainers/Planning%20Resources/{MM}.{YYYY}%20({MON})%20Development%20Tracking%20Projects%20List.pdf"
    schedule: monthly
    role: discovery + update (457+ projects, comprehensive entitlement list)
    host_page: "https://www.santamonica.gov/status-of-development-projects"

  - name: sm_ministerial
    collector: pdf_parser
    endpoint: "https://www.santamonica.gov/media/Document%20Library/Topic%20Explainers/Santa%20Monica%27s%20Housing%20Progress/Status%20of%20Ministerial%20Housing%20Applications_{M.DD.YY}.pdf"
    schedule: monthly
    role: update (expedited approval pathway + dates for 108+ projects)
    host_page: "https://www.santamonica.gov/status-of-housing-administrative-approvals"
    cross_ref: "Match with sm_dev_tracking on address + permit_number"
    notes: "URL pattern varies — some editions have _NEW suffix, some have date like _4.15.26. Safest approach: scrape host_page for current PDF link."

  # ── UPDATE (Socrata — permit tracking) ──
  - name: sm_active_permits
    collector: socrata
    endpoint: "https://data.smgov.net/resource/kpzy-s8rg.json"
    schedule: weekly
    role: update (building permit status progression)

  - name: sm_inspections
    collector: socrata
    endpoint: "https://data.smgov.net/resource/xird-2kxi.json"
    schedule: weekly
    role: update (active construction tracking)

  # ── OPTIONAL: deeper permit detail ──
  # - name: sm_epermit_accela
  #   collector: accela
  #   endpoint: "https://epermit.smgov.net"
  #   api: "https://developer.accela.com/v4/records"
  #   auth: oauth2
  #   role: enrichment (full permit history — only if Socrata insufficient)
```

### Layer 1: Collectors
Modular, one per source type. Each collector:
- Accepts a config block (endpoint, params, filters, bounds)
- Queries the source and handles pagination
- Outputs a list of standardized `RawRecord` objects
- Logs what it pulled (record count, date range, any errors)

**Collector types to build (in priority order):**

| Collector | Covers | API type | Priority |
|-----------|--------|----------|----------|
| `socrata` | LADBS permits, LAHD affordable, LA County permits, and any Socrata-based open data portal nationwide | REST/JSON (SODA API) | P0 — build first |
| `la_planning_api` | LA Case Reports (biweekly filings) | Custom REST | P0 |
| `arcgis` | ZIMAS, ArcGIS-based map services (many cities use this) | REST/JSON | P1 |
| `pdf_tabular` | Santa Monica tracking PDFs, any structured tabular PDF | File download + parse | P1 |
| `accela` | Accela Citizen Access portals (ePermit, used by many cities) | REST or browser automation | P2 |
| `ceqa` | CEQAnet state clearinghouse | Scraper | P2 |
| `costar_csv` | CoStar exports (seeder only) | CSV/Excel file parse | P0 (seeder) |
| `pipedream_xlsx` | TCG Pipedream reports (seeder only) | Excel file parse | P0 (seeder) |

#### Pipedream Ingester — Detailed Spec

**File format:** `.xlsm` (macro-enabled Excel). Read with `openpyxl`, `data_only=True` (read computed values, not formulas).

**Target tab:** `DataStorage` (row 3 = headers, row 4+ = data). Headers are in odd-numbered columns (1, 3, 5, ...). Even columns are formatting spacers — skip them.

**Ingestion rules:**
1. Read all rows from DataStorage where `ProjectID` (col 1) is not null and not "--"
2. Skip rows where `CurrStatus` is "Delete - Duplicate" (these are known dupes; record in DismissedRecords and preserve `CorrP` in notes or relationship staging if populated)
3. Skip rows where `CurrStatus` is "Delete - Outside Market Area" (record in DismissedRecords)
4. Skip rows where `CurrStatus` is "Delete - Not Residential" (record in DismissedRecords)
5. For remaining rows, map fields per the table in section 3b
6. Handle "--" as null/empty for all fields (Pipedream uses "--" as its null sentinel)
7. Unroll PStat1-6 / PStatDate1-6 into StatusHistory rows (PStat1 is most recent previous, PStat6 is oldest)
8. Also create a StatusHistory entry for the current CurrStatus / CurrStatusDate
9. Collect Site1-4 into `source_urls[]`, skipping any that are "--"
10. Collect RelP1-6 into staged `ProjectRelationship` rows, skipping any that are "--"
11. Set `status_confidence` = "high" for all Pipedream records (human-verified)
12. Set `created_by` = "pipedream_import"
13. Set `last_editor` from Editor field, `last_edit_date` from EditDate field

**Address normalization on import:** Pipedream addresses are reasonably clean (e.g., "5939 W Sunset Blvd", "1718 N Las Palmas Ave") but need standardization for matching. Apply the normalizer from Layer 2 during import.

**Multi-file handling:** LA has 3 Pipedream files covering different submarkets. Each has a different region prefix in ProjectID. Import all three; deduplication should be handled by address matching (same project shouldn't appear in two files unless it's on a boundary). If duplicates are found across files, flag for researcher review.

**Scope filtering (City of LA only):** Since some Pipedream files cover areas outside City of LA (e.g., West Hollywood, Glendale/Burbank), filter on `City == "Los Angeles"` during import for the LA proof of concept. Preserve the others in a separate holding table for when those markets are added.

#### CoStar Ingester — Detailed Spec

**File format:** `.xlsx`, single sheet (name varies by export date, e.g., `Export041526`), 287 columns. Standard `openpyxl` read.

**Critical design: header-name-based mapping.** CoStar uses completely different column layouts for different property type exports (233 of 287 columns shift between MF and non-MF). The ingester must **never hardcode column numbers.** Instead:
```python
# Read header row, build name→column lookup
headers = {}
for cell in ws[1]:
    if cell.value:
        headers[cell.value] = cell.column

# Then resolve fields by name
address = row_vals.get(headers['Property Address'])
developer = row_vals.get(headers.get('Developer Name'))
constr_status = row_vals.get(headers['Constr Status'])
```
This approach handles any CoStar export regardless of property type and is robust against CoStar changing their export layout in the future.

**Multi-file input:** The seeder accepts a folder of CoStar export files. Each file is read independently with its own header mapping. Files may contain MF, non-MF, or a mix of property types. All records flow into the same pipeline.

**CoStar exports are capped at 500 rows.** For full LA coverage, expect 4-8 export files per market (split by property type, submarket, or status). Nate stitches these together manually today; our system handles them natively as separate files.

**Ingestion rules:**
1. For each `.xlsx` file in the CoStar seed folder:
   a. Read header row → build name→column lookup
   b. Read all data rows (row 2+)
   c. Resolve fields by header name per Section 3c
2. Map `Constr Status` → Pipedream status enum per mapping table in Section 3b
3. Normalize zip codes to 5 digits (CoStar sometimes includes ZIP+4, e.g., "90057-3106")
4. Normalize city names: "Los Angeles CBD", "Downtown Los Angeles", "Hollywood" etc. all → "Los Angeles" for City of LA filtering
5. Convert bed mix percentages from 0-100 scale to 0.0-1.0 (Pipedream uses 0-1, CoStar uses 0-100)
6. Parse "Construction Begin" from "Mon YYYY" string to date (first of month)
7. Parse "Year Built" + "Month Built" to estimated delivery date
8. Set `status_confidence` = "medium" for all CoStar records (automated, not researcher-verified)
9. Set `created_by` = "costar_import"
10. Store CoStar `PropertyID` as `ProjectIdentifier(type=costar_property_id)`
11. Detect property type from `Property Type` header — store as-is for non-MF (Office, Retail, Hospitality, etc.)

**Deduplication with Pipedream on import:**
When seeding, Pipedream is ingested first, CoStar second. For each CoStar record:
1. Match against existing DB by APN (highest confidence — CoStar has 92-98% APN coverage)
2. Fall back to address matching if no APN match
3. If matched: merge CoStar fields into existing Pipedream record, filling gaps only (never overwrite Pipedream data)
4. If no match: create new record from CoStar data alone
5. Fields CoStar uniquely contributes on merge: `ProjectIdentifier(type=apn)`, `ProjectIdentifier(type=costar_property_id)`, `zoning`, `owner`, `true_owner`, `acres`, `parking_spaces`, `style`, `total_sf`, `date_construction_start`, `costar_submarket`, `building_class`

**Deduplication across CoStar files:**
Multiple CoStar exports may contain the same project. Deduplicate on `PropertyID` (CoStar's internal ID) — if the same PropertyID appears in two files, take the first occurrence and skip the duplicate.

**Handling "Abandoned" status (~40-50% of most exports):**
These represent projects that were planned but never built. Import them with status = "Inactive" but flag them so the differ knows not to actively track them. Useful as historical context but shouldn't generate review queue items.

### Layer 2: Matcher
Takes `RawRecord` objects and matches them against the master database.

**Matching strategy (in order of confidence):**
1. **APN (Assessor Parcel Number)** — Highest confidence. Unique per parcel. Not always available.
2. **Exact address match** — After normalization (see below).
3. **Fuzzy address match** — Handles variations like "St" vs "Street", missing unit numbers, directional abbreviations.
4. **Coordinate proximity** — If lat/lng available, match within ~50m radius. Handles cases where address formats differ significantly.
5. **Project name + developer** — Fallback for cases where address data is unreliable.

**Address normalization rules:**
- Standardize directionals (N/S/E/W → North/South/East/West, or vice versa — pick one)
- Standardize street types (St/Street, Ave/Avenue, Blvd/Boulevard, etc.)
- Strip unit/suite numbers for building-level matching
- Uppercase everything for comparison
- Handle common LA-specific patterns (e.g., "S. Figueroa" vs "South Figueroa St")

**Match output buckets:**
- `CONFIDENT_MATCH` — High confidence this raw record maps to an existing project. Auto-proceed to differ.
- `POSSIBLE_MATCH` — Likely the same project but needs human confirmation. Flagged in review queue.
- `NO_MATCH` — Likely a new project. Packaged as a candidate for review.

### Layer 3: Differ
For `CONFIDENT_MATCH` and confirmed `POSSIBLE_MATCH` records, compares incoming data against stored record.

**Change detection:**
- Field-by-field comparison for all tracked fields
- Assigns priority to each change type:
  - **HIGH:** Status change (e.g., Proposed → Approved → Under Construction → Completed), appeal filed, project withdrawn/stalled
  - **MEDIUM:** Unit count change, developer change, new permit activity, affordability breakdown updated
  - **LOW:** Date corrections, description text changes, staff reassignment
- Respects source hierarchy — a ZIMAS status update can override a CoStar status, but nothing overrides a TCG researcher's manual override without flagging it

**For `NO_MATCH` (new project candidates):**
- Assigns a relevance score based on:
  - Project type (residential/mixed-use = high relevance, pure commercial = medium, non-development permits = low)
  - Size (300 units = definitely pipeline, 3 units = probably not worth tracking)
  - Source reliability
- Packages with all available source data for researcher review

### Layer 4: Review Workflow
The researcher's workspace. For MVP, the delivery surface can be an Excel workbook or a simple web UI. Regardless of surface, the review queue itself is persisted in database tables from day one so researcher actions are durable and auditable.

**Review queue contents:**
1. **New project candidates** — Source data, relevance score, reason flagged. Researcher decides: add to pipeline, skip, or flag for more research.
2. **Status changes** — Current record vs. incoming data, side by side. Researcher confirms or rejects.
3. **Possible matches** — System thinks these might be the same project. Researcher confirms or splits.
4. **Potential stalls** — Projects with no activity across any source for X months. Researcher investigates.
5. **Low-confidence items** — Anything the system isn't sure about.

**Researcher actions:**
- Accept (creates a review decision and applies the approved update to the master database)
- Reject (records the rejection, optionally adds a dismissal/filter rule)
- Override (records a manual field value, which gets protected from future automated updates)
- Defer (keeps the item open but deprioritized for later)
- Add note (free-text annotation attached to the project or review item)

---

## 3. Data Model

### 3a. Pipedream Source Analysis

> Based on analysis of `PipeDream 2026 Q1 Hollywood_Los Feliz v3.6.xlsm`. This is one of three Pipedream files covering the LA market. Each file covers a geographic submarket defined by zip codes in the Key tab.

**Workbook structure (18 tabs):**
- `StartMenu` — Navigation/launch page
- `ProjectInput` — Form interface where researchers enter/update projects (not a table — it's a structured form layout)
- `Dashboard` — Summary reporting view
- `Export` — Formatted export of project data
- `StorageImport` — Bulk import interface (2019 columns — handles incoming data from other Pipedream files or external sources)
- `StatusOverride` — Batch status change tool
- `DataStorage` — **The database.** 81 fields per project, up to 1081 rows. This is what we ingest.
- `Key` — Region definitions (zip-to-region mapping), field option lists, staff roster, ProjectID generation
- `CountyLists` — County-level region configuration
- `TempBackup` — Temporary backup of current edits
- `ImportDupList` / `StorageDupList` — Deduplication detection on import
- `IBU1-4` — Input buffer tabs (staging area for multi-project entry)
- `SBU1-2` — Storage backup tabs (full copies of DataStorage for versioning)

**ProjectID format:** `{region_index}.{sequence}` (e.g., `23.00001`). The prefix `23` maps to "Hollywood/Los Feliz, CA" in the Key tab's Region ID Generator. Each Pipedream file has its own region prefix(es). On import to our system, we preserve these as `ProjectIdentifier(type=tcg_pipedream_id)` values and generate our own canonical IDs.

**Status lifecycle (from Key tab):**
```
Conceptual → Proposed → Pending → Approved → Under Construction → Pre-Leasing/Pre-Selling → Complete
                                                    ↘ Stalled
                                                    ↘ Inactive
Special: Delete - Duplicate | Delete - Outside Market Area | Delete - Not Residential
```
Note: "Pending" in Pipedream means in entitlement review (EIR in progress, planning commission review, etc.). "Approved" means entitled but not yet permitted/under construction. The system tracks up to 6 previous statuses with dates, providing a full status history per project.

**Important workflow note:** When status moves to Pre-Leasing/Pre-Selling or Complete, the researcher is instructed to set CurrStatusDate to the lease start date (not the date they updated the record). This means status dates have real-world meaning, not just edit timestamps.

**Product type options:** Apartment, Condo, Single-Family, Townhome, Micro/Co-Living, Other, Unknown
**Rental/For-Sale options:** Rental, For-Sale, Both (Rental & FS), Unknown
**Senior type options:** Non Age-Restricted, Senior, Student, Unknown

**This file contains 372 projects** (Hollywood/Los Feliz submarket):
- Status: Complete (115), Stalled (84), Under Construction (47), Pending (38), Approved (34), Inactive (19), Proposed (16), Delete flags (18), Conceptual (1)
- Almost entirely Rental Apartment (364 of 372)
- 370 in City of Los Angeles, 2 in West Hollywood

### 3b. CoStar Source Analysis

> Based on analysis of `CostarExport (23).xlsx`. This is a single export covering the full LA market.

**File format:** `.xlsx`, single sheet (`Export041526`), 287 columns, 1000 data rows (likely a CoStar export row limit — may need multiple exports or API access for full coverage).

**CoStar exports are capped at 500 rows.** To get full coverage, Nate stitches multiple exports together (e.g., 2 MF exports + 1 non-MF export for the combined file we initially analyzed). **Each property-type export has a completely different column layout** — 233 of 287 columns are in different positions between MF and non-MF. Only 4 columns share the same position (Constr Status, Construction Begin, Longitude, Zoning).

**The ingester must map by header name, not column number.** Read row 1, build a name→column lookup, and resolve fields by name. This handles any CoStar export regardless of property type and is robust against CoStar changing their export format over time. The seeder accepts a folder of raw CoStar export files (one per export), reads each file's headers independently, and ingests them all into the same pipeline.

**Constr Status distribution (1000 records, reliable across both layouts):**
- Abandoned: 394 (39%) — projects that were planned but never built
- Proposed: 364 (36%) — in planning/pre-entitlement
- Under Construction: 180 (18%) — actively building
- Deferred: 47 (5%) — paused/stalled
- Final Planning: 15 (2%) — entitled, close to breaking ground

**Multifamily breakdown (772 rows):**
- Rent Type: Market (310), Market/Affordable (263), Affordable (172), Unknown (27)
- Style: Mid-Rise (514), Low-Rise (100), Hi-Rise (85), Garden (3), Townhome (3)
- Market Segment: All (728), Senior (13), Corporate (1), Military (1)
- 14 duplicate addresses found (likely phased projects or data artifacts)

**Field population rates (1000 records):**
- Units: 100%, Lat/Long: 98%, Parcel Number: **92%**, RBA (SF): 78%, Owner: 75%, Stories: 74%, Land Area: 67%, Constr Begin: 60%, Zoning: 52%, Developer: 45%, Year Built: 41%

**Key difference from Pipedream:** CoStar has **92% parcel number coverage** vs. Pipedream's 1%. This is the single biggest enrichment CoStar provides — APN is our strongest matching key for linking to public sources like LADBS and ZIMAS.

**CoStar status → Pipedream status mapping:**
| CoStar Constr Status | Pipedream Equivalent | Notes |
|---------------------|---------------------|-------|
| Under Construction | Under Construction | Direct match |
| Final Planning | Approved | Entitled, ready to build |
| Proposed | Proposed or Pending | CoStar doesn't distinguish; default to Proposed, let researcher refine |
| Deferred | Stalled | Paused projects |
| Abandoned | Inactive | Projects that never materialized |

**CoStar address format:** Similar to Pipedream. Examples: "8070 W Beverly Rd", "602 S Westlake Ave", "549 S Harvard Blvd". Uses W/S/N/E directional abbreviations, standard street type abbreviations. Some addresses have ranges ("407-413 E 5th St") or bare street names ("W 3rd St"). City field includes both "Los Angeles" and neighborhood-level names ("Los Angeles CBD", "Downtown Los Angeles", "Hollywood") — will need normalization.

### 3c. CoStar Field Inventory

> **All fields referenced by header name, not column number.** Column positions differ between MF and non-MF exports (233 of 287 columns shift). The ingester reads row 1 headers and builds a name→column lookup per file.

Only fields relevant to pipeline tracking are listed. CoStar has 287 columns total, but most are operational/financial data (rent rates, vacancy, cap rates, etc.) not needed for pipeline tracking.

**Identity & Location (same header names in both MF and non-MF exports):**
| CoStar Header | Description | MF Pop. | Non-MF Pop. | Maps to DB field |
|--------------|-------------|---------|-------------|-----------------|
| Property Address | Street address | 100% | 100% | `canonical_address` (after normalization) |
| Property Name | Project/building name | ~70% | ~60% | `project_name` |
| City | City name (inconsistent — includes neighborhood names in MF) | 100% | 100% | `city` (needs normalization) |
| State | State abbreviation | 100% | 100% | `state` |
| Zip | Zip code (sometimes 9-digit) | 100% | 100% | `zip` (truncate to 5) |
| County Name | County | 100% | 100% | `county` |
| Market Name | CoStar market (e.g., "Los Angeles, CA") | 100% | 100% | `market` |
| Submarket Name | CoStar submarket (e.g., "Koreatown") | 100% | 100% | `costar_submarket` |
| Latitude | Latitude | 98% | 100% | `lat` |
| Longitude | Longitude | 98% | 100% | `lng` |
| Parcel Number 1(Min) | APN — primary parcel | **92%** | **98%** | `apn` |
| Parcel Number 2(Max) | APN — secondary parcel (multi-parcel sites) | sparse | sparse | `apn_secondary` |
| PropertyID | CoStar internal ID | 100% | 100% | `ProjectIdentifier (costar_property_id)` |

**Project Details:**
| CoStar Header | Description | MF Pop. | Non-MF Pop. | Maps to DB field |
|--------------|-------------|---------|-------------|-----------------|
| Property Type | "Multifamily" for MF; "Office", "Retail", "Hospitality", etc. for non-MF | 100% | 100% | `property_type` |
| Secondary Type | "Apartments" for MF; "Hotel", "Storefront", "Loft/Creative Space", etc. | ~99% | varies | `secondary_type` |
| Number Of Units | Total residential unit count | 100% | 3% (student housing only) | `total_units` |
| RBA | Rentable Building Area (SF) | 78% | 100% | `total_sf` |
| Number Of Stories | Stories | 74% | 96% | `stories` |
| Style | Building style (Mid-Rise, Hi-Rise, etc.) | ~93% | sparse | `style` |
| Land Area (AC) | Site acreage | 67% | varies | `acres` |
| Number Of Parking Spaces | Parking | sparse | sparse | `parking_spaces` |
| Zoning | Zoning designation | 52% | varies | `zoning` |
| Rooms | Hotel rooms | N/A | 37% (hospitality) | `hotel_keys` |
| Building Class | A/B/C rating | N/A | ~80% | `building_class` |

**Bed mix (MF only):**
| CoStar Header | Maps to DB field |
|--------------|-----------------|
| % Studios | `pct_studio` (divide by 100) |
| % 1-Bed | `pct_1bed` (divide by 100) |
| % 2-Bed | `pct_2bed` (divide by 100) |
| % 3-Bed | `pct_other_bed` (combine with 4BR, divide by 100) |
| % 4-Bed | `pct_other_bed` (combine with 3BR, divide by 100) |

**Affordability (MF only):**
| CoStar Header | Description | Population | Maps to DB field |
|--------------|-------------|------------|-----------------|
| Rent Type | Market / Affordable / Market+Affordable | ~97% | `rent_or_sale` (combined with logic) |
| Affordable Type | Affordable Units / Rent Restricted / Rent Subsidized / etc. | ~41% | `affordable_type` |
| Market Segment | All / Senior / etc. | ~97% | `age_restriction` |

> **Note:** CoStar doesn't separate market-rate vs. affordable unit counts. It only has total units + a flag for whether the property is affordable/mixed. Pipedream has explicit MRUnits and AffUnits. When both sources cover the same project, use Pipedream's unit breakdown.

**Status & Dates (same header names across exports):**
| CoStar Header | Description | Population | Maps to DB field |
|--------------|-------------|------------|-----------------|
| Constr Status | Pipeline status (see mapping table in 3b) | 100% | `pipeline_status` (after mapping) |
| Building Status | Same values as Constr Status | 100% | (redundant, use Constr Status) |
| Construction Begin | Month + Year construction started (e.g., "Dec 2023") | 60% | `date_construction_start` |
| Year Built | Expected/actual completion year | 41% | `date_delivery` (year only) |
| Month Built | Completion month | sparse | `date_delivery` (combine with Year Built) |

**Developer & Owner:**
| CoStar Header | Description | MF Pop. | Non-MF Pop. | Maps to DB field |
|--------------|-------------|---------|-------------|-----------------|
| Developer Name | Developer | 45% | 56% | `developer` |
| Owner Name | Current owner | 75% | ~80% | `owner` |
| True Owner Name | Beneficial owner (through LLCs) | varies | varies | `true_owner` |
| Architect Name | Architect | sparse | sparse | `architect` |

> **Note:** CoStar's Developer field is only 45% for MF, 56% for non-MF. Pipedream's is ~99%. When merging, Pipedream developer takes priority.

**Non-MF export breakdown (231 records):**
| Property Type | Count | Key Secondary Types |
|--------------|-------|-------------------|
| Hospitality | 85 | Hotel (85) |
| Office | 58 | Loft/Creative Space (7), Movie/Radio/TV Studio (9) |
| Retail | 38 | Storefront (10), Restaurant (4), Fast Food (3) |
| Specialty | 22 | Self-Storage (2), Parking Lot (1), Data Center (1) |
| Health Care | 9 | Medical (5), Assisted Living (6), Hospital (1) |
| Industrial | 7 | Warehouse (3) |
| Student | 6 | Apartments-Student (5), Dormitory (1) |
| Sports & Entertainment | 1 | — |

Constr Status: Abandoned (118, 51%), Proposed (65, 28%), Under Construction (24, 10%), Final Planning (12, 5%), Deferred (12, 5%)

### 3d. Pipedream Field Inventory (DataStorage tab)

All 81 fields from DataStorage, grouped by function. Column numbers reference the Excel layout (every-other-column pattern — data in odd columns, formatting/spacing in even).

**Identity & Location:**
| Pipedream Field | Col | Description | Population | Maps to DB field |
|----------------|-----|-------------|------------|-----------------|
| ProjectID | 1 | `{region}.{seq}` format (e.g., 23.00001) | 100% | `ProjectIdentifier (tcg_pipedream_id)` |
| Name | 11 | Project name (e.g., "Palladium Residences") | 100% | `project_name` |
| Developer | 13 | Developer/owner (e.g., "CIM Group") | ~99% | `developer` |
| Address | 15 | Street address (e.g., "5939 W Sunset Blvd") | 100% | `canonical_address` (after normalization) |
| State | 17 | Always "CA" for LA files | 100% | `state` |
| County | 19 | Always "Los Angeles" for LA files | 100% | `county` |
| City | 21 | City name (e.g., "Los Angeles", "West Hollywood") | 100% | `city` |
| Zip | 23 | 5-digit zip code | 100% | `zip` |
| Region | 25 | TCG submarket name (e.g., "Hollywood/Los Feliz, CA") | 100% | `tcg_region` |
| Lat | 27 | Latitude | **100%** | `lat` |
| Long | 29 | Longitude | **100%** | `lng` |

**Project Details:**
| Pipedream Field | Col | Description | Population | Maps to DB field |
|----------------|-----|-------------|------------|-----------------|
| RentFS | 31 | Rental / For-Sale / Both / Unknown | 100% | `rent_or_sale` |
| MRUnits | 33 | Market-rate unit count | ~95% | `market_rate_units` |
| AffUnits | 35 | Affordable unit count | ~90% | `affordable_units` |
| TotUnits | 37 | Total units (MR + Aff) | ~99% | `total_units` |
| Acres | 39 | Site acreage | sparse | `acres` |
| RetailSF | 41 | Retail square footage | **38%** | `retail_sf` |
| OfficeSF | 43 | Office square footage | **5%** | `office_sf` |
| HKeys | 45 | Hotel keys (if hospitality component) | sparse | `hotel_keys` |
| ProdType | 47 | Apartment/Condo/Townhome/etc. | ~99% | `product_type` |
| Elevation | 49 | Number of stories | **88%** | `stories` |
| Senior | 51 | Age restriction type | ~95% | `age_restriction` |
| PercS | 53 | % Studio units | **53%** | `pct_studio` |
| Perc1B | 55 | % 1-Bedroom units | ~53% | `pct_1bed` |
| Perc2B | 57 | % 2-Bedroom units | ~53% | `pct_2bed` |
| PercOther | 59 | % Other bedroom types | ~53% | `pct_other_bed` |
| PercBedSum | 61 | Validation: should sum to 1.0 | ~53% | (validation only, not stored) |

**Status & Dates:**
| Pipedream Field | Col | Description | Population | Maps to DB field |
|----------------|-----|-------------|------------|-----------------|
| CurrStatus | 63 | Current pipeline status | 100% | `pipeline_status` |
| CurrStatusDate | 65 | Date of current status (real-world date, not edit date) | ~98% | `status_date` |
| DeliveryDate | 147 | Estimated or actual delivery/completion date | **45%** | `date_delivery` |
| PStat1 / PStatDate1 | 103-104 | Previous status 1 + date (most recent previous) | varies | → `status_history` table |
| PStat2 / PStatDate2 | 107-108 | Previous status 2 + date | varies | → `status_history` table |
| PStat3 / PStatDate3 | 111-112 | Previous status 3 + date | varies | → `status_history` table |
| PStat4 / PStatDate4 | 115-116 | Previous status 4 + date | varies | → `status_history` table |
| PStat5 / PStatDate5 | 119-120 | Previous status 5 + date | varies | → `status_history` table |
| PStat6 / PStatDate6 | 123-124 | Previous status 6 + date (oldest) | varies | → `status_history` table |

**Jurisdiction & Reference:**
| Pipedream Field | Col | Description | Population | Maps to DB field |
|----------------|-----|-------------|------------|-----------------|
| Jurisdiction | 67 | Planning jurisdiction | **0%** (all "--") | `jurisdiction` |
| RefNum | 69 | Reference/case number | **3%** | `ProjectIdentifier (case_number)` |
| APN | 71 | Assessor Parcel Number | **1%** | `apn` |

> **Key gap:** Jurisdiction, RefNum, and APN are barely populated in Pipedream. These are exactly the fields automated public sources (LADBS, ZIMAS, Case Reports) can fill. This is one of the highest-value enrichment opportunities.

**Planner/Contact Info:**
| Pipedream Field | Col | Description | Population | Maps to DB field |
|----------------|-----|-------------|------------|-----------------|
| Plan1Name | 73 | Primary planner name | **1%** | `planner_1_name` |
| Plan1City | 75 | Primary planner city | ~1% | `planner_1_city` |
| Plan1Email | 77 | Primary planner email | ~1% | `planner_1_email` |
| Plan1Phone | 79 | Primary planner phone | ~1% | `planner_1_phone` |
| Plan2Name | 81 | Secondary planner name | ~0% | `planner_2_name` |
| Plan2City | 83 | Secondary planner city | ~0% | `planner_2_city` |
| Plan2Email | 85 | Secondary planner email | ~0% | `planner_2_email` |
| Plan2Phone | 87 | Secondary planner phone | ~0% | `planner_2_phone` |

> Planner fields are almost never populated. Consider whether to carry these forward or drop them. ZIMAS does provide "Staff assigned" — could auto-populate.

**Notes & Sources:**
| Pipedream Field | Col | Description | Population | Maps to DB field |
|----------------|-----|-------------|------------|-----------------|
| Notes | 89 | Researcher notes — the real intelligence (status rationale, market context, developer behavior, appeal outcomes) | **78%** | `researcher_notes` |
| Site1 | 91 | Primary source URL (planning case page, LADBS, developer site) | **95%** | `source_urls[]` |
| Site2 | 93 | Additional source URL | ~70% | `source_urls[]` |
| Site3 | 95 | Additional source URL | ~40% | `source_urls[]` |
| Site4 | 97 | Additional source URL | ~20% | `source_urls[]` |
| PersonalNotes | 99 | Action items / follow-up notes (researcher-private) | sparse | `personal_notes` |
| ChangeNotes | 101 | What changed in last update and why | ~50% | `change_notes` |

> Notes field is extremely valuable — contains judgment calls like "survived appeal," "no sign of BPs yet," "developer wants to build more parking." This is irreplaceable human intelligence. Must be preserved and never overwritten by automation.

**Project Relationships:**
| Pipedream Field | Col | Description | Population | Maps to DB field |
|----------------|-----|-------------|------------|-----------------|
| PrevName1 | 127 | Previous project name (if renamed) | sparse | `previous_names[]` |
| PrevName2 | 129 | Second previous name | rare | `previous_names[]` |
| CorrP | 131 | Correct ProjectID (for duplicates — points to the canonical record) | sparse | `ProjectRelationship (duplicate)` |
| PCPart | 133 | Project Counterpart — rental/FS component ProjectID | **0%** | `ProjectRelationship (counterpart)` |
| RelP1-6 | 135-145 | Related project IDs (phases, master plan components) | sparse | `ProjectRelationship` |

> Related Projects track phased developments. E.g., a 3-phase master plan would have each phase as its own project, with RelP fields linking them. Our system should support this with a `project_relationships` table.

**Workflow/Admin:**
| Pipedream Field | Col | Description | Population | Maps to DB field |
|----------------|-----|-------------|------------|-----------------|
| Complete | 3 | Workflow state: "READY FOR EXPORT", "IN PROGRESS", etc. | 100% | (internal workflow, not migrated) |
| Editor | 5 | Staff initials of last editor (e.g., "JN", "DKB", "NG") | 100% | `last_editor` |
| EditDate | 7 | Date of last edit | 100% | `last_edit_date` |
| NewEntry | 9 | "New" or "Update" — whether this was a new add or an update | 100% | (internal workflow) |
| Import | 149 | Whether imported from another source | sparse | `import_source` |
| ImportDate | 151 | Date of import | sparse | `import_date` |
| UpComplete | 153 | Whether update cycle is complete (Yes/No) | varies | (internal workflow) |
| UpCompleteDate | 155 | Date update completed | varies | (internal workflow) |
| NewPID | 157 | New ProjectID (if reassigned) | rare | (migration only) |
| EditLog | 159 | Edit log marker | varies | (internal workflow) |

### 3e. Master Project Record (Revised)

> Schema finalized based on both Pipedream and CoStar field analysis, then tightened after architecture review. `Project` stores the current best-known canonical state; identifiers, provenance, and review state live in dedicated tables below.

```
Project:
  # ── Identity ──
  id:                   UUID (internal, auto-generated)
  canonical_address:    string (normalized — uppercase, standardized abbreviations)
  raw_addresses:        string[] (all address variants seen across sources)
  lat:                  float (convenience copy for exports/debugging)
  lng:                  float (convenience copy for exports/debugging)
  location:             geography(Point, 4326) (primary spatial column for PostGIS)
  geocode_confidence:   enum [high, medium, low, none]
  market:               string (FK to market config, e.g., "los_angeles")
  city:                 string (e.g., "Los Angeles", "West Hollywood")
  state:                string (e.g., "CA")
  county:               string (e.g., "Los Angeles")
  zip:                  string (5-digit)
  tcg_region:           string (TCG submarket name, e.g., "Hollywood/Los Feliz, CA")
  jurisdiction:         string (planning jurisdiction — enrichable from public sources)
  costar_submarket:     string (CoStar submarket name, e.g., "Koreatown" — different from TCG region)
  zoning:               string (zoning designation — 52% populated from CoStar, enrichable from ZIMAS)

  # ── Project Details ──
  project_name:         string (nullable)
  previous_names:       string[] (if project was renamed)
  developer:            string
  applicant:            string (may differ from developer — often available from planning sources)
  description:          text (full project description — from planning sources)
  rent_or_sale:         enum [Rental, For-Sale, Both, Unknown]
  product_type:         enum [Apartment, Condo, Single-Family, Townhome, Micro/Co-Living, Other, Unknown]
  age_restriction:      enum [Non Age-Restricted, Senior, Student, Unknown]
  stories:              int (Pipedream calls this "Elevation")
  total_units:          int
  market_rate_units:    int
  affordable_units:     int
  pct_studio:           float (0.0-1.0, nullable)
  pct_1bed:             float (0.0-1.0, nullable)
  pct_2bed:             float (0.0-1.0, nullable)
  pct_other_bed:        float (0.0-1.0, nullable)
  acres:                float (nullable)
  retail_sf:            int (nullable — 38% populated in Pipedream)
  office_sf:            int (nullable — 5% populated in Pipedream)
  hotel_keys:           int (nullable)
  total_sf:             int (RBA from CoStar — 78% populated; nullable)
  parking_spaces:       int (nullable — from CoStar or public sources)
  style:                string (Mid-Rise, Hi-Rise, Low-Rise, Garden, etc. — from CoStar)
  property_type:        string (Multifamily, Office, Retail, Hospitality, etc. — from CoStar for non-MF)
  affordable_type:      string (Affordable Units, Rent Restricted, Rent Subsidized — from CoStar)

  # ── Ownership (from CoStar) ──
  owner:                string (current owner — 75% from CoStar)
  true_owner:           string (beneficial owner through LLCs — from CoStar)
  architect:            string (nullable — from CoStar)

  # ── Status Tracking ──
  pipeline_status:      enum [Conceptual, Proposed, Pending, Approved, Under Construction,
                              Pre-Leasing/Pre-Selling, Complete, Stalled, Inactive,
                              Delete-Duplicate, Delete-Outside Market Area, Delete-Not Residential]
  status_date:          date (real-world date of status, NOT the date the record was edited)
  status_confidence:    enum [high, medium, low] (high = researcher-verified, medium = public source, low = inferred)
  status_source:        string (which source last updated the status)
  date_delivery:        date (estimated or actual delivery — 45% Pipedream, 41% CoStar via Year/Month Built)
  date_construction_start: date (from CoStar "Construction Begin" — 60% populated)

  # ── Entitlement & Permit Detail (mostly enriched from public sources) ──
  entitlement_type:     string (e.g., "by-right", "discretionary", "SB330", "density bonus" — from Case Reports/ZIMAS)
  appeal_status:        string (from ZIMAS)
  ceqa_status:          string (from ZIMAS/CEQAnet)

  # ── Planner/Contact Info ──
  planner_1_name:       string (nullable — rarely populated in Pipedream, enrichable from ZIMAS)
  planner_1_city:       string (nullable)
  planner_1_email:      string (nullable)
  planner_1_phone:      string (nullable)
  planner_2_name:       string (nullable)
  planner_2_city:       string (nullable)
  planner_2_email:      string (nullable)
  planner_2_phone:      string (nullable)

  # ── Notes & References ──
  researcher_notes:     text (PROTECTED — never overwritten by automation)
  personal_notes:       text (researcher action items / follow-ups — PROTECTED)
  change_notes:         text (what changed and why — PROTECTED)
  source_urls:          string[] (up to 4+ reference URLs — planning pages, news, developer sites)

  # ── Deterministic identifiers, relationships, and source provenance ──
  # live in dedicated tables below (`ProjectIdentifier`, `ProjectRelationship`,
  # and `ProjectSourceRecord`) rather than on the `Project` row.

  # ── TCG Workflow ──
  last_editor:          string (researcher initials — from Pipedream "Editor" field)
  last_edit_date:       date
  last_reviewed_by:     string (nullable)
  last_reviewed_date:   date (nullable)
  researcher_override:  jsonb (fields manually set by researcher — these are PROTECTED from automated updates)

  # ── Metadata ──
  created_at:           datetime
  updated_at:           datetime
  created_by:           string (source name or researcher initials)
```

### 3f. Status History Table

Replaces Pipedream's fixed PStat1-6 columns with an unbounded history. On Pipedream import, we unroll all 6 status slots into this table.

```
StatusHistory:
  id:                   UUID
  project_id:           FK → Project
  status:               enum (same as pipeline_status)
  status_date:          date (real-world date)
  source:               string (e.g., "pipedream_import", "ladbs_collector", "researcher_manual")
  notes:                text (nullable — why the status changed)
  created_at:           datetime (when this record was created in our system)
```

### 3g. Project Identifier Table

Stores all deterministic match keys and source-stable IDs. This replaces single-value or array fields on `Project` such as `apn`, `case_numbers[]`, `permit_numbers[]`, `costar_id`, and `tcg_pipedream_id`.

```
ProjectIdentifier:
  id:                   UUID
  project_id:           FK → Project
  identifier_type:      enum [apn, zimas_pin, case_number, permit_number, costar_property_id, tcg_pipedream_id]
  value:                string
  source:               string (which source provided this identifier)
  is_primary:           bool (default false)
  first_seen_at:        datetime
  last_seen_at:         datetime
  notes:                text (nullable)

  constraints:
    unique(identifier_type, value)
```

### 3h. Project Relationships Table

Replaces Pipedream's fixed RelP1-6 columns and acts as the sole source of truth for inter-project relationships.

```
ProjectRelationship:
  id:                   UUID
  project_id:           FK → Project
  related_project_id:   FK → Project
  relationship_type:    enum [phase, master_plan, counterpart, duplicate, supersedes]
  notes:                text (nullable)
```

### 3i. Source Provenance Table

Replaces the `sources` JSON blob on `Project`. This makes provenance queryable, auditable, and easier to diff.

```
ProjectSourceRecord:
  id:                   UUID
  project_id:           FK → Project
  source_name:          string
  source_record_id:     string
  source_url:           string (nullable)
  first_seen_at:        datetime
  last_seen_at:         datetime
  last_pulled_at:       datetime
  raw_payload:          jsonb (optional raw source payload)
  mapped_fields:        jsonb (normalized fields extracted from this source record)
  field_provenance:     jsonb (field → confidence / extraction metadata)

  constraints:
    unique(source_name, source_record_id)
```

### 3j. Review Queue State

Review is core system state, not just a UI concern. Persist it in the database even if the first delivery surface is Excel.

```
ReviewItem:
  id:                   UUID
  project_id:           FK → Project (nullable for unmatched candidates)
  source_run_id:        FK → SourceRun (nullable)
  item_type:            enum [new_candidate, status_change, possible_match, potential_stall, low_confidence]
  status:               enum [open, accepted, rejected, deferred, auto_accepted]
  priority:             enum [high, medium, low]
  match_confidence:     float (nullable)
  payload:              jsonb (side-by-side comparison, candidate data, or match evidence)
  assigned_to:          string (nullable)
  created_at:           datetime
  resolved_at:          datetime (nullable)
  resolved_by:          string (nullable)

ReviewDecision:
  id:                   UUID
  review_item_id:       FK → ReviewItem
  action:               enum [accept, reject, override, defer, note]
  actor:                string
  notes:                text (nullable)
  field_overrides:      jsonb (nullable)
  created_at:           datetime
```

### 3k. Change Log

Every change to a project record gets logged:
```
ChangeLog:
  id:                   UUID
  project_id:           FK → Project
  review_item_id:       FK → ReviewItem (nullable)
  timestamp:            datetime
  source:               string (which collector or "manual")
  field:                string
  old_value:            jsonb
  new_value:            jsonb
  change_type:          enum [auto_accepted, researcher_confirmed, researcher_rejected, researcher_override]
  priority:             enum [high, medium, low]
  reviewed_by:          string (nullable)
```

### 3l. Source Run Log

Tracks every time a collector runs:
```
SourceRun:
  id:                   UUID
  market:               string
  source_name:          string
  run_timestamp:        datetime
  records_pulled:       int
  new_matches:          int
  updates_found:        int
  new_candidates:       int
  errors:               text (nullable)
  duration_seconds:     int
```

### 3m. Dismissed Records Table

Tracks records from public sources that a researcher has explicitly rejected, so the system doesn't re-surface them. Critical for preventing "Delete - Duplicate", "Delete - Not Residential", and "Delete - Outside Market Area" projects from being re-discovered.

```
DismissedRecord:
  id:                   UUID
  source:               string (which collector found this)
  source_record_id:     string (the ID in the source system)
  canonical_address:    string (normalized)
  reason:               enum [not_residential, outside_market, duplicate, too_small, other]
  dismissed_by:         string (researcher initials)
  dismissed_at:         datetime
  notes:                text (nullable)
```

---

## 4. Source Inventory & Workflow Analysis

> This section documents every source, what it provides, how to access it programmatically, and how it fits
> into the pipeline workflow. Verified against Compound's field extraction analysis (April 2026) and
> independent research of each endpoint.

### 4a. Seed Sources

| Source | Type | Format | Coverage | Notes |
|--------|------|--------|----------|-------|
| CoStar export | CSV/Excel | Structured | All project types, full LA market | Baseline — broad but sometimes stale |
| TCG Pipedream reports | Excel (.xlsm) | Structured (TCG format) | Rental residential, partial LA coverage | Gold standard where available — human-verified, detailed unit mixes, affordability, status |

### 4b. Source-by-Source Analysis

#### Source 1: ZIMAS (zimas.lacity.org) — ENRICHMENT

**What it is:** The City of LA's master property information system. Contains planning case records, zoning, and building permit links for every property in the city. This is the deepest source of entitlement detail.

**Role:** Enrichment only. ZIMAS does NOT provide a downloadable list of projects. You must already know a case number or address to look anything up.

**Access methods:**
- **PDIS (case detail pages):** `planning.lacity.gov/pdiscaseinfo/Search/casenumber/{CASE_NUMBER}` — HTML pages, scrapeable. Also `planning.lacity.gov/pdiscaseinfo/numericcaseid/{NUMERIC_ID}`.
- **ArcGIS REST services:** `zimas.lacity.org/arcgis/rest/services/D_CASES_WDI_PWA/MapServer/{LAYER_ID}/query` — supports `where=CASE_NBR='...'` and returns JSON. Layers 0-4 available. Max 1000 records per query.
- **Address → case number lookup:** NOT PROGRAMMATICALLY AVAILABLE. This is ZIMAS's critical gap. The web UI does it interactively but no REST endpoint exists for reverse lookup.

**Compound's claimed fields — VERIFIED:**
| Field | Available? | Access method |
|-------|-----------|---------------|
| Approval status & appeal status | ✅ Yes | PDIS HTML scrape |
| End of appeal period | ✅ Yes | PDIS HTML scrape |
| Case filed/accepted/assigned dates | ✅ Yes | PDIS HTML scrape |
| Full project description | ✅ Yes | PDIS HTML scrape |
| Requested entitlements (full legal text) | ✅ Yes | PDIS HTML scrape |
| Applicant & representative | ✅ Yes | PDIS HTML scrape |
| Determination Letters (PDFs) | ✅ Yes | PDIS linked PDF downloads |
| Address, council district, CPA | ✅ Yes | PDIS HTML + ArcGIS query |
| Staff assigned | ✅ Yes | PDIS HTML scrape |

**Maps to DB fields:** `appeal_status`, `ceqa_status`, `entitlement_type`, `ProjectIdentifier (case_number)`, `description`, `applicant`, `planner_*` fields, `source_urls[]`

**Workflow — how the tool uses it:**
1. ZIMAS is triggered when we obtain a case number from another source (LA Case Reports, LADBS, Pipedream Site1 URLs)
2. Scrape PDIS page for that case number → extract all fields above
3. Match to existing project via address/case number
4. Update enrichment fields (entitlement detail, approval status, appeal status, planner info)
5. Download Determination Letters for archival/reference

**Can it find new projects?** No. ZIMAS is lookup-only. Discovery comes from other sources.

**Do we need to know about a project first?** Yes — we need either a case number or an address.

**Workaround for address→case:** Pipedream Site1 URLs often contain PDIS links with embedded case numbers. LADBS permits link to cases. LA Case Reports provide case numbers directly. Between these three sources, most projects will have case numbers before we ever need ZIMAS.

---

#### Source 2: LA Case Reports (planning.lacity.gov/resources/case-reports) — DISCOVERY

**What it is:** A biweekly report of every new planning case filed with the City of LA Planning Department. This is the primary DISCOVERY source — it's how we find new projects entering the entitlement pipeline.

**Role:** Discovery + Update. Provides a comprehensive feed of new filings that we can scan for pipeline-relevant projects.

**Access methods:**
- **Biweekly PDF API:** `planning.lacity.gov/dcpapi/general/biweeklycase/doc/{id}` — returns PDF. IDs are numeric (e.g., 6594 = Jan 25–Feb 7, 2026). History back to at least 2021. Also a "Cases Completed" endpoint at `planning.lacity.gov/dcpapi/general/casereports/doc/{id}`.
- **Socrata dataset — LAHD Housing Projects:** `data.lacity.org/resource/mymu-zi3s.json` — affordable housing projects 2003-present, SODA API. Fields: APN, PROJECT_NUMBER, NAME, DEVELOPMENT_STAGE, SITE_ADDRESS, SITE_UNITS, PROJECT_TOTAL_UNITS, HOUSING_TYPE, DEVELOPER, SITE_LATITUDE, SITE_LONGITUDE, etc.
- **Interactive map:** ArcGIS-based, on the case-reports page. Specific MapServer endpoint needs to be discovered via network inspection.
- **NOTE:** A dedicated "Discretionary Planning Applications" Socrata dataset reportedly exists (entitlement data 2010-present, updated monthly) but the specific dataset ID was not found in public search. Contact planning.metrics@lacity.org to request.

**Compound's claimed fields — VERIFIED (from PDF reports):**
| Field | Available? | Access method |
|-------|-----------|---------------|
| Case number | ✅ Yes | PDF parse |
| Address | ✅ Yes | PDF parse |
| Full project description | ✅ Yes | PDF parse |
| Request type(s) | ✅ Yes | PDF parse (HCA, VHCA, Density Bonus, TOC, 100% Affordable, Coastal Dev, etc.) |
| Application date | ✅ Yes | PDF parse |
| Community plan area | ✅ Yes | PDF parse (organized by council district) |
| Council district | ✅ Yes | PDF parse |
| Neighborhood council (CNC) | ✅ Yes | PDF parse |
| Coordinates (lat/lng) | ⚠️ Partial | Not in biweekly PDFs directly; available if the interactive map ArcGIS endpoint is queryable |
| Reporting period | ✅ Yes | From PDF filename/header |

**Compound's stat: "81 out of 421 cases are housing/pipeline-relevant"** — plausible. Most cases are beverage permits, signage, minor variances. Housing-relevant request types to filter for: HCA, VHCA, DB (Density Bonus), TOC, QPSH, Measure JJJ, CU (Conditional Use for residential), 100% Affordable.

**Maps to DB fields:** `ProjectIdentifier (case_number)`, `canonical_address`, `description`, `entitlement_type`, `pipeline_status` (new filing = Proposed), `lat`/`lng`/`location` (if from map), `jurisdiction`

**Workflow — how the tool uses it:**
1. Every 2 weeks: download latest biweekly PDF via API endpoint
2. Parse PDF into structured records (`pdfplumber` first; use `tabula-py` when lattice extraction is materially better)
3. Filter for housing-relevant request types
4. For each case: attempt to match against master DB by address
5. If match found → update case numbers, description, entitlement details → queue as UPDATE
6. If no match → flag as NEW PROJECT CANDIDATE → queue for researcher review
7. Chain case numbers into ZIMAS for full enrichment

**Can it find new projects?** YES — this is the primary discovery source. Every new planning application in LA appears here.

**Do we need to know about a project first?** No. This source is comprehensive — every filing appears regardless of whether we're tracking it.

---

#### Source 3: LADBS Permits (data.lacity.org) — UPDATE + DISCOVERY

**What it is:** The City of LA's building permit database. Tracks when projects move from entitlement into actual construction (permit issuance, inspections, certificates of occupancy).

**Role:** Update (construction tracking) + secondary Discovery (some projects file permits before appearing in planning records).

**Access methods:**
- **Primary Socrata dataset:** `data.lacity.org/resource/hbkd-qubn.json` — SODA API, full SoQL query support
- **New Building Permits (filtered):** `data.lacity.org/resource/ydma-y4hd.json` — pre-filtered for new construction only
- **Building Permits: New Housing Units:** `data.lacity.org/resource/cpkv-aajs.json` — specialized for housing unit counts
- **Certificate of Occupancy:** `data.lacity.org/resource/3f9m-afei.json` — tracks completion (CofO issuance)
- **Inspections by Permit Status:** `data.lacity.org/resource/2w4b-a48u.json` — construction progress tracking

**Key fields (from SODA API):**
- `permit_type` (Bldg-New, Bldg-Addition, Bldg-Alter/Repair, Bldg-Demolition)
- `permit_sub_type`, `permit_number`, `permit_status`
- Address fields (street number, direction, name, suffix)
- APN (Assessor Parcel Number)
- Tract, block, lot
- Work description
- Valuation
- Number of residential dwelling units
- Square footage/floor area
- Issue date
- Coordinates — needs verification (may require geocoding)

**SoQL filter for new residential:**
```
$where=permit_type='Bldg-New'
```

**Maps to DB fields:** `ProjectIdentifier (permit_number)`, `pipeline_status` (permit issued = Under Construction transition signal), `total_units`, `total_sf`, `canonical_address`, `ProjectIdentifier (apn)`, `date_construction_start`

**Workflow — how the tool uses it:**
1. Weekly: query SODA API for permits filed/issued since last run, filtered for Bldg-New
2. For each permit: match against master DB by APN (primary) or address (fallback)
3. If match found → update permit numbers, check for status change signals (permit issued = construction start)
4. If no match → check if it's a significant project (unit count > threshold) → flag as NEW CANDIDATE
5. Periodic: query Certificate of Occupancy dataset to detect project completion
6. Periodic: query Inspections dataset for construction progress signals

**Can it find new projects?** Yes, secondarily. A project that files a building permit without going through discretionary planning review (by-right projects) may appear here before LA Case Reports.

**Do we need to know about a project first?** No for discovery queries. The filtered "New Building Permits" dataset (ydma-y4hd) is comprehensive.

---

#### Source 4: LAHD Affordable Housing (data.lacity.org) — UPDATE + DISCOVERY

**What it is:** LA Housing Department's catalog of affordable housing projects receiving city financing (Prop HHH, ULA, Supportive Housing Program, etc.).

**Role:** Affordable pipeline tracking. Catches publicly-funded projects that may not appear prominently in other sources.

**Access methods:**
- **Socrata dataset:** `data.lacity.org/resource/mymu-zi3s.json` (LAHD Affordable Housing Projects List 2003-Present)
- Also: `data.lacity.org/resource/an7z-aq2k.json` (LAHD Affordable Housing Projects Catalog)

**Key fields:**
- APN, PROJECT_NUMBER, NAME, DEVELOPMENT_STAGE
- CONSTRUCTION_TYPE, SITE_ADDRESS, SITE_COUNCIL_DISTRICT
- SITE_UNITS, PROJECT_TOTAL_UNITS, HOUSING_TYPE
- SUPPORTIVE_HOUSING flag
- DATE_FUNDED, IN_SERVICE_DATE
- DEVELOPER, MANAGEMENT_COMPANY
- SITE_LATITUDE, SITE_LONGITUDE

**Maps to DB fields:** `affordable_type`, `affordable_units`, `total_units`, `developer`, `pipeline_status`, `date_delivery` (from IN_SERVICE_DATE), `lat`/`lng`

**Workflow:**
1. Monthly: pull full dataset (relatively small — hundreds of records)
2. Match against master DB by APN or address
3. Update affordable-specific fields (funding stage, affordable unit count, housing type)
4. Flag unmatched projects as new affordable candidates

**Can it find new projects?** Yes — publicly-funded projects may enter this dataset before other sources.

---

#### Source 5: SM Development Tracking PDF (santamonica.gov) — DISCOVERY + UPDATE

**What it is:** A comprehensive list of every project in the entitlement pipeline in Santa Monica, published as a PDF. This is the SM equivalent of LA Case Reports but far richer — it's a full project list, not just new filings.

**Role:** For Santa Monica, this IS the comprehensive pipeline list. Discovery + Update in one.

**Access methods:**
- **PDF download:** URL pattern is predictable: `santamonica.gov/media/Document%20Library/Topic%20Explainers/Planning%20Resources/{MM}.{YYYY}%20({MON})%20Development%20Tracking%20Projects%20List.pdf`
- Example: `01.2026 (JAN) Development Tracking Projects List.pdf`
- **Host page:** `santamonica.gov/status-of-development-projects`
- **Update frequency:** Published weekly (URL changes monthly)
- **Structured data on Socrata:** NOT AVAILABLE. This data exists ONLY as PDF. The Socrata portal (data.smgov.net) has permit data but not this planning-level tracking.

**Compound's claimed fields — VERIFIED (457 projects in Jan 2026 edition):**
| Field | Available? | Notes |
|-------|-----------|-------|
| Project name/address | ✅ Yes | |
| Permit number | ✅ Yes | Cross-references with ePermit and Ministerial list |
| Applicant | ✅ Yes | |
| Assigned planner | ✅ Yes | |
| File date | ✅ Yes | |
| Description (use type, stories, SF, units, parking) | ✅ Yes | Embedded in description text, needs NLP/regex extraction |
| Total square footage | ⚠️ ~59% populated | |
| Unit mix and affordability breakdown | ⚠️ ~50% populated | |
| Process status | ✅ Yes | Under Staff Review, ARB Concept Review, Approved, Withdrawn, etc. |

**Maps to DB fields:** `project_name`, `canonical_address`, `ProjectIdentifier (permit_number)`, `applicant`, `description`, `total_sf`, `total_units`, `affordable_units`, `stories`, `pipeline_status`, `parking_spaces`

**Workflow:**
1. Weekly/monthly: download latest PDF from predictable URL
2. Parse with `pdfplumber` first; fall back to `tabula-py` only if lattice extraction is materially better for a given PDF
3. Extract structured records
4. Diff against previous month's parsed records to detect: new projects, status changes, withdrawals
5. Match against master DB by address + permit number
6. Queue changes for review

**Can it find new projects?** YES — this is a comprehensive list of all entitlement-stage projects in SM.

**Do we need to know about a project first?** No. Every tracked project is listed.

---

#### Source 6: SM Ministerial Housing Applications PDF (santamonica.gov) — UPDATE

**What it is:** A list of housing projects subject to expedited/ministerial approval in Santa Monica (SB330, SB9, SB684, and lawsuit-settlement properties). Complements the Development Tracking PDF — that one has project details, this one has approval pathway status.

**Access methods:**
- **PDF download:** `santamonica.gov/media/Document%20Library/Topic%20Explainers/Santa%20Monica%27s%20Housing%20Progress/Status%20of%20Ministerial%20Housing%20Applications_{DATE}_NEW.pdf`
- **Host page:** `santamonica.gov/status-of-housing-administrative-approvals`
- **Update frequency:** Periodic (latest found: March 17, 2026 / April 8, 2026 editions)

**Compound's claimed fields — VERIFIED (108+ projects):**
| Field | Available? |
|-------|-----------|
| Address and permit number | ✅ Yes |
| Preliminary application date | ✅ Yes |
| Application type (SB330, Formal, SB9, SB684) | ✅ Yes |
| Formal application date | ✅ Yes |
| Approval date | ✅ Yes |
| Determination status | ✅ Yes |
| Plans approved status | ✅ Yes |
| Withdrawn/expired flags | ✅ Yes |

**Cross-reference strategy:** Match against SM Development Tracking PDF on Address + Permit Number. Dev Tracking gives project details; this gives approval pathway and dates.

**Maps to DB fields:** `entitlement_type` (SB330/SB9/SB684), `pipeline_status`, `status_date` (approval date), various date fields

**Workflow:**
1. Monthly: download latest PDF
2. Parse into structured records
3. Match against master DB by address + permit number
4. Update status fields — approval date, determination status, pathway type
5. Flag withdrawn/expired projects for researcher attention

---

#### Source 7: SM ePermit / Accela (epermit.smgov.net) — CONFIRMATION

**What it is:** Santa Monica's building permit database, powered by Accela Citizen Access. Used to confirm whether an entitled project is actually moving toward construction.

**Access methods:**
- **Accela REST API:** `developer.accela.com` — authenticated V4 API. Endpoints include `GET /v4/records` (all records), `GET /v4/records/{recordId}/additional` (detail), and search endpoints.
- **Authentication:** OAuth required. Register app at developer.accela.com for client ID/secret. Rate limit: 1000 requests/hour.
- **Socrata dataset (Active Permits):** `data.smgov.net/resource/kpzy-s8rg.json` — SODA API, updated daily. This is the easier path for bulk queries.
- **Inspection Schedule:** `data.smgov.net/resource/xird-2kxi.json` — daily inspection data for tracking active construction.

**Compound's claimed fields — VERIFIED:**
| Field | Available? | Source |
|-------|-----------|--------|
| Permit filed (yes/no) | ✅ Yes | Socrata or Accela |
| Permit status | ✅ Yes | Received, In Review, Review Completed, Ready to Issue, Issued, Finaled |
| Record type | ✅ Yes | Commercial, Residential, Mixed Use |
| Project description | ✅ Yes | |
| Full permit history | ✅ Yes | Accela API (more detail than Socrata) |

**Maps to DB fields:** `ProjectIdentifier (permit_number)`, `pipeline_status` (Issued = Under Construction signal), `property_type`

**Workflow:**
1. Weekly: query Socrata `kpzy-s8rg` for active permits, filtered for new construction
2. Match against master DB by address
3. Update permit status — track progression from Received → In Review → Issued → Finaled
4. Permit "Issued" = strong signal that construction is starting
5. Permit "Finaled" = project is complete (ready for CofO)

**Can it find new projects?** Rarely. By the time a building permit is filed, the project should already be in our DB from the Development Tracking PDF. But by-right projects might appear here first.

---

#### Source 8: SM Active Building Permits Map (santamonica.gov/active-building-permits) — REDUNDANT

**What it is:** An ArcGIS-based map of active building permits in Santa Monica.

**Status:** REDUNDANT. Compound confirmed that everything on this map is available through ePermit/Socrata with more detail. The SM Socrata dataset `kpzy-s8rg` (Active Building & Safety Permits) covers the same data in structured, queryable form.

**Recommendation:** Skip building a dedicated collector. Use the Socrata dataset instead.

---

#### Additional Sources (not in Compound's analysis, identified by our research)

**LA County Planning (Socrata):** `data.lacounty.gov/resource/ccmr-xemc.json` — covers unincorporated LA County. Low priority for initial POC (City of LA focus) but needed for expansion.

**CEQAnet:** `ceqanet.lci.ca.gov` — California Environmental Quality Act filings. Large projects (typically 50+ units) requiring EIR/MND appear here early, sometimes before planning applications. Monthly scrape. Collector type: custom web scraper.

**CityOfLosAngeles/planning-entitlements (GitHub):** Official city repo with ETL pipelines for entitlement data. May contain useful code patterns or undocumented endpoints.

### 4c. Source Role Summary

```
                      ┌─────────────────────────────────────────┐
                      │            SEED SOURCES                  │
                      │                                         │
                      │  CoStar (broad, all property types)     │
                      │  Pipedream (deep, residential, partial) │
                      └─────────────────┬───────────────────────┘
                                        │
                      ┌─────────────────▼───────────────────────┐
                      │          MASTER DATABASE                 │
                      └─────────────────┬───────────────────────┘
                                        │
          ┌─────────────────────────────┼─────────────────────────────┐
          │                             │                             │
  ┌───────▼─────────┐        ┌─────────▼──────────┐       ┌─────────▼──────────┐
  │   DISCOVERY      │        │    UPDATE           │       │   ENRICHMENT       │
  │ (find new)       │        │ (track existing)    │       │ (add detail)       │
  │                  │        │                     │       │                    │
  │ LA Case Reports  │        │ LADBS Permits       │       │ ZIMAS/PDIS         │
  │ SM Dev Tracking  │        │ SM ePermit/Socrata  │       │ (case detail on    │
  │ CEQAnet          │        │ SM Ministerial PDF  │       │  demand, requires  │
  │ LAHD Affordable  │        │ LAHD Affordable     │       │  case number)      │
  │ LADBS (by-right) │        │ LA CofO             │       │                    │
  └────────┬─────────┘        └──────────┬──────────┘       └──────────┬─────────┘
           │                             │                             │
           └─────────────────────────────┼─────────────────────────────┘
                                         │
                      ┌──────────────────▼──────────────────────┐
                      │          REVIEW QUEUE                    │
                      │  (researcher confirms/rejects/edits)     │
                      └─────────────────────────────────────────┘
```

### 4d. Source → Collector Type Mapping

| Source | Collector Type | Data Format | Auth Required | Bulk List? | Lookup Only? |
|--------|---------------|-------------|---------------|------------|-------------|
| LADBS Permits | `socrata` | JSON (SODA API) | No (app token recommended) | ✅ Yes | No |
| LADBS New Housing Units | `socrata` | JSON (SODA API) | No | ✅ Yes | No |
| LADBS CofO | `socrata` | JSON (SODA API) | No | ✅ Yes | No |
| LA Case Reports | `pdf_parser` | PDF (biweekly API) | No | ✅ Yes | No |
| ZIMAS/PDIS | `scraper` | HTML (PDIS pages) | No | ❌ No | ✅ Yes |
| ZIMAS ArcGIS | `arcgis` | JSON (REST query) | No | ❌ No | ✅ Yes |
| LAHD Affordable | `socrata` | JSON (SODA API) | No | ✅ Yes | No |
| SM Dev Tracking | `pdf_parser` | PDF (tabular) | No | ✅ Yes | No |
| SM Ministerial | `pdf_parser` | PDF (tabular) | No | ✅ Yes | No |
| SM ePermit/Permits | `socrata` | JSON (SODA API) | No | ✅ Yes | No |
| SM ePermit Detail | `accela` | JSON (REST API) | Yes (OAuth) | ✅ Yes | Both |
| CEQAnet | `ceqa_scraper` | HTML | No | ✅ Yes | No |
| LA County Planning | `socrata` | JSON (SODA API) | No | ✅ Yes | No |

### 4f. Verified API Field Schemas (from direct endpoint testing 2026-04-15)

**LADBS Permits (hbkd-qubn) — VERIFIED ✅**
Tested: `data.lacity.org/resource/hbkd-qubn.json?$limit=3&$where=permit_type='Bldg-New'`
Total Bldg-New records: **39,518**
```
Fields confirmed:
  pcis_permit, permit_type, permit_sub_type (e.g., "Apartment", "1 or 2 Family Dwelling"),
  initiating_office, issue_date, address_start, street_name, street_suffix, zip_code,
  work_description, valuation, of_residential_dwelling_units, of_stories,
  contractors_business_name, contractor_address, contractor_city, license,
  principal_first_name, principal_last_name, license_expiration_date,
  applicant_first_name, applicant_last_name, applicant_business_name,
  zone, council_district
```
Note: Address is decomposed (start, name, suffix, zip) — needs assembly. No lat/lng or APN in base fields visible. May need geocoding or cross-reference.

**LAHD Affordable Housing (mymu-zi3s) — VERIFIED ✅**
Tested: `data.lacity.org/resource/mymu-zi3s.json?$limit=2`
```
Fields confirmed:
  apn, project_number, name, development_stage, construction_type,
  address (full string, e.g., "101 N BOYLE AVE Los Angeles, CA 90033"),
  council_district, site_cd, site_units, project_total_units, housing_type,
  supportive_housing, sh_units_per_site, date_funded, hcidla_funded, leverage,
  tax_exempt_conduit_bond, tdc, in_service_date, developer, management_company,
  contact_phone, jobs, reporturl2 (JSON with URL + description), contract_numbers,
  date_stamp, longitude, latitude, geocoded_column (GeoJSON Point)
```
Very rich — has APN, lat/lng, developer, units, funding info. Excellent for affordable pipeline matching.

**PDIS Case Detail — VERIFIED ✅**
Tested: `planning.lacity.gov/pdiscaseinfo/Search/casenumber/DIR-2026-647-SPPC`
```
Fields confirmed (HTML scrape):
  Case Number, Case Filed On, Accepted For Review On, Assigned Date,
  Staff Assigned, Hearing Waived/Date Waived, Hearing Location, Hearing Date,
  DIR Action, DIR Action Date, End of Appeal Period, Appealed (Yes/No),
  BOE Reference Number, Case on Hold,
  Primary Address (table: Address, CNC, CD),
  "View All Addresses" link,
  Project Description, Requested Entitlement (full legal text),
  Applicant (name), Representative (name [Company: ...]),
  "View Related Cases" link,
  Approved Documents tab (Type, Scan Date, Signed),
  Initial Submittal Documents tab
```
All of Compound's claimed fields confirmed. Also has tabs for Ordinance, Zoning Information, CPC Cards, ZA Cards.

**ZIMAS ArcGIS Layer 1 (WDI PCTS) — VERIFIED ✅**
Tested: `zimas.lacity.org/arcgis/rest/services/D_CASES_WDI_PWA/MapServer/1/query?where=CASE_NBR+LIKE+'DIR-2026%'&outFields=*&f=json`
```
Fields confirmed:
  CASE_ID (Double), CASE_NBR (String/60), PIN (String/13 — parcel identifier!),
  ESRI_OID, shape (Polygon geometry in WKID 2229)
```
PIN field is a parcel identifier that can be used for matching. Supports LIKE queries, pagination, statistics. Max 1000 records per query. exceededTransferLimit flag when more results available.

**LA Case Reports Biweekly PDF — VERIFIED ✅**
Tested: `planning.lacity.gov/dcpapi/general/biweeklycase/doc/6594` (period: 01/25/2026 to 02/07/2026)
```
PDF structure (13 pages, tabular, organized by Council District):
  Filing Date, Case Number (hyperlinked to PDIS!), Address, CNC,
  Community Plan Area, Project Description, Request Type, Applicant Contact
  
  Footer: "Council District X Records: N" count per district
```
Well-structured tables with visible borders. Start with `pdfplumber`; if extraction quality is poor, this is a strong candidate for `tabula-py` lattice mode. Case number hyperlinks go directly to PDIS for enrichment. 13 pages covers all 15 council districts for one biweekly period.

**SM Dev Tracking PDF — VERIFIED ✅**
Tested: `santamonica.gov/.../01.2026 (JAN) Development Tracking Projects List.pdf`
```
PDF structure (85 pages, tabular, organized by project status category):
  Columns: #, NAME, APPLICANT, ZIP, ADDRESS/PERMIT#, FILE DATE,
           DESCRIPTION, Total SF, UNIT MIX SIZE AND AFFORDABILITY,
           PROCESS STATUS, PLANNER
  
  Categories: "PENDING AAs", and others (85 pages suggests 400+ projects)
  Description field contains: Use type, stories, SF breakdown, units, parking
  Unit Mix field contains: Studio/1BR/2BR/3BR counts + affordability breakdown
```
Very rich, well-structured. Start with `pdfplumber`; use `tabula-py` as a fallback if bordered-table extraction is materially cleaner.

**SM Ministerial Housing PDF — VERIFIED ✅**
Tested: `santamonica.gov/.../Status of Ministerial Housing Applications_4.15.26.pdf`
```
PDF structure (1 page, wide table, all projects on single page):
  Columns: Address, Date Preliminary Application Submitted,
           Submitted Preliminary Application (type — e.g., "SB330 Application"),
           Submitted Preliminary Plans, Date Formal Application Submitted,
           Submitted Formal Application, Submitted Formal Plans,
           Date Approved, Approved Entitlement, Approved Plans
  
  Section: "Administrative Approvals"
  Application types visible: SB330 Application, Formal Application
```
Single-page table — easy parse. Cross-references with Dev Tracking PDF on Address + Permit Number.

**SM Socrata (data.smgov.net) — NOT VERIFIED ⚠️**
Dataset kpzy-s8rg ("Active Building & Safety Permits") returned error pages during testing. Site may have been temporarily down. The dataset is documented on the Socrata portal and has been confirmed via web search. Will verify at build time.

### 4e. Case Number Chain

Case numbers are the critical linking key between sources. Here's how they flow:

```
LA Case Reports (biweekly PDF)
    ↓ case number
ZIMAS/PDIS (full case detail)  ←── also reachable from Pipedream Site1 URLs
    ↓ address/APN
LADBS Permits (building permit status)
    ↓ permit number
LADBS CofO (completion confirmation)
```

For Santa Monica, the chain is:
```
SM Dev Tracking PDF (permit number + address)
    ↓ address + permit number
SM Ministerial PDF (approval pathway + dates)
    ↓ address
SM ePermit/Socrata (building permit status)
```

---

## 5. Collection Workflow

> How the system uses its sources to maintain the pipeline. This is the runtime loop that runs after initial seeding.

### 5a. Two Patterns: Pull vs. Lookup

The system uses two complementary patterns:

**Source-first ("pull")** is the primary pattern. Most sources — LADBS, LA Case Reports, LAHD, the SM PDFs — publish comprehensive lists. We pull everything new since the last run, then match it against our database. This is the only way to find projects we don't know about yet, and it's far more efficient than looking up 1,000+ projects individually.

**Database-first ("lookup")** is the secondary pattern, used only for ZIMAS/PDIS. ZIMAS has no bulk export — you need a case number to look anything up. So we enrich known projects on demand, triggered when a new case number arrives from a pull source.

### 5b. Execution Order

Each collection cycle runs four steps in order. Steps 1-2 pull from sources; Step 3 looks up known projects; Step 4 generates output.

#### Step 1: Discovery Sweep — Find New Projects

Run the "comprehensive list" sources and diff against our database. These are the sources that can surface projects we've never seen.

| Priority | Source | What it catches | Frequency | Notes |
|----------|--------|----------------|-----------|-------|
| **1** | LA Case Reports (biweekly PDF) | Every new planning case in LA | Biweekly | Primary discovery source. Parse PDF → filter for housing request types (HCA, VHCA, DB, TOC, QPSH, 100% Affordable) → match against DB. Unmatched = new candidates. Produces case numbers for Step 3. |
| **2** | LADBS New Building Permits (ydma-y4hd) | By-right projects that skip discretionary review | Weekly | These never appear in Case Reports. Filter for `permit_type='Bldg-New'` with residential subtypes. |
| **3** | LAHD Affordable Housing (mymu-zi3s) | Publicly-funded projects entering the pipeline | Monthly | Small dataset, rich fields. Match on APN (available in this dataset) or address. |
| **4** | SM Dev Tracking PDF | All entitlement-stage projects in Santa Monica | Monthly | Comprehensive — parse current month, diff against previous month to detect new projects, status changes, and withdrawals. |
| **5** | CEQAnet | Large projects (typically 50+ units) in EIR phase | Monthly | Early warning. These may appear here before any planning application is filed. |

**Why this order:** Case Reports runs first because it's the richest discovery source and produces case numbers we chain into ZIMAS in Step 3. LADBS second because it catches the by-right projects Case Reports misses. LAHD third because it's a smaller, specialized set. SM sources run independently of LA sources and can execute in parallel.

#### Step 2: Status Updates — Track Existing Projects

Run sources that reveal whether known projects have progressed through the pipeline.

| Source | Signal detected | Maps to status change | Frequency |
|--------|----------------|----------------------|-----------|
| LADBS Permits (hbkd-qubn) | Permit issued for a tracked project | Approved → Under Construction | Weekly |
| LADBS CofO (3f9m-afei) | Certificate of Occupancy issued | Under Construction → Complete | Weekly |
| SM ePermit/Socrata (kpzy-s8rg) | Permit status moves (In Review → Issued → Finaled) | Tracks SM construction progression | Weekly |
| SM Ministerial PDF | Approval date populated, plans approved | Pending → Approved | Monthly |
| LA Case Reports (same data from Step 1) | Existing case appears with new request types or status indicators | Various — depends on case action | Biweekly |

**Key principle:** We don't re-query sources just for Step 2. Steps 1 and 2 operate on the same pulled data — we just process it differently. Step 1 looks for unmatched records (new projects). Step 2 looks for matched records with changed fields (status updates).

#### Step 3: Enrichment — Deepen Known Projects

This is the database-first step. For any project that gained a new case number in Steps 1-2, or any project flagged as needing enrichment:

| Source | Trigger | What it adds |
|--------|---------|-------------|
| ZIMAS/PDIS (HTML scrape) | New case number discovered | Approval status, appeal status, end of appeal period, determination letters, full entitlement text, applicant/representative, assigned planner |
| ZIMAS ArcGIS (Layer 1 query) | New case number discovered | PIN (parcel number) for improved future matching, case geometry |

**How it works:**
1. Collect all case numbers newly linked to projects in Steps 1-2
2. For each, scrape the PDIS page: `planning.lacity.gov/pdiscaseinfo/Search/casenumber/{CASE_NBR}`
3. Extract structured fields from HTML
4. Also query ZIMAS ArcGIS to grab PIN: `zimas.lacity.org/arcgis/rest/services/D_CASES_WDI_PWA/MapServer/1/query?where=CASE_NBR='{CASE_NBR}'&outFields=*&f=json`
5. Update project record with enrichment fields

**Rate limiting:** PDIS is a city website, not an API. Scrape politely — 1-2 second delays between requests. Batch enrichment during off-peak hours.

#### Step 4: Generate Review Queue

Everything from Steps 1-3 that represents a change gets queued for researcher review, prioritized by type:

| Priority | Queue item type | Example | Researcher action |
|----------|----------------|---------|-------------------|
| **High** | New project candidate | Unmatched case from LA Case Reports with 200+ units | Confirm or dismiss. If confirmed, fills in any missing fields. |
| **High** | Major status change | Permit issued → project moving to Under Construction | Verify, update delivery date estimate |
| **Medium** | Field update on existing project | Developer changed, unit count revised, new case number linked | Review and accept/reject |
| **Medium** | Ambiguous match | Address fuzzy-matches an existing project at 85% confidence | Confirm match or mark as separate project |
| **Low** | Minor enrichment | Planner name updated, project description refined | Auto-accept or batch-review |

### 5c. First Run After Seeding (Special Case)

The very first collection cycle after seeding a market is different from subsequent runs:

1. **Seed database** with CoStar + Pipedream (per Phase 1 build plan)
2. **Run full discovery sweep** — this will match a lot of existing projects against public records for the first time
3. **The first run will produce a large initial enrichment batch** — hundreds of projects getting case numbers, permit numbers, and ZIMAS detail linked for the first time
4. **Researcher reviews the initial batch** — confirms matches, dismisses false positives, fills gaps
5. **Subsequent runs are incremental** — only new filings and status changes since last run

### 5d. Cycle Timing

The overall pipeline cadence is driven by LA Case Reports (biweekly publication):

```
Week 1:  LA Case Reports PDF published → Steps 1-4 run (full cycle)
         LADBS weekly pull (Steps 1-2 only)
         SM Socrata weekly pull (Step 2 only)

Week 2:  LADBS weekly pull (Steps 1-2 only)
         SM Socrata weekly pull (Step 2 only)

Week 3:  LA Case Reports PDF published → Steps 1-4 run (full cycle)
         LADBS weekly pull
         SM Socrata weekly pull
         Monthly sources: LAHD, SM Dev Tracking PDF, SM Ministerial PDF, CEQAnet (Steps 1-2)

[repeat]
```

**Enrichment (Step 3)** runs on-demand whenever new case numbers appear, which typically coincides with the biweekly full cycle.

**Review queue (Step 4)** is generated and persisted after every source run. Researchers can work the queue at their own pace — high-priority items surface to the top.

### 5e. Workflow Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                    COLLECTION CYCLE (biweekly)                       │
│                                                                     │
│  ┌─── STEP 1: DISCOVERY ──────────────────────────────────────┐    │
│  │                                                             │    │
│  │  LA Case Reports ──┐                                       │    │
│  │  LADBS New Bldg ───┤── parse/query → match against DB ──┐  │    │
│  │  LAHD Affordable ──┤                                     │  │    │
│  │  SM Dev Tracking ──┤     ┌──────────────┐                │  │    │
│  │  CEQAnet ──────────┘     │ UNMATCHED →  │──→ NEW PROJECT │  │    │
│  │                          │   CANDIDATES │    QUEUE       │  │    │
│  └──────────────────────────┴──────────────┴────────────────┘  │    │
│                                                     │              │
│  ┌─── STEP 2: STATUS UPDATES ─────────────────────────────────┐    │
│  │                                                             │    │
│  │  Same pulled data from Step 1, plus:                       │    │
│  │  LADBS Permits (full) ──┐                                  │    │
│  │  LADBS CofO ────────────┤── match against DB ──→ MATCHED  │    │
│  │  SM ePermit/Socrata ────┤       with changed    RECORDS   │    │
│  │  SM Ministerial PDF ────┘       fields          QUEUE     │    │
│  │                                                             │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                      │                              │
│                            new case numbers                         │
│                                      │                              │
│  ┌─── STEP 3: ENRICHMENT ───────────▼─────────────────────────┐    │
│  │                                                             │    │
│  │  For each new case number:                                 │    │
│  │    ZIMAS/PDIS ──→ scrape case detail page                  │    │
│  │    ZIMAS ArcGIS ──→ query for PIN (parcel)                 │    │
│  │                                                             │    │
│  │  Updates: approval status, appeal status, entitlement      │    │
│  │  text, planner info, determination letters, PIN            │    │
│  │                                                             │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                      │                              │
│  ┌─── STEP 4: REVIEW QUEUE ─────────▼─────────────────────────┐    │
│  │                                                             │    │
│  │  HIGH:   New candidates, major status changes              │    │
│  │  MEDIUM: Field updates, ambiguous matches                  │    │
│  │  LOW:    Minor enrichment (auto-acceptable)                │    │
│  │                                                             │    │
│  │  → Researcher works queue → confirms/rejects/edits         │    │
│  │  → Dismissed records go to DismissedRecords table          │    │
│  │  → Confirmed changes update master DB                      │    │
│  │                                                             │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 6. Matching Strategy

> How the system determines whether a record from a public source refers to an existing project in the database or a new project. This is one of the most critical components — real-world projects change addresses, names, unit counts, developers, and product types throughout the development lifecycle.

### 6a. The Core Problem

The same real-world project can look very different across sources and over time:

```
Source A (CoStar seed):       "1437 7th Street" — 68 units — BCM 1437 7th Street LLC
Source B (Case Reports):      "1437 7th St" — CPC-2024-1234-DB — mixed-use, 65 units
Source C (SM Dev Tracking):   "BCM 7th Street Project" — 1437 7th Street — 70 units — Pending
Source D (3 months later):    "The Stella" — 1435-1441 7th St — 72 units — Approved
```

A naive exact-address matcher misses that these are all the same project. The matcher must be resilient to address variations, name changes, evolving unit counts, and records that start with minimal detail and gain specificity over time.

### 6b. Tiered Matching (in execution order)

Matching runs in tiers from highest to lowest confidence. Each tier is cheaper/faster than the next. If a tier produces a definitive match, skip the remaining tiers.

#### Tier 1: Deterministic Keys (confidence: 0.95+)

Exact-match identifiers that definitively link records. When these match, there's no ambiguity.

| Key | Sources that provide it | Coverage | Notes |
|-----|------------------------|----------|-------|
| **APN / Parcel Number** | CoStar (92%), LAHD (100%), ZIMAS PIN | Best single key | Parcel doesn't change even when address/name/developer do. Large projects may span multiple parcels — store as `ProjectIdentifier` rows and match on any overlap. |
| **Case Number** | LA Case Reports, ZIMAS/PDIS | LA projects with planning cases | Permanent identifier. Once linked, never ambiguous. |
| **Permit Number** | LADBS, SM ePermit/Socrata, SM Dev Tracking, SM Ministerial | Projects with building permits | Links permit records to projects definitively. |
| **CoStar PropertyID** | CoStar seed | Seeded projects only | Internal CoStar ID — stable across exports. |
| **Pipedream ProjectID** | TCG Pipedream seed | Seeded projects only | e.g., "23.00001" — stable within TCG system. |

**Implementation:** For each incoming record, check all available deterministic keys against the DB index. Any single key match = confirmed match. Store all deterministic keys in `ProjectIdentifier` rows so future records can match on any of them without bloating the `Project` row.

#### Tier 2: Normalized Address Match (confidence: 0.85-0.92)

Most matching will happen here, especially for sources that lack APN.

**Address normalization pipeline:**
```
Raw input: "1437 7TH ST, APT 2B, Santa Monica, CA 90401"
                           │
Step 1: Parse with usaddress  →  {number: "1437", street: "7TH", suffix: "ST",
                                   unit: "APT 2B", city: "Santa Monica", ...}
Step 2: Standardize directionals  →  N→NORTH, S→SOUTH, E→EAST, W→WEST
Step 3: Standardize suffixes     →  ST→STREET, AVE→AVENUE, BLVD→BOULEVARD, etc.
Step 4: Strip unit/suite/apt     →  Remove unit designator (store separately)
Step 5: Uppercase, trim           →  "1437 7TH STREET"
Step 6: Generate canonical form   →  "1437 7TH STREET SANTA MONICA CA 90401"
```

**Match types within Tier 2:**

| Match type | Confidence | Example |
|-----------|-----------|---------|
| Exact normalized address | 0.92 | "1437 7TH STREET" = "1437 7TH STREET" |
| Address range overlap | 0.87 | "1435-1441 7TH STREET" overlaps "1437 7TH STREET" |
| Address with unit variance | 0.85 | "1437 7TH STREET" vs "1437 7TH STREET UNIT A" |

**Address range handling:** Parse hyphenated address numbers (1435-1441) into a range. If any existing project's address number falls within that range on the same street, flag as match candidate. This catches the common pattern where a project acquires adjacent parcels and the address range expands.

**LA-specific normalization rules:**
- "Los Angeles CBD" / "Downtown Los Angeles" / "DTLA" → "Los Angeles"
- Handle directional streets: "N Las Palmas Ave" — the "N" is part of the street name, not a prefix
- Boulevard abbreviations: "W Sunset Blvd" is standard Pipedream format
- Numbered streets: "7th" = "7TH" = "SEVENTH"

#### Tier 3: Geocoding + Proximity (confidence: 0.60-0.82)

Solves two critical problems: projects with no address yet, and projects whose address changed.

**Geocoding sources (in priority order):**
1. Coordinates already in source data (CoStar 98% coverage, LAHD has geocoded_column, Case Reports map)
2. APN → centroid lookup via county assessor or ZIMAS geometry
3. Address → geocode via geocoding service (Census, Google, or LA city geocoder)

**Proximity matching rules:**

| Distance | Confidence (alone) | With 1 corroborating field | Interpretation |
|----------|-------------------|---------------------------|----------------|
| < 30m | 0.75 | 0.88 | Almost certainly same site |
| 30-75m | 0.60 | 0.80 | Likely same project, could be adjacent parcel |
| 75-150m | 0.40 | 0.65 | Possible — flag for review if corroborating fields |
| > 150m | — | — | Not a proximity match |

**Why proximity alone isn't sufficient:** Two unrelated projects can be 20 meters apart (adjacent lots). Proximity must always be combined with corroborating evidence from Tier 4 fields. Proximity < 30m + matching developer = near-certain same project. Proximity < 30m + completely different developer/use type = likely different projects.

**Geocode storage:** Store coordinates on every project record. For projects without coordinates, attempt geocoding from whatever address/APN we have. Flag as `geocode_confidence: high|medium|low|none` based on source. Re-geocode when address is updated.

#### Tier 4: Fuzzy Field Matching (confidence: varies, used to corroborate)

These fields are never sufficient alone for matching. They corroborate Tier 2/3 matches or resolve ambiguous cases.

| Field | Match method | Weight | Notes |
|-------|-------------|--------|-------|
| **Developer name** | Fuzzy string (Levenshtein, token set ratio) | High | "BCM 1437 7th Street LLC" ↔ "BCM Development" — should match. Entity names often vary. |
| **Applicant name** | Fuzzy string | Medium | Cross-references between Case Reports and SM Dev Tracking |
| **Unit count** | Numeric similarity (within ±25%) | Medium | Counts change through development but large discrepancies suggest different projects |
| **Stories/height** | Exact or ±1 | Medium | Relatively stable signal — a 7-story project rarely becomes 3 stories |
| **Project description** | Keyword/NLP overlap | Medium | "7-story mixed-use, 40 residential units, retail" ↔ "Mixed-use 7 stories 40 units ground floor commercial" |
| **Property/use type** | Categorical match | Low-Medium | Residential ↔ Residential is corroborating; Residential ↔ Industrial suggests different projects |
| **Project name** | Fuzzy string | Low | Names change frequently — "BCM 7th Street Project" → "The Stella". Useful when it matches, uninformative when it doesn't. |

**Fuzzy string matching:** Use `rapidfuzz` library (Python, faster than `fuzzywuzzy`). Token set ratio handles word order and partial matches well: `fuzz.token_set_ratio("BCM 1437 7th Street LLC", "BCM Development LLC")` → high score because shared tokens. Threshold: > 75 = plausible match, > 85 = strong match.

#### Tier 5: Composite Confidence Score

For non-deterministic matches, compute a weighted composite score:

```python
def compute_match_confidence(existing, incoming):
    score = 0.0

    # Tier 1: Deterministic (any one is sufficient)
    if apn_match(existing, incoming):           return 0.95
    if case_number_match(existing, incoming):   return 0.97
    if permit_number_match(existing, incoming): return 0.97

    # Tier 2: Address
    addr_score = normalized_address_similarity(existing, incoming)
    if addr_score == 1.0:                       score = max(score, 0.92)
    elif address_range_overlap(existing, incoming): score = max(score, 0.87)
    elif addr_score > 0.85:                     score = max(score, 0.80)

    # Tier 3: Proximity
    distance = haversine(existing.lat_lng, incoming.lat_lng)
    if distance < 30:   prox_score = 0.75
    elif distance < 75:  prox_score = 0.60
    elif distance < 150: prox_score = 0.40
    else:                prox_score = 0.0

    # Tier 4: Corroborating fields
    corr_count = 0
    corr_boost = 0.0
    if developer_fuzzy_match(existing, incoming) > 0.75:
        corr_count += 1; corr_boost += 0.10
    if abs(existing.units - incoming.units) / max(existing.units, 1) < 0.25:
        corr_count += 1; corr_boost += 0.06
    if existing.stories and incoming.stories and abs(existing.stories - incoming.stories) <= 1:
        corr_count += 1; corr_boost += 0.06
    if description_overlap(existing, incoming) > 0.5:
        corr_count += 1; corr_boost += 0.05

    # Combine proximity + corroboration
    if prox_score > 0 and corr_count > 0:
        score = max(score, prox_score + corr_boost)

    return min(score, 0.94)  # cap below deterministic threshold
```

**Decision thresholds:**

| Score | Action |
|-------|--------|
| ≥ 0.85 | **Auto-match.** Link records, queue field changes for review. |
| 0.65 – 0.84 | **Ambiguous match.** Surface to researcher as "possible match — confirm or reject." Show both records side by side. |
| 0.40 – 0.64 | **Weak signal.** Treat as new project candidate but note the potential link. Researcher decides. |
| < 0.40 | **No match.** Treat as new project candidate. |

### 6c. Handling Projects That Change Over Time

**Principle: match conservatively, track liberally.**

When the matcher links a source record to a project, it stores the source's original values alongside the canonical values. When the same source later reports different values (new address, changed unit count, different project name), that's not a matching failure — it's a **field change** that flows through the differ and into the review queue.

**Previous values are never discarded.** The ChangeLog table captures every field change with old value, new value, source, and timestamp. This creates a complete development history:

```
Project: "The Stella" (1435-1441 7th St, Santa Monica)
  2024-01: CoStar seed → "1437 7th Street", 68 units, "BCM 1437 7th St LLC"
  2024-03: SM Dev Tracking → address now "1437 7th Street", 65 units, status "Under Staff Review"
  2024-06: Case Reports → case CPC-2024-1234-DB filed, description says 70 units
  2024-09: SM Dev Tracking → address now "1435-1441 7th St", 72 units, name "The Stella"
  2025-01: SM Ministerial → SB330 application submitted, approval date populated
  2025-04: SM ePermit → building permit issued
```

**Stored aliases:** The `raw_addresses` array and `previous_names` field accumulate every variant the project has been known by. Future matching checks all aliases, not just the current canonical address.

### 6d. Unlocated Candidates

Some very-early-stage records lack enough detail to match or geolocate — e.g., a CEQAnet filing that says "Proposed 300-unit development, Hollywood area" with no specific address.

**Strategy:**
1. Store as `unlocated_candidate` with whatever fields are available (description, developer, neighborhood, unit count)
2. Each collection cycle, re-run matching against the unlocated pool — a Case Reports filing two weeks later may provide the address
3. If still unlocated after 3 cycles, surface to researcher as "early-stage project, needs manual investigation"
4. Researcher can: (a) link it to a known project, (b) create a new project with partial data, or (c) dismiss it

### 6e. Deduplication Within Sources

Before cross-source matching, handle within-source duplicates:

- **CoStar:** Dedup across multiple exports on PropertyID (same project may appear in both MF and non-MF exports)
- **SM Dev Tracking PDF:** Nate noted duplicates from modifications — dedup on Address + Permit Number, keep latest status
- **LADBS:** Multiple permits per project (demolition, grading, foundation, building) — group by address/APN, treat as one project
- **LA Case Reports:** Same project may file multiple cases (CPC for entitlements, ENV for CEQA) — group by address, link all case numbers

### 6f. Match Index

For performance, maintain an in-memory or database index keyed on matchable fields sourced from `Project`, `ProjectIdentifier`, and address aliases:

```
MatchIndex:
  apn_index:           dict[str, project_id]       — APN → project (also stores ZIMAS PIN)
  case_number_index:   dict[str, project_id]       — case number → project
  permit_number_index: dict[str, project_id]       — permit number → project
  address_index:       dict[str, project_id]       — normalized canonical address → project
  alias_address_index: dict[str, project_id]       — all raw_addresses entries → project
  geo_index:           spatial index (R-tree)       — for proximity queries
  costar_property_id_index: dict[str, project_id]
  tcg_pipedream_id_index:   dict[str, project_id]
```

On each incoming record:
1. Check deterministic indexes (APN, case #, permit #, source IDs) — O(1) each
2. Check address index + alias index — O(1)
3. If no hit, geocode and query geo_index for nearby projects — O(log n)
4. If proximity candidates found, run Tier 4 fuzzy comparison — O(k) where k = nearby candidates
5. Compute composite score, apply threshold, queue result

---

## 7. Build Plan

### Phase 1: Foundation
> Goal: Database schema, seeder, and first public source connected.

| Step | Task | Status | Notes |
|------|------|--------|-------|
| 1.1 | Analyze CoStar export fields and format | **`done`** | 287 cols, 1000 rows (772 MF + 228 non-MF). Dual column layout detected. Full spec in Sections 3b-3c. |
| 1.2 | Analyze Pipedream report fields and format | **`done`** | Hollywood/Los Feliz file analyzed. 81 fields mapped. Full spec in Sections 3a-3b. Remaining 2 files to be analyzed at seed time (same format expected). |
| 1.3 | Finalize master database schema | **`done`** | Schema in 3e incorporates both Pipedream and CoStar fields. Ready to implement as SQLAlchemy models. |
| 1.4 | Set up project structure (Python, deps, config) + create new Supabase project | `done` | Supabase project created (`pipe-agent-ii`). Repo scaffold, `.env`, package config, SQLAlchemy models, local `.venv`, and Alembic environment are in place. Initial Alembic migration applied successfully to Supabase using the session pooler connection. `postgis` and `pg_trgm` enabled. |
| 1.5 | Build address normalization module | `done` | Implemented in `src/tcg_pipeline/matching/normalizer.py` with `usaddress` parsing, unit stripping, suffix/directional normalization, numbered-street normalization, address-range parsing, and initial LA city alias handling. Covered by targeted pytest cases. |
| 1.6 | Build Pipedream ingester (seeder) | `not_started` | Build the higher-priority truth source first. |
| 1.7 | Build CoStar ingester (seeder) | `not_started` | |
| 1.8 | Seed LA market with CoStar + Pipedream data | `not_started` | First real data in the system |

### Phase 2: First Public Source + Matching
> Goal: LADBS permits flowing in and matching against seeded data.

| Step | Task | Status | Notes |
|------|------|--------|-------|
| 2.1 | Build Socrata collector (generic) | `not_started` | Reusable for LADBS, LAHD, LA County, SM permits. Accepts dataset ID + SoQL filter + field mapping. |
| 2.2 | Configure LADBS permit source (hbkd-qubn) | `not_started` | Also register ydma-y4hd (new buildings) and cpkv-aajs (new housing units) as supplementary |
| 2.3 | Build matcher (address normalization + matching logic) | `not_started` | APN match first, address fallback. See Section 4e for case number chaining. |
| 2.4 | Build differ (change detection + priority assignment) | `not_started` | |
| 2.5 | Persist review items + decisions in the database | `not_started` | Review state is core system state, even if the first researcher surface is Excel. |
| 2.6 | Run first LADBS collection + match/diff cycle | `not_started` | Validate the whole pipeline |
| 2.7 | Review results, tune matching thresholds | `not_started` | Iterative |

### Phase 3: Discovery + Enrichment
> Goal: Finding new projects and enriching known ones.

| Step | Task | Status | Notes |
|------|------|--------|-------|
| 3.1 | Build PDF parsing utilities + source-specific adapters | `not_started` | Shared PDF helpers are reusable, but each source still gets its own parser logic. Start with `pdfplumber`; use `tabula-py` only where it clearly performs better. |
| 3.2 | Configure LA Case Reports source | `not_started` | Biweekly PDF API: planning.lacity.gov/dcpapi/general/biweeklycase/doc/{id}. Filter for housing-relevant request types. |
| 3.3 | Build ZIMAS/PDIS scraper (enrichment mode) | `not_started` | Accepts case number → scrapes PDIS page → returns structured fields. NOT for bulk discovery. |
| 3.4 | Wire up case number chaining (Case Reports → ZIMAS) | `not_started` | Also chain from Pipedream Site1 URLs and LADBS permits. |
| 3.5 | Run discovery cycle, review new project candidates | `not_started` | |

### Phase 4: Researcher Interface + Remaining Sources
> Goal: Researcher-ready workflow, full LA source coverage.

| Step | Task | Status | Notes |
|------|------|--------|-------|
| 4.1 | Build review queue surface (Excel export or minimal web UI) | `not_started` | The underlying review tables already exist by this phase; this step is about researcher-facing delivery. |
| 4.2 | Add LAHD affordable housing collector | `not_started` | Socrata mymu-zi3s / an7z-aq2k — reuses Socrata collector |
| 4.3 | Add LA County planning collector | `not_started` | Socrata ccmr-xemc — reuses Socrata collector |
| 4.4 | Add LADBS CofO collector (3f9m-afei) | `not_started` | Completion detection. Reuses Socrata collector. |
| 4.5 | Add CEQAnet collector | `not_started` | Custom scraper — early warning for large projects |
| 4.6 | Full end-to-end test: seed → collect → match → diff → review | `not_started` | |

### Phase 5: Santa Monica Market
> Goal: Prove multi-market by standing up SM with its distinct source types (PDF-heavy + Socrata + Accela).

| Step | Task | Status | Notes |
|------|------|--------|-------|
| 5.1 | Write SM market config | `not_started` | |
| 5.2 | Configure SM Dev Tracking PDF collector | `not_started` | Reuses PDF parser. Predictable monthly URL pattern. |
| 5.3 | Configure SM Ministerial PDF collector | `not_started` | Cross-ref with Dev Tracking on address + permit number |
| 5.4 | Configure SM Active Permits (Socrata kpzy-s8rg) | `not_started` | Reuses Socrata collector |
| 5.5 | (Optional) Build Accela collector for SM ePermit detail | `not_started` | OAuth required. Lower priority — Socrata covers most needs. |
| 5.6 | Seed SM with CoStar + run full cycle | `not_started` | |

### Phase 6: Generalize for Additional Markets
> Goal: Template for standing up any new market quickly.

| Step | Task | Status | Notes |
|------|------|--------|-------|
| 6.1 | Document market onboarding process | `not_started` | What sources does a new market need? How to configure? |
| 6.2 | Build market config template + validation | `not_started` | |
| 6.3 | Select third market (different metro area) | `not_started` | True generalization test |
| 6.4 | Stand up third market | `not_started` | |

---

## 8. Decision Log

> Record significant architectural and implementation decisions here so future sessions don't relitigate them.

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-04-15 | CoStar + Pipedream as seed sources, public data for updates/discovery | CoStar gives breadth, Pipedream gives depth. Public sources fill gaps and keep things current. |
| 2026-04-15 | Pipedream/TCG data takes priority over CoStar where both exist | Human-verified data is more reliable than CoStar's automated collection |
| 2026-04-15 | Researcher overrides are protected from automated updates | Prevents the system from clobbering human judgment |
| 2026-04-15 | Start with LA as proof of concept, design for multi-market | Validates architecture on a real market without over-engineering |
| 2026-04-15 | ~~SQLite for MVP database, upgrade path to Postgres~~ **SUPERSEDED** — Supabase (PostgreSQL) from day one | PostgreSQL from the start avoids migration pain later. Supabase adds hosted DB, PostGIS for spatial queries, REST API, auth, realtime, and a dashboard. Create a new Supabase project during initial setup. |
| 2026-04-15 | Build in Claude Code, not Cowork | This is a multi-file software project that needs persistent state, version control, and iterative development |
| 2026-04-15 | Living architecture doc (this file) updated with each meaningful commit | Keeps the project's "memory" in sync with reality |
| 2026-04-15 | Pipedream status values adopted as canonical status enum | Pipedream statuses are well-defined and used by researchers daily. No reason to invent new ones. Public source statuses get mapped to these. |
| 2026-04-15 | Use StatusHistory table instead of fixed PStat1-6 columns | Pipedream's 6-slot limit is artificial. Unbounded history is better for long-lived projects. |
| 2026-04-15 | DismissedRecords table to prevent re-discovery of rejected projects | Pipedream has 18 "Delete-*" records in this file alone. Without tracking these, public sources would keep suggesting them as new projects. |
| 2026-04-15 | "--" is Pipedream's null sentinel | Must be treated as null/empty during import. Consistent across all fields. |
| 2026-04-15 | Pipedream addresses are the initial address normalization benchmark | Addresses like "5939 W Sunset Blvd" and "1718 N Las Palmas Ave" define the formatting patterns we need to handle. |
| 2026-04-15 | Filter Pipedream import to City == "Los Angeles" for LA POC | Pipedream files cover broader areas (West Hollywood, Glendale/Burbank). Those cities are separate markets. |
| 2026-04-15 | Scope includes all development types, not just residential | Pipeline tracks rental, for-sale, and commercial development. Pipedream is residential-focused but the system should accept commercial projects from CoStar and public sources. |
| 2026-04-15 | CoStar ingested after Pipedream; fills gaps only, never overwrites | Preserves researcher-verified Pipedream data. CoStar's key unique contributions: APN (92%), zoning, owner, total SF, style, construction start date. |
| 2026-04-15 | APN is the primary match key for CoStar ↔ Pipedream dedup | CoStar has 92% APN coverage vs. Pipedream's 1%. APN is more reliable than address matching. |
| 2026-04-15 | CoStar ingester maps by header name, not column number | MF and non-MF exports have completely different column layouts (233 of 287 columns shift). Header-name mapping handles any export type cleanly and is robust against future CoStar format changes. |
| 2026-04-15 | CoStar seeder accepts a folder of export files, not one stitched file | CoStar caps exports at 500 rows. Each file is read independently with its own header mapping. Dedup across files on PropertyID. |
| 2026-04-15 | CoStar "Abandoned" projects imported as Inactive, not actively tracked | 39% of CoStar export is Abandoned. Useful as history but shouldn't generate review queue noise. |
| 2026-04-15 | CoStar bed mix percentages are 0-100 scale; Pipedream uses 0-1 | Normalize to 0-1 on import. Store as 0.0-1.0 in database. |
| 2026-04-15 | Source workflow analysis complete — all 6 Compound sources verified | All fields Compound claimed are confirmed extractable. Detailed access methods, endpoints, and workflows documented in Section 4b. |
| 2026-04-15 | ZIMAS is enrichment-only, not discovery | No programmatic address→case lookup exists. Case numbers must come from other sources (LA Case Reports, LADBS, Pipedream URLs). PDIS pages are scrapeable once case number is known. |
| 2026-04-15 | LA Case Reports is the primary LA discovery source | Biweekly PDFs via API at planning.lacity.gov/dcpapi/general/biweeklycase/doc/{id}. Must be PDF-parsed; no structured API. ~81/421 cases are housing-relevant per biweekly period. |
| 2026-04-15 | SM Dev Tracking PDF is SM's comprehensive pipeline list | 457 projects, published weekly as PDF. Not available as structured data on Socrata. Start with `pdfplumber`; use `tabula-py` only if extraction quality is materially better. Predictable URL pattern enables automated download. |
| 2026-04-15 | SM Active Building Permits Map is redundant with SM Socrata permits | Compound confirmed. Skip dedicated collector — use Socrata dataset kpzy-s8rg instead. |
| 2026-04-15 | SM ePermit has both Accela API and Socrata access | Socrata kpzy-s8rg (daily, no auth) is sufficient for most needs. Accela V4 API (OAuth, 1000 req/hr) available for deeper permit history if needed. Start with Socrata. |
| 2026-04-15 | Three collector types cover all sources | Socrata collector (LADBS, LAHD, LA County, SM permits), PDF parser collector (LA Case Reports, SM Dev Tracking, SM Ministerial), HTML scraper (ZIMAS/PDIS, CEQAnet). Accela collector is optional/Phase 5. |
| 2026-04-15 | Multiple LADBS Socrata datasets available | Primary: hbkd-qubn (all permits). Supplementary: ydma-y4hd (new buildings only), cpkv-aajs (new housing units), 3f9m-afei (Certificate of Occupancy), 2w4b-a48u (inspections). All reuse same Socrata collector. |
| 2026-04-15 | LAHD affordable dataset ID confirmed: mymu-zi3s | Also an7z-aq2k (catalog). Contains APN, project name, developer, units, lat/lng, development stage, funding date. Monthly update. |
| 2026-04-15 | Santa Monica is Phase 5, not Phase 6 | SM sources are already analyzed and well-understood. It's the natural second market — tests PDF parsing and multi-source cross-referencing. A third market (different metro) is the true generalization test. |
| 2026-04-15 | Collection workflow: source-first pull + database-first enrichment | Most sources publish comprehensive lists — pull everything new, match against DB. ZIMAS is the exception (lookup-only, needs case number). Full workflow documented in Section 5. |
| 2026-04-15 | Four-step execution order: Discovery → Status Updates → Enrichment → Review Queue | Discovery finds new projects, Status Updates tracks existing ones, Enrichment deepens via ZIMAS (triggered by new case numbers), Review Queue prioritizes researcher attention. |
| 2026-04-15 | LA Case Reports is the primary discovery source, drives biweekly cadence | Every new planning case appears here. Produces case numbers for ZIMAS chaining. Biweekly publication sets the overall pipeline rhythm. |
| 2026-04-15 | Steps 1 and 2 share pulled data — single pull, dual processing | Don't query the same source twice. Pull once, then process for both unmatched records (discovery) and matched records with changes (status updates). |
| 2026-04-15 | First run after seeding is a special full-linkage cycle | Initial run links hundreds of seeded projects to public records for the first time. Produces large enrichment batch. Subsequent runs are incremental. |
| 2026-04-15 | Five-tier matching strategy: deterministic keys → normalized address → geocoding/proximity → fuzzy fields → composite score | Real-world projects change addresses, names, unit counts, and developers over time. Single-field matching fails. Tiered approach handles this gracefully. Full spec in Section 6. |
| 2026-04-15 | APN is the single best match key | Parcels don't change even when everything else does. CoStar 92%, LAHD 100%, ZIMAS PIN available. Pipedream only 1% — but CoStar backfills this. |
| 2026-04-15 | Match conservatively, track liberally | When values change (address, units, name), that's a field change logged in ChangeLog, not a matching failure. Store all aliases in raw_addresses[] and previous_names[]. |
| 2026-04-15 | Auto-match threshold: ≥0.85, ambiguous: 0.65-0.84, weak: 0.40-0.64, no match: <0.40 | Balances automation with researcher oversight. Most deterministic and address matches auto-link. Proximity + fuzzy matches get human review. |
| 2026-04-15 | Unlocated candidates re-matched each cycle | Very-early-stage projects (no address) stay in a pool and get re-matched as more detail emerges from subsequent source pulls. Surface to researcher after 3 cycles if still unlocated. |
| 2026-04-15 | Supabase (PostgreSQL) as database from day one | New Supabase project to be created during setup. Eliminates SQLite→Postgres migration. PostGIS available for proximity matching. Dashboard useful during development. |
| 2026-04-15 | Vercel for frontend (Phase 4+), Render for backend/workers | Frontend is future scope. Backend collectors run locally first, then move to Render cron jobs for automation. |
| 2026-04-15 | Keep the `Project` row focused on current canonical state; move deterministic keys into `ProjectIdentifier` | APNs, case numbers, permit numbers, and source-stable IDs are multi-valued/query-heavy and should not live as arrays or one-off columns on `Project`. |
| 2026-04-15 | Use `ProjectSourceRecord` instead of a `sources` JSON blob on `Project` | Source provenance needs to be queryable, auditable, and easy to diff. A normalized table is cleaner than opaque JSON for this use case. |
| 2026-04-15 | Use `ProjectRelationship` as the sole source of truth for inter-project links | Avoid duplicated relationship state and fake FK arrays on `Project`. |
| 2026-04-15 | Persist review queue state in the database before building the researcher surface | Accept/reject/override/defer is core system state, not UI-only metadata. |
| 2026-04-15 | Add a PostGIS `location` column and keep `lat`/`lng` as convenience fields | Spatial matching should use native geography/geometry types from day one. |
| 2026-04-15 | Build address normalization before the seed ingesters | Both Pipedream and CoStar ingestion depend on canonical addresses for deduplication and future matching. |
| 2026-04-15 | Address normalization canonical form uses uppercase full-word directionals/suffixes and strips units for building-level matching | Implemented with `usaddress` plus custom normalization rules. Numbered streets normalize to ordinal numeric form (`SEVENTH` → `7TH`), and LA market city aliases such as `DTLA` and `Hollywood` normalize to `Los Angeles` when `market='los_angeles'`. |
| 2026-04-15 | Address normalization should degrade gracefully instead of failing a seed or collector run | `usaddress.parse()` remains the primary parser, but normalization now falls back to a loose street-line/city/state/ZIP pass if parsing fails so ingestion can continue while preserving the raw address. |
| 2026-04-15 | Prefer `httpx` + `psycopg` + `typer`; keep `sodapy` and `supabase-py` optional | This keeps the runtime lean and closer to the underlying protocols while preserving future flexibility. |
| 2026-04-15 | Start PDF extraction with `pdfplumber`, use `tabula-py` only as a fallback | Avoid a Java dependency unless a specific source materially benefits from lattice extraction. |
| 2026-04-15 | Initial schema applied to Supabase via Alembic using session pooler connection | Local network does not support IPv6, so the Supabase session pooler URL is the reliable development path. Extensions `postgis` and `pg_trgm` are enabled in the target database. |
| 2026-04-15 | `ProjectRelationship` uses explicit outgoing and incoming ORM paths | Self-referential project links need separate foreign-key-aware relationships for `RelP1-6` import and later graph queries. |
| 2026-04-15 | Add lookup indexes in a follow-up migration and ignore PostGIS system tables in Alembic autogenerate | Added indexes for `status_history(project_id, status_date)`, `change_log(project_id, timestamp)`, `project_source_records(project_id)`, `project_identifiers(value)`, and `project_relationships(related_project_id)`. Alembic excludes `spatial_ref_sys` from future diffs. |

---

## 9. Open Questions

> Items to resolve as we build. Remove from this list and add to Decision Log when resolved.

- [x] **Pipedream field mapping:** ~~Blocked on receiving files.~~ **RESOLVED 2026-04-15.** Full 81-field inventory documented in Section 3b. See Decision Log for key findings. Pipedream is a macro-enabled .xlsm with DataStorage as the database tab, 81 fields per project, "--" as null sentinel, every-other-column layout. Detailed ingester spec in Section 2 under Collectors.
- [x] **CoStar field mapping:** ~~Blocked on receiving the file.~~ **RESOLVED 2026-04-15.** 287 columns analyzed, dual column layout documented (MF vs non-MF rows). Status mapping table created. Full spec in Sections 3b-3c.
- [x] **CoStar ↔ Pipedream overlap:** **RESOLVED 2026-04-15.** Pipedream ingested first, CoStar second. CoStar matches on APN (92% coverage) or address. On match: CoStar fills gaps only, never overwrites Pipedream. CoStar uniquely contributes: APN, zoning, owner, total_sf, style, construction start date, CoStar submarket.
- [x] **CoStar export row limit:** ~~Export appears capped at 1000 rows.~~ **RESOLVED 2026-04-15.** CoStar caps at 500 rows per export. Nate stitches multiple exports together. Our seeder accepts a folder of raw export files and reads each independently. No need to stitch.
- [x] **CoStar column layout issue:** ~~Dual column layout requires row-type detection.~~ **RESOLVED 2026-04-15.** MF and non-MF exports have completely different column layouts (233/287 shift). Solution: map by header name, not column number. Each file reads its own headers.
- [ ] **LADBS address geocoding:** LADBS permits (hbkd-qubn) have decomposed address fields (address_start, street_name, street_suffix, zip) but no lat/lng or APN visible in the verified field set. May need to: (a) assemble address and geocode, or (b) use the "New Building Permits" (ydma-y4hd) or "Building Permits: New Housing Units" (cpkv-aajs) datasets which may have coordinates. Verify at build time.
- [ ] **CoStar city name normalization:** CoStar MF exports use "Los Angeles", "Los Angeles CBD", "Downtown Los Angeles", "Hollywood", etc. as city values. Non-MF exports consistently use "Los Angeles". Need a mapping table for MF city normalization. (Non-MF is clean — all 231 records show "Los Angeles".)
- [ ] **Non-MF project handling:** CoStar provides 231 non-MF projects (offices, hotels, retail). These are valuable for comprehensive pipeline tracking. How should they appear in the review interface? Separate section? Same queue with a property type filter?
- [x] **Address normalization library:** **RESOLVED 2026-04-15.** Start with `usaddress` (pure Python, easy to deploy) + custom LA normalization rules (directionals, street types, numbered streets, LA city name variants). Full normalization pipeline spec in Section 6b. Upgrade to `libpostal` later if accuracy is insufficient. Matching also uses geocoding + proximity as a fallback when address normalization alone isn't enough (Section 6c).
- [x] **Database choice for production:** **RESOLVED 2026-04-15.** Supabase (hosted PostgreSQL) from day one. Create a new Supabase project during initial setup (Step 1.4). No SQLite phase needed. PostGIS available for spatial matching. Supabase provides dashboard, auth, REST API, and realtime for future review UI.
- [ ] **Review interface format:** Which researcher surface should sit on top of the persisted review tables first: Excel output or a simple web UI? Excel fits existing workflow but limits collaboration. Pipedream is already Excel-based, so Excel output would feel natural to researchers.
- [x] **ZIMAS address-to-case-number lookup:** **RESOLVED 2026-04-15.** Confirmed: no programmatic address→case endpoint exists. But this is a non-issue — case numbers flow in from three other sources: (1) LA Case Reports biweekly PDFs, (2) LADBS permit records, (3) Pipedream Site1 URLs which often contain PDIS case links. ZIMAS is enrichment-only; we never need to search it cold. See Section 4b Source 1 and Section 4e (Case Number Chain).
- [ ] **Pipedream "Pending" → public source status mapping:** Pipedream "Pending" means in entitlement (EIR underway, planning review). LADBS and ZIMAS use different status terminology. Need a mapping table. Same for "Stalled" — how do we detect stall from public sources? Probably absence of activity for X months.
- [ ] **Unit count threshold for new project candidates:** What's the minimum unit count for the system to flag a new project? Pipedream's smallest projects are ~10-20 units. Should the system flag a 5-unit project from LADBS? A 2-unit ADU?
- [ ] **Update frequency:** How often should each source be polled? Weekly? Biweekly? Different per source?
- [ ] **Multi-market coordination:** When we expand beyond LA, do markets share a single database or get separate databases? Single is better for cross-market reporting but adds complexity.
- [x] **Hosting/scheduling:** **RESOLVED 2026-04-15.** Collectors run locally during development. Production: Render for backend workers/cron jobs, Vercel for frontend review UI (Phase 4+). Database is Supabase (always cloud-hosted). Collection schedule: biweekly full cycle aligned with LA Case Reports, weekly LADBS/SM Socrata pulls.
- [ ] **Pipedream ongoing sync:** After initial seed, will researchers continue to update Pipedream files? If so, we need a recurring import/sync process, not just a one-time seed. Or does the new system replace Pipedream entirely?

---

## 10. Tech Stack

### Infrastructure
- **Database:** Supabase (hosted PostgreSQL). Create a new Supabase project during initial setup. Supabase provides: PostgreSQL database, REST API (PostgREST), realtime subscriptions, auth, edge functions, and a dashboard — all useful for this project.
- **Backend / collectors:** Python 3.11+ — runs collection cycles, matching, diffing. Initially runs locally or as a scheduled job. Can be deployed to **Render** (background workers, cron jobs) when ready for automation.
- **Frontend (future):** Review queue UI. Deploy to **Vercel** (React/Next.js) or **Render** (if keeping it simple with server-rendered pages). Not needed for Phase 1-3 — Excel export covers early researcher workflow.
- **Version control:** Git + GitHub (assumed)

### Database Details (Supabase/PostgreSQL)
- SQLAlchemy ORM connects to Supabase's PostgreSQL instance via standard `postgresql+psycopg://` connection string
- Supabase connection string available in project settings → Database → Connection string
- Use Supabase's Row Level Security (RLS) if/when multi-user access is needed
- Enable `postgis` for spatial queries and `pg_trgm` for text similarity/index support during setup if available
- Store project geometry in a native PostGIS `location` column; keep `lat`/`lng` as convenience fields for exports and debugging
- Supabase dashboard provides a free data explorer for ad-hoc queries during development
- Alembic for schema migrations (standard SQLAlchemy migration tool)

### Python Libraries
- `httpx` — API calls to Socrata, ArcGIS, PDIS
- `sqlalchemy` + `psycopg` — ORM + PostgreSQL driver
- `alembic` — Database migrations
- `geoalchemy2` — SQLAlchemy support for PostGIS geometry/geography columns
- `usaddress` — Address parsing/normalization
- `rapidfuzz` — Fuzzy string matching (developer names, project names)
- `pdfplumber` — Primary PDF extraction library
- `tabula-py` — Fallback for PDFs where lattice extraction is materially better; requires Java
- `openpyxl` — Excel read/write (CoStar import, Pipedream import, review queue export)
- `pydantic` + `pydantic-settings` — Data validation and configuration loading
- `typer` — CLI interface
- `tenacity` — Retries/backoff for flaky public endpoints
- `pytest` + `respx` or `pytest-httpx` — Test support for API collectors
- `sodapy` — Optional. Only add if direct `httpx` + SoQL proves too cumbersome.
- `supabase-py` — Optional. Not required for core DB access when using SQLAlchemy directly.
- `schedule` or `APScheduler` — Task scheduling (for local runs; Render cron for production)

### Frontend Libraries (Phase 4+, when review UI is built)
- **Next.js** (React) — deployed on Vercel
- **Supabase JS client** — connects frontend to database with auth + realtime
- **Tailwind CSS** — styling
- This is future scope — not needed until Phase 4 (review interface)

### Project Structure
```
tcg-pipeline/
  ARCHITECTURE.md          ← this file (Claude Code reads at session start)
  README.md
  pyproject.toml
  .env                     ← Supabase connection string, API keys (gitignored)
  .env.example             ← Template for .env
  alembic.ini              ← Migration config
  config/
    markets/
      los_angeles.yaml
      santa_monica.yaml
  src/
    tcg_pipeline/
      __init__.py
      cli.py               ← CLI entry point
      settings.py          ← Pydantic settings / environment loading
      db/
        models.py           ← SQLAlchemy models (Project, identifiers, provenance, review tables)
        connection.py       ← Supabase/PostgreSQL connection setup
        migrations/         ← Alembic migrations
        seed.py             ← Seeder logic
      collectors/
        base.py             ← Base collector class
        socrata.py          ← LADBS, LAHD, SM permits, LA County
        arcgis.py           ← ZIMAS ArcGIS layer queries
        pdis_scraper.py     ← ZIMAS/PDIS HTML case detail scraper
        pdf_parser.py       ← LA Case Reports, SM Dev Tracking, SM Ministerial
        accela.py           ← SM ePermit (optional, Phase 5)
        ceqa.py             ← CEQAnet scraper
      ingesters/
        costar.py           ← CoStar CSV/Excel parser
        pipedream.py        ← Pipedream Excel parser
      matching/
        normalizer.py       ← Address parsing + normalization pipeline
        geocoder.py         ← Geocoding + coordinate storage
        match_index.py      ← In-memory index (APN, case#, permit#, address, geo)
        matcher.py          ← Tiered match logic + composite scoring
        differ.py           ← Change detection + priority assignment
      review/
        queue.py            ← Review queue generation
        export_excel.py     ← Excel output for researchers (Phase 1-3)
      utils/
        logging.py
        geo.py              ← Coordinate helpers (PostGIS integration)
  tests/
    ...
  data/
    seed/                   ← Drop CoStar/Pipedream files here
    output/                 ← Review queue exports land here
  frontend/                 ← Next.js app (Phase 4+, separate deploy to Vercel)
    ...
```

---

*This document should be read by Claude Code / Codex at the start of every session. Update it whenever the plan changes, a decision is made, or a build step is completed.*

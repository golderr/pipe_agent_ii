# Evidence Layer Integration Guide

> **Audience:** Claude Code / Codex agent implementing the evidence layer retrofit.
> **Date:** 2026-04-20
> **Status:** Plan complete. No code written yet. Awaiting implementation.

---

## Table of Contents

1. [Project Context](#1-project-context)
2. [Current Codebase Audit](#2-current-codebase-audit)
3. [The Evidence Layer — What and Why](#3-the-evidence-layer--what-and-why)
4. [Evidence Table Schema](#4-evidence-table-schema)
5. [Modified Project Table](#5-modified-project-table)
6. [Supporting Tables](#6-supporting-tables)
7. [Resolution Engine](#7-resolution-engine)
8. [Field Resolution Rules](#8-field-resolution-rules)
9. [Likelihood Engine](#9-likelihood-engine)
10. [Current Architecture vs. Target Architecture](#10-current-architecture-vs-target-architecture)
11. [Retrofit Plan — Phased Implementation](#11-retrofit-plan--phased-implementation)
12. [Key Design Decisions](#12-key-design-decisions)
13. [Files That Need Changes](#13-files-that-need-changes)

---

## 1. Project Context

This is the **TCG Pipeline Tracker** — a real estate development pipeline tracking system for the Los Angeles market. It monitors construction projects from concept through completion, pulling data from government sources (LADBS permits, inspections, CofOs), baseline datasets (Pipedream researcher workbooks, CoStar exports), and eventually news articles and deep research.

The system is built with:
- **Python** (SQLAlchemy, Alembic, Typer, httpx, openpyxl, usaddress, geoalchemy2)
- **Supabase (PostgreSQL + PostGIS)** as the database
- **Socrata Open Data API** for LA government data
- Market-configurable YAML for source definitions

---

## 2. Current Codebase Audit

### What's Built and Working

| Component | Files | Summary |
|-----------|-------|---------|
| **Schema / Models** | `src/tcg_pipeline/db/models.py` | 11 tables: Project, StatusHistory, ProjectIdentifier, ProjectRelationship, ProjectSourceRecord, SourceRun, ReviewItem, ReviewDecision, ChangeLog, DismissedRecord. 3 Alembic migrations deployed. |
| **Pipedream Ingester** | `src/tcg_pipeline/ingesters/pipedream.py` | Parses .xlsm workbooks (DataStorage tab), builds Project + identifiers + status history + source records. Full enum mapping, address normalization, relationship staging. ~600 lines. |
| **CoStar Ingester** | `src/tcg_pipeline/ingesters/costar.py` | Parses .xlsx exports, merges onto existing Pipedream-seeded projects via CoStar Property ID → APN → address matching. ~550 lines. |
| **Socrata Collector** | `src/tcg_pipeline/collectors/socrata.py` | Async paginated HTTP pulls from Socrata endpoints. Supports incremental mode (`:updated_at` cursor), hash-based change detection. |
| **LADBS Source Adapters** | `src/tcg_pipeline/source_adapters/ladbs.py` | 6 adapters: `ladbs_permits_pi9x_tg5x` (Bldg-New permits), `ladbs_permit_activity_pi9x_tg5x` (non-Bldg-New), `ladbs_inspections_9w5z_rg2h`, `ladbs_cofo` (3f9m-afei), plus 2 legacy frozen-dataset adapters. Each maps Socrata rows → RawRecord with appropriate `status_evidence_type`. |
| **Matching** | `src/tcg_pipeline/matching/matcher.py` | 3-tier: source_record lookup → identifier match → address match. Returns MatchResult with confidence score. |
| **Address Normalizer** | `src/tcg_pipeline/matching/normalizer.py` | Uses `usaddress` library. Normalizes directionals, suffixes, ordinals, ranges. City alias handling (LA-specific). |
| **Differ** | `src/tcg_pipeline/matching/differ.py` | Compares existing Project fields against incoming RawRecord. Produces DetectedChange list + StatusSuggestion. |
| **Status Rules** | `src/tcg_pipeline/status_rules.py` | Forward-only status progression. Evidence rules: `building_permit_issued` → Approved (supporting), `building_inspection_recorded` → UC (direct), `certificate_of_occupancy_issued` → Complete (direct). |
| **Collect + Persist** | `src/tcg_pipeline/db/collect.py` | THE main integration point. `persist_collected_records()` orchestrates: match → upsert source record → diff → create review items. |
| **Seed Persistence** | `src/tcg_pipeline/db/seed.py` | Pipedream + CoStar import with dedup, relationship resolution, field merging logic. |
| **CLI** | `src/tcg_pipeline/cli.py` | Typer app: `doctor`, `preview_pipedream`, `seed_pipedream`, `preview_costar`, `seed_costar`, `preview_source`, `collect_source` |
| **Market Config** | `config/markets/los_angeles.yaml` | 6 LADBS sources + LAHD + la_case_reports + zimas_pdis defined. Only Socrata collectors implemented. |
| **Tests** | `tests/` | 3,429 lines across 13 test files covering all components above. |

### What's NOT Built

- No frontend / UI / API layer
- No PDF parser collector (`la_case_reports`)
- No scraper collector (`zimas_pdis`)
- No LAHD affordable collector (Socrata endpoint defined, adapter not written)
- No news scraping or deep research integration
- No evidence table (data lives in ProjectSourceRecord with upsert semantics)
- No resolution engine (Project fields are written directly by ingesters, not computed)
- No developer registry / alias resolution / fuzzy matching
- No likelihood engine
- No output formats (Excel export, exhibit appendix)
- No confidence scoring beyond basic `status_confidence` enum

### Key Architectural Pattern

The current data flow:

```
Collector → RawRecord → match_raw_record() → _upsert_source_record() → diff → ReviewItem
                                                      ↑
                                            ProjectSourceRecord: overwrites on re-pull
```

`ProjectSourceRecord` stores:
- `raw_payload` (JSONB) — full source row
- `mapped_fields` (JSONB) — normalized field values
- `field_provenance` (JSONB) — which source provided which field
- `source_row_hash` — for change detection

This is structurally similar to our target Evidence table, but uses **upsert semantics** (same source_record_id = overwrite). Evidence needs **append-only semantics** (every observation = new row).

---

## 3. The Evidence Layer — What and Why

### Problem

Currently, when a collector pulls new data for a known project, it overwrites the previous source record and diffs against the Project. If we want to know "what did LADBS say about this project 3 months ago?" — that data is gone. There's no provenance, no history, no way for a researcher to see all evidence that informed a determination.

### Solution

The evidence layer makes three fundamental changes:

1. **Evidence is append-only.** Every data point from every source creates an immutable row. Nothing is overwritten or deleted.
2. **The Project record is computed.** Resolution rules derive the "current best value" for each field from all available evidence. The Project record is recomputed whenever new evidence arrives.
3. **Each field has its own resolution rule.** Status behaves differently from units, which behaves differently from delivery year. No single universal hierarchy.

### Core Principles

- **Researcher overrides are sacred (Tier 0).** If a researcher manually sets a value, no automated source can change it.
- **Confidence is always computed and surfaced.** Every resolved field carries HIGH / MEDIUM / LOW confidence.
- **Deep research is an ingest method, not a source.** The actual publisher determines the tier. Deep research is how the evidence entered the system, not who published it.

---

## 4. Evidence Table Schema

```sql
CREATE TABLE evidence (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID REFERENCES projects(id) ON DELETE CASCADE,  -- nullable for unmatched
    source_type         VARCHAR(120) NOT NULL,    -- who published (ladbs_permit, news_article, etc.)
    source_tier         INTEGER NOT NULL,         -- computed from source_type via config
    ingest_method       VARCHAR(30) NOT NULL,     -- how it entered: scheduled_collector, deep_research, manual_entry, seed_import, costar_refresh
    source_record_id    VARCHAR(255),             -- ID in the source system (permit #, article URL, etc.)
    collected_at        TIMESTAMPTZ NOT NULL DEFAULT now(),  -- when our system pulled this
    evidence_date       DATE,                     -- real-world date (article publish, permit issue, etc.)
    raw_data            JSONB,                    -- full original record
    extracted_fields    JSONB,                    -- normalized fields with per-field extraction confidence
    signal_flags        JSONB,                    -- for likelihood engine (nullable)
    notes               TEXT                      -- agent or researcher annotation
);

CREATE INDEX ix_evidence_project_id ON evidence(project_id);
CREATE INDEX ix_evidence_source_type ON evidence(source_type);
CREATE INDEX ix_evidence_evidence_date ON evidence(evidence_date);
CREATE INDEX ix_evidence_collected_at ON evidence(collected_at);
```

### source_type Values

**Government (Tier 1):** `ladbs_permit`, `ladbs_cofo`, `ladbs_inspection`, `lahd_affordable`, `la_case_report`, `zimas_pdis`, `zimas_arcgis`, `sm_dev_tracking`, `sm_ministerial`, `sm_permit`

**Baseline (Tier 1 for Pipedream, Tier 3 for CoStar):** `pipedream`, `costar`

**News/Research (Tier 2):** `news_article`, `developer_website`

**Low confidence (Tier 4):** `social_media`, `forum`

### ingest_method Values

- `scheduled_collector` — pulled by a collector on its regular schedule
- `deep_research` — found by an agent/researcher-triggered research session
- `manual_entry` — researcher typed this in directly
- `seed_import` — initial market seeding
- `costar_refresh` — monthly CoStar re-import

### extracted_fields Format

```json
{
  "pipeline_status": { "value": "Under Construction", "confidence": "high" },
  "total_units": { "value": 450, "confidence": "high" },
  "delivery_year": { "value": 2028, "confidence": "low" },
  "developer": { "value": "CIM Group", "confidence": "high" }
}
```

For structured sources (Socrata APIs, CoStar CSV), confidence is implicit in the source tier — no per-field confidence needed in `extracted_fields`.

---

## 5. Modified Project Table

Add these columns to the existing `projects` table:

```sql
-- Evidence-derived metadata
ALTER TABLE projects ADD COLUMN confidence VARCHAR(10) NOT NULL DEFAULT 'low';
ALTER TABLE projects ADD COLUMN confidence_reason JSONB;
ALTER TABLE projects ADD COLUMN likelihood FLOAT;
ALTER TABLE projects ADD COLUMN likelihood_breakdown JSONB;
ALTER TABLE projects ADD COLUMN delivery_year_provenance VARCHAR(30);
ALTER TABLE projects ADD COLUMN last_evidence_date DATE;

-- Inclusion flags (sticky — researcher-only, automation cannot change)
ALTER TABLE projects ADD COLUMN inclusion_in_analysis BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE projects ADD COLUMN inclusion_in_exhibit BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE projects ADD COLUMN inclusion_note VARCHAR(255);
```

The existing `status_confidence` column can be aliased or eventually migrated to `confidence`. They represent the same concept — just broadened from status-only to overall project confidence.

The existing `researcher_override` JSONB column maps directly to the "Tier 0 override" concept — no change needed there.

---

## 6. Supporting Tables

### DeveloperRegistry

```sql
CREATE TABLE developer_registry (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name  VARCHAR(255) NOT NULL UNIQUE,
    is_top_tier     BOOLEAN NOT NULL DEFAULT false,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE developer_alias (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    developer_id    UUID NOT NULL REFERENCES developer_registry(id) ON DELETE CASCADE,
    alias_name      VARCHAR(255) NOT NULL UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_developer_alias_developer_id ON developer_alias(developer_id);
```

### Source Tier Config (YAML)

```yaml
# config/source_tiers.yaml
source_tiers:
  tier_1:   # Government + researcher-verified
    - ladbs_permit
    - ladbs_cofo
    - ladbs_inspection
    - lahd_affordable
    - la_case_report
    - zimas_pdis
    - sm_dev_tracking
    - sm_ministerial
    - sm_permit
    - pipedream

  tier_2:   # Contextual intelligence
    - news_article

  tier_3:   # Broad but often stale
    - costar
    - developer_website

  tier_4:   # Low confidence
    - social_media
    - forum
```

Note: Per-field resolution rules define their own source priority where the default doesn't apply (e.g., news ranks above government for developer identity).

---

## 7. Resolution Engine

The resolution engine runs whenever new evidence arrives for a project. It recomputes each field on the Project record by applying field-specific rules.

```python
# src/tcg_pipeline/resolution/engine.py

def resolve_project(project_id: UUID, session: Session) -> dict[str, Any]:
    """Recompute all derived fields for a project from its evidence."""
    evidence = get_all_evidence(session, project_id)
    project = session.get(Project, project_id)

    resolved = {}
    resolved['pipeline_status'] = resolve_status(evidence, project)
    resolved['total_units'] = resolve_units(evidence, project, 'total_units')
    resolved['affordable_units'] = resolve_unit_split(evidence, project, 'affordable_units')
    resolved['market_rate_units'] = resolve_unit_split(evidence, project, 'market_rate_units')
    resolved['product_type'] = resolve_product_type(evidence, project)
    resolved['delivery_year'] = resolve_delivery_year(evidence, project)
    resolved['age_restriction'] = resolve_age_restriction(evidence, project)
    resolved['developer'] = resolve_developer(evidence, project)

    # Compute derived values
    resolved['likelihood'] = compute_likelihood(resolved, evidence)
    resolved['confidence'] = compute_overall_confidence(resolved, evidence)

    # Apply researcher overrides (Tier 0 — never clobbered)
    if project.researcher_override:
        for field, value in project.researcher_override.items():
            resolved[field] = value

    # Diff against current, log changes, update project
    changes = diff_and_apply(project, resolved, session)
    return changes
```

### Proposed File Structure

```
src/tcg_pipeline/resolution/
├── __init__.py
├── engine.py              # resolve_project() orchestrator
├── confidence.py          # compute_overall_confidence()
├── likelihood.py          # compute_likelihood()
└── fields/
    ├── __init__.py
    ├── status.py          # resolve_status()
    ├── units.py           # resolve_units(), resolve_unit_split()
    ├── product_type.py    # resolve_product_type()
    ├── delivery_year.py   # resolve_delivery_year()
    ├── age_restriction.py # resolve_age_restriction()
    └── developer.py       # resolve_developer() + canonicalization
```

---

## 8. Field Resolution Rules

### 8a. pipeline_status

**Strategy:** Highest status wins (forward-only). Tier determines confidence, not correctness.

**Status progression:**
```
Conceptual → Proposed → Pending → Approved → Under Construction → Pre-Leasing/Pre-Selling → Complete
                                                   ↘ Stalled (flagged, not auto-assigned)
                                                   ↘ Inactive
```

**Under Construction promotion paths:**

| Path | Evidence Required | Auto? | Confidence |
|------|-------------------|-------|------------|
| 1 | Permit issued + inspection activity | Yes | HIGH |
| 2 | Permit issued + independent non-gov source claiming UC | Yes | HIGH |
| 3 | Permit issued alone | **NO — flag for review** | — |
| 4 | Non-gov source claiming UC, no permit | **NO — flag for review** | — |
| 5 | 2+ independent non-gov sources both claiming UC | Yes | MEDIUM |

**Regression:** Never automatic. Explicit regression language → flag for researcher review.

**Auto-stall detection:** No evidence for 12+ months on Approved/UC → flag as potentially Stalled (don't auto-change).

**Confidence:**
- HIGH: Government confirms, OR 2+ sources agree (most recent < 90 days), OR researcher confirmed
- MEDIUM: Permit issued but no inspections (UC), OR single authoritative source, OR LLM HIGH confidence
- LOW: Single CoStar only, OR evidence > 180 days, OR LLM MEDIUM/LOW, OR conflicting sources

### 8b. total_units / affordable_units / market_rate_units

**total_units:** Most recent evidence wins regardless of tier. Flag if change > 10% from current. Tie-break by tier (Gov > News > CoStar).

**affordable/market_rate:** Only update from sources that explicitly provide the split (Pipedream, LAHD, SM Dev Tracking, news when explicit). Never infer split from total. When total changes but split doesn't update → flag.

### 8c. product_type

**Most recent wins.** Flag any change between known types. Unknown → known = auto-update (gap-fill). Product type = residential component. Commercial tracked in `retail_sf`, `office_sf`, `hotel_keys`.

### 8d. delivery_year

**Most recent source wins with 6-month freshness threshold.** UC projects exempt from freshness (keep source date until it actually passes).

Provenance tags: `explicit_government`, `explicit_tcg`, `explicit_news`, `explicit_costar`, `estimated_calc`.

**Estimation formula (fallback):**
```
estimated_delivery_year = current_year + base_years[status] + size_adjustment

base_years:      UC: 2.0 | Approved: 3.0 | Pending: 4.5 | Proposed: 5.5 | Conceptual: 7.0
size_adjustment: <200 units: -0.5 | 200-500: 0.0 | 500-1000: +1.0 | >1000: +1.5
```

### 8e. age_restriction

**Most recent explicit mention wins.** Critical: silence is NOT evidence. A news article that doesn't mention age restriction provides NO evidence for this field.

Enum: `Senior`, `Student`, `Unknown` (no "Non Age-Restricted" — absence of restriction is just Unknown until explicitly stated).

### 8f. developer

**Most recent wins + canonicalization.** Flag on canonical name change.

Canonicalization: Normalize → exact alias match → fuzzy (rapidfuzz `token_set_ratio`): ≥90 auto-resolve, 75-89 auto-resolve + flag, <75 no match → flag as new developer.

News ranks above government for developer because government records capture who filed paperwork (often an attorney), not who is building.

---

## 9. Likelihood Engine

```
if status == "Under Construction":
    likelihood = 1.00
else:
    likelihood = base_rate[status] + sum(signal_adjustments)
    likelihood = clamp(likelihood, 0.02, 0.98)
```

**Base rates:** UC: 1.00, Approved: 0.55, Pending: 0.30, Proposed: 0.15, Conceptual: 0.08, Stalled: 0.03

**Positive signals:** construction_financing_announced (+0.10), sales_or_leasing_center_open (+0.10), permits_filed (+0.08), top_tier_developer (+0.05), recent_activity_under_90_days (+0.05), prior_phase_delivered_same_site (+0.05), affordable_or_inclusionary_component (+0.03)

**Negative signals:** unknown_or_first_time_developer (-0.05), public_opposition_or_lawsuit (-0.10), no_activity_12_to_24_months (-0.10), land_assembly_incomplete (-0.10), no_activity_over_24_months (-0.20)

Store breakdown JSON on project record.

---

## 10. Current Architecture vs. Target Architecture

### Current Flow (per source collection run)

```
SocrataCollector.collect()
    → list[RawRecord]

persist_collected_records(session, raw_records, ...)
    for each raw_record:
        match_result = match_raw_record(session, raw_record)
        if matched:
            _upsert_source_record(session, project, raw_record)  ← OVERWRITES
            diff_result = diff_project_against_record(project, raw_record)
            if diff_result.has_reviewable_changes:
                create ReviewItem
        else:
            create ReviewItem (NEW_CANDIDATE or POSSIBLE_MATCH)
```

### Target Flow (with evidence layer)

```
SocrataCollector.collect()
    → list[RawRecord]

persist_collected_records(session, raw_records, ...)
    for each raw_record:
        match_result = match_raw_record(session, raw_record)
        
        # CHANGE 1: Always insert evidence (append-only)
        evidence = insert_evidence(session, raw_record, match_result, ingest_method="scheduled_collector")
        
        # CHANGE 2: Still upsert source record for matcher compatibility (transitional)
        _upsert_source_record(session, project, raw_record)
        
        if matched:
            # CHANGE 3: Resolution engine replaces direct field comparison
            changes = resolve_project(project.id, session)
            
            # Review items generated from resolution changes + confidence
            if changes.needs_review:
                create ReviewItem
        else:
            create ReviewItem (NEW_CANDIDATE or POSSIBLE_MATCH)
```

### Key Difference

Before: Project is the source of truth, updated directly.
After: Evidence is the source of truth. Project is computed from evidence.

---

## 11. Retrofit Plan — Phased Implementation

### Phase 1: Schema + Backfill (non-breaking, no behavior changes)

1. **Alembic migration:** Create `evidence`, `developer_registry`, `developer_alias` tables
2. **Alembic migration:** Add new columns to `projects` table (`confidence`, `confidence_reason`, `likelihood`, `likelihood_breakdown`, `delivery_year_provenance`, `last_evidence_date`, `inclusion_in_analysis`, `inclusion_in_exhibit`, `inclusion_note`)
3. **Create `config/source_tiers.yaml`**
4. **Backfill script:** Convert existing `ProjectSourceRecord` rows → `Evidence` rows. Each PSR becomes one evidence row with:
   - `source_type` = PSR.source_name
   - `source_tier` = derived from source_tiers.yaml
   - `ingest_method` = "seed_import" (for pipedream/costar) or "scheduled_collector" (for ladbs_*)
   - `evidence_date` = PSR.source_updated_at or PSR.source_created_at
   - `raw_data` = PSR.raw_payload
   - `extracted_fields` = PSR.mapped_fields (restructured into {field: {value, confidence}} format)
5. **Backfill script:** Populate `developer_registry` from distinct `developer` values in `projects` table

### Phase 2: Resolution Engine (new code, existing behavior unchanged)

1. Implement `src/tcg_pipeline/resolution/` package with all per-field resolvers
2. Implement likelihood engine
3. Add CLI command: `resolve-all` — re-resolves every project from backfilled evidence
4. **Validation:** Run resolve-all, compare computed values to current project values, log discrepancies
5. Tune resolution rules until discrepancies are understood and acceptable

### Phase 3: Collector Refactor (modify existing behavior)

1. Modify `persist_collected_records()` to **insert Evidence rows** alongside existing PSR upsert
2. After evidence insert, call `resolve_project()` 
3. Resolution engine output replaces the differ for field updates
4. Keep differ for generating review items (or fold review-item logic into resolution engine)
5. Update seed ingesters to write Evidence for future imports

### Phase 4: Developer Canonicalization

1. Add `rapidfuzz` dependency
2. Build alias matching in `src/tcg_pipeline/resolution/fields/developer.py`
3. CLI command: `canonicalize-developers` — one-time sweep of all projects
4. Wire into resolve_developer() for ongoing resolution

### Phase 5: Output Formats + UI (future, not part of this guide)

- Template export (Pipedream-format Excel)
- Summary tables (CMA block)
- Exhibit appendix
- Project evidence view (UI)

---

## 12. Key Design Decisions

### ProjectSourceRecord — Keep or Remove?

**Keep for now.** It serves the matcher (`match_raw_record()` does `SELECT ... WHERE source_name = X AND source_record_id = Y` against PSR). Removing it requires refactoring the matcher to query evidence instead. Safe to do later once evidence layer is proven.

### Parallel Operation During Transition

During Phase 3, the system writes both Evidence AND updates PSR. The resolution engine runs alongside the existing differ. Log discrepancies. Once confident, remove the old path.

### resolve_project() — Sync or Async?

**Synchronous initially.** Projects typically have <20 evidence rows. Resolution is pure computation — should complete in <100ms. If volume grows (news articles), can move to async/queue later.

### Handling Existing Data

The backfill (Phase 1, step 4) converts all historical PSR rows to Evidence. After backfill + resolve-all, the Project table should be consistent with what the resolution engine would compute. Any discrepancies indicate either:
- A resolution rule needs tuning (expected — this is why Phase 2 has a validation step)
- Data quality issues in the seed data (useful to surface)

### Review Item Generation Post-Retrofit

The resolution engine can generate review items when:
- A field would change but evidence confidence is LOW
- Multiple sources conflict (e.g., two different unit counts from recent evidence)
- An auto-stall condition is detected
- A permit-alone UC promotion would require corroboration

---

## 13. Files That Need Changes

### Modified (existing files)

| File | What Changes |
|------|-------------|
| `src/tcg_pipeline/db/models.py` | Add Evidence, DeveloperRegistry, DeveloperAlias models. Add new columns to Project. |
| `src/tcg_pipeline/db/collect.py` | Insert evidence rows. Call resolve_project() after evidence insert. |
| `src/tcg_pipeline/db/seed.py` | Future seed imports write Evidence rows alongside existing logic. |
| `src/tcg_pipeline/status_rules.py` | Refactor into or import from `resolution/fields/status.py`. Keep backward compat for existing tests. |
| `pyproject.toml` | Add `rapidfuzz` dependency for developer canonicalization. |
| `alembic/versions/` | 2 new migration files. |

### New Files

```
src/tcg_pipeline/resolution/
├── __init__.py
├── engine.py
├── confidence.py
├── likelihood.py
└── fields/
    ├── __init__.py
    ├── status.py
    ├── units.py
    ├── product_type.py
    ├── delivery_year.py
    ├── age_restriction.py
    └── developer.py

src/tcg_pipeline/developer/
├── __init__.py
├── registry.py
└── canonicalize.py

config/source_tiers.yaml
config/likelihood.yaml

scripts/backfill_evidence.py
scripts/backfill_developers.py

tests/test_resolution_engine.py
tests/test_resolve_status.py
tests/test_resolve_units.py
tests/test_resolve_delivery_year.py
tests/test_resolve_developer.py
tests/test_likelihood.py
tests/test_developer_canonicalization.py
```

---

## Appendix: Source Tier Reference

| Tier | Sources | Trust Level |
|------|---------|-------------|
| 0 | Researcher override | Sacred — automation cannot change |
| 1 | LADBS permit/CofO/inspection, LAHD, LA Case Reports, ZIMAS, SM Dev Tracking, SM Permit, Pipedream | Government procedural records + researcher-verified |
| 2 | News articles | Contextual intelligence — per-field LLM extraction confidence modulates within tier |
| 3 | CoStar, Developer websites | Broad coverage but often stale |
| 4 | Social media, Forums | Low confidence |

Note: Per-field rules override this default hierarchy where appropriate (e.g., news > government for developer identity).

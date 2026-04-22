# Evidence Layer Retrofit Plan

> Analysis of the current codebase and plan for integrating the evidence layer from EVIDENCE-LAYER-SPEC.md.

**Date:** 2026-04-20

---

## 1. Repo Audit Summary

### What's Built and Working

| Component | Status | Key Files |
|-----------|--------|-----------|
| **Schema / Models** | ✅ Complete | `src/tcg_pipeline/db/models.py` (11 tables, 3 Alembic migrations) |
| **Pipedream Ingester** | ✅ Complete | `src/tcg_pipeline/ingesters/pipedream.py` — parses .xlsm workbooks, builds Project + identifiers + status history + source records |
| **CoStar Ingester** | ✅ Complete | `src/tcg_pipeline/ingesters/costar.py` — parses .xlsx, merges onto existing Pipedream-seeded projects |
| **Socrata Collector** | ✅ Complete | `src/tcg_pipeline/collectors/socrata.py` — async paginated pulls, incremental mode, hash-based change detection |
| **LADBS Source Adapters** | ✅ Complete | `src/tcg_pipeline/source_adapters/ladbs.py` — 6 adapters: permits (pi9x-tg5x), permit activity (pi9x-tg5x), inspections (9w5z-rg2h), CofO (3f9m-afei), plus 2 legacy frozen-dataset adapters |
| **Matching** | ✅ Complete (3-tier) | `src/tcg_pipeline/matching/matcher.py` — source_record → identifier → address |
| **Address Normalizer** | ✅ Complete | `src/tcg_pipeline/matching/normalizer.py` — usaddress parsing, directional/suffix/ordinal normalization |
| **Differ** | ✅ Complete | `src/tcg_pipeline/matching/differ.py` — detects field changes between project and incoming record |
| **Status Rules** | ✅ Complete | `src/tcg_pipeline/status_rules.py` — forward-only with evidence types (permit_issued→Approved, inspection→UC, CofO→Complete) |
| **Collect + Persist** | ✅ Complete | `src/tcg_pipeline/db/collect.py` — full pipeline: match → upsert source record → diff → create review items |
| **Seed Persistence** | ✅ Complete | `src/tcg_pipeline/db/seed.py` — Pipedream + CoStar merge with dedup, relationship resolution |
| **CLI** | ✅ Complete | `src/tcg_pipeline/cli.py` — `doctor`, `preview_pipedream`, `seed_pipedream`, `preview_costar`, `seed_costar`, `preview_source`, `collect_source` |
| **Market Config** | ✅ Complete | `config/markets/los_angeles.yaml`, `config/markets/santa_monica.yaml` |
| **Tests** | ✅ Solid | 3,429 lines across 13 test files |

### What's NOT Built Yet

- **No frontend / UI** — no React, no views
- **No API layer** — no REST/RPC endpoints, no Supabase Edge Functions
- **No PDF parser collector** (`la_case_reports` — defined in config, no code)
- **No scraper collector** (`zimas_pdis` — defined in config, no code)
- **No LAHD affordable collector** (defined in config, no code)
- **No news scraping / deep research**
- **No evidence table** — data lives in `ProjectSourceRecord` (upsert model, not append-only)
- **No developer registry / alias resolution**
- **No likelihood engine**
- **No output formats** (Excel export, exhibit appendix, template export)
- **No confidence scoring** beyond per-source-record `field_provenance`
- **No resolution engine** — fields on `Project` are written directly by seed ingesters and not recomputed

---

## 2. Critical Architectural Observation

The existing `ProjectSourceRecord` table is the closest analog to the planned `Evidence` table:

```
ProjectSourceRecord:
  id, project_id, source_name, source_record_id, source_row_id,
  source_url, source_created_at, source_updated_at, source_row_hash,
  first_seen_at, last_seen_at, last_pulled_at,
  raw_payload (JSONB), mapped_fields (JSONB), field_provenance (JSONB)
```

**Key difference:** `ProjectSourceRecord` uses **upsert semantics** — when the same source record is re-pulled, the row is updated in place. The evidence layer requires **append-only semantics** — every observation creates a new row, preserving history.

Additionally, `ProjectSourceRecord` is keyed by `(source_name, source_record_id)` with a `UniqueConstraint`, meaning you can only store one version of each source record per project. The evidence table has no such constraint — the same permit can generate multiple evidence rows as it progresses through statuses.

### Current Data Flow

```
Collector → RawRecord → match_raw_record() → _upsert_source_record() → diff → ReviewItem
                                                      ↑
                                            (overwrites raw_payload, mapped_fields)
```

### Target Data Flow (with evidence layer)

```
Collector → RawRecord → match_raw_record() → insert_evidence() → resolve_project() → diff (for review items)
                                                      ↑
                                            (append-only, never overwrites)
```

---

## 3. Schema Changes Required

### A. New Tables

1. **`evidence`** — per EVIDENCE-LAYER-SPEC.md §2
2. **`developer_registry`** — per §4
3. **`developer_alias`** — per §4

### B. Modify `projects` Table (new columns)

```python
# Evidence-derived metadata
confidence: Mapped[str] = mapped_column(STATUS_CONFIDENCE_ENUM, nullable=False, default="low")
confidence_reason: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
likelihood: Mapped[float | None] = mapped_column(Float, nullable=True)
likelihood_breakdown: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
delivery_year_provenance: Mapped[str | None] = mapped_column(String(30), nullable=True)
last_evidence_date: Mapped[date | None] = mapped_column(Date, nullable=True)

# Inclusion flags (sticky, researcher-only)
inclusion_in_analysis: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
inclusion_in_exhibit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
inclusion_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
```

### C. `ProjectSourceRecord` — Keep or Remove?

**Recommendation: Keep it, but redefine its role.** 

`ProjectSourceRecord` becomes a **"current state cache"** — a denormalized view of the most recent evidence per (source_name, source_record_id). This preserves backward compatibility with the existing matching logic (which relies on `ProjectSourceRecord` for source-record-based matching) while the evidence table becomes the authoritative history.

Eventually, `ProjectSourceRecord` could be eliminated in favor of a materialized view or a query against the evidence table, but for a safe incremental migration, keeping it avoids breaking the matcher.

### D. No Conflicts with Existing Schema

The existing `status_confidence` field on `Project` maps cleanly to the new `confidence` field. We just need to rename or alias it. The `researcher_override` JSONB field already exists and maps to the "Tier 0 override" concept. No destructive changes needed.

---

## 4. Collector Refactor Assessment

### Socrata Collector + LADBS Adapters (`collect.py`)

**Refactor difficulty: Medium.** The current `persist_collected_records()` function is the single integration point. It needs to:

1. After matching, **insert an Evidence row** (instead of only upserting `ProjectSourceRecord`)
2. Still upsert `ProjectSourceRecord` for matcher compatibility (or refactor matcher to query evidence)
3. After evidence insertion, call `resolve_project()` instead of relying on the differ alone

The good news: `RawRecord` already carries `raw_payload`, `mapped_fields`, `source_name`, `source_record_id`, `source_created_at`, `source_updated_at` — most of what Evidence needs. We just need to add:
- `source_tier` (derive from `source_name` via config)
- `ingest_method` (always `scheduled_collector` for this path)
- `evidence_date` (extract from `mapped_fields` — e.g., `status_evidence_date`, `permit_issue_date`)
- `extracted_fields` (restructure `mapped_fields` into the `{field: {value, confidence}}` format)
- `signal_flags` (for likelihood engine — can be null initially)

### Seed Ingesters (Pipedream + CoStar)

**Refactor difficulty: Medium-High.** These ingesters currently create `Project` objects directly with all fields populated. They'd need to:

1. Create evidence rows for each seed record
2. Let the resolution engine compute the Project fields
3. OR (pragmatic approach): keep the seed path as-is for initial load, then backfill evidence rows from the existing `ProjectSourceRecord.raw_payload` data

**Recommendation:** Take the pragmatic approach. The seed ingesters are one-time operations that already ran. We should:
- Write a **backfill migration** that converts existing `ProjectSourceRecord` rows into `Evidence` rows
- Going forward, new seed imports also write Evidence rows
- The existing Project field values become the "baseline" that the resolution engine can verify/override

---

## 5. Resolution Engine Insertion Point

### Where It Fits

There is currently **no service layer** between collectors and the DB — `persist_collected_records()` in `db/collect.py` IS the service layer. The resolution engine should be:

```
src/tcg_pipeline/resolution/
├── __init__.py
├── engine.py          # resolve_project() orchestrator
├── fields/
│   ├── __init__.py
│   ├── status.py      # resolve_status()
│   ├── units.py       # resolve_units(), resolve_unit_split()
│   ├── product_type.py
│   ├── delivery_year.py
│   ├── age_restriction.py
│   └── developer.py   # resolve_developer() + canonicalization
└── likelihood.py      # compute_likelihood()
```

### Integration Points

1. **`db/collect.py` → `persist_collected_records()`** — after inserting evidence, call `resolve_project(project_id)` 
2. **`db/seed.py`** — after backfill migration, can optionally re-resolve all projects
3. **Future: researcher UI** — after manual override, call `resolve_project(project_id)`
4. **Future: deep research** — after inserting evidence from research, call `resolve_project(project_id)`

### Existing Logic to Build On

- `status_rules.py` already implements forward-only status progression and evidence-type rules. This becomes the foundation of `resolution/fields/status.py`.
- `matching/differ.py` detects changes but doesn't apply them. The resolution engine replaces the differ's role for field values (differ remains useful for generating review items).
- `matching/normalizer.py` is reusable for developer alias normalization (fuzzy matching on top).

---

## 6. Migration Strategy

### Recommended Order

```
Phase 1: Schema + Backfill (non-breaking)
├── 1a. Alembic migration: add evidence table, developer_registry, developer_alias
├── 1b. Alembic migration: add new columns to projects table
├── 1c. Backfill script: convert ProjectSourceRecord rows → Evidence rows
└── 1d. Backfill script: populate developer_registry from existing developer field values

Phase 2: Resolution Engine (new code, no existing behavior changes)
├── 2a. Implement resolution/engine.py + per-field resolvers
├── 2b. Implement likelihood engine
├── 2c. Wire resolve_project() into collect.py (after evidence insert)
├── 2d. Add CLI command: resolve-all (re-resolve every project from evidence)
└── 2e. Test: run resolve-all, compare output to current project values, flag discrepancies

Phase 3: Collector Refactor (modify existing behavior)
├── 3a. Modify persist_collected_records() to write Evidence rows
├── 3b. Keep ProjectSourceRecord upsert for matcher compatibility (can remove later)
├── 3c. After evidence insert, call resolve_project() instead of just creating review items
└── 3d. Update seed ingesters to write Evidence for future seed imports

Phase 4: Developer Canonicalization
├── 4a. Build alias registry with fuzzy matching (rapidfuzz)
├── 4b. Wire into resolve_developer()
└── 4c. CLI command: canonicalize-developers (one-time sweep)

Phase 5: Output Formats + UI (future)
├── 5a. Project evidence view (all sources for a project)
├── 5b. Confidence/likelihood display
├── 5c. Template export (Excel)
└── 5d. Exhibit appendix generation
```

### Why This Order Works

- **Phase 1 is non-breaking.** Adding tables and columns doesn't change any existing behavior. The system keeps running exactly as-is.
- **Phase 2 is additive.** The resolution engine is new code that can be tested independently against the backfilled evidence without changing how collectors work.
- **Phase 3 is the critical refactor** but by then the resolution engine is tested and proven against historical data.
- **Phase 4 is standalone** — developer canonicalization doesn't depend on the collector refactor.
- **Phase 5 is UI/output** — depends on everything above but is decoupled from backend logic.

### Data Loss Risk

**None.** The existing `ProjectSourceRecord` rows have `raw_payload` preserved, which means we can reconstruct Evidence rows from historical data. The backfill migration (1c) ensures no information is lost.

---

## 7. Key Design Decisions for Implementation

### Q: Should resolve_project() run synchronously after every evidence insert?

**Yes, initially.** The resolution is pure computation over a small number of evidence rows per project (typically <20 rows). It should complete in <100ms. If it becomes a bottleneck later (e.g., when news articles create high evidence volume), we can make it async/queued.

### Q: How do review items change?

Currently, review items are generated by the differ when a source record changes. With the evidence layer:
- **Status promotions requiring review** (e.g., LADBS permit alone → Approved) still generate review items
- The resolution engine itself can create review items when:
  - A field would change but confidence is LOW
  - Multiple sources conflict
  - A researcher override would be clobbered (this shouldn't happen, but flag it)

### Q: What about the existing `status_confidence` field?

Rename to `confidence` in the migration (or add the new `confidence` column and deprecate `status_confidence`). The new `confidence` field is broader — it reflects overall project confidence, not just status.

### Q: Can we run old and new paths in parallel during transition?

**Yes.** During Phase 3, we can:
1. Write evidence AND upsert ProjectSourceRecord
2. Run resolve_project() AND the old differ
3. Compare results, log discrepancies
4. Once satisfied, remove the old path

---

## 8. Estimated Effort

| Phase | Effort | Dependencies |
|-------|--------|--------------|
| Phase 1: Schema + Backfill | 2-3 days | None |
| Phase 2: Resolution Engine | 4-5 days | Phase 1 |
| Phase 3: Collector Refactor | 2-3 days | Phase 2 |
| Phase 4: Developer Canonicalization | 2 days | Phase 1 |
| Phase 5: Output Formats | 3-5 days | Phase 2+ |

**Total for core evidence layer (Phases 1-3): ~8-11 days of focused work.**

---

## 9. Files That Need Changes

### Modified
- `src/tcg_pipeline/db/models.py` — add Evidence, DeveloperRegistry, DeveloperAlias models + new Project columns
- `src/tcg_pipeline/db/collect.py` — insert evidence, call resolution engine
- `src/tcg_pipeline/db/seed.py` — optionally write evidence on future seed imports
- `src/tcg_pipeline/status_rules.py` — refactor into resolution/fields/status.py (or import from there)
- `alembic/versions/` — 2 new migration files

### New
- `src/tcg_pipeline/resolution/__init__.py`
- `src/tcg_pipeline/resolution/engine.py`
- `src/tcg_pipeline/resolution/fields/status.py`
- `src/tcg_pipeline/resolution/fields/units.py`
- `src/tcg_pipeline/resolution/fields/product_type.py`
- `src/tcg_pipeline/resolution/fields/delivery_year.py`
- `src/tcg_pipeline/resolution/fields/age_restriction.py`
- `src/tcg_pipeline/resolution/fields/developer.py`
- `src/tcg_pipeline/resolution/likelihood.py`
- `src/tcg_pipeline/resolution/confidence.py`
- `src/tcg_pipeline/developer/__init__.py`
- `src/tcg_pipeline/developer/registry.py`
- `src/tcg_pipeline/developer/canonicalize.py`
- `scripts/backfill_evidence.py`
- `scripts/backfill_developers.py`
- `config/source_tiers.yaml`
- `tests/test_resolution_engine.py`
- `tests/test_resolve_status.py`
- `tests/test_resolve_units.py`
- `tests/test_developer_canonicalization.py`
- `tests/test_likelihood.py`

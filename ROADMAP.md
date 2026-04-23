# TCG Pipeline Tracker — Roadmap & Current Build Plan

> **This is the active build plan.** It supersedes the build plan (Section 7) in `ARCHITECTURE.md`.
> For detailed source specifications, field inventories, matching strategy, and data model definitions, refer to `ARCHITECTURE.md`. That file remains the reference for *what the system is*; this file is the reference for *what to build next and what has been built*.

**Last updated:** 2026-04-22
**Maintained by:** Nate Goldstein + Claude Code / Codex agents

---

## How to Use This Document

This is a living document. It should be updated continuously as work progresses.

**For Claude Code / Codex agents:** Read this file at the start of every session. When you complete a build step, mark it `done` in the table and add a date + brief note. When the plan changes (new steps, reordered priorities, removed items), update the tables and add an entry to the Decision Log at the bottom. Use commits as checkpoints.

**For researchers and contributors:**
- **Status markers:** `done`, `in_progress`, `not_started`, `deferred`, `blocked`
- **When completing a step:** Change status to `done`, add completion date and a one-line note summarizing what was built and where the code lives.
- **When priorities shift:** Move steps between phases or reorder them, but always record why in the Decision Log.
- **When adding new work:** Add it to the appropriate phase. If it doesn't fit, create a new phase. Keep phase numbering stable — insert sub-phases (e.g., Phase 3a) rather than renumbering everything.
- **When deferring work:** Change status to `deferred` and note why. Don't delete — deferred work often comes back.

**Principles for this document:**
1. The build plan reflects what we *actually intend to build next*, not an idealized sequence.
2. Every step should be small enough to complete in 1-3 days of focused work.
3. Dependencies between steps should be explicit.
4. If a step has been `in_progress` for more than a week without progress, it needs to be broken down or re-evaluated.
5. The Decision Log is append-only. Don't edit old entries — add new ones that supersede them.

---

## 1. Project Overview

An automated system for building and maintaining comprehensive real estate development pipeline data across US markets. See `ARCHITECTURE.md` Section 1 for the full overview.

**Core workflow (with evidence layer):**
```
Seed (once) → Collect (scheduled) → Match → Insert Evidence → Resolve Project → Diff → Review (human) → Update
```

The system uses an **append-only evidence store** where all incoming data is captured as immutable evidence rows. The Project record is a **derived view** computed by per-field resolution rules. This ensures full provenance, supports confidence scoring, and allows the resolution logic to be rerun or refined without losing any historical data.

---

## 2. Current System State

**As of 2026-04-22.** Update this section when major milestones are reached.

### What's Built and Working

| Component | Status | Key Code |
|-----------|--------|----------|
| **Database schema** | Complete (16 tables) | `db/models.py` — Project, Evidence, StatusHistory, ProjectIdentifier, ProjectRelationship, ProjectSourceRecord, SourceRun, ReviewItem, ReviewDecision, ChangeLog, DismissedRecord, DeveloperRegistry, DeveloperAlias, ResolutionLog + enums |
| **Alembic migrations** | 4 migrations applied | `alembic/versions/` — initial schema, seed fixes, LADBS rewire, evidence layer Phase 1 |
| **Pipedream ingester** | Complete | `ingesters/pipedream.py` — parses .xlsm DataStorage tab, 81 fields |
| **CoStar ingester** | Complete | `ingesters/costar.py` — parses .xlsx exports, header-based mapping |
| **Seed persistence** | Complete | `db/seed.py` — Pipedream + CoStar merge with dedup, relationship resolution |
| **Socrata collector** | Complete | `collectors/socrata.py` — async paginated, incremental mode, hash-based change detection |
| **LADBS source adapters** | Complete (6 adapters) | `source_adapters/ladbs.py` — permits + permit activity (pi9x-tg5x), inspections (9w5z-rg2h), CofO (3f9m-afei), 2 legacy frozen-dataset adapters |
| **Matcher** | Complete (3-tier) | `matching/matcher.py` — source_record → identifier → address |
| **Address normalizer** | Complete | `matching/normalizer.py` — usaddress parsing, directional/suffix/ordinal normalization |
| **Differ** | Complete | `matching/differ.py` — field change detection between project and incoming record |
| **Status rules** | Complete | `status_rules.py` — forward-only with evidence types |
| **Collect + persist pipeline** | Complete (evidence-aware) | `db/collect.py` — match → write evidence → upsert PSR → resolve project → diff → review items |
| **Evidence write layer** | Complete | `db/evidence.py` — write_raw_record_evidence, write_source_record_evidence, write_pipedream_snapshot_evidence, hash-based dedup |
| **Resolution engine** | Complete | `resolution/engine.py` — orchestrates 6 field resolvers, computes likelihood + confidence, writes ResolutionLog, appends StatusHistory |
| **Field resolvers** | Complete (all 6) | `resolution/fields/` — status, units, delivery_year, developer, product_type, age_restriction |
| **Likelihood engine** | Complete (v1) | `resolution/likelihood.py` — base rate per status + signal adjustments |
| **Confidence rollup** | Complete | `resolution/confidence.py` — field-level → project-level aggregation |
| **Source tier config** | Complete | `source_tiers.py` + `config/source_tiers.yaml` — logical source types, 4-tier hierarchy |
| **Developer canonicalization** | Complete | `developer/registry.py` — rapidfuzz matching, alias management, registry merging |
| **Review workflow** | Complete | `db/review_workflow.py` — accept/reject/defer with evidence relinking, resolution re-run |
| **Backfill scripts** | Complete (not yet run on prod) | `scripts/backfill_evidence.py`, `scripts/backfill_developers.py` |
| **CLI** | Complete | `cli.py` — doctor, preview/seed pipedream/costar, preview/collect source, resolve-all, canonicalize-developers |
| **Market config** | Complete (LA + SM stub) | `config/markets/los_angeles.yaml`, `config/markets/santa_monica.yaml` |
| **Tests** | Solid | 21 test files covering all major components |

### What's NOT Built

- No frontend / UI of any kind
- No API layer (no REST endpoints, no Supabase Edge Functions)
- No news article scraping or deep research integration
- No LAHD affordable housing collector (defined in config, no code)
- No PDF parser collector (la_case_reports — defined in config, no code)
- No ZIMAS/PDIS scraper (defined in config, no code)
- No CEQAnet collector
- No output formats (Excel export, exhibit appendix, CMA block)
- No delivery year freshness threshold (6-month rule, UC exemption)
- No auto-stall detection (12+ months no evidence → flag)
- No likelihood config externalization (base rates hardcoded in Python)
- No seed ingester evidence writes (seed path doesn't create evidence rows yet)
- No Santa Monica market implementation
- No multi-market generalization

---

## 3. Evidence Layer Architecture

> This section summarizes the evidence layer design. For the full specification, see `docs/specs/EVIDENCE_LAYER_INTEGRATION_GUIDE.md` and `docs/specs/EVIDENCE_LAYER_DECISIONS.md`.

### Core Concept

All incoming data — from collectors, seed imports, news articles, researcher input — is stored as **immutable evidence rows** in the `evidence` table. The `Project` record is never written to directly by collectors. Instead, a **resolution engine** reads all evidence for a project and computes the canonical field values using per-field rules.

### Source Tiers

| Tier | Sources | Role |
|------|---------|------|
| **Tier 0** | Researcher overrides | Highest authority. Two modes: `sticky` (never clobbered — legacy default) and `until_newer_evidence` (holds until genuinely newer evidence appears — new default for review-driven overrides). See EVIDENCE_LAYER_DECISIONS.md §21. |
| **Tier 1** | Government sources (LADBS, LAHD, ZIMAS, SM permits) + Pipedream | Authoritative public record + human-verified TCG research |
| **Tier 2** | News articles (BizJournals, etc.) | Timely intelligence, moderate confidence |
| **Tier 3** | CoStar, developer websites | Broad coverage, sometimes stale or inaccurate |
| **Tier 4** | Social media, forums | Weak signal, requires corroboration |

### Resolution Rules (per-field)

| Field | Rule | Notes |
|-------|------|-------|
| **status** | Forward-only progression, evidence-type gated | CofO→Complete (direct), inspection→UC (direct), permit alone→Approved (requires review), UC needs corroboration |
| **total_units** | Most recent evidence wins (any source) | |
| **affordable/market_rate units** | Most recent from allowlisted sources only | Pipedream, LAHD, SM Dev Tracking, news |
| **product_type** | Most recent explicit value wins | |
| **age_restriction** | Most recent explicit value wins | |
| **delivery_year** | Explicit source date > existing project value > estimation formula | Provenance tracked: explicit_government, explicit_tcg, etc. |
| **developer** | Most recent wins, then custom source priority as tiebreak + canonicalization | pipedream > news > developer_website > costar > ladbs (tiebreak only — newer evidence always wins over older regardless of source priority) |

### Data Flow

```
Source → Collector → RawRecord → match_raw_record()
                                       ↓
                              write_raw_record_evidence()  (append-only)
                                       ↓
                              _upsert_source_record()      (PSR cache, for matcher compat)
                                       ↓
                              resolve_project()            (recompute all fields from evidence)
                                       ↓
                              diff → ReviewItem            (flag changes for researcher)
```

---

## 4. Build Plan

### Phase A: Backfill & Validation
> **Goal:** Prove the evidence layer produces correct results against real data before building anything on top of it.
> **Priority:** Immediate — this is the prerequisite for everything else.
> **Depends on:** Nothing (all code exists, just needs to be run and validated).
> **Required reading:** `docs/specs/EVIDENCE_LAYER_INTEGRATION_GUIDE.md` (resolution rules, schema), `docs/specs/EVIDENCE_LAYER_DECISIONS.md` (edge cases, thresholds). For status evidence semantics: `ARCHITECTURE.md` Section 8 Decision Log entries from 2026-04-16 (LADBS permit → Approved not UC, CofO only with real date → Complete, inspections only recent+substantive → UC).

| Step | Task | Status | Notes |
|------|------|--------|-------|
| A.1 | Run `backfill_evidence.py --dry-run` against production DB | `done` | 2026-04-22: Dry-run succeeded on live DB. Would insert 1520 PSR evidence rows + 572 Pipedream snapshots, skip 572 Pipedream PSRs, 0 duplicate skips. Preflight found 2 existing orphan evidence rows, neither matched a current PSR. |
| A.2 | Run `backfill_evidence.py` (commit mode) | `done` | 2026-04-22: Committed successfully on live DB. Evidence table now totals 2094 rows (including 572 Pipedream snapshots and 2 pre-existing orphan rows). Post-commit dry-run was idempotent: 0 inserts, 2092 duplicate skips. |
| A.3 | Run `backfill_developers.py --dry-run` against production DB | `done` | 2026-04-22: Dry-run succeeded on live DB. Would insert 1 missing registry row (`Helio / UCLA`) and skip 313 existing developer names. |
| A.4 | Run `backfill_developers.py` (commit mode) | `done` | 2026-04-22: Committed successfully on live DB. Added 1 missing canonical developer (`Helio / UCLA`); registry now contains 319 rows. Post-commit dry-run was idempotent: 0 inserts. |
| A.5 | Run `resolve-all --market los_angeles` (shadow mode) | `blocked` | 2026-04-22/23: Partial shadow validation ran, but full sweep is not completing reliably through the current CLI/shell path. Timed-out runs leave stale `idle in transaction` Postgres backends that must be terminated manually. Clean run marker `2026-04-23T05:53:12Z` produced 411 new `resolution_log` rows across 75 projects before the blocker recurred. Need a robust batched runner or root-cause fix before full-market shadow validation can complete. |
| A.6 | Review resolution discrepancies, tune if needed | `not_started` | Manually inspect the discrepancy log. Are the resolved values more correct than current? Any systematic errors? |
| A.7 | Run `canonicalize-developers --market los_angeles` (shadow mode) | `not_started` | Review fuzzy matches and new registry entries before applying. |
| A.8 | Run `resolve-all --apply` + `canonicalize-developers --apply` | `not_started` | Depends on A.6 and A.7 confirming results are good. This is the point of no return — project fields will be overwritten with resolution engine values. |
| A.9 | Spot-check 10-20 projects post-apply | `not_started` | Manually verify field values, evidence rows, confidence, likelihood for a representative sample. |

### Phase B: Frontend — Project Explorer (Read-Only)
> **Goal:** A visual interface to browse projects, see evidence, understand what the system knows. Enables Nate and researchers to see data and think through the workflow.
> **Priority:** High — needed to inform all subsequent design decisions.
> **Depends on:** Phase A (need validated data to display).
> **Required reading:** `ARCHITECTURE.md` Section 3e (master project record — all fields and their types), Section 3d (Pipedream field inventory — what researchers are used to seeing), Section 6 (matching strategy — understand confidence scores displayed in UI). For evidence layer fields: `docs/specs/EVIDENCE_LAYER_INTEGRATION_GUIDE.md`.

| Step | Task | Status | Notes |
|------|------|--------|-------|
| B.1 | Set up Next.js project with Supabase JS client + Tailwind | `not_started` | Deploy to Vercel. Use Supabase PostgREST for data access — no custom API layer needed initially. |
| B.2 | Project list view | `not_started` | Sortable/filterable table: project name, address, status, confidence, likelihood, total units, developer, delivery year. Pagination. |
| B.3 | Project detail view | `not_started` | All project fields, current resolved values with confidence badges. Map pin if lat/lng exists. |
| B.4 | Evidence timeline for a project | `not_started` | Chronological list of all evidence rows for a project. Show source type, evidence date, extracted fields, raw data expandable. |
| B.5 | Resolution log view | `not_started` | Show discrepancies the engine detected: field, current vs. resolved, rule applied, confidence. |
| B.6 | Basic filtering and search | `not_started` | Filter by status, confidence level, likelihood range, developer. Text search on address/project name. |
| B.7 | Dashboard summary stats | `not_started` | Project counts by status, confidence distribution, evidence coverage (projects with 0/1/2+ sources), recently updated. |

### Phase C: Frontend — Review Queue & Researcher Workflow
> **Goal:** Researchers can act on the system's findings: accept new projects, confirm status changes, set overrides.
> **Priority:** High — this is the core value proposition for researchers.
> **Depends on:** Phase B (need the project views to contextualize review items).
> **Required reading:** `ARCHITECTURE.md` Section 4 (Layer 4: Review Workflow — priority tiers HIGH/MEDIUM/LOW, review item types, researcher decision semantics), Section 5 (Collection Workflow — understand the four-step execution order: Discovery → Status Updates → Enrichment → Review Queue, and why review items are generated at each step). For the backend accept/reject/defer logic: `src/tcg_pipeline/db/review_workflow.py`.

| Step | Task | Status | Notes |
|------|------|--------|-------|
| C.1 | Review queue list view | `not_started` | Pending review items sorted by priority. Show: review type, source, project (if matched), summary of proposed change. |
| C.2 | Review item detail view | `not_started` | Full context: the evidence that triggered the review, the project's current state, what the resolution engine would change. Side-by-side comparison. |
| C.3 | Accept / Reject / Defer actions | `not_started` | Wire to backend review_workflow.py functions. Accept links evidence to project and re-resolves. Reject creates DismissedRecord. Defer marks for later. |
| C.4 | Researcher override UI | `not_started` | Per-field override: researcher can set any project field manually with a note. Stored as structured `researcher_override` JSONB. Default mode is `until_newer_evidence` (holds until genuinely newer evidence appears); explicit `sticky` mode available for permanent locks. UI must expose the mode toggle and show when an override has been superseded. See EVIDENCE_LAYER_DECISIONS.md §21. |
| C.5 | Supabase Edge Functions or API routes for write operations | `not_started` | The review workflow logic lives in Python (`review_workflow.py`). Need to decide: (a) rewrite accept/reject/defer as Supabase Edge Functions (TypeScript), (b) build a thin Python API (FastAPI/Litestar) that the frontend calls, or (c) use Supabase database functions (PL/pgSQL). Decision needed at this step. |
| C.6 | Inclusion flags UI | `not_started` | Toggle `inclusion_in_analysis` and `inclusion_in_exhibit` per project, with note field. Sticky — persists across resolution runs. |
| C.7 | Change history view | `not_started` | Show ChangeLog entries for a project: who changed what, when, from what value to what value. |

### Phase D: News Scraping & Deep Research
> **Goal:** Automated collection of news articles about development projects. First source: BizJournals LA. Enriches the evidence layer with Tier 2 signals.
> **Priority:** High — fills critical intelligence gaps that government sources and CoStar miss (developer announcements, project milestones, community opposition, timeline updates).
> **Depends on:** Phase A (evidence layer must be validated). Can be built in parallel with Phase B/C frontend work.
> **Required reading:** `config/source_tiers.yaml` (Tier 2 = news_article), `ARCHITECTURE.md` Section 5 (collection workflow — how discovery vs. enrichment sources differ; news is discovery+enrichment, not lookup-only like ZIMAS). For matching extracted article references to projects: `ARCHITECTURE.md` Section 6 (matching strategy).

| Step | Task | Status | Notes |
|------|------|--------|-------|
| D.1 | Design news article evidence schema | `not_started` | Define `extracted_fields` structure for articles: project references, status signals, developer mentions, unit counts, delivery dates, sentiment. Define `signal_flags` for likelihood engine. |
| D.2 | Build BizJournals scraper | `not_started` | Authenticated scraper for `bizjournals.com/losangeles`. Login with Nate's credentials. Scrape real estate / development articles. Respect rate limits. Store raw article text + metadata. |
| D.3 | Build article NLP extraction pipeline | `not_started` | Extract structured fields from article text: project names, addresses, developers, unit counts, status signals, dates. LLM-assisted extraction (Claude API) is likely the right approach given the unstructured nature of news text. |
| D.4 | Article → Evidence integration | `not_started` | Match extracted project references to existing projects (or flag as new candidates). Write evidence rows with `source_type=news_article`, `source_tier=2`. Resolution engine already handles Tier 2. |
| D.5 | Article review queue | `not_started` | Extracted findings should be surfaced as review items, not auto-applied. Researcher confirms: "yes, this article is about project X and says Y." |
| D.6 | Scheduled article collection | `not_started` | Periodic scrape (daily or every few days). Incremental — track which articles have been processed. |
| D.7 | Researcher paste-a-link flow | `not_started` | Future: researcher pastes a URL, system fetches + extracts + creates evidence. Supports any news source, not just BizJournals. |
| D.8 | Additional news sources | `deferred` | The Architect, Urbanize LA, Curbed LA, LA Times real estate section. Add after BizJournals pipeline is proven. |

### Phase E: Resolution Engine Refinements
> **Goal:** Implement remaining spec features that improve data quality.
> **Priority:** Medium — improves quality but system works without these.
> **Depends on:** Phase A. Can be built in parallel with other phases.

| Step | Task | Status | Notes |
|------|------|--------|-------|
| E.1 | Delivery year freshness threshold | `not_started` | Source-provided dates older than 6 months should be treated as stale. UC projects exempt from freshness check. See EVIDENCE_LAYER_DECISIONS.md for full rules. |
| E.2 | Auto-stall detection | `not_started` | Flag projects with 12+ months of no new evidence as potentially stalled. Generate review items, don't auto-change status. |
| E.3 | Externalize likelihood config to YAML | `not_started` | Move base rates and signal weights from `likelihood.py` to `config/likelihood.yaml`. Makes tuning easier without code changes. |
| E.4 | Wire seed ingesters to write evidence | `not_started` | Modify `db/seed.py` so future Pipedream/CoStar seed imports also create evidence rows. Currently only the collector path and backfill scripts write evidence. |
| E.5 | Delivery year size adjustment | `not_started` | The estimation formula supports a size adjustment but it's hardcoded to 0. Implement: large projects (500+ units) add 1 year to estimate. |

### Phase F: Additional Collectors
> **Goal:** Expand LA source coverage beyond LADBS.
> **Priority:** Lower — LADBS is the primary lifecycle source and it's already working. These add incremental coverage.
> **Depends on:** Phase A. Independent of frontend work.
> **Required reading:** `ARCHITECTURE.md` Section 4b (source-by-source analysis — access methods, endpoints, field coverage, SoQL filters, rate limits for every source), Section 4c-4d (source role summary and collector type mapping), Section 5 (collection workflow — pull vs. lookup pattern, execution order, cycle timing). Critical: ZIMAS is lookup-only (no bulk export) — case numbers must come from other sources first. LA Case Reports drives the biweekly cadence.

| Step | Task | Status | Notes |
|------|------|--------|-------|
| F.1 | LAHD affordable housing collector | `not_started` | Socrata mymu-zi3s / an7z-aq2k. Reuses Socrata collector + new source adapter. Fills affordable housing gap. |
| F.2 | LA Case Reports PDF collector | `not_started` | Biweekly PDF API: `planning.lacity.gov/dcpapi/general/biweeklycase/doc/{id}`. Parser with pdfplumber. Early warning for large projects in entitlement. |
| F.3 | ZIMAS/PDIS scraper (enrichment) | `not_started` | Accepts case number → scrapes PDIS page → returns structured fields. NOT for bulk discovery. Depends on case numbers flowing in from other sources. |
| F.4 | Case number chaining | `not_started` | Chain from LA Case Reports → ZIMAS, from Pipedream Site1 URLs → PDIS, from LADBS permits → case lookups. |
| F.5 | CEQAnet collector | `not_started` | Custom scraper — early warning for large projects with environmental review. |
| F.6 | LA County planning collector | `deferred` | Socrata ccmr-xemc. Don't wire in until a county or unincorporated market exists. |

### Phase G: Output Formats
> **Goal:** Export pipeline data for researcher consumption and client deliverables.
> **Priority:** Lower — useful once the data is validated and the workflow is proven.
> **Depends on:** Phase A + Phase C (need validated data + researcher review workflow established).

| Step | Task | Status | Notes |
|------|------|--------|-------|
| G.1 | Excel template export | `not_started` | Export project data to the format researchers are used to (Pipedream-like layout). |
| G.2 | Summary tables / CMA block | `not_started` | Market-level summary statistics in tabular form. |
| G.3 | Exhibit appendix generation | `not_started` | Formatted project-by-project appendix for client reports. |
| G.4 | Evidence provenance export | `not_started` | Per-project source audit: which sources contributed, when, what they said. |

### Phase H: Santa Monica Market
> **Goal:** Prove multi-market by standing up SM with its distinct source types (PDF-heavy + Socrata + Accela).
> **Priority:** After LA is stable.
> **Depends on:** Phases A-C minimum, ideally F.2 (PDF parser reusable for SM).
> **Required reading:** `ARCHITECTURE.md` Section 4b sources 4-6 (SM Dev Tracking PDF, SM Ministerial PDF, SM Active Permits Socrata — access methods, URL patterns, field coverage). SM Dev Tracking is SM's "gold standard" discovery source (457 projects, weekly PDF). Accela API is optional — Socrata covers most needs.

| Step | Task | Status | Notes |
|------|------|--------|-------|
| H.1 | Write SM market config | `not_started` | |
| H.2 | Configure SM Dev Tracking PDF collector | `not_started` | Reuses PDF parser. Predictable monthly URL pattern. |
| H.3 | Configure SM Ministerial PDF collector | `not_started` | Cross-ref with Dev Tracking on address + permit number. |
| H.4 | Configure SM Active Permits (Socrata kpzy-s8rg) | `not_started` | Reuses Socrata collector. |
| H.5 | (Optional) Build Accela collector for SM ePermit detail | `not_started` | OAuth required. Lower priority. |
| H.6 | Seed SM with CoStar + run full cycle | `not_started` | |

### Phase I: Generalization & Additional Markets
> **Goal:** Template for standing up any new market quickly.
> **Priority:** After SM proves the pattern.
> **Depends on:** Phase H.

| Step | Task | Status | Notes |
|------|------|--------|-------|
| I.1 | Document market onboarding process | `not_started` | What sources does a new market need? How to configure? |
| I.2 | Build market config template + validation | `not_started` | |
| I.3 | Formalize source registry + adapter contract | `not_started` | |
| I.4 | Plan `ProjectMarketMembership` migration for overlapping markets | `not_started` | Required before any city + county overlap goes live. |
| I.5 | Select and stand up third market (different metro) | `not_started` | True generalization test. |

---

## 5. Completed Work — Historical Build Phases

> These sections record what was built before this roadmap was created, for reference. They correspond to the original build plan in ARCHITECTURE.md Section 7.

### Original Phase 1: Foundation (Complete)

All steps done. Database schema, Pipedream + CoStar ingesters, address normalization, seed persistence. See ARCHITECTURE.md Section 7 Phase 1 for details.

### Original Phase 2: First Public Source + Matching (Complete)

Socrata collector, LADBS permit adapters, matcher, differ, status rules, collect+persist pipeline, incremental cursor, source-row metadata. See ARCHITECTURE.md Section 7 Phase 2 for details. Note: steps 2.4, 2.5, 2.10 were marked `in_progress` in the original doc but are functionally complete — the remaining gaps (broader field-level diffing, reconciliation reporting) are now handled by the evidence layer.

### Original Phase 3: Discovery + Enrichment (Partially Complete)

Steps 3.0 through 3.0f (LADBS source bundle expansion, recall audit, Socrata coverage diagnosis, source rewire) are all done. Steps 3.1–3.5 (PDF parser, LA Case Reports, ZIMAS, case number chaining, discovery cycle) are not started and have been moved to Phase F of this roadmap.

### Evidence Layer Retrofit (Complete — Phases 1-4 of retrofit plan)

This was a major architectural addition not in the original build plan. Documented in `docs/specs/evidence_layer_retrofit_plan.md`. All four core phases are complete:
- **Retrofit Phase 1:** Schema + backfill infrastructure (migration, evidence table, developer tables, new project columns, backfill scripts)
- **Retrofit Phase 2:** Resolution engine + all field resolvers + likelihood + confidence
- **Retrofit Phase 3:** Collector refactor (evidence writes wired into collect.py, dual-write with PSR, resolve_project called after evidence insert)
- **Retrofit Phase 4:** Developer canonicalization (registry, alias management, fuzzy matching, CLI commands)

---

## 6. Tech Stack

### Infrastructure
- **Database:** Supabase (hosted PostgreSQL with PostGIS + pg_trgm)
- **Backend / collectors:** Python 3.11+
- **Frontend:** Next.js (React) on Vercel, Supabase JS client, Tailwind CSS
- **Hosting:** Render for backend workers/cron jobs, Vercel for frontend, Supabase for database

### Key Python Dependencies
`httpx`, `sqlalchemy` + `psycopg`, `alembic`, `geoalchemy2`, `usaddress`, `rapidfuzz`, `openpyxl`, `pydantic` + `pydantic-settings`, `typer`, `tenacity`

### Frontend Dependencies (Phase B+)
`next`, `@supabase/supabase-js`, `tailwindcss`

### News / Research Dependencies (Phase D)
`playwright` or `httpx` + `beautifulsoup4` (for BizJournals scraping), `anthropic` (Claude API for article extraction)

See ARCHITECTURE.md Section 10 for the full stack listing.

---

## 7. Key Reference Documents

| Document | Location | Purpose |
|----------|----------|---------|
| Architecture & Design Spec | `ARCHITECTURE.md` | Detailed data model, source inventory, field inventories, matching strategy, collection workflow. The original design document. |
| Evidence Layer Integration Guide | `docs/specs/EVIDENCE_LAYER_INTEGRATION_GUIDE.md` | Comprehensive guide to the evidence layer: schema, resolution rules, integration points. Written for Claude Code / Codex agents. |
| Evidence Layer Decisions | `docs/specs/EVIDENCE_LAYER_DECISIONS.md` | Implementation decisions answering developer questions about edge cases, thresholds, and design tradeoffs. Includes conditional override semantics (§21), STATUS_CHANGE rejection (§21a), observation ordering (§21b), and delivery-date override provenance (§21c). This is a living document — check for recent additions. |
| Evidence Layer Retrofit Plan | `docs/specs/evidence_layer_retrofit_plan.md` | Initial retrofit assessment: repo audit, schema changes, migration strategy, effort estimates. |
| LADBS Source Audits | `docs/audits/` | Recall audits, coverage analysis, source rewire verification for LADBS Socrata datasets. |
| Market Config (LA) | `config/markets/los_angeles.yaml` | Source definitions for the Los Angeles market. |
| Source Tier Config | `config/source_tiers.yaml` | Source type → tier mapping for the evidence layer. |

---

## 8. Decision Log

> Append-only. Record significant decisions with date, what was decided, and why. Don't edit old entries — add new entries that supersede them.
>
> **Historical decisions:** `ARCHITECTURE.md` Section 8 contains the full decision log from project inception (2026-04-15 onwards) — approximately 90 entries covering source-specific semantics, ingestion rules, matching thresholds, schema design choices, and LADBS evidence type rules. This ROADMAP carries forward the most architecturally significant entries below. For source-specific implementation decisions (e.g., "only final CofO with real date emits Complete evidence," "only recent substantive inspections emit UC evidence," "CoStar maps by header name not column number"), always consult ARCHITECTURE.md Section 8.

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-04-15 | CoStar + Pipedream as seed sources, public data for updates/discovery | CoStar gives breadth, Pipedream gives depth. Public sources fill gaps and keep things current. |
| 2026-04-15 | Pipedream/TCG data takes priority over CoStar where both exist | Human-verified data is more reliable than CoStar's automated collection. |
| 2026-04-15 | Researcher overrides are protected from automated updates | Prevents the system from clobbering human judgment. |
| 2026-04-15 | Start with LA as proof of concept, design for multi-market | Validates architecture on a real market without over-engineering. |
| 2026-04-15 | Supabase (hosted PostgreSQL) from day one, no SQLite phase | PostGIS, dashboard, auth, REST API, realtime for future UI. |
| 2026-04-16 | LADBS treated as a source bundle, not a single endpoint | Multiple datasets needed for full lifecycle coverage: permits, inspections, CofO. |
| 2026-04-17 | Rewire LADBS sources from frozen hbkd-qubn/cpkv-aajs to live pi9x-tg5x/9w5z-rg2h | Frozen datasets were causing false not-found results. Live replacements recover ~50 of 71 missing UC projects. |
| 2026-04-20 | Add evidence layer — append-only evidence store with resolution engine | ProjectSourceRecord upsert model loses history. Evidence layer preserves all observations, enables confidence scoring, supports per-field resolution rules. Major architectural addition. |
| 2026-04-20 | Keep ProjectSourceRecord as "current state cache" alongside evidence | Preserves backward compatibility with matcher during incremental migration. Can be removed later. |
| 2026-04-20 | Source tiers: Tier 0 (researcher), Tier 1 (government + Pipedream), Tier 2 (news), Tier 3 (CoStar), Tier 4 (social) | Establishes hierarchy for resolution rules. Researcher overrides are sacred Tier 0. |
| 2026-04-20 | Developer canonicalization: rapidfuzz token_set_ratio, >=90 auto, 75-89 flag, <75 new | Balances automation with human review for ambiguous matches. |
| 2026-04-20 | Hash-based evidence dedup | Only insert new evidence when raw_data_hash changes. Prevents table bloat from incremental overlap re-pulls. |
| 2026-04-22 | Frontend prioritized before additional collectors and Excel outputs | Need visual understanding of data to inform researcher workflow design. Can't design the tool without seeing the data. |
| 2026-04-22 | News scraping (BizJournals) prioritized before remaining collectors | Tier 2 news fills intelligence gaps (developer announcements, timelines, opposition) that government sources miss. Higher value per source than incremental Tier 1 coverage. |
| 2026-04-22 | De-prioritize LAHD, LA Case Reports, ZIMAS, Excel outputs | These are incremental improvements. The core pipeline (LADBS → evidence → resolution → review) is already working. Circle back after frontend + news are proven. |
| 2026-04-22 | Build plan moved from ARCHITECTURE.md to ROADMAP.md | ARCHITECTURE.md is the design reference. ROADMAP.md is the living build plan. Separation prevents the design doc from becoming stale as priorities shift. |
| 2026-04-22 | Researcher overrides are conditional by default (`until_newer_evidence`), not unconditionally sacred | Refinement of earlier "Tier 0 never clobbered" rule. Review-driven overrides hold until genuinely newer evidence appears. Sticky mode still available for explicit permanent locks. Legacy overrides without `mode` treated as sticky for backward compat. See EVIDENCE_LAYER_DECISIONS.md §21. |
| 2026-04-22 | STATUS_CHANGE rejection writes a conditional override and re-resolves | Rejecting a status change blocks that evidence from re-applying but doesn't block newer evidence. Override baseline is copied from current resolution. See EVIDENCE_LAYER_DECISIONS.md §21a. |
| 2026-04-22 | Observation ordering: evidence_date → collected_at → source priority → source_tier | Closes bug where older high-priority source could beat newer lower-priority source. Re-running resolve-all may shift some developer values. See EVIDENCE_LAYER_DECISIONS.md §21b. |
| 2026-04-22 | delivery_year_provenance set to `researcher_override` when date_delivery comes from override | Returns to evidence-derived provenance when override is superseded. See EVIDENCE_LAYER_DECISIONS.md §21c. |
| 2026-04-16 | LADBS permit issuance = Approved evidence, not UC proof | TCG status definitions put first permit issuance inside Approved; UC requires visible vertical construction. Resolution engine flags permit-alone as requires_review. |
| 2026-04-16 | Only final CofO with real `cofo_issue_date` emits Complete evidence | Corrected/reactivated/superseded CofO rows remain source detail until explicitly modeled. |
| 2026-04-16 | Only recent, substantive inspections on active permits emit UC evidence | Adapter persists all inspection context but only emits `building_inspection_recorded` when recent + substantively positive + permit in active status. |
| 2026-04-16 | Periodic full reconciliation required for Socrata sources | Catches source-side corrections, row disappearance, and stale-logic bugs in our own earlier filters. |
| 2026-04-16 | Source-row disappearance tracked first, not auto-applied | If a Socrata row vanishes between runs, treat as source-state event until repeated absence or stronger evidence justifies changing project state. |
| 2026-04-15 | Pipedream status values adopted as canonical status enum | Well-defined and used by researchers daily. Public source statuses get mapped to these. |
| 2026-04-15 | Match conservatively, track liberally | When values change (address, units, name), that's a field change in ChangeLog, not a matching failure. Store all aliases in raw_addresses[] and previous_names[]. |
| 2026-04-15 | Auto-match threshold >=0.85, ambiguous 0.65-0.84, weak 0.40-0.64, no match <0.40 | Balances automation with researcher oversight. Most deterministic and address matches auto-link. Proximity + fuzzy matches get human review. |
| 2026-04-15 | Four-step execution order: Discovery → Status Updates → Enrichment → Review Queue | Discovery finds new projects, Status Updates tracks existing ones, Enrichment deepens via ZIMAS (triggered by new case numbers), Review Queue prioritizes researcher attention. |
| 2026-04-15 | ZIMAS is enrichment-only, not discovery | No programmatic address→case lookup exists. Case numbers must come from other sources (LA Case Reports, LADBS, Pipedream URLs). |
| 2026-04-15 | Scope includes all development types, not just residential | Pipeline tracks rental, for-sale, and commercial. Pipedream is residential-focused but system accepts commercial projects from CoStar and public sources. |

---

## 9. Open Questions

> Items to resolve as we build. Move to Decision Log when resolved.

- [ ] **Review workflow backend for frontend:** Should accept/reject/defer be (a) Supabase Edge Functions (TypeScript), (b) Python API (FastAPI), or (c) PL/pgSQL database functions? Affects Phase C.5.
- [ ] **BizJournals scraping legality/TOS:** Confirm that authenticated scraping of BizJournals with a paid subscription is acceptable for this use case.
- [ ] **Article extraction approach:** LLM-assisted (Claude API) vs. rule-based NLP for extracting structured fields from news articles. LLM is more flexible but has cost and latency implications.
- [ ] **Pipedream ongoing sync:** After initial seed, will researchers continue to update Pipedream files? If so, need a recurring import/sync process. Or does this system replace Pipedream entirely?
- [ ] **Non-MF project handling:** CoStar provides 231 non-MF projects (offices, hotels, retail). How should they appear in the review interface? Separate section? Same queue with a property type filter?
- [ ] **Unit count threshold for new project candidates:** Minimum unit count for the system to flag a new LADBS project? Pipedream's smallest are ~10-20 units.
- [ ] **Update frequency:** How often should each source be polled? Weekly LADBS? Daily news? Biweekly full cycle?
- [ ] **Source disappearance policy:** When a Socrata row vanishes, how many absent reconciliations before changing project state?
- [ ] **Stalled/inactive evaluator timing:** 12 months is the current spec. Is that the right threshold? Should it vary by status?
- [ ] **CoStar city name normalization:** CoStar MF exports use "Los Angeles", "Los Angeles CBD", "Downtown Los Angeles", "Hollywood", etc. Need a mapping table for MF city normalization. Non-MF is clean. Affects Phase A backfill data quality. (From ARCHITECTURE.md q. 2048)
- [ ] **Pipedream relationship ID format:** `CorrP`, `PCPart`, and `RelP1-6` normalize abbreviated numeric IDs by zero-padding. Verify against a real workbook whether relationship fields always use full 5-digit sequences or sometimes abbreviate. Affects Phase A backfill correctness. (From ARCHITECTURE.md q. 2051)
- [ ] **Pipedream "Pending" → public source status mapping:** Pipedream "Pending" means in entitlement. LADBS and ZIMAS use different terminology. Need a mapping table. Same for "Stalled" detection from absence of activity. (From ARCHITECTURE.md q. 2055)
- [ ] **Overlapping market support:** `Project.market` is sufficient while markets don't overlap. Before any city + county datasets go live, need `ProjectMarketMembership` model. (From ARCHITECTURE.md q. 2065)
- [ ] **Boundary-based routing:** Current city-scoped filtering relies on source coverage declarations + `--allowed-city`. Before county/metro expansion, define how to do boundary checks from parsed address, parcel, or geometry. (From ARCHITECTURE.md q. 2067)

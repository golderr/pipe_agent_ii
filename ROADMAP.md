# TCG Pipeline Tracker — Roadmap & Current Build Plan

> **This is the active build plan.** It supersedes the build plan (Section 7) in `ARCHITECTURE.md`.
> For detailed source specifications, field inventories, matching strategy, and data model definitions, refer to `ARCHITECTURE.md`. That file remains the reference for *what the system is*; this file is the reference for *what to build next and what has been built*.

**Last updated:** 2026-04-24
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
| **Tier 0** | Researcher overrides | Highest authority, but review-protected rather than permanently sticky. New contradicting evidence creates a review item; it never silently replaces or silently yields to the override. See EVIDENCE_LAYER_DECISIONS.md §22. |
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
| A.5 | Run `resolve-all --market los_angeles` (shadow mode) | `done` | 2026-04-23: Full-market shadow sweep completed with the batched runner. Current `resolution_log` is de-duplicated per project and contains 7,519 rows across all 1,362 LA projects. Field counts: 1,362 each for confidence_reason, delivery_year_provenance, last_evidence_date, likelihood, likelihood_breakdown; 292 confidence; 253 date_delivery; 128 developer; 27 total_units; 9 pipeline_status. |
| A.6 | Review resolution discrepancies, tune if needed | `done` | 2026-04-23: Phase A review packet regenerated from the final LA shadow rerun (`docs/notes/phase_a_review/reshadow_run_postfix.log`). Final packet counts: `status_review.csv` (9), `units_review.csv` (27), `delivery_review.csv` (34 explicit overwrites), `delivery_estimate_spotcheck.csv` (10 sampled from 218 estimated fills), `developer_review.csv` (161 judgment rows), `developer_category_cleanup.csv` (84 FYI rows), `developer_canonical_cleanup.csv` (59 exact alias/canonical cleanup rows), `developer_helio_ucla_cluster.csv` (10), `developer_alias_candidates.csv` (0 after alias adds + rerun). Review decisions: accept all 9 status rows; accept all explicit delivery rows; accept all estimated delivery fills as policy; accept unit deltas `<= 5`, hold larger deltas with conditional overrides; accept Category/canonical developer cleanup rows; accept remaining developer rows except architecture-firm and raw-value override exceptions captured in the apply profile. Validation memo: `docs/notes/phase_a_validation.md`. |
| A.7 | Run `canonicalize-developers --market los_angeles` (shadow mode) | `done` | 2026-04-23: Shadow run completed with no writes. After deleting the polluted `Category` registry row and its aliases in production, the rerun scanned 318 registry rows and 967 projects with non-null developer values; would change 0 project values in this mode. Match summary: 1,134 exact, 2 fuzzy-auto, 13 fuzzy-review, 0 new registry-entry candidates. `--apply` now skips fuzzy-review registry merges and project developer rewrites so ambiguous matches remain shadow-only until reviewed. Later reruns also split exact alias/canonical cleanup rows out of manual review into `developer_canonical_cleanup.csv`. |
| A.8 | Run `resolve-all --apply` + `canonicalize-developers --apply` | `done` | 2026-04-23: Bucket-level decision profile `phase_a_2026_04_23` dry-ran cleanly on 384 CSV rows (`360` accept, `20` defer, `4` override) and wrote 24 conditional overrides before apply. LA apply completed across all 1,362 projects using the stable batched runner (`scripts/run_phase_a_resolve.py`): an initial 76-project apply plus a resumed 1-project batch sweep recorded in `docs/notes/phase_a_review/phase_a_apply_resume.log`. Resume summary: 1,286 projects resolved, 7,234 resolution-log rows written, changed fields `confidence` 280, `date_delivery` 233, `developer` 281, `pipeline_status` 9, `total_units` 6. `canonicalize-developers --apply` then scanned 316 registry rows, merged 1 row, created 172 rows, created 5 aliases, and changed 26 project developer values. |
| A.9 | Spot-check 10-20 projects post-apply | `done` | 2026-04-23: Post-apply verification passed on representative status, units, delivery-date, and developer cases. Held overrides stayed intact for 20 large-unit defers and 4 developer exceptions; current DB now shows 23 projects with researcher overrides and 0 override/value mismatches. Spot checks confirmed: Miles at Highland is `Complete` with a non-future delivery date, large-unit defer and small-unit accept cases landed correctly, and architecture-firm developer exceptions (`NOW`, `Pico Gateway Apartments`, `2023 WESTWOOD BOULEVARD`) kept the current developer. One project (`Lake on Wilshire`) was re-resolved after `canonicalize-developers --apply` clobbered a raw developer override; code now prevents that path from recurring. |

### Phase B: Frontend — Read-Only Explorer
> **Goal:** A read-only interface over the validated pipeline data. Researchers can browse, search, and inspect projects + evidence but cannot yet edit. Sets expectations: this is not yet the Pipedream replacement — it's the preview that validates the data.
> **Priority:** High — informs all subsequent design decisions.
> **Depends on:** Phase A validated data, plus the three schema prerequisites below (B.0a, B.0b, B.0c).
> **Required reading:** `docs/specs/ui_requirements.md` (primary UI spec), `docs/specs/data_model_changes.md` (schema changes required), `docs/specs/field_inventory.md` (field class audit — prerequisite), `docs/specs/EVIDENCE_LAYER_INTEGRATION_GUIDE.md`, `ARCHITECTURE.md` Section 3d (Pipedream field inventory), Section 3e (master project record), Section 6 (matching strategy).

| Step | Task | Status | Notes |
|------|------|--------|-------|
| B.0 | **Prerequisite:** take a production DB snapshot before schema migrations | `done` | 2026-04-24: `pg_dump` custom-format schema+data snapshot written to ignored local path `data/output/db_snapshots/prod_snapshot_20260424_184916.dump` (3,155,000 bytes). SHA256 `32840FCEE59A4784963A6CC329160A799DEB62A8D388E999486520DC887DAF6D`. `pg_restore -l` read the archive TOC successfully. |
| B.0a | **Prerequisite:** complete the field inventory audit | `done` | 2026-04-24: Audited `docs/specs/field_inventory.md` against live SQLAlchemy models and source ingesters/adapters. Added missing live `Project` fields (`applicant`, `description`, CoStar physical fields, lifecycle/planning fields, geocode/status/review metadata). Verification script confirmed 0 unclassified live `Project` columns remain. |
| B.0b | **Prerequisite:** data model migrations for jurisdictions + markets | `done` | 2026-04-24: Added `markets` and `jurisdictions` tables, seeded `los_angeles` / `city_of_los_angeles`, added nullable `projects.market_id` and `projects.jurisdiction_id`, and applied migrations through `202604240005` to production. Backfill verification: 1,362 `los_angeles` projects, 0 missing `market_id`, 0 missing `jurisdiction_id`. Existing `projects.market` / `projects.jurisdiction` string columns remain in place until all CLI, collector, matcher, and UI reads have migrated. |
| B.0c | **Prerequisite:** source_runs expansion + source_registrations table | `done` | 2026-04-24: Added `source_registrations` and seeded 7 City of LA sources from `config/markets/los_angeles.yaml`. Expanded `source_runs` with `jurisdiction_id`, `trigger_type`, user/job metadata, finish time, row counts, and error text. Production verification: 7 source registrations, 7 historical source runs, 0 null `trigger_type` values. |
| B.1 | Next.js scaffolding + Supabase + shadcn + auth + nav shell + one live read path | `done` | 2026-04-25: Vercel project `the-concord-group/tcg-pipeline` is linked and deployed to production at `https://tcg-pipeline.vercel.app`; production env vars are set and Supabase Auth redirect URLs were configured. Production smoke passed: `/login` 200, `/coverage` 307 to `/login?next=%2Fcoverage`, disallowed email submit 303 to `/login?error=not_allowed`, allowed `ng@theconcordgroup.com` submit 303 to `/login?sent=1`, real inbox magic-link click lands on authenticated Coverage with jurisdiction rows visible. Added `.vercelignore` so local secrets/data are not uploaded by CLI deployments. Preview env vars are not set yet because the CLI required branch-scoped Preview variables in this session; set them from the dashboard before relying on preview deployments. Original B.1 implementation checks passed: `npm run typecheck`, `npm run lint`, `npm run build`, and `npm audit --audit-level=moderate`. |
| B.2 | Coverage view | `done` | 2026-04-25: Coverage view implemented and visible in production after authenticated magic-link smoke. Includes Supabase-backed jurisdiction/project/source/review-item aggregation, filter bar, session-sticky filters, local-only pins, optional columns, queue priority counts, source freshness, expandable source detail, and disabled Phase C actions. Added read-only authenticated RLS for `review_items` so Coverage can show queue/deferred counts without opening writes. `npm run typecheck`, `npm run lint`, `npm run build`, `npm audit --audit-level=moderate`, and `pytest -q` passed. Refresh and Upload buttons remain disabled until Phase C. |
| B.3 | Pipeline list view (table + map) | `done` | 2026-04-25: Implemented read-only Pipeline with Supabase-backed project/latest-evidence/APN data, dense sortable table, collapsible filter sidebar, sticky filters, local saved views, row hover preview, keyboard navigation (`j`/`k`, Enter, `/`, Escape), command search, read-only preview drawer, and tokenless MapLibre map view with configurable raster tiles and status-colored clustered pins. Added `project_latest_evidence` Postgres read view so Pipeline no longer fetches the full `evidence` table. New Project remains disabled until Phase C. Verified with `npm run typecheck`, `npm run lint`, `npm run build`, `npm audit --audit-level=moderate`, and `python -m pytest -q`. Deployed to production at `https://tcg-pipeline.vercel.app`; logged-out `/pipeline` redirects to `/login?next=%2Fpipeline`. |
| B.4 | Project Detail — Snapshot tab | `done` | 2026-04-25: Implemented direct Project Detail route at `/pipeline/[projectId]` and wired Pipeline table rows, command search, keyboard Enter, and map popups to open it. Snapshot is read-only and renders Core, Source Facts, Identity, Notes, Relationships, and Computed sections with field-class badges, source badges, hover provenance popovers, and amber highlights for fields in open review items. Added `project_field_resolution` Postgres read view over latest `resolution_log` rows for per-field source/rule/confidence data. Verified with `npm run typecheck`, `npm run lint`, `npm run build`, `npm audit --audit-level=moderate`, and `python -m pytest -q`. |
| B.5 | Project Detail — Evidence tab | `done` | 2026-04-25: Implemented read-only Evidence tab at `/pipeline/[projectId]?tab=evidence`. It renders the project's evidence rows chronologically with month dividers, native collapsible rows, generic snippets, extracted-field summaries, raw JSON expansion, source URL links from `project_source_records`, and server-rendered filters for field, source, and date range. Snapshot source badges now link into the Evidence tab filtered to that field. Source-specific snippet renderers and suspect-row writes remain deferred. Verified with `npm run typecheck`, `npm run lint`, `npm run build`, `npm audit --audit-level=moderate`, and `python -m pytest -q`. |
| B.6 | Project Detail — Resolution + Changes + Overrides tabs | `done` | 2026-04-26: Added read-only Project Detail tabs for Resolution, Changes, and Overrides. Resolution reads latest per-field rows from `project_field_resolution`, separates changed resolver output from collapsed unchanged tracked output, and shows current/resolved values, rule, confidence, linked evidence, and evidence drill-through only when evidence rows are linked. Changes reads both `change_log` review-commit rows and `status_history` lifecycle rows so the tab has useful pre-Phase-C activity. Overrides reads the legacy `projects.researcher_override` JSONB payload and shows active override metadata plus captured baselines when present. Alternatives considered and superseded override history are not currently stored, so the UI states those Phase C limitations honestly. |
| B.7 | Dashboard (5 tiles) | `done` | 2026-04-26: Implemented the read-only Dashboard at `/dashboard` with five tiles from `docs/specs/ui_requirements.md` §18: Needs Attention, Stalled Candidates, Contradictions, Pipeline By Status, and Recent Activity. Tiles use existing Phase B read access only: projects, review_items, recent evidence, source_runs, and markets. Pipeline status tile links now initialize Pipeline status filters from `?status=` query params. Stalled candidates are a read-only heuristic for Approved/U/C projects with real evidence dates older than 12 months; formal stall review-item generation remains Phase E. Contradictions renders as a Phase C inactive state until contradiction-type review items exist. Needs Attention links to Coverage until the Review Queue is built. |

### Phase C: Write Path + Review Queue
> **Goal:** Researchers can edit any field, create new projects, review the system's proposed changes, and commit decisions in batch. This phase is where the tool replaces Pipedream.
> **Priority:** High — core value proposition. Follows Phase B.
> **Depends on:** Phase B (read-only surfaces give researchers context), FastAPI backend scaffolding, contradiction detection service.
> **Structure:** Split into two sequenced halves. C-early ships editing (unlocks Pipedream replacement workflow). C-late ships the review queue (unlocks system-proposed-change review).
> **Required reading:** `docs/specs/review_workflow.md`, `docs/specs/ui_requirements.md`, `docs/specs/EVIDENCE_LAYER_DECISIONS.md` §22 (review-protected override semantics).

#### C-early: Inline editing + new project creation

| Step | Task | Status | Notes |
|------|------|--------|-------|
| C.a | FastAPI backend scaffolding | `done` | 2026-04-26: Added FastAPI write-path scaffold under `src/tcg_pipeline/api/` with health/readiness, Supabase JWT verification plus auth-server fallback, allowed-email enforcement, DB session dependency, C.a protected route stubs, API tests, env/docs updates, and Render runbook. Verified `pytest`, `npm run lint`, `npm run typecheck`, `npm run build`, and scoped ruff for the new API files. |
| C.a-bis | Staging write environment gate | `in_progress` | Before any real Phase C write path is used from Vercel previews, stand up and verify staging Supabase + staging FastAPI/Render or explicitly re-decide the preview write policy. Production Supabase previews were acceptable for Phase B read-only; Phase C writes need an isolated target or a documented exception. 2026-04-26: Added repeatable `researcher_overrides` migration verification commands/runbook for the pre-C.d gate, including metadata mismatch checks, index/RLS/policy/grant checks, pre/post resolution snapshots, and verbose drift output. Current pre-migration compare was only a tool sanity check; C.a-bis is still not complete until the migration is applied and verified against staging or a snapshotted DB and preview write routing policy is decided. |
| C.a.1 | Developer registry + apply-script hardening | `done` | 2026-04-26: Moved forward from E.3a to unblock Phase D news ingestion. Added `scripts/audit_developer_registry.py` shadow/apply-delete tooling over the shared meaningful-token overlap guard; destructive apply now prunes unsafe aliases for safe canonical rows and only deletes whole canonical rows when the canonical name itself is unsafe. Shadow audit against the configured DB returned 0 flagged canonical rows with `>=3` aliases. A one-time looser `--min-aliases 1` shadow audit found and then pruned two confirmed-bad aliases: `Marmar Group` under `The Panorama Group`, and `Realm Group LLC` under `The Remm Group`; follow-up `--min-aliases 1` and `--min-aliases 3` shadow audits both returned 0 flagged rows. Refactored Phase A architecture-firm exceptions from raw-value-only to scoped `(project_id, raw_value, expected_current_developer)` rows. Hardened developer registry persistence so generic-only canonical names are not inserted and unsafe aliases are ignored/dropped during exact matching, alias creation, and registry merges. |
| C.b | Source snippet renderers per source_type | `done` | 2026-04-26: Added backend `SnippetPayload` models and renderer registry under `src/tcg_pipeline/review/snippets.py`, with source-specific renderers for LADBS permit/inspection/CofO, CoStar, Pipedream, news articles, developer websites, researcher overrides, computed evidence, and generic fallbacks for future source types. Wired protected `GET /evidence/{id}/snippet?field=...` to load evidence rows and return snippets; missing auth returns `401` before DB access and missing evidence returns `404`. News article renderer reads stored highlights without re-extraction for Phase D. |
| C.c | Promote `researcher_override` to a table | `done` | 2026-04-26: Added `researcher_overrides` table migration and SQLAlchemy model with active `(project_id, field_name)` uniqueness, legacy JSONB backfill, authenticated read-only RLS, and no direct client write grants. Added shared override helpers that merge legacy JSONB with active table rows (table wins per field), warn on legacy-only divergence, preserve first-set audit metadata on reaffirm, and dual-write/prune the legacy JSONB during the transition so Phase B Overrides UI remains stable. Resolution now reads table-backed overrides first and clears superseded active rows; review workflow writes overrides through the shared helper; developer canonicalization respects active table overrides. Legacy JSONB column is intentionally retained until table-backed write/read paths are verified in a deployed environment. |
| C.d | Inline editing on Core fields (Evidence-derived) | `not_started` | Per `docs/specs/ui_requirements.md` §14. Edit writes to `researcher_overrides` via FastAPI. Resolution re-runs. ChangeLog entry. ⓘ visual cue indicates the field is evidence-derived. |
| C.e | Inline editing on Identity + Notes fields | `not_started` | Researcher-authored fields write directly to project row via FastAPI. Notes are append-only — each edit writes a new `project_notes` row. |
| C.f | Relationship picker UI | `not_started` | Phase siblings, master projects, related-by-address. Opens a project search modal; writes to `project_relationships`. |
| C.g | New project creation flow | `not_started` | `[+ New project]` in Pipeline. Required: canonical address, market, jurisdiction. Matcher runs on submit; shows possible duplicates. On create: opens Project Detail for the new project. |

#### C-late: Review Queue + contradiction detection

| Step | Task | Status | Notes |
|------|------|--------|-------|
| C.h | `ReviewItem` / `ReviewDecision` staged/committed state machine | `not_started` | Per `docs/specs/data_model_changes.md` §5 and `docs/specs/review_workflow.md` §4. Schema migration adds state columns. Refactor `review_workflow.py` into decision layer (staging) + commit layer (transactional apply). Persist actor user_id and/or full email on write-side audit rows; local-part display labels are not unique across domains. |
| C.i | Contradiction detection service | `not_started` | Per `docs/specs/review_workflow.md` §5 and `docs/specs/EVIDENCE_LAYER_DECISIONS.md` §22. Runs after evidence ingest and after resolve_project; generates `override_contradiction` review items with at-minimum MEDIUM priority. This is also the point where legacy `mode`/`baseline` resolver supersession stops being load-bearing and becomes audit-only. |
| C.j | Review Queue UI | `not_started` | Per `docs/specs/ui_requirements.md` §4. Grouped by project, chronological within project (optional group-by-source toggle). Priority-sorted. Staged state indicators. Keyboard-driven decisions (A/S/D/F). Bulk select + bulk action. Commit button with dynamic text. |
| C.k | Review Item detail view | `not_started` | Per `docs/specs/ui_requirements.md` §5. Split pane with current value / proposed value. Multi-conclusion sub-panes. Processed items section. Navigation with `[` / `]`. |
| C.l | Coverage — scrape kickoff + CoStar upload | `not_started` | Per `docs/specs/ui_requirements.md` §3.5 and `docs/specs/data_model_changes.md` §8. Scrape jobs queue (RQ on Render). CoStar upload as file picker. Add `python-multipart` when implementing FastAPI `File(...)` upload handling. Progress polling via `/scrape_jobs/{id}`. |
| C.m | Inclusion flags UI | `not_started` | Toggle `inclusion_in_analysis` and `inclusion_in_exhibit` per project with note field. Sticky. |
| C.n | ChangeLog view | `not_started` | Project Detail → Changes tab wiring, filter by actor / date / field. |
| C.o | Reviewed tab in Review Queue | `not_started` | Secondary tab showing previously committed decisions with filters for audit. |

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
| D.4 | Article → Evidence integration | `not_started` | Match extracted project references to existing projects (or flag as new candidates). Write evidence rows with `source_type=news_article`, `source_tier=2`. Resolution engine already handles Tier 2. **Blocked on C.a.1** — article NLP will introduce many new developer names, so registry pollution hardening must land first or the Phase A cleanup will silently reform. |
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
| E.3a | Developer registry + apply-script hardening | `superseded` | Moved forward to C.a.1 so registry pollution hardening lands before Phase D news ingestion and before the first Santa Monica Phase-A-style apply cycle. |
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
| H.6 | Seed SM with CoStar + run full cycle | `not_started` | **Blocked on C.a.1** — the Phase A apply script (`scripts/apply_phase_a_decisions.py`) currently hard-codes architecture-firm overrides by raw_value only. Before reusing it for SM, refactor to address-scoped or per-row overrides per C.a.1 item (2). |

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
`next`, `@supabase/supabase-js`, `tailwindcss`, `react-map-gl`, `maplibre-gl`

### Frontend Write-Path Backend (Phase C+)
`fastapi`, `uvicorn`, `python-jose` (or `pyjwt`) for Supabase JWT verification. Thin HTTP layer over `src/tcg_pipeline/db/review_workflow.py`; deploys as an additional Render service. Frontend reads remain direct via Supabase PostgREST with RLS; only writes route through this API. See Phase C.a and the 2026-04-23 Decision Log entry.

### News / Research Dependencies (Phase D)
`playwright` or `httpx` + `beautifulsoup4` (for BizJournals scraping), `anthropic` (Claude API for article extraction)

See ARCHITECTURE.md Section 10 for the full stack listing.

---

## 7. Key Reference Documents

| Document | Location | Purpose |
|----------|----------|---------|
| Architecture & Design Spec | `ARCHITECTURE.md` | Detailed data model, source inventory, field inventories, matching strategy, collection workflow. The original design document. |
| Evidence Layer Integration Guide | `docs/specs/EVIDENCE_LAYER_INTEGRATION_GUIDE.md` | Comprehensive guide to the evidence layer: schema, resolution rules, integration points. Written for Claude Code / Codex agents. |
| Evidence Layer Decisions | `docs/specs/EVIDENCE_LAYER_DECISIONS.md` | Implementation decisions answering developer questions about edge cases, thresholds, and design tradeoffs. Includes review-protected override semantics (§22), superseded conditional override semantics (§21), STATUS_CHANGE rejection (§21a), observation ordering (§21b), and delivery-date override provenance (§21c). This is a living document — check for recent additions. |
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
| 2026-04-23 | Ignore polluted generic developer registry names and do not recreate them automatically | Production had a `Category` registry row with unrelated aliases. Matching and persistence now block ignored generic names, and the polluted row was deleted from prod so future sweeps do not recreate it. |
| 2026-04-23 | Fuzzy-review developer canonicalization stays shadow-only even under `canonicalize-developers --apply` | 75-89 similarity matches remain ambiguous. Auto-merging registry rows or rewriting project developer values at that threshold can make bad matches sticky, so apply mode now skips those writes until reviewed. |
| 2026-04-23 | `estimated_calc` may fill blank `date_delivery` values for Proposed / Pending / Approved projects | Phase A validation spot-checked 10 representative null-to-estimate rows and accepted the full 218-row bucket. The value remains explicitly tagged by `delivery_year_provenance = estimated_calc` so downstream consumers can filter or down-weight it. |
| 2026-04-23 | Low-tier `total_units` overwrites are allowed when absolute delta is `<= 5`; larger deltas require researcher review | Phase A review showed small deltas behave like measurement noise while larger deltas often reflect phase splits or project-identity ambiguity. The Phase A apply profile therefore accepted 7 small deltas and wrote conditional overrides for 20 larger deltas to preserve current values until newer evidence arrives. |
| 2026-04-23 | Recent article evidence can outrank CoStar for `date_delivery` when the article is within the last 6 months | Forward-looking requirement for Phase D. Articles often capture operator-stated timeline updates that lag in CoStar. This is a field-specific source-priority override for delivery dates only, not a general tier change. |
| 2026-04-23 | `canonicalize-developers --apply` must not rewrite a project developer field that is currently protected by a researcher override | Phase A apply surfaced one override-clobber case (`Lake on Wilshire`). Future sweeps now preserve project developer values whenever `researcher_override.developer` is present; canonical registry maintenance can still proceed independently. |
| 2026-04-23 | Frontend write path will be a Python FastAPI service over `review_workflow.py`, not Supabase Edge Functions or PL/pgSQL | `review_workflow.py` is ~500 lines of orchestration (accept/reject/defer, orphan evidence linking, cross-project conflict detection, override writing, resolution re-run). Porting to TypeScript duplicates logic across two stacks with guaranteed drift; PL/pgSQL makes testing and control flow painful. FastAPI reuses existing Python, deploys on the Render stack already in use, and keeps the same code callable from CLI/cron. Reads continue to go direct through Supabase PostgREST with RLS; only writes route through the API. RLS policies must land with B.1 — mutable tables locked to service role so the API boundary is not bypassable. Affects Phase C.a and the B.1/B.2 scaffolding choices. |
| 2026-04-23 | Review-protected override semantics supersede sticky/until_newer_evidence | Researcher inputs never silently rot (sticky problem) and never silently yield (until_newer_evidence problem). Every override holds its value until explicitly reviewed; new contradicting evidence generates a review item at minimum MEDIUM priority. See `docs/specs/EVIDENCE_LAYER_DECISIONS.md` §22. |
| 2026-04-23 | Review Queue uses batch-commit model, not immediate-apply | Researchers stage decisions as they work; commit applies all staged decisions atomically. Staged state persists across sessions per-user. Decisions can be revised before commit. Commit allows partial — undecided rows stay in queue. Rationale: matches researcher workflow (scan through changes, decide, act in batch). Avoids half-applied updates if a session is interrupted. |
| 2026-04-23 | Deferred review items stay at the bottom of the same queue, never auto-expire | "Queue cleared" requires deferred items also decided. Deferred count surfaced in Coverage so backlog is visible, not hidden. A separate Deferred tab would risk becoming a forgotten graveyard. Choice is explicit trade-off: less disciplined researchers will have non-empty queues; more disciplined researchers will clear them. |
| 2026-04-23 | Optimistic last-click-wins concurrency; no row-level locking | At 1-3 users, row-level locking is ceremony without payoff. When User A stages a decision, User B sees the row removed from their queue next refresh. If B tries to stage concurrently, B's request returns 409 with a "just decided by A" banner. Revisit if team grows. |
| 2026-04-23 | Coverage is the name of the jurisdictions view | Analyst-friendly term: "I cover LA, Phoenix, Seattle." Beats "Jurisdictions" (literal), "Scope" (technical), "Territories" (sales-y). |
| 2026-04-23 | Badge palette: eight source categories with color + text | Blue = live user input (current user and other TCG researchers, distinguished by initials). Teal = Pipedream (legacy spreadsheet, distinct from live input). Green = Gov. Amber = News. Purple = CoStar (per TCG internal convention). Slate = Web (developer websites). Gray = Computed. Small, subtle, paired with shape/icon for accessibility. See `docs/specs/ui_requirements.md` §9. |
| 2026-04-23 | Keyboard shortcut layout uses asdf for decisions | Homerow-left, one-handed, adjacent keys. Keycaps on buttons show the shortcut — labels don't have to match initials. Accept new = a, Keep old = s, Defer = d, Custom = f. Navigation: jk, hl. Bulk: shift-a/s/d. Goto: g+letter. |
| 2026-04-23 | Pipedream replacement is the Phase C target, not Phase B | Phase B ships read-only for expectation-setting and data validation. Phase C-early (inline editing, new project creation) is the point at which researchers stop using Pipedream. Structuring Phase C into early (edit) and late (review queue) ordering lets researchers use the tool in a useful way earlier. |
| 2026-04-23 | Web scraping is strictly developer_website for now | The Web badge category maps to `developer_website` source_type only. No general-web scraping planned for MVP. Category is reserved for future expansion. |
| 2026-04-24 | Take a DB snapshot before Phase B schema migrations | Markets, jurisdictions, and source-run backfills touch core project scoping and freshness metadata. A Supabase point-in-time backup or `pg_dump` immediately before B.0b/B.0c gives a cheap recovery path if backfill logic is wrong. |
| 2026-04-24 | B.1 must ship one real authenticated read path | Scaffolding alone can hide Supabase client, RLS, session, and Server Component integration bugs until B.2. `/coverage` must load real jurisdiction rows through Supabase PostgREST with RLS before the next UI surface builds on it. |
| 2026-04-24 | Source-populated direct fields are read-only for MVP | Fields populated directly by CoStar/Pipedream/collectors but not currently owned by the resolution engine (`rent_or_sale`, physical attributes, unit mix, owner, zoning, etc.) should not be editable until the backend either promotes them to evidence-derived fields or teaches ingesters to respect overrides. Avoids researcher edits being overwritten by the next source refresh. |
| 2026-04-24 | Vercel previews use production Supabase during read-only Phase B | Previews stay behind the same `ALLOWED_EMAILS` allowlist and read from the same Supabase project while no writes exist. This keeps review simple and avoids premature staging infrastructure. Before Phase C writes, revisit and likely split previews to staging Supabase/FastAPI. See `docs/ops/frontend_deployment.md`. |
| 2026-04-24 | Coverage may read `review_items` but cannot write them in Phase B | B.2 needs pending/deferred queue counts per jurisdiction. Authenticated users get SELECT on `review_items` under RLS; no INSERT/UPDATE/DELETE grants are added. Review decisions and queue mutation still wait for the Phase C FastAPI write path. |
| 2026-04-25 | Use MapLibre and configurable raster tiles for the Pipeline map | Mapbox GL JS v3 introduces token/licensing/telemetry concerns for this tokenless internal map, so Pipeline now uses `react-map-gl/maplibre` + `maplibre-gl`. Tile URL and attribution are environment-configurable. The default remains OpenStreetMap only as a short-term internal fallback; choose a paid or self-hosted provider (MapTiler, Stadia, Protomaps, etc.) before routine/heavier usage. |
| 2026-04-25 | Use Postgres read views for UI evidence summaries | Phase B reads should avoid shipping whole append-only tables to Next.js just to compute UI summaries. Pipeline now reads `project_latest_evidence` (`DISTINCT ON project_id`) through PostgREST/RLS. B.4 should add a richer per-field provenance read model from `resolution_log` rather than extending client-side reductions. |
| 2026-04-25 | Project Detail Snapshot uses `resolution_log` as the field provenance read model | B.4 source badges and hover provenance should reflect the field-level resolution trail, not just the newest evidence row for the project. Added `project_field_resolution` (`DISTINCT ON project_id, field`) for latest rule, confidence, and evidence IDs per field. B.5 can still fetch full project evidence timelines separately. |
| 2026-04-25 | Snapshot field badges must be honest about provenance gaps | Do not infer field provenance by matching canonical UI names against source-native `extracted_fields` keys. If `resolution_log` does not link evidence IDs for a displayed field, show it as unlinked/system-sourced rather than biasing toward whichever evidence happens to use canonical names. A source-key alias mapper or expanded resolver logging can be added later. |
| 2026-04-25 | Evidence tab reads full project evidence rows directly | The Evidence tab is an audit timeline, not field provenance. It can fetch all evidence rows for the selected project, merge source URLs from `project_source_records`, and show source-native extracted fields/raw JSON honestly. Field-level provenance remains owned by `resolution_log` and Snapshot badges. |
| 2026-04-25 | Evidence field filters use resolver-linked evidence IDs | Snapshot-to-Evidence drill-down must not match canonical UI field names against source-native `extracted_fields` keys. A field filter like `field=total_units` now shows rows whose evidence IDs are linked by `project_field_resolution` for that canonical field. Raw source keys remain visible only as source detail. |
| 2026-04-26 | B.6 tabs are read-only projections over current storage | Resolution uses latest `resolution_log` rows; Changes uses `change_log`; Overrides uses legacy `projects.researcher_override` JSONB. Because the current backend does not store alternatives considered or superseded override history, Phase B surfaces those gaps instead of inventing data. |
| 2026-04-26 | Resolution tab shows resolver output, not only discrepancies | Current `resolution_log` rows are latest resolver outputs for tracked fields, not a discrepancy-only table. B.6 groups changed rows first, collapses unchanged tracked rows, and only exposes Evidence drill-through when linked evidence rows exist. The Changes tab also includes `status_history` so Phase B shows lifecycle activity before Phase C review commits start writing `change_log`. |
| 2026-04-26 | Dashboard tiles stay read-only in Phase B | B.7 provides situational awareness from existing read models and tables, without creating new review items or write-side jobs. Stalled Candidates is a heuristic count for Approved/U/C projects with real evidence dates older than 12 months, not the Phase E auto-stall workflow. Contradictions stays in a Phase C inactive state until contradiction-type review items exist. Needs Attention links to Coverage because `/review` remains a placeholder until the real Review Queue ships. |
| 2026-04-26 | Developer registry hardening moved from Phase E to Phase C | E.3a is now C.a.1 and should run after the write API scaffold/staging gate work, before source snippet/rendering work if possible. The work is self-contained, has no frontend dependency, and unblocks D.4 article evidence writes by preventing news NLP from recreating polluted developer registry rows. Run the long-tail registry audit in shadow mode against prod first, matching the Phase A apply pattern. |
| 2026-04-26 | Researcher overrides table migration keeps legacy JSONB during transition | C.c promotes overrides into `researcher_overrides` for per-field history and future contradiction FKs, but `projects.researcher_override` remains synchronized until deployed table-backed write/read paths are verified. The resolver merges legacy JSONB and active table rows, with table rows winning per field and a warning when legacy-only keys remain. This keeps Phase B read-only Overrides UI stable while Phase C write paths move to the new table. |
| 2026-04-26 | Legacy override mode remains load-bearing until contradiction review lands | §22 review-protected semantics are the target, but the current resolver still honors `mode = until_newer_evidence` and `baseline` to supersede older overrides. C.c preserves those fields in the table for compatibility. C.i must replace silent supersession with `override_contradiction` review items before `mode` becomes audit-only. |
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

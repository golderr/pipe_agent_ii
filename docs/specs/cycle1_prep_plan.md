# Pre-AGENT.reset Cycle 1 Implementation Plan

> **Living plan.** This is the operational checklist for executing the six pre-cycle-1 Review Queue UX items scoped on 2026-05-13. Update it as work lands — check off sub-tasks, record open questions resolved, and capture lessons learned. The ROADMAP rows say *what* and *why*; this document says *how* and *in what order*.
>
> **Last updated:** 2026-05-13 (Item 6 scope corrected to existing `projects.stories`)
> **Maintained by:** Nate Goldstein + Claude Code

---

## 1. Overview

Six pre-cycle-1 items must land before `AGENT.reset` cycle 1 begins:

| # | Item | Roadmap row | Status | Commit |
|---|---|---|---|---|
| 1 | Resolver-level suppression of benign LADBS follow-on permits | `UX.regression-suppression` | ✅ Shipped 2026-05-13 | `e4c37c4` |
| 2 | Card source-detail rendering for permit + CoStar regression cards | `UX.card-source-detail` | ✅ Shipped 2026-05-13 | `93e8648` |
| 3 | 3-field Current/Evidence/Result model + Confirm/Defer/Detail + auto-advance | `UX.3-field-review` | 🔜 Phase 3 (~2-3 days) | — |
| 4 | Narrative descriptiveness (name permit types + news sources) | `UX.narrative-detail` | ✅ Shipped 2026-05-13 | `c111433` |
| 5 | Duplicate-prevention table for new candidates + possible matches | `UX.dedup-table` | 🔜 Phase 4 (~1.5-2 weeks) | — |
| 6 | Building height / stories resolver support (canonical field `stories`) | `UX.building-height` | 🔜 Phase 2 next (~0.5-1 day) | — |

**Total:** ~2.5-3 weeks at single-threaded execution; ~2 weeks if independent backends + frontends can overlap.

Three items remain between cycle 1 and cycle 2 (`UX.hover-snippet`, `UX.permit-prompt`, `UX.synthetic-hygiene`) and one before Phase H (`UX.market-filter`); those are out of scope for this plan.

---

## 2. Recommended sequencing

```
Phase 1 — Quick wins (days 1-2)
  Item 1 (UX.regression-suppression)   — backend resolver + tests
  Item 4 (UX.narrative-detail)         — narrative templates + prompts
  Item 2 (UX.card-source-detail)       — backend snippet renderers + frontend display

Phase 2 — Stories resolver foundation (day 3)
  Item 6 (UX.building-height)          — news reference migration, resolver, project-detail UI

Phase 3 — 3-field model (days 6-9)
  Item 3 (UX.3-field-review)           — backend payload contract + frontend ThreeFieldEditor +
                                         simplified actions + auto-advance

Phase 4 — Dedup table (days 10-19)
  Item 5 (UX.dedup-table)              — pg_trgm indexes, retrieval module, Discovery tab,
                                         subject row + candidate table, three-layer retrieval,
                                         overlap highlighting, click-to-view map, live updates,
                                         match-with-deltas, three-way per-row actions
```

### Why this order
- Items 1, 4, 2 are surgical changes; they validate that the working environment + test loop is healthy before bigger surgery starts.
- Item 6 must land before Item 5 wires the dedup table's `building_height_stories` column to real data.
- Item 3 ships before Item 5 because the dedup table's "Match-to-this with field-deltas" UX builds on the 3-field model components.
- Item 5 is the longest single item; sequencing it last lets prior items absorb the early-cycle learning curve.

### Parallel opportunity
- Item 6's news-extraction-schema change and Item 3's backend payload contract are both small additive backend changes that can be done in parallel by separate threads if available.
- Item 5 splits cleanly into backend (retrieval + matching helpers + indexes) and frontend (Discovery tab + table component + map popup). These can run in parallel once Item 6 has shipped.

---

## 3. Item 1 — UX.regression-suppression

**Goal:** stop the resolver from emitting `status_regression_candidate` for LADBS permit issuances that are benign follow-on paperwork on already-UC projects.

**Files to touch:**
- `src/tcg_pipeline/resolution/fields/status.py` — candidate emission logic
- New: `src/tcg_pipeline/resolution/regression_filters.py` — small module containing the additive-paperwork allowlist + the suppression predicate
- `tests/test_resolution_fields.py` — new test cases

**Sub-tasks:**

- [ ] **Define the additive-paperwork allowlist.** LADBS permit `status_desc` values that indicate a permit is in force (not cancelled/voided/expired) are *additive paperwork* when they land on an already-UC project. Initial allowlist:
  - `Issued`, `Permit Finaled`, `Ready to Issue`, `Plan Check Submitted`, `Pending Inspection`
  - Empty string / `None`
- [ ] **Define the regression-signal list.** Values that DO indicate genuine regression:
  - `Cancelled`, `Void`, `Expired`, `Revoked`, `Withdrawn`, `Plan Check Cancelled`, `Permit Cancelled`
- [ ] **Document the source.** Production currently has limited LADBS evidence (only 36 rows, mostly synthetic). Initial allowlist is from Socrata docs and common LADBS terminology. Treat as v1 and revisit after first cycle's organic LADBS data lands. Sentinel behavior for unknown `status_desc`: assume additive (do NOT emit candidate) and log a `system_alert` once per session with the unknown value so we can extend the list.
- [ ] **Implement the suppression predicate.** Final rule is intentionally simpler than the initial same-source-family sketch. Function signature: `is_benign_ladbs_additive_paperwork(evidence) -> tuple[bool, dict | None]`. Returns True iff:
  1. `evidence.source_type` is in `{'ladbs_permit', 'ladbs_permit_activity'}`
  2. `evidence.raw_data['status_desc']` is in the additive allowlist (or unknown - fail-additive)
  3. No same-source-family condition is required. Rationale: LADBS permit issuance is never a regression signal regardless of how the current status was established. A project that's UC because of news evidence and then has a LADBS permit issued is showing forward progress (first government corroboration), not regression. Adding the same-source-family condition would generate false-positive regression cards for genuine forward progress.
- [ ] **Wire the predicate into `resolve_status`.** After enumerating regression candidates, filter them via the predicate. Log to `resolution_log.metadata.suppressed_regression_candidates` so the audit trail shows what was filtered + why.
- [ ] **Tests:**
  - [ ] `test_follow_on_permit_on_uc_project_does_not_emit_regression_candidate` (Issued ladbs_permit on UC project — no candidate)
  - [ ] `test_cancellation_on_uc_project_emits_regression_candidate` (Cancelled ladbs_permit on UC project — candidate emitted)
  - [ ] `test_unknown_status_desc_logs_alert_and_treats_as_additive` (logs system_alert, no candidate)
  - [ ] `test_news_regression_candidate_unaffected` (news status regression still emits)
  - [ ] `test_stalled_inactive_carve_out_unchanged` (Stalled/Inactive still take the manual-review path)
  - [ ] `test_cross_source_regression_emits_candidate` (Pipedream/news regression on UC project IS emitted - predicate only suppresses LADBS additive paperwork)
- [ ] **Local validation** against the staged regression card `5eeb3658-8326-4cbd-889b-cc902f55a611`: re-run the resolver and confirm no regression candidate is now emitted for that fixture.
- [ ] **Commit + push.** Single focused commit. CI confirms test suite green.

**Acceptance:** Tests pass, the known-benign synthetic regression card stops being emitted, an unknown `status_desc` produces a debug alert without blocking the resolve pass.

**Phase 1 hardening sub-tasks:**

- [x] **Item alpha: persist suppressed-only regression audit trail.** When LADBS additive-paperwork suppression is the only status-regression outcome, `resolve_project` now writes a `resolution_log` row with `rule_applied="regression_candidate_suppressed"` and the suppressed candidate metadata.
- [x] **Item beta: live alerting for unknown LADBS status_desc.** The LADBS suppression predicate now returns a pending alert payload for unknown values, and `resolve_project` drains that metadata channel into `system_alerts` when resolution writes are enabled.
- [x] **Item delta: document dropped same-source-family condition.** The predicate documentation now records that LADBS additive paperwork is suppressed regardless of the source family that established the current higher-rank status.
- [x] **Item epsilon: normalize LADBS status_desc matching.** Public additive and regression status allowlists keep source-native display strings, while private membership-key sets are derived with `.strip().casefold()` at module load. Inbound `status_desc` values use the same key before membership checks.

**Lessons learned:**

- Suppressed candidates are still audit decisions. If no active regression candidate exists, the engine needs a dedicated suppressed-only log path so queue suppression does not erase traceability.
- Resolver predicates should stay session-free. Engine-owned metadata channels are the safer place to coordinate database side effects like operator alerts.
- A LADBS permit issuance is forward progress even when the current UC status came from news or another non-LADBS source. Requiring same-source-family would create false-positive cards for first government corroboration.
- Keep source-native display sets as the public constants, and derive normalized private keys for comparisons. That keeps reader-facing values, alerts, and evidence descriptors stable while making membership checks case-insensitive.

---

## 4. Item 4 — UX.narrative-detail

**Goal:** make narratives name specific permit types and specific news sources instead of generic "LADBS signal" / "news evidence".

**Files to touch:**
- `src/tcg_pipeline/review/human_summary.py` — narrative templates
- `src/tcg_pipeline/agents/prompts/news_v1/system.md` — agent prompt
- `src/tcg_pipeline/agents/prompts/permit_v1/system.md` — agent prompt
- Tests in `tests/test_review_human_summary.py`

**Sub-tasks:**

- [ ] **Audit current narratives** in `human_summary.py`. Identify every template that emits generic phrases:
  - "LADBS signal" → must include permit type + permit number when available
  - "news evidence" → must include source slug (e.g., "Urbanize LA")
  - "CoStar evidence" → must include upload date or property ID
  - "Pipedream evidence" → must include workbook date
- [ ] **Add a helper** `format_source_descriptor(evidence_row) -> str` that returns a specific phrase per source type:
  - `ladbs_permit` → `"LADBS {permit_type_normalized} #{permit_number}"` (e.g., "LADBS Bldg-New permit #19010-10000-00001")
  - `news_article` → `"{source_display_name} article"` (e.g., "Urbanize LA article") using `news_sources.display_name` (add a `display_name` column if not present)
  - `costar` → `"CoStar upload from {upload_date}"`
  - `pipedream` → `"Pipedream workbook from {workbook_date}"`
  - Fallback for unknown source: `"{source_type} evidence"` with a debug log
- [ ] **Update each narrative template** in `human_summary.py` to use the new helper. Preserve existing structure (don't restructure narratives — just enrich descriptors).
- [ ] **Update agent prompts.** In both `news_v1/system.md` and `permit_v1/system.md`, the human_summary instruction block should say: "When writing `human_summary`, always name the specific source (e.g., 'Urbanize LA article from April 30, 2026', not 'news evidence') and the specific permit type + number when applicable (e.g., 'LADBS Bldg-New permit #X', not 'LADBS signal')."
- [ ] **Tests:**
  - [ ] `test_human_summary_names_permit_type` — fixture with ladbs_permit evidence → narrative contains the permit_type
  - [ ] `test_human_summary_names_news_source_display_name` — fixture with urbanize_la article → narrative contains "Urbanize LA"
  - [ ] `test_human_summary_falls_back_for_unknown_source` — sentinel behavior verified
- [ ] **Smoke validation.** Send one curated paste-link smoke article through and inspect the resulting `human_summary` for the new specificity. Same for one synthetic LADBS regression smoke (via slice 6 calibration tooling if convenient).
- [ ] **Backfill?** No — existing narratives stay as-is; `AGENT.reset` wipes them.
- [ ] **Commit + push.**

**Acceptance:** Tests pass; a fresh review item generated under the new code shows a specific source descriptor in its `human_summary`; agent prompts deployed; smoke confirms agent outputs specific descriptors.

**Phase 1 hardening sub-tasks:**

- [x] **Item gamma: LADBS permit-number lookup fallback.** Regression-candidate descriptors and review-card LADBS snippets now prefer Socrata's `pcis_permit`, then existing permit-number aliases, then `evidence.source_record_id` so both Item 4 narratives and Item 2 source-field cards have a permit number whenever the evidence row carries one.

**Lessons learned:**

- Source descriptor and snippet helpers need to follow source-native raw column names first. Canonical aliases are useful fallbacks, but production LADBS fixtures use `pcis_permit`.

---

## 5. Item 2 — UX.card-source-detail

**Goal:** when a regression (or any other) review card shows permit or CoStar evidence, the reviewer can see source-specific structured fields directly on the card without drilling in.

**Files to touch:**
- `src/tcg_pipeline/review/snippets.py` — extend SnippetPayload models + renderers
- Frontend: `components/review/` regression-card-related files (locate the exact file via grep)
- Backend tests + frontend snapshot tests

**Sub-tasks:**

- [ ] **Audit current `SnippetPayload` returned by `snippets.py`** for `ladbs_permit` and `costar` source types. Identify which fields are present today vs missing.
- [ ] **Extend `LadbsPermitSnippet`** (or whatever the class is called) to include:
  - `permit_number`, `permit_type` (e.g., 'Bldg-New'), `action_code` if present, `status_desc` (the in-force/cancelled status)
- [ ] **Extend `CostarSnippet`** to include:
  - `costar_property_id`, `upload_date`
  - Defer `source_field_for_proposed_value` until Item 5 defines raw-column resolution for `source_fields`; do not emit the canonical resolver field name as if it were the raw CoStar column.
- [ ] **Update the renderer functions** to populate the new fields from `evidence.raw_data`.
- [ ] **Update frontend review-card component(s)** to render the new fields on permit + CoStar cards. Visual placement: a small subheader row beneath the existing supporting-evidence area, formatted as a labeled inline list (e.g., `Permit: Bldg-New #19010-10000-00001 · Status: Issued`).
- [ ] **Tests:**
  - [ ] Backend: `test_ladbs_permit_snippet_includes_permit_number_type_action_status`
  - [ ] Backend: `test_costar_snippet_surfaces_property_id_aliases`
  - [ ] Frontend: snapshot test for the regression card with a permit-source fixture
- [ ] **Commit + push.**

**Acceptance:** Re-viewing the LADBS regression card `5eeb3658` (still in the queue as staged-deferred until `AGENT.reset` wipes it) now shows permit type + number + status_desc on the card surface.

**Phase 1 hardening sub-tasks:**

- [x] **Item zeta: CoStar `PropertyID` lookup + misleading `source_field` cleanup.** CoStar source fields now accept the source-native `PropertyID` key in addition to existing aliases. The snippet payload omits `source_field` until Item 5 can decide whether to reintroduce it with true raw-column resolution.

**Deferred follow-ons:**

- [ ] **Per-source allowed-key schema for `source_fields`.** Wait until Item 5 (`UX.dedup-table`) defines its source-fields use cases, then add a per-source API serialization schema.
- [ ] **Extract `SourceFieldsInline` to a shared component.** Wait until Item 3 (`UX.3-field-review`) reshapes review-card components; extract during that refactor.
- [ ] **Investigate LADBS `action_code`.** Track as a separate source-data investigation, not Phase 1 cleanup.
- [ ] **Reintroduce CoStar raw source field only with raw-column resolution.** Item 5 should decide whether `source_field_for_proposed_value` is useful once the system can map canonical fields back to the actual CoStar column name.

**Lessons learned:**

- Do not surface canonical resolver field names as source-native metadata. If a review card labels a field as source detail, it needs to reflect the raw source record or stay omitted.

---

## 6. Item 6 - UX.building-height (canonical field name: `stories`)

**Goal:** add resolver support for the existing `projects.stories` field so the dedup table's building-height column has a single canonical value per project. CoStar and Pipedream ingesters already populate `stories`; this item adds news-extraction support and a resolver.

**Files to touch:**
- New Alembic migration: add nullable integer `candidate_stories` to `news_project_references`
- `src/tcg_pipeline/db/models.py` - NewsProjectReference (add `candidate_stories`)
- News extraction prompt schema: `src/tcg_pipeline/news/prompts/extract_v2/schema.json`
- News extraction prompt text: `src/tcg_pipeline/news/prompts/extract_v2/system.md`
- News extraction / integration: thread `candidate_stories` into reference rows and evidence `extracted_fields.stories`
- New: `src/tcg_pipeline/resolution/fields/stories.py` - resolver
- `src/tcg_pipeline/resolution/engine.py` - wire the resolver in
- Frontend project detail Snapshot: verify/show the field labelled `Stories`
- Migration verification + tests

**Sub-tasks:**

- [x] **Canonical field confirmed.** `projects.stories` already exists as a nullable integer Project field. CoStar and Pipedream already populate it from source-native story-count columns.
- [x] **Source field names confirmed.** CoStar export: `Number Of Stories` (both MF and Commercial workbooks). Pipedream DataStorage: `Elevation` at col 48 (header in row 3). LADBS `pi9x-tg5x` active feed exposes `height` as text / decimal feet, not stories, so LADBS is dropped from the Item 6 source list. See deferred follow-on below.
- [ ] **Write the Alembic migration.** Adds one nullable integer column `candidate_stories` to `news_project_references`. No Project migration needed.
- [ ] **Update db/models.py** for `NewsProjectReference.candidate_stories` only.
- [ ] **Run migration locally + verify production/staging target only after backup.** Per `docs/ops/migration_runbook.md` discipline: `pg_dump` backup first. The remaining migration is nullable/additive and should land before `AGENT.reset`.
- [ ] **Update extract_v2 schema + prompt** to capture `candidate_stories` from articles. Prompt language should say `building height in stories` so the extraction instruction is unambiguous; store the DB/model field as `candidate_stories`.
- [ ] **Update news integrator** to write `candidate_stories` on reference rows and thread it into evidence as `stories`.
- [ ] **Write the resolver.** New file `resolution/fields/stories.py`. Rule: most recent explicit value wins, with source-priority tiebreak for same-date matches (Pipedream > CoStar > news_article). Treat null as no evidence - null Project value remains null if no evidence has the field.
- [ ] **Wire into resolution_engine.** Register the resolver alongside existing field resolvers.
- [ ] **Update project detail Snapshot UI.** Verify `stories` is already displayed; adjust label if needed.
- [ ] **Tests:**
  - [ ] Migration: forward + downgrade work cleanly
  - [ ] News extraction: prompt/schema accepts `candidate_stories` from a fixture
  - [ ] News integrator: reference row and evidence expose `stories`
  - [ ] Resolver: most-recent-wins, source-priority tiebreak, null handling
- [ ] **Apply migration in production.** Backup + apply per migration runbook. Record in Decision Log.
- [ ] **Commit + push.**

**Acceptance:** Migration applied to production, CoStar / Pipedream / news can populate `stories`, resolver writes the correct value, and project-detail Snapshot shows the field.

**Deferred follow-ons:**

- [ ] **Investigate LADBS height-to-stories semantics.** `pi9x-tg5x` exposes `height` as decimal feet; deprecated `cpkv-aajs` / `hbkd-qubn` expose `of_stories`. A live-feed mapping requires per-product-type feet-per-story rules and an evidence-quality decision. Park as a separate source-data investigation, not Phase 2 scope.

**Lessons learned:**

- Verify the actual source schema against a live API call before adding it to a multi-source resolver. Documentation hedges like `TBD, verify at first map` should be treated as work-not-yet-done, not as a placeholder to code against.
- Verify the canonical schema before naming a new field. Both the prior `elevation` framing and the LADBS `stories_proposed` assumption were spec-side hallucinations that would have been caught by a quick model-file grep. Pre-flight verification belongs at planning time, not just implementation time.

---

## 7. Item 3 — UX.3-field-review

**Goal:** replace the current 2-value (Current vs Proposed) display with a 3-field Current/Evidence/Result model across all value-change review item types. Simplify action buttons to Confirm/Defer/Detail. Auto-advance to next item after action.

**Files to touch:**
- Backend: `src/tcg_pipeline/api/routers/review.py` (or wherever the queue + item endpoints live) — extend response shape
- Frontend: `app/(app)/review/` page + `components/review/` cards + actions
- Tests on both sides

**Sub-tasks:**

- [ ] **Design the unified payload contract.** Every value-change review item (`status_change`, `status_regression_review`, `material_contradiction`, `unit_split_mismatch`, `override_contradiction`) returns:
  ```
  {
    field_name: "pipeline_status",
    field_type: "status_enum" | "integer" | "decimal" | "date" | "developer" | "product_type" | ...,
    current_value: <typed>,
    evidence_value: <typed>,
    agent_recommended_value: <typed>,  // defaults to evidence_value when no agent recommendation; null if neither
    constraints: {
      // optional, per field type
      enum_values: [...],          // for enums
      min: ..., max: ...,           // for numbers
      // etc
    },
    supporting_evidence_ids: [...],  // already exists today
    dissenting_evidence_ids: [...],  // already exists today
    human_summary: "...",            // already exists today
  }
  ```
- [ ] **Backend changes:**
  - [ ] Extend `GET /review/queue/{item_id}` to return the unified shape for value-change items. Map each existing item type's payload to the new shape.
  - [ ] Map `agent_recommended_value` from `payload.agent_revised_verdict` / `payload.agent_recommendation` for items that have an agent. For items without an agent (direct review cards from slice 5), `agent_recommended_value` = `current_value`.
  - [ ] Tests for each item type's serialization.
- [ ] **Frontend changes:**
  - [ ] Build a generic `<ThreeFieldEditor>` component that takes `current`, `evidence`, `defaultResult`, `fieldType`, `constraints` and renders three cells:
    - Current (read-only)
    - Evidence (read-only)
    - Result (editable per field type — dropdown / number / date / autocomplete)
  - [ ] Refactor the review-item detail page (`app/(app)/review/[itemId]/`) to use `<ThreeFieldEditor>` for value-change items.
  - [ ] Simplify action buttons: Confirm (commits the Result value), Defer (no decision, marks deferred), Detail (drill-in for full context).
  - [ ] Implement auto-advance: after Confirm/Defer, fetch the next item in the active queue and load it. Use the existing queue navigation helpers from C.k.1 ([ ] keys).
  - [ ] Keep the existing decision-card layout for discovery items (`new_candidate`, `possible_match`) — those go to the new Discovery tab built in Item 5.
- [ ] **Server actions:**
  - [ ] Confirm action accepts the Result value and writes a `review_decisions` row + applies the override / change_log entry through the existing review-workflow helpers.
  - [ ] Defer action just marks the item deferred without applying.
- [ ] **Tests:**
  - [ ] Backend: serialization test per value-change item type
  - [ ] Frontend: component tests for ThreeFieldEditor with each field type
  - [ ] E2E: confirm-with-modified-result writes the modified value, not the agent recommendation
- [ ] **Local validation** on prod data: walk a few existing value-change items and confirm the new UI renders correctly.
- [ ] **Commit + push.**

**Acceptance:** All five value-change item types render with the 3-field model; Confirm applies the Result value (whether equal to current, evidence, or user-edited); auto-advance moves to the next item; existing decision-card layout still works for discovery items.

---

## 8. Item 5 — UX.dedup-table

**Goal:** the duplicate-prevention safety net for `new_candidate` + `possible_match` review items. New "Discovery" tab on `/review`. Source-grouped cards with subject row + candidate table. Three-layer retrieval. Cell-level overlap highlighting. Match-with-deltas. Click-to-view map popup. Live updates after match/create.

**Files to touch (extensive):**
- New: `alembic/versions/2026_05_14_xxxx_add_pg_trgm_indexes.py` — pg_trgm GIN indexes
- New: `src/tcg_pipeline/matching/similarity.py` — shared similarity helpers (extracted so the matcher can also use them)
- New: `src/tcg_pipeline/matching/candidates.py` — three-layer retrieval module
- Backend: `src/tcg_pipeline/api/routers/review.py` — new `GET /review/queue/{item_id}/candidates` endpoint
- Backend: action endpoints for Match-to-this, Match-with-deltas, Create-new-and-link, Create-new-unlinked
- Frontend: new Discovery tab on `/review` page
- Frontend: new components — DedupCard, SubjectRow, CandidateTable, MapPopup, RelationshipPickerInline
- Tests

**Sub-tasks:**

### 8.1 Schema + indexes
- [ ] **Migration** adds two GIN indexes:
  ```sql
  CREATE INDEX ix_projects_canonical_address_trgm ON projects USING GIN (canonical_address gin_trgm_ops);
  CREATE INDEX ix_projects_project_name_trgm ON projects USING GIN (project_name gin_trgm_ops);
  ```
- [ ] Verify PostGIS GIST index on `projects.location` exists (from B.0a). Add if missing.

### 8.2 Backend retrieval (`matching/candidates.py`)
- [ ] **Layer 1 — hard signals.** Query returns all rows matching any of:
  - Geographic: `ST_DWithin(location, subject_point, 250)` (~250m)
  - APN: matching `project_identifiers.value` where `kind='apn'`
  - CoStar Property ID: matching `project_identifiers.value` where `kind='costar_property_id'`
  - Canonical address: exact match on `canonical_address` (normalized)
  - Developer + any other secondary signal: canonical_developer match AND (within 1km OR product_type match OR partial address)
- [ ] **Layer 2 — soft signals.** Top 20 by weighted likelihood score (see formula in §8.3). Query uses pg_trgm `similarity()` function for name and address.
- [ ] **Layer 3 — broader sweep.** Behind explicit "show more" parameter. Returns all projects within 1km OR any matching trigram token.
- [ ] **Performance constraint:** Layer 1 + Layer 2 capped to 25 total rows per response. Layer 3 capped to 100.

### 8.3 Match-likelihood formula
- [ ] Implement in `matching/similarity.py`:
  ```
  likelihood = 0.30 * geographic_proximity_score        # exponential falloff from 0 at 1km to 1.0 at 0m
             + 0.25 * address_similarity                 # pg_trgm similarity(canonical_address, subject_address)
             + 0.20 * name_similarity                    # pg_trgm similarity(project_name, subject_name) — null subject_name → 0
             + 0.10 * developer_match                    # 1.0 exact-canonical, 0.7 fuzzy ≥0.85 ratio, else 0
             + 0.10 * unit_count_proximity               # 1.0 within ±5%, exponential falloff
             + 0.05 * product_type_match                 # 1.0 exact, 0 else
  ```
- [ ] Each component returns 0.0–1.0; missing subject fields → that component contributes 0 weight (rebalance weights proportionally so the total still scales to 0–1).
- [ ] Total returned as percentage in API + UI.

### 8.4 API endpoint
- [ ] `GET /review/queue/{item_id}/candidates?layer={1|2|3}&include_layer3=false`
- [ ] Returns:
  ```
  {
    subject: { /* fields from the review-item payload, editable on frontend */ },
    candidates: [
      {
        project_id, project_name, canonical_address, developer, units_total,
        units_market, units_affordable, units_workforce, product_type,
        age_restriction, pipeline_status, building_height_stories, lat, lng,
        match_likelihood: 0.0–1.0,
        match_signals: { geographic, address, name, developer, units, product_type },
        match_layer: 1 | 2 | 3,
        distance_meters: float | null,
        open_review_item_count: int,
      },
      ...
    ],
    layer_3_available: bool,    // hints frontend to show "show more" affordance
  }
  ```

### 8.5 Frontend Discovery tab
- [ ] **Tab UI** on `/review` page. State persisted in URL query param (`?tab=discovery`).
- [ ] **List view** shows discovery items grouped by source (one card per article or per LADBS permit).
- [ ] **DedupCard component:**
  - [ ] Header: source name (small), project name (if extracted), project address, `Potential matches: N` and `New candidate probability: X%` indicators
  - [ ] Subject row: editable cells inline (text, number, dropdown depending on field type)
  - [ ] Candidate table below
  - [ ] Header action `Create new` (no modal confirmation)
- [ ] **CandidateTable component:**
  - [ ] Columns from the agreed list (project name, address, developer, units total/market/affordable/workforce, product type, age restriction, status, building height, lat, lng, match likelihood)
  - [ ] Sortable by clicking column headers
  - [ ] Cell-level overlap highlighting:
    - Substring match for name / address / developer (case-insensitive)
    - Cross-field numeric equality for unit counts (subject's "312 affordable" highlights candidate's "312 total" too)
    - Distance-threshold highlight for lat/lng (within ~250m subject lat/lng)
    - Building-height match within ±2 stories
  - [ ] Per-row actions: `Match to this`, `Create new + link as ▾` (dropdown shows relationship types)
  - [ ] Pre-existing review-item badge `⚠ N open` (hover-popover lists them)
  - [ ] Match-likelihood column at right, sortable
  - [ ] Default sort: subject first, then by match likelihood DESC (Layer 1 candidates first)
- [ ] **MapPopup component:** opened by a map icon button on the card header. Renders MapLibre map showing subject pin + numbered candidate pins corresponding to table row numbers. Click outside or close button dismisses.
- [ ] **Match-with-deltas modal:** when reviewer clicks `Match to this` and the subject has any field values that disagree with the matched project's current values, show a confirm step listing the deltas with checkboxes for which to apply.
- [ ] **Live updates after match/create:**
  - [ ] On successful Create or Create+link, push the new project onto the local candidate cache so subsequent cards see it
  - [ ] On Match-to-this, mark the matched project in the cache so subsequent cards can sort it higher

### 8.6 Backend write actions
- [ ] **`POST /review/items/{item_id}/match`** — body: `{matched_project_id, accept_deltas: [field_name, ...]}`. Updates `news_project_references.matched_project_id` (or analogous for permits), closes review item, optionally writes value-change review items for non-accepted deltas (so they queue for normal review). Audit row in `change_log`.
- [ ] **`POST /review/items/{item_id}/create-and-link`** — body: `{relationship_type, related_project_id, project_fields}`. Creates Project, creates `project_relationships` row, closes review item. Audit rows.
- [ ] **`POST /review/items/{item_id}/create`** — body: `{project_fields}`. Creates Project (unlinked), closes review item. Audit row.

### 8.7 Tests
- [ ] Backend retrieval: fixtures exercise each Layer; ordering correct; overlap signals correctly computed
- [ ] Backend match-likelihood: each component returns 0-1; missing-field rebalancing works
- [ ] Backend action endpoints: each writes the correct rows + closes the item
- [ ] Frontend: component tests for DedupCard / SubjectRow / CandidateTable / MapPopup
- [ ] Frontend: e2e for match-then-next-card flow with live update verifying new project appears in subsequent card

### 8.8 Smoke validation
- [ ] Walk through 5 of the 25 active `new_candidate` permit items via the new Discovery tab. Confirm: Layer 1 candidates appear (probably empty for these — they were unmatched for a reason), Layer 2 candidates appear with sensible likelihood, overlap highlighting works on at least one row.
- [ ] Create a Render one-off paste-link smoke that should produce a `new_candidate`, then process it via the new tab.
- [ ] Verify a Match-to-this + Match-with-deltas flow on a fixture.

**Acceptance:** Discovery tab loads with all open discovery items, candidate tables populate quickly (<500ms per card), overlap highlighting visible, match/create flows work end-to-end, live updates work in-session, no duplicate projects created during a curated stress test of 5+ articles about the same potential project.

---

## 9. Cross-cutting concerns

### 9.1 Testing discipline
- Each item ships with both unit tests (fast, run locally) and at least one smoke validation (against prod data or a curated fixture).
- After each item ships, run the full test suite to confirm no regressions.

### 9.2 Migration discipline
- Per `docs/ops/migration_runbook.md`: `pg_dump --format=custom` backup before any prod schema migration.
- Each migration commit's PR description records the backup identity, target DB, pre/post Alembic versions, verification SQL summary.

### 9.3 Rollback paths
- Items 1, 2, 4 are pure code changes; rollback = revert commit + redeploy.
- Items 3 and 5 have frontend + backend changes; revert may require coordinating both deploys.
- Item 6 has schema changes; rollback requires a downgrade migration (must be tested before applying forward).

### 9.4 Slice coordination
- Slice 5/6/7 regression work is settled. No active parallel agent at time of plan writing.
- If a parallel agent resumes work mid-implementation, coordinate before touching:
  - `src/tcg_pipeline/resolution/fields/status.py` (Item 1)
  - `src/tcg_pipeline/news/integration.py` (Item 4)
  - `src/tcg_pipeline/agents/prompts/*` (Items 4 + a few)

### 9.5 Deployment cadence
- Each item ships as one or two commits; deploy after each to validate in prod against real data before moving to the next item.
- Smoke test the Render API + worker after each deploy.

---

## 10. Open questions — resolved 2026-05-13

- [x] **LADBS allowlist completeness (Item 1).** Production has only 36 LADBS evidence rows today, mostly synthetic. Final v1 allowlist (combined from Socrata API docs + LADBS terminology + fail-additive sentinel for unknowns):
  - **Additive (no regression candidate):** `Issued`, `Permit Finaled`, `Ready to Issue`, `Plan Check Submitted`, `Plans Approved`, `Pending Inspection`, `CofO Issued`, `CofO Pending`, empty/None
  - **Regression signals (DO emit candidate):** `Expired`, `Cancelled`, `Void`, `Revoked`, `Withdrawn`, `Plan Check Cancelled`, `Permit Cancelled`
  - **Sentinel:** unknown `status_desc` → treat as additive + log a `system_alert` (key `ladbs_unknown_permit_status`, scope `{status_desc: <value>}`) once per unique value per day so operators can extend the list as new values appear.
- [x] **News source `display_name` column (Item 4).** Not needed — `news_sources.name` already holds display-friendly values: `"Urbanize LA"`, `"L.A. Business Journal"`, `"Paste-a-link"`. Use `NewsSource.name` directly. Drop the proposed `display_name` migration from Item 4 scope.
- [x] **CoStar / Pipedream stories column names (Item 6).** Confirmed against real workbooks and superseded by model-file verification on 2026-05-13:
  - **Canonical Project field:** `projects.stories` already exists. No new `elevation` Project field is needed.
  - **CoStar (both MF and Commercial exports):** column header `Number Of Stories`; already mapped to `Project.stories`.
  - **Pipedream DataStorage sheet:** column `Elevation` at col 48 (header row is row 3, not row 1); already mapped to `Project.stories`.
- [x] **Relationship picker integration (Item 5).** Located in `app/(app)/pipeline/[projectId]/relationship-picker.tsx`. The component uses `RELATIONSHIP_OPTIONS` (5 types — phase, master_plan, counterpart, duplicate, supersedes — exactly what Item 5 needs) and `useActionState` hooks bound to project-detail-specific server actions in `./actions`. **Not directly reusable as-is** — actions are scoped to "I have a project, add a relationship to another existing project." For the dedup-table create-and-link flow we need a different action shape that creates BOTH a new Project AND a relationship in one transaction. **Plan:** extract the inner UI atoms (`RelationshipTypeSelect`, `ProjectSearchDropdown`) into shared components under `components/relationships/`, then write a new wrapper in the dedup-table that uses those atoms with a new action bound to `POST /review/items/{item_id}/create-and-link`. Estimated ~2-3 extra hours for the extraction; small addition to Item 5 effort.

---

## 11. Tracking progress

Mark sub-tasks complete by changing `[ ]` to `[x]`. Append per-item lessons learned at the bottom of each item's section. Update the **Last updated** date at the top whenever the plan changes.

When all six items are `done`, link this document from the AGENT.reset row in ROADMAP and we begin cycle 1 prep.

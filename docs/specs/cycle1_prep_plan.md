# Pre-AGENT.reset Cycle 1 Implementation Plan

> **Living plan.** This is the operational checklist for executing the six pre-cycle-1 Review Queue UX items scoped on 2026-05-13. Update it as work lands — check off sub-tasks, record open questions resolved, and capture lessons learned. The ROADMAP rows say *what* and *why*; this document says *how* and *in what order*.
>
> **Last updated:** 2026-05-14 (Item 5C ready for review; Discovery tab shell)
> **Maintained by:** Nate Goldstein + Claude Code

---

## 1. Overview

Six pre-cycle-1 items must land before `AGENT.reset` cycle 1 begins:

| # | Item | Roadmap row | Status | Commit |
|---|---|---|---|---|
| 1 | Resolver-level suppression of benign LADBS follow-on permits | `UX.regression-suppression` | ✅ Shipped 2026-05-13 | `e4c37c4` |
| 2 | Card source-detail rendering for permit + CoStar regression cards | `UX.card-source-detail` | ✅ Shipped 2026-05-13 | `93e8648` |
| 3 | 3-field Current/Evidence/Result model + Confirm/Defer/Detail + auto-advance | `UX.3-field-review` | ✅ Shipped 2026-05-14 | `aee2b26` |
| 4 | Narrative descriptiveness (name permit types + news sources) | `UX.narrative-detail` | ✅ Shipped 2026-05-13 | `c111433` |
| 5 | Duplicate-prevention table for new candidates + possible matches | `UX.dedup-table` | 🚧 In progress | — |
| 6 | Building height / stories resolver support (canonical field `stories`) | `UX.building-height` | ✅ Shipped 2026-05-13 | `7ce99af` |

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
- [x] **Write the Alembic migration.** Adds one nullable integer column `candidate_stories` to `news_project_references`. No Project migration needed.
- [x] **Update db/models.py** for `NewsProjectReference.candidate_stories` only.
- [ ] **Run migration locally + verify production/staging target only after backup.** Per `docs/ops/migration_runbook.md` discipline: `pg_dump` backup first. The remaining migration is nullable/additive and should land before `AGENT.reset`.
- [x] **Update extract_v2 schema + prompt** to capture `candidate_stories` from articles. Prompt language should say `building height in stories` so the extraction instruction is unambiguous; store the DB/model field as `candidate_stories`.
- [x] **Update news integrator** to write `candidate_stories` on reference rows and thread it into evidence as `stories`.
- [x] **Write the resolver.** New file `resolution/fields/stories.py`. Rule: most recent explicit value wins, with source-priority tiebreak for same-date matches (Pipedream > CoStar > news_article). Treat null as no evidence - null Project value remains null if no evidence has the field.
- [x] **Wire into resolution_engine.** Register the resolver alongside existing field resolvers.
- [x] **Update project detail Snapshot UI.** Verify `stories` is already displayed; adjust label if needed.
- [ ] **Tests:**
  - [x] Migration: forward + downgrade SQL generation works cleanly; live application remains gated by the backup/runbook step above
  - [x] News extraction: prompt/schema accepts `candidate_stories` from a fixture
  - [x] News integrator: reference row and evidence expose `stories`
  - [x] Resolver: most-recent-wins, source-priority tiebreak, null handling
- [ ] **Apply migration in production.** Backup + apply per migration runbook. Record in Decision Log.
- [x] **Commit + push.**

**Acceptance:** Migration applied to production, CoStar / Pipedream / news can populate `stories`, resolver writes the correct value, and project-detail Snapshot shows the field.

**Deferred follow-ons:**

- [ ] **Investigate LADBS height-to-stories semantics.** `pi9x-tg5x` exposes `height` as decimal feet; deprecated `cpkv-aajs` / `hbkd-qubn` expose `of_stories`. A live-feed mapping requires per-product-type feet-per-story rules and an evidence-quality decision. Park as a separate source-data investigation, not Phase 2 scope.

**Lessons learned:**

- Verify the actual source schema against a live API call before adding it to a multi-source resolver. Documentation hedges like `TBD, verify at first map` should be treated as work-not-yet-done, not as a placeholder to code against.
- Verify the canonical schema before naming a new field. Both the prior `elevation` framing and the LADBS `stories_proposed` assumption were spec-side hallucinations that would have been caught by a quick model-file grep. Pre-flight verification belongs at planning time, not just implementation time.
- Keep retry schemas aligned with the active extraction schema when adding required prompt fields; otherwise a schema-invalid first pass can retry back into an older shape.
- When an existing Project field becomes resolver-owned, verify the UI grouping as well as field visibility. `stories` was already visible in Snapshot, but belonged in Core rather than read-only Source Facts after resolver wiring.
- Resolver-owned Snapshot fields also need API metadata wired in parallel today: override allowlists, integer coercion, activity labels, review priority, and diff support all need to agree until Item 3 decides whether to centralize field metadata.

---

## 7. Item 3 — UX.3-field-review

**Goal:** replace the current 2-value (Current vs Proposed) display with a 3-field Current/Evidence/Result model across all value-change review item types. Simplify action buttons to Confirm/Defer/Detail. Auto-advance to next item after action.

**Files to touch:**
- Backend: `src/tcg_pipeline/api/routers/review.py` (or wherever the queue + item endpoints live) — extend response shape
- Frontend: `app/(app)/review/` page + `components/review/` cards + actions
- Tests on both sides

**Sub-tasks:**

- [x] **Design the unified payload contract.** Every value-change review item (`status_change`, `status_regression_review`, `material_contradiction`, `unit_split_mismatch`, `override_contradiction`) returns:
  ```
  {
    field_name: "pipeline_status",
    field_type: "status_enum" | "integer" | "decimal" | "date" | "developer" | "product_type" | ...,
    current_value: <typed>,
    evidence_value: <typed>,
    agent_recommended_value: <typed> | null,  // actual agent output only; null when no agent weighed in
    default_result_value: <typed>,      // agent recommendation if present, else evidence_value, else current_value
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
- [x] **Backend changes:**
  - [x] Extend `GET /review/queue/{item_id}` and queue-list serialization to return the unified shape for value-change items. Map each existing value-change payload shape to the new `value_change` response.
  - [x] Map `agent_recommended_value` from `payload.agent_revised_verdict` / `payload.agent_recommendation` only when an agent has made a recommendation. For agentless cards (including direct slice-5 `status_regression_review` cards), leave it null and default the Result cell to `evidence_value` so Confirm applies the evidence-proposed change unless the reviewer edits it.
  - [x] Keep field metadata distributed for now, but add a drift test across the review payload metadata, override allowlists, integer coercion set, activity labels, and changelog priority map. Defer a full registry refactor until Item 5 or later.
  - [x] Tests for the real value-change item types (`status_change`, `status_regression_review`, `override_contradiction`). Material contradictions and unit-split mismatches ride through `status_change` payloads today.
- [x] **Frontend changes:**
  - [x] Build a generic `<ThreeFieldEditor>` component that takes `current`, `evidence`, `defaultResult`, `fieldType`, `constraints` and renders three cells:
    - Current (read-only)
    - Evidence (read-only)
    - Result (editable per field type — dropdown / number / date / autocomplete)
  - [x] Refactor the review-item detail page (`app/(app)/review/[itemId]/`) to use `<ThreeFieldEditor>` for value-change items.
  - [x] Simplify value-change action buttons: Confirm (stages the Result value), Defer, Detail from queue cards.
  - [x] Implement auto-advance: detail Confirm/Defer routes to the next active item; queue-card actions move focus to the next visible item before refresh.
  - [x] Keep the existing decision-card layout for discovery items (`new_candidate`, `possible_match`) — those go to the new Discovery tab built in Item 5.
- [x] **Server actions:**
  - [x] Confirm action accepts the Result value and writes a `review_decisions` row + applies the override / change_log entry through the existing review-workflow helpers.
  - [x] Defer action just marks the item deferred without applying.
- [x] **Tests:**
  - [x] Backend: serialization test per real value-change item type
  - [x] Backend: metadata drift test for resolver-owned scalar review fields
  - [x] Frontend: payload helper tests for unified value-change defaults and Result coercion by field type
  - [x] E2E: authenticated Playwright walkthrough confirms a modified Result value (`1450`) stages and commits to `change_log` as the modified value.
- [x] **Local validation** on staging data: authenticated Playwright walkthrough confirmed a 1,400-unit Result input renders as raw `1400`, unedited Confirm stages and commits `1400`, and `status_regression_review` Result dropdowns exclude the three `Delete-*` statuses. Synthetic validation rows were cleaned up afterward; no existing open 1,000+ `total_units` card was available.
- [x] **Commit + push.**

**Acceptance:** All real value-change item types render with the 3-field model; Confirm applies the Result value (whether equal to current, evidence, or user-edited); auto-advance moves to the next item; existing decision-card layout still works for discovery items.

**Lessons learned:**

- Treat `agent_recommended_value` as agent output only. Agentless cards should default Result to the evidence value, especially status-regression cards where the reviewer is judging the proposed regression.
- A full shared field registry is larger than Item 3. A focused review metadata helper plus drift tests catches the same class of misses without forcing API, activity, override, and project-detail modules through one refactor.

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

**Pre-flight decisions (resolved 2026-05-14):**

- **Match-with-deltas component reuse:** reuse the Item 3 `<ThreeFieldEditor>` presentation, but generalize its prop boundary to a plain value-change UI model (`fieldName`, `fieldLabel`, `fieldType`, `currentValue`, `evidenceValue`, `defaultResultValue`, `constraints`). Real review items and project-vs-project delta rows both adapt into that shape; match deltas do not pretend to be persisted review items.
- **Match-with-deltas API body:** keep v1 narrow: `accept_deltas` is a list of field names, not per-delta edited values. Inline value editing happens on the subject row before Match/Create; post-match disagreements that are not accepted become normal value-change review items.
- **Subject-row inline edit write path:** no direct PATCH endpoint in v1. Keep subject edits client-local and bundle them into Match/Create requests as `edits: {...}` so source-reference correction and match/create decision apply atomically. This avoids a window where candidate/reference state diverges from extracted source state before the reviewer commits an action.
- **Card-focus URL state:** persist only `?tab=discovery` during normal navigation. Accept `?tab=discovery&card=<item_id>` for direct loads, but keep focus changes local so keyboard/table movement does not clutter browser history.
- **Multi-reference articles:** render one Discovery card per review item/reference. This matches the queue's item-as-unit model and avoids article-level grouping special cases.

**Reviewable sub-phases:**

1. **5A — Backend retrieval + indexes:** pg_trgm migration, PostGIS index verification, similarity helpers emitting per-signal `{score, contributed, searched, label, detail, weight}`, layered candidate retrieval with a top-level `searched` descriptor for the confident empty state, backend tests covering per-signal contributions and `searched` content.
2. **5B — Candidate API endpoint:** `GET /review/queue/{item_id}/candidates` returning the per-signal payload + `searched` block + `new_candidate_probability`; separate `GET /review/items/{item_id}/match-preview?candidate_id=...` endpoint for the row-focused impact-preview line; subject payload normalization; response schemas; API tests.
3. **5C — Discovery tab shell:** tab routing/state, discovery item grouping, empty/loading/error states, basic card shell.
4. **5D — Candidate table + overlap:** subject-row editor, candidate table with sortable columns, cell-level overlap highlighting, per-row signal chips rendering `match_signals` (green/gray/hidden), row color band by likelihood (paired with the numeric percentage, not replacing it), confident empty state rendering the `searched` block, keyboard navigation into the table (`1`–`9` row select, `m` Match, `n` Create-new with confirm modal, `l` link-dropdown), map popup.
5. **5E — Match/Create actions:** atomic `edits` write path, match-with-deltas confirm step (reusing the generalized ThreeFieldEditor UI model), create/link actions with `relationship_type ∈ {phase, master_plan, counterpart, supersedes}` (no `duplicate`), Create-new confirm modal, audit/change-log entries with "absorbed reference X from source S" framing, focused-row impact-preview line backed by the match-preview endpoint, action tests.
6. **5F — End-to-end validation:** synthetic and real queue walkthroughs (including a no-candidate card to validate the confident empty state, a multi-signal Layer-1 card to validate chip rendering, and a 1000+ unit project to keep the Item 3 number-formatting regression closed), live-update behavior, performance check (<500ms per card), docs/roadmap completion, promote ROADMAP row to `done`.

**Sub-tasks:**

### 8.1 Schema + indexes
- [x] **Migration** adds two GIN indexes:
  ```sql
  CREATE INDEX ix_projects_canonical_address_trgm ON projects USING GIN (canonical_address gin_trgm_ops);
  CREATE INDEX ix_projects_project_name_trgm ON projects USING GIN (project_name gin_trgm_ops);
  ```
- [x] Verify PostGIS GIST index on `projects.location` exists (from B.0a). Add if missing.

### 8.2 Backend retrieval (`matching/candidates.py`)
- [x] **Layer 1 — hard signals.** Query returns all rows matching any of:
  - Geographic: `ST_DWithin(location, subject_point, 250)` (~250m)
  - APN: matching `project_identifiers.value` where `kind='apn'`
  - CoStar Property ID: matching `project_identifiers.value` where `kind='costar_property_id'`
  - Canonical address: exact match on `canonical_address` (normalized)
  - Developer + any other secondary signal: canonical_developer match AND (within 1km OR product_type match OR partial address)
- [x] **Layer 2 — soft signals.** Top 20 by weighted likelihood score (see formula in §8.3). Query uses pg_trgm `similarity()` function for name and address.
- [x] **Layer 3 — broader sweep.** Behind explicit "show more" parameter. Returns all projects within 1km OR any matching trigram token.
- [x] **Performance constraint:** Layer 1 + Layer 2 capped to 25 total rows per response. Layer 3 capped to 100.

### 8.3 Match-likelihood formula
- [x] Implement in `matching/similarity.py`:
  ```
  likelihood = 0.30 * geographic_proximity_score        # exponential falloff from 0 at 1km to 1.0 at 0m
             + 0.25 * address_similarity                 # pg_trgm similarity(canonical_address, subject_address)
             + 0.20 * developer_match                    # 1.0 exact-canonical, 0.7 fuzzy ≥0.85 ratio, else 0
             + 0.10 * name_similarity                    # pg_trgm similarity(project_name, subject_name) — null subject_name → 0
             + 0.10 * unit_count_proximity               # 1.0 within ±5%, exponential falloff
             + 0.05 * product_type_match                 # 1.0 exact, 0 else
  ```
- [x] Each component returns 0.0–1.0; missing subject fields → that component contributes 0 weight (rebalance weights proportionally so the total still scales to 0–1).
- [x] Total returned as normalized 0.0-1.0 for API/UI to format as a percentage.
- [ ] **Recency weighting is deferred** — see Deferred follow-ons below. The v1 formula does not include any recency component. Cycle 1 calibration will tell us whether stale candidates clutter the top of result lists; if so, add a small (~5%) weight on `Project.updated_at` or `last_evidence_at` as a follow-up.

### 8.4 API endpoint
- [x] `GET /review/queue/{item_id}/candidates?layer={1|2|3}&include_layer3=false`
- [x] Returns:
  ```
  {
    subject: { /* fields from the review-item payload, editable on frontend */ },
    candidates: [
      {
        project_id, project_name, canonical_address, developer, units_total,
        units_market, units_affordable, units_workforce, product_type,
        age_restriction, pipeline_status, building_height_stories, lat, lng,
        match_likelihood: 0.0–1.0,
        match_signals: {
          identifier?: {score, contributed, searched, label, detail, weight},
          geographic: {score, contributed, searched, label, detail, weight},
          address: {score, contributed, searched, label, detail, weight},
          name: {score, contributed, searched, label, detail, weight},
          developer: {score, contributed, searched, label, detail, weight},
          units: {score, contributed, searched, label, detail, weight},
          product_type: {score, contributed, searched, label, detail, weight},
        },
        match_layer: 1 | 2 | 3,
        distance_meters: float | null,
        open_review_item_count: int,
      },
      ...
    ],
    searched: { layer_1: [...], layer_2: {...}, layer_3: {...} },
    new_candidate_probability: 0.0–1.0,
    layer_3_available: bool,    // hints frontend to show "show more" affordance
  }
  ```
- [x] **`searched` block contents.** Backend retrieval (`matching/candidates.py`) emits this descriptor on every response so the empty-state UI can explain itself. Shape: `layer_1` is the list of hard-signal probes tried (`apn`, `costar_property_id`, `address_exact`, `geo_250m`, `developer_plus_secondary`); `layer_2` records the trigram thresholds + weight set used; `layer_3` records whether broader sweep is reachable from this query. Each entry includes a human-readable label so the frontend renders without translation tables.
- [x] **`GET /review/items/{item_id}/match-preview?candidate_id=...`** — separate lightweight endpoint returning `{review_items_to_close: int, evidence_rows_to_reattach: int, value_change_items_that_would_be_queued: [field_name, ...]}` for the focused candidate row. Called on row focus/hover before the reviewer commits Match. Kept off the main `/candidates` response so we don't pay the preview cost for all 25 candidates on every card load. Preview count scope is the current item plus open/staged siblings tied to the same `news_project_references.id`; target-project-wide review items stay out of scope.
- [x] `?layer=3` implies `include_layer3=true` as a convenience. The frontend can request the broader sweep with either explicit flag.

### 8.5 Frontend Discovery tab
- [x] **Tab UI** on `/review` page. State persisted in URL query param (`?tab=discovery`).
- [x] **List view** shows discovery items grouped by review item/reference (one card per `new_candidate` / `possible_match` item).
- [ ] **DedupCard component:**
  - [x] Header: source name (small), project name (if extracted), project address, `Potential matches: N` and `New candidate probability: X%` indicators
  - [ ] Subject row: editable cells inline (text, number, dropdown depending on field type). Edits stay client-local until included atomically in Match/Create action payloads as `edits: {...}`.
  - [ ] Candidate table below
  - [ ] **Confident empty state** when no candidates returned: render the API response's `searched` block as a paragraph (e.g., "No candidates found within 1km. Searched by APN, CoStar Property ID, normalized address, name/address trigrams (threshold from `searched.layer_2.trigram_min_score`), and canonical developer match. None passed Layer 1 or Layer 2 thresholds."). Do not render a bare empty table — reviewers second-guess silent failure.
  - [ ] **Header action `Create new`** — opens a small confirm modal ("Create new project — no match selected. Continue?") before write. False-new is the highest-cost outcome in this flow, so it gets a friction step; the affordance itself stays visually de-emphasized vs the per-row Match action.
- [ ] **CandidateTable component:**
  - [ ] Columns from the agreed list (project name, address, developer, units total/market/affordable/workforce, product type, age restriction, status, building height, lat, lng, match likelihood)
  - [ ] Sortable by clicking column headers
  - [ ] Cell-level overlap highlighting:
    - Substring match for name / address / developer (case-insensitive)
    - Cross-field numeric equality for unit counts (subject's "312 affordable" highlights candidate's "312 total" too)
    - Distance-threshold highlight for lat/lng (within ~250m subject lat/lng)
    - Building-height match within ±2 stories
  - [ ] **Row color band by match-likelihood**, paired with (not replacing) the numeric percentage. Layer 1 candidates render with a green band; Layer 2 with a green-to-yellow-to-orange gradient by likelihood; Layer 3 with a neutral gray band. The band reads in <200ms; the number is still there for ties and precision.
  - [ ] **Per-signal chips inline next to the match-likelihood column.** Render one chip per signal from `match_signals` (geographic, address, name, developer, units, product_type, identifier when present). Green chip when `contributed=true`; gray when searched but not contributed; omitted when the subject lacked the field. Each chip's tooltip shows the underlying `score` and `detail`. This is the "why it matched" surface — reviewer scans reasons in under a second.
  - [ ] Per-row actions: `Match to this`, `Create new + link as ▾` (dropdown shows `phase` / `master_plan` / `counterpart` / `supersedes` — see §8.6 for the rationale on dropping `duplicate` from this dropdown).
  - [ ] **Impact preview line** on the focused candidate row, fetched lazily from `GET /review/items/{item_id}/match-preview?candidate_id=...` (see §8.4). Renders as a single line ("This will close 3 open review items and reattach 4 evidence rows to project X.") above the per-row Match button so the reviewer sees the blast radius before committing.
  - [ ] Pre-existing review-item badge `⚠ N open` (hover-popover lists them)
  - [ ] Match-likelihood column at right, sortable
  - [ ] Default sort: subject first, then by match likelihood DESC (Layer 1 candidates first)
- [ ] **Keyboard navigation into the candidate table.** Extends the existing `[`/`]` queue navigation (C.k.1).
  - [ ] `↑`/`↓` move focus between candidate rows on the active card.
  - [ ] `1`–`9` quick-select the corresponding candidate row.
  - [ ] `m` triggers Match-to-this on the focused row (opens match-with-deltas modal if there are field disagreements with the subject; otherwise commits directly).
  - [ ] `n` triggers the card-header Create-new flow — opens the confirm modal first, doesn't write on the keypress alone (matches the friction step above).
  - [ ] `l` opens the relationship dropdown on the focused row's Create-new-link action.
- [ ] **MapPopup component:** opened by a map icon button on the card header. Renders MapLibre map showing subject pin + numbered candidate pins corresponding to table row numbers. Click outside or close button dismisses.
- [ ] **Match-with-deltas modal:** when reviewer clicks `Match to this` and the subject has any field values that disagree with the matched project's current values, show a confirm step listing the deltas with checkboxes for which to apply. Reuse the generalized ThreeFieldEditor value-change UI model for each delta row.
- [ ] **Live updates after match/create:**
  - [ ] On successful Create or Create+link, push the new project onto the local candidate cache so subsequent cards see it
  - [ ] On Match-to-this, mark the matched project in the cache so subsequent cards can sort it higher

### 8.6 Backend write actions
- [ ] **Write payload convention:** `edits` always targets the source/reference row (`news_project_references.candidate_*` or the analogous permit subject payload) before the reviewer commits the action. `project_fields` is the create-shape for the new `Project`; for create flows it may include corrected subject values, but it does not replace the source-row correction audit.
- [ ] **Relationship-type vocabulary for the Discovery flow** is `{phase, master_plan, counterpart, supersedes}`. **`duplicate` is intentionally NOT in this set.** The Discovery flow operates on `article → existing-project` or `article → new-project`; "Create new + link as duplicate" is a semantic contradiction (if the subject is the same underlying project as N, the right action is Match-to-this, not Create). Project-to-project duplicate marking is a separate decision that operates on two already-persisted projects and is deferred to a future `UX.project-merge` workflow (see Deferred follow-ons below and the ROADMAP row).
- [ ] **`POST /review/items/{item_id}/match`** — body: `{matched_project_id, edits: {...}, accept_deltas: [field_name, ...]}`. Applies subject edits atomically, updates `news_project_references.matched_project_id` (or analogous for permits), closes review item, optionally writes value-change review items for non-accepted deltas (so they queue for normal review). Audit row in `change_log` with explicit "absorbed reference X from source S on date D by user U" framing — both the surviving project's Activity tab and the audit trail need to be able to point at the source reference after the merge.
- [ ] **`POST /review/items/{item_id}/create-and-link`** — body: `{relationship_type, related_project_id, project_fields, edits: {...}}` where `relationship_type ∈ {phase, master_plan, counterpart, supersedes}`. Applies subject edits atomically, creates Project, creates `project_relationships` row, closes review item. Audit rows.
- [ ] **`POST /review/items/{item_id}/create`** — body: `{project_fields, edits: {...}}`. Applies subject edits atomically, creates Project (unlinked), closes review item. Audit row.

### 8.7 Tests
- [x] Backend retrieval: fixtures exercise each Layer; ordering correct; overlap signals correctly computed
- [x] Backend match-likelihood: each component returns 0-1; missing-field rebalancing works
- [x] Backend retrieval: `searched` block emitted with the expected probes per layer; per-signal `contributed` flag agrees with score threshold
- [ ] Backend action endpoints: each writes the correct rows + closes the item; Match-to-this `change_log` row contains "absorbed reference X from source S" framing
- [ ] Backend action endpoints: `relationship_type` validator rejects `duplicate` for create-and-link
- [x] Backend match-preview endpoint: returns correct `review_items_to_close` and `evidence_rows_to_reattach` counts for a focused candidate
- [ ] Frontend: component tests for DedupCard / SubjectRow / CandidateTable / MapPopup
- [x] Frontend helper tests for Discovery item filtering, one-card-per-review-item grouping, and subject normalization
- [ ] Frontend: per-signal chip renders green-when-contributed / gray-when-searched / hidden-when-absent
- [ ] Frontend: row color band agrees with match-likelihood band thresholds
- [ ] Frontend: confident empty state renders the `searched` block when no candidates returned
- [ ] Frontend: keyboard nav 1–9, `m`, `n`, `l` route to the right handlers; `n` requires confirm-modal interaction before writing
- [ ] Frontend: e2e for match-then-next-card flow with live update verifying new project appears in subsequent card

### 8.8 Smoke validation
- [ ] Walk through 5 of the 25 active `new_candidate` permit items via the new Discovery tab. Confirm: Layer 1 candidates appear (probably empty for these — they were unmatched for a reason), Layer 2 candidates appear with sensible likelihood, overlap highlighting works on at least one row.
- [ ] Create a Render one-off paste-link smoke that should produce a `new_candidate`, then process it via the new tab.
- [ ] Verify a Match-to-this + Match-with-deltas flow on a fixture.

**Acceptance:** Discovery tab loads with all open discovery items, candidate tables populate quickly (<500ms per card), overlap highlighting visible, match/create flows work end-to-end, live updates work in-session, no duplicate projects created during a curated stress test of 5+ articles about the same potential project.

**5A lessons learned:**
- Shape retrieval metadata before the API layer: "why it matched" chips and confident empty states require per-signal scores, `contributed` flags, and a `searched` summary from the backend retrieval module.
- Developer is a stronger cycle-1 dedup signal than project name; the initial weights now use developer 0.20 and name 0.10.

**5B lessons learned:**
- Keep subject/candidate delta computation shared between preview and write paths. The preview emits only field names today, but the same helper will feed the Match-with-deltas action in 5E.
- Preview blast-radius counts should follow the reviewer's decision scope: current discovery card plus same-reference open/staged sibling cards, not every open item on the matched project.

**5C lessons learned:**
- Keep Discovery focus local by default. Deep links can seed the focused card with `card=<item_id>`, but normal focus movement should not write browser history entries.
- One card per review item/reference keeps the shell aligned with queue semantics; article-level grouping can wait until real reviewer feedback shows the density tradeoff is worth it.

**Deferred follow-ons:**

- **`UX.project-merge` — project-to-project duplicate marking + record combination.** When two already-persisted projects describe the same underlying real-world project, the reviewer needs a way to merge them: pick a survivor, re-attach evidence rows to the survivor, write a `project_relationships` row of type `duplicate`, soft-delete or status-flag the loser, leave an audit trail. Most natural surface is the pipeline tab's project-detail view, not the Discovery tab — Discovery operates on incoming `article → project` decisions, not on `project → project` decisions. Reuse the match-with-deltas component infrastructure shipped in Item 5 (the generalized ThreeFieldEditor value-change UI model handles project-vs-project deltas the same way it handles subject-vs-candidate). Estimated effort: ~1 week given Item 5 infrastructure. See ROADMAP row `UX.project-merge`.
- **Recency weighting in match-likelihood (`§8.3`).** Add a small (~5%) weight on `Project.updated_at` or `last_evidence_at` if cycle 1 reveals stale candidates cluttering the top of result lists. Skip in v1 because the existing hard signals (APN, address, geo, identifier) already do the heavy lifting and adding recency would mostly affect tiebreaking among already-strong candidates. Defer the calibration decision until real reviewer feedback is available.
- **Canonical developer lookup index.** `_add_developer_secondary_hard_signals` currently scopes to the market and normalizes non-null developer names in Python. This is acceptable for Los Angeles cycle 1 scale; Phase H or multi-market volume should add a `projects.canonical_developer` column (trigger/backfill populated) with a btree index so developer-secondary hard matches become SQL equality.
- **Layer 2 weight/threshold calibration.** Calibrate `MATCH_SIGNAL_WEIGHTS` and `TRIGRAM_MIN_SCORE` after cycle 1's first real walkthrough surfaces ranking complaints. Skip pre-calibration guesswork until the Discovery tab is running against organic queue items.
- **Audit-the-survivor surface on project detail.** Lesson from CRM merge UIs: when Match-to-this lands, the surviving project's Activity tab should render "Absorbed reference X from source S on date D by user U" as a discoverable history entry. Item 5's `change_log` row carries the data; verify the existing Activity feed renders it usefully and add an explicit row template if not.

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

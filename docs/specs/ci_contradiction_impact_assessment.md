# C.i Contradiction Detection — Impact Assessment

> **Pre-build deliverable for AGENT.2** per `agentic_escalation_design.md` §5.5.0.
> **Author:** Claude Code (research pass against the codebase as of 2026-05-04, commit `10a21e5`).
> **Reviewer:** Nate Goldstein. **Goal:** 10-minute scan to validate Claude Code's enumeration before AGENT.2 starts the rewrite.
> **Format:** Tables. `⚠ HUMAN REVIEW` markers flag rows where Claude Code's analysis depends on assumptions a researcher should verify. **Read those first.**

---

## TL;DR

- One in-process call site for `detect_project_contradictions` ([resolution/engine.py:229](../../src/tcg_pipeline/resolution/engine.py#L229)). One CLI call site for `detect_contradictions` ([cli.py:716](../../src/tcg_pipeline/cli.py#L716)). One backfill script call site ([scripts/backfill_evidence.py:31](../../scripts/backfill_evidence.py#L31)).
- 14 callers of `resolve_project(apply=True)` across the codebase. Each one indirectly invokes contradiction detection. AGENT.2's contradiction-move means each of these gets *agent-led* contradiction reasoning when the agent fires, and *fallback to today's `detect_project_contradictions`* otherwise. **No call-site signature changes** in the proposed design.
- One additional consumer of `values_contradict` (a pure helper exported from `contradictions.py`): the news review router uses it for evidence-stance classification ([api/routers/review.py:355](../../src/tcg_pipeline/api/routers/review.py#L355)). This consumer does not depend on contradiction *items* — only the comparator function. Safe.
- Frontend reads `item_type.includes("contradiction")` for dashboard tile counts and review-detail banner text. Payload shape matters: `current_override.value`, `evidence_ids`, `field_name`, plus the contradiction-value field. **For agent-produced contradiction items the value field is `proposed_alternatives: list[...]` per §I.1, replacing the legacy single `proposed_value`. Renderer handles both shapes — agent items via the multi-alt list, legacy/fallback items via the single-value field.**
- Schema columns `review_items.contradicted_override_id`, `review_items.contradiction_priority`, `review_items.field_name`, `review_items.winning_evidence_id`, `review_items.payload.evidence_ids` are all load-bearing and must be populated identically by the agent path.
- 8 dedicated tests in `tests/test_contradiction_detection.py`, plus contradiction cases in 5 other test files. **All must continue to pass under the agent-led flow.**

---

## A. Callers of `resolve_project(apply=True)` (each indirectly invokes contradiction detection)

| # | Path | Today's contract | Change under new flow | Migration | ⚠ HUMAN REVIEW |
|---|------|------------------|----------------------|-----------|----------------|
| A1 | [db/collect.py:184](../../src/tcg_pipeline/db/collect.py#L184) — Socrata/LADBS scheduled-collector path after evidence write | `resolve_project(apply=True)` runs post-evidence; contradiction detection fires post-resolve against active overrides | Permits will route through agent (AGENT.3) for `new_candidate`, >10% unit change, product-type change. For matched-confirmed permits without those triggers, contradiction detection still fires post-resolve via fallback. **No call-site change.** | None | — |
| A2 | [news/integration.py:758](../../src/tcg_pipeline/news/integration.py#L758) — news article integration after `_integrate_current_extraction` | Same as A1 | News will route through agent (AGENT.2). Agent owns pre-resolve "article doesn't fit state" reasoning. Post-resolve `detect_project_contradictions` fires only when agent did not run (no agent triggers fired) or fails. **No call-site change.** | None | — |
| A3 | [db/review_workflow.py:254](../../src/tcg_pipeline/db/review_workflow.py#L254) — set researcher override (sets `apply=True` after writing override) | Override write triggers re-resolve; new-evidence-vs-override contradictions fire as new review items | Same fallback semantics. Override writes are not agent-driven; fallback continues to handle this case. **No call-site change.** | None | — |
| A4 | [db/review_workflow.py:367](../../src/tcg_pipeline/db/review_workflow.py#L367) — same as A3 in clear-override path | Same as A3 | Same as A3 | None | — |
| A5 | [db/review_workflow.py:980](../../src/tcg_pipeline/db/review_workflow.py#L980) — review-decision commit path | Commit-induced re-resolve; detection runs post-resolve | Same fallback. The commit path explicitly passes `skip_contradiction_review_item_ids={review_item.id}` for `OVERRIDE_CONTRADICTION` commits ([review_workflow.py:1232](../../src/tcg_pipeline/db/review_workflow.py#L1232)) so the in-flight item isn't re-detected. **AGENT.2 must preserve this skip-list semantic in the agent path.** | Agent runner must accept and respect `skip_contradiction_review_item_ids` equivalently | ⚠ HUMAN REVIEW: confirm the agent's decision flow respects mid-commit skip semantics. Skip-list pattern is C.h-era code; verify nothing else depends on it that I missed. |
| A6 | [db/review_workflow.py:1053](../../src/tcg_pipeline/db/review_workflow.py#L1053) — commit-batch helper | Same as A5 | Same as A5 | Same as A5 | — |
| A7 | [db/review_workflow.py:1227](../../src/tcg_pipeline/db/review_workflow.py#L1227) — see A5 (this is the same line ref above; verify in code) | Same | Same | Same | ⚠ HUMAN REVIEW: A5/A6/A7 may overlap or be the same site. Verify line numbers map to distinct call sites in current HEAD. |
| A8 | [db/review_workflow.py:1832](../../src/tcg_pipeline/db/review_workflow.py#L1832) — late-stage commit helper | Same | Same | None | — |
| A9 | [db/seed.py:227](../../src/tcg_pipeline/db/seed.py#L227) — initial Pipedream/CoStar seed | Seed-time re-resolve; runs only once during fresh seed | Pipedream agent path is AGENT.5 (deferred). For the AGENT.2 cutover, seed path stays deterministic. **No call-site change in the sprint.** | When AGENT.5 ships, this site adds the Pipedream agent profile. Out of AGENT.2 scope. | — |
| A10 | [db/seed.py:357](../../src/tcg_pipeline/db/seed.py#L357) — seed update path | Same as A9 | Same as A9 | Same as A9 | — |
| A11 | [api/project_overrides.py:68](../../src/tcg_pipeline/api/project_overrides.py#L68) — pre-override resolution snapshot (`apply=False`) | Used to compute baseline before override write; **does not** trigger detection (apply=False) | No change. Pre-resolve dry-run remains identical. | None | — |
| A12 | [api/project_overrides.py:94](../../src/tcg_pipeline/api/project_overrides.py#L94) — set-override apply | FastAPI override write triggers re-resolve + detection | Same as A3 | None | — |
| A13 | [api/project_overrides.py:147](../../src/tcg_pipeline/api/project_overrides.py#L147) — clear-override apply | Same as A12 | Same as A12 | None | — |
| A14 | [cli.py:657](../../src/tcg_pipeline/cli.py#L657) — `resolve-all` CLI command | Bulk re-resolve sweep | Same. Bulk sweeps are admin-driven; agent path doesn't fire. Fallback continues. **No call-site change.** | None | — |
| A15 | [contradictions.py:82](../../src/tcg_pipeline/review/contradictions.py#L82) — `detect_contradictions` calls `resolve_project(apply=False)` to get a dry-run for batch detection | Pure read — no side effects | This is C.i internals. Agent path does not change this. **CLI `detect-contradictions` continues to work as today.** | None | — |

**Summary for §A.** No call-site signatures change. The contradiction-detection move is a change inside `detect_project_contradictions` and a new agent-runner integration in two specific places (A1 permits, A2 news). Everything else falls through to the existing post-resolve fallback path.

---

## B. Code that reads contradiction outputs

| # | Path | Today's contract | Change under new flow | Migration | ⚠ HUMAN REVIEW |
|---|------|------------------|----------------------|-----------|----------------|
| B1 | [api/routers/review.py:39, :355](../../src/tcg_pipeline/api/routers/review.py#L355) — `values_contradict` consumer for evidence-stance classification | Pure comparator: returns "supporting" / "against" by comparing proposed vs extracted values | `values_contradict` is exported from `contradictions.py` and is field-name + value comparison only. **Does not depend on contradiction items or detection flow.** Safe to keep as-is. | None | — |
| B2 | [api/routers/review.py](../../src/tcg_pipeline/api/routers/review.py) — `/review/queue` endpoint serializing review items, including `OVERRIDE_CONTRADICTION` | Reads `review_items` table directly via SQLAlchemy. Item-type-agnostic: returns whatever the row contains. | Agent-produced contradiction items use the same `review_items` row + `OVERRIDE_CONTRADICTION` enum value. **Payload shape must match** (see §C). | None — payload shape preserved | ⚠ HUMAN REVIEW: confirm that `/review/queue` does not branch on item_type other than via inclusion of agent-specific evidence-summary fields. The endpoint at [routers/review.py](../../src/tcg_pipeline/api/routers/review.py) is large and I read only the `_evidence_stance` helper closely. |
| B3 | [db/review_workflow.py:1177, :1234](../../src/tcg_pipeline/db/review_workflow.py#L1177) — review commit path branches on `item_type == ReviewItemType.OVERRIDE_CONTRADICTION` | DECISION_ACCEPT_NEW for contradiction items clears the override; DECISION_KEEP_OLD writes a refreshed override | Agent-produced items use the same `OVERRIDE_CONTRADICTION` enum value. Branch logic is unchanged. **No code change required here.** | None | — |
| B4 | [lib/dashboard/data.ts:283-315](../../lib/dashboard/data.ts#L283) — Dashboard contradictions tile | Filters open `review_items` where `item_type.includes("contradiction")`. Counts and priority-bucket distribution. | Agent-produced contradiction items use `item_type = "override_contradiction"`. Filter substring-matches; works identically. | None | — |
| B5 | [lib/dashboard/types.ts:34](../../lib/dashboard/types.ts#L34) — TypeScript shape for the tile data | `contradictions: { active, total, priorities }` | Same shape; no change. | None | — |
| B6 | [app/(app)/dashboard/page.tsx:181-210](../../app/(app)/dashboard/page.tsx#L181) — Dashboard tile rendering | Reads the typed shape; renders count + priority bars | Same. **The "Phase C contradiction detector" copy on line 192-193 should be updated to reflect the agent-led flow** but is cosmetic. | Update placeholder text post-AGENT.2 launch. | — |
| B7 | [app/(app)/pipeline/[projectId]/page.tsx:944](../../app/(app)/pipeline/[projectId]/page.tsx#L944) — Project Detail comment about Phase C contradiction work | Stale comment | Update copy post-AGENT.2 | None functional | — |
| B8 | [lib/review/payload.ts:224](../../lib/review/payload.ts#L224) — review-detail banner: `item.itemType.includes("contradiction") ? "This item conflicts with a manual override." : null` | Substring match on item type for banner text | Same — agent-produced items match the substring. **Consider whether agent-produced contradiction items want richer banner text** (e.g., "Agent flagged a contradiction; see reasoning trace") but this is a UX enhancement not a regression. | Optional UX work post-AGENT.2 | — |
| B9 | [lib/review/payload.test.ts:29, :64, :152](../../lib/review/payload.test.ts) — test fixtures use `itemType: "override_contradiction"` | Tests assert payload extraction for contradiction shapes | Same shape; tests should still pass. **Add new test cases for agent-produced contradiction items with `reasoning_trace` once schema is extended.** | New test cases as part of AGENT.2 | — |
| B10 | [lib/review/reviewed.test.ts:41](../../lib/review/reviewed.test.ts#L41) — reviewed-tab tests use contradiction item type | Same as B9 | Same as B9 | Same as B9 | — |
| B11 | [lib/project-detail/data.ts:560](../../lib/project-detail/data.ts#L560) — Changes-tab actor label for `"contradiction_detection"` source | Maps the literal string `contradiction_detection` (the value of `CONTRADICTION_DETECTION_ACTOR` from `contradictions.py`) to the display label "Contradiction detection" | Agent-produced contradictions need their own actor string. **Decision needed:** does the agent path use the same actor, a new `"agent_contradiction_detection"` actor, or distinguish via reasoning trace presence? Recommend: keep the same actor (renames break change-log audit history) and surface "agent" via the reasoning trace render. | Add label entries if the actor string changes | ⚠ HUMAN REVIEW: confirm desired UX for Changes-tab actor labels under the agent path. |

**Summary for §B.** Payload shape is the load-bearing contract. As long as agent-produced `OVERRIDE_CONTRADICTION` items populate the same payload fields, all readers above continue to work. UI copy in two places references "Phase C" stale framing and should be updated cosmetically.

---

## C. Schema columns and payload shape

| # | Field / column | Today's contract | Change under new flow | Migration | ⚠ HUMAN REVIEW |
|---|----------------|------------------|----------------------|-----------|----------------|
| C1 | `review_items.item_type = OVERRIDE_CONTRADICTION` | Existing enum value, partial unique index per `(project_id, field_name, item_type)` ([db/models.py:1614-1626](../../src/tcg_pipeline/db/models.py#L1614)) | Agent-produced contradictions use the same enum value. **Unique index continues to enforce one active contradiction per (project_id, field_name).** Preserves the C.tail.11 consolidation guarantee. | None | — |
| C2 | `review_items.field_name` | Set by agent or fallback to per-field name | Same | None | — |
| C3 | `review_items.winning_evidence_id` | FK to `evidence.id`, the resolver's winning candidate | Agent path also sets this from its decision. **Agent must populate identically; otherwise C.tail.12 decision-card supporting/dissenting evidence rendering breaks.** | Test that agent-produced items populate winning_evidence_id | ⚠ HUMAN REVIEW: confirm the agent's decision schema produces an unambiguous winning-evidence ID even when the agent's reasoning consults multiple candidates. Mismatch would degrade decision-card UX. |
| C4 | `review_items.contradicted_override_id` | FK to `researcher_overrides.id` (the active override the contradiction is against) | Agent must populate. Used by the C.tail.11 audit shape and by review-detail rendering. | None — populate from the `override_contradiction` case branch in agent | — |
| C5 | `review_items.contradiction_priority` | String (`"high"` / `"medium"`) — duplicates `priority` enum but in string form for legacy reasons | Agent must populate. | None | ⚠ HUMAN REVIEW: this column is duplicative with `priority`. AGENT.2 might be a clean opportunity to deprecate it. **Out of scope for this assessment** — flagging as a follow-on cleanup candidate. |
| C6 | `review_items.payload.origin` | `"override_contradiction_detection"` (set by C.i) | Agent path could use `"agent_override_contradiction"` to distinguish, OR keep same value for compatibility. **Recommend: keep same value; distinguish via presence of `agent_run_id` in payload.** | Add `agent_run_id` to payload when agent-produced | — |
| C7 | `review_items.payload.field_name` | String | Same | None | — |
| C8 | `review_items.payload.current_override.{value, set_by, set_at, note, mode, baseline}` | Snapshot of the active override at detection time | Agent must populate identically. | None | — |
| C9 | `review_items.payload.proposed_value` (legacy/fallback path) **and** `review_items.payload.proposed_alternatives` (agent path, per §I.1) | Legacy: the contradicting value as a single scalar. Agent: list of `{value, source_evidence_id, source_summary, agent_confidence}` ordered with the agent's best guess first. | **Resolved by §I.1.** Agent populates `proposed_alternatives`; STATUS_CHANGE items keep single-value `proposed_value`; renderer dispatches on which field is present. | None — both shapes coexist in the schema; renderer handles both. | ✅ Resolved 2026-05-04 — see §I.1. |
| C10 | `review_items.payload.evidence_ids` | List of evidence row UUIDs supporting the contradiction (used by decision-card supporting/dissenting render) | Agent path must populate. **For agent runs, this should include all evidence the agent consulted that bears on the field**, not just the winning candidate. | Test that decision-card rendering works for both legacy and agent-produced contradictions | ⚠ HUMAN REVIEW: confirm whether agent-produced items should populate `evidence_ids` from the resolver's view (only the contradicting evidence) or from the agent's `evidence_consulted[]` (potentially much wider). Affects decision-card UX. |
| C11 | `agent_runs` (new table per design §5.6) + `agent_run_review_items` (new join table per design §5.6) | N/A today | New. **Authoritative agent-run-to-review-item linkage lives in `agent_run_review_items(agent_run_id, review_item_id)`** — one agent run can produce multiple review items, so a column on `review_items` would force a 1:1 model that doesn't match reality. `payload.agent_run_id` may stay as a denormalized rendering hint to avoid a join on every list-view query, but it is not the source of truth. | UI render path: prefer the denormalized hint when present; backstop join via `agent_run_review_items` when not. Audit and admin queries always go through the join table. | — |

**Summary for §C.** Schema columns are stable. The agent path must populate them identically. The new `agent_runs` table augments the audit log; `agent_run_review_items` is the authoritative agent-to-review linkage.

---

## D. Audit / backfill tooling

| # | Path | Today's contract | Change under new flow | Migration | ⚠ HUMAN REVIEW |
|---|------|------------------|----------------------|-----------|----------------|
| D1 | [scripts/collapse_duplicate_review_items.py:138](../../scripts/collapse_duplicate_review_items.py#L138) — pre-`202604280018` collapse helper | SQL filter on `item_type IN ('status_change', 'override_contradiction')` | Agent-produced contradictions match the same item_type. **No script changes needed.** | None | — |
| D2 | [scripts/backfill_evidence.py:31, :98-100, :119](../../scripts/backfill_evidence.py#L31) — backfill scans for contradictions after evidence writes | Calls `detect_contradictions` directly post-backfill | Same call site; same function. **The agent does not run during backfill** (backfill is bulk historical evidence; agent reasoning would be expensive and untargeted). Fallback path is what backfill always used. | None | — |
| D3 | [cli.py:716, :758](../../src/tcg_pipeline/cli.py#L716) — `tcg-pipeline detect-contradictions` admin CLI | Calls `detect_contradictions(session, project_ids)` for ad-hoc audit | Same. CLI is for admin/audit purposes — agent does not run. Fallback path. | None | — |
| D4 | [alembic/versions/2026_04_27_0011](../../alembic/versions/2026_04_27_0011_add_override_contradiction_review_items.py) — migration that added `OVERRIDE_CONTRADICTION` enum + columns | Historical, applied. | Untouched. | None | — |
| D5 | [alembic/versions/2026_04_28_0018](../../alembic/versions/2026_04_28_0018_add_review_item_decision_card_columns.py) — added `field_name`, `winning_evidence_id`, etc. | Historical, applied. | Untouched. | None | — |

**Summary for §D.** Audit tooling continues to use `detect_contradictions` directly; the agent does not interpose on backfill or admin-CLI paths.

---

## E. Tests

| # | Path | Coverage | Change required | ⚠ HUMAN REVIEW |
|---|------|----------|-----------------|----------------|
| E1 | [tests/test_contradiction_detection.py](../../tests/test_contradiction_detection.py) — 8 dedicated tests covering create/update/invalidate, baseline-less legacy override, developer legal-suffix noise, registry-alias suppression, unit string vs int equivalence, pipeline_status supporting evidence, stale-item invalidation, skip-staged preservation | All must continue to pass under the agent-led flow when no agent run occurs (fallback). Add a parallel set of tests for agent-led contradiction detection covering the same cases plus reasoning-trace and tool-calls-summary. | — |
| E2 | [tests/test_review_workflow.py](../../tests/test_review_workflow.py) — covers commit paths that branch on `OVERRIDE_CONTRADICTION` | Must continue to pass. Add agent-produced contradiction commit cases. | ⚠ HUMAN REVIEW: confirm scope — `test_review_workflow.py` is large; verify all contradiction-related test functions are identified. |
| E3 | [tests/test_project_override_api.py](../../tests/test_project_override_api.py) — FastAPI override endpoints | Must continue to pass. Override write → re-resolve → detection cycle is unchanged. | — |
| E4 | [tests/test_resolution_cli.py](../../tests/test_resolution_cli.py) — `resolve-all` CLI | Must continue to pass. CLI is fallback-path. | — |
| E5 | [tests/test_backfill_evidence.py](../../tests/test_backfill_evidence.py) — backfill script | Must continue to pass. Backfill is fallback-path. | — |
| E6 | [tests/test_api_scaffold.py](../../tests/test_api_scaffold.py) — references contradiction (verify scope) | Verify current usage. | ⚠ HUMAN REVIEW: confirm that the contradiction reference in this file is incidental (likely test fixtures) and not load-bearing. |

**Summary for §E.** All existing tests must continue to pass. New tests for agent-produced contradictions are part of AGENT.2 scope.

---

## F. Resolved uncertainties and self-verification items

This section was originally "Open Uncertainties" requiring researcher yes/no calls. As of 2026-05-04, all entries below fall into one of three categories: ✅ **Resolved** by a §I researcher decision; 🔍 **Self-verify** — Claude Code will confirm during the rewrite (no researcher input needed); 📌 **Out of scope** — tracked elsewhere or deferred. None are blockers for AGENT.2 starting.

1. ✅ **Resolved by §I.5 — Skip-list semantics in agent path (relates to A5/A6/A7).** The agent runner respects `skip_contradiction_review_item_ids` identically to the deterministic path. The skip-list only prevents same-transaction re-detection; new evidence later still produces a new review item per review-protected override semantics.

2. 🔍 **Self-verify — `review_items` line numbers in §A (A5/A6/A7/A8).** Multiple `resolve_project(apply=True)` call sites in `review_workflow.py`. Claude Code verifies against current HEAD during the rewrite.

3. 🔍 **Self-verify — `/review/queue` endpoint branches on item type.** Claude Code reads the rest of the endpoint during the rewrite to confirm no contradiction-specific branches beyond `_evidence_stance` and what §B/§C enumerate.

4. ✅ **Resolved by §I.1 — `proposed_value` semantics for agent-led contradictions (C9).** Agent-produced contradiction items use `payload.proposed_alternatives: list[...]` (multi-alternative schema); STATUS_CHANGE items keep the single-value `proposed_value`. Renderer handles both shapes.

5. ✅ **Resolved by §I.2 — `evidence_ids` population for agent-led contradictions (C10).** Focused list on display (only directly-contradicting evidence, ~1-3 IDs); wide list (everything the agent consulted) lives in `agent_runs.evidence_consulted[]`, drillable from review detail.

6. ✅ **Resolved by §I.3 — `winning_evidence_id` (C3) under agent reasoning.** Newest evidence (latest `evidence_date`, with `collected_at` as secondary tiebreak) when no single piece dominates. Aligns with today's resolver tiebreak logic.

7. 📌 **Out of scope — `contradiction_priority` column (C5) potentially deprecatable.** Duplicates `priority` enum in string form. Tracked as a follow-on cleanup candidate; not in AGENT.2.

8. ✅ **Resolved by §I.4 — Changes-tab actor label (B11).** Agent-produced contradictions log under distinct `"Agent contradiction detection"` actor, not the same one as today's automatic detection. Reasoning trace continues to be in the detail view.

9. 📌 **Out of scope (deferred to AGENT.5) — Pipedream and CoStar paths (A9/A10).** AGENT.2 does not change these; they continue with deterministic post-resolve detection. AGENT.4 (CoStar) and AGENT.5 (Pipedream) own the source-profile additions for those streams.

10. 🔍 **Self-verify — `test_api_scaffold.py` and `test_review_workflow.py` scope (E2/E6).** Claude Code enumerates full contradiction-related test scope during the rewrite.

---

## G. What's safe to change without further review

These are unambiguous, no-uncertainty changes the agent rewrite can make:

- Add an agent-produced contradiction code path in `detect_project_contradictions`. Existing fallback path continues unchanged.
- Wire the news integrator and permit-collector paths through the agent runner before the post-resolve fallback runs.
- Insert a row into `agent_run_review_items(agent_run_id, review_item_id)` for every contradiction (or other) review item the agent produces. Optionally also write `payload.agent_run_id` as a denormalized rendering hint, but the join table is the source of truth.
- Update Dashboard tile copy ([app/(app)/dashboard/page.tsx:192-193](../../app/(app)/dashboard/page.tsx#L192)) to remove "Phase C contradiction detector" framing once AGENT.2 lands.
- Update Project Detail comment ([app/(app)/pipeline/[projectId]/page.tsx:944](../../app/(app)/pipeline/[projectId]/page.tsx#L944)) to remove stale Phase C reference.
- Add new tests for agent-led contradiction detection (does not require touching existing tests).
- Populate `agent_runs` rows for every agent contradiction decision; query-side join for UI is read-only.

---

## H. Recommended rewrite order

1. Build the agent runner and `agent_runs` table with no contradiction-detection touch (per AGENT.2 sequence). This validates the runner shape before C.i interaction.
2. Add agent contradiction decision branch at integration time, populating `review_items` with the schema in §C identically to today, plus `agent_run_id` in payload.
3. Move news integration to use the agent runner. Permit collector path follows in AGENT.3.
4. Run the existing test suite. Every test in §E must pass.
5. Add new tests for agent-produced contradiction cases.
6. Update the cosmetic UI strings in §G.
7. Verify the §F open uncertainties have been resolved with the researcher before this point.

---

**End of assessment.** Researcher's job: scan all `⚠ HUMAN REVIEW` markers, then read §F open uncertainties. If any of those need direct discussion, raise before AGENT.2 begins. Otherwise: ready to proceed.

---

## I. Researcher decisions on open items (resolved 2026-05-04)

All decisions below are committed and supersede any contrary text earlier in this assessment.

| # | Question | Decision | Implication |
|---|----------|----------|-------------|
| I.1 | When the agent flags a contradiction, what gets recorded as `proposed_value`? (was C9) | **Multi-alternative.** Schema changes from `proposed_value: any` to `proposed_alternatives: list[{value, source_evidence_id, source_summary, agent_confidence}]`. Agent's best guess is first; competing values from different sources follow. | Decision-card UI renders alternatives compactly (creative use of hover for per-alternative source detail). Accept-new flow extends to "pick which alternative." When researcher picks alternative N, the resulting `researcher_overrides` row records the chosen alternative's source so audit shows the picked source (not just generic "user override"). STATUS_CHANGE items keep single `proposed_value` shape — multi-alt is OVERRIDE_CONTRADICTION-only. Adds ~1-2 days to AGENT.2 for UI + schema work; researcher confirmed scope is acceptable. |
| I.2 | How wide is the `evidence_ids` list on a contradiction item? (was C10) | **Focused.** Display list contains only directly-contradicting evidence (~1-3 IDs). Wide list (everything the agent consulted) goes into the new `agent_runs.evidence_consulted[]` field, drillable from review detail. | Decision-card supporting/dissenting render stays clean. Audit trail preserved without UI clutter. |
| I.3 | What's the `winning_evidence_id` tiebreak when multiple pieces of evidence support the agent's decision equally? (was C3) | **Newest.** When no single dominant evidence exists, pick the row with the latest `evidence_date` (with `collected_at` as secondary tiebreak). | Aligns with today's resolver tiebreak logic. Predictable and matches researcher expectations. |
| I.4 | What actor name does an agent-produced contradiction log under in the Changes tab? (was B11) | **Distinct label: "Agent contradiction detection."** Different from today's `"Contradiction detection"` actor used by the deterministic path. | Researchers can scan the Changes tab and immediately see whether a contradiction event was agent-driven or fallback-driven. Reasoning trace continues to be available in the detail view. |
| I.5 | Skip-list confirmation (was A5) | **Confirmed.** Agent runner respects `skip_contradiction_review_item_ids` identically to the deterministic path. The skip-list only prevents re-detection of the *same* contradiction in the *same* commit transaction. | Researcher's caveat — "as long as overrides aren't 'protected' and can change with new evidence" — is already preserved by the existing review-protected override semantics (`EVIDENCE_LAYER_DECISIONS.md` §22). New evidence arriving later still flows through normal contradiction detection and creates a new review item; the skip-list does not make the override sticky. |
| I.6 | Trigger thresholds for unit-count delta agent triggers | **Uniform 10%** across all source profiles (news, permit, future CoStar/Pipedream). Replaces the >50% in earlier design notes. | Likely pushes agent fire rate above the §7 cost-model projection (15-20% → 25-30% expected initially). Cost guardrails (scoped daily caps + kill switches) handle this; first-week monitoring expects higher-than-projected fire rate. Logged as risk R-fire-rate in design doc. |
| I.7 | Global UI date format | **All user-facing dates use `m/d/yy` format.** Project-wide convention, not just AGENT-related. | Recorded in ROADMAP §8 Decision Log as a global UI standard. No separate cleanup item — rolling cleanup as each UI surface is touched (AGENT.2 + future work). New code uses `m/d/yy` from the start. |

**Status of `⚠ HUMAN REVIEW` markers earlier in this assessment:**
- A5 → Resolved by I.5.
- A7 → Claude Code will verify line numbers during the rewrite (no researcher input needed).
- B2 → Claude Code will verify endpoint scope during the rewrite (no researcher input needed).
- B11 → Resolved by I.4.
- C3 → Resolved by I.3.
- C5 → Out of AGENT.2 scope; tracked as future cleanup candidate.
- C9 → Resolved by I.1.
- C10 → Resolved by I.2.
- E2, E6 → Claude Code will enumerate full test scope during the rewrite (no researcher input needed).

**§F Open Uncertainties:** All resolved by §I above or designated for Claude Code self-verification during the rewrite. Cross-references — §F.1 ↔ I.5; §F.2 ↔ self-verify; §F.3 ↔ self-verify; §F.4 ↔ I.1; §F.5 ↔ I.2; §F.6 ↔ I.3; §F.7 ↔ out of scope; §F.8 ↔ I.4; §F.9 ↔ unchanged (Pipedream/CoStar are AGENT.5/AGENT.4, not AGENT.2); §F.10 ↔ self-verify.

AGENT.2 is cleared to proceed.

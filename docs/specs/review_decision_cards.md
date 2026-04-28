# Review Queue: Decision-Card Consolidation

Updated: 2026-04-28

This document is the canonical design reference for `C.tail.11` (backend) and `C.tail.12` (frontend) in [ROADMAP.md](../../ROADMAP.md). It supplements [docs/specs/review_workflow.md](review_workflow.md) §2.3-§2.4 (which already describes the dedup intent we are now implementing).

Read alongside:

- [docs/specs/review_workflow.md](review_workflow.md) — review item generation, payload, dedup invariant, state machine.
- [docs/specs/data_model_changes.md](data_model_changes.md) — review item schema baseline.
- [docs/specs/EVIDENCE_LAYER_DECISIONS.md](EVIDENCE_LAYER_DECISIONS.md) §22 — review-protected override semantics (informs override_contradiction reshape).
- [src/tcg_pipeline/db/collect.py](../../src/tcg_pipeline/db/collect.py) — current per-record ingest path that this work replaces.
- [src/tcg_pipeline/review/contradictions.py](../../src/tcg_pipeline/review/contradictions.py) — contradiction detection that gets reshaped to use the same evidence_ids[] shape.
- [src/tcg_pipeline/review/snippets.py](../../src/tcg_pipeline/review/snippets.py) — server-side per-source snippet renderers.

---

## 1. Problem

The ingest path in [src/tcg_pipeline/db/collect.py:195-223](../../src/tcg_pipeline/db/collect.py#L195-L223) appends one ReviewItem per matched source record. A single LADBS sweep can produce 11+ near-identical "Pending → Under Construction" rows on one project, each with its own action button group. This violates [docs/specs/review_workflow.md](review_workflow.md) §2.4 (one open item per `(project_id, field_name, item_type)`) and gets worse once Phase D news evidence starts landing.

A decision-card UX is the right researcher model: one card per logical decision, with supporting and against evidence collapsible underneath, and a single set of action buttons.

---

## 2. Target shape

### 2.1 Schema additions

Add to `review_items`:

- `field_name` (text, nullable) — promoted out of payload.
- `winning_evidence_id` (uuid, FK to `evidence`, nullable) — the evidence row that drove the resolution rule.
- Partial unique index on `(project_id, field_name, item_type) WHERE state IN ('open', 'staged') AND field_name IS NOT NULL`.

Keep existing columns (nullable, repurposed as "latest representative"):

- `source_run_id` — most recent contributing run.
- `match_confidence` — best supporting match.

In `payload`:

- `evidence_ids[]` — all evidence touching this field for this item.

Defer:

- `proposed_value_hash` — not needed if uniqueness is on the simpler tuple.

### 2.2 Active uniqueness invariant

**One active item per `(project_id, field_name, item_type)`.** When resolution produces a different `proposed_value` for the same tuple, the existing item is invalidated and a new item is created. The partial unique index enforces correctness; the upsert path catches integrity errors and retries on race.

This matches [docs/specs/review_workflow.md](review_workflow.md) §2.4 exactly.

### 2.3 Support / against classification

**Computed at read time, not stored.** Each evidence row's extracted value for `field_name` is normalized using the resolution engine's existing helpers and compared to the item's normalized `proposed_value`:

- Status: enum normalization (e.g. "Permit Issued" supports "Approved" depending on rule).
- Developer: `canonicalize_developer_name` + registry alias suppression.
- Units: `±5` threshold (matches `SMALL_UNIT_DELTA` in [contradictions.py](../../src/tcg_pipeline/review/contradictions.py)).
- Delivery date: `±30` day threshold.
- Other text fields: `normalize_comparable`.

Match → **supporting**. Disagree → **against**. Silent on the field → omit (toggleable via "show all evidence").

Storing membership only (not the categorization) means a rules tweak takes effect without a backfill.

### 2.4 Lifecycle

```
open ─ stage ─ staged ─ commit ─ committed
  │              │
  │              └─ revise → open / staged
  │
  └─ proposal flip → invalidated  (and a new open item is born)
```

When the resolved `proposed_value` for a `(project_id, field_name, item_type)` tuple changes, the existing item transitions to `invalidated` (existing lifecycle state) and a fresh `open` item is created. We never silently mutate `proposed_value` on an open item, because that would change the meaning of any decision a researcher has already staged.

Worked example:

- T1: Article arrives. Resolution: `Pending → UC`. Item created with `proposed_value=UC`. Article in supporting at read time.
- T2: CofO arrives. Resolution: `Pending → Complete` (CofO trumps news). T1 item invalidated. New item created with `proposed_value=Complete`. CofO is winning supporting; article is now in against (because it claimed UC, not Complete). Same evidence pool, re-categorized against the new proposal.

### 2.5 Contradiction items adopt the same shape

`override_contradiction` becomes structurally identical to `status_change` / `field_change`:

- `current_value` = the override value.
- `proposed_value` = the dissenting candidate's value.
- Supporting (read-time) = evidence that agrees with the override.
- Against (read-time) = evidence that contradicts the override.
- `winning_evidence_id` = the strongest dissenting evidence row.

The current contradiction payload tracks `candidate_evidence_ids` (against) but does not track which evidence rows agree with the override (the supporting set). Computing and storing the supporting set is real work in [src/tcg_pipeline/review/contradictions.py](../../src/tcg_pipeline/review/contradictions.py), not just a payload reshape.

### 2.6 Out of scope for this consolidation

Discovery item types are explicitly **not** consolidated:

- `new_candidate` — no project to group by; the decision is "create or dismiss."
- `possible_match` — the proposal *is* "which project does this belong to"; the action set is "bind to candidate X, Y, Z, or create new," not accept/keep/defer.

These keep their current per-record semantics.

---

## 3. UI shape

### 3.1 Queue card

```
┌─ Decision card ───────────────────────────────────┐
│ Pipeline status     [HIGH] [status_change]        │
│ Pending  →  Under Construction                    │
│ rule: inspection_recent_substantive   conf: high  │
│                            [Accept][Keep][Defer][⋮]│
│                                                   │
│ ▶ Supporting (8)                                  │
│ ▶ Against (2)                                     │
└───────────────────────────────────────────────────┘
```

- Single action button group per card.
- Two **independently** collapsible sections (Supporting, Against). Both collapsed by default with counts visible.
- When expanded: top 2-3 evidence rows with inline summary string, plus "view all (N) on detail" link.
- Winning evidence highlighted (star + subtle bg tint) within Supporting.

### 3.2 Detail page

- Same support/against sections, no caps, full server-rendered snippets.
- Custom value form, notes, source URL — unchanged from current.

### 3.3 Snippet rendering strategy

**Server-side only.** Client-side per-source renderers would drift from [src/tcg_pipeline/review/snippets.py](../../src/tcg_pipeline/review/snippets.py). Two pieces:

1. Inline summary string (~80 chars) returned precomputed in the queue API, generated by a new `summary_line()` method on each renderer in `snippets.py`.
2. Full snippet bodies lazy-load on detail-page expand via the existing `GET /evidence/{id}/snippet?field=...` endpoint.

Queue API hydration cap: top 5 supporting + top 5 against per item, plus `total_supporting` / `total_against` counts. Detail page hits a separate endpoint for the full set.

---

## 4. Work breakdown

### 4.1 `C.tail.11` — Backend

1. Alembic migration: add `field_name`, `winning_evidence_id` columns; create the partial unique index. Backfill `field_name` from existing payloads via a deterministic extractor; rows where extraction is ambiguous get null `field_name` and are excluded from the unique index.
2. Modify [src/tcg_pipeline/db/collect.py:195-223](../../src/tcg_pipeline/db/collect.py#L195-L223): upsert against the active `(project_id, field_name, item_type)` tuple for `status_change` and `field_change` only. Append the new evidence ID to `payload.evidence_ids`. Refresh `winning_evidence_id`, `priority`, `flags`, `updated_at`. Catch unique-violation on race and retry against the now-existing row.
3. When resolution produces a different `proposed_value` for the same tuple, invalidate the existing item via the existing lifecycle and insert fresh.
4. Reshape [src/tcg_pipeline/review/contradictions.py](../../src/tcg_pipeline/review/contradictions.py) to compute the supporting-evidence set (currently only tracks the dissenting candidate). Use the same `evidence_ids[]` payload shape so contradiction cards render identically to status_change cards.
5. Discovery items (`new_candidate`, `possible_match`) retain current per-record semantics. No changes.
6. Backfill script `scripts/collapse_duplicate_review_items.py`, dry-run + apply. **Aborts by default** if any duplicate group contains a staged decision. `--migrate-staged` flag picks the staged item as survivor and merges supplanted evidence references into it. Never silently invalidates staged work.
7. Tests:
   - Ingest test: N source rows produce one ReviewItem with N evidence IDs.
   - Proposal-flip test: UC item invalidated, Complete item created with re-categorized evidence pool.
   - Contradiction regression: support set computed correctly, dissent set unchanged.
   - Backfill: dry-run, abort-on-staged, migrate-staged variants.

### 4.2 `C.tail.12` — Frontend

1. Rework `ReviewItemRow` in [app/(app)/review/review-queue-client.tsx](../../app/(app)/review/review-queue-client.tsx) into a decision card. Single action button group. Two independent collapsible sections with counts visible when collapsed.
2. Inline evidence summary strings come precomputed from the queue API. No client-side source-type renderer logic.
3. Highlight `winning_evidence_id` in the Supporting section (star + subtle bg tint).
4. Update [app/(app)/review/[itemId]/page.tsx](../../app/(app)/review/[itemId]/page.tsx) detail view to the same shape with full lists and full server-rendered snippets.
5. Add `supportingEvidenceForItem`, `dissentingEvidenceForItem`, `winningEvidenceForItem` helpers in [lib/review/payload.ts](../../lib/review/payload.ts) with Vitest coverage in [lib/review/payload.test.ts](../../lib/review/payload.test.ts).

---

## 5. Open decisions

1. **Inline summary format.** Server returns a single ~80-char line per evidence row. Defined per `source_type` as a new `summary_line()` method on each renderer in [src/tcg_pipeline/review/snippets.py](../../src/tcg_pipeline/review/snippets.py).
2. **Hydration cap.** Top 5 supporting + top 5 against per item in queue payload, plus totals. Detail page calls a separate endpoint for the full set.
3. **Evidence row interactivity in queue.** Read-only for now. Click-through to project Evidence tab is post-launch.

---

## 6. Risks

- **Migration backfill of `field_name`.** Existing payloads aren't perfectly uniform. The deterministic extractor needs a tested fallback path for ambiguous rows (probably: leave `field_name` null and exclude from the uniqueness invariant). Audit before the migration ships.
- **Concurrency on upsert.** The partial unique index gives correctness; the ingest code must catch integrity errors and retry against the now-existing row.
- **Backfill blast radius.** LA queue is in active use. Run during a quiet window or use `--migrate-staged`. Coordinate with researcher activity.
- **Contradiction payload reshape audit.** Read-side consumers of `current_override` / `candidate` keys need to be greppedbefore the migration ships. No external consumers expected, but verify.

---

## 7. Sequencing

`C.tail.11` ships first. UI keeps rendering one card per item, but there is now one item per decision — queue collapses immediately and researchers see volume relief.

`C.tail.12` ships second. Decision-card UI with support/against sections.

Phase D.5 (article review queue) builds on this. Articles fold into existing items as supporting or against evidence with no extra UI work.

---

## 8. Out of scope

- Bulk multi-select shortcuts.
- Reviewed-tab date-range filters / pagination (`C.tail.7`).
- Auto-stall detection / freshness thresholds (Phase E).
- `proposed_value_hash` column.
- Discovery item consolidation (`new_candidate`, `possible_match`).
- Click-through interactivity on evidence rows in the queue.

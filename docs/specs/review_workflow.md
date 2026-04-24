# Review Workflow — Backend Specification

Updated: 2026-04-24

This document specifies the backend state machine, API surface, and orchestration logic that supports the review queue UI. It is the backend counterpart to `docs/specs/ui_requirements.md` (frontend spec) and depends on the schema in `docs/specs/data_model_changes.md`.

Read alongside:

- `docs/specs/ui_requirements.md` — frontend behavior.
- `docs/specs/data_model_changes.md` — schema.
- `docs/specs/EVIDENCE_LAYER_DECISIONS.md` §22 — review-protected override semantics.
- `src/tcg_pipeline/db/review_workflow.py` — existing review workflow code this spec extends.

---

## 1. Overview

The review workflow coordinates four concerns:

1. **Generating review items** when the resolution engine produces changes that need researcher attention.
2. **Staging decisions** as researchers work through the queue.
3. **Committing decisions** as a transactional batch.
4. **Emitting override contradictions** when new evidence contradicts existing researcher overrides.

The existing `review_workflow.py` handles a subset of this (accept/reject/defer on individual items, evidence relinking on match acceptance). Phase C work expands it into the full batch-commit model with per-user staging and contradiction detection.

---

## 2. Review Item Generation

### 2.1 Triggers

Review items are generated in four contexts:

1. **Post-resolution** — after `resolve_project(project_id)` runs, any field whose resolved value differs from the project's current stored value generates a review item (unless the difference is below threshold per §22.2 of decisions).
2. **Post-ingest** — after new evidence rows land, contradiction detection (see §5) runs against active researcher overrides; any contradiction produces an `override_contradiction` item.
3. **Matcher output** — weak or ambiguous matches produce `possible_match` items (existing behavior).
4. **Source disappearance** — when a Socrata row vanishes across reconciliations (existing behavior in status_rules).

### 2.2 Review item types

The `ReviewItemType` database enum values are lowercase. Existing values remain in place; Phase C adds the new values needed by the review queue.

| Type | Origin | Typical priority |
|---|---|---|
| `status_change` | Resolution engine detects new status | HIGH or MEDIUM |
| `field_change` | Resolution engine detects non-status field change above threshold | MEDIUM or LOW |
| `possible_match` | Matcher returns weak match | MEDIUM |
| `new_candidate` | Matcher returns no match, evidence is strong enough | HIGH or MEDIUM |
| `potential_stall` | Existing stall-risk workflow item | MEDIUM |
| `low_confidence` | Existing low-confidence workflow item | LOW or MEDIUM |
| `override_contradiction` | New evidence contradicts existing override | At minimum MEDIUM (§22.3) |
| `contradiction` | Multiple sources disagree on a field and no winner emerges cleanly | HIGH |
| `unit_split_mismatch` | Total units updated but affordable/market split doesn't sum | MEDIUM |

Review item priorities are computed by rules per type (see §3).

### 2.3 Review item payload

`ReviewItem.payload` stores the structured data the UI needs to render the row and the detail view:

```json
{
  "field_name": "pipeline_status",
  "current_value": "Pending",
  "proposed_value": "Approved",
  "winning_evidence_id": "uuid...",
  "supporting_evidence_ids": ["uuid1", "uuid2"],
  "rule_applied": "highest_status_wins",
  "resolution_confidence": "medium",
  "candidates": [
    {"value": "Approved", "evidence_ids": ["..."], "suggested": true},
    {"value": "Under Construction", "evidence_ids": ["..."]}
  ],
  "flags": ["single_source_tier_3", "canonical_target_polluted"],
  "contradiction_baseline": {...}  // only for override_contradiction
}
```

The UI reads this to render the row, hover evidence, and multi-candidate options.

### 2.4 Deduplication

At most one open review item per `(project_id, field_name, item_type)` combination exists at a time. If the resolution engine re-runs and a review item would be a duplicate of an existing open item, update the existing row (refresh payload, bump timestamp) rather than creating a new one. Prevents queue pollution from repeated resolves.

---

## 3. Priority Computation

### 3.1 Rules per item type

| Item type | HIGH criteria | MEDIUM criteria | LOW criteria |
|---|---|---|---|
| `status_change` | Tier 1 evidence, substantive (permit + inspection, CofO, etc.) | Tier 2 or single-source Tier 1 | Tier 3 only |
| `field_change` (`total_units`) | `abs(delta) > 50` OR multi-source agreement | `abs(delta) > 5` | (doesn't generate — see small-delta policy) |
| `field_change` (`developer`) | Multi-source agreement on non-polluted canonical | Fuzzy review required, or single-source from any tier | Exact-canonical match from Tier 3 alone |
| `field_change` (`date_delivery`) | Article evidence within 6 months contradicts, OR delta > 2 years | Delta 30d–2yr | Delta < 30d |
| `possible_match` | Confidence 0.65–0.84 | Confidence 0.40–0.64 | — (no match below 0.40 generates) |
| `new_candidate` | Strong Tier 1 evidence with complete address | Strong Tier 2 evidence or partial address | — |
| `override_contradiction` | Strong Tier 1 or multi-source agreement | Single-source contradiction (default minimum) | never |
| `contradiction` | Tier 1 sources disagree | Tier 2+ sources disagree | — |

### 3.2 Configuration

Priority rules are centralized in `src/tcg_pipeline/review/priority.py` (or equivalent). Thresholds externalized to YAML where practical for tuning without code changes.

---

## 4. Staged → Committed State Machine

### 4.1 States

Per `data_model_changes.md` §5, review items have a four-state queue lifecycle. Review decisions have their own staged/committed lifecycle, linked to the item.

```
   ┌─ open ───────────────────────┐
   │      (unreviewed)             │
   │                               │
   │  stage accept/keep/custom     │
   │  stage defer                  │
   │  ▼                            │
   │ staged ─────── revise ──┐     │
   │         │               │     │
   │         │ commit         │     │
   │         ▼                ▼     │
   │   committed          open     │
   │                               │
   │  invalidated (by external change)  │
   └───────────────────────────────┘
```

### 4.2 Transitions

| From | To | Trigger |
|---|---|---|
| `open` | `staged` | User stages a decision (Accept / Keep / Defer / Custom). |
| `staged` | `open` | User revises a staged decision back to undecided. |
| `staged` | `staged` | User revises a staged decision to a different choice. |
| `staged` | `committed` | User commits the queue; the decision applies. |
| `open` | `invalidated` | Evidence changes so that the original proposal no longer makes sense (e.g., project was deleted; field was otherwise changed). |
| `staged` | `invalidated` | Same as above; staged decisions are silently dropped if the underlying situation has changed. Notify the user post-commit. |

### 4.3 Per-user scoping

- `review_decisions.staged_by` scopes the decision to one user.
- A `ReviewItem` in state `staged` means *any* user has staged a decision on it. The UI shows it as "Staged by [name]" to other users and the corresponding row is removed from their active queue.
- Only the original stager can revise or unstage.
- An item staged by A and then invalidated by a newer event returns to `open` state, visible to all users again.

### 4.4 Commit operation

**`POST /review/commit`** commits all `staged` decisions for the current user, scoped by jurisdiction if requested.

Request:
```json
{
  "jurisdiction_id": "uuid...",      // optional; commits all if omitted
  "dry_run": false                    // optional; returns the summary without applying
}
```

Processing:

1. Select candidate decisions: `WHERE state = 'staged' AND staged_by = current_user AND decision_type != 'defer' [AND jurisdiction filter]`.
2. Sort decisions by project_id to minimize resolution re-runs.
3. Open a single DB transaction.
4. For each decision:
   - **Accept new**: Write any `researcher_override` only if needed (most accept-new cases leave the project in its auto-resolved state, no override needed). Mark decision `committed`.
   - **Keep old**: Write a `researcher_override` for this field with the current value, author = user, mode = (legacy; see §22.7 — modes retained for audit only). Contradiction baseline = current resolution frontier.
   - **Custom**: Write a `researcher_override` with the user's entered value + note + optional source_url.
   - **Candidate_N**: Same as Accept New but with the user's chosen candidate value if the engine's suggested candidate was not selected.
   - Mark `ReviewItem.state = 'committed'`.
5. After all decisions applied, call `resolve_project(project_id, apply=True)` once per affected project.
6. Write ChangeLog entries per field change.
7. Update `user_jurisdiction_reviews` table (last_committed_at, commit_count, decisions_committed).
8. Commit the DB transaction.

If any step fails: rollback the transaction; staged state preserved; return error with details.

### 4.5 Commit response

```json
{
  "committed_decisions": 32,
  "affected_projects": 28,
  "field_changes_applied": 37,
  "review_items_committed": 32,
  "review_items_remaining": 15,
  "deferred_items": 12,
  "jurisdictions_touched": ["uuid..."],
  "queue_cleared": false,              // true iff all non-deferred items decided
  "duration_ms": 1240
}
```

UI uses this to render the success banner and update queue state.

---

## 5. Contradiction Detection Service

### 5.1 Motivation

Per `EVIDENCE_LAYER_DECISIONS.md` §22, researcher overrides are review-protected. New contradicting evidence generates review items at minimum MEDIUM priority. This requires a detection service that knows when new evidence contradicts an override.

### 5.2 When it runs

Two triggers:

1. **After evidence ingest.** Any collector or backfill that writes new `evidence` rows calls `detect_contradictions(affected_project_ids)` at the end of its run.
2. **After resolution re-run.** `resolve_project` calls contradiction detection for the single project at the end of `apply=True` runs.

### 5.3 Algorithm

```
def detect_contradictions(project_ids: list[UUID]) -> list[ReviewItem]:
    review_items = []
    for project_id in project_ids:
        overrides = active_researcher_overrides(project_id)
        for override in overrides:
            contradicting_evidence = find_contradicting_evidence(
                project_id=project_id,
                field_name=override.field_name,
                override_value=override.value,
                override_set_at=override.set_at,
            )
            if not contradicting_evidence:
                continue
            priority = compute_contradiction_priority(
                field_name=override.field_name,
                evidence_rows=contradicting_evidence,
            )
            existing = find_open_review_item(
                project_id=project_id,
                field_name=override.field_name,
                item_type='override_contradiction',
            )
            if existing:
                existing.payload = build_contradiction_payload(override, contradicting_evidence)
                existing.priority = priority
                existing.updated_at = now()
            else:
                item = create_review_item(
                    project_id=project_id,
                    item_type='override_contradiction',
                    field_name=override.field_name,
                    priority=priority,
                    payload=build_contradiction_payload(override, contradicting_evidence),
                    contradicted_override_id=override.id,
                )
                review_items.append(item)
    return review_items
```

### 5.4 Per-field contradiction rules

Implemented per `EVIDENCE_LAYER_DECISIONS.md` §22.2:

```python
CONTRADICTION_RULES = {
    'pipeline_status':    lambda cur, new: cur != new,
    'total_units':        lambda cur, new: cur != new and abs(cur - new) > 5,
    'affordable_units':   lambda cur, new: cur != new and abs(cur - new) > 5,
    'market_rate_units':  lambda cur, new: cur != new and abs(cur - new) > 5,
    'developer':          lambda cur, new: canonicalize(cur) != canonicalize(new),
    'product_type':       lambda cur, new: cur != new,
    'age_restriction':    lambda cur, new: cur != new,
    'date_delivery':      lambda cur, new, evidence: (
        abs((cur - new).days) > 30 or
        (evidence.source_type == 'news_article' and evidence.evidence_date >= today - 180d)
    ),
}
```

### 5.5 Integration with `resolve_project`

`resolve_project(apply=True)` at the end of its work:

```python
def resolve_project(project_id, apply=True):
    # ... existing resolution logic ...
    if apply:
        commit_resolved_values(project, field_resolutions)
    write_resolution_log(project, field_resolutions)
    if apply:
        detect_contradictions([project_id])
```

---

## 6. API Surface (FastAPI)

Phase B read surfaces use Supabase PostgREST directly with RLS. The FastAPI surface below is the Phase C write/review backend. Read endpoints listed here are optional aggregation endpoints if direct Supabase reads become too awkward for a UI surface; B.1 should not depend on FastAPI being live.

### 6.1 Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/coverage` | List jurisdictions with counts (queue, deferred, last-reviewed, freshness). |
| `GET` | `/coverage/{jurisdiction_id}` | Jurisdiction detail — sources, last runs. |
| `POST` | `/coverage/{jurisdiction_id}/pin` | Pin/unpin a jurisdiction. |
| `POST` | `/coverage/{jurisdiction_id}/scrape` | Kick off scrape for a source (payload: source_name). Returns scrape_job_id. |
| `POST` | `/coverage/{jurisdiction_id}/costar-upload` | Multipart upload for CoStar export. Returns costar_upload_id. |
| `GET` | `/scrape_jobs/{id}` | Status polling for a scrape job. |
| `GET` | `/review/queue` | List review items with state, priority, and user staging. Supports filters. |
| `GET` | `/review/queue/{item_id}` | Review item detail view data. |
| `POST` | `/review/{item_id}/decide` | Stage a decision. Body: `{decision_type, value?, candidate_index?, note?, source_url?}`. |
| `POST` | `/review/{item_id}/revise` | Revise a staged decision. Same body as `/decide`. |
| `POST` | `/review/{item_id}/unstage` | Return a staged decision to open. |
| `POST` | `/review/commit` | Commit staged decisions. Optional jurisdiction filter. |
| `GET` | `/projects/{id}` | Project detail. |
| `POST` | `/projects/{id}/field` | Update a researcher-authored field directly. |
| `POST` | `/projects/{id}/override` | Set a researcher override (equivalent to an inline edit on a Core field). |
| `DELETE` | `/projects/{id}/override/{field}` | Clear an override. Triggers re-resolution. |
| `POST` | `/projects/{id}/note` | Add an append-only note. Body: `{note_type, text}`. |
| `POST` | `/projects/{id}/relationship` | Link relationship. Body: `{relationship_type, target_project_id}`. |
| `POST` | `/projects` | Create new project. Body: `{canonical_address, market_id, jurisdiction_id, ...optional_fields}`. Runs matcher, returns candidates if duplicates. |

### 6.2 Auth

- B.1 auth uses Supabase magic-link email with an approved-email allowlist.
- Unauthenticated browser users redirect to `/login`.
- Supabase JWT in `Authorization: Bearer <token>`.
- API verifies the JWT against Supabase JWKS.
- Extracts user_id; passes to workflow functions as `actor`.

### 6.3 RLS and permissions

- Read endpoints rely on RLS at the Postgres level (authenticated users can read).
- Write endpoints require service-role access to the tables; the API uses a service role connection server-side, authorized by the validated JWT.
- No client-side direct writes: all mutations go through the API.

### 6.4 Rate limiting

- Scrape kickoff: max 1 concurrent job per (user, jurisdiction, source). Additional calls return 429 with "already running."
- Commit: no limit (commits are transactional and safe).

---

## 7. Evidence Snippet Renderers

### 7.1 Purpose

Per `ui_requirements.md` §10.2, every evidence row has a source-type-specific renderer producing human-readable content for hover popovers and detail views. These are backend functions called during review item payload generation and on-demand via a `GET /evidence/{id}/snippet?field={field_name}` endpoint.

### 7.2 Renderer registry

```python
# src/tcg_pipeline/review/snippets.py

SNIPPET_RENDERERS = {
    'ladbs_permit':          render_ladbs_permit_snippet,
    'ladbs_inspection':      render_ladbs_inspection_snippet,
    'ladbs_cofo':            render_ladbs_cofo_snippet,
    'zimas_pdis':            render_zimas_pdis_snippet,
    'zimas_arcgis':          render_zimas_arcgis_snippet,
    'la_case_report':        render_la_case_report_snippet,
    'lahd_affordable':       render_lahd_affordable_snippet,
    'costar':                render_costar_snippet,
    'pipedream':             render_pipedream_snippet,
    'news_article':          render_news_article_snippet,
    'developer_website':     render_developer_website_snippet,
    'researcher_override':   render_override_snippet,
    # fallback
    '*':                     render_generic_snippet,
}

def render_snippet(evidence: Evidence, field_name: str) -> SnippetPayload:
    renderer = SNIPPET_RENDERERS.get(evidence.source_type, SNIPPET_RENDERERS['*'])
    return renderer(evidence, field_name)
```

### 7.3 Output shape

```json
{
  "summary": "pipeline_status: Approved",
  "detail": "Permit PCIS 11010-10000-02451 issued 2013-01-02, current status: Issued.",
  "fields": {
    "field_name": "pipeline_status",
    "extracted_value": "Approved"
  },
  "source_metadata": {
    "source_type": "ladbs_permit",
    "source_tier": 1,
    "collected_at": "2026-04-18T08:00:00Z",
    "evidence_date": "2013-01-02"
  },
  "external_link": "https://...",     // if applicable
  "highlights": []                    // passage highlights for articles, empty otherwise
}
```

### 7.4 News article renderer (Phase D)

Special case: for news articles, `highlights` contains the specific passage that drove the field extraction, with offsets into the article text for rendering.

```json
{
  "summary": "developer: Helio Capital",
  "detail": "BizJournals · 2026-04-08 · Jane Reporter",
  "highlights": [
    {
      "passage": "...construction on the 310-unit Helio project is expected to start Q3 2026 under developer Helio Capital...",
      "field": "developer",
      "value": "Helio Capital",
      "offset_start": 142,
      "offset_end": 158
    }
  ],
  "external_link": "https://bizjournals.com/losangeles/news/..."
}
```

Highlights are stored with the evidence row when the article is extracted (Phase D work), so rendering at UI time is a lookup, not a re-extraction.

---

## 8. Defer Mechanics

### 8.1 Behavior

- Defer is a decision type (`decision_type = 'defer'`), not a separate state.
- Deferred items remain in the queue at the bottom, sorted into a Deferred section.
- Deferred items are **not** included in commit operations — they stay staged indefinitely.
- Deferred count is surfaced per jurisdiction in Coverage.
- Deferred items can be revised to Accept / Keep / Custom later, then included in a subsequent commit.

### 8.2 "Queue cleared" definition

A jurisdiction's queue is `cleared` iff:
- No review items with `state = 'open'` exist for any project in that jurisdiction.
- No review items with `state = 'staged'` and `decision_type = 'defer'` exist.
- All staged non-defer decisions have been committed.

If any deferred items exist, the queue is not cleared; Coverage shows the deferred count as a reminder to return to them.

### 8.3 Deferred refresh on new evidence

When new evidence arrives for a field with a deferred decision, the underlying `ReviewItem` payload is updated (new evidence added to `supporting_evidence_ids`, `proposed_value` may shift if the winner changed). The deferred decision remains valid — the user sees the refreshed context when they eventually return.

If the new evidence *invalidates* the original proposal (e.g., a project was deleted, the field's resolved value now matches the current value), the ReviewItem transitions to `invalidated` and the deferred decision is dropped.

---

## 9. Concurrency

### 9.1 Optimistic last-click-wins

When two users attempt to stage decisions on the same ReviewItem:

1. User A stages first. `review_decisions` row inserted with `staged_by = A`; `review_items.state = 'staged'`.
2. User B attempts to stage a few seconds later. Backend checks state:
   - If `review_items.state = 'staged'` AND no decision exists for user B on this item: return 409 Conflict with payload `{staged_by: "A", decision_type: "accept_new", staged_at: "..."}`.
   - If the state is `staged` but `staged_by = B` already (race with B's own prior stage): accept as revise.
3. UI shows B a banner: "Just decided by A — Accept new. [View]"
4. B can dismiss or click to view A's decision.

### 9.2 Commit conflicts

- User A stages, User B stages a different field on the same project. Both stage successfully.
- User A commits. Project resolution re-runs.
- User B's staged decisions remain staged. If any of them are now invalidated by A's commit (e.g., B staged an Accept for the same field A committed), the item transitions to `invalidated` on re-resolve and B sees it marked as such next time they view the queue.

### 9.3 Jurisdiction claim (informational)

The `/coverage` endpoint returns an `active_reviewers` array per jurisdiction, derived from users whose session recently touched any review item for that jurisdiction. Informational only; not enforced.

---

## 10. Migration from Current `review_workflow.py`

### 10.1 Existing surface

Current `review_workflow.py` exposes:

- `accept_review_item(session, item_id, ...)` — applies the review item immediately.
- `reject_review_item(session, item_id, ...)` — dismisses, creates DismissedRecord.
- `defer_review_item(session, item_id, ...)` — marks for later.

All apply immediately (no staging).

### 10.2 Refactor

Split into two layers:

- **Decision layer** (new): functions that stage decisions — `stage_accept`, `stage_keep_old`, `stage_custom`, `stage_defer`. Write to `review_decisions` with `state = 'staged'`.
- **Commit layer** (new): `commit_staged_decisions(user_id, jurisdiction_id=None)` — walks staged decisions, applies them, handles transactions.
- **Legacy behavior** (deprecated): existing `accept_review_item` / `reject_review_item` immediate-apply functions remain available for CLI / backfill use cases but the API no longer calls them directly.

Existing `possible_match` evidence-relinking logic in `_link_orphan_evidence` remains unchanged and is invoked by the commit layer when a `possible_match` decision applies.

### 10.3 Dismissed records

`DismissedRecord` continues to be written when a `new_candidate` is "keep old" (i.e., rejected). Not changed by this refactor.

---

## 11. Open Questions

1. **Commit atomicity scope.** Current spec: one big transaction per commit. For very large commits (1000+ decisions), this may exceed statement timeouts (currently 5 min) or lock many rows for long periods. Consider chunking commits into batches of N decisions (similar to the `resolve-all` batched runner). Revisit during implementation.
2. **Notification on invalidation.** If user B's staged decision is invalidated by user A's commit, does the UI show a toast immediately, or only next time B opens the queue? For 1-3 users, "next time B opens" is probably fine.
3. **Revert of committed decisions.** ChangeLog supports audit. A proper undo of a committed decision requires: clear the override, re-resolve, write a new ChangeLog entry. Is "undo" a first-class UI action, or only available via support tools? Defer until after C-late.
4. **Scrape job queue infrastructure.** RQ is the leaning recommendation. Alternative: Supabase pg_cron for simplicity. Evaluate during C.a.

---

## 12. Cross-References

- `docs/specs/ui_requirements.md` — frontend flows that consume this API.
- `docs/specs/data_model_changes.md` — schema for `review_items`, `review_decisions`, `researcher_overrides`, etc.
- `docs/specs/EVIDENCE_LAYER_DECISIONS.md` §22 — contradiction thresholds and priority rules.
- `src/tcg_pipeline/db/review_workflow.py` — legacy implementation being refactored.
- `src/tcg_pipeline/resolution/engine.py` — resolution engine that produces review items.
- `ROADMAP.md` Phase C.a — FastAPI architecture decision.

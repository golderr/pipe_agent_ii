# Review Item Human Summary — Plan Brief

> **Purpose:** This brief frames a small but high-leverage UX change: every review item carries a one-sentence, human-friendly summary explaining why it's in the queue and what the reviewer should look for. It is the input to the plan doc (`review_item_human_summary_plan.md`), which the developer writes before any code lands.
>
> **Status:** Decisions in §3 are locked. Plan doc is pending. No implementation until plan is reviewed and approved.

---

## 1. Context: the problem

The current Review Queue surfaces a lot of structured data per item — proposed value, current value, source link, evidence IDs, reason_code, confidence, agent reasoning trace — but a researcher scanning 20-30 items still has to mentally reconstruct "what's the story here?" from those fields.

A one-sentence narrative summary at the top of each review item turns triage from a synthesis task into a glance task. Concretely, instead of seeing:

```
news_status_uncorroborated | pipeline_status | medium priority
proposed: Under Construction | current: Approved
source: urbanize_la article 2be01283-... | reason_code: news_status_uncorroborated_high_quality_permit_jurisdiction
```

the researcher sees:

```
We had this project at Approved. A new Urbanize article says construction has started, 
but LADBS hasn't recorded any inspections yet — verify before promoting to UC.

[structured fields below for drill-down]
```

The structured data is still there. The narrative is what tells the researcher whether to spend 5 seconds dismissing or 5 minutes investigating.

## 2. Goal

Every review item carries a `human_summary` field that:

1. **Reads naturally as a single sentence (or short paragraph, ≤ 3 sentences).** No jargon, no internal field names, no UUIDs. A new researcher should understand it without referencing schema docs.
2. **Answers what the system currently believes, what the new signal says, and what action is recommended.** Not always all three — depends on item type — but never assumes the reader has context.
3. **Is source-anchored.** Mentions the source (Urbanize, LADBS permit, Pipedream snapshot, etc.) and the relevant date or detail. Not generic.
4. **Is generated at item creation time**, not on read. Cached so list views render fast.
5. **Has a sensible fallback when absent** (older items pre-deployment, edge cases where neither path produced one) — falls back to today's title/summary template so the UI never shows blank.

## 3. Locked design decisions

| # | Decision | Locked answer |
|---|---|---|
| 1 | Where the field lives | **`review_items.payload.human_summary`** (string, optional, max ~500 chars). Read by the frontend and any other consumer that wants to render the human-readable form. |
| 2 | Authorship — agent-fired items | **The agent drafts the summary as part of its verdict.** Extend `agent_revised_verdict` schema with a `human_summary` field. The agent prompt includes instructions for what makes a good summary. No incremental LLM cost — it's the same call. |
| 3 | Authorship — integrator-created items (non-agent) | **Deterministic templates per `item_type`.** Each item type has a template function that takes the payload and produces a summary string. Predictable, fast, cheap, easy to tune. Example for `news_status_uncorroborated`: `"Article from {source_name} ({article_date}) suggests this project moved to {proposed_value}. {jurisdiction_policy_reason} — verify before applying."` |
| 4 | Authorship — fallback | **If the agent didn't produce one and no template matches, fall back to the existing `{field_label} changed` title pattern.** No blank fields. |
| 5 | Style constraints for agent-authored summaries | One sentence preferred; up to three short sentences acceptable. Plain English. No internal IDs, no schema field names. Source-anchored. Recommendation-oriented when the recommendation is clear. |
| 6 | Style constraints for template-authored summaries | Same audience, same constraints. Templates use payload fields only (no LLM calls). Test that every template renders without missing-field errors for representative payloads. |
| 7 | Where it surfaces in the UI | Review Queue list view (lead text on each row, replacing or augmenting today's title pattern); Review item detail view (lead paragraph above the structured fields); Activity / Audit Log drill-through (lead text on review-item-linked rows). |
| 8 | Editability | **Not editable in v1.** Once generated, the summary is static. If the researcher disagrees, they can leave a review note or use existing review actions. Editability is a future enhancement. |
| 9 | Migration | **Don't backfill.** Existing review items keep their current rendering. New items get the field. AGENT.reset wipes anyway. |
| 10 | Localization | **English only for v1.** No multi-language support; not a current product requirement. |

## 4. What the plan doc should cover

The plan doc (`docs/specs/review_item_human_summary_plan.md`, 1-2 pages) should address every section below.

### Section 1 — Schema and payload changes

- The `human_summary` field on `review_items.payload` (string, nullable, max length)
- The `human_summary` field on `agent_revised_verdict` (string, nullable, max length)
- No schema migration if both live in JSONB — verify
- Validation rules at the API / writer level (length cap, no HTML, no leading/trailing whitespace)

### Section 2 — Agent prompt changes

What changes in the news_v1 prompt (and eventually permit_v1, regression prompt, future profiles):

- A new instruction block teaching the agent how to write the summary
- Examples of good and bad summaries (1-2 of each)
- Output schema update to include the field
- Constraints: max length, no internal IDs, source-anchored
- Where the instruction sits in the prompt (probably near the verdict schema, so the model produces it as part of the structured output)

### Section 3 — Deterministic template module

A new module (suggested location: `src/tcg_pipeline/review/human_summary.py`) that:

- Registers a template function per `item_type` value in `ReviewItemType`
- Each template takes the review item's payload and returns a string
- Falls through to a default template if no specific one is registered
- Default template: today's `{field_label} changed` pattern, so behavior is unchanged for unregistered types

Plan should list every `item_type` value currently in use and which ones need templates first:

- `new_candidate`
- `possible_match`
- `status_change`
- `news_status_uncorroborated`
- `override_contradiction`
- (Effect 2 future: `status_regression_review`)
- (Other existing types — enumerate in plan)

Decide which to ship in the first slice vs. defer. Recommendation: ship `news_status_uncorroborated`, `new_candidate`, and `possible_match` first since those are the high-volume items the researcher actually sees post-cutover.

### Section 4 — Integration point in review item creation

Where the human_summary gets attached:

- Agent-fired items: pull from `agent_revised_verdict.human_summary` when the review item is created in `_link_orphan_evidence` / decision-card consolidation paths
- Integrator-created items: call the appropriate template function from the human_summary module at the creation call site

Identify every place a review item is created (probably 6-10 call sites across `news/integration.py`, `db/collect.py`, `review/decision_cards.py`, etc.) and confirm the human_summary attachment is wired at each.

### Section 5 — Frontend rendering

- Where the field is read in the Review Queue list view (likely in `app/(app)/review/page.tsx` or a row component)
- Where it's read in the Review item detail view
- Where it's read in Activity / Audit Log drill-through
- Fallback rendering when the field is absent — must not show blank or "null"
- Styling: probably prominent typography, distinct from the structured-field section

### Section 6 — Test coverage

- Agent: news_v1 prompt smoke produces a sensible human_summary in the verdict
- Agent: human_summary respects max length
- Templates: every registered template renders successfully for representative payloads
- Templates: missing-field cases fall through gracefully
- Integration: review items created via news integration have human_summary populated
- Integration: review items created via collect path have human_summary populated
- Frontend: list view renders human_summary when present, falls back when absent
- Activity / Audit Log: drill-through shows human_summary for review-item-linked rows

### Section 7 — Edge cases and risks

- **Agent produces a low-quality or hallucinatory summary.** Validation step? Or accept it as best-effort and let the researcher's note path correct it?
- **Template produces an empty string** (all payload fields missing). Should fall back, not show blank.
- **The summary contains an outdated reference** to a value that subsequently changed (e.g., agent says "currently at Approved" but a higher-priority resolution lands before the researcher reads it). The summary is a snapshot in time; this is acceptable but worth documenting.
- **Decision-card consolidation per C.tail.11** may merge multiple agent runs / multiple evidence rows into one review item. Whose human_summary wins? Most recent? Combined? Decide and justify.
- **Re-rendering the summary later** — once the agent runs and produces a summary, do we ever regenerate it? Probably not; static-at-creation is the simplest contract.

### Section 8 — Open questions

Anything where the locked decisions don't translate cleanly into code, or where you've hit ambiguity. Be specific. Don't invent answers.

### Section 9 — Implementation sequencing

Rough starting proposal:

- Slice 1: Schema field + agent prompt update for `news_v1` + frontend rendering for Review Queue list/detail
- Slice 2: Deterministic template module + templates for `news_status_uncorroborated`, `new_candidate`, `possible_match` + wire into creation paths
- Slice 3: Extend to remaining item types + Activity / Audit Log rendering
- Slice 4: Extend to other agent profiles (permit_v1, future regression prompt) as those profiles activate

Each slice independently testable and shippable.

## 5. How to approach the plan

- **Keep it small.** This is a focused UX change. Resist the urge to expand scope into "also redesign the Review Queue" or "also rewrite the prompt structure." That's separate work.
- **Reuse existing patterns.** Payload extensions, agent verdict extensions, frontend rendering helpers, test fixtures — all known patterns. No new infrastructure needed.
- **Test the templates against real production payloads.** Pull a sample of recent `review_items.payload` rows from each `item_type` and verify the templates render sensibly. (I can query Supabase to give you samples if helpful.)
- **The agent prompt is the highest-leverage change in this slice.** A well-written prompt produces consistently good summaries with zero ongoing maintenance. A poorly-written prompt produces summaries the researcher learns to ignore. Spend prompt-engineering time here.

## 6. Cost discipline

- Agent-authored summaries: **$0 incremental.** Same LLM call, extra field in the structured output. Output token count increases by ~50-100 tokens per call. Negligible.
- Template-authored summaries: **$0.** Pure Python, no LLM.
- No Haiku-cheap fallback layer needed for v1. If we hit cases where neither agent nor template path applies, the existing title pattern is fine as fallback.

## 7. Deliverable

The plan doc, committed to `docs/specs/review_item_human_summary_plan.md`. No code commits in this round. Once submitted, researcher reviews and responds with feedback / approve / requested changes. Approved plan becomes the design reference for implementation slices.

**Timing:** not urgent. Post-cutover, pre-AGENT.reset. Can land in parallel with Effect 2 (regression handling) since the scopes don't overlap — Effect 2's new `status_regression_review` item type will use this brief's human_summary infrastructure.

**Coordination with Effect 2:** when both are in flight, the regression-handling plan should reference this brief's `human_summary` field as a required output of `confirm_regression` / `defer_to_review` verdicts. Easier to commit to here than retrofit later.

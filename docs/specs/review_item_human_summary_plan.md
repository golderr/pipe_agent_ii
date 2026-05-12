# Review Item Human Summary Plan

Purpose: add a cached `human_summary` sentence to every new review item so researchers can see why the item exists and what to inspect without reconstructing the story from raw fields. This plan follows the locked decisions in `review_item_human_summary_brief.md`; implementation waits for review approval.

## 1. Schema and Payload Changes

- Store the rendered text at `review_items.payload.human_summary`. `ReviewItem.payload` is JSONB, so no table migration is needed for the review item field. Keep it optional/nullable for old rows and edge cases, with a writer-level max of 500 characters.
- Extend `agent_revised_verdict` with `human_summary`. `AgentRun.agent_revised_verdict` is already JSONB, so no migration is needed there either. Prompt/schema validation should treat the field as expected for new agent runs but nullable for backward compatibility.
- Add one normalization path in the future `src/tcg_pipeline/review/human_summary.py`: trim leading/trailing whitespace, collapse empty strings to `None`, reject HTML-ish content, and enforce the 500 character cap. If validation fails, review item creation should continue and fall back to a deterministic/default summary instead of failing the intake transaction.
- Do not backfill old rows. The UI helper must render a fallback for missing summaries and must not show blank, `null`, or raw JSON.

## 2. Agent Prompt Changes

The highest-leverage change is in `src/tcg_pipeline/agents/prompts/news_v1/system.md`, near the final output schema and verdict-shape blocks so the model emits the summary as part of `agent_revised_verdict`.

Add a short instruction block:

- Include `human_summary` on every final verdict.
- Prefer one sentence; allow up to three short sentences when needed.
- Plain English for a new researcher. No UUIDs, raw field names, schema terms, or reason codes.
- Source-anchor the sentence with the article/source name and date/detail when available.
- State current belief, new signal, and recommended action when applicable.
- When multiple sources, candidates, or values matter, include a compact decision rationale and a concise lean: `leaning toward X because A and B`. Use the highest-signal evidence available to the agent, such as address alignment, unit-count proximity, product type / rent-or-sale fit, permit date, inspection absence, article recency, or source tier. Include up to 2-3 concrete factors when they materially help the reviewer decide.

Examples to include in the prompt:

- Good: `We had this project at Approved; the April 2026 Urbanize article says construction has started, but LADBS inspections have not corroborated it yet, so verify before promoting to Under Construction.`
- Good: `This article could match either 123 Main or the nearby 125 Main phase; the address and unit count (article says 85, TCG has 83) point more strongly to 123 Main, but confirm before attaching the evidence.`
- Good: `This article could match 123 Main or the 125 Main phase; the address, unit count (85 in the article vs. 83 in TCG), and rental-apartment description all point more strongly to 123 Main, while 125 Main is tracked as for-sale.`
- Good: `Urbanize reports the project is complete, while the latest LADBS permit activity only shows inspections through March; keep the item in review and verify whether a CofO or leasing source supports Complete.`
- Bad: `pipeline_status changed because reason_code=news_status_uncorroborated_high_quality_permit_jurisdiction.`
- Bad: `Review item 2be01283 needs manual handling.`
- Bad: `There are many facts in the article and several possible outcomes; review all evidence carefully.`

Apply the same contract later to `permit_v1` and any future profiles. For Effect 2, the new regression prompt should make `human_summary` a required verdict output for `confirm_regression` and `defer_to_review` verdicts. This adds no incremental LLM call; it is the same agent response with about 50-100 more output tokens.

## 3. Deterministic Template Module

Create `src/tcg_pipeline/review/human_summary.py` as the single Python module for summary normalization and templates. It should expose a registry keyed by `ReviewItemType`, plus a default function equivalent to today's `{field_label} changed` title pattern. Template functions take the review item payload, and optionally known `item_type`/`field_name`, and return a normalized string or `None`.

Authorship precedence at creation time:

1. If an agent ran and `agent_revised_verdict.human_summary` is valid, use it.
2. Else, if a registered template exists for the item type, use that.
3. Else, use the default `{field_label} changed` fallback.

Current and planned item types:

| Item type | First-slice treatment |
|---|---|
| `new_candidate` | Explicit template in the first template slice. |
| `possible_match` | Explicit template in the first template slice. |
| `news_status_uncorroborated` | Explicit template in the first template slice. |
| `status_change` | Default first, explicit template in the next slice. |
| `override_contradiction` | Default first, explicit template after agent/override payloads are sampled. |
| `multi_tenure_review` | Default first, explicit semantic-template follow-up. |
| `project_cancellation_review` | Default first, explicit semantic-template follow-up. |
| `potential_stall` | Enum exists; no direct creator found in current code search. Default until active. |
| `low_confidence` | Enum exists; no direct creator found in current code search. Default until active. |
| `status_regression_review` | Effect 2 future type; explicit template and agent verdict summary required when that type lands. |

Representative template examples:

- `news_status_uncorroborated`: `Article from {source_name} ({article_date}) suggests {field_label} should move from {current_value} to {proposed_value}, but the jurisdiction policy still needs corroboration; verify before applying.`
- `new_candidate`: `{source_name} reported a project at {canonical_address}; no existing project matched confidently, so review whether to create a new candidate.`
- `possible_match`: `{source_name} reported {canonical_address}; the matcher found possible existing projects, and {best_match_basis} points most strongly to {candidate_label}, so confirm the right match before attaching the evidence.`

## 4. Creation Integration Points

Wire summary attachment at every review-item creation path, using a helper that returns a payload copy with `human_summary` set only when absent or invalid.

- `src/tcg_pipeline/news/integration.py`
  - `_upsert_discovery_review_item`: news `new_candidate` and `possible_match`.
  - `_upsert_status_change_review_items`: confirmed news field/status decision cards.
  - `_upsert_agent_override_contradiction_review_items`: agent-reviewed override contradictions; prefer agent-authored summaries.
  - `_upsert_agent_structural_conflict_review_items`: agent-escalated structural conflicts.
  - `_upsert_semantic_review_items` / `_upsert_semantic_review_item`: `news_status_uncorroborated`, `multi_tenure_review`, and `project_cancellation_review`.
- `src/tcg_pipeline/db/collect.py`
  - `_create_unmatched_review_item`: collector-created `new_candidate` and `possible_match`.
  - `_upsert_status_change_review_items`: collector-created status/field decision cards.
- `src/tcg_pipeline/review/decision_cards.py`
  - `upsert_decision_card_review_item`: preserve an existing valid `human_summary` when refreshing/consolidating an active item. If the existing item lacks one and the incoming payload has one, fill it.
- `src/tcg_pipeline/review/contradictions.py`
  - `detect_project_contradictions`: deterministic `override_contradiction` path via decision cards.
- `src/tcg_pipeline/db/review_workflow.py`
  - `_create_follow_up_review_item`: post-accept follow-up `status_change` items.

Decision-card consolidation rule: first valid summary wins. Later merged evidence should update structured payload/evidence IDs but should not rewrite the lead sentence unless the existing item had no summary. This keeps the summary static at creation time and avoids changing the meaning of an item while a researcher may be viewing or staging it.

## 5. Frontend Rendering

Add a helper in `lib/review/payload.ts`, for example `humanSummaryForItem(item)`, that reads `payload.human_summary` and falls back to `{fieldLabel(fieldNameForItem(item))} changed`.

- Review Queue list: render the summary as lead text in `app/(app)/review/review-queue-client.tsx`, above the current/proposed value blocks and below the item chips.
- Review item detail: render the summary as a lead paragraph near the page heading or at the top of Decision Context in `app/(app)/review/[itemId]/review-item-detail-client.tsx`.
- Activity / Audit Log: extend the API response to include review-item summaries for rows with `review_item_id` or `review_item_ids`, then render them in `app/(app)/activity/page.tsx` near the review-item links. Project detail audit rows should use the same API/read helper if they are in scope for v1.
- Hide `human_summary` from the generic flattened payload table after rendering it as the lead text, to avoid duplication.

## 6. Test Coverage

- Agent prompt smoke: `news_v1` fixture output includes a source-anchored `agent_revised_verdict.human_summary`.
- Agent validation: overlong, blank, or HTML-like summaries are rejected or normalized and fall back without failing the run.
- Templates: every registered template renders for representative payloads.
- Templates: missing fields produce the default title-pattern fallback.
- Integration: news `new_candidate`, `possible_match`, and `news_status_uncorroborated` items receive `payload.human_summary`.
- Integration: collect-path `new_candidate` / `possible_match` and status-change items receive a summary.
- Decision cards: refresh/consolidation preserves the first valid summary and fills a missing one.
- Frontend: list and detail render `human_summary` when present and fallback text when absent.
- Activity / Audit Log: review-item-linked rows render the summary and do not show blank text.

## 7. Edge Cases and Risks

- Low-quality agent summary: validate shape only, not truth. Prompt examples and smoke tests carry quality; researchers can still leave notes or reject the item.
- Hallucinated agent detail: summaries must be grounded in intake/tool results. If the field is invalid or missing, fall back to templates rather than blocking.
- Overlong mini-memos: richer summaries are useful only when they guide the decision. Allow up to 2-3 concrete reasons for a lean when they are genuinely discriminating, but avoid listing every source, candidate, or raw payload field.
- Empty template output: treat as missing and use the default title-pattern fallback.
- Outdated snapshot: the summary is static and reflects creation-time context. This is acceptable and should be documented in code comments/tests where consolidation is handled.
- Production payload variance: before tuning explicit templates beyond the first slice, sample recent `review_items.payload` by item type so templates use fields that are actually present.
- Cost: agent-authored summaries are the same LLM call; template summaries are pure Python. No fallback LLM layer.

## 8. Open Questions

- Does "Activity / Audit Log drill-through" include the project-detail audit table in `app/(app)/pipeline/[projectId]/page.tsx` for v1, or only `/activity`? Recommendation: include it if the existing data shape can be reused cheaply; otherwise ship `/activity` first and keep project-detail audit as a follow-up.
- `potential_stall` and `low_confidence` are in the enum but no active direct creation call sites showed up in current code search. Confirm there are no external writers before deciding whether they need explicit v1 templates.
- For semantic review items that aggregate multiple article IDs, the plan keeps the first valid summary. If product wants summaries to mention growing article counts, that would require deliberate regeneration and should be a later enhancement.

## 9. Implementation Sequencing

1. Schema/prompt slice: add normalization helper, extend `news_v1` verdict instructions/schema, add agent tests, and render list/detail with fallback. This is independently shippable because old items still render.
2. High-volume template slice: implement explicit templates for `news_status_uncorroborated`, `new_candidate`, and `possible_match`; wire news and collect creation paths.
3. Completion slice: wire remaining creation paths, add explicit templates for `status_change`, `override_contradiction`, `multi_tenure_review`, and `project_cancellation_review`, and finish Activity / Audit Log rendering.
4. Coordination slice: extend `permit_v1` when permit-agent summaries become useful, and require `human_summary` on Effect 2 `status_regression_review` verdicts/templates when that item type lands.

No code implementation should start until this plan is reviewed and approved.

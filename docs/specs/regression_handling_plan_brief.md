# Regression Handling Redesign — Plan Brief

> **Purpose:** This brief frames the regression-handling redesign and captures the locked design decisions that the implementation plan should be built against. It is the input to the plan doc (`regression_handling_plan.md`), which the developer writes before any code lands.
>
> **Status:** Decisions in §3 are locked. Plan doc is pending. No implementation until plan is reviewed and approved.
>
> **Sibling slice:** Effect 1 (terminal-state suppression of `news_status_uncorroborated` review items on Complete / Pre-Leasing-Pre-Selling projects) is being implemented separately. This brief is Effect 2 — the broader regression-handling redesign.

---

## 1. Context: what the system does today

The status resolver in [src/tcg_pipeline/resolution/fields/status.py](../../src/tcg_pipeline/resolution/fields/status.py) enforces strict forward-only progression. When any evidence suggests a status that ranks LOWER than the project's current status in `STATUS_PROGRESS_ORDER`:

- The resolver discards the regression candidate
- `rule_applied="forward_only_preserve_current"` is written to `resolution_log` with the discarded candidate in metadata
- **No review item is created**
- The researcher never sees the signal

The current order:

```
Conceptual(0) → Proposed(1) → Pending(2) → Approved(3) → Under Construction(4) → Pre-Leasing/Pre-Selling(5) → Complete(6)
```

The only carve-out today: transitions to `Stalled` or `Inactive` (`MANUAL_REVIEW_STATUSES`) go through `_requires_manual_status_review` and DO create review items. Everything else gets silently dropped.

## 2. Why this matters

Real-world project lifecycles aren't actually forward-only. Cases the current system silently discards:

- **Descope after entitlement** — Project approved for 200 units, developer reverts to planning for 120 units → Approved → Proposed
- **Entitlement expiration** — Project Approved, entitlements expire without permits being pulled → Approved → Proposed or Conceptual
- **Funding fallthrough at construction** — Project breaks ground, financing collapses, work halts → UC → Approved (paused) or Stalled
- **Cancellation and revival** — Project Approved → cancelled → revived years later. Today only the "cancelled" half can be captured (via Stalled); the revival is fine because it's forward.
- **Press correction** — News reports "construction has begun" → later news clarifies "groundbreaking was symbolic, work hasn't started" → UC → Approved
- **Pre-leasing/pre-selling reversion** (rarer, higher-stakes) — Construction defects discovered during pre-leasing inspection → work resumes → Pre-Leasing → UC; Pre-selling withdrawn due to legal issues → Pre-Selling → Approved

Each is a legitimate signal the current resolver throws on the floor. The researcher only finds out via manual review or external knowledge.

## 3. Locked design decisions

These are decided. The plan should be built around them, not re-litigate them. If a decision genuinely seems wrong, surface it as an open question in §6 of the plan doc with reasoning — but the default is "build to these."

| # | Decision | Locked answer |
|---|---|---|
| 1 | Which signal sources trigger regression review? | **Any source.** News, LADBS, Pipedream, CoStar — all eligible. Source tier informs the agent's confidence but doesn't gate the trigger. |
| 2 | Review item shape | **New `status_regression_review` item type.** Differentiates from forward-status items in UX; plays cleanly with C.tail.11 decision-card consolidation; allows per-type filtering in Review Queue / Activity / smoke reports. |
| 3 | Review item payload shape | Mirror `status_change` plus regression-specific fields: `proposed_value`, `current_value`, `candidate_evidence_ids`, `reason_code`, `source_anchors`, `agent_run_id`, `agent_recommendation`, `review_action_taken`. |
| 4 | Threshold for auto-apply | **Agent decides; no hardcoded confidence rule.** Resolver detects candidate → agent runs → returns `confirm_regression` / `defer_to_review` / `dismiss` with its own confidence. Auto-apply floor is `confirm_regression` at confidence ≥0.9 from rank ≤4, ≥0.95 from rank ≥5. |
| 5 | Agent involvement | **New trigger: `status_regression_candidate`.** Fires from resolver/integrator across all sources. Agent uses existing tools (`get_project_state`, `get_permits_for_project`, `get_articles_about_parcel_or_address`, etc.) to verify the signal. Separate trigger from `material_contradiction` — that's about attribution; this is about post-attribution direction. |
| 6 | Pre-Leasing/Pre-Selling handling | **Not terminal — high threshold.** Agent prompt encodes that regressions from rank ≥5 require stronger corroboration. Default to `defer_to_review` more often; auto-apply requires confidence ≥0.95. |
| 7 | Complete handling | **Terminal.** Effect 1's terminal-state suppression handles `news_status_uncorroborated` items. For Effect 2: when current rank == 6 (Complete) and proposed rank < 6, drop the candidate at the integrator level — don't fire the regression agent trigger. Log to `resolution_log` for audit but skip review item creation entirely. |
| 8 | Verdict shape for the agent | Extend `agent_revised_verdict` with: `decision` (`confirm_regression` / `defer_to_review` / `dismiss`), `confidence`, `proposed_status`, `current_status`, `regression_reason_code`, `corroborating_evidence_ids`, `reason`. Maintains parity with existing agent run audit pattern. |
| 9 | Migration of pre-existing silently-dropped regressions | **Don't bother.** AGENT.reset wipes data; only catch new regressions going forward. |
| 10 | Kill switch | **Not needed.** "Old path" is silent_drop, which we don't want to fall back to. Failure modes are recoverable through researcher review queue dismissal. Pre-reset, data is wipeable. Don't proliferate operational levers for behavioral changes with recoverable failure modes. |

## 4. Goal

A regression handling system that:

1. **Doesn't silently lose signal.** Every regression candidate either gets the researcher's attention, gets auto-applied with audit trail, or gets explicitly dismissed by the agent with reasoning logged.
2. **Differentiates signal strength via the agent, not hardcoded confidence numbers.** The agent reads the source content, cross-references corroborating evidence via existing tools, and decides whether the signal is strong enough to act on.
3. **Respects Complete as truly terminal.** A project with a CofO genuinely cannot un-complete. Any earlier-status signal on a Complete project is dropped entirely.
4. **Treats Pre-Leasing/Pre-Selling as "high threshold" but not terminal.** Real regressions DO happen there (defects, presale issues, litigation), but the public commitment of being in those states means the bar for auto-applying or surfacing a review item should be higher.
5. **Integrates cleanly with the existing AGENT.2 trigger architecture and `agent_run_review_items` linkage pattern.** Don't build a parallel audit shape; reuse `agent_revised_verdict` + agent_run_review_items.

## 5. What the plan doc should cover

The plan doc (`docs/specs/regression_handling_plan.md`, 2-4 pages) should address every section below. Each section should be concrete enough that an implementer can build to it without further clarification.

### Section 1 — Current behavior baseline

Restate what the resolver does today (including the Stalled/Inactive carve-out), confirming the silent-drop default. Cite specific code locations. Make sure we're aligned on the starting point before describing changes.

### Section 2 — Proposed integration flow

Walk through what happens end-to-end for each regression scenario. Specifically:

- News article reports "construction has paused" for project currently at UC
- Pipedream snapshot shows lower status than current resolved value
- LADBS permit cancellation event for a project currently at UC
- News article reports "developer cancels project" for project at Approved
- Pre-leasing project hits litigation, news article reports leasing halted

For each: where the regression candidate gets detected, what triggers fire, how the agent decides, what review item gets created (or doesn't), what the resolver writes.

### Section 3 — Resolver changes

What changes in [src/tcg_pipeline/resolution/fields/status.py](../../src/tcg_pipeline/resolution/fields/status.py):

- The `forward_only_preserve_current` path is no longer the silent end. It should either:
  - (a) Continue to preserve current status, but also fire the regression trigger to the agent for deliberation, OR
  - (b) Wait for agent verdict before deciding what to preserve

  Pick one and justify. Researcher's instinct is (a) — preserve current status pending agent verdict, then auto-apply (if agent says confirm at high enough confidence) at the next resolve cycle. Cleaner separation; resolver isn't blocked on agent.

- The Complete-as-terminal carve-out (per Decision 7) — short-circuit before the agent runs for rank 6 → rank <6.

- The existing Stalled/Inactive carve-out — does it stay separate, or merge into the new regression-trigger path? Surface your read with reasoning.

### Section 4 — Integrator changes

What changes in [src/tcg_pipeline/news/integration.py](../../src/tcg_pipeline/news/integration.py) and equivalent code paths in the LADBS / Pipedream sides:

- How the regression trigger gets fired (which call site, what payload)
- Interaction with Effect 1's terminal-state suppression — Effect 1's suppression at rank ≥5 with `news_status_uncorroborated` is specifically about the jurisdiction-policy gate; Effect 2's regression handling is broader. Make sure they don't conflict or double-fire.
- How `news_use_legacy_pass3` / `news_use_legacy_semantic` flags interact with the new regression path

### Section 5 — Agent profile changes

What changes in `src/tcg_pipeline/agents/profiles.py` and the news/permit prompt files:

- Add `status_regression_candidate` to `AgentTrigger`
- Add to `NEWS_AGENT_PROFILE.triggers` (and eventually `PERMIT_AGENT_PROFILE.triggers`)
- Prompt content for the new trigger — how the agent should reason about regression signal strength, what tools to use, how to weight Tier 1 vs Tier 2 evidence, the rank-≥5 elevated threshold rule

### Section 6 — Review item type + payload schema

- The new `status_regression_review` item type — where it lives in the enum, how it surfaces in Review Queue UI, what filters apply
- Payload schema (concrete JSON structure with all field types)
- Priority logic — when is a regression `high` priority vs `medium` vs `low`? Suggested baseline: high if rank delta ≥2 (UC → Conceptual), medium if rank delta == 1 (UC → Approved), and agent's confidence factors in.
- Auto-apply mechanics — when the agent says `confirm_regression` at confidence above threshold, does the resolver auto-apply on the next resolve cycle? Does the review item get created in `committed` state (audit-only)? Or in `open` state with a "review and confirm or revert" affordance?

### Section 7 — Activity / Audit Log + smoke report integration

- Activity / Audit Log view should display regression decisions distinctly from forward-status changes
- News-agent smoke report should include regression trigger counts and outcomes in its summary
- The `agent_run_review_items` linkage must populate cleanly so audit trail works
- Coverage Review Queue may need a regression-specific filter preset

### Section 8 — Test coverage plan

Concrete test scenarios — list them out as test names. At minimum:

- Resolver: regression candidate from any source → trigger fires
- Resolver: Complete + earlier signal → silent drop, no trigger
- Agent: news regression confirmed at high confidence → auto-apply review item created
- Agent: news regression confirmed at medium confidence → defer_to_review review item created
- Agent: news regression dismissed → resolution_log entry, no review item
- Agent: Pre-Leasing regression → elevated threshold applied; default to defer
- Stalled/Inactive carve-out: still works after change
- Forward progression: existing behavior unchanged
- Integration: Pass 2c truncation + regression candidate — what happens? (Edge case worth a test)
- Multi-source: Pipedream regression + corroborating news → agent confidence boosts → auto-apply
- Agent + Effect 1 terminal-state suppression interaction — no double-fire, no missed signal

### Section 9 — Edge cases and risks

This is the judgment section. Specifically anticipate and address:

- **Agent regression trigger fires for the same project on every cron run** when a stale news article keeps producing the same regression signal. How do we deduplicate? (Decision-card consolidation per C.tail.11 may handle this, but verify.)
- **Race conditions** between resolver writing `forward_only_preserve_current` and agent later writing a confirmed regression. Does the resolution sequence handle this cleanly?
- **Agent cost** of the new trigger — at LA cron volume, how many regression triggers do we expect to fire per day? Run a back-of-envelope estimate against the news bucket cost cap.
- **Agent prompt complexity creep** — the news prompt is already large. Adding regression reasoning makes it larger. Is the right shape "extend the news prompt" or "separate prompt for regression"? Surface your call with reasoning.
- **Agent vs. deterministic LADBS evidence conflict** — agent confirms regression based on news, but a fresh LADBS inspection (UC evidence) lands on the same project shortly after. Resolution conflict — who wins?
- **Researcher override interaction** — if a researcher has an active override on `pipeline_status`, does the regression trigger fire? Auto-apply? Create review item that overrides the override?
- **`agent_revised_verdict` shape extension** — make sure adding the new fields doesn't break existing news-agent code paths or Activity / Audit Log rendering

### Section 10 — Open questions

List anything where the locked decisions above don't translate cleanly into code, or where you've hit ambiguity. Be specific. Don't invent answers — surface them for review.

### Section 11 — Implementation sequencing

Once the plan is approved, what's the order of slices? Rough starting proposal:

- Slice 1: Add `status_regression_candidate` trigger + agent profile changes + regression prompt
- Slice 2: Resolver changes — fire trigger, handle agent verdict, auto-apply logic
- Slice 3: New review item type + payload + Review Queue UI rendering
- Slice 4: Activity / Audit Log integration + smoke report extensions
- Slice 5: LADBS / Pipedream integrator changes (news first since news is the cutover surface)

Each slice independently testable and shippable. Surface dependencies between them.

## 6. How to approach the plan

- **Don't re-litigate the locked decisions.** They went through researcher review. If genuinely wrong, flag in §10 with reasoning — but default is "build to these."
- **Anticipate the failure modes.** The current `forward_only_preserve_current` rule was probably written this way because regressions felt risky to surface. The new system inherits that risk if it generates noise or misfires. §9 is where you prove you've thought about how this fails, not just how it succeeds.
- **Reuse existing patterns.** The AGENT.2 trigger architecture, `agent_revised_verdict` shape, `agent_run_review_items` linkage, decision-card consolidation, kill-switch posture — these are known patterns. Don't invent new ones unless the existing ones genuinely don't fit.
- **Pre-reset is the window.** Lands before AGENT.reset's first stabilization cycle. Data generated during testing is throwaway. Design the right thing, not the safe-by-default thing.
- **Cost discipline.** Estimate the daily cost impact at production cadence (5-10 Urbanize articles/day, expected regression candidate rate, agent run cost ~$0.07-0.15 each). If "$5/day extra," fine. If "$50/day extra," surface that as a §9 concern — we may need a deterministic pre-filter.

## 7. Deliverable

The plan doc, committed to `docs/specs/regression_handling_plan.md`. No code commits in this round. Once submitted, researcher reviews and responds with feedback / approve / requested changes. Approved plan becomes the design reference for implementation slices.

**Timing:** not urgent. Post-cutover, pre-AGENT.reset. A good plan saves three bad slices.

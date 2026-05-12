# Regression Handling Redesign Plan

Purpose: implement Effect 2 from `regression_handling_plan_brief.md`: regression candidates should no longer disappear behind `forward_only_preserve_current`. This is a plan only. No implementation should start until reviewed and approved.

## 1. Current Behavior Baseline

`src/tcg_pipeline/resolution/fields/status.py` currently resolves `pipeline_status` by collecting explicit status observations, adding direct structured status evidence (`building_permit_issued`, `building_inspection_recorded`, `certificate_of_occupancy_issued`), and selecting the highest ranked status in `STATUS_PROGRESS_ORDER`.

If the selected status is `Stalled` or `Inactive`, `_requires_manual_status_review` preserves the current status and returns `rule_applied="manual_status_review_preserve_current"` with review metadata. Those transitions are already manual-review-only and should remain separate.

If the selected ranked status is lower than the current project status, `resolve_status` preserves the current value and returns `rule_applied="forward_only_preserve_current"` with metadata containing `candidate_status`, `evidence_type`, `source_type`, and `requires_review=False`. That is the silent-drop behavior Effect 2 changes.

One important code detail: `src/tcg_pipeline/resolution/engine.py::resolve_project` writes `resolution_log` rows only when `resolution.value` differs from the current project value. A preserved regression usually does not produce a persisted `resolution_log` row today, despite the resolver returning regression metadata. Effect 2 should explicitly add audit logging for preserved regression candidates, including Complete-terminal drops.

News has an additional pre-agent path in `src/tcg_pipeline/news/integration.py`: `_material_status_regression` currently folds a status regression into the broad `material_contradiction` trigger. Effect 2 should split that into the new `status_regression_candidate` trigger so attribution contradictions and lifecycle regressions are not conflated.

## 2. Proposed Integration Flow

Common flow:

1. Source ingestion writes evidence as it does today.
2. The status resolver emits ranked regression candidate metadata when a non-terminal project has lower-status evidence.
3. If current status is `Complete` and proposed status is lower, the integrator drops the regression candidate before agent routing. It still preserves evidence and writes an audit-only `resolution_log` entry.
4. Otherwise, the source integration path calls the agent runner with trigger `status_regression_candidate`.
5. The agent returns `confirm_regression`, `defer_to_review`, or `dismiss`.
6. `confirm_regression` above threshold creates a committed audit review item and applies the regression through existing review/override mechanics. `defer_to_review` or below-threshold confirmation creates an open `status_regression_review`. `dismiss` creates no review item but leaves the `agent_runs` row and resolution audit trail.

Scenario handling:

- News article says construction has paused for a UC project: Pass 2c must provide a valid ranked lower-status signal or explicit regression reason. The news integrator writes evidence, routes `status_regression_candidate`, and the agent checks project state, article body, similar articles, and permits. If Pass 2c emits `Stalled`/`Inactive`, the existing manual-review/cancellation path stays separate.
- Pipedream snapshot shows a lower status than current: structured ingest writes the Pipedream evidence, resolver emits a regression candidate, and the structured-source integration path creates a direct `status_regression_review` with `agent_recommendation=null` until an AGENT.5 Pipedream profile exists. The review item uses the Pipedream evidence as `candidate_evidence_ids`.
- LADBS permit cancellation event for a UC project: if LADBS deterministic evidence maps to `Inactive`/`Stalled`, keep the manual-review carve-out. If it maps to a ranked lower status such as `Approved`, route `status_regression_candidate` through `permit_v1`.
- News article says developer cancels an Approved project: keep `project_cancellation_review` from the semantic reason-code path. Do not convert this to `status_regression_review`.
- Pre-Leasing project hits litigation and news reports leasing halted: because Pre-Leasing/Pre-Selling is not terminal, route a regression candidate if the semantic output supports a lower ranked status. The prompt and auto-apply gate require the higher threshold (`>=0.95`), so most cases should defer to review.

Effect 1 interaction: `news_status_uncorroborated` suppression is only for ambiguous high-quality-jurisdiction UC signals. Do not create `status_regression_candidate` from that reason code alone, or Pre-Leasing projects would reintroduce the queue noise Effect 1 removed. If the same article or another reference in the same article carries a separate regression signal, that independent signal still routes through Effect 2.

## 3. Resolver Changes

Choose option (a): preserve current status pending agent verdict. The resolver should not block on the agent. It should continue returning the current status, but expose regression candidates so integrators can route the agent and review workflow after the transaction has evidence and source context.

Proposed changes:

- Add a small candidate shape in the status resolution layer, either as a dataclass converted into `FieldResolution.metadata["regression_candidates"]` or as a field on `ProjectResolutionResult`. It should include `current_status`, `proposed_status`, `current_rank`, `proposed_rank`, `rank_delta`, `evidence_ids`, `source_type`, `evidence_type`, `evidence_date`, and any source semantic reason code available from evidence.
- Emit one raw regression candidate per lower-ranked observation, not just one candidate from the highest lower-ranked status. This matters because the resolver's "highest status wins" can hide a fresh lower-status evidence row when older UC/Complete evidence still exists.
- Integrators may merge raw candidates by `(project_id, current_status, proposed_status)` before routing, but the merged payload must preserve all candidate evidence IDs, source types, source anchors, and reason codes. Each source family still owns its own route: news evidence goes through `news_v1`, LADBS evidence goes through `permit_v1`, and Pipedream/CoStar use direct review items until their profiles exist.
- Preserve current status in all regression cases until a committed human/system decision creates an override or otherwise authorizes the regression.
- Add audit logging for preserved regression candidates even when the resolved value equals the current value. The cleanest option is a nullable `metadata` JSONB column on `resolution_log`; without that, the candidate status must be inferred from evidence rows, which is weaker.
- Complete terminal rule: if current rank is 6 and proposed rank is lower, mark the candidate as terminal-dropped and do not route the agent. Persist a `resolution_log` row with `rule_applied="terminal_regression_dropped"` and candidate metadata.
- Keep Stalled/Inactive separate. They are not ranked lifecycle regressions; they remain manual-review-only through the existing carve-out and semantic cancellation templates.

## 4. Integrator Changes

News:

- Split `_material_status_regression` out of `material_contradiction`. Unit and developer contradictions remain `material_contradiction`; ranked status regressions become `status_regression_candidate`.
- Build the agent intake from the matched project, reference, current/proposed status, semantic reason code, source anchors, article IDs, extraction IDs, and evidence IDs.
- Do not route regression candidates when Pass 2c is unavailable. The current Pass 2c suppression path correctly prevents raw Pass 2b status from bypassing policy. A parse alert is the visibility mechanism.
- Legacy flags: if `news_use_legacy_pass3=true`, do not fire `status_regression_candidate`. Legacy Pass 3a is rollback re-extraction, not the agent trigger path. If `news_use_legacy_semantic=true`, regression handling is degraded: without Pass 2c semantic output, do not route semantic regression candidates. Raw legacy evidence can still land, but it should not create the new regression review item until default semantic mode is restored.

Structured source paths:

- In `src/tcg_pipeline/db/collect.py`, after evidence write and `resolve_project`, inspect the status regression candidate metadata for LADBS and future collector-backed structured sources.
- For LADBS, route through `permit_v1` once `status_regression_candidate` is added to that profile.
- CoStar upload/seed paths use the same shared review-card helper after their `resolve_project` call. CoStar regressions create direct low-priority `status_regression_review` items with deterministic narrative copy and `agent_recommendation=null`.
- Pipedream should use the shared direct-review helper once AGENT.5 / the Pipedream sync path updates existing projects. The current seed importer skips existing `tcg_pipedream_id` rows, so Pipedream production activation is deferred rather than pretending coverage exists.
- Pipedream and CoStar should not use a catch-all source-agnostic agent profile. Until AGENT.4/5 add source profiles, structured Pipedream/CoStar regressions should create direct `status_regression_review` items with `agent_recommendation=null`. That preserves the "any source" guarantee without breaking source-profile cost/audit boundaries.
- Do not route Pipedream/CoStar evidence through `news_v1`. If multiple source families emit candidates in the same resolve pass, each source profile or deterministic path owns its own review/agent action. Pipedream/CoStar candidates use the direct review-item path until AGENT.4/5 add source-specific profiles.
- `Complete` terminal drops happen here too: evidence remains, no agent run, no review item, audit row only.

Agent result handling:

- Link any created `status_regression_review` to the triggering `agent_run` through `agent_run_review_items`.
- For `dismiss`, do not create a review item. Store decision, confidence, reason, and consulted evidence in `agent_runs`.
- For `confirm_regression` below threshold, create an open review item with agent recommendation but no auto-apply.

## 5. Agent Profile Changes

- Add `AgentTrigger.STATUS_REGRESSION_CANDIDATE = "status_regression_candidate"`.
- Add the trigger to `NEWS_AGENT_PROFILE.triggers` and `PERMIT_AGENT_PROFILE.triggers`. Future CoStar/Pipedream profiles should include it at creation.
- News regression tools should include `get_project_state`, `search_articles_similar`, `get_article_body`, and `get_permits_for_project`. This intentionally broadens the news profile beyond article tools because cross-stream permit verification is necessary for regression decisions and also useful for material contradiction triage.
- Permit regression tools can reuse `get_project_state`, `get_permits_for_project`, `get_permits_for_parcel`, and `get_articles_about_parcel_or_address`.
- Extend the agent verdict schema with:
  - `decision`: `confirm_regression`, `defer_to_review`, or `dismiss`
  - `confidence`: number from 0 to 1
  - `proposed_status`
  - `current_status`
  - `regression_reason_code`
  - `corroborating_evidence_ids`
  - `reason`
  - `human_summary`
- Prompt instructions:
  - Treat status regression as post-attribution lifecycle direction, not project attribution.
  - Weight Tier 1 structured evidence heavily, but consider news corrections and corroboration.
  - Regressions from rank `>=5` require stronger evidence; default to `defer_to_review` unless corroborated.
  - Complete projects should not normally appear; if they do, return `dismiss` and explain the terminal status.
  - Auto-applied regressions use `until_newer_evidence`, so later fresher or stronger evidence can supersede the regression.
  - Write a concise `human_summary` suitable for `review_items.payload.human_summary`.

## 6. Review Item Type and Payload Schema

Add `ReviewItemType.STATUS_REGRESSION_REVIEW = "status_regression_review"` and an Alembic enum migration for `review_item_type_enum`. Include it in decision-card consolidation so active items are unique by `(project_id, field_name, item_type)` and repeated evidence merges into one card.

Payload:

```json
{
  "origin": "status_regression_candidate",
  "field_name": "pipeline_status",
  "source_name": "urbanize_la",
  "source_record_id": "reference-or-source-record-id",
  "current_value": "Under Construction",
  "proposed_value": "Approved",
  "current_rank": 4,
  "proposed_rank": 3,
  "rank_delta": 1,
  "candidate_evidence_ids": ["..."],
  "evidence_ids": ["..."],
  "winning_evidence_id": "...",
  "reason_code": "news_status_correction_or_source_code",
  "source_anchors": [{"text": "...", "offset_start": 120, "offset_end": 180}],
  "agent_run_id": "...",
  "agent_outcome": "completed",
  "agent_recommendation": {
    "decision": "defer_to_review",
    "confidence": 0.84,
    "reason": "...",
    "corroborating_evidence_ids": ["..."]
  },
  "agent_revised_verdict": {"decision": "..."},
  "review_action_taken": null,
  "news_context": {},
  "match": {},
  "mapped_fields": {},
  "human_summary": "Urbanize reports work has halted; review whether to regress pipeline status from Under Construction to Approved."
}
```

Priority:

- High: rank delta `>=2`, current rank `>=5`, Tier 1 evidence contradicting current state, or agent confidence `>=0.9` but below the auto-apply floor.
- Medium: rank delta `1` with reasonable source support.
- Low: only for weak/ambiguous source support if the agent explicitly recommends defer and confidence is low; otherwise weak cases should be `dismiss` with no item.

Auto-apply:

- If `NEWS_REGRESSION_AUTO_APPLY_ENABLED=true` and the agent returns `confirm_regression` with confidence `>=0.9` from current rank `<=4`, create a `status_regression_review` item in committed/audit state with `status=auto_accepted`, add a committed `ReviewDecision`, write a `change_log` row with `change_type=auto_accepted`, link `agent_run_review_items`, and apply the regression through the existing override/resolution workflow. Current ranks `>=5` (`Pre-Leasing/Pre-Selling` and `Complete`) remain review-only at any confidence.
- Use a system actor such as `agent.status_regression_candidate`.
- The override should be explicit and auditable, because the resolver's forward-only rule otherwise preserves current status. Recommended mode: `until_newer_evidence` with a baseline tied to the winning/candidate evidence date. The implementation must verify that newer-evidence clearing works for system-authored overrides, not only researcher-authored overrides.
- If an active researcher override exists on `pipeline_status`, do not auto-apply. Create an open review item with override context instead.

## 7. Activity, Review Queue, and Smoke Reports

- Activity already displays agent rows, trigger counts, review links, evidence summaries, and review item human summaries. Add label/rendering coverage for `status_regression_review` so it is not displayed as a generic status change.
- Review Queue should get the new item type label, filter compatibility, payload rendering, accept/reject/custom behavior, and a human-summary template.
- Activity should surface terminal-drop resolution logs distinctly if `resolution_log.metadata` is added; otherwise include `rule_applied="terminal_regression_dropped"` rows with evidence summaries.
- `tcg-pipeline news-agent-smoke-report` should count `status_regression_candidate` triggers, include linked regression review item counts, and optionally validate `--require-triggers status_regression_candidate` for curated smokes.
- `agent_run_review_items` linkage is required for open and auto-accepted regression review items.
- Coverage/Review Queue can add a saved preset later: `item_type=status_regression_review`, priority high/medium first.

## 8. Test Coverage Plan

Recommended test names:

- `test_resolve_status_emits_regression_candidate_for_lower_ranked_evidence`
- `test_resolve_status_enumerates_regression_candidate_even_when_higher_old_evidence_wins`
- `test_resolver_emits_candidate_per_lower_ranked_observation_not_just_max`
- `test_resolve_status_complete_terminal_drop_has_no_agent_trigger`
- `test_resolve_status_stalled_inactive_manual_review_still_works`
- `test_resolve_status_forward_progression_unchanged`
- `test_news_regression_candidate_uses_status_regression_trigger_not_material_contradiction`
- `test_news_regression_confirmed_high_confidence_auto_accepts_review_item`
- `test_news_regression_confirmed_below_threshold_creates_open_review_item`
- `test_news_regression_dismissed_creates_no_review_item`
- `test_news_preleasing_regression_requires_elevated_auto_apply_threshold`
- `test_news_pass2c_truncation_does_not_create_regression_candidate`
- `test_news_use_legacy_semantic_true_does_not_emit_status_regression_candidate`
- `test_news_effect1_uncorroborated_terminal_suppression_does_not_fire_regression_alone`
- `test_news_effect1_suppression_does_not_block_independent_regression_signal`
- `test_permit_regression_candidate_links_agent_run_to_review_item`
- `test_pipedream_regression_candidate_payload_contains_candidate_evidence`
- `test_status_regression_review_payload_gets_human_summary`
- `test_news_agent_smoke_report_counts_status_regression_candidate`
- `test_active_researcher_override_blocks_regression_auto_apply`
- `test_system_authored_until_newer_evidence_regression_override_clears_on_fresh_tier1_status`
- `test_confirmed_regression_followed_by_fresh_ladbs_uc_evidence_re_resolves_or_flags_conflict`

## 9. Edge Cases and Risks

- Repeated stale articles: decision-card consolidation should merge repeated `status_regression_review` items for the same project/field/proposed status, but agent cost can still repeat before upsert. Add a pre-agent dedupe: if an active regression review already exists for the same current/proposed pair, merge evidence and skip a new agent run unless the new evidence is Tier 1 or materially newer.
- Race between preserve-current and agent verdict: preserve current status first. Auto-apply only after the agent result is persisted. The auto-apply path should run a fresh `resolve_project(apply=False)` before writing an override so it sees any evidence that landed while the agent ran.
- Cost: at organic Urbanize cadence of 5-10 articles/day, likely regression candidates should be rare, roughly 0-2/day. At `$0.07-$0.15` per run, expected daily incremental cost is `$0.00-$0.30`; even a worst organic day where all 10 articles trigger is about `$1.50`. This is not material against the news bucket warn/hard caps, but backfills should still report trigger rates before approval.
- Prompt complexity: start by extending the existing news prompt with a compact regression section and verdict schema. Do not split a separate regression prompt unless smoke runs show degraded behavior or token pressure. AGENT.7 prompt compression can revisit.
- Agent vs deterministic LADBS conflict: fresh Tier 1 UC evidence after an auto-applied regression should either clear the regression override by `until_newer_evidence` semantics or create an override/contradiction review item. Do not let an old news regression override a newer LADBS inspection indefinitely.
- Researcher override interaction: trigger can still run for visibility, but auto-apply must be disabled when an active pipeline_status override exists. The review item should include override context and recommend researcher action.
- Verdict compatibility: `agent_revised_verdict` gains optional keys only. Existing agent rows and Activity rendering must tolerate absent regression fields.
- Complete terminal evidence: evidence should remain in history, but no regression agent run should fire. The audit row and raw evidence are enough.
- Any-source scope: Pipedream/CoStar direct review-item creation is acceptable until AGENT.4/5 profiles exist, but the payload must make `agent_recommendation=null` explicit so smoke reports and Activity do not imply an agent reviewed it.

## 10. Open Questions

1. How should Pipedream/CoStar route before their full agent profiles exist? Recommendation: create direct `status_regression_review` items without an agent run or agent recommendation until AGENT.4/5 land source-specific profiles. Revisit at AGENT.4/5 design time, or earlier only if direct review items create persistent queue backlog without agent corroboration.
2. Should `resolution_log` gain nullable `metadata JSONB`? Recommendation: yes. It is the cleanest way to record candidate status, rank delta, and terminal-drop reason when the resolved value is intentionally unchanged.
3. What exact override mode should auto-applied regression use? Recommendation: `until_newer_evidence` with the candidate evidence date as baseline, so later Tier 1 evidence can supersede the regression.

## 11. Implementation Sequencing

Slice 0: approval gates before code.

- Confirm candidate enumeration semantics: one raw candidate per lower-ranked observation, merge before agent routing without losing evidence IDs.
- Confirm Pipedream/CoStar direct review-item routing until AGENT.4/5.
- Decide whether `resolution_log.metadata` ships in Slice 1.

Slice 1: Contracts and schema.

- Add `status_regression_candidate` trigger.
- Add `status_regression_review` enum value and migration.
- Add review payload helpers, human-summary template, frontend labels, and smoke-report trigger support.
- Add optional `resolution_log.metadata` if approved.

Slice 2: Resolver candidate emission.

- Enumerate regression candidates from all status observations.
- Preserve current status.
- Emit terminal-drop audit metadata for Complete.
- Keep Stalled/Inactive carve-out unchanged.

Slice 3: News integration first.

- Split status regression from `material_contradiction`.
- Route `status_regression_candidate` through `news_v1`.
- Create/merge/dismiss/auto-accept `status_regression_review` based on agent verdict.
- Add curated paste-link smoke coverage.

Slice 4: Review workflow auto-apply.

- Implement committed auto-accepted regression items and system-authored override/resolution application.
- Link `agent_run_review_items`.
- Confirm Activity and Review Queue behavior.

Slice 5: Structured source rollout.

- Wire `collect.py` regression candidates for LADBS/permit profile.
- Add CoStar seed/upload direct regression review-item creation.
- Keep Pipedream direct-review support in the shared helper, but defer production activation until the Pipedream sync/import path updates existing projects.
- Add tests for CoStar seed, CoStar upload linkage, Pipedream helper behavior, and LADBS scenarios.

Slice 6: Monitoring and stabilization.

- Extend daily news smoke validation with `status_regression_candidate` trigger
  counts, linked `status_regression_review` card counts, and an optional
  minimum linked-card validator for curated smokes.
- Defer permit smoke regression-count parity to a follow-up monitoring slice.
- Add regression trigger counts to AGENT.reset review artifacts.
- Watch duplicate-trigger rates and cost for 3-5 organic cron days before any broad backfill.
- Ensure AGENT.2 Activity / Audit Log rendering is live for regression-specific agent, review, and resolution audit rows before declaring the regression rollout complete.

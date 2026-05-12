# Agentic Escalation Layer — Design

> **Status:** Draft — supersedes the standalone `agentic_pipeline_proposal.md` for purposes of build planning.
> **Scope:** Project-attribution decision layer for any structured intake. Replaces Pass 3a/3b deterministic re-extraction with an Opus-class agent loop, swaps default extraction model, and adds retrieval infrastructure. The initial sprint ships news and permit consumers; CoStar and Pipedream are deferred follow-ons that plug into the same architecture.
> **Authoring context:** Reconciles the original proposal against the actual codebase (verified against [news/extraction.py](../../src/tcg_pipeline/news/extraction.py), [news/integration.py](../../src/tcg_pipeline/news/integration.py), [matching/news_matcher.py](../../src/tcg_pipeline/matching/news_matcher.py), [matching/matcher.py](../../src/tcg_pipeline/matching/matcher.py), [db/models.py](../../src/tcg_pipeline/db/models.py), and the 2026-05-02 D.6 staging smoke result), and updates with researcher answers to the 17 clarifying questions.
> **Last updated:** 2026-05-05
> **Maintained by:** Nate Goldstein + Claude Code

---

## 0. Top-Of-File Callouts (read first)

Two cross-cutting commitments that affect every section below:

### 0.1 No Batch API for now; architect for plug-in later

The original proposal called for cron-driven extraction to route through Anthropic's Batch API for ~50% cost discount, with paste-a-link staying synchronous. **We are not doing this in the initial build.**

- **Decision:** All extraction stays synchronous via `client.messages.create` for both paste-a-link and scheduled scrape, exactly as today.
- **Architectural constraint:** The agent runner and extraction dispatch path must be designed so that a future swap to Batch API for scheduled-scrape extractions is a swap of dispatch backend, not a rewrite of integration timing. Concretely:
  - Extraction calls go through a thin dispatcher interface (`ExtractionDispatcher`) with `synchronous` and (future) `batch` implementations.
  - Integration triggering must not assume "the extraction completed in this same worker job." Today it does; the new design separates extraction completion from integration triggering so a future Batch implementation can land integration on a different code path (results-poll handler, webhook, or queue event).
  - Cost reservation, structured logging, and `news_extractions` row writes stay identical regardless of dispatch backend.
- **Roadmap impact:** Add a deferred item under Phase D-late (or a later phase) for "Batch API dispatch for scheduled extractions" with explicit cost-savings target.

### 0.2 Default extraction model is Opus 4.7

The proposal suggested Sonnet 4.6 as the default extraction model. After AGENT.1 smoke-set A/B testing, the researcher selected Opus 4.7 for quality.

- **Decision:** Keep Opus 4.7 as the default extraction model. Stage 1 still keeps the model-swap infrastructure (configurable model, prompt-cache validation across models, A/B harness) so future model changes can be measured rather than guessed.
- **Primary candidates measured in Stage 1:**
  - **Claude Sonnet 4.6** — default tier-step-down assumption from the proposal. Untested against TCG articles.
  - **GPT-5.4** — separate provider entirely; requires a multi-provider abstraction in the LLM layer or a Vercel AI Gateway integration before A/B can run against it.
  - **Claude Opus 4.7 (selected)** — measured as the baseline and retained as default.
- **Supplemental candidates:** Opus 4.6 and GPT-5.5 were requested and measured after provider preflight confirmed availability.
- **Decision gate result:** Opus 4.7 won on quality. **There is no hard cost target that fails the choice** — the slim no-glossary prompt already cuts default-extraction cost substantially, and AGENT.2 reduces total Opus usage by replacing Pass 3a/3b re-extraction with targeted agent escalation.
- **Roadmap impact:** Stage 1 built the model A/B harness and the cross-provider abstraction. AGENT.1 now proceeds to retrieval/embedding implementation.

---

## 1. Decision Summary

**Architectural framing.** The agent layer is the **project-attribution decision layer for any structured intake**, not a news-only or permit-only feature. News and permits are the first two consumers; CoStar uploads, Pipedream uploads, and any future intake source plug into the same agent runner via a source profile (§5.9). The sprint ships news and permits; CoStar (AGENT.4) and Pipedream (AGENT.5) are deferred follow-ons.

This document supersedes the originally-proposed Pass 0/1/2/3 deterministic-with-re-extraction pattern with a hybrid pipeline:

1. **Discovery & fetch.** Daily cron polls source, or researcher pastes a link. No model. *(Unchanged from today.)*
2. **Pass 1 structural.** Regex and dictionary extraction. No model. *(Unchanged.)*
3. **Triage.** Haiku 4.5 broad-net classifier, uncertain leans relevant. *(Unchanged.)*
4. **Default extraction.** Single-call structured extraction using Opus 4.7 per §0.2. System prompt cached. *(Still configurable; Opus 4.7 is the selected default.)*
5. **Match.** Deterministic 5-stage cascade matcher. Classifies as `confirmed`, `possible`, `new_candidate`, or `discarded`. *(Unchanged.)*
6. **Agent escalation.** Opus 4.7 agent loop with tool access. Replaces today's Pass 3a structural-conflict + low-confidence reextract path AND today's Pass 3b new-candidate reextract path. Output uses the same extraction schema plus `reasoning_trace`, `evidence_consulted[]`, `tool_calls_summary[]`. *(New.)*
7. **Output-quality retry.** Separate cheaper retry path for `parse_error / schema_invalid / refused / truncated` extraction outputs — these route to a stronger-guidance retry, NOT the agent loop. *(New, replaces today's Pass 3a output-quality branch.)*
8. **Pre-resolve contradiction check.** Agent owns contradiction reasoning for both pre-resolve "this article doesn't fit known state" cases AND post-resolve "newer evidence contradicts an override" cases. Today's `detect_project_contradictions` post-resolve flow gets folded into the agent layer. *(Substantial change — see §5.5.)*
9. **Evidence write & resolve.** Extraction or agent output becomes evidence rows. Per-field resolver logic unchanged. *(Unchanged.)*
10. **Review queue.** Populated on agent-explicit escalation, agent-flagged contradictions, and `new_candidate`/`possible` matches the agent didn't promote. Per-reference, not per-article, gates retrieval-index inclusion. *(Granularity change.)*

**Quality-driven decision.** Latency is not a constraint for the agent path; researchers will wait. Cost target ~$100/12-month backfill is aspirational, not a hard gate — under any of the three candidate default-extraction models, the agent-on-hard-cases architecture is a cost improvement over today's Opus-on-everything path. Cost informs model choice and trigger calibration; it does not block stage progression.

---

## 2. What Stays, What Changes, What's Added

### Stays unchanged
- Pass 0 fetch (`PoliteNewsCollector`, robots.txt, rate limits, conditional GET, fetch-path interface).
- Pass 1 structural extraction ([news/structural.py](../../src/tcg_pipeline/news/structural.py)).
- Triage (Haiku 4.5, broad-net, uncertain-leans-relevant).
- Deterministic 5-stage matcher cascade ([news_matcher.py](../../src/tcg_pipeline/matching/news_matcher.py)): identifier → registry hint → address composite → developer/neighborhood/units fingerprint → project-name fuzzy → new_candidate / discarded.
- Per-field resolver ([resolution/engine.py](../../src/tcg_pipeline/resolution/engine.py)) and source-tier weighting.
- Evidence supersession via `evidence.superseded_at` and the active partial index ([db/models.py:763-826](../../src/tcg_pipeline/db/models.py)).
- Source registry, news scheduler, cost cap with `pg_advisory_xact_lock`. Today's `news_extraction_costs` accounting becomes `llm_cost_usage` (bucket-keyed) post-AGENT.1; the `pg_advisory_xact_lock` mechanism stays.
- Review queue staged/committed state machine, batch commit, change_log audit.
- Synchronous `client.messages.create` dispatch (per §0.1).

### Changes

| Today | Becomes |
|---|---|
| Pass 3a fires on (a) Pass 1↔2 conflict, (b) low confidence, (c) parse/schema/refused/truncated | (a) and (b) → agent escalation. (c) → cheaper output-quality retry path. |
| Pass 3b fires on `new_candidate` matches at integration time | Folds into agent escalation triggers (`new_candidate` is one of several agent triggers). |
| `reextract_v1` prompt is the active re-extraction template | Reference-only. Agent prompt + tool definitions replace it. Legacy reextraction rows backfilled with synthetic `reasoning_trace` per §10. |
| Default extraction is Opus 4.7 | Stays Opus 4.7 after Stage 1 A/B. The model remains configurable and the harness remains available for future swaps. |
| `detect_project_contradictions` runs only post-resolve, only against active researcher overrides | Folded into agent layer. Agent owns contradiction reasoning pre-resolve (article-vs-state) and post-resolve (newer-evidence-vs-override). |
| Reviewer acceptance is per-field via review_decisions; no retrieval consequence | Per-reference acceptance gates retrieval-index inclusion. |
| Re-extraction output is structured JSON only | Agent output adds `reasoning_trace` (100-300 chars), `evidence_consulted[]` (list of {article_id, project_id, role}), `tool_calls_summary[]`. |

### Added (genuinely new)
- Agent runner: tool dispatch, retry logic, per-run cost cap (target $5 absolute hard ceiling, average $0.50–$1 per agent run), wallclock timeout, structured logging.
- Seven agent tools (see §6.3).
- `pgvector` extension + article embedding pipeline. Per-reference acceptance gating per §6.4.
- PostGIS GIST index on `Project.location` (one-line migration).
- Project-state digest as a Postgres **view** (not a table — see Q15) over `project_field_resolution` + `project_latest_evidence` plus a small recent-evidence query.
- Evidence schema extensions (`reasoning_trace`, `evidence_consulted`, `tool_calls_summary`).
- Output-quality retry path for parse/schema/refused/truncated extractions.
- A/B harness for the model swap.

---

## 3. Decision Log (Q1–Q17 Answers)

Recorded verbatim from researcher answers, 2026-05-04, with implementation notes.

### Q1 — Sonnet vs Opus quality A/B
**Answer.** Initially open to Sonnet 4.6, GPT-5.4, or staying on Opus 4.7 if costs are manageable. Opus 4.6 and GPT-5.5 were added as supplemental measured candidates once availability was confirmed. Researcher decision on 2026-05-05: keep Opus 4.7.
**Implementation.** Stage 1 builds an A/B harness running candidates against the D.6 smoke article set (5 URLs from `tests/fixtures/news/urbanize_la/pass1_validation_articles.json`), comparing extraction outputs field-by-field plus per-call cost. Primary run covers Opus 4.7, Sonnet 4.6, and GPT-5.4; supplemental run covers Opus 4.7, Opus 4.6, and GPT-5.5. Goal of Stage 1 is to produce firm cost-and-quality numbers per candidate, not to enforce a budget gate. The selected default is Opus 4.7.

### Q2 — Basis for "Sonnet good enough"
**Answer.** Just model-tier ladder; no real basis.
**Implementation.** Sonnet as default was a hypothesis, not a commitment. The A/B harness exists specifically because the assumption was unvalidated. Outcome of Stage 1 is "stay on Opus because the quality gap isn't worth the savings."

### Q3 — Cost target
**Answer.** Aspirational target is ~$100 for 12-month backfill (~$17 for 8-week LA window). **This is not a hard budget.** Under the current trajectory we already extract every article with Opus, so any agent-on-hard-cases architecture using *any* of the three candidate default-extraction models is a cost improvement over status quo. Sonnet being more expensive than projected is not a fail.
**Implementation.** Cost is a measured input to the model-choice decision and the Stage 2 trigger calibration, not a stage gate. See §7 for the updated cost framing.

### Q4 — Trigger routing
**Answer.** Agent fires on (a) Pass 1↔2 reasoning conflict and (b) low confidence. Output-quality failures (parse_error, schema_invalid, refused, truncated) route to a separate cheaper retry-with-stronger-guidance path, NOT the agent.
**Implementation.** §5.4 defines the retry path. The agent prompt does not need to handle "previous extraction was malformed JSON."

### Q5 — Contradiction detection placement
**Answer.** Move contradiction detection earlier. Agent becomes the contradiction authority for both pre-resolve (article-vs-state) and post-resolve (newer-evidence-vs-override) cases.
**Implementation.** Folds today's `detect_project_contradictions` into the agent layer. C.i code becomes a fallback for the case where the agent didn't fire (still handles override contradictions when no agent run occurred). See §5.5.
**Thoughtfulness requirement (researcher direction, 2026-05-04):** C.i was shipped on 2026-04-27 — extremely recent. Rewriting it is acceptable but must be done carefully. The Stage 2 build plan must include an explicit upstream/downstream impact assessment before any C.i code is touched. That assessment must enumerate every code path that calls into or depends on C.i's contradiction outputs, including but not limited to:
- `resolve_project(apply=True)` callers in `db/collect.py`, `news/integration.py`, `db/review_workflow.py`, FastAPI override and edit endpoints, `canonicalize-developers --apply`, and the `detect-contradictions` CLI.
- Review queue rendering of `override_contradiction` items in the frontend (review queue list, review detail, decision cards).
- The `contradicted_override_id` and `contradiction_priority` columns on `review_items` and any code that reads them.
- Backfill scripts and audit tooling that scan for active contradiction items.
- Test fixtures and integration tests covering contradiction behavior.

For each call site, the assessment must answer: does the new agent-led flow change the contract this caller depended on? If yes, list the change and the migration. The rewrite proceeds only after this assessment is reviewed.
**Regression coverage.** Every C.i contradiction case currently in the test suite must continue to pass under the new flow. Add new tests for the agent-run cases.
**No feature flag.** Per researcher direction, the rewrite happens cleanly rather than running both paths in parallel. The thoughtfulness above is the mitigation, not a flag.

### Q6 — Agent decision authority
**Answer.** Types 1, 2, and 3 are agent authority. Type 4 (whole-article discard) is researcher action.
- **Type 1:** Promote `new_candidate` → `confirmed` against an existing project.
- **Type 2:** Downgrade deterministic `confirmed` to `possible` when agent doesn't trust the article.
- **Type 3:** Pick a single project from a `possible` multi-candidate result and confirm it.
- **Type 4 (NOT agent):** If agent thinks article isn't about a real project, it escalates to review with reasoning, doesn't retroactively mark the article not-relevant.
- All three types must produce audit trail: matcher's original verdict, agent's revised verdict, reasoning trace, tools consulted.
- **Type 2 specifically** needs review queue UI to surface "matcher said confirmed, agent said possible — here's why."

### Q7 — Safe-state on agent failure or timeout
**Answer.** Deterministic outcome stands; article goes to review with "agent failed: see notes" attachment.
**Implementation.** Agent runner wraps each run in try/except/timeout. On any failure (timeout, tool-dispatch error, model API error, malformed agent output), the deterministic matcher's verdict is committed normally and a flag `agent_run_failed=true` plus error text is attached to the resulting review item or evidence row.

### Q8 — Permit dedup pain quantification
**Answer.** Real pain point. Hard to quantify precisely. Want to minimize manual dedup on "new candidates."
**Implementation.** Stage 3 trigger calibration prioritizes `new_candidate`-style unmatched permits, where dedup pain is concentrated.

### Q9 — Permit agent triggers
**Answer.** Agent only on `new_candidate`-style unmatched permits, plus very large unit-count changes and/or product-type changes (to confirm same project).
**Implementation.** Stage 3 trigger set is a strict subset of Stage 2's news triggers:
- Permit `new_candidate` (no deterministic match, but `create_new_candidates: true` source like `ladbs_permits`).
- Permit's mapped fields imply unit-count change >10% from current project state (per the uniform threshold decision in §5.9; revision 5).
- Permit's mapped fields imply product-type change from current project state.
- Permit triggers do NOT include parse/refused/truncated (LADBS payload is structured already), low-confidence-LLM-extraction (no LLM in the permit path), or general "address composite confirmed" cases.

### Q10 — Pipedream overlap statistical sufficiency
**Answer.** Not confirmed, but expects enough overlap. Plan: Pipedream coverage will grow with our market expansion.
**Implementation.** Build the auto-comparison job; treat the first June 2026 run as a sample-size validation pass. If <30 confirmed matches in the window, document the gap and rely more heavily on reviewer-acceptance signal until overlap grows.

### Q11 — Pipedream mapping & geographic scope
**Answer.** Don't know if mapping exists. Pipedream covers parts of San Diego, San Francisco, Silicon Valley, Seattle, Denver, plus others. Plan: expand "compare quarterly Pipedream output against tool, treat differences (where Pipedream is correct) as feedback for tool improvement" to those markets as we expand.
**Implementation.**
- Build a `pipedream_coverage_zips` config table (or YAML) keyed by Pipedream survey window with the zip-code-list per survey.
- Map TCG project_ids to coverage windows via `Project.zip` in coverage list AND `last_evidence_date` within the window's compare period.
- Initial implementation LA-only; San Diego, SF, Silicon Valley, Seattle, Denver, others added as those markets come online.
- This is roadmap Phase H/I work; the auto-compare *job* is built in Stage 2 but its inputs grow with market expansion.

### Q12 — Silent agent error blind spot
**Answer.** Need a way to surface non-escalated, non-flagged agent decisions for spot-grading. Unsure whether ongoing, initial-build-only, or new-market-only. Minimize manual work.
**Implementation.**
- Build a "spot-check sampler" that surfaces N random agent decisions per week (default N=10) where the agent ran but did not escalate to review.
- These appear in a researcher dashboard tile labeled "Agent spot-check queue."
- Researcher can mark each as "agreed" or "disagreed (here's why)" — disagreement feeds back into eval.
- Default cadence: ongoing at 10/week initially, with the option to taper to monthly after 3 months of stable agreement-rate.
- New-market rollout: bump to 20/week for first 4 weeks of any new market's agent activation.

### Q13 — Retrieval index gating granularity
**Answer.** Index per-reference, not per-article.
**Implementation.** A reference enters the embedding index only after its associated review item is committed `accept`. References inside the same article can be partial: reference A indexed, reference B not. Embedding pipeline keys on `(article_id, reference_index)` not `article_id`. More plumbing but accepted as a tradeoff.

### Q14 — State digest update cadence
**Answer.** Incremental, but only on writes that matter for retrieval — accept-flow commits, resolver writes that change a tracked field, project creation. Skip raw evidence writes.
**Implementation.** Since the digest is implemented as a view (Q15), this question simplifies: views are always fresh by definition. The intent of Q14 — "agent must read up-to-date state" — is automatically satisfied by view-based reads. The cadence concern only re-emerges if we later promote any digest field to a materialized view, in which case `REFRESH MATERIALIZED VIEW` is called from those three trigger points.

### Q15 — Digest table vs view
**Answer.** View is fine.
**Implementation.** `get_project_state` is a Python tool handler that joins `project_field_resolution`, `project_latest_evidence`, and a small `recent_evidence` query. No new table. Promote to materialized view only if production observation shows read-cost is constraining the agent loop.

### Q16 — Legacy reextraction row backfill
**Answer.** Backfill in cutover migration as one atomic Alembic revision.
**Implementation.** Stage 2 cutover migration:
1. Adds `reasoning_trace`, `evidence_consulted`, `tool_calls_summary` columns on `news_extractions` (or a new `agent_runs` table — see §6.5 design choice).
2. Backfills synthetic `reasoning_trace = "Legacy non-agent re-extraction. Triggered by: <triggered_by>. The Pass 3a/3b path re-prompted Opus with the prior extraction's output and trigger context; this row reflects that single-shot re-extraction, not an agent loop."` for all existing `pass='reextraction'` rows.
3. Sets `evidence_consulted = []` and `tool_calls_summary = []` for legacy rows.
4. Adds a `legacy_reextract: true` flag in the diagnostic JSONB.

UI renderer is single-shape; legacy rows have empty consulted-evidence/tools sections with the synthetic prose explaining why.

### Q17 — Sequencing
**Answer (revised 2026-05-04).** Marathon sprint: build all three stages in one continuous workstream and ship as one cutover. Permits live from day one. Cost guardrails (scoped daily cap + runtime kill switch per §5.8) replace staged-rollout production observation as the safety mechanism.

The three roadmap items below are dependency-tracking units within the sprint, not separate shipping events:

**AGENT.1 — Default-extraction infrastructure + retrieval prerequisites.**
- Multi-provider abstraction in [news/llm.py](../../src/tcg_pipeline/news/llm.py) (Anthropic + OpenAI, or Vercel AI Gateway).
- Three-way A/B harness; run Opus 4.7, Sonnet 4.6, GPT-5.4 against the D.6 smoke article set.
- Researcher decision complete: keep Opus 4.7 as the default extraction model. No hard cost gate.
- PostGIS GIST index on `Project.location`.
- Article embedding pipeline gated on Q13 per-reference acceptance.
- Drop in-prompt glossary entirely (option 3 per §5.1).

**AGENT.2 — Agent on news.**
- **Pre-build deliverable (Claude Code-owned):** §5.5.0/§5.5.1 contradiction-detection impact assessment, written to `docs/specs/ci_contradiction_impact_assessment.md`. Researcher reads before any C.i code is touched. Concise. `⚠ HUMAN REVIEW` markers on uncertain rows.
- Agent runner, tools, evidence-schema additions, output-quality retry path.
- Shared semantic field interpretation layer: source-profile-owned mapping from observed source facts/language to TCG evidence fields. Initial scope covers `pipeline_status`, `product_type`, `age_restriction`, `date_delivery`, and unit buckets including `workforce_units`. News uses an LLM only for unstructured/ambiguous article language; deterministic source signals stay deterministic.
- Replace Pass 3a (a)+(b) and Pass 3b with agent loop.
- Move contradiction detection earlier (per Q5).
- Cutover migration backfills legacy reextractions (per Q16).
- Worker model: one job per article needing agent escalation (per R8 / §5.3).
- Runtime kill switch `agent_enabled_for_news` (per §5.8). Default `true`.

**AGENT.3 — Agent on permits.**
- Cross-stream tools, permit-specific failure-mode prompts.
- Wire permits into the same semantic interpretation interface with deterministic LADBS/source-profile rules first; LLM/agent interpretation is reserved for ambiguous permit descriptions, conflicting source signals, or cross-stream exceptions.
- LADBS adapter integration through agent on calibrated trigger set (Q9 — `new_candidate`, large unit changes, product-type changes only).
- Scoped permit cost cap: `cost_caps` row for `bucket='permits'` with `daily_warn_usd: $50`, `daily_hard_usd: $75` per §5.8.
- Runtime kill switch `agent_enabled_for_permits` (per §5.8). Default `true` at launch.

**Single cutover, all three live simultaneously.** First 4 weeks: daily monitoring of per-bucket cost, fire rate, acceptance rate, spot-check sampler agreement. Either kill switch flippable within minutes if costs spike.

---

## 4. Pipeline Architecture

### 4.1 Today's pipeline (verified against codebase)

```
Discovery (cron RSS / sitemap, or paste-a-link)
   ↓
Pass 0 — fetch (httpx + trafilatura, polite collector)
   ↓
Pass 1 — structural (regex + Aho-Corasick dictionaries)
   ↓
Pass 2a — triage (Haiku 4.5, broad-net JSON classifier)
   ↓ [if triage = relevant]
Pass 2b — extraction (Opus 4.7 single call, structured JSON via tool_choice)
   ↓
   ├── [if Pass 1↔2 conflict OR low candidate_confidence OR parse/schema/refused/truncated]
   │       ↓
   │   Pass 3a — reextract_v1 (Opus 4.7 single call with prior output + trigger context)
   ↓
Match (5-stage cascade: identifier → registry hint → address composite → fingerprint → name fuzzy)
   ↓
   ├── [if any reference returned new_candidate]
   │       ↓
   │   Pass 3b — reextract_v1 (Opus 4.7 single call with new-candidate context)
   ↓
Integrate (write evidence rows; possible/new_candidate write orphan evidence)
   ↓
Resolve (per-field resolver writes Project row updates)
   ↓
detect_project_contradictions (post-resolve, only against researcher_overrides)
   ↓
Review queue (STATUS_CHANGE per field, POSSIBLE_MATCH, NEW_CANDIDATE, OVERRIDE_CONTRADICTION items)
```

### 4.2 Proposed pipeline (post-Stage 2)

```
Discovery (cron RSS / sitemap, or paste-a-link)
   ↓
Pass 0 — fetch
   ↓
Pass 1 — structural
   ↓
Pass 2a — triage (Haiku 4.5)
   ↓ [if triage = relevant]
Pass 2b — default extraction (model TBD per Stage 1 A/B; structured JSON)
   ↓
   ├── [if parse_error / schema_invalid / refused / truncated]
   │       ↓
   │   Output-quality retry (same model, stronger guidance prompt; up to 2 retries)
   ↓
Match (deterministic 5-stage cascade — UNCHANGED)
   ↓
   ├── Agent escalation triggers (any of):
   │     - Pass 1↔2 structural conflict (reasoning, not output-quality)
   │     - candidate_confidence: low on populated fields
   │     - new_candidate match
   │     - possible match with multiple candidates
   │     - extraction returned multiple distinct project mentions
   │     - article materially contradicts existing project state
   │       (>10% unit delta, status regression, developer mismatch)
   │       ↓
   │   Agent loop (Opus 4.7 with tools)
   │       - get_project_state
   │       - get_recent_evidence
   │       - search_articles_by_project
   │       - search_articles_similar (vector)
   │       - get_developer_projects
   │       - get_nearby_projects (PostGIS, 0.25mi default, returns distance_feet)
   │       - escalate_to_review
   │       ↓
   │   Agent output: revised match decision + reasoning_trace + evidence_consulted + tool_calls_summary
   │       ↓
   │   [if agent failure/timeout: deterministic stands + "agent failed" flag on review item]
   ↓
Integrate (write evidence rows; agent overrides matcher per Q6 types 1/2/3)
   ↓
Resolve (per-field resolver — UNCHANGED)
   ↓
[contradiction reasoning now lives inside agent escalation;
 detect_project_contradictions runs only as fallback when no agent ran]
   ↓
Review queue
   - Per-reference review items
   - Type 2 agent overrides surface "matcher confirmed, agent downgraded — why" UI
   - Per-reference acceptance gates retrieval-index inclusion
```

### 4.3 Stage 3 addition: permits

```
LADBS Socrata pull → adapter → RawRecord
   ↓
match_raw_record (deterministic — UNCHANGED)
   ↓
   ├── Agent escalation triggers (permit subset, per Q9):
   │     - new_candidate (unmatched permit on a create_new_candidates: true source)
   │     - >10% unit-count change implied vs current project state
   │     - product-type change implied vs current project state
   │       ↓
   │   Agent loop (same runner as news; permit-specific prompt + cross-stream tools)
   │       - all 7 news tools, plus:
   │       - get_permits_for_parcel(parcel_id)
   │       - get_permits_for_project(project_id)
   │       - get_articles_about_parcel_or_address(parcel_or_address, radius_feet)
   │       - get_permits_for_parcel_or_address(parcel_or_address, radius_feet)
   ↓
Integrate (existing collect.py path, with agent overrides)
   ↓
Resolve → Review queue
```

---

## 5. Component Design

### 5.1 Default extraction (Stage 1 / AGENT.1)

Goal: enable model swap with measured quality validation, AND remove the dominant cost driver (the in-prompt glossary).

**Glossary removal — option 3 (researcher direction 2026-05-04).**
- Today's `render_news_glossary` in [news/prompts.py:219-260](../../src/tcg_pipeline/news/prompts.py) emits the full developer registry + market-filtered project list as a cached system block (~103k tokens at LA scale; ~50k+ projects unscoped at 25-market scale, which exceeds context).
- Stage 1 removes this from the default-extraction prompt entirely. Implemented 2026-05-05: `render_extraction_prompt` now emits only (a) the system template (`extract_v2/system.md`, small), (b) the signal flag registry (small).
- Default extraction emits raw `candidate_name` and `candidate_developer` text without `registry_developer_id` / `registry_project_id` hints. `extract_v2/schema.json` keeps those fields available for parser compatibility but no longer requires them.
- Default extraction is evidence-only: `extract_v2/system.md` explicitly bans outside knowledge, web knowledge, memory, and guessing. Missing names, addresses, developers, counts, dates, statuses, coordinates, and identifiers stay null unless directly observed in the provided article text or structural signals.
- `candidate_status_signal` is treated downstream as TCG `pipeline_status` evidence, so `extract_v2` now includes a concise TCG status rubric. A conference comment or first mention of an idea is `Conceptual`; `Proposed` requires stated application/planning/design-review activity or another concrete proposal beyond an idea. The prompt also tells the model to verify ambiguous structural status phrases against nearby article text rather than copying the structural canonical value blindly.
- `extract_v1` is retained as the legacy glossary prompt so historical rows tagged `extract_v1` keep one meaning. Legacy `reextract_v1` keeps its glossary block until AGENT.2 moves Pass 3a/3b into `news/extraction_legacy.py`; the A/B harness must call the default `extract_v2` path, not either legacy path.
- The deterministic matcher continues to use its existing fuzzy registry matching (developer canonicalization via `canonicalize_developer_name`, project name fuzzy via rapidfuzz).
- Registry knowledge moves to the agent's tool layer (`get_developer_projects`, `search_articles_similar`, `get_nearby_projects`) — accessed on demand when the agent fires, not preloaded into every extraction.
- Eliminates the 25-market scaling blocker and the dominant per-article cost line.

**Schema impact.** `ProjectReferencePayload.registry_developer_id` and `registry_project_id` ([extraction.py:287-288](../../src/tcg_pipeline/news/extraction.py)) become optional fields the LLM is no longer asked to populate. The matcher's `validate_reference_registry_hints` ([news_matcher.py:191-226](../../src/tcg_pipeline/matching/news_matcher.py)) still works for paste-a-link or future code paths that pass registry hints, but those hints are no longer expected from default extraction.

**Configuration.**
- `news_extract_model` setting (already exists — [settings.py:59](../../src/tcg_pipeline/settings.py)).
- `news_extract_provider` setting chooses `anthropic`, `openai`, or `vercel_ai_gateway`; Anthropic remains the production default.
- `MODEL_PRICING_USD_PER_MILLION` covers the AGENT.1 harness candidates: Haiku 4.5 triage, Opus 4.7, Opus 4.6, Sonnet 4.6, GPT-5.5, and GPT-5.4. Alias support covers native IDs and Gateway-style provider prefixes (for example `anthropic/claude-sonnet-4-6`, `openai/gpt-5.4`).
- Vercel AI Gateway requires `AI_GATEWAY_API_KEY`. It must not fall back to `OPENAI_API_KEY`; the keys are distinct and falling back would mask configuration errors as 401s during the harness.
- Pricing assumptions are machine-readable for harness output. Current explicit assumption: OpenAI Responses usage should report cache-creation tokens as zero; if non-zero cache-creation usage is ever passed into internal accounting, it is priced at the full input rate. Cached input uses current OpenAI list pricing.
- Current routing policy until explicitly revised: use direct provider APIs for all built/current AGENT work. Run Claude candidates through native Anthropic and GPT candidates through native OpenAI. Vercel AI Gateway remains a deferred operational option for centralized routing/monitoring; before enabling it, run a sweep of all LLM call sites, configs, pricing aliases, cost attribution, alerts, and deployment env vars to confirm Gateway routing is intentional and no direct-provider assumptions remain. A separate Gateway connectivity smoke may be added later as an auxiliary run, not the primary A/B.

**A/B harness — end-to-end, not extraction-JSON-only (revised 2026-05-04).** Senior-developer feedback called out that the product impact is attribution + review workload, not just per-field JSON correctness. The harness measures the full pipeline outcome per candidate model.

- CLI command implemented 2026-05-05: `tcg-pipeline news ab-extract --candidates anthropic:claude-opus-4-7,anthropic:claude-sonnet-4-6,openai:gpt-5.4 --fixture tests/fixtures/news/urbanize_la/pass1_validation_articles.json`.
- Runs all three models against the same articles with the new (slim) cached system prompt — no glossary, just template + signal flags.
- Uses active `extract_v2` through `render_extraction_prompt`, then parses with the production extraction parser.
- Runs deterministic matcher projections directly, and projects review-item counts by invoking the existing news integration code inside a rollback-only transaction. The harness report is therefore non-mutating: temporary `news_articles`, `news_extractions`, `news_project_references`, `source_runs`, `evidence`, and `review_items` rows are rolled back after each projection.
- Before the first article LLM call, the CLI prints the redacted `DATABASE_URL`, fixture article count, candidate count, and planned LLM call count. The harness then runs a lightweight provider/model preflight per candidate; any missing key, unreachable model, or Gateway routing error aborts before the paid article loop.
- Harness LLM spend intentionally bypasses `reserve_llm_cost` / `record_llm_cost` and therefore does not write `llm_cost_usage` rows. The JSON report records this explicitly under `cost_accounting` and is the audit trail for harness spend.
- Per-model metrics captured for each article:
  - **Parse outcomes:** parse_status distribution (`ok` / `parse_error` / `schema_invalid` / `refused` / `truncated`).
  - **Reference counts:** how many `project_references` does each model emit per article. Outliers in either direction indicate over- or under-extraction.
  - **Matcher outcome distribution:** how many references resolve to `confirmed` / `possible` / `new_candidate` / `discarded` after the deterministic matcher runs on each model's output.
  - **Agent trigger rate:** pre-AGENT.2 proxy for how many articles would fire the agent under each model's output: current Pass 3a structural/low-confidence reasons plus `pass2_new_candidate` matcher outcomes. Parse/schema/refusal/truncation outcomes are reported separately as output-quality retry candidates, not agent triggers.
  - **Review item counts:** projected `STATUS_CHANGE`, `NEW_CANDIDATE`, and `POSSIBLE_MATCH` counts from the rollback integration pass. `OVERRIDE_CONTRADICTION` remains AGENT.2-specific and is not projected by the AGENT.1 harness scaffold.
  - **Cost per article:** scaffold records the measured default-extraction call cost from provider usage. The final AGENT.2 comparison extends this to all-in cost once agent-run pricing exists: triage + default extraction + projected agent runs.
  - **Latency:** end-to-end per article wallclock.
  - **Payload quality (researcher spot-grade):** for each model on each article, researcher rates the extraction output 1-5 on (a) factual correctness, (b) completeness, (c) field-attribution fidelity (e.g., "did the model put the developer in the right field?"). Sample size: 5 articles × 3 models = 15 graded outputs. Manageable.
- Output: a single JSON summary report under `data/output/news/ab_extract_*.json` by default. Each article result includes empty `payload_quality_spot_grade.score` / `notes` fields for researcher grading before deciding.
- Researcher picks default model from the data. No hard cost gate. Cost is one of seven dimensions, not the gating one.

**Live AGENT.1 smoke-set results (2026-05-05).**

Primary run after `extract_v2` observed-field tightening: `data/output/news/ab_extract_20260505_174623.json`.

| Candidate | Parse outcomes | References | Agent trigger rate | Projected review items | Measured/adjusted cost |
|---|---:|---:|---:|---:|---:|
| `anthropic:claude-opus-4-7` | 5/5 ok | 6 | 1.0 | 4 | `$0.202427` |
| `anthropic:claude-sonnet-4-6` | 5/5 ok | 5 | 0.6 | 4 | `$0.107957` |
| `openai:gpt-5.4` | 5/5 ok | 5 | 0.6 | 3 | `$0.054534` adjusted to current OpenAI cached-input pricing; the JSON report was generated before the cached-input pricing correction and shows `$0.072966`. |

Supplemental requested run against two additional available models: `data/output/news/ab_extract_20260505_180014.json`.

| Candidate | Parse outcomes | References | Agent trigger rate | Projected review items | Measured cost |
|---|---:|---:|---:|---:|---:|
| `anthropic:claude-opus-4-7` | 5/5 ok | 6 | 1.0 | 4 | `$0.201802` |
| `anthropic:claude-opus-4-6` | 5/5 ok | 6 | 0.8 | 4 | `$0.233970` |
| `openai:gpt-5.5` (`gpt-5.5-2026-04-23`) | 5/5 ok | 6 | 0.8 | 4 | `$0.292948` |

Decision: keep Opus 4.7 as the default extraction model. Opus 4.6 matched the same aggregate pipeline metrics but cost more in this run because it did not receive Anthropic cache-hit accounting. GPT-5.5 parsed cleanly and matched aggregate counts, but was slower and more expensive than Opus 4.7 on this prompt/fixture set.

**Prompt cache validation.**
- Today's `_cacheable_system_blocks` ([extraction.py:210-219](../../src/tcg_pipeline/news/extraction.py)) emits `cache_control: ephemeral` per system block. With the glossary removed, the cache write becomes ~1k tokens instead of ~103k. Cache writes effectively become free; cache hits become near-free. The cache infrastructure stays in place but its cost contribution is negligible after this change.
- Cross-provider note: GPT-5.4 has different cache semantics than Anthropic's prompt cache. Now that the cache write is small, cache-discount asymmetry between providers matters much less.

### 5.1.1 Semantic field interpretation layer (Stage 2 / shared)

AGENT.2 adds a shared semantic field interpretation layer so TCG field semantics are not buried permanently inside the general news extractor. The layer converts observed source facts/language into canonical evidence fields with reason codes, confidence, source anchors, and `requires_review` flags. Initial interpreters: `pipeline_status`, `product_type`, `age_restriction`, `date_delivery`, and unit buckets including `workforce_units`.

**Interface sketch.**
```python
def interpret_semantic_field(
    observation: SemanticFieldObservation,
    *,
    profile: SourceProfile,
    field_name: str,
    project_context: ProjectContext | None = None,
) -> SemanticFieldInterpretation:
    ...
```

`SemanticFieldObservation` is source-shaped but normalized enough to be shared: observed text or structured source facts, source type, article/reference identifiers when applicable, offsets/snippets, source-native field names if already known, and optional current project field values. `SemanticFieldInterpretation` returns the canonical field value, confidence, reason code, evidence type or interpretation type, supporting excerpt/structured anchor, and `requires_review`.

Each source profile owns `SemanticInterpreterProfile` entries declaring the deterministic rule tables, whether LLM fallback is allowed, which capability/model config key the fallback uses, and the maximum context shape for that source. This keeps the runner generic while making source-specific field semantics explicit and testable.

**Initial field scope.**
- **`pipeline_status`:** maps article/permit language to TCG status. Example: conference-level idea/first mention → `Conceptual`; concrete application/planning/design-review activity → `Proposed`/`Pending` per TCG definitions; permit issuance → `Approved`; recent substantive inspection → `Under Construction`; CofO → `Complete`.
- **`product_type`:** maps language such as apartment, condo, townhome, single-family, micro/co-living, and care-based senior living. "55+ apartments" remains `Apartment`; assisted living / memory care / skilled nursing / CCRC should not be collapsed silently into ordinary apartments.
- **`age_restriction`:** maps 55+, 62+, senior, active-adult, student, university housing, and non-age-restricted language independently from product type.
- **`date_delivery`:** interprets projected timing language into normalized dates with explicit reason codes. Example: "end of 2026" → a documented normalized midpoint/date convention such as `2026-12-15`; "mid-2027" and "Q3 2027" use similarly documented conventions. The raw text stays anchored so reviewers can see the projection source.
- **Unit buckets:** interpret total/affordable/workforce/market-rate counts as distinct components. Workforce units are not affordable units and are not market-rate units.

**News behavior.**
- Default `extract_v2` continues to emit `candidate_status_signal` during AGENT.1 so the A/B harness can spot-grade status quality without adding another moving part.
- In AGENT.2, news references run through semantic interpreters before writing canonical evidence for the scoped fields above. For straightforward phrases, interpreters can use deterministic phrase/rubric rules. For article language that is semantic or ambiguous ("floated the idea", "plans are taking shape", "work appears underway", "senior living community", "completion by the end of 2026"), the profile may invoke a compact LLM prompt whose cached system context is only the relevant TCG field rubric.
- The interpreters see only the relevant reference text/snippets and structural leads, not a batched list of full articles. This preserves per-project attribution for multi-project articles and keeps audit anchors precise.

**Permit behavior.**
- Permits use deterministic mapping first: application/filing events map to early-stage statuses per source-profile rules, building permit issuance maps to `Approved`, recent substantive inspection maps to `Under Construction`, CofO maps to `Complete`, and structured permit/source fields map product/unit fields when reliable.
- LLM/agent interpretation is reserved for ambiguous permit descriptions, conflicting source signals, or cross-stream exceptions. Structured permit rows should not pay LLM cost for cases the rule table can map reliably.

**A/B scope.** The AGENT.1 model A/B remains a default-extraction test. Its spot-grade must explicitly include semantic field correctness for status, product type, age restriction, delivery date projection, and unit bucket extraction, but the separate semantic interpreters are evaluated in AGENT.2 with focused fixtures once extraction-model choice is settled. Do not default this layer to Opus 4.7 without measurement; these are narrow classification/explanation tasks and may be suitable for the selected extraction model or a cheaper model.

### 5.2 Output-quality retry path (Stage 2)

Replaces today's Pass 3a output-quality branch.

**Triggers.** Extraction returned `parse_error`, `schema_invalid`, `refused`, or `truncated`.

**Behavior.**
- Same model as the failing extraction.
- New prompt template `extract_retry_v1` with:
  - Strong "output strict JSON, no preamble" instruction.
  - For `truncated`: instruction to be more concise per reference; raise effective `max_tokens` cap.
  - For `refused`: rephrased framing (no policy-flag-likely content; the article is public news).
  - For `parse_error` / `schema_invalid`: include the parser's error text and the prior model's bad output as context.
- Up to 2 retries. If both fail, escalate to review queue with the legacy "extraction quality failure" payload.
- Records as `pass='extract_retry'` rows in `news_extractions` (new pass enum value) and as `capability='extract_retry'` in `llm_cost_usage` so retry fire rate and spend are directly queryable.
- Cost-cap-bounded; reuses existing `reserve_llm_cost`/`record_llm_cost`.

**What it does NOT do.** No tool dispatch. No reasoning trace. This is the cheap path.

**Implementation note (2026-05-06).** The active retry path is `extract_retry_v1`, wired after the initial default extraction persists a parse/schema/refusal/truncation failure and before any legacy Pass 3a output-quality path can run. Successful retries replace the article's `current_extraction_id`; exhausted retries preserve deterministic failure handling and do not create `agent_runs` rows.

### 5.3 Agent runner (Stage 2 — source-agnostic from day one)

The genuinely new component. **Built once, source-agnostic.** News and permits are the first two consumers in the sprint; CoStar and Pipedream plug in later via source profiles (§5.9) without runner changes.

**Interface.**
```python
def run_agent_for_intake(
    intake: IntakeRecord,                       # source-agnostic envelope, see below
    *,
    matcher_results: list[MatchResult],         # deterministic matcher output (source-shaped, normalized to a base type)
    trigger_reasons: list[AgentTrigger],        # which trigger(s) fired
    profile: SourceProfile,                     # declares allowed tools, prompt template, cap bucket, kill switch
    client: AgentClient | None = None,          # dependency-injected LLM/tool-loop client
    produced_review_item_ids: list[uuid.UUID] | None = None,
    settings: Settings | None = None,
    session_factory: sessionmaker | None = None,
    now: datetime | None = None,
) -> AgentRunResult:
    ...

@dataclass(frozen=True)
class IntakeRecord:
    source_type: str                  # "news_article", "ladbs_permit", "costar", "pipedream", future...
    intake_record_id: str             # source-specific identifier (article_id, permit_number, costar_property_id, ...)
    extraction_id: uuid.UUID | None   # the default-extraction output, when applicable (news only today)
    payload: dict                     # source-specific structured fields the agent reasons over
```

The runner is the same code regardless of source. What varies is the `SourceProfile`: which tools the agent can call, which system prompt frames the task, which cost-cap bucket the run charges, which kill switch gates execution.

**Tool dependency contract.** The runner builds an `AgentRunRequest` for the client and tools. That request carries the resolved `session_factory` and `settings`; tool handlers read those dependencies from the request rather than closure-binding DB access at each registration site. This keeps the tool signature source-agnostic while letting DB-backed tools query state.

**News payload contract.** For news, `intake.payload` is lean structured context only: title, URL, source slug, published date, extracted references, and compact matcher verdict summaries. It must not carry the full article body. Full body, accepted article chunks, registry state, and nearby project context are fetched on demand through tools so the base user message stays small across multi-turn loops.

**Implementation slice (2026-05-05).** `src/tcg_pipeline/agents/` now contains the source-agnostic runner skeleton and profile registry. The runner validates triggers/source type and profile-required intake fields, honors profile kill switches, reserves/trues-up daily cost under profile capability keys such as `agent.news_v1`, persists terminal `agent_runs` rows for killed-by-switch, failed-budget, failed-timeout, failed-error, and injected-client success paths, and links produced review items through `agent_run_review_items`. The Anthropic tool-loop client shell and bounded tool registry now exist. First news integration wiring is intentionally narrow: `new_candidate` references can call the agent for Type 1 `promote_existing_project`; `possible` references with deterministic candidate IDs can call the agent for Type 3 `confirm_existing_project`, constrained to the matcher-provided candidate list. No-change, escalation, invalid revision payloads, and failures preserve deterministic review fallback.

**Runner loop (pseudocode).**
```
budget = AgentRunBudget(max_tool_calls=15, max_cost_usd=5.00, max_wallclock_seconds=300)
context = build_initial_context(article, extraction, matcher_results, trigger_reasons)
trace = ReasoningTrace()

while not budget.exhausted():
    response = opus.messages.create(
        model="claude-opus-4-7",
        system=AGENT_SYSTEM_PROMPT,        # cacheable
        messages=context.messages,
        tools=AGENT_TOOLS,
        max_tokens=4000,
    )
    budget.record(response.usage)

    if response.stop_reason == "tool_use":
        for tool_use in response.tool_uses:
            tool_result = dispatch_tool(tool_use)
            context.append_tool_result(tool_use.id, tool_result)
            trace.record_tool_call(tool_use.name, tool_use.input, tool_result.summary)
        continue

    if response.stop_reason == "end_turn":
        # agent emitted final structured decision
        return parse_agent_decision(response, trace, budget)

    if response.stop_reason in ("max_tokens", "refusal"):
        return AgentRunFailure("output_quality_failure", trace, budget)

return AgentRunFailure("budget_exhausted", trace, budget)
```

**Per-run cost cap.** Hard ceiling $5.00 (proposal's suggestion), but normal-case target much lower — see §9. The runner reserves this amount before the client call and performs a post-hoc priced-usage check after the call. If actual usage exceeds the per-run cap, the runner records the actual cost, writes a terminal `failed_budget` audit row, raises a `SystemAlert`, and leaves the deterministic result standing.

**Wallclock cap.** 300 seconds per agent run. Latency is not a constraint for researchers; the cap exists only to bound runaway agent loops. The runner owns timeout enforcement around the client call; timeout writes `outcome='failed_timeout'`, releases the reservation, and leaves deterministic output standing.

**Tool-count cap.** The runner owns a final sanity check that `len(tool_calls_summary) <= profile.max_tool_calls`, even though individual tools also enforce §5.4.1 output budgets internally. If the client exceeds the profile cap, the runner records the incurred cost, writes `outcome='failed_error'`, and leaves deterministic output standing.

**Worker model — one job per article (Q17 / R8 decision).** Stage 2 splits today's "one scrape job ingests N articles end-to-end" pattern into two job kinds:
- `news_scrape` — the existing compatibility job kind. It runs discovery + fetch + Pass 1 + triage + default extraction for all articles in a scheduled batch, then preflights whether each article needs agent escalation. This was called `news_scrape_discovery` in the design sketch, but the implementation keeps `news_scrape` so existing scheduler/admin references do not churn.
- `news_agent_integrate` — one job per article that needs agent escalation. Each job loads the prior extraction state, runs the agent loop with up to 300s wallclock, and writes integration outputs. With one article per job, the per-job timeout never has to accommodate a batched agent run.

This split is the cleanest fit for the synchronous-dispatch commitment in §0.1: each agent job is a self-contained synchronous unit, easy to retry on failure, and trivially convertible to async batch dispatch later if §0.1 is revisited. It also matches Q7's safe-state — a single job's failure doesn't take down the discovery batch.

The discovery job enqueues integration jobs after Pass 2 completes; integration jobs run concurrently up to RQ worker count. There is at most one active `news_agent_integrate` child row per article. If a parent scrape retries, it reuses the active child row instead of creating duplicate child jobs; if the child row exists only because Redis was unavailable during enqueue, a later parent retry may enqueue that same row. Child completion increments the parent `source_runs.new_matches` with an atomic SQL update so concurrent children cannot lose counts. Cost cap and reservation logic stay unchanged because they're already keyed per-extraction-call, not per-job.

**Failure handling (Q7).** Agent-loop failure modes return `AgentRunFailure`. Caller (integration path) treats as "agent didn't run successfully; fall back to deterministic verdict" and attaches the failure mode to the review item. Catastrophic worker-level failures that happen before integration starts are operational failures, not semantic decisions: the `news_agent_integrate` scrape job is marked failed and retryable/visible to ops, and no deterministic fallback review item is written outside the integration path.

**Dispatch interface (per §0.1).** Uses an `ExtractionDispatcher` abstraction so a future Batch API path can land cleanly. The agent runner specifically stays synchronous in the foreseeable future even if the default extraction migrates to Batch.

### 5.4 Agent tools — core and source-specific

Tools are categorized so each source profile can declare exactly which subset its agent runs may call. The runner enforces the subset; tools outside the profile are not visible to the agent.

**Implementation slice (2026-05-05).** `AgentToolRegistry` exposes only profile-allowed tools, dispatches registered handlers, enforces per-tool output budgets with truncation metadata, and records compact call summaries. `AnthropicAgentClient` loads the profile system prompt, calls Anthropic Messages with tool specs, feeds tool results back into the loop, aggregates usage across turns, and parses the final structured JSON decision. The first DB-backed tools are live: `get_project_state` reads `Project`, `project_field_resolution`, `project_latest_evidence`, and referenced evidence metadata through `request.session_factory`; `search_projects` performs a bounded registry lookup by article-observed address/name/developer so `new_candidate` rows without matcher candidate IDs can still produce auditable project IDs before promotion; `search_articles_similar` embeds a query and searches accepted `news_article_chunks`; `get_article_body` fetches stored article text after search narrows to an article. `search_articles_similar` supports a `published_after` freshness filter and returns matched project/evidence IDs when the accepted reference was already attributed. If an accepted-new reference produced evidence but did not update `news_project_references.matched_project_id`, retrieval falls back to the evidence row's project/evidence IDs so accepted chunks can still lead the agent into `get_project_state`.

Registry truncation is the last-resort safety net. Real tools should self-limit first (top-K, compact row summaries, explicit `total_results`) so the agent still receives useful partial results rather than a generic truncation notice.

**Tool-internal cost accounting.** Researcher decision 2026-05-05: ignore `search_articles_similar` query-embedding cost as negligible. It intentionally does not reserve or record `llm_cost_usage` rows. Future non-negligible tool-internal model calls, such as reranking, must make an explicit accounting decision before shipping. Pure DB tools such as `get_project_state` and `get_article_body` have no extra LLM spend.

#### Core tools (always available, regardless of source)

- **`get_project_state(project_id)`** — Reads `project_field_resolution` (DISTINCT ON view) for current resolved values + provenance + confidence. Joins to `Project` for non-resolved fields (canonical_address, lat/lng, project_name).
- **`search_projects(query_text?, address?, project_name?, developer?)`** — Bounded registry search returning compact project IDs and match reasons from article-observed address/name/developer context. The agent must call `get_project_state` before using a returned project for `promote_existing_project`.

- **`get_recent_evidence(project_id, since_date=None, limit=10)`** — Queries `evidence` table (active rows via `superseded_at IS NULL`) for the project, ordered by `evidence_date DESC`. Returns source_type, source_tier, evidence_date, key extracted_fields, notes.

- **`get_developer_projects(developer_id, limit=30)`** — Reads `Project` rows where `developer` matches the canonical name OR an alias from `developer_alias`, ordered by `last_evidence_date DESC`. Returns project metadata snapshot.

- **`get_nearby_projects(coordinates_or_address, radius_miles=0.25)`** — PostGIS `ST_DWithin` against `Project.location` using the new GIST index. If input is an address, geocodes via the existing Geocodio-first/Esri-fallback geocoder. Returns project_id, name, developer, status, last_updated, **distance_feet**, recent_evidence_summary, product_type. Radius parameter in miles, per-candidate distance in feet — agent reasons over distance to decide same-site vs nearby-but-distinct.

- **`escalate_to_review(reason, candidate_changes)`** — Agent's explicit "I want a human" signal. Writes a review item with the agent's reasoning trace and proposed-but-not-applied changes.

#### News-specific tools

- **`search_articles_by_project(project_id, limit=20)`** — Joins `news_project_references` → `news_articles`, filters to references where `match_status = confirmed` AND associated review decision is `accept` (or auto-applied). Returns article URL, title, published_at, key extracted fields.

- **`search_articles_similar(query_text, published_after=None, include_whole_article_chunks=false, limit=10)`** — pgvector cosine similarity over the article-chunk embedding index. Filters to per-reference-accepted entries (per Q13); optional `published_after` ISO date/datetime keeps recall fresh when stale articles are less useful than recent corroboration. Returns top-K with similarity score, article metadata, accepted reference fields, and matched project/evidence IDs when available.

- **`get_article_body(article_id, max_chars=6000)`** — Fetches stored `news_articles.body_text` for a specific article after chunk search narrows candidates. Returns metadata + body excerpt only; no raw HTML. Hard `max_chars` cap 12000.

#### Permit-specific tools (Stage 3 / AGENT.3)

- **`get_permits_for_parcel(parcel_id)`** — Reads `evidence` rows where `source_type LIKE 'ladbs_%'` AND raw_data references the given APN, regardless of which project they're attributed to. Returns permit_number, permit_type, issue_date, work_description, valuation, applicant.

- **`get_permits_for_project(project_id)`** — Reads `evidence` rows where `project_id = ?` AND `source_type LIKE 'ladbs_%'`. Returns same shape as above, filtered to a known project.

- **`get_articles_about_parcel_or_address(parcel_or_address, radius_feet=300)`** — Cross-stream: from a permit context, find news articles about projects within `radius_feet` of the permit's address, OR articles whose `candidate_address` normalizes to the permit's APN/address.

- **`get_permits_for_parcel_or_address(parcel_or_address, radius_feet=300)`** — Cross-stream: from a news context, find permits filed against parcels within `radius_feet` of the article's address.

#### 5.4.1 Tool output budgets

Per-run cost cap is meaningless if tools dump unbounded context back into the agent loop. Every tool returns a **compact summary**, not raw evidence rows or article bodies. Hard token budget per tool:

| Tool | Output budget | Truncation behavior |
|------|--------------:|---------------------|
| `get_project_state` | ≤1500 tokens | Returns resolved values + provenance + confidence. No raw evidence. |
| `get_article_body` | ≤3500 tokens | Fetches stored body text for one article after chunk search narrows candidates. Defaults to 6000 chars, hard cap 12000 chars, no raw HTML. |
| `get_recent_evidence` | ≤1500 tokens | Top 10 rows by default. Each row summarized to ≤150 tokens (source_type, evidence_date, key extracted_fields, ≤80-char notes excerpt). |
| `search_articles_by_project` | ≤2000 tokens | Top 20 articles. Each entry: URL, title, published_at, ≤100-char extracted-summary line. |
| `search_articles_similar` | ≤2500 tokens | Top 5 matches by default, hard cap 10. Each entry: similarity score, URL, title, published_at, matched project/evidence IDs when available, ≤200-char excerpt. Optional `published_after` narrows stale-heavy indexes. |
| `get_developer_projects` | ≤1500 tokens | Top 30 projects. Each entry: project_id, name, status, last_evidence_date, total_units. |
| `get_nearby_projects` | ≤2000 tokens | Top 20 results within radius. Each entry: project_id, name, developer, status, **distance_feet**, last_updated, ≤80-char recent-evidence summary. |
| `get_permits_for_parcel` | ≤1500 tokens | Top 20 permits. Each entry: permit_number, type, issue_date, ≤80-char work_description excerpt, valuation. |
| `get_permits_for_project` | ≤1500 tokens | Same as above, scoped to one project. |
| `get_articles_about_parcel_or_address` | ≤1500 tokens | Top 15 articles, distance + 1-line summary each. |
| `get_permits_for_parcel_or_address` | ≤1500 tokens | Top 15 permits, distance + permit-type + 1-line summary each. |
| `escalate_to_review` | N/A | Write-only; no output to agent. |

**Truncation contract.** When a tool's natural result exceeds its budget, the response payload includes a `truncated: true` flag and a `total_results: N` count, plus a hint: `"results omitted; refine query (e.g., narrower radius, more specific developer name) to see more."` The agent can decide to widen the query or accept the truncation. **Tools never silently drop results.**

**Why this matters.** Per-run cost cap ($5 hard) is bounded only at the LLM-API level. If `get_recent_evidence` returns 50 rows × 500 tokens each = 25k input tokens for the agent's next turn, the per-run cost budget is consumed by tool returns rather than reasoning. Hard tool-output budgets keep the agent's effective context window for actual reasoning.

**Geocoding consistency.** Both stream types must use the same geocoder. Today both paths (manual project creation, news matcher) use Geocodio-first/Esri-fallback. Stage 3 cross-stream tools rely on this consistency; if it ever drifts, cross-stream queries will miss legitimate matches.

### 5.5 Contradiction detection (Stage 2 — Q5)

#### 5.5.0 Impact-assessment deliverable (sprint pre-work, Claude Code-owned)

Before any C.i code is touched in the sprint, Claude Code produces a structured impact-assessment document at `docs/specs/ci_contradiction_impact_assessment.md`. This is research work, not coding work. It precedes the rewrite.

Authoring rules for the assessment (these are firm):

- **Keep all output very concise and to the point.** A researcher needs to read this before the rewrite starts. Don't bury the signal. Bullet-point tables and short paragraphs only — no narrative prose, no "background" sections, no restating things already in this design doc.
- **Explicitly mark any line where human review is extra critical.** Use a `⚠ HUMAN REVIEW` prefix on rows where Claude Code's analysis depends on assumptions a researcher should check (e.g., "I'm inferring that this caller doesn't read `contradicted_override_id` based on a grep with no hits — please confirm no dynamic field access exists"). The reviewer should be able to scan for these markers and focus their attention there.
- **Flag uncertainties as uncertainties.** If Claude Code finds a dependency it's not sure about, the assessment lists it under an "Open uncertainties" section and surfaces it to the researcher before the rewrite begins. Don't gloss.
- **Do not begin the rewrite until the researcher has read the assessment** (does not need to formally sign off — a "read it, looks fine" is enough). Researcher review is a 10-minute scan, not a thoroughness audit.

The assessment must enumerate the items in §5.5.1 below. Format: one row per dependency, columns `Path | Today's contract | Change under new flow | Migration | ⚠ HUMAN REVIEW marker`.

Today: `detect_project_contradictions` runs post-resolve, only against active researcher overrides ([review/contradictions.py](../../src/tcg_pipeline/review/contradictions.py)).

Stage 2: agent owns contradiction reasoning. Two cases:

**Case A — Pre-resolve, article-vs-current-state.** Triggered when extraction's reference fields imply >10% unit delta, status regression, or developer mismatch vs current project state for a deterministic-confirmed match. Agent reads project state + recent evidence and decides:
- Article is plausible → write evidence, let resolver update fields, no contradiction item.
- Article is suspect → downgrade match to `possible` (Q6 type 2), write evidence as orphan, escalate to review with "matcher confirmed, agent downgraded — here's why."

**Case B — Post-resolve, newer-evidence-vs-override.** When new evidence would contradict an active researcher override post-resolve. Today this fires `detect_project_contradictions`. Stage 2: agent runs at integration time and reasons about the override before evidence is committed. If agent thinks the new evidence is correct and the override is stale, it surfaces this in the review item with reasoning. If agent thinks the override should hold, it still writes the evidence (the override is review-protected, not silent) but the review item's reasoning helps the researcher decide.

**Multi-alternative `proposed_value` (researcher decision 2026-05-04).** Agent-produced contradiction items use `payload.proposed_alternatives: list[{value, source_evidence_id, source_summary, agent_confidence}]` instead of today's single `proposed_value` field. The agent's best guess is first; competing values from different sources follow. When the reviewer picks alternative N via the decision card, the resulting `researcher_overrides` row records the chosen alternative's source attribution (not generic "user override"). STATUS_CHANGE items keep the single-value `proposed_value` shape — multi-alt is OVERRIDE_CONTRADICTION-only. Decision-card UI renders alternatives compactly using hover-revealed source detail. See [`ci_contradiction_impact_assessment.md`](ci_contradiction_impact_assessment.md) §I.1.

**Distinct actor label (researcher decision 2026-05-04).** Agent-produced contradictions log to the Changes tab under `"Agent contradiction detection"`, distinct from today's `"Contradiction detection"` used by the deterministic fallback path. Researchers can scan the Changes tab and see at a glance whether a contradiction event was agent-driven or fallback-driven. See [`ci_contradiction_impact_assessment.md`](ci_contradiction_impact_assessment.md) §I.4.

**Fallback.** When no agent ran (deterministic match with no agent triggers), the existing post-resolve `detect_project_contradictions` continues to run as a safety net for override contradictions only.

**Override semantics preserved.** The skip-list mechanism (`skip_contradiction_review_item_ids`) prevents re-detection of the *same* contradiction in the *same* commit transaction. It does not make overrides sticky. New evidence arriving later still flows through normal contradiction detection and produces a new review item — review-protected override semantics per `EVIDENCE_LAYER_DECISIONS.md` §22. See [`ci_contradiction_impact_assessment.md`](ci_contradiction_impact_assessment.md) §I.5.

**System-authored regression override exception (2026-05-12).** High-confidence
`status_regression_candidate` auto-accepts may write a system-authored
`pipeline_status` override with actor `agent.status_regression_candidate`.
These rows are distinct from researcher-authored overrides: they may yield to
newer evidence without creating an override-contradiction review item, and the
supersession is recorded in `resolution_log.metadata`. See
`EVIDENCE_LAYER_DECISIONS.md` §22.11. Researcher-authored overrides keep the
review-protected semantics described above.

### 5.5.1 What the impact assessment must enumerate

C.i shipped on 2026-04-27 — extremely recent at time of this design (2026-05-04). Per researcher direction, the rewrite proceeds without a feature flag; the §5.5.0 assessment is the mitigation. The assessment must enumerate, at minimum:

**Callers of `resolve_project(apply=True)` that trigger contradiction detection:**
- `db/collect.py` — scheduled-collector path after evidence write.
- `news/integration.py` — news article integration.
- `db/review_workflow.py` — review-decision commit path.
- FastAPI override set/clear endpoints in `src/tcg_pipeline/api/`.
- FastAPI direct-field write endpoints (Identity edits and Core overrides).
- `canonicalize-developers --apply` CLI.
- The `detect-contradictions` CLI in admin/audit mode.

**Code that reads contradiction outputs:**
- Review queue list rendering — backend `/review/queue` endpoint and the frontend `/review` page that filters and counts by item_type.
- Review detail page — `/review/[itemId]` and the `lib/review/payload.ts` helpers that interpret contradiction payloads.
- Decision card rendering (C.tail.11/12) — the consolidated card UI assumes specific contradiction payload shape.
- Coverage / Dashboard — counts of `override_contradiction` items by jurisdiction.

**Schema columns that must continue to mean what they mean today:**
- `review_items.contradicted_override_id` — FK to `researcher_overrides`.
- `review_items.contradiction_priority`.
- `review_items.field_name` and `review_items.winning_evidence_id` — used by decision-card consolidation.
- `review_items.payload.evidence_ids` — the consolidated supporting/dissenting evidence list.

**Audit / backfill tooling:**
- `scripts/collapse_duplicate_review_items.py` — assumes contradiction-item shape.
- Any future eval tooling that scans for contradiction acceptance rates.

**Tests:**
- All `tests/test_*contradiction*.py` and any contradiction case in integration tests for collect/news/review.

For each item, the assessment must answer: **does the new agent-led flow change the contract this caller depends on?** If yes, list the specific change and its migration. If no, document why it's safe.

### 5.5.2 Regression coverage and migration discipline

- Every C.i contradiction case in the current test suite must continue to pass under the agent-led flow. Add new tests for agent-run contradiction cases.
- Production-data spot checks before cutover: pull the current production set of active `override_contradiction` review items, simulate the new flow against the same input data, confirm the new flow produces equivalent or strictly more useful output (with reasoning trace).
- The fallback path (post-resolve `detect_project_contradictions` when no agent ran) is load-bearing for non-news writes that don't trigger the agent. It cannot be removed in Stage 2; only the news-integration path's contradiction detection is moved into the agent.

### 5.6 Evidence schema extensions (Stage 2)

Two implementation options for storing agent metadata:

**Option A — Columns on `news_extractions`.**
- Add `reasoning_trace TEXT`, `evidence_consulted JSONB`, `tool_calls_summary JSONB` to `news_extractions`.
- New `pass` enum value: `agent`.
- Pros: single audit table for all extraction outputs.
- Cons: `evidence_consulted` could be large; `news_extractions` rows then carry that weight.

**Option B — Separate `agent_runs` table.**
- New table `agent_runs` with FK to `news_extractions` (or to `evidence` directly).
- Stores `reasoning_trace`, `evidence_consulted`, `tool_calls_summary`, plus run metadata (budget consumed, tools invoked, success/failure).
- Pros: keeps `news_extractions` lean; supports cold-storage tier for old `evidence_consulted` data.
- Cons: extra join in UI render path.

**Recommendation: Option B, with a source-agnostic key shape and full observability from day one.** The audit log nature of agent runs argues for separation. Observability is the safety mechanism if the sprint moves fast — every field below is populated for every run.

```sql
CREATE TABLE agent_runs (
    id                            UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Identity / linkage
    intake_source_type            TEXT NOT NULL,           -- "news_article", "ladbs_permit", "costar", "pipedream", future...
    intake_record_id              TEXT NOT NULL,           -- source-specific identifier as string; for news, stringified news_articles.id
    intake_extraction_id          UUID REFERENCES news_extractions(id) ON DELETE SET NULL,  -- nullable; news-only today
    project_id                    UUID REFERENCES projects(id) ON DELETE SET NULL,  -- the project this run reasoned about, if any
    source_run_id                 UUID REFERENCES source_runs(id) ON DELETE SET NULL,  -- which source_run produced the intake
    scrape_job_id                 UUID REFERENCES scrape_jobs(id) ON DELETE SET NULL,  -- the worker job that triggered this run

    -- Profile / trigger context
    profile_name                  TEXT NOT NULL,           -- "news_v1", "permit_v1", future...
    profile_version               TEXT NOT NULL,           -- e.g., "1.0.0" — bumps with prompt or tool changes
    triggered_by                  JSONB NOT NULL,          -- list of triggers that fired (one or more): ["pass1_pass2_conflict", "low_confidence", ...]

    -- Model / call metadata
    provider                      TEXT NOT NULL,           -- "anthropic", "openai", future
    model                         TEXT NOT NULL,           -- "claude-opus-4-7", future
    prompt_version                TEXT NOT NULL,           -- agent system prompt version at run time
    input_tokens_uncached         INTEGER NOT NULL,
    input_tokens_cache_creation   INTEGER NOT NULL,
    input_tokens_cached           INTEGER NOT NULL,
    output_tokens                 INTEGER NOT NULL,
    cost_usd                      NUMERIC(10, 6) NOT NULL,
    latency_ms                    INTEGER NOT NULL,

    -- Decision content
    reasoning_trace               TEXT,                    -- ≤500 chars target, surface in UI
    evidence_consulted            JSONB NOT NULL DEFAULT '[]'::jsonb, -- list of {source_type, record_id, role}
    tool_calls_summary            JSONB NOT NULL DEFAULT '[]'::jsonb, -- list of {tool, args_summary, result_summary, latency_ms, output_token_count}
    matcher_original_verdict      JSONB,                   -- deterministic matcher's verdict at run start
    agent_revised_verdict         JSONB,                   -- agent's final decision (Q6 type 1/2/3, or "no_change", or "escalated")

    -- Outcome
    outcome                       TEXT NOT NULL,           -- "completed", "escalated", "failed_timeout", "failed_budget", "failed_error", "killed_by_switch"
    error_text                    TEXT,                    -- populated when outcome starts with "failed_"
    budget_consumed_usd           NUMERIC(10, 6) NOT NULL, -- duplicate of cost_usd for budget-tracking queries
    tool_calls_count              INTEGER NOT NULL,
    wallclock_seconds             INTEGER NOT NULL,

    -- Lifecycle timestamps
    started_at                    TIMESTAMPTZ NOT NULL,    -- when the runner began this run
    completed_at                  TIMESTAMPTZ NOT NULL,    -- terminal-row timestamp; every current outcome is terminal
    created_at                    TIMESTAMPTZ NOT NULL DEFAULT now()  -- row insertion time; equals started_at in the common path
);

ALTER TABLE agent_runs
  ADD CONSTRAINT ck_agent_runs_triggered_by_nonempty_array
  CHECK (jsonb_typeof(triggered_by) = 'array' AND jsonb_array_length(triggered_by) > 0),
  ADD CONSTRAINT ck_agent_runs_evidence_consulted_array
  CHECK (jsonb_typeof(evidence_consulted) = 'array'),
  ADD CONSTRAINT ck_agent_runs_tool_calls_summary_array
  CHECK (jsonb_typeof(tool_calls_summary) = 'array');

CREATE INDEX ix_agent_runs_intake ON agent_runs (intake_source_type, intake_record_id);
CREATE INDEX ix_agent_runs_project ON agent_runs (project_id) WHERE project_id IS NOT NULL;
CREATE INDEX ix_agent_runs_profile_outcome ON agent_runs (profile_name, outcome, created_at DESC);
CREATE INDEX ix_agent_runs_source_run ON agent_runs (source_run_id) WHERE source_run_id IS NOT NULL;
CREATE INDEX ix_agent_runs_created_at ON agent_runs (created_at DESC);

-- Agent-to-review-item link (one agent run can produce multiple review items)
CREATE TABLE agent_run_review_items (
    agent_run_id     UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    review_item_id   UUID NOT NULL REFERENCES review_items(id) ON DELETE CASCADE,
    PRIMARY KEY (agent_run_id, review_item_id)
);
CREATE INDEX ix_agent_run_review_items_review_item ON agent_run_review_items (review_item_id);
```

**Why a join table rather than a column on `review_items` (revised 2026-05-04 per senior-developer feedback).** A single agent run can produce multiple review items — e.g., the agent reasons about an article and emits both a `STATUS_CHANGE` for `developer` and a `STATUS_CHANGE` for `total_units`, plus an `OVERRIDE_CONTRADICTION` if the project had a researcher override. A nullable `review_items.agent_run_id` column would force a 1:1 model that doesn't match reality. The join table is the source of truth. `payload.agent_run_id` may stay as a denormalized hint for rendering (avoids a join for every review-list query), but read-side queries that need accurate "which review items came from this run" or "which run produced this review item" go through `agent_run_review_items`.

**Why every field matters from day one (researcher direction 2026-05-04, senior-developer feedback).** If the sprint moves fast, observability is the safety mechanism. We need to be able to answer "what did the agent do, what did it cost, how long did it take, what did it consult, what did it decide, what failed" in a single SQL query without joining across tables. Per-field rationale:
- `provider` / `model` — model A/B and model-swap audit; required for cost reconciliation across providers.
- `profile_version` / `prompt_version` — eval gating depends on version-tagged decisions.
- `triggered_by` as a list — agent runs can fire from multiple triggers simultaneously; single-string framing loses information.
- Token-count breakdown — cache-hit tracking, cost reconciliation, prompt-cache regression detection.
- `latency_ms` / `wallclock_seconds` — operational health monitoring, R8 worker-timeout guardrail.
- `source_run_id` / `scrape_job_id` — drill-through from cost dashboards to the specific source run / job.
- `tool_calls_count` — quick cost-anomaly detection ("agent ran 14 tools on one article").
- `error_text` — failure-mode triage without needing log scraping.

The key shape `(intake_source_type, intake_record_id)` is source-agnostic by design. News, permits, CoStar, Pipedream, and any future source share the same table. For news, the AGENT.2 convention is `intake_record_id = str(news_articles.id)`; runners should not substitute extraction IDs or URL hashes.

Legacy reextractions (Q16) get synthetic rows in `news_extractions` per today's schema; the `agent_runs` table simply has no rows for them. UI renderer treats absence of an `agent_runs` row as "legacy or default extraction" and falls back to today's render.

### 5.7 Retrieval infrastructure (Stage 1 + Stage 2)

**Article embedding pipeline (AGENT.1 build, AGENT.2 use).**

**Implementation status (2026-05-05).** The AGENT.1 indexing code is implemented in
`src/tcg_pipeline/news/embeddings.py`, exposed by `tcg-pipeline news index-articles`, and
wired to the `news_backfill_chunk` worker kind. The AGENT.1 migration was applied to the
single production Supabase database after a logical backup. Production smoke indexed one
accepted Urbanize reference into one per-reference chunk plus one whole-article chunk
(`883` embedding input tokens, `$0.000018`) and an immediate rerun skipped both unchanged
chunks with zero API calls.

**Per-reference indexing contract (concrete, revised 2026-05-04 — actual decision-type names verified against [db/review_workflow.py:81-85](../../src/tcg_pipeline/db/review_workflow.py#L81)).**

A reference enters the embedding index iff **any** of the following gates fires:

**Gate 1 — Committed-accept review.** All three are true:
1. The reference's associated `news_project_references.review_item_id` resolves to a `review_items` row.
2. `review_items.state = 'committed'`.
3. The latest committed `review_decisions` row for that item has `decision_type = 'accept_new' OR decision_type LIKE 'candidate_%'` — i.e., the researcher accepted the article's claim (either as proposed by the agent/system, or by selecting one of the candidate alternatives in the multi-alt schema; candidate decision types take the form `candidate_<index>` like `candidate_0`, `candidate_1`, …). Decisions of `keep_old` or `custom` reject the article's claim and do NOT pass this gate.

**Gate 2 — Auto-applied confirmation (NEW).** The news integrator confirms a match deterministically and writes evidence without producing a review item (because the article corroborates existing state and no field actually changed). At integration time, an audit row is written to `news_reference_auto_applied(article_id, reference_index, source_run_id, applied_at)`. The embedding pipeline reads this audit table and indexes those references. **This gate is what allows confirmed-no-review references — typically the most reliable signal — to enter the retrieval index.**

**Gate 3 — Auto-applied via D.late.A high-confidence policy** (post-D.late.A). When the future high-confidence auto-apply policy ships, references it auto-applies are indexed via the same `news_reference_auto_applied` audit row, with a marker distinguishing the gate-3 auto-apply path from the gate-2 corroborating-evidence path.

**References that never enter the index:** rejected via `keep_old` or `custom`, deferred, never reached committed state, or matched as `discarded` by the matcher.

**Edge case - whole-article retrieval context.** The indexer emits one whole-article chunk when at least one reference in the article passes a gate. This chunk is broad retrieval context only: it does not mean every reference or every claim in that article was accepted, and AGENT.2 retrieval tools must still report the per-reference gate source alongside the whole-article hit.

**Edge case — multi-reference articles.** When an article produces N references, each reference is gated independently. References A and B from the same article can be indexed even if reference C is rejected. The chunk schema keys on `(article_id, reference_index)` to support this.

**Edge case — re-extraction supersession.** When a re-extraction supersedes an earlier extraction's references (via `news_extractions.supersedes_extraction_id`), the prior references' indexed chunks are marked stale (`embedded_at` cleared, `superseded_at` set on the chunk row). The current extraction's references go through the gate fresh. Stale chunks are NOT returned by `search_articles_similar`.

Schema:
- Enable `pgvector` extension in Supabase.
- New table: `news_article_chunks` with `article_id`, `reference_index` (nullable; null = whole-article chunk for articles with no references but accepted-as-a-whole), `chunk_text`, `chunk_offset_start`, `chunk_offset_end`, `embedding vector(1536)`, `embedded_at`, `superseded_at`, `model`, `gate_source` (one of `'review_accept'` for gate 1, `'auto_applied_corroborating'` for gate 2, `'auto_applied_high_confidence'` for gate 3).
- Index: `ivfflat` or `hnsw` on `embedding`. Partial index `WHERE superseded_at IS NULL` so the search tool's query plan stays fast.
- New table: `news_reference_auto_applied(article_id, reference_index, source_run_id, applied_at, gate)` for the auto-applied audit trail. `gate` column distinguishes corroborating (gate 2) vs high-confidence (gate 3, future).

Pipeline:
- Triggered when a review item commits with `ReviewDecision.action = ACCEPT` AND (`decision_type = 'accept_new'` OR `decision_type LIKE 'candidate_%'`) — i.e., the researcher accepted the article's claim. Applies to all review item types that gate references: `status_change`, `override_contradiction`, `possible_match`, `new_candidate`. Embedding pipeline subscribes to commit events.
- Triggered on auto-applied confirmation (gate 2) when integration writes the `news_reference_auto_applied` audit row.
- Triggered on re-extraction to supersede prior chunks.
- Chunking strategy: per-reference chunks (the body offsets cited in `passage_excerpts`) plus one whole-article chunk for general retrieval (only indexed if the article has at least one accepted reference).
- Embedding model: direct OpenAI `text-embedding-3-small` at 1536 dimensions for AGENT.1. Cost accounting prices input tokens at `$0.02 / 1M`; alternatives such as Voyage/Cohere are deferred model-registry options and require a vector-dimension migration/sweep.
- Idempotence: `--apply` filters out active chunks with identical `(article_id, reference_index, model, chunk_text)` before reserving cost or calling the embedding API. Re-running unchanged inputs should report skipped unchanged chunks and spend `$0`.
- Re-embed on embedding-model upgrade or on prompt-version bumps that materially change downstream agent reasoning (rare).

**Backfill.** AGENT.1 populates the index from existing `news_project_references` whose review items are already committed-accept. The current production set is small (D.6 staging smoke = 5 articles, 9 references; expected D.B 8-week LA backfill = ~150 articles, ~300-500 references). Backfill runs through `tcg-pipeline news index-articles` (plan-only by default, `--apply` to spend/write) or the durable `news_backfill_chunk` worker job.

**PostGIS GIST index (Stage 1).**
- `CREATE INDEX ix_projects_location_gist ON projects USING GIST (location);`
- One-line Alembic migration.
- Required for `get_nearby_projects` to be sub-50ms at 25-market scale.

**Project state digest (Stage 2 — Q15).**
- View-based, not a new table.
- `get_project_state` is a Python tool handler that joins `project_field_resolution` + `project_latest_evidence` + a small `recent_evidence` query.
- Promote to materialized view only if production observation shows read latency is constraining the agent loop.

### 5.8 Cost guardrails and runtime kill switches (single-cutover launch)

Because the sprint plan ships Stages 1–3 in a single cutover with permits live from day one (researcher direction, 2026-05-04), the operational guardrails below are load-bearing. Without them, "build with it on, dial back if too expensive" depends on noticing problems quickly enough — which is exactly what the cap and kill switch automate.

**Scoped daily cost caps — generalized schema with config/override/usage split (revised 2026-05-04 per senior-developer feedback).** Today already separates config (`news_cost_caps`) from spend (`news_extraction_costs`). The AGENT.1 generalization preserves that separation, adds the `bucket` dimension, and pulls temporary cap overrides into their own table so the steady-state cap config stays clean. Three tables:

```sql
-- Configuration: one row per (bucket, effective_from). Auditable cap changes over time.
CREATE TABLE cost_caps (
    bucket             TEXT NOT NULL,                -- "news", "permits", "costar", "pipedream", future...
    effective_from     DATE NOT NULL,
    effective_to       DATE,                         -- NULL = currently active
    daily_warn_usd     NUMERIC(10, 2) NOT NULL,
    daily_hard_usd     NUMERIC(10, 2) NOT NULL,
    notes              TEXT,                         -- why this cap was set/changed
    PRIMARY KEY (bucket, effective_from)
);

-- Temporary cap overrides: time-bounded bumps without rewriting cap config.
-- Successor to today's news_cost_caps.override_hard_usd / override_until / override_set_by_user_id / override_note columns.
-- Audit contract: preserve the existing UUID-keyed user-id semantics from news_cost_caps.override_set_by_user_id (db/models.py:1444).
CREATE TABLE cost_cap_overrides (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bucket             TEXT NOT NULL,                -- which bucket the override applies to
    override_hard_usd  NUMERIC(10, 2) NOT NULL,      -- new hard cap while active
    override_warn_usd  NUMERIC(10, 2),               -- optional new warn cap; NULL = leave the warn cap from cost_caps
    effective_from     TIMESTAMPTZ NOT NULL DEFAULT now(),
    effective_until    TIMESTAMPTZ NOT NULL,         -- automatic expiry
    set_by_user_id     UUID,                         -- Supabase user id of researcher who set the override (matches today's news_cost_caps audit pattern). Populated for researcher actions; NULL otherwise.
    set_by_actor       TEXT,                         -- non-user setter identifier (system, script, automated cap bump). Populated when there is no user; NULL otherwise.
    note               TEXT NOT NULL,                -- required: why the bump was needed
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Exactly one setter must be populated. Authoritative-actor ambiguity is not allowed.
    CHECK ((set_by_user_id IS NOT NULL) <> (set_by_actor IS NOT NULL))
);
-- Postgres rejects now() in a partial-index predicate (not immutable). Use a
-- regular index on (bucket, effective_until DESC); the cap-reservation query
-- filters with WHERE effective_until > now() at query time.
CREATE INDEX ix_cost_cap_overrides_bucket_until
    ON cost_cap_overrides (bucket, effective_until DESC);

-- Spend rollup: one row per (bucket, cost_date, capability, provider, model).
-- Successor to today's news_extraction_costs with bucket added.
CREATE TABLE llm_cost_usage (
    bucket             TEXT NOT NULL,                -- "news", "permits", ...
    cost_date          DATE NOT NULL,
    capability         TEXT NOT NULL,                -- "triage", "extraction", "extract_retry", "agent.news_v1", "agent.permit_v1", "reserved", future...
    provider           TEXT NOT NULL,                -- "anthropic", "openai", "_reservation_" for in-flight reservations
    model              TEXT NOT NULL,                -- model id, or "_reservation_" for reservation rows
    call_count         BIGINT NOT NULL DEFAULT 0,    -- preserved from today's news_extraction_costs.call_count
    input_tokens_uncached      BIGINT NOT NULL DEFAULT 0,
    input_tokens_cache_creation BIGINT NOT NULL DEFAULT 0,
    input_tokens_cached        BIGINT NOT NULL DEFAULT 0,
    output_tokens              BIGINT NOT NULL DEFAULT 0,
    spent_usd          NUMERIC(12, 6) NOT NULL DEFAULT 0,
    PRIMARY KEY (bucket, cost_date, capability, provider, model)
);
CREATE INDEX ix_llm_cost_usage_bucket_date ON llm_cost_usage (bucket, cost_date DESC);
```

**Reservation row semantics.** Today's `news_extraction_costs` uses sentinel rows with `pass_name='reserved'`, `model='_reservation_'` for in-flight cost reservations under `pg_advisory_xact_lock`. Carries forward in `llm_cost_usage` as `(bucket, cost_date, capability='reserved', provider='_reservation_', model='_reservation_')`. The cap-reservation logic increments `spent_usd` on this row when reserving; on completion, it decrements the reservation row and increments the real `(bucket, cost_date, capability, provider, model)` row. Stale `_reservation_` rows from worker crashes are swept by the same cleanup job that exists today (per the D.M roadmap notes).

**Cap-reservation lookup:** for each reservation, the logic queries (1) the active `cost_caps` row for the bucket (latest `effective_from` where `effective_to IS NULL`), (2) any active `cost_cap_overrides` row (`effective_from <= now() < effective_until`), (3) today's spend rollup. If an override is active, its `override_hard_usd` replaces the base `daily_hard_usd`; `override_warn_usd` replaces base warn if non-NULL. Worst-active-cap wins ambiguities. All three lookups run inside the same `pg_advisory_xact_lock` transaction.

Initial cap-config rows (all editable via the future J.1 admin console):

| Bucket | Daily warn | Daily hard | Notes |
|--------|-----------:|-----------:|-------|
| `news` | $25 | $35 | Inherits today's `news_cost_caps` defaults. Covers triage + default extraction + agent runs on news. |
| `permits` | $50 | $75 | New in AGENT.3. Covers agent runs on LADBS permit intake. |
| `costar` (deferred AGENT.4) | TBD | TBD | New bucket; sized for bulk-upload spike. Researcher confirms threshold before AGENT.4 launches. |
| `pipedream` (deferred AGENT.5) | TBD | TBD | New bucket; sized for occasional Pipedream upload bursts. |

**Migration plan (AGENT.1):**
1. Create `cost_caps` table; insert one row for `bucket='news'` with today's defaults (warn $25, hard $35) and `effective_from=<today>`.
2. Create `cost_cap_overrides` table (empty at launch).
3. Create `llm_cost_usage` table.
4. Migrate today's `news_extraction_costs` rows into `llm_cost_usage` with `bucket='news'`. Existing `pass` column maps to `capability` (with `agent.<profile>` capability strings introduced for agent runs in AGENT.2/3); existing `call_count` carries forward to the new column. Reservation rows continue to use sentinel `provider='_reservation_'` / `model='_reservation_'` semantics.
5. If today's `news_cost_caps` table contains an active override (`override_hard_usd`, `override_until`, etc.), copy it into `cost_cap_overrides` as a row with the same expiry. Field mapping (subject to the strict CHECK constraint that exactly one setter is populated):
    - `bucket = 'news'`
    - `override_hard_usd = news_cost_caps.override_hard_usd`
    - `override_warn_usd = NULL` (today's table has no per-override warn; leave the base warn from `cost_caps`)
    - `effective_from = news_cost_caps.created_at` (or `now()` if unavailable)
    - `effective_until = news_cost_caps.override_until`
    - **`set_by_user_id = news_cost_caps.override_set_by_user_id` when non-NULL; otherwise `set_by_user_id = NULL` and `set_by_actor = 'migration_2026_05_xx'`** (substituting the actual migration revision id). The existing `override_set_by_user_id` column is nullable, so the migration must handle both cases. Never populate both.
    - `note = news_cost_caps.override_note` if non-empty; otherwise `note = 'Migrated from news_cost_caps; original note was empty.'` (`note` is NOT NULL in the new table).
    - The 2026-05-02 D.6 staging $50 cap bump used today's mechanism; this copy step preserves that audit history with explicit user-id where available.
6. Drop `news_extraction_costs` and `news_cost_caps` (atomic snapshot-and-verify migration).
7. Cost reservation logic joins `cost_caps` (latest active row per bucket) + `cost_cap_overrides` (any active override) + `llm_cost_usage` (today's spend per bucket) to compute remaining headroom. Reservation logic keys on originating intake stream to charge the right bucket. All buckets continue to use `pg_advisory_xact_lock` race protection.

If permits are cost-runaway, the news bucket is unaffected. Worst-case overshoot is one day of the affected bucket's hard cap, not a month of compounding. Future source profiles (CoStar, Pipedream) plug in as new bucket rows in both tables without schema changes.

**Runtime kill switches.** Three settings flags, all flippable without a deploy:

- `agent_enabled_for_news: bool` (default `true`) — when `false`, news intake falls back to default-extraction-only (no Pass 3a/3b, no agent reasoning).
- `agent_enabled_for_permits: bool` (default `true`) — when `false`, permit intake falls back to today's deterministic `match_raw_record` path with no agent involvement.
- `news_use_legacy_pass3: bool` (default `false`) — emergency-only fallback that re-routes news through the imported legacy Pass 3a/3b code (preserved as `extraction_legacy.py`). Used only if AGENT.2 has a regression that default-only mode can't paper over. Documented in Edit 1 above.

**Live LLM opt-in.** `agent_allow_live_llm: bool` defaults to `false`. News integration may run with injected fake/test clients, but it will not construct a real Anthropic agent client unless this flag is explicitly true. Production workers must set it before live agent routing; tests and local development fail fast instead of accidentally spending LLM credits.

The flags are read at agent-runner entry. Flipping `agent_enabled_for_permits` (or `agent_enabled_for_news`) to `false` takes effect on the next job; in-flight jobs complete normally.

**Degraded-fallback semantics — keep Pass 3a/3b importable for one release (decision 2026-05-04).** The cutover migration removes Pass 3a/3b from the active news ingestion code path, but the code itself stays in the repo as `news/extraction_legacy.py` (or equivalent module name) for one release cycle after AGENT.2 ships. This means:

- **Default behavior with kill switches OFF:** agent runs as designed.
- **`agent_enabled_for_news=false`:** news ingestion falls back to **default extraction only, no second pass**. This is a degraded-but-safe mode: extractions still happen, but no Pass 3a structural-conflict re-extract and no Pass 3b new-candidate re-extract. Acceptable for short-term cost-spike triage or quality regression triage.
- **Stronger fallback if needed:** an emergency settings flag `news_use_legacy_pass3=true` re-routes news through the imported legacy Pass 3a/3b code. Used only if AGENT.2 has a regression that the kill-switch's default-only mode can't paper over.

The legacy module is removed in the next major release after AGENT.2 stabilizes (no specific date — researcher decides when the agent path is "obviously fine"). Until then it's dead code, but the cost of dead code in the repo is low and the safety net it provides is meaningful.

The migration that removes Pass 3a/3b from the *active* code path is unchanged; only the deletion of the legacy module is delayed.

**What gets monitored daily for the first 4 weeks.**
- Per-bucket spend against caps.
- Agent fire rate by trigger type.
- Agent average per-run cost.
- Reviewer acceptance rate on agent decisions, split by source type.
- Spot-check sampler agreement rate.

These surface in the spot-check dashboard tile per §6.4.

### 5.9 Source profiles

A source profile declares everything source-specific about how the agent runs for one intake stream. The runner is source-agnostic; profiles let new sources plug in without runner changes.

**Profile contents:**

```python
@dataclass(frozen=True)
class SourceProfile:
    name: str                                # "news_v1", "permit_v1", "costar_v1", "pipedream_v1"
    intake_source_type: str                  # matches IntakeRecord.source_type
    triggers: list[AgentTrigger]             # source-specific trigger conditions
    allowed_tools: frozenset[str]            # tool names this profile may invoke
    system_prompt_path: Path                 # path to source-specific system prompt template
    cost_cap_bucket: str                     # which cost_caps bucket charges this profile's runs (e.g., "news", "permits")
    kill_switch_setting: str                 # which Settings flag gates execution
    semantic_interpreters: dict[str, SemanticInterpreterProfile]
    user_prompt_renderer: Callable           # builds the per-run user message from IntakeRecord.payload
```

**Unit-delta trigger threshold — uniform 10% (researcher decision 2026-05-04).** All source profiles use a `>10%` unit-count delta threshold for firing the agent on contradiction-vs-state cases. Replaces the earlier `>50%` (permit) and `>25%` (news) sketches. See [`ci_contradiction_impact_assessment.md`](ci_contradiction_impact_assessment.md) §I.6. Implication: agent fire rate likely 25-30% initially rather than the design doc's earlier 15-20% projection. Cost guardrails handle this — see risk R10 in §11.

**News profile (built in AGENT.2).**
- Triggers: 6 conditions per §1 step 6 (Pass 1↔2 conflict, low confidence, new_candidate, possible multi-candidate, multiple distinct mentions, material contradiction). Material contradiction = `>10%` unit delta, status regression, or developer mismatch vs current state.
- Allowed tools: all core tools + news-specific tools (`search_articles_by_project`, `search_articles_similar`).
- Semantic interpreters: hybrid deterministic + compact LLM path per §5.1.1 for status, product type, age restriction, delivery date projection, and unit buckets including `workforce_units`. They run per project reference, not per full-article batch, and write canonical evidence only when the observed article text supports the field.
- Prompt path: `prompts/agent/news_v1/system.md`.
- Cap bucket: `news` (existing daily cap row).
- Kill switch: `agent_enabled_for_news`.
- Required intake fields: `extraction_id` (string `intake_record_id` remains the news article id; `extraction_id` anchors the specific default extraction the agent reasoned over).

Implementation path: `tcg_pipeline.agents.profiles.NEWS_AGENT_PROFILE`. Capability key: `agent.news_v1`. Current prompt scaffold path: `src/tcg_pipeline/agents/prompts/news_v1/system.md`.

**Permit profile (built in AGENT.3).**
- Triggers: narrow Q9 set — `new_candidate` on `create_new_candidates: true` LADBS sources, `>10%` unit-count change vs current state, product-type change vs current state.
- Allowed tools: all core tools + permit-specific tools (`get_permits_for_parcel`, `get_permits_for_project`, `get_articles_about_parcel_or_address`, `get_permits_for_parcel_or_address`).
- Semantic interpreters: deterministic LADBS/source-profile mapping first; LLM/agent path only for ambiguous permit descriptions, conflicting signals, or cross-stream exceptions.
- Prompt path: `prompts/agent/permit_v1/system.md`.
- Cap bucket: `permits` (new daily cap row, `$50` warn / `$75` hard).
- Kill switch: `agent_enabled_for_permits`.

**CoStar profile (deferred, AGENT.4).**
- Triggers (sketch — calibrate during AGENT.4 build): contradiction-only profile. Fires when CoStar update implies `>10%` unit delta vs current resolved state, status regression, or developer mismatch — *not* on `new_candidate` (CoStar's `costar_property_id` usually catches matches via the deterministic identifier tier).
- Allowed tools: all core tools + cross-stream tools where useful (`get_articles_about_parcel_or_address`).
- Prompt path: TBD.
- Cap bucket: `costar` (new daily cap row, sized for bulk-upload spike).
- Kill switch: `agent_enabled_for_costar`.
- **Special consideration: bulk uploads.** A CoStar Excel might contain hundreds of rows. At ~10% trigger rate × $0.50 average × 1,000 rows = $50/upload — manageable but spiky. Cap should be sized per-upload-day rather than per-rolling-day, or the upload should be rate-limited to fit within the daily cap.

**Pipedream profile (deferred, AGENT.5).**
- Triggers (sketch — calibrate during AGENT.5 build): contradiction-only profile, *and* contradiction must be against Tier 1 evidence (newer government source). Trigger threshold: `>10%` unit delta. Pipedream is human-curated Tier 1; the agent should not second-guess researchers based on lower-tier evidence. Default behavior is "Pipedream wins unless higher-confidence Tier 1 evidence disagrees."
- Allowed tools: all core tools.
- Prompt path: TBD.
- Cap bucket: `pipedream` (new daily cap row).
- Kill switch: `agent_enabled_for_pipedream`.
- **Special consideration: Pipedream uploads currently flow through `db/seed.py`, not the scheduled-collector or news-integrator paths.** Wiring Pipedream through the agent layer means refactoring the seed path — not bad, but real work. AGENT.5 owns that refactor.
- **Special consideration: human-curated semantics.** The agent's reasoning over Pipedream input is in tension with "researcher said it, trust it." Profile must default conservative — agent escalates rather than overrides on Pipedream inputs. Type 2 (downgrade matcher's confirmed) is *not* in scope for the Pipedream profile by default.

**Future sources.** Each new structured intake source declares a profile as part of its onboarding. New-market and new-source checklists in Phase I.1 must include "agent source profile" as a required artifact. No agent runner changes needed; the runner reads the profile and dispatches accordingly.

**Profile registry.** Profiles live in `src/tcg_pipeline/agent/profiles/` with one file per profile. The runner loads the active profile by `intake_source_type`. Profiles are versioned (`news_v1`, `news_v2`) so prompt changes don't silently shift agent behavior — the prompt-version-bump eval gate from §6.5 applies to profile bumps, not just default-extraction prompt bumps.

---

## 6. Eval Methodology

Per Q12 plus the proposal's three-layer design plus Q10/Q11 answers.

### 6.1 Layer 1 — Continuous reviewer-acceptance signal

Mechanism in code today (review_decisions). Agent decisions appear as review items; reviewer accepts/rejects. Reject rate per agent decision type tracked in a dashboard.

Computed metrics:
- Per-trigger-type acceptance rate (e.g., "new_candidate-triggered agent runs accepted at 87%").
- Per-decision-type acceptance rate (Q6 types 1/2/3 each get separate rates).
- Trend over time.

**Free, scales automatically, continuous.** Blind spot: silent agent errors that don't trigger review.

### 6.2 Layer 2 — Pipedream auto-comparison (Q10/Q11)

Mechanism: on each Pipedream survey publication, run a comparison job that:
1. Identifies TCG project_ids within the survey window's coverage zips AND with `last_evidence_date` inside the comparison window (default ±2-4 weeks of publication).
2. For each project, compares TCG's resolved values (status, developer, total_units, location) against Pipedream's recorded values.
3. Disagreements get logged with both sides; researcher reviews a sample.
4. If TCG was wrong and Pipedream was right, that's a feedback item: prompt-tuning candidate, glossary expansion, missing source, etc.

Mapping table: `pipedream_coverage_windows` (survey_id, publication_date, zip_codes[], compare_window_start, compare_window_end). Initial population for June 2026 LA survey.

Geographic coverage growth: as TCG markets are added (San Diego, SF, Silicon Valley, Seattle, Denver, others — per Q11), each market that overlaps with Pipedream coverage adds rows to this table.

**June 2026 first run** validates whether overlap is statistically sufficient (target ≥30 confirmed-match projects). If not, document the gap and rely more heavily on Layer 1.

### 6.3 Layer 3 — Targeted hand-grading on disagreement

When Layer 1 and Layer 2 disagree (reviewer accepted but Pipedream contradicts, or reviewer rejected but agent looks right on inspection), researcher hand-grades that case. ~20-30 articles/month/market expected at steady state.

### 6.4 Spot-check sampler (Q12)

Independent of the three layers above. Surfaces N=10 random non-escalated agent decisions per week to a researcher dashboard tile. Researcher marks each "agreed" or "disagreed (here's why)."

Cadence options:
- **Initial build** (Stage 2 first 4 weeks): 10/week.
- **Steady state**: 10/week unless agreement-rate >95% for 3 months → taper to 10/month.
- **New market activation**: 20/week for first 4 weeks of any new market's agent rollout.

Disagreements feed into Layer 3 hand-grading and prompt iteration.

### 6.5 Eval pass rate gate

Per the existing D.late.B framing: prompt-version bumps gated on `--eval-pass-rate >= 0.90` against the accumulated eval set (Layer 1 + Layer 2 + Layer 3 + Q12 spot-checks).

---

## 7. Cost Model

### Aspirational target (Q3) — not a hard gate
- 12-month backfill: ~$100 aspirational.
- 8-week LA window: ~$17 aspirational.

These are *targets*, not budget gates. Stage 1 measures cost across all three default-extraction candidates and Stage 2 measures realized agent fire-rate and per-run cost. The numbers inform model choice and trigger calibration but do not block stage progression. Under the current trajectory we already extract every article with Opus 4.7; any of the candidate default models combined with agent-on-hard-cases is a cost improvement over status quo.

### Today's measured cost (status-quo baseline)
- 2026-05-02 D.6 staging smoke: $7.27 across 5 articles, 14 LLM calls.
- Post-Pass-3a-tightening 8-week LA projection (per ROADMAP D.B): $80–110.

### Proposed cost composition by default-model candidate

Per-article steady state, projected (validate against Stage 1 A/B):

**With Sonnet 4.6 as default:**
- Triage (Haiku 4.5): ~$0.002.
- Default extraction (Sonnet 4.6, cache-warm): ~$0.05–0.10.
- Output-quality retry (rare; <5% of articles): ~$0.05 amortized.
- Agent run (15-20% of articles, average 5 tool calls, Opus 4.7 with cached system prompt): target average $0.50, hard cap $5.00.
- Average per article: ~$0.14. 8-week LA: ~$21.

**With Opus 4.7 as default (status-quo model, agent-on-hard-cases architecture):**
- Triage: ~$0.002.
- Default extraction (Opus 4.7, cache-warm): measured by Stage 1. Current Anthropic list pricing is $5/MTok input, $6.25/MTok 5m cache write, $0.50/MTok cache hit, and $25/MTok output; older D.6 projections used the prior $15/$75 tier and therefore overstate this line.
- Output-quality retry: ~$0.10 amortized.
- Agent run (same as above): ~$0.10 amortized at 17.5% fire rate.
- Average per article: Stage 1 harness output is authoritative. Expect lower than the earlier ~$0.50 / ~$75 projection if token counts resemble D.6, because current Opus 4.7 list pricing is lower than the prior estimate.
- Still cheaper than status quo ($80–110) if Pass 3a/3b at status-quo fire rate is replaced by bounded agent runs on a fraction of articles.

**With GPT-5.4 or GPT-5.5 as default:**
- GPT-5.4 pricing implemented for A/B accounting at $2.50/MTok input, $0.25/MTok cached input, and $15/MTok output.
- GPT-5.5 pricing implemented for supplemental A/B accounting at $5.00/MTok input, $0.50/MTok cached input, and $30/MTok output.
- OpenAI Responses usage is expected to report cache-creation tokens as zero; if non-zero cache-creation usage is ever passed into internal accounting, it is priced at the full input rate.
- Agent runs continue on Opus 4.7 regardless of default model.
- Stage 1 A/B harness measures.

### Cost levers (in order of effect)
- Default model choice (Stage 1 — biggest single lever).
- Agent trigger calibration (Stage 2 — fire rate is half the cost equation).
- Per-run cap on agent calls (Stage 2 — bounds worst case).
- Glossary removal (AGENT.1 option 3; removes the dominant cached-system-prompt cost rather than slicing it).
- Prompt-cache discipline (still retained, but much less important once the glossary is removed).
- Batch API for scheduled extractions (deferred per §0.1; ~50% off scheduled-extraction line item if/when adopted).

### Cap enforcement
Cost-cap config lives in `cost_caps` (per §5.8 split). Spend rollup lives in `llm_cost_usage`. Cost reservation logic joins the two: latest active cap row per bucket vs today's spend row per bucket. The `news` bucket continues with $25 warn / $35 hard at launch (today's defaults). AGENT.2 adds a per-run cap on agent calls (separate from the daily-aggregate cap). All caps use the existing `pg_advisory_xact_lock` race protection.

---

## 8. Migration Plan

**Single-cutover note.** Per the marathon sprint structure (§3 Q17, §9), all three migration groups below land in one cutover event. They are listed as separate groups for build-tracking clarity, not as separate shipping events. Within the sprint, AGENT.1 migrations may be applied to a development branch for the A/B harness work before AGENT.2/3 code is complete, but production application is one atomic event.

### Pre-cutover
- DB snapshot before any migrations.
- Document current Pass 3a fire rate baseline (from production after D.6 cron stabilizes).
- §5.5.0 contradiction impact assessment authored and read by the researcher.

### AGENT.1 migrations
1. `CREATE EXTENSION pgvector;`
2. `CREATE INDEX ix_projects_location_gist ON projects USING GIST (location);`
3. `CREATE TABLE news_article_chunks (...)` — includes `gate_source` column and `superseded_at` per §5.7.
4. `CREATE TABLE news_reference_auto_applied (...)` per §5.7 auto-applied gate.
5. Cost-cap schema split per §5.8: create `cost_caps` (config), `cost_cap_overrides` (time-bounded bumps), `llm_cost_usage` (rollup with `call_count` carried forward and reservation-row sentinel semantics preserved). Migrate `news_extraction_costs` rows into `llm_cost_usage` with `bucket='news'`. Copy any active `news_cost_caps` override into `cost_cap_overrides`. Drop `news_extraction_costs` and `news_cost_caps`.
6. Embedding pipeline scripts + first per-reference-accepted backfill.
7. A/B harness CLI command (artifact: produces a comparison report covering parse outcomes, reference counts, matcher outcome distribution, agent trigger proxy rate, projected review item counts, measured extraction cost, and payload quality spot-grade fields; AGENT.2 extends this to all-in cost once agent-run pricing exists) — ends with researcher decision memo.
8. Multi-provider abstraction in `news/llm.py` (Anthropic + OpenAI/Vercel AI Gateway) — required for GPT-5.4 in the A/B.

### AGENT.2 migrations
1. `CREATE TABLE agent_runs (...)` per §5.6 full observability schema. Implemented in repo as `202605050029` with the authoritative `agent_run_review_items` join table. Follow-up `202605050030` tightens `evidence_consulted` / `tool_calls_summary` to required JSON arrays, requires `completed_at`, and documents the news `intake_record_id` convention. Both AGENT.2 migrations were production-applied on 2026-05-06 after a fresh logical backup.
2. New `pass` enum value `extract_retry` (output-quality retry path).
3. Backfill synthetic `reasoning_trace` for legacy `pass='reextraction'` rows (Q16).
4. Move Pass 3a/3b code from `news/extraction.py` to `news/extraction_legacy.py`; remove from active news ingestion code path; keep importable per §5.8.
5. Code rollout: agent runner, tools (with §5.4.1 output budgets), output-quality retry, contradiction-detection move (Q5).
6. Schema: agent-produced `OVERRIDE_CONTRADICTION` items use `payload.proposed_alternatives: list[...]` per assessment §I.1. STATUS_CHANGE items keep single-value `proposed_value`.
7. UI updates: per-reference review items, Type 2 disagreement display, agent reasoning render, multi-alternative decision card with hover-revealed source detail, distinct `"Agent contradiction detection"` actor label.
8. Spot-check sampler dashboard tile.
9. Pipedream coverage table (initial LA survey window).
10. Settings flags: `agent_enabled_for_news` (default `true`), `news_use_legacy_pass3` (default `false`).

### AGENT.3 migrations
1. Permit-side cross-stream tool implementations (with §5.4.1 output budgets).
2. Permit-trigger calibration config (uniform 10% unit-delta threshold per §5.9).
3. `agent_runs` already exists from AGENT.2; permit runs use the same table.
4. Permit-specific failure-mode prompt additions.
5. Add `cost_caps` row for `bucket='permits'` (`daily_warn_usd: $50`, `daily_hard_usd: $75`).
6. Settings flag: `agent_enabled_for_permits` (default `true`).

---

## 9. Roadmap Amendments

The existing ROADMAP.md should be updated as follows. Concrete edits to apply when convenient:

### Phase D (revisions)
- **D.late.A — Auto-apply for high-confidence article matches.** No change to current scope, but note that auto-apply gating becomes more nuanced once the agent layer ships: high-confidence agent decisions (Q6 type 1 with strong corroborating tools-consulted evidence) may qualify for auto-apply differently than today's high-confidence deterministic matches. Re-evaluate gating criteria when Stage 2 ships.
- **D.late.B — Eval set bootstrap.** Replaced/extended by §6 of this doc. No more "curated golden set" framing; reviewer-decision + Pipedream auto-compare + targeted hand-grading + spot-check sampler.

### Phase D-late (new items to add)

**Sprint structure.** Per researcher direction (2026-05-04), Stages 1–3 build as one continuous sprint and ship as one cutover event. The three roadmap items below preserve dependency tracking and review boundaries; they are not separate shipping events.

- **D.late.AGENT.1 — Default-extraction infrastructure + retrieval prerequisites.**
  - Build multi-provider abstraction in `news/llm.py` (Anthropic + OpenAI, or Vercel AI Gateway).
  - Build three-way A/B harness; run Opus 4.7, Sonnet 4.6, GPT-5.4 against D.6 smoke set.
  - Researcher decision complete: keep Opus 4.7 as the default extraction model. No hard cost gate.
  - Add PostGIS GIST index on `Project.location`.
  - Build article embedding pipeline (per-reference acceptance gating).
  - **Drop the in-prompt glossary entirely** (option 3 from the §5.1 / §7 glossary discussion). Default extraction emits raw `candidate_name` / `candidate_developer` text without registry hints; matcher continues using fuzzy registry matching; agent's tools (Stage 2) provide registry knowledge on demand.
  - Depends on: D.6 stable production cron.

- **D.late.AGENT.2 — Agent on news.**
  - **Pre-build deliverable (Claude Code-owned, researcher-reviewed):** Contradiction-detection impact assessment per §5.5.0/§5.5.1, written to `docs/specs/ci_contradiction_impact_assessment.md`. Researcher reads before any C.i code is touched. Concise, marked with `⚠ HUMAN REVIEW` flags on uncertain rows.
  - Build agent runner, tools, evidence-schema additions.
  - Build shared semantic field interpretation layer (§5.1.1) and wire news references through it before writing canonical evidence for status, product type, age restriction, delivery date projection, and unit buckets including `workforce_units`. AGENT.1 A/B spot-grades semantic field quality, but the separate interpreters ship in AGENT.2.
  - Replace Pass 3a (a)+(b) and Pass 3b with agent loop.
  - Build output-quality retry path for parse/refused/truncated.
  - Move contradiction detection earlier (per Q5). No feature flag; impact assessment is the mitigation.
  - Cutover migration backfills legacy reextractions.
  - Worker model: one job per article needing agent escalation (per R8 decision).
  - Per-reference review queue + retrieval index gating.
  - Spot-check sampler dashboard.
  - Pipedream coverage compare job (LA window first).
  - **Runtime kill switch** `agent_enabled_for_news` (per §5.8). Default `true` at launch.
  - Depends on: D.late.AGENT.1.

- **D.late.AGENT.3 — Agent on permits.**
  - Add cross-stream tools and permit-specific failure-mode prompts.
  - Wire permits into the shared semantic interpretation layer with deterministic source-profile rules first; reserve LLM/agent interpretation for ambiguous permit descriptions, conflicting signals, or cross-stream exceptions.
  - Wire LADBS adapter integration through agent loop on calibrated trigger set (new_candidate, >10% unit change, product-type change).
  - Validates: cross-stream tools improve attribution.
  - **Scoped permit cost cap** (per §5.8): `cost_caps` row for `bucket='permits'` with `daily_warn_usd: $50`, `daily_hard_usd: $75` initially, independent of news bucket.
  - **Runtime kill switch** `agent_enabled_for_permits`. Default `true` at launch (researcher direction: "build with it on, monitor closely, dial back if needed").
  - Depends on: D.late.AGENT.2.

- **Single cutover event.** All three items above ship together in production. First 4 weeks post-cutover: daily monitoring of per-bucket spend, agent fire rate, reviewer acceptance, spot-check sampler agreement. Either kill switch can be flipped within minutes if costs spike.

- **D.late.AGENT.4 — CoStar agent path (deferred follow-on).**
  - Add CoStar source profile (per §5.9) to the agent runner.
  - Trigger profile: contradiction-only (>10% unit delta, status regression, developer mismatch). No `new_candidate` triggers — CoStar's stable `costar_property_id` short-circuits identifier matching for the common case.
  - Allowed tools: core tools + cross-stream `get_articles_about_parcel_or_address`.
  - New daily cost-cap bucket sized for bulk-upload spike.
  - Kill switch: `agent_enabled_for_costar`.
  - Wire CoStar upload path (currently `db/seed.py` for initial seed and `costar_uploads` table tracking) through the agent runner for in-scope rows.
  - Depends on: AGENT.3 stable in production.

- **D.late.AGENT.5 — Pipedream agent path (deferred follow-on).**
  - Add Pipedream source profile (per §5.9) to the agent runner.
  - Trigger profile: contradiction-only AND contradiction must be against Tier 1 evidence. Conservative default — agent escalates rather than overrides on Pipedream input. Type 2 (downgrade matcher's confirmed) NOT in scope.
  - Allowed tools: core tools.
  - New daily cost-cap bucket.
  - Kill switch: `agent_enabled_for_pipedream`.
  - **Includes refactor of Pipedream upload path** (currently flows through `db/seed.py`, not the scheduled-collector or news-integrator paths). Refactor is real work owned by AGENT.5.
  - Depends on: AGENT.3 stable in production.

- **D.late.AGENT.batch — Batch API dispatch for scheduled extractions (deferred).**
  - Implement Anthropic Batch API path in `ExtractionDispatcher` for cron-driven extractions only. Paste-a-link stays synchronous. Agent runs stay synchronous.
  - Cost-savings target: ~50% off scheduled-scrape extraction line item.
  - Status: explicitly NOT in initial build per §0.1; architecture must support clean swap-in later.

- **(Folded into D.late.AGENT.1) — GPT-5.4 cross-provider integration.**
  - Originally a separate deferred item; consolidated into AGENT.1 because the A/B harness must measure all three candidates including GPT-5.4. Multi-provider abstraction is part of the build, not a future deferment.

- **(Folded into D.late.AGENT.1) — Glossary slicing / in-prompt registry decision (was D.B sub-decision).**
  - The deferred D.B "glossary slicing" decision is resolved by going directly to option 3: drop the in-prompt glossary entirely. Registry knowledge moves to the agent's tool layer. Eliminates the ~103k-token cache write that dominates today's per-article cost and removes the 25-market scaling blocker.

### Phase E (no change)
Phase E remains "Resolution Engine Refinements" (E.1–E.5 as written). No agent-related items belong here.

### Phase F (no change)
Phase F remains "Additional Collectors" (F.1–F.6). Agent-on-permits work is in Phase D-late, not Phase F.

### Phase H (note on glossary problem)
The Phase H "address context layer" sub-task remains as written. The proposal-level concern about per-market glossary at scale is resolved by AGENT.1's option-3 decision (drop the in-prompt glossary entirely). Phase H no longer needs to wrestle with per-market glossary scaling; that problem dissolves once retrieval is live.

### Phase I (new-market / new-source onboarding)
Phase I.1 (formalize new-market onboarding process) gains a new required artifact: **agent source profile** for any structured intake source the new market introduces. Per §5.9, the profile declares triggers, allowed tools, prompt template, cost-cap bucket, and kill switch. This becomes part of the new-market checklist alongside market/jurisdiction config, address context, source docs, and source-tier mappings.

### Phase J (Platform Admin Console)
Phase J.1 (LLM model configuration console) was originally scoped as news-extraction-specific. Generalize to **agent runner + per-source-profile model selection**. Each profile registers a stable capability key (e.g., `agent.news_v1`, `agent.permit_v1`) so admins can switch agent models per profile without code rewrites.

---

## 10. Open Items / Deferred Decisions

1. **Embedding model** for article chunks. Stage 1 sub-decision; cheap path is OpenAI `text-embedding-3-small`.
2. **Sample size sufficiency for Pipedream auto-compare.** Validates on June 2026 first run.
3. **Pipedream coverage mapping infrastructure.** Build minimum for LA in Stage 2; expand per-market as markets come online (San Diego, SF, Silicon Valley, Seattle, Denver, others — per Q11).
4. **Spot-check cadence taper rules.** Default 10/week; tune after 3 months of agreement-rate data.
5. **Cross-provider integration approach.** Resolved 2026-05-05: direct provider APIs are current policy for all built/current AGENT work. Vercel AI Gateway stays a deferred operational option for centralized routing/monitoring, but Gateway activation requires a deliberate code/config sweep first.
6. **Cold-storage tier for `agent_runs.evidence_consulted`.** Decide if/when the table grows large.
7. **Batch API dispatch.** Deferred per §0.1; architecture supports plug-in.

---

## 11. Risks and Mitigations

### R1 — Sonnet (or chosen alternate model) quality is materially worse than Opus
**Mitigation.** Resolved by Stage 1 A/B: stay on Opus 4.7 as default. This is still a cost improvement over today's Pass-3a/3b-on-Opus-on-everything path because AGENT.2 fires the agent on a fraction of articles instead of re-extracting deterministically for every difficult case.

### R2 — Agent fire rate exceeds projection
**Updated projection (2026-05-04).** Original §7 cost model assumed 15-20% fire rate. With the researcher-chosen uniform 10% unit-delta trigger threshold (replacing earlier >50%/>25% sketches), fire rate is more likely 25-30% initially — possibly higher in the first weeks before prompt-tuning settles.

**Mitigation.** Cost guardrails (scoped daily caps + kill switches per §5.8) bound the worst case. Stage 2 launch measures fire rate weekly via the spot-check dashboard. If sustained materially higher than the new 25-30% projection AND costs are tracking uncomfortably, tighten triggers (e.g., raise unit-delta threshold from 10% to 15% on a per-profile basis, narrow low-confidence threshold). Cost is informed by fire rate but not budget-gated; the lever exists if we want to use it.

### R3 — Per-agent-run cost exceeds projection (target $0.50 average)
**Mitigation.** Per-run cap ($5 hard) prevents runaway. Cost monitored via `llm_cost_usage` daily rollup against the `cost_caps` daily-warn/hard rows for the relevant bucket. If average drifts up, investigate whether tool-call count is high (prompt issue) or per-tool-call cost is high (retrieved-context size issue).

### R4 — Contradiction detection regression after Q5 move
**Mitigation.** No feature flag (per researcher direction 2026-05-04). The mitigation is the §5.5.1 mandatory upstream/downstream impact assessment before any C.i code is touched, plus the §5.5.2 regression coverage and production-data spot checks before cutover. Stage 2 build plan must list the impact assessment as a deliverable that gates the rewrite. The fallback path (post-resolve `detect_project_contradictions` for non-news writes that don't trigger the agent) remains in place after Stage 2 — only the news-integration contradiction path moves into the agent.

### R5 — Per-reference retrieval index gating creates blind spots
**Mitigation.** Spot-check sampler (Q12) catches non-flagged-but-wrong agent decisions independently of retrieval. If silent errors trace to "agent retrieved X but X was a rejected reference that shouldn't have been indexed," that's a gating bug, not a methodology failure.

### R6 — Permit volume × agent cost exceeds news cost by >10×
**Mitigation (revised 2026-05-04 for single-cutover sprint).** Three layers:
1. **Trigger narrowness.** Permit triggers are strictly the Q9 set as revised: `new_candidate`, >10% unit change, product-type change. No general "address composite confirmed but worth a second look" trigger.
2. **Scoped daily cap.** Per §5.8, permit agent spend has its own `cost_caps` row (`bucket='permits'`, `daily_warn_usd: $50`, `daily_hard_usd: $75`) independent of news. Worst-case overshoot is one day's hard cap, not compounding.
3. **Runtime kill switch.** `agent_enabled_for_permits` flag, flippable without deploy. If first-week costs are unacceptable, flip to deterministic-only fallback in minutes.

Researcher direction is to launch with permits live ("build with it on, monitor, dial back if needed"). The cap and kill switch make that bet bounded.

### R7 — Cross-stream geocoding inconsistency
**Mitigation.** Both intake paths today use Geocodio-first/Esri-fallback. Stage 3 launch verifies they still match by spot-checking 50 cross-stream queries. If drift detected, fix before relying on cross-stream tools.

### R8 — Agent timeouts under worker job timeout (900s) — RESOLVED
**Decision (2026-05-04):** Option (b) — one job per article that needs agent escalation. See §5.3 worker-model section. The discovery + Pass 0/1/2 batch stays as one job; agent integration is split into one job per article. Each agent job's 300s cap fits inside the existing 900s scrape job timeout with significant headroom. Concurrency comes from RQ worker count, not from in-job parallelism.

### R9 — Single-cutover rollout risk
**Mitigation (revised 2026-05-04).** Researcher direction is to ship all three stages as one cutover rather than staged shipping events. The mitigation is no longer "stage gates between cutovers" but the runtime-controllable safety mechanisms in §5.8:
- Scoped daily cost caps (separate news vs permit buckets) with `pg_advisory_xact_lock` race protection.
- Runtime kill switches (`agent_enabled_for_news`, `agent_enabled_for_permits`) flippable in minutes without a deploy.
- Daily monitoring of cost, fire rate, acceptance rate, and spot-check agreement during the first 4 weeks.

Trading "weeks of staged observation" for "minutes-to-flip kill switch + bounded daily cap" is acceptable to the researcher. The cap enforces a worst-case overshoot of one day's hard cap, not a month of compounding cost.

---

## 12. Cross-References

- `docs/specs/news_research_design.md` — superseded for Pass 3a/3b/extraction-model details by this doc; still load-bearing for Pass 0/1, triage, source registry, scheduler.
- `docs/specs/EVIDENCE_LAYER_INTEGRATION_GUIDE.md` — evidence row contract, resolver semantics. Unchanged.
- `docs/specs/EVIDENCE_LAYER_DECISIONS.md` — §22 review-protected override semantics; §21f recent-article delivery-date priority. Unchanged.
- `docs/specs/review_workflow.md` — staged/committed state machine. Per-reference acceptance is an extension, not a contradiction.
- `docs/specs/review_decision_cards.md` — decision card consolidation (C.tail.11/12). Stage 2 needs to extend cards to render agent reasoning + Type 2 disagreement display.
- `ROADMAP.md` — agent rollout items (D.late.AGENT.1/2/3) per §9 above.
- `NOTES_agentic_research.md` — Google Alerts + agentic research pass notes (2026-05-01). Adjacent ideas; the agent runner architecture in this doc is reusable for the human-initiated "research this project" mode that NOTES_agentic_research describes.

---

## 13. Revision History

- **2026-05-08 (revision 35) — News-agent child-job hardening.**
  - Worker-model naming now matches implementation: the parent scheduled job kind remains `news_scrape` for compatibility; `news_scrape_discovery` was only the design sketch name.
  - Added the active-child uniqueness contract for `news_agent_integrate`: one queued/running child row per article, parent retries reuse the row, and Redis-unavailable rows may be enqueued later.
  - Documented atomic `source_runs.new_matches` increments across concurrent child jobs.
  - Clarified Q7 failure semantics: agent-loop failures fall back inside integration; catastrophic worker-level child failures are retryable ops failures and do not write fallback review items outside integration.

- **2026-05-04 — Initial draft.** Reconciles the original `agentic_pipeline_proposal.md` against actual codebase state (verified extraction.py, integration.py, news_matcher.py, db/models.py, settings.py, costs.py, structural.py, prompts.py, evidence.py, collect.py, resolution/engine.py, source_adapters/ladbs.py). Incorporates researcher answers to the 17 clarifying questions. Adds two top-of-file callouts: Batch API deferred, model-choice deferred.

- **2026-05-05 (revision 14) — Prompt-version audit cut for slim default extraction.**
  - Active default extraction is now `extract_v2`, not `extract_v1`. `config/news_prompts.yaml` points `extract` at `extract_v2` so new `news_extractions.prompt_id` rows have a clean cutover marker.
  - `extract_v2/system.md` instructs the model to emit raw candidate names/developers and not infer registry IDs; `extract_v2/schema.json` no longer requires `registry_developer_id` / `registry_project_id`.
  - `extract_v1` is restored as the legacy glossary prompt/schema so historical rows tagged `extract_v1` keep one meaning for cost and quality reconciliation.
  - Developer/project dictionary Pass 3a structural-conflict triggers are effectively silent for active `extract_v2` because default extraction no longer emits registry hints. This is intentional interim behavior until AGENT.2 moves those cases into the agent layer; the A/B harness should measure agent-trigger proxies and reviewer workload with that gap visible.

- **2026-05-05 (revision 15) — AGENT.1 A/B harness scaffold implemented.**
  - Added `tcg-pipeline news ab-extract` with candidate syntax `<provider>:<model>` and default candidates Opus 4.7, Sonnet 4.6, and GPT-5.4.
  - Harness uses active `extract_v2`, production parser/schema validation, production matcher, and rollback-only integration projection for review-item counts.
  - Report output includes parse status counts, reference counts, matcher status/match-type counts, agent-trigger proxy reasons, projected review-item counts, measured extraction cost, latency, token usage, pricing assumptions, and blank researcher spot-grade fields.
  - The integration projection intentionally rolls back temporary article/extraction/reference/evidence/review/source-run rows, so the harness can run against staging or production-like databases without polluting operational data.

- **2026-05-05 (revision 16) — A/B harness live-run guardrails.**
  - Added a DB-backed rollback invariant test that snapshots `news_articles`, `news_extractions`, `news_project_references`, `evidence`, `review_items`, and `source_runs` before and after a stubbed harness run.
  - CLI startup prints the redacted DB target plus fixture article count, candidate count, and planned LLM call count before any provider call.
  - Harness preflights each provider/model before the article loop so wrong model IDs, missing keys, or Gateway configuration failures do not turn into repeated per-article `api_error` rows.
  - Report metadata now states that harness spend bypasses cost-cap accounting and does not write `llm_cost_usage`; provider cache semantics are also called out as not perfectly apples-to-apples.

- **2026-05-05 (revision 17) — Extract v2 evidence/status guardrails.**
  - `extract_v2/system.md` now explicitly bans outside knowledge, web knowledge, memory, assumptions, and guessing missing values.
  - Because `candidate_status_signal` becomes TCG `pipeline_status` evidence in the news integrator, `extract_v2` now includes the TCG status rubric. A conference comment or first mention of an idea maps to `Conceptual`; `Proposed` requires stated application/planning/design-review activity or another concrete proposal beyond an idea.
  - The prompt now treats structural signals as evidence leads that must be checked against nearby article text, especially for ambiguous status words such as "proposed", "plans", and "planning".

- **2026-05-05 (revision 18) — Shared semantic interpretation layer added to AGENT.2/3 scope.**
  - AGENT.2 now explicitly builds a shared semantic field interpretation layer (§5.1.1) before writing TCG evidence from news references. The AGENT.1 A/B harness still measures default extraction only, but its spot-grade should include semantic correctness for status, product type, age restriction, delivery date projection, and unit buckets.
  - News semantic interpretation is source-profile-owned and hybrid: deterministic rules for straightforward phrases/signals, compact LLM interpretation only for unstructured or ambiguous article language.
  - Initial semantic fields: `pipeline_status`, `product_type`, `age_restriction`, `date_delivery`, and unit buckets including `workforce_units`. Delivery-date projection examples such as "end of 2026" get documented normalization conventions and keep the raw text anchored.
  - AGENT.3 wires permits into the same interface with deterministic LADBS/source-profile rules first. LLM/agent interpretation is reserved for ambiguous permit descriptions, conflicting signals, or cross-stream exceptions.
  - Workforce units are tracked as a planned canonical unit bucket in ROADMAP E.6; interpreters must not silently collapse workforce units into affordable or market-rate counts before that field lands.

- **2026-05-05 (revision 19) — Direct provider APIs are current routing policy.**
  - For all already-built files and future AGENT steps until an explicit revision, use direct provider APIs: native Anthropic for Claude/agent calls and native OpenAI for GPT candidates.
  - Vercel AI Gateway remains a later operational option for centralized routing/monitoring, not part of the current A/B or default build path.
  - Before enabling Gateway, run a sweep of all LLM call sites, configs, pricing aliases, cost attribution, alerts, and deployment env vars to confirm routing is intentional and no direct-provider assumptions remain.

- **2026-05-05 (revision 20) — AGENT.1 live A/B costs recorded.**
  - Primary post-tightening smoke-set report: `data/output/news/ab_extract_20260505_174623.json`. Costs: Opus 4.7 `$0.202427`, Sonnet 4.6 `$0.107957`, GPT-5.4 `$0.054534` adjusted to current OpenAI cached-input pricing. The original JSON was generated before the cached-input pricing correction and shows `$0.072966`.
  - Supplemental requested report: `data/output/news/ab_extract_20260505_180014.json`. Costs: Opus 4.7 `$0.201802`, Opus 4.6 `$0.233970`, GPT-5.5 (`gpt-5.5-2026-04-23`) `$0.292948`. All three supplemental candidates parsed 5/5 articles.
  - Initial interpretation: Opus 4.7 remained the quality baseline; Opus 4.6 matched aggregate pipeline metrics but was more expensive in this run because it did not receive Anthropic cache-hit accounting; GPT-5.5 parsed cleanly but was slower and more expensive than Opus 4.7 on this prompt/fixture set.

- **2026-05-05 (revision 21) — Default extraction model selected.**
  - Researcher decision: keep Opus 4.7 as the default extraction model.
  - No runtime config/code change is required because Opus 4.7 was already the production default.
  - AGENT.1's remaining implementation blocker is retrieval: choose the embedding model and build the article chunk/indexing pipeline.

- **2026-05-05 (revision 22) — AGENT.1 article embedding/indexing path implemented.**
  - Selected direct OpenAI `text-embedding-3-small` for AGENT.1 article embeddings, matching the `vector(1536)` schema.
  - Added `news/embeddings.py`: accepted-reference gate queries, per-reference/whole-article chunk building, OpenAI embeddings client, active-chunk supersession, and `llm_cost_usage` reservation/true-up.
  - Added `tcg-pipeline news index-articles` in dry-run/apply form and wired the `news_backfill_chunk` worker task to the same implementation.
  - Follow-up hardening: unchanged active chunks are skipped before reservation/API calls, and the whole-article chunk is documented as broad retrieval context rather than per-claim acceptance.
  - Remaining validation: apply the AGENT.1 migration to a dev/staging database and smoke `index-articles --source-slug urbanize_la` before AGENT.2 retrieval tools consume the index.

- **2026-05-05 (revision 23) — AGENT.2 agent-run audit schema tightened.**
  - Follow-up migration `202605050030` makes `agent_runs.evidence_consulted` and `agent_runs.tool_calls_summary` `NOT NULL DEFAULT '[]'::jsonb` with JSON-array CHECK constraints, so observability arrays are enforced by the database rather than runner discipline.
  - `agent_runs.completed_at` is now `NOT NULL` because every current outcome enum value is terminal; runners insert terminal audit rows after completion/failure/kill-switch exit.
  - `agent_runs.intake_record_id` now documents the news convention explicitly: for news runs, use the stringified `news_articles.id`.
  - Added DB-backed schema contract tests for valid insert/join behavior, required non-empty `triggered_by`, failed-outcome `error_text`, nonnegative counters, JSON-array observability fields, and `ON DELETE SET NULL` audit preservation.
  - Production remains applied only through AGENT.1 (`202605040028`) until the AGENT.2 runner/cutover checkpoint.

- **2026-05-05 (revision 24) — AGENT.2 runner/profile skeleton implemented.**
  - Added `tcg_pipeline.agents` with source-agnostic `IntakeRecord`, `AgentTrigger`, `SourceProfile`, `NEWS_AGENT_PROFILE`, and `run_agent_for_intake`.
  - The runner is dependency-injected: real LLM/tool-loop execution is still deferred, but the audit/cost shell is live in code and tested with fake clients.
  - Implemented terminal `agent_runs` persistence for kill-switch, daily-budget rejection, client failure, and successful injected-client paths, including `agent_run_review_items` linkage and `llm_cost_usage` rows under `agent.news_v1`.
  - Added `news_v1` system-prompt scaffold emphasizing no outside knowledge, source/tool anchoring, bounded tool summaries, and final structured output.
  - Production news ingestion remains unchanged until the real AGENT.2 client/tools and cutover wiring land.

- **2026-05-05 (revision 25) — Runner-owned safety guardrails.**
  - The runner now owns wallclock timeout enforcement around the client call. Timeout writes `outcome='failed_timeout'`, releases the reservation, and preserves deterministic behavior.
  - The runner now performs post-hoc `tool_calls_summary` count checks and per-run cost checks. Tool-count violations write `failed_error`; cost overshoots write `failed_budget`, record actual cost, and raise a `SystemAlert`.
  - `SourceProfile.required_intake_fields` added. `NEWS_AGENT_PROFILE` requires `extraction_id` so the agent cannot run without the default-extraction anchor.
  - Added focused tests for timeout, tool-count violation, cost-overshoot alerting, and missing news extraction anchor.

- **2026-05-05 (revision 26) — Anthropic client and bounded tool dispatch shell.**
  - Added `AnthropicAgentClient` for the AGENT.2 runner seam: cacheable profile system prompt, Anthropic Messages tool loop, profile `max_output_tokens`, usage aggregation across turns, and final structured JSON parsing.
  - Added `AgentToolRegistry` / `AgentTool` primitives. The registry exposes only profile-allowed tools, rejects unregistered/disallowed calls, enforces hard output budgets with truncation metadata, and records compact tool-call summaries for `agent_runs.tool_calls_summary`.
  - Added fake-Anthropic and fake-tool tests for successful tool-loop execution, unknown-tool failure, invalid-final-JSON failure, allowed-tool exposure, and output truncation.
  - Production news ingestion remains unchanged; the next slices add real DB-backed tool handlers and wire news trigger decisions into the runner.

- **2026-05-05 (revision 27) — Tool dependency and payload contracts before DB-backed tools.**
  - `AgentRunRequest` now carries the runner-resolved `session_factory` and `settings`, giving DB-backed tools a single source-agnostic dependency path instead of closure-binding dependencies at every registration site.
  - `AnthropicAgentClient` validates final outcomes before the runner persists them. Only `completed` and `escalated` are accepted client outcomes; unrecognized strings become `failed_error` with an explicit parse/contract message.
  - News `intake.payload` convention is lean context only. Full article bodies and retrieval context must come through tools to avoid growing the base user message across every tool-loop turn.
  - Added tests for max-iteration loop failure, unrecognized final outcome handling, and runner-to-tool dependency propagation.

- **2026-05-05 (revision 28) — First DB-backed agent tool.**
  - `get_project_state` implemented as the first real tool handler. It reads `Project`, `project_field_resolution`, `project_latest_evidence`, and referenced evidence metadata through `AgentRunRequest.session_factory`, returning compact project state and field-level provenance without raw evidence bodies.
  - `build_agent_tool_registry()` now registers `get_project_state`; `build_anthropic_agent_client()` uses that registry by default when a caller does not inject one.
  - Tool-output budgets updated before retrieval tools land: `get_project_state` ≤1500 tokens; `search_articles_similar` target ≤2500 tokens with self-limited top-K/excerpts.
  - Pure DB tools have no extra LLM spend; query-embedding accounting was settled in revision 29 before `search_articles_similar` shipped.
  - Outcome parsing hardened: case-insensitive accepted outcomes, empty-string outcome rejected as malformed.

- **2026-05-05 (revision 29) — News retrieval tools complete the search-then-fetch pattern.**
  - `search_articles_similar` and `get_article_body` added in the same slice so the agent's first retrieval workflow is complete: search accepted chunks, narrow article candidates, then fetch stored body text only when needed.
  - `search_articles_similar` uses the existing OpenAI news embedding client, searches active non-superseded `news_article_chunks`, excludes whole-article chunks by default, caps `top_k` at 10, supports an optional `published_after` freshness filter, and returns ≤200-char excerpts with article IDs/URLs/source metadata/gate source plus matched project/evidence IDs when available.
  - `get_article_body` fetches title, URL, source slug, published date, and stored body text excerpt for one article; default 6000 chars, hard cap 12000 chars, no raw HTML.
  - News agent prompt updated to require the search-then-fetch pattern and to distinguish chunk evidence from full-body evidence.
  - Researcher decision: query-embedding cost for `search_articles_similar` is intentionally ignored as negligible and does not write cost rows.

- **2026-05-05 (revision 30) — First narrow news trigger wiring.**
  - `news/integration.py` now routes deterministic `new_candidate` matcher outcomes through the `news_v1` agent profile before writing final evidence/review output, unless `news_use_legacy_pass3=true`.
  - First mutation contract is deliberately narrow: agent `{"decision": "promote_existing_project", "project_id": "<uuid>"}` can convert a deterministic `new_candidate` into a confirmed match to an existing project; `no_change`, `escalated`, failed, budget-rejected, timeout, and killed-by-switch outcomes all fall back to the deterministic new-candidate review item.
  - Review items created after an agent run are linked back through `agent_run_review_items`; promoted matches carry the `agent_run_id` in matcher diagnostics and link any resulting status-change review item.
  - `agent_allow_live_llm=false` blocks construction of real agent clients; injected fake/test clients still run. Live production routing must explicitly set it true.
  - Malformed promotion confidence is rejected; missing confidence defaults to 0.93, while non-numeric or out-of-range confidence falls back to deterministic review rather than silently swapping the value.
  - `news_use_legacy_pass3=true` preserves the old Pass 3b re-extraction path for emergency fallback. Default AGENT.2 behavior is no Pass 3b; the agent handles `new_candidate` review/promotion and the deterministic review item remains the safe fallback.

- **2026-05-06 (revision 31) — Possible-match Type 3 routing.**
  - `possible` matcher outcomes with deterministic `candidate_project_ids` now route through `news_v1` using the `possible_multi_candidate` trigger.
  - The trigger string is kept for audit continuity even though the implementation now covers single-candidate possible matches too; the agent action remains "choose from matcher-provided candidate IDs or preserve review fallback."
  - The only mutating verdict is `{"decision": "confirm_existing_project", "project_id": "<uuid>", "confidence": 0.0-1.0}` and the selected project must be one of the matcher-provided candidate IDs. Off-list IDs, invalid confidence, `no_change`, `escalated`, failed, budget-rejected, timeout, and killed-by-switch outcomes all preserve the deterministic possible-match review item.
  - Produced fallback review items are linked through `agent_run_review_items`; confirmed Type 3 matches write evidence against the selected project and use `match_type='agent_confirmed_possible_match'`.
  - First live smoke completed on 2026-05-06: Rosa's Place possible-match candidate was confirmed to the matcher-provided project ID after `get_project_state`, with no fallback review item.
  - The Anthropic 400 visible during that local paste-link smoke was the known extraction temperature-deprecation retry path, not an agent-loop 400; future agent-turn 400s should still be investigated with provider response bodies.

- **2026-05-06 (revision 34) — Interim low-confidence Pass 3a cost gate.**
  - Default extraction skips legacy Pass 3a re-extraction for `pass2_low_confidence` when `news_use_legacy_pass3=false`; the audited `news_v1` low-confidence agent path now owns that case.
  - Structural-conflict Pass 3a re-extraction remains active until the harder-trigger / contradiction rewrite step moves those cases into the agent layer.
  - Setting `news_use_legacy_pass3=true` restores legacy low-confidence re-extraction for emergency fallback during AGENT.2 development.

- **2026-05-06 (revision 33) — Output-quality retry path implemented.**
  - `extract_retry_v1` is now the active non-agent retry prompt for default-extraction `parse_error`, `schema_invalid`, `refused`, and `truncated` outcomes.
  - The retry path makes at most two attempts, persists rows with `pass='extract_retry'`, records spend under `capability='extract_retry'`, and includes the previous parse status/error/output in the retry prompt while keeping the glossary out of the prompt.
  - Successful retry rows become the article's current extraction and feed normal integration; exhausted retries preserve the deterministic failure/review path and do not enter the `news_v1` agent loop.
  - Added DB-backed combined-trigger tests for `new_candidate + low_confidence` and `possible_multi_candidate + low_confidence`; fixture-based low-confidence rows are the repeatable smoke/regression recipe because organic low-confidence model output is nondeterministic.
  - Step 11 spot-check sampling should explicitly surface `low_confidence` runs where a deterministic `discarded` reference was promoted to an existing project, since that is the highest-authority news-agent override path.

- **2026-05-06 (revision 32) — Low-confidence trigger routing.**
  - References with `candidate_confidence='low'` and at least one populated load-bearing field now route through `news_v1` using the `low_confidence` trigger.
  - If `low_confidence` appears with `new_candidate` or `possible_multi_candidate`, the existing trigger's verdict contract governs and low-confidence fields are reasoning context. If `low_confidence` is the only trigger, allowed verdicts are `no_change`, `escalated`, or `promote_existing_project` for a deterministic low-confidence discard that should match an existing project.
  - Deterministic fallback still stands on no-change, escalation, budget rejection, timeout, killed-by-switch, invalid confidence, or invalid promotion project ID. Status-change review items produced from low-confidence confirmed evidence link back through `agent_run_review_items`.
  - First live smoke completed on 2026-05-06 with synthetic fixture `d14149b95c`: the deterministic matcher confirmed the synthetic project, the agent ran with `triggered_by=['low_confidence']`, called `get_project_state`, returned `decision=no_change`, linked to two low-priority status-change review items, and recorded `$0.071788` under `agent.news_v1`.

- **2026-05-05 (revision 13) — Initial slim default extraction prompt implementation.**
  - Initial slice removed `render_news_glossary` from `render_extraction_prompt` and sent only the extraction system template plus signal-flag registry as cacheable system blocks.
  - Senior review identified that changing `extract_v1` in place would make pre-cutover and post-cutover `news_extractions.prompt_id = 'extract_v1'` rows mean two different prompts. Revision 14 resolves that by restoring `extract_v1` as legacy and promoting the slim prompt as `extract_v2`.
  - `render_reextraction_prompt` explicitly keeps the legacy glossary path until AGENT.2 moves Pass 3a/3b into `news/extraction_legacy.py`. This preserves current fallback behavior while ensuring the AGENT.1 A/B harness measures the intended slim default-extraction path.

- **2026-05-05 (revision 12) — AGENT.1 provider/pricing hardening.**
  - **Gateway auth:** Vercel AI Gateway requires `AI_GATEWAY_API_KEY`; no fallback to `OPENAI_API_KEY`. This avoids confusing 401s during the A/B harness.
  - **Pricing readiness:** `MODEL_PRICING_USD_PER_MILLION` now covers Opus 4.7, Opus 4.6, Sonnet 4.6, GPT-5.5, GPT-5.4, and Haiku 4.5, with provider-prefix aliases for harness/Gateway IDs. Opus 4.7/4.6 pricing uses current Anthropic list pricing. GPT-5.5/GPT-5.4 cached-input cost uses current OpenAI list pricing; the remaining OpenAI accounting assumption is only that Responses usage reports cache-creation tokens as zero.
  - **Harness routing convention:** A/B harness runs Claude candidates through native Anthropic and GPT candidates through native OpenAI. Gateway-routed model IDs require a separate sweep/activation decision before use.
  - **Operational cleanup:** successful LLM calls clear stale provider-specific missing-key alerts for the relevant component. Added extraction-shaped OpenAI-compatible schema-invalid coverage before the harness.

- **2026-05-04 (revision 11) — `cost_cap_overrides` constraint + migration field mapping.**
  - **Strict CHECK on setter columns (Item 1).** Constraint tightened from `set_by_user_id IS NOT NULL OR set_by_actor IS NOT NULL` (at-least-one) to `(set_by_user_id IS NOT NULL) <> (set_by_actor IS NOT NULL)` (exactly-one). Audit clarity: there is one authoritative setter per override row; ambiguity (both populated) is not allowed. Comments updated to match.
  - **Migration step 5 audit-field population spelled out (Item 2).** Today's `news_cost_caps.override_set_by_user_id` is nullable, which interacts with the new exactly-one CHECK. Migration explicitly maps: when non-NULL, copy to `set_by_user_id`; when NULL, populate `set_by_actor = 'migration_<revision_id>'`. Same-row population of both is not allowed by the CHECK. Empty `override_note` rewritten to a default migration note since the new `note` column is NOT NULL.

- **2026-05-04 (revision 10) — Audit-contract and grep-correctness cleanup.**
  - **`cost_cap_overrides.set_by_user_id` UUID, not `set_by` TEXT (Item 1).** Today's `news_cost_caps.override_set_by_user_id` is `UUID(as_uuid=True)` ([db/models.py:1444](../../src/tcg_pipeline/db/models.py#L1444)). The new table preserves that contract: `set_by_user_id UUID` for researcher actions, plus an optional `set_by_actor TEXT` column for non-user setters (system, script, automated cap bumps). Revision 11 tightened the `CHECK` constraint to require exactly one setter column.
  - **§F section rename (Item 2).** "Open uncertainties" → "Resolved uncertainties and self-verification items." Each entry now tagged with ✅ Resolved / 🔍 Self-verify / 📌 Out of scope. Section intro updated to clarify none are blockers for AGENT.2 starting.
  - **ROADMAP AGENT.1 row aligned with design predicates (Item 3).** Per-reference indexing predicate spelled out as `ReviewDecision.action = ACCEPT AND (decision_type = 'accept_new' OR decision_type LIKE 'candidate_%')`. Cost schema description expanded from `cost_caps(bucket, ...)` to "three tables per design §5.8: `cost_caps` (config) + `cost_cap_overrides` (overrides preserving `set_by_user_id` UUID audit) + `llm_cost_usage` (rollup with `call_count` + reservation sentinels)."
  - **Revision-history grep noise (Item 4).** Revision 7 and revision 8 entries originally wrote the gate predicate as `decision_type IN ('accept_new', 'candidate_*')` — invalid SQL that would now match grep against the live predicate as a false positive. Both entries updated to use the corrected `OR/LIKE` form, with explicit notes documenting the original (broken) wording so the audit trail is intact but grep-clean.

- **2026-05-04 (revision 9) — SQL/syntax correctness fixes.**
  - **Partial-index predicate (Item 1).** `CREATE INDEX ... WHERE effective_until > now()` rejected by Postgres because `now()` is not immutable. Replaced with a regular index on `(bucket, effective_until DESC)`; the cap-reservation query keeps `WHERE effective_until > now()` as a runtime filter.
  - **Bucket naming straggler (Item 2).** §5.9 permit profile said `Cap bucket: permit` (singular). Fixed to `permits` (plural) — matches the consistent `news` / `permits` / `costar` / `pipedream` convention used everywhere else.
  - **`IN ()` with wildcard (Item 3).** `decision_type IN ('accept_new', 'candidate_*')` was invalid SQL. Wildcard matching doesn't work inside `IN ()`. Rephrased as `decision_type = 'accept_new' OR decision_type LIKE 'candidate_%'` in both §5.7 gate-1 contract and §5.7 pipeline trigger. Candidate decision types take the form `candidate_<index>` (e.g., `candidate_0`, `candidate_1`).
  - **Stale open-uncertainty entries in assessment doc (Item 4).** §F.4, §F.5, §F.6 still listed `proposed_value` / `evidence_ids` / `winning_evidence_id` as open. Marked each as resolved by the corresponding §I row (I.1, I.2, I.3).

- **2026-05-04 (revision 8) — Senior-developer second follow-up cleanup.**
  - **Stale `>50%` references (Item 1):** non-actionable. Senior dev's line references (assessment doc line 25, design doc lines 1022, 1110) were from a pre-revision-7 version. Current state is already `>10%` at all those locations.
  - **Cap-override capability (Item 2):** new `cost_cap_overrides` table preserves today's `news_cost_caps.override_hard_usd` / `override_until` / setter / note semantics. Time-bounded bumps (like the 2026-05-02 D.6 staging $50 bump) live here without polluting the steady-state cap config. AGENT.1 migration step 5 added to copy any active override into the new table.
  - **`llm_cost_usage` reservation + call_count (Item 3):** added `call_count` column (preserved from today's `news_extraction_costs`). Documented reservation row mapping: sentinel `provider='_reservation_'` / `model='_reservation_'` / `capability='reserved'` per bucket+date. Stale-reservation cleanup unchanged.
  - **Bucket naming (Item 4):** `permit_daily_warn_usd` / `permit_daily_hard_usd` references replaced with `cost_caps` row for `bucket='permits'` (plural). All `bucket=...` strings now use plural-consistent names: `news`, `permits`, `costar`, `pipedream`.
  - **Agent-run linkage in C.i assessment (Item 5):** assessment doc C11 row and §G action item updated. The `agent_run_review_items` join table is the authoritative source of truth; `payload.agent_run_id` may be a denormalized rendering hint only. Resolves the inconsistency between the design doc and the assessment doc.
  - **Retrieval gate wording (Item 6):** §5.7 pipeline section updated. Trigger predicate now reads `ReviewDecision.action = ACCEPT AND (decision_type = 'accept_new' OR decision_type LIKE 'candidate_%')`. The "non-discovery" framing was incorrect; replaced with explicit list of review item types that gate references (`status_change`, `override_contradiction`, `possible_match`, `new_candidate`). [Note: revision 8 originally wrote the predicate using `IN ('accept_new', 'candidate_*')` which is invalid SQL; revision 9 corrected this. The valid form is recorded here so grep against the live predicate doesn't return false positives in the revision history.]
  - **§5.8 prose accuracy (Item 7):** "Today's `news_cost_caps` mixes config and spend" was inaccurate — today already separates `news_cost_caps` (config) from `news_extraction_costs` (spend). Rewritten to "Today already separates config from spend; AGENT.1 generalization preserves that separation, adds the bucket dimension, and pulls temporary overrides into their own table."
  - **Lifecycle timestamps on `agent_runs` (Item 8):** confirmed already present from revision 7; no new edit needed.

- **2026-05-04 (revision 7) — Senior-developer follow-up cleanup pass.**
  - Stale `>50%` unit-delta references swept to `>10%` across §3 Q9 implementation, §4.2 pipeline diagram, §4.3 permits diagram, §9 AGENT.3 amendment, §9 AGENT.4 sketch, R6 mitigation. Assessment doc A1 and ROADMAP AGENT.4 updated. The `>50%/>25%` references that remain are intentional historical context in revision-history and "replaces earlier" text.
  - Cost-cap schema split (§5.8): `cost_caps` (config: bucket, effective_from, effective_to, daily_warn_usd, daily_hard_usd, notes) and `llm_cost_usage` (rollup: bucket, cost_date, capability, provider, model, token-count breakdown, spent_usd). Replaces the prior single-table `cost_caps(bucket, cost_date, ..., spent_usd)` design that conflated config and spend. AGENT.1 migration step rewritten.
  - All references to `news_cost_caps` and `news_extraction_costs` outside historical/migration text now reference `cost_caps` and `llm_cost_usage` (§2 Stays-unchanged, §5.9 SourceProfile field, §7 cap enforcement, §11 R3 mitigation).
  - Agent-to-review linkage promoted from `payload.agent_run_id` hint to a first-class join table `agent_run_review_items(agent_run_id, review_item_id)`. One agent run can produce multiple review items; column-on-review_items would force a 1:1 model. `payload.agent_run_id` may stay as a denormalized rendering hint.
  - Lifecycle timestamps added to `agent_runs`: `started_at` (NOT NULL) and `completed_at` (nullable until terminal outcome). `created_at` retained as row-insertion time.
  - Per-reference indexing contract (§5.7) rewritten as three explicit gates: gate 1 = committed-accept review where the decision is `accept_new` or any `candidate_<index>`; gate 2 = auto-applied corroborating confirmation written via `news_reference_auto_applied(article_id, reference_index, source_run_id, applied_at, gate)`; gate 3 = future high-confidence auto-apply (post-D.late.A). Decision-type names match the actual constants in [db/review_workflow.py:81-85](../../src/tcg_pipeline/db/review_workflow.py#L81). `news_article_chunks.gate_source` updated to three-value enum. [Note: this revision wrote the gate-1 SQL predicate in invalid form `IN ('accept_new', 'candidate_*')`; revision 9 corrected it to `decision_type = 'accept_new' OR decision_type LIKE 'candidate_%'`.]
  - Assessment doc TL;DR and §C C9 entry updated so they reflect the multi-alternative `proposed_value` decision (§I.1) directly rather than relying on the reader to apply overrides.

- **2026-05-04 (revision 6) — Senior-developer review feedback absorbed.**
  - Pass 3a/3b code preserved as `extraction_legacy.py` for one release cycle. New emergency settings flag `news_use_legacy_pass3` re-routes news through the imported legacy code if AGENT.2 has a regression that default-only mode can't paper over. Cleaner architectural cutover; safety net retained.
  - Cost cap table generalized from news-named (`news_cost_caps`) to `cost_caps(bucket, ...)` with one row per source bucket. AGENT.1 migrates today's data as `bucket='news'` rows. Future buckets (`permits`, `costar`, `pipedream`) plug in without schema changes.
  - `agent_runs` schema expanded with full observability: provider, model, profile_version, prompt_version, triggered_by-as-list, token-count breakdown, latency, source_run_id, scrape_job_id, error_text, tool_calls_count, wallclock_seconds. If sprint moves fast, observability is the safety mechanism — every field populated for every run.
  - A/B harness (§5.1) reframed from extraction-JSON-only to product-impact metrics: parse outcomes, reference counts, matcher outcome distribution, agent trigger proxy rate, projected review item counts, measured extraction cost now and all-in cost after AGENT.2 agent pricing exists, payload quality (researcher spot-grade). Adds ~half-day to AGENT.1 scope.
  - New §5.4.1 tool output budgets. Hard token caps per tool with truncated/total-results contract. Prevents tool returns from consuming the per-run cost budget.
  - §5.7 per-reference indexing contract tightened: indexed iff `review_item.state='committed'` AND latest decision `action='accept'` (or auto-applied via separate audit-row gate). Edge cases for multi-reference articles and re-extraction supersession enumerated. New table `news_reference_auto_applied` and column `gate_source` on `news_article_chunks`.

- **2026-05-04 (revision 5) — Researcher decisions on contradiction impact assessment open items.**
  - Multi-alternative `proposed_value`: agent-produced contradiction items use `payload.proposed_alternatives: list[{value, source_evidence_id, source_summary, agent_confidence}]` instead of a single `proposed_value`. STATUS_CHANGE items keep single-value shape. Decision-card UI renders alternatives compactly with hover-revealed source detail. Adds ~1-2 days to AGENT.2 scope. See `ci_contradiction_impact_assessment.md` §I.1.
  - Distinct actor label: agent-produced contradictions log under `"Agent contradiction detection"` in the Changes tab (vs today's `"Contradiction detection"` for fallback). See §I.4.
  - Unit-delta trigger threshold: uniform 10% across all source profiles (replacing earlier >50%/>25% sketches). See §I.6.
  - Risk R2 updated: original 15-20% fire-rate projection revised to 25-30% with the 10% threshold. Cost guardrails handle this; first-week monitoring expects higher rate.
  - Override semantics clarified: skip-list mechanism prevents same-transaction re-detection only; new evidence later still produces new review items per `EVIDENCE_LAYER_DECISIONS.md` §22 review-protected semantics. See §I.5.
  - Other resolutions: focused `evidence_ids` on display (wide list goes into `agent_runs.evidence_consulted[]`); `winning_evidence_id` tiebreak is "newest evidence_date." See §I.2, §I.3.

- **2026-05-04 (revision 4) — Source-agnostic generalization.**
  - Reframed the agent layer as the project-attribution decision layer for *any* structured intake. News and permits are the first two consumers; CoStar (AGENT.4) and Pipedream (AGENT.5) are deferred follow-ons that plug into the same architecture without runner changes.
  - §5.3 agent runner: input is a generic `IntakeRecord` with a `source_type` discriminator. Same code regardless of source.
  - §5.4 tools: split into core (always available) and source-specific (declared per profile).
  - §5.6 evidence schema: `agent_runs` keyed on `(intake_source_type, intake_record_id)` rather than news-specific identifiers.
  - §5.9 (new): source profiles. Each intake stream declares triggers, allowed tools, prompt template, cap bucket, kill switch. News profile and permit profile are first; CoStar and Pipedream profiles sketched but not built.
  - §9: AGENT.4 (CoStar agent path) and AGENT.5 (Pipedream agent path) added as deferred follow-ons. Pipedream-specific refactor of `db/seed.py` flagged as part of AGENT.5 scope. Phase I.1 onboarding gains "agent source profile" as a required artifact. Phase J.1 model-config console generalized to per-profile.
  - Sprint scope unchanged: AGENT.1/2/3 still ship news + permits in one cutover. The architectural generalization adds ~5–10% to sprint scope (clean source-profile abstraction, source-agnostic schema keys) and dramatically reduces retrofit cost when CoStar and Pipedream paths land later.

- **2026-05-04 (revision 3) — Marathon sprint + glossary option 3 + operational guardrails.**
  - Sprint structure: all three stages built in one continuous workstream, shipped as a single cutover. Permits live from day one. Replaces the original staged-rollout shipping plan.
  - Glossary decision: skip option 2 (Pass 1 entity-linked slice) and go directly to option 3 (drop in-prompt glossary entirely; rely on agent tools for registry knowledge). Folded into AGENT.1.
  - Operational guardrails (new §5.8): scoped daily cost caps (separate buckets for news vs permits) plus runtime kill switches (`agent_enabled_for_news`, `agent_enabled_for_permits`). The cap and kill switch replace the staged-rollout observation period as the safety mechanism — researcher direction is "build with it on, monitor closely, dial back quickly if too expensive."
  - Contradiction impact assessment: assigned to Claude Code with explicit authoring rules (very concise, `⚠ HUMAN REVIEW` markers on uncertain rows, researcher does a 10-minute read before C.i code is touched). New §5.5.0 covers the deliverable framing.
  - Roadmap structure: three D.late.AGENT items retained as dependency-tracking units within the sprint, not separate shipping events.
  - Risk R6 (permit cost) updated: mitigation is now (1) trigger narrowness, (2) scoped daily cap, (3) runtime kill switch.

- **2026-05-04 (revision 2) — Researcher direction on the four proposed modifications.**
  - Cost target softened from hard gate to aspirational. Stage 1 measures Opus 4.7, Sonnet 4.6, and GPT-5.4 as a three-way A/B and reports cost-quality data; researcher picks. No model choice fails on cost alone.
  - GPT-5.4 cross-provider abstraction promoted from "deferred" to part of Stage 1 build, since the A/B can't measure GPT-5.4 without it.
  - Stage 3 (agent on permits) does not require a quantified permit-dedup-pain measurement; researcher confirmed the pain is real and the build proceeds regardless.
  - Contradiction-detection rewrite (Q5) does not get a feature flag. Mitigation is the §5.5.1 mandatory upstream/downstream impact assessment before any C.i code is touched, plus §5.5.2 regression coverage. Rewrite must explicitly enumerate every code path that depends on C.i's contradiction outputs, including non-news callers, schema columns, UI rendering, and audit tooling.
  - Worker model decision (R8) resolved: one job per article that needs agent escalation. Discovery + Pass 0/1/2 stays as one batch job; agent integration splits into per-article jobs each with a 300s wallclock cap, well inside the 900s scrape job timeout.
  - Cost framing in §7 rewritten to show all three candidate default-extraction models and to drop the "miss the target" failure mode.

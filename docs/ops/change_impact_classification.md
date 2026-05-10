# Change Impact Classification — Reset Granularity Decision Guide

> **Audience:** Claude Code / Codex agents and human contributors working through `AGENT.reset` stabilization cycles or post-production prompt/policy iteration.
> **Purpose:** When making a change, determine the minimum reset needed to validate it end-to-end. Pick the cheapest tier that fully exercises the change.
> **Cross-references:** `ROADMAP.md` §`AGENT.reset` row and `STAB.*` rows; `docs/specs/agentic_escalation_design.md`; `docs/specs/semantic_interpretation_layer_design.md`; `docs/specs/news_research_design.md`.

---

## 1. Reset granularity tiers

Cheapest → most expensive. Pick the lowest tier that fully exercises the change.

| Tier | What it does | LLM cost | Time at LA scale |
|------|--------------|----------|------------------|
| **0** | No reset. Change takes effect on next ingestion or next page load. | $0 | 0 |
| **1** | Re-resolve only — `tcg-pipeline resolve-all --apply` over existing evidence. | $0 | minutes |
| **2** | Re-extract / re-run agent / re-run semantic from stored Pass 0 article bodies. No re-fetch. | LLM only | 10–30 min for ~75 articles |
| **3** | Re-ingest news only. Wipe news data tables + re-run paste-a-link smokes + news cron at the configured backfill window. | Fetch + LLM | 20–40 min |
| **4** | Re-ingest one source family (permits / CoStar / Pipedream). | Source-dependent | varies |
| **5** | Full `AGENT.reset` (R.1–R.6.5). Truncate-and-reseed everything except config tables. | Full backfill cost | 1–2 days |
| **Reset user actions** | Wipe review decisions / overrides / notes; preserve evidence + ingestion artifacts + agent runs. Followed by Tier 1 re-resolve. | $0 | seconds |

---

## 2. Decision rules by change type

### 2.1 UI/UX and pure-display changes → Tier 0

- Frontend layout, copy, component additions/modifications
- New dashboard tiles, filter presets, sort orders, saved views
- Keyboard shortcut additions, badge colors, map tile providers
- Activity log filter additions
- Pagination / virtualization
- Authentication / allowlist changes
- Read-view (Postgres view) definition updates that don't backfill data

### 2.2 Pure config changes → Tier 0

- `cost_caps` thresholds, per-source `schedule_cron`, `backfill_window_days`
- Source pause/resume, kill-switch flips (`agent_enabled_for_news`, `news_use_legacy_pass3`, etc.)
- Allowed-email lists, RLS grants on read-only views
- Per-host crawl-delay / rate-limit tuning
- Logging or telemetry additions

### 2.3 Resolution-engine changes → Tier 1

- Field resolver logic in `src/tcg_pipeline/resolution/fields/` (status, units, delivery_year, developer, product_type, age_restriction, workforce_units)
- Likelihood / confidence rollup formulas (`resolution/likelihood.py`, `resolution/confidence.py`)
- Source-tier priority tiebreakers (`config/source_tiers.yaml`)
- Override mode / baseline behavior in `db/researcher_overrides.py`
- C.i contradiction detection thresholds in `review/contradictions.py`
- Status forward-only progression rules (`status_rules.py`)

### 2.4 Per-source extraction prompt changes → Tier 2

- News extraction prompt (`extract_v2`)
- News triage prompt (Pass 2a Haiku)
- News Pass 2c semantic prompt (`interpret_v1`)
- News re-extraction retry prompt (`extract_retry_v1`)
- Permit extraction prompts (when AGENT.3 ships)

> ⚠ **Tier 2 currently has tooling gaps** — see §4 below. Until those CLIs exist, Tier 2 changes effectively require Tier 3.

### 2.5 Agent prompt or trigger logic changes → Tier 2 (or Tier 3 if integration shape changes)

- `news_v1/system.md` agent system prompt
- Trigger detection (`_pass1_pass2_conflicts_*`, `_material_contradictions_*`, `_override_contradictions_*` in `news/integration.py`)
- Source profile changes (`agents/profiles.py` — triggers, allowed tools, cost caps, max_tool_calls)

**Tier 3 if** the change affects review-item *creation shape* (new payload fields, new `proposed_alternatives` structure, new `system_recommendation` shape) — you'll want to re-run integration to see the new payloads in the queue.

### 2.6 Source-specific non-prompt logic

| Change | Tier |
|--------|------|
| News matcher (`matching/news_matcher.py`) | 2 — re-run matcher over existing references |
| News integrator field mapping (`news/integration.py`) | 2 |
| News structural extraction Pass 1 (`news/structural.py`) | 3 — runs at fetch time |
| News article body sanitization (`news/extraction.py` Pass 0 path) | 3 — runs at fetch time |
| News evidence integration (`news/integration.py` post-resolve) | 2 |

### 2.7 Per-market or jurisdiction policy changes → Tier 2

- `jurisdiction.permit_data_quality` policy (`low` / `high`)
- Per-market `semantic_glossary.yaml` addendum
- Reason-code registry entries (`semantic/reason_codes.py`)
- News status corroboration policy

Affects only the relevant market's articles; can scope re-extraction by `news_articles.source.market_id` (most LA articles are market-unscoped today, so the scope is broader in practice).

### 2.8 Schema migrations

- **Additive** (new columns, new tables, new indexes) → **Tier 0**. Migration alone; nullable defaults handle existing rows.
- **Constraints** (new NOT NULL, new uniqueness, new FK) → **Tier 1** if existing data must re-resolve to satisfy. Backfill DDL runs as part of the migration.
- **Destructive** (drop column, narrowing type, removing FK) → **Tier 5** if a clean backfill isn't writable. Take a fresh `pg_dump` first regardless.

### 2.9 Source family changes → Tier 4

- LADBS adapter changes (datasets, filters, evidence-emission rules) → re-ingest permits
- New permit dataset, new news source onboarding
- CoStar field-mapping changes → re-import CoStar
- Pipedream field-mapping changes → re-import Pipedream

### 2.10 Cross-system / blast-radius changes → Tier 5

- Address normalizer (`matching/normalizer.py`) — affects every source's match key
- Matcher core (`matching/matcher.py`) — non-news attribution path
- Differ logic (`matching/differ.py`) — change detection across all sources
- Evidence schema or source-tier rebalance
- Developer canonicalization registry — Tier 1 + `canonicalize-developers --apply` if entries are stable; Tier 5 if registry itself needs re-derivation from source evidence

---

## 3. The "reset user actions" tool

For testing the review workflow repeatedly without re-ingesting articles. Spec lives in `ROADMAP.md` row `STAB.reset-user`. Implementation pending.

### 3.1 What it clears

- `review_decisions` (staged + committed)
- `review_items.state` reset to `open` (un-stage / un-commit)
- `researcher_overrides` (delete, not soft-clear, for the rerun use case)
- `project_notes` (all rows; the table is fully user-content)
- `change_log` rows where actor is human (filter by `reviewed_by_user_id IS NOT NULL` or actor identity columns)
- `status_history` rows where source is human-driven; preserve collector/resolver-driven entries
- `project_relationships` rows created by humans
- **Optionally** (separate flag): manually-created `projects` rows (those without `project_source_records` ties to seed/collector data)

### 3.2 What it preserves

- `evidence`, `news_articles`, `news_extractions`, `news_project_references`, `news_article_chunks` (embeddings — expensive to recompute)
- `agent_runs`, `agent_run_review_items` — system audit; useful to compare across review test cycles
- `source_runs`, `system_alerts`, `llm_cost_usage` — system audit
- `resolution_log` — gets overwritten on next `resolve-all` anyway
- Auto-generated `review_items` (the rows themselves; just with state reset)
- All config tables

### 3.3 Workflow

1. **Pre-check** — refuse unless `RESET_TOOLS_ENABLED=true` env flag is set.
2. **Production lockout** — refuse if the configured DB URL matches a known-production host, even with the flag on.
3. **Backup** — take a `pg_dump` snapshot to a configurable path. Always. Cheap insurance. Abort the reset if the dump fails.
4. **Preview** — print per-table row counts of what will be deleted. Require explicit `--confirm` (or interactive yes/no).
5. **One transaction** — all DELETEs and the state reset run in a single transaction. A failure mid-way rolls back.
6. **Re-resolve** — run `resolve-all --apply` to recompute project field values without override interference. Some fields shift back to source-derived values; that's the desired post-reset state.
7. **Audit** — log a `system_alerts` entry recording the reset action with timestamps, actor, and table counts.

### 3.4 Subtleties

- **User vs system actor in `change_log`**: today's schema has `reviewed_by_user_id` and `reviewed_by_email` from C.h. Legacy rows from before C.h may have null user IDs and look "system." A filter like `WHERE reviewed_by_user_id IS NOT NULL OR actor LIKE 'researcher%'` catches the human cases. Verify against actual production-shaped data the first time.
- **Manually-created projects don't have a flag today**. Three options: (a) skip them in the reset and let the user wipe manually if needed; (b) infer from `project_source_records` absence; (c) add a `created_by_user_id` column on projects. Option (a) is the simplest first cut.
- **`researcher_overrides.cleared_at`**: today's schema soft-clears by setting `cleared_at`. The reset should DELETE rather than soft-clear — cleaner for the rerun use case, and the audit lives in the `pg_dump` snapshot anyway.
- **C.tail.6 advisory locks** are per-transaction; the reset transaction is unaffected by them.

---

## 4. Tooling gaps to close before Tier 2 becomes a real option

These CLIs do not exist today. Until they do, "Tier 2" changes effectively require Tier 3 (re-ingest from scratch).

| Proposed CLI | Purpose | Triggered by |
|--------------|---------|--------------|
| `tcg-pipeline news re-extract-all` | Iterate `news_articles` with stored Pass 0 bodies; re-run Pass 2a/2b/2c on the new prompt; optionally re-run matcher → integrator. No re-fetch. | Extraction / triage / Pass 2c prompt changes |
| `tcg-pipeline news re-run-agent` | Re-run the agent loop on existing extractions where triggers fire. No re-fetch, no Pass 2 re-extraction. | Agent prompt or trigger-logic changes |
| `tcg-pipeline news re-run-semantic` | Re-run Pass 2c only, against existing Pass 2b extractions and per-market glossaries. | Glossary / jurisdiction policy changes |

All three should:

- Run in a configurable scope (single article ID, market filter, date range, all).
- Support a `--dry-run` mode that reports projected LLM cost before any spend.
- Reserve cost via the existing `llm_cost_usage` cap-accounting machinery.
- Use `evidence.superseded_at` to mark superseded prior evidence (consistent with existing re-extraction conventions).
- Write `agent_runs` audit rows for any agent re-runs.

---

## 5. When in doubt

Default to the highest plausibly-relevant tier. At LA scale (4-week stabilization window, ~$15–25 per cycle), Tier 3 is cheap enough that erring up is usually fine; the time cost (~30 minutes) is the larger constraint, not dollars.

For a quick sanity check before a full cycle: run `tcg-pipeline news ab-extract` against the smoke fixture set with the new candidate config — that produces per-article output divergence in a rollback-only transaction without committing anything. Useful for catching obviously-broken changes before paying for a real cycle.

---

## 6. What NOT to do

- **Don't truncate `evidence` directly** — strips provenance and breaks resolution audit. Use the source-scoped Tier 3/4 procedures.
- **Don't reset `agent_runs` selectively** — `agent_run_review_items` has FK constraints that will break.
- **Don't TRUNCATE config tables** (`markets`, `jurisdictions`, `source_registrations`, `news_sources`, `news_signal_flag_registry`, `cost_caps`, `cost_cap_overrides`) outside a full Tier 5 reset; their rows survive every stabilization cycle.
- **Don't skip the `pg_dump` backup** before any Tier 4+ operation, even in stabilization. Cheap to take; expensive to rebuild from without one.
- **Don't do partial Tier 2** — re-running only Pass 2c without also re-running integration leaves review items locked to a stale extraction version. Run the full chain or none of it.
- **Don't run `reset-user-actions` without `RESET_TOOLS_ENABLED=true`** — it will refuse, but don't try to bypass the guard.
- **Don't run any reset tool against production** even when `RESET_TOOLS_ENABLED=true`. The production DB host check is the last line of defense; respect it.

---

## 7. Quick reference table

| Change type | Tier | Notes |
|-------------|------|-------|
| Frontend / display only | 0 | |
| Cost caps, schedules, kill switches | 0 | |
| Resolver formula | 1 | `resolve-all --apply` |
| Extraction prompt | 2 | needs `news re-extract-all` (gap) |
| Agent prompt | 2 | needs `news re-run-agent` (gap) |
| Pass 2c semantic prompt | 2 | needs `news re-run-semantic` (gap) |
| Glossary addendum (per-market) | 2 | scoped to market |
| News matcher / integrator | 2 | |
| News structural Pass 1 | 3 | runs at fetch |
| Jurisdiction policy | 2 | |
| Additive migration | 0 | |
| Constraint migration | 1 | |
| Destructive migration | 5 | |
| LADBS adapter | 4 | re-ingest permits |
| Address normalizer | 5 | cross-system |
| Matcher / differ core | 5 | cross-system |
| Review workflow UI test rerun | reset-user-actions + 1 | |

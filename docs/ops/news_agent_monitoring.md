# News Agent Monitoring

Use this runbook during the news-first cutover, the first Urbanize cron observation,
and each `AGENT.reset` stabilization cycle. The report is read-only: it queries
`source_runs`, `agent_runs`, `agent_run_review_items`, `llm_cost_usage`, and
`news_semantic_interpretations` / `system_alerts`; it does not enqueue jobs or
call an LLM.

## First Cron Check

Urbanize LA is scheduled for 7:30 AM Pacific (`30 7 * * *` LA time per
`news_sources.schedule_cron`). Run this after the first expected cron window has
had time to finish, using a lookback that covers the cron plus queue lag.

```powershell
tcg-pipeline news-agent-smoke-report `
  --hours 18 `
  --source-name urbanize_la `
  --min-source-runs 1 `
  --max-total-cost-usd 10 `
  --output data/output/news_agent_smoke/news-agent-first-cron-YYYYMMDD.json
```

The `--min-source-runs 1` flag is load-bearing: it makes the command fail if the
scheduler did not produce an Urbanize `source_run`.

## Daily Observation

Run once per day while news is in the observation window.

```powershell
tcg-pipeline news-agent-smoke-report `
  --hours 24 `
  --source-name urbanize_la `
  --min-source-runs 1 `
  --max-total-cost-usd 5 `
  --output data/output/news_agent_smoke/news-agent-YYYYMMDD.json
```

Use `--min-agent-runs` only when the article set is expected to trigger the news
agent. A normal Urbanize cron can legitimately produce zero agent rows if no
article needs escalation.

For the duplicate-trigger watch, run a rolling 3-5 day window after regression
handling is live and set the ceiling from observed post-migration volume:

```powershell
$duplicateCeiling = <approved ceiling from observed post-migration data>
tcg-pipeline news-agent-smoke-report `
  --hours 120 `
  --source-name urbanize_la `
  --min-source-runs 3 `
  --max-status-regression-duplicate-projects $duplicateCeiling `
  --output data/output/news_agent_smoke/news-agent-duplicate-watch-YYYYMMDD.json
```

## Curated Paste-Link Smokes

Use curated paste-link smokes to exercise rare news-agent paths that organic
Urbanize cadence may not hit quickly.

```powershell
tcg-pipeline news paste-link-smoke "https://example.com/article-url?tcg_smoke=YYYYMMDDa"
tcg-pipeline news-agent-smoke-report `
  --hours 2 `
  --source-name news_paste_a_link `
  --min-source-runs 1 `
  --min-agent-runs 1 `
  --output data/output/news_agent_smoke/news-agent-paste-link-YYYYMMDDa.json
```

Suggested rare paths: `material_contradiction`, `override_contradiction`,
`pass1_pass2_conflict`, and `low_confidence`.

For curated regression smokes that are expected to create a review card, require
the trigger and linked review-item count explicitly:

```powershell
tcg-pipeline news-agent-smoke-report `
  --hours 2 `
  --source-name news_paste_a_link `
  --min-source-runs 1 `
  --min-agent-runs 1 `
  --require-triggers status_regression_candidate `
  --min-status-regression-review-items 1 `
  --output data/output/news_agent_smoke/news-agent-regression-YYYYMMDDa.json
```

If the curated article is expected to produce a `dismiss` verdict, omit
`--min-status-regression-review-items`; the agent run and trigger should still
appear, but no review card is created by design.

## Reading The Report

- `source_run_count`: number of news `source_runs` in the window. For daily
  Urbanize cron, this should be at least 1.
- `agent_run_count`: number of `news_v1` agent rows in the window. Zero can be
  valid for organic cron.
- `outcome_counts` / `trigger_counts`: agent outcome and trigger distribution.
  Investigate `failed_*` outcomes immediately.
- `review_item_type_counts`: linked review-item distribution across news-agent
  rows in the window. This counts `agent_run_review_items` linkages, not
  globally distinct review cards, so a card linked to multiple runs can appear
  more than once.
- `status_regression_agent_run_count`: number of `news_v1` runs whose trigger
  set included `status_regression_candidate`.
- `status_regression_review_item_count`: number of linked
  `status_regression_review` cards. Use `--min-status-regression-review-items`
  for curated smokes where a regression card is expected.
- `status_regression_open_count` / `status_regression_auto_accepted_count`:
  distinct linked regression cards by review status. Use these during
  `NEWS_REGRESSION_AUTO_APPLY_ENABLED` rollout to distinguish queueing from
  auto-accepted mutations.
- `status_regression_duplicate_project_count`: distinct
  `(project_id, current_status, proposed_status)` tuples that produced two or
  more `status_regression_candidate` runs in the report window. The project ID
  comes from `agent_runs.project_id`; the current/proposed statuses come from
  `agent_revised_verdict` first because dismiss outcomes often have no review
  card, with linked `status_regression_review` payloads as fallback. Use
  `--max-status-regression-duplicate-projects` as the automated replacement for
  manual cross-day duplicate checks. The flag defaults to off. On 2026-05-12
  the configured Supabase DB was found one migration behind and then upgraded to
  `202605110038`; historical rows before that migration cannot calibrate a
  duplicate ceiling. Observe post-migration regression volume first, then pass
  an explicit ceiling.
- `agent_run_total_cost_usd`: sum of `agent_runs.cost_usd` for `news_v1`.
- `cost_usage_total_usd`: full `news` bucket cost across all capabilities in
  `llm_cost_usage`, including extraction, triage, semantic, retry, embeddings,
  and agent calls. This is the value validated by `--max-total-cost-usd`.
- `cost_usage_by_capability`: per-capability cost/call/token breakdown. Use this
  to detect whether semantic or extraction spend dominates the day.
- `cost_cap_days`: daily warn/hard cap context for the `news` bucket.
- `semantic_parse_status_counts`: Pass 2c parse health in the window. Any
  `truncated`, `refused`, `parse_error`, or `schema_invalid` row needs review
  before cron sign-off because status promotion depends on usable semantic
  output.
- `semantic_issues`: compact details for non-OK Pass 2c rows, including the
  article/extraction IDs, prompt ID/version, output tokens, and parse error.
  An active `news_semantic_parse_failed` alert is the operator-facing contract:
  raw Pass 2b status signals for that article are intentionally not promoted,
  and no fallback review item is created until a researcher reviews the article
  or the semantic pass is rerun successfully.
- `alerts`: active or recent news-related alerts. The CLI prints the first 50;
  if `alerts_truncated=true`, query `system_alerts` directly before signing off.
- `missing_review_link_count`: informative by default. Some news-agent outcomes
  auto-apply without fallback review items, so review links are not required
  unless `--require-review-links` is passed.

## Live-LLM Kill Switches

Three Render dashboard env vars gate live LLM calls. The global
`AGENT_ALLOW_LIVE_LLM` is the universal off switch. Two per-profile switches
optionally override the global for one agent type without affecting the other.

| Env var | Type | Effect |
|---|---|---|
| `AGENT_ALLOW_LIVE_LLM` | `bool` (default `false`) | Global. When `true`, agent profiles can make live Anthropic calls unless a per-profile override blocks them. |
| `AGENT_ALLOW_LIVE_LLM_NEWS` | `bool` or unset | When set, overrides the global for the `news_v1` profile only. Unset → falls back to the global. |
| `AGENT_ALLOW_LIVE_LLM_PERMITS` | `bool` or unset | When set, overrides the global for the `permit_v1` profile only. Unset → falls back to the global. |

The resolver is `Settings.live_llm_allowed_for(profile_name)` in
[settings.py](../../src/tcg_pipeline/settings.py). All gating call sites read
through this helper, so the per-profile override is consistent across the
agent runner, news integration, and permit collection paths.

**When to set a per-profile override.** During steady-state production both
per-profile vars should be unset and the global is the single source of truth.
Set a per-profile override only during an incident where you need to kill one
profile's LLM calls without affecting the other. Examples:

- News incident: cost spike, output-quality regression, model misbehaving on
  Urbanize articles. Set `AGENT_ALLOW_LIVE_LLM_NEWS=false`, leave
  `AGENT_ALLOW_LIVE_LLM_PERMITS` unset (or `true`). Permit agent keeps running.
- Permit incident: same shape, set `AGENT_ALLOW_LIVE_LLM_PERMITS=false` and
  leave news running.
- Total kill: set `AGENT_ALLOW_LIVE_LLM=false`. Per-profile vars become
  irrelevant unless one is explicitly set to `true` to keep that one profile
  alive.

**Audit signal when a gate blocks a call.** The agent runner writes an
`agent_runs` row with `outcome=killed_by_switch` and
`error_text="agent_allow_live_llm gate is off for profile <name>; no
AgentClient was provided"`. The news/permit smoke reports surface these via
`outcome_counts`. Investigate any unexpected `killed_by_switch` count.

## Recovering Stranded Articles

A "stranded" article is one that completed Pass 0/1/2a/2b cleanly but did not
finish Pass 2c, matching, agent, or integrator — typically because a transient
provider error (Supabase pooler SSL drop, Anthropic 5xx) crashed the
`news_agent_integrate` worker job. The article is left with `triage=relevant`,
`current_extraction_id` set, candidate references in `match_status='pending'`,
no `agent_runs`, and no semantic interpretation. Without explicit recovery the
article sits silently broken — the daily smoke report did not surface this
state before the visibility fix (D.6, 2026-05-12).

### Detection

A failed `news_agent_integrate` job now raises a `news_integrate_failed`
system_alert scoped by `article_id`. The alert surfaces in the smoke report's
`alerts` section because of the existing `news_*` alert clause. Look for:

```
warning news_integrate_failed: news_agent_integrate failed for article <id>;
  article references remain pending. Run `tcg-pipeline news reprocess-stranded
  --article-id <id> --apply` to re-enqueue.
```

The alert message includes the exact recovery command. The alert is cleared
automatically the next time an integrate job for the same article completes
successfully.

To audit independently of the alert (e.g., for historical stranded articles
that predate the alert wiring):

```powershell
tcg-pipeline news reprocess-stranded --days 30
```

This is the default dry-run mode and prints any stranded articles plus the
recovery context (source_run_id, original triggers, last failed job error).

### Recovery

```powershell
tcg-pipeline news reprocess-stranded --apply
```

Apply mode enqueues a fresh `news_agent_integrate` job per stranded article,
reusing the original `trigger_reasons` from the failed job's payload where
available. Use `--article-id <uuid>` (repeatable) to target specific articles.
Use `--include-no-failed-job` to also reprocess articles with the structural
stranded signature but no tracked failed `scrape_jobs` row — by default these
are excluded because they are typically pre-Pass-2c historical staging-smoke
artifacts that predate the full pipeline.

Local shells cannot reach Render's private Redis, so `--apply` from a workstation
will create QUEUED `scrape_jobs` rows that never get picked up. Run apply mode
from inside the Render worker network: dashboard one-off shell or
`POST /v1/services/{worker_service_id}/jobs` with `startCommand`
`tcg-pipeline news reprocess-stranded --apply`.

### Repeat Failures

If the same article hits `news_integrate_failed` three or more times in a row,
the failure is not transient — there is a deterministic interaction between
that article's shape and the integrator. Historically (2026-05-12) this
surfaced as a Supabase pooler SSL drop during a long agent loop with many
triggers; the fix was TCP keepalives on the engine ([commit
332a967](https://github.com/golderr/pipe_agent_ii/commit/332a967)). Future
repeat-failure patterns warrant similar root-cause investigation rather than
indefinite retry. The alert detail captures `job_id`, `source_run_id`, and the
original `trigger_reasons` to support that investigation.

## Window Semantics

The report filters source runs by `source_runs.run_timestamp` and agent runs by
`agent_runs.created_at`. A scrape that starts near a window boundary can produce
agent rows just outside the source-run window. If this appears in daily
observation, rerun with a wider `--hours` value before treating it as a bug.

## Backfill Posture

Do not run the Urbanize backfill before `AGENT.reset`. D.B remains sequenced at
R.6.5 so the backfill lands on a clean post-reset database and can be priced
using observed production fire rates from this report.

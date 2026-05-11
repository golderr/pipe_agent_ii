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

## Reading The Report

- `source_run_count`: number of news `source_runs` in the window. For daily
  Urbanize cron, this should be at least 1.
- `agent_run_count`: number of `news_v1` agent rows in the window. Zero can be
  valid for organic cron.
- `outcome_counts` / `trigger_counts`: agent outcome and trigger distribution.
  Investigate `failed_*` outcomes immediately.
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

## Window Semantics

The report filters source runs by `source_runs.run_timestamp` and agent runs by
`agent_runs.created_at`. A scrape that starts near a window boundary can produce
agent rows just outside the source-run window. If this appears in daily
observation, rerun with a wider `--hours` value before treating it as a bug.

## Backfill Posture

Do not run the Urbanize backfill before `AGENT.reset`. D.B remains sequenced at
R.6.5 so the backfill lands on a clean post-reset database and can be priced
using observed production fire rates from this report.

# AGENT.reset Runbook

Use this runbook for each `AGENT.reset` stabilization cycle and for the final
settle cycle. It supplements the `AGENT.reset` row in `ROADMAP.md`; it does not
replace the backup/truncate/reseed sequence there.

## Artifact Directory

Create one directory per cycle:

```powershell
$cycle = "reset-YYYYMMDD-a"
New-Item -ItemType Directory -Force "data/output/agent_reset/$cycle"
```

Store all JSON reports and operator notes under that directory. The cycle should
be reviewable from artifacts alone without querying production manually.

## Required Cycle Artifacts

### News Observation

Run after R.6 re-enables news cron and again after R.6.5 backfill.

```powershell
tcg-pipeline news-agent-smoke-report `
  --hours 24 `
  --source-name urbanize_la `
  --min-source-runs 1 `
  --max-total-cost-usd 25 `
  --output "data/output/agent_reset/$cycle/news-agent-24h.json"
```

For curated regression smokes, require the regression trigger and linked card
when the expected verdict is review/defer/confirm:

```powershell
tcg-pipeline news-agent-smoke-report `
  --hours 2 `
  --source-name news_paste_a_link `
  --min-source-runs 1 `
  --min-agent-runs 1 `
  --require-triggers status_regression_candidate `
  --min-status-regression-review-items 1 `
  --output "data/output/agent_reset/$cycle/news-regression-smoke.json"
```

If the curated article is expected to produce a `dismiss` verdict, omit
`--min-status-regression-review-items`; the trigger should still appear.

### Permit Observation

Run after R.4 collector replay once permit cutover is enabled.

```powershell
tcg-pipeline permit-agent-smoke-report `
  --source-name ladbs_permits `
  --market los_angeles `
  --require-triggers status_regression_candidate `
  --min-status-regression-review-items 1 `
  --output "data/output/agent_reset/$cycle/permit-agent-ladbs.json"
```

For a normal collector replay where no permit regression is expected, drop
`--require-triggers status_regression_candidate` and
`--min-status-regression-review-items 1`; keep the JSON artifact.

### Pipedream Coverage Compare

Run at R.7 when the June 2026 Pipedream refresh is available.

```powershell
tcg-pipeline compare-pipedream-coverage `
  --market los_angeles `
  --output "data/output/agent_reset/$cycle/pipedream-coverage-compare.json"
```

Use the workbook and coverage filters chosen for that cycle. Record those inputs
in the cycle notes.

## Regression Metrics To Record

Every cycle note should copy these values from the news and permit reports:

- `trigger_counts.status_regression_candidate`
- `status_regression_agent_run_count`
- `review_item_type_counts.status_regression_review`
- `status_regression_review_item_count`
- News only: `status_regression_open_count`
- News only: `status_regression_auto_accepted_count`
- `outcome_counts`, especially any `failed_*` outcome
- Cost totals and capability breakdowns (`cost_usage_total_usd`,
  `cost_usage_by_capability`, and agent-row cost)

While `NEWS_REGRESSION_AUTO_APPLY_ENABLED=false`,
`status_regression_auto_accepted_count` should be 0. Before flipping the gate,
verify that this metric is present in the daily artifact and that the Activity
view shows `auto_accepted` change rows.

## Duplicate Trigger And Cost Watch

For the first 3-5 organic Urbanize cron days after the regression slices ship,
compare daily artifacts for repeated `status_regression_candidate` triggers on
the same project/current-status/proposed-status combination.

Treat the following as investigation triggers:

- Duplicate regression triggers for the same project/status pair across multiple
  days without new evidence.
- Any `failed_*` agent outcome.
- Any active `news_semantic_parse_failed` alert.
- News bucket cost materially above the observed production cadence.
- Status regression cards accumulating faster than researchers can clear them.

Do not run D.B Urbanize backfill early to increase sample size; R.6.5 remains
the first intended backfill point on a clean post-reset database.

## Activity / Audit Verification

Before declaring a cycle clean, verify the Activity UI against the artifacts:

- `/activity?view=agent&actor=news_v1` shows news `status_regression_candidate`
  runs with linked `status_regression_review` cards when cards were created.
- `/activity?view=agent&actor=permit_v1` shows permit regression runs after
  LADBS replay when the permit agent is enabled.
- `/activity?view=auto_applied&field=pipeline_status` shows
  `change_type=auto_accepted` rows when regression auto-apply is enabled.
- Resolution rows preserve `resolution_log.metadata.regression_candidates` for
  preserved regressions and terminal-regression drops.
- Review-item links open to the Review Queue detail page and use the
  status-regression human summary, not a generic fallback.

If Activity is missing rows that the smoke artifact counts, treat the cycle as
not signed off even if the backend reports passed.

## Sign-Off Notes

Each cycle note should include:

- Backup identifier and SHA/checksum.
- Migration head.
- Source exports used for CoStar and Pipedream.
- News backfill window and paste-link smoke URLs.
- The regression metrics listed above.
- Any reviewer decisions that should be wiped before the next stabilization
  cycle.
- Whether `UI.QA` found layout or flow issues that must land before the next
  cycle.

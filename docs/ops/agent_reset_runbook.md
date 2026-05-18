# AGENT.reset Runbook

> **Last updated:** 2026-05-18 (R.2 execution hardening).
>
> **Cycle 1 preflight:** see `docs/ops/agent_reset_cycle1_preflight.md` for the
> 2026-05-18 kickoff verification packet, recovery closeout, and R.1 backup
> evidence. R.2 remains senior-gated.

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

## R.2 Truncate Contract

R.2 is the destructive reset boundary. Do not run it until a senior reviewer has
approved the table categorization, the command text, and the current cycle's
row-count baseline. Run R.1 first and record a rollback-capable backup.

Use explicit table enumeration without `CASCADE`. If a preserved table still
references a truncate target, PostgreSQL should fail the transaction; that is
the intended guardrail against silently wiping an unreviewed table. Keep
`RESTART IDENTITY` so per-cycle sequences reset. Use one `TRUNCATE TABLE`
statement listing all targets; do not split it into per-table truncate
statements, because the single statement is what lets PostgreSQL process
intra-list foreign key chains without `CASCADE`.

AGENT.reset destroys researcher-entered data tied to projects, including
`researcher_overrides` and `project_notes`. This is a forced consequence of
truncating `projects`. For cycle 1, this is acceptable because pre-reset state is
mostly synthetic/tuning state. For later reset cycles after reviewers have
invested significant time setting overrides or writing notes, evaluate whether a
partial-reset strategy or pre-reset export is needed before R.2.

Before execution, run:

```powershell
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" $env:DATABASE_URL -c "SELECT 1"
```

Quiesce the Render worker before R.2 so scheduled news jobs, queued collector
work, and worker heartbeats do not race the truncate or the R.3 reseed:

1. Suspend `tcg-pipeline-worker` (`srv-d7sfvt7avr4c73b4uj2g`) via Render API or
   dashboard.
2. Wait at least 30 seconds for in-flight work to drain.
3. Re-run the `SELECT 1` connectivity check immediately before the truncate.
4. Execute R.2.
5. Run post-execution verification queries.
6. Keep the worker suspended through the senior R.3 approval gate and R.3 reseed
   verification, then resume it.

Render API:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "https://api.render.com/v1/services/srv-d7sfvt7avr4c73b4uj2g/suspend" `
  -Headers @{ Authorization = "Bearer $env:RENDER_API_KEY"; Accept = "application/json" }

Invoke-RestMethod `
  -Method Post `
  -Uri "https://api.render.com/v1/services/srv-d7sfvt7avr4c73b4uj2g/resume" `
  -Headers @{ Authorization = "Bearer $env:RENDER_API_KEY"; Accept = "application/json" }
```

If a Render API call appears to fail locally, query the service state before
retrying. Render may have accepted the suspend/resume change even when the local
PowerShell client reports an exception.

Then run the approved command in one transaction:

```powershell
$sql = @'
BEGIN;
SET LOCAL lock_timeout = '30s';
TRUNCATE TABLE
  agent_run_review_items,
  agent_runs,
  change_log,
  costar_uploads,
  dismissed_records,
  evidence,
  news_admin_actions,
  news_article_chunks,
  news_articles,
  news_extractions,
  news_project_references,
  news_reference_auto_applied,
  news_semantic_interpretations,
  project_identifiers,
  project_notes,
  project_relationships,
  project_source_records,
  projects,
  researcher_overrides,
  resolution_log,
  review_decisions,
  review_items,
  scrape_jobs,
  source_runs,
  status_history,
  system_alerts,
  worker_heartbeats
RESTART IDENTITY;
COMMIT;
'@

& "C:\Program Files\PostgreSQL\18\bin\psql.exe" `
  -v ON_ERROR_STOP=1 `
  $env:DATABASE_URL `
  -c $sql
```

### R.2 Truncate Tables

| Table | Rationale |
|-------|-----------|
| `agent_run_review_items` | Per-cycle join rows between agent decisions and review items. |
| `agent_runs` | Per-cycle agent execution audit tied to ingested articles, permits, and review items. |
| `change_log` | Project mutation audit generated from pre-reset dummy/tuning state. |
| `costar_uploads` | Per-cycle upload audit tied to reseed files. |
| `dismissed_records` | Reviewer/source dismissal state should restart with the clean baseline. |
| `evidence` | Core collected evidence rows; reset requires a clean evidence graph. |
| `news_admin_actions` | News admin-event history from pre-reset tuning state. |
| `news_article_chunks` | Derived embedding chunks tied to reset news articles. |
| `news_articles` | Collected news articles are replayed/backfilled after reset. |
| `news_extractions` | LLM extraction outputs tied to reset news articles. |
| `news_project_references` | Per-article project references tied to reset news articles/projects. |
| `news_reference_auto_applied` | Per-cycle auto-apply audit tied to news references. |
| `news_semantic_interpretations` | Pass 2c outputs tied to reset news articles/references. |
| `project_identifiers` | Project-scoped identifiers are rebuilt from reseed/replay. |
| `project_notes` | Human notes from dummy/tuning state should not carry into cycle 1. |
| `project_relationships` | Project-scoped relationships are rebuilt or reauthored after reset. |
| `project_source_records` | Project-to-source-row links would orphan when projects/source rows reset. |
| `projects` | Canonical project rows are rebuilt from CoStar/Pipedream and collectors. |
| `researcher_overrides` | Pre-reset human override state should not constrain the clean replay. |
| `resolution_log` | Resolution audit rows are derived from the reset evidence graph. |
| `review_decisions` | Review decisions are per-cycle researcher actions. |
| `review_items` | Review queue items are regenerated from clean ingest/resolve. |
| `scrape_jobs` | Worker job history is per-cycle operational state. |
| `source_runs` | Collector/source-run audit is regenerated during replay. |
| `status_history` | Project status history is rebuilt from reseed/replay evidence. |
| `system_alerts` | Operational alerts restart clean so reset noise is not mixed with old alerts. |
| `worker_heartbeats` | Ephemeral worker liveness rows are regenerated on worker restart. |

### R.2 Preserve Tables

| Table | Rationale |
|-------|-----------|
| `alembic_version` | Schema state; never truncate during reset. |
| `cost_cap_overrides` | Budget/operator config survives reset. |
| `cost_caps` | Budget config survives reset. |
| `developer_alias` | Developer canonical registry seed/config survives reset. |
| `developer_registry` | Developer canonical registry seed/config survives reset. |
| `jurisdictions` | Market/jurisdiction config required by matchers and collectors. |
| `llm_cost_usage` | Operational cost history is preserved for spend continuity across reset cycles. |
| `markets` | Market config required by matchers and collectors. |
| `news_signal_flag_registry` | News signal taxonomy/config survives reset. |
| `news_sources` | News collector/source config survives reset. |
| `service_credential_validations` | Credential validation audit survives reset. |
| `service_credentials` | Required encrypted service credentials; truncating breaks workers. |
| `source_registrations` | Source registration config required by collectors. |
| `spatial_ref_sys` | PostGIS reference metadata. |

Any public table not listed in either table is a senior decision point. Stop and
categorize it before running R.2.

## R.3 Seed Prerequisites

R.3 blocks until current source export workbooks have been placed manually:

- `data/seed/costar/` must contain current CoStar `.xlsx` export workbook(s).
- `data/seed/pipedream/` must contain the latest Pipedream `.xlsm` workbook with
  the documented `DataStorage` tab.

The `.gitkeep` placeholders mean these directories are intentionally not
version-controlled. Fresh exports must be placed before each reset cycle. Record
filename, byte size, last-modified timestamp, and SHA256 in the R.3 Decision Log
before running seed commands.

Use the established order for consistency:

```powershell
tcg-pipeline seed-costar data/seed/costar --market los_angeles
tcg-pipeline seed-pipedream data/seed/pipedream --market los_angeles
```

After R.3, verify seed command stdout and SQL row counts for `projects`,
`evidence`, and `project_source_records`. Do not proceed to R.4 collectors until
senior review approves the R.3 results.

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
- `status_regression_duplicate_project_count`
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

## Automated Duplicate Trigger And Cost Watch

The smoke validators compute `status_regression_duplicate_project_count` as the
number of distinct project/current-status/proposed-status tuples that triggered
`status_regression_candidate` more than once inside the report window. Enforce
the ceiling with `--max-status-regression-duplicate-projects` instead of
hand-comparing JSON artifacts across days.

Do not hard-code a default ceiling until a post-migration observation window has
real regression volume. The 2026-05-12 preflight found the configured Supabase
database one migration behind (`202605110037`); after the `202605110038`
contract migration was applied, historical rows still contained no
post-migration regression-candidate signal. The validator flag defaults to off
so operators can first observe live volume, then choose an explicit ceiling.

For the rolling duplicate watch, run a 3-5 day window with the approved ceiling
for that cycle:

```powershell
$duplicateCeiling = <approved ceiling from observed post-migration data>
tcg-pipeline news-agent-smoke-report `
  --hours 120 `
  --source-name urbanize_la `
  --min-source-runs 3 `
  --max-status-regression-duplicate-projects $duplicateCeiling `
  --output "data/output/agent_reset/$cycle/news-regression-duplicate-watch.json"
```

Treat the following as investigation triggers:

- Duplicate regression triggers for the same project/status pair across multiple
  days or inside one smoke window without new evidence.
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

## Recovery Job Processing Without Local Redis

Use this only for a senior-approved one-off recovery when
`tcg-pipeline news reprocess-stranded --apply` creates a persisted
`scrape_jobs(kind='news_agent_integrate')` row but cannot enqueue it because the
local Redis connection is unavailable.

The direct worker entry point is acceptable because `run_news_agent_integrate_job`
loads the job from the database by job id, reads `scrape_jobs.target_payload` for
`article_id`, `source_run_id`, and optional `force_project_id`, runs the same
integration path the RQ worker would run, marks the job completed, increments the
source-run counters, and clears the `news_integrate_failed` alert. Redis is only
the dispatch layer; it is not the durable job state.

Procedure:

```powershell
$env:AGENT_ALLOW_LIVE_LLM = "true"
@'
from uuid import UUID
from tcg_pipeline.workers.news_jobs import run_news_agent_integrate_job

run_news_agent_integrate_job(UUID("<queued-news-agent-integrate-job-id>"))
'@ | python -
```

Then verify all of the following before considering the recovery closed:

```powershell
tcg-pipeline news reprocess-stranded --article-id <article-id>
```

- The recovery `scrape_jobs` row has `status='completed'` and no `error_text`.
- The original `news_integrate_failed` alert row has `cleared_at` populated.
- Active news/semantic alert count is 0, or any remaining alert is explicitly
  accepted for the cycle.
- Pending reference count for the recovered article is 0.
- A post-recovery `news-agent-smoke-report` or approved SQL substitute passes.

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

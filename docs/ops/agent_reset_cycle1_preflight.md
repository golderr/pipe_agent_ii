# AGENT.reset Cycle 1 Preflight

> **Status:** Preflight packet prepared 2026-05-18. The active news integration
> alert found during preflight was recovered and cleared. `AGENT.reset` R.1
> rollback-capable backup is complete. R.2 remains gated on senior reviewer
> approval for the destructive reset sequence.
>
> **Last updated:** 2026-05-18.
> **Maintained by:** Nate Goldstein + Claude Code.

---

## Scope

This packet captures the read-only kickoff verification before the first
`AGENT.reset` stabilization cycle and the senior-approved R.1 backup evidence.
It does not authorize or perform truncate, reseed, collector replay, backfill,
or D.EXP schema work.

## Verification Run

Local artifact directory:

```text
data/output/agent_reset/reset-20260518-preflight/
```

Artifacts are intentionally under `data/output/` and are ignored by git. The
status report summarizes the durable signals.

### Render Deploy State

- API service `srv-d7sfvt7avr4c73b4uj20`: latest deploy `dep-d85mig4m0tmc739linv0`, status `live`, commit `5de9905a5d976a7d4c1bd2123a39e2f1cf33af48`.
- Worker service `srv-d7sfvt7avr4c73b4uj2g`: latest deploy `dep-d85mig4m0tmc739linag`, status `live`, commit `5de9905a5d976a7d4c1bd2123a39e2f1cf33af48`.
- API and worker env gates: `AGENT_ENABLED_FOR_NEWS=true`, `AGENT_ENABLED_FOR_PERMITS=true`, `AGENT_ALLOW_LIVE_LLM=true`, profile overrides unset, `NEWS_USE_LEGACY_SEMANTIC=false`, `NEWS_USE_LEGACY_PASS3=false`.

### Permit Cutover Source Run

Command:

```powershell
tcg-pipeline permit-agent-smoke-report `
  --source-run-id 50005ea8-fcbe-486f-b88c-1f69d0ff07e3 `
  --min-agent-runs 25 `
  --max-agent-runs 25 `
  --require-outcomes completed `
  --max-total-cost-usd 2 `
  --output data/output/agent_reset/reset-20260518-preflight/permit-agent-cutover-source-run.json
```

Result:

- Source run: `50005ea8-fcbe-486f-b88c-1f69d0ff07e3`.
- Source: `los_angeles/ladbs_permits`.
- Records pulled: 25.
- Agent runs: 25.
- Outcomes: `completed=25`.
- Triggers: `new_candidate=25`.
- Review item types: `new_candidate=25`.
- Status-regression agent runs: 0.
- Status-regression review items: 0.
- Duplicate status-regression projects: 0.
- Missing review links: 0.
- Total cost: `$1.584786`.

### Discovery Candidate Spot Check

Read-only DB spot check covered the five documented 5H permit cards:

- `104ddd4e-ca5b-495a-9ddf-1b5cd91bb539`
- `2846ecec-fb32-4fa6-9ce0-1a962392cd5c`
- `897209d5-f172-4f6f-a10d-1f382854de06`
- `2daffd30-13d1-4591-8613-32d4408ab9b6`
- `59464849-e104-47b2-af69-ec1ef3e2a4a5`

Result:

- Source run market is `los_angeles`; jurisdiction is null.
- Source run has 25 review items, all still open.
- Each checked card is `new_candidate`, status `open`, and reference-less.
- Each checked card returns 25 candidates with `layer_3_available=true`.
- Local DB timing across three runs per card: min 138.1 ms, median-of-medians 169.9 ms, max 392.0 ms.

This confirms the documented permit cards still exercise the reference-less
Discovery path that depends on source-run market context.

### News Observation

The broad `tcg-pipeline news-agent-smoke-report --hours 24 --source-name urbanize_la`
preflight timed out locally after 184 seconds and left no JSON artifact. The
orphaned local process was terminated.

Narrow SQL checks were run instead and written to:

```text
data/output/agent_reset/reset-20260518-preflight/news-agent-24h-sql-preflight.json
```

Result:

- One Urbanize source run in the last 24 hours.
- Four Urbanize scrape jobs in the last 24 hours.
- Zero failed news agent runs in the last 24 hours.
- One active news alert: `news_integrate_failed`.
- Recent news bucket spend is low (`agent.news_v1=$0.168396`, `semantic.news_v1=$0.201132`, extraction `$0.166033`, triage `$0.004746` for 2026-05-18).

The full news smoke must still pass before R.1, either by rerunning the standard
24-hour command from a faster network path or by using an approved narrower
window such as `--hours 6`. The SQL substitute is sufficient for this preflight
packet but is not the final cycle-start monitoring baseline.

Dry-run recovery command:

```powershell
tcg-pipeline news reprocess-stranded `
  --article-id 753cb948-4b97-4d79-94af-67b1e407f301
```

Dry-run result:

- One stranded article found: `753cb948-4b97-4d79-94af-67b1e407f301`.
- Title: `Soaring above the 17-acre One Beverly Hills site`.
- Source run: `be6a18a9-7a89-4694-b576-1ce0ab12d0cf`.
- Reference state: `1/1 pending`.
- Trigger: `possible_multi_candidate`.
- Last failed job: `e2bcb09f-c375-4898-b05b-78c2093d824f`.
- Last error: Supabase pooler SSL `bad record mac`.
- No `--apply` was run during this preflight.

### Recovery Update

Senior approved recovering the stranded article before R.1 so the reset cycle
does not start with a pre-existing `news_integrate_failed` alert.

Command:

```powershell
tcg-pipeline news reprocess-stranded `
  --article-id 753cb948-4b97-4d79-94af-67b1e407f301 `
  --apply
```

Local apply created recovery job `2f7ca648-13ae-4fb5-85c0-f908db0bb654` but
could not enqueue it because local Redis was unavailable. The recovery job was
then processed directly through the same worker entry point
`run_news_agent_integrate_job` with `AGENT_ALLOW_LIVE_LLM=true`.

Recovery result:

- Job `2f7ca648-13ae-4fb5-85c0-f908db0bb654` completed at
  `2026-05-18T20:14:24Z`.
- The original `news_integrate_failed` alert row was cleared at
  `2026-05-18T20:14:24Z`.
- Follow-up `tcg-pipeline news reprocess-stranded --article-id
  753cb948-4b97-4d79-94af-67b1e407f301` dry-run found 0 stranded articles.
- SQL check found `active_news_alert_count=0`, recovery job status `completed`,
  and `stranded_pending_reference_count=0`.

Post-recovery baseline:

```powershell
tcg-pipeline news-agent-smoke-report `
  --hours 6 `
  --source-name urbanize_la `
  --min-source-runs 1 `
  --max-total-cost-usd 25 `
  --output data/output/agent_reset/reset-20260518-preflight/news-agent-6h-post-recovery.json
```

Result:

- Source runs: 1.
- Agent runs: 3.
- Outcomes: `completed=2`, `escalated=1`.
- Triggers: `new_candidate=2`, `pass1_pass2_conflict=1`,
  `possible_multi_candidate=1`.
- Review item types: `new_candidate=2`, `possible_match=1`.
- Status-regression counts: all 0.
- Missing review links: 0.
- Semantic parse statuses: `ok=3`.
- News bucket total cost: `$0.833135` against `$125` hard cap.
- Validation failures: none.

The smoke report still lists the historical `news_integrate_failed` alert
because it was raised inside the 6-hour reporting window, but that alert row has
`cleared_at=2026-05-18T20:14:24Z`; the active-alert SQL check is the source of
truth that no active news alert remains.

### Data-Quality Observations

The 2026-05-18 Urbanize source run `be6a18a9-7a89-4694-b576-1ce0ab12d0cf`
has `market='unscoped'`. This is non-blocking for the permit 5H source-run-market
path because the LADBS permit cutover source run checked above has
`market='los_angeles'` and the five reference-less permit cards depend on that
LA slug.

Brief code/log investigation found this is expected for scheduled Urbanize, not
a newly discovered source-run insertion bug. `docs/sources/news/urbanize_la.md`
documents that `urbanize_la` is seeded without market or jurisdiction so the
matcher can decide relevance across live and future markets. The scheduler path
in `src/tcg_pipeline/workers/news_jobs.py` writes `source.market.slug` when a
source has one and otherwise writes `"unscoped"`; tests such as
`tests/test_d6_urbanize_smoke.py` use the same shape. The open question for
future cycles is not whether this row should be LA-scoped, but whether any
reference-less Create flow for news-backed cards should ever depend on
`source_run.market`; today news-backed cards use persisted `NewsProjectReference`
context, while the 5H bug fix targeted permit/fallback cards.

## Gates Before R.2

- Active `news_integrate_failed` alert: resolved 2026-05-18.
- Approved narrower post-recovery news smoke: passed 2026-05-18 with no
  validation failures.
- R.1 rollback-capable backup: completed 2026-05-18.
- Get senior reviewer approval before running R.2 truncate.

## R.1 Rollback-Capable Backup

Senior approved R.1 after the preflight and recovery closeout. R.2 truncate has
not been executed.

Pre-flight:

- `pg_dump (PostgreSQL) 18.3` from `C:\Program Files\PostgreSQL\18\bin`.
- `psql ... SELECT 1` against the configured `DATABASE_URL` passed.
- Target DB: Supabase project `qqnlbfncqwqkvsdufjwa`,
  host `aws-1-us-east-2.pooler.supabase.com`, database `postgres`.
- Alembic version at backup time: `202605140040`.

Command:

```powershell
$backup = "data/output/db_snapshots/supabase_pre_agent_reset_cycle1_20260518_133653.dump"
& "C:\Program Files\PostgreSQL\18\bin\pg_dump.exe" `
  --format=custom `
  --no-owner `
  --no-privileges `
  --file $backup `
  $env:DATABASE_URL
```

Artifact:

- Path:
  `data/output/db_snapshots/supabase_pre_agent_reset_cycle1_20260518_133653.dump`.
- SHA256:
  `74E2F7C99B851C09BC5DCAE9037E62C5AAB293B6F4018EDDCC705F1BD7090FE7`.
- Size: `4,441,347` bytes.
- `pg_restore --list`: passed.

Row counts at backup time:

- `projects=1364`
- `evidence=2209`
- `source_runs=57`
- `news_articles=76`
- `scrape_jobs=55`
- `review_items=257`

Rollback command:

```powershell
& "C:\Program Files\PostgreSQL\18\bin\pg_restore.exe" `
  --clean `
  --if-exists `
  --no-owner `
  --no-privileges `
  --dbname=$env:DATABASE_URL `
  data/output/db_snapshots/supabase_pre_agent_reset_cycle1_20260518_133653.dump
```

## R.2 Table Inventory Check

Read-only schema inventory before proposing R.2 found two FK-dependent data
tables that would be truncated by `CASCADE` if the ROADMAP list is used:
`project_source_records` (`2100` rows) and `news_semantic_interpretations`
(`27` rows). Recommended R.2 command should include them explicitly rather than
letting cascade hide them.

Other live base tables not named in the ROADMAP data-table list or the explicit
preserve list:

- `dismissed_records=37`
- `news_admin_actions=1`
- `worker_heartbeats=2`
- `service_credentials=0`
- `service_credential_validations=0`

Senior reviewer should approve whether those operational/history tables are
included in R.2 or intentionally preserved before any truncate command runs.

## Next Action

Recommended next action is R.2: truncate the approved data-table list only after
explicit senior approval. Do not run any truncate or reseed command from this
packet alone.

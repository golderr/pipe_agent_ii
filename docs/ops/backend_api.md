# Backend API

Phase C write paths use a FastAPI service over the existing Python pipeline code.
Phase B frontend reads continue to go directly through Supabase PostgREST under RLS.

## C.a Scope

C.a establishes the service boundary only:

- Health and readiness checks.
- Supabase Auth JWT validation.
- Allowed-email enforcement.
- Server-side SQLAlchemy session dependency.
- Protected route stubs for future write paths.

The protected write routes intentionally return `501 Not Implemented` until the
corresponding Phase C steps implement real behavior. Do not wire frontend mutation
controls to these routes until the specific backend path is complete.

## Evidence Snippet Reads

`GET /evidence/{id}/snippet` is a FastAPI read endpoint backed by the privileged
server-side database session, not Supabase PostgREST/RLS. Access is currently
limited by Supabase JWT validation plus `ALLOWED_EMAILS`; project/user-level
read scoping should be revisited with the Phase C staging/auth policy before
the contributor set broadens.

## Required Environment

```powershell
APP_ENV=development
DATABASE_URL=postgresql+psycopg://...
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_ANON_KEY=...
GEOCODIO_API_KEY=...
ESRI_API_KEY=...
ALLOWED_EMAILS=ng@theconcordgroup.com
API_CORS_ORIGINS=http://localhost:3000,https://tcg-pipeline.vercel.app
API_AUTH_AUDIENCE=authenticated
API_REQUIRED_ROLE=authenticated
API_JWKS_CACHE_TTL_SECONDS=600
ENABLE_PREVIEW_WRITES=false
REDIS_URL=redis://...
SCRAPE_JOB_QUEUE_NAME=scrape_jobs
```

`DATABASE_URL` is the privileged server-side Postgres connection used by the API.
Browser clients must never receive it.

`ALLOWED_EMAILS` is required in every environment. An empty allowlist fails
closed, including local development, preview, and staging deployments.

`ENABLE_PREVIEW_WRITES` only affects the Next.js server action guard. Keep it
false unless a preview write session has an explicit target and owner.

`REDIS_URL` enables durable RQ-backed scrape execution. If it is unset, local
Coverage Refresh falls back to FastAPI background tasks.

`GEOCODIO_API_KEY` and `ESRI_API_KEY` are optional for local development, but
production project creation should configure both. Manual project creation tries
Geocodio first and automatically falls back to Esri when the Geocodio result is
not high-confidence. Keep these keys on the FastAPI service only; they are not
`NEXT_PUBLIC_*` frontend variables.

## Local Run

```powershell
pip install -e .[dev]
uvicorn tcg_pipeline.api.main:app --reload --host 127.0.0.1 --port 8000
```

Smoke checks:

```powershell
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8000/readyz
curl -H "Authorization: Bearer <supabase-access-token>" http://127.0.0.1:8000/auth/whoami
```

## Render Deployment

Recommended Render web service command:

```powershell
uvicorn tcg_pipeline.api.main:app --host 0.0.0.0 --port $PORT
```

Health check path:

```text
/healthz
```

Durable scrape worker command for a separate Render worker service:

```powershell
tcg-pipeline worker
```

Run one worker process per Render worker instance. RQ workers process one job at
a time, so concurrency is controlled by the number of worker service instances
rather than threads inside a single process.

Set the same environment variables as local, with production values. The Render
service should not be used by Vercel preview writes until the preview/staging
policy is revisited for Phase C.

## Preview Write Policy

Phase C preview writes are blocked by default. Vercel previews may keep using
Supabase/PostgREST for Phase B read-only surfaces, but they must not point at a
production FastAPI write service for mutation routes unless an explicit preview
write session is approved and documented.

Use this default until the team needs heavier staging infrastructure:

- Production writes use the production Vercel app and production FastAPI service.
- Production `API_CORS_ORIGINS` should include the production frontend URL and
  local development origins only; do not add wildcard Vercel preview origins.
- Preview write testing requires an explicit target decision: either a staging
  FastAPI/Supabase pair, or a temporary approved write session against production
  with a narrow `ALLOWED_EMAILS` list.
- Do not rely on `APP_ENV` alone to identify preview traffic. `APP_ENV` is set on
  the Render service, while the preview/production distinction comes from the
  caller's Vercel deployment and configured `NEXT_PUBLIC_API_BASE_URL`.
- C.d write endpoints must preserve this policy when real mutation handlers
  replace the current `501` stubs.

## C.d Core Field Overrides

Project Detail Core-field edits call the FastAPI override boundary through
Next.js server actions:

```text
POST   /projects/{project_id}/override
DELETE /projects/{project_id}/override/{field_name}
```

Only evidence-derived Core fields are accepted: `pipeline_status`,
`total_units`, `affordable_units`, `market_rate_units`, `developer`,
`product_type`, `age_restriction`, and `date_delivery`.

The API writes/clears `researcher_overrides`, re-runs
`resolve_project(apply=True)`, updates project edit metadata, and writes a
`change_log` row with `change_type = researcher_override`. C.tail.2 removed the
legacy `projects.researcher_override` JSONB mirror; active table rows are the
source of truth.

New manual Core-field edits use `mode = review_protected`. They are not
permanent sticky locks: the manual value remains current, but when newer
evidence would change that field under the resolver's field rules, the resolver
creates an `override_contradiction` review item instead of silently overwriting
or silently ignoring the manual edit.

C.i contradiction detection applies the field-specific thresholds from
`EVIDENCE_LAYER_DECISIONS.md` §22: unit deltas must exceed 5, delivery dates
must differ by more than 30 days unless recent article evidence is involved,
developers compare after normalization and confident registry canonicalization,
and pipeline status disagreements are always reviewable. Legacy
`until_newer_evidence` overrides now use the same review-protected behavior
instead of silently yielding to newer evidence.

For operational checks before a large apply/backfill, run:

```bash
tcg-pipeline detect-contradictions --market los_angeles --only-with-overrides
```

This focused audit scans only projects with active researcher overrides, which is
the practical pre-apply check for the contradiction rows a broad resolver pass is
likely to create. Omit `--only-with-overrides` for a full-market health scan.
Both modes are dry runs by default. Add `--apply` only after reviewing the
reported created/updated/invalidated counts.

The command snapshots the project ID list before processing. For long-running
production scans against a moving dataset, resume from the printed `Last project
id` with `--start-after <project-id>` rather than restarting from the beginning.

The preview-write block is enforced by the Next.js server action guard. The
FastAPI service still trusts Supabase JWT validation plus `ALLOWED_EMAILS` for
direct API calls.

## C.e Identity Fields + Project Notes

Project Detail Identity and Notes edits call FastAPI through Next.js server
actions:

```text
POST /projects/{project_id}/field
POST /projects/{project_id}/note
```

`/field` is for researcher-authored direct fields on `projects`, not
evidence-derived Core fields. It accepts identity/workflow fields such as
`project_name`, `previous_names`, `raw_addresses`, city/state/county/ZIP,
`tcg_region`, `source_urls`, planner contact fields, `inclusion_in_analysis`,
`inclusion_in_exhibit`, and `inclusion_note`. The API updates the project row,
updates edit metadata, and writes a `change_log` row with
`change_type = researcher_confirmed`.

`/note` is append-only for `researcher_notes`, `personal_notes`, and
`change_notes`. Each write inserts a `project_notes` row. C.tail.3 removed the
legacy latest-note columns from `projects`; Project Detail derives latest-note
previews from `project_notes`.

`project_notes` grants authenticated clients `SELECT` only under RLS. Direct
PostgREST writes remain blocked; writes go through FastAPI with Supabase JWT
validation and `ALLOWED_EMAILS`.

## C.f Project Relationships

Project Detail relationship links call FastAPI through a Next.js server action:

```text
POST /projects/{project_id}/relationship
```

Body:

```json
{
  "relationship_type": "phase",
  "related_project_id": "<project-uuid>",
  "notes": "optional note"
}
```

Supported `relationship_type` values are `phase`, `master_plan`,
`counterpart`, `duplicate`, and `supersedes`.

The API validates both projects, rejects self-links, writes
`project_relationships`, updates project edit metadata, and writes a
`change_log` row with `change_type = researcher_confirmed`. Duplicate
`project_id` / `related_project_id` / `relationship_type` submissions are
idempotent. If a duplicate submission includes a non-empty changed note, the
existing note is updated and audited; otherwise the API returns the existing
relationship without adding another audit row.

The picker search is read-only and runs as a Next.js server-action Supabase
query by project name/address. Relationship mutation still goes through FastAPI.
`project_relationships` remains authenticated SELECT-only under RLS for
PostgREST clients; direct browser writes remain blocked.

C.tail.5 adds relationship maintenance for outgoing links:

```text
PATCH /projects/{project_id}/relationship/{relationship_id}
DELETE /projects/{project_id}/relationship/{relationship_id}
```

`PATCH` accepts `relationship_type` and/or `notes`. Sending `notes: null` or an
empty note clears the stored note. Retyping fails with `409` if the new
`project_id` / `related_project_id` / `relationship_type` tuple already exists.
`DELETE` removes the outgoing relationship. Both endpoints require the
relationship to belong to the `project_id` in the path, update project edit
metadata, and write `change_log` rows containing the old and new relationship
payloads. Incoming links are displayed on Project Detail but must be edited from
their source project.

## C.g New Project Creation

Pipeline new-project creation calls FastAPI through a Next.js server action:

```text
POST /projects
```

Body:

```json
{
  "canonical_address": "123 W 1st St",
  "market_id": "<market-uuid>",
  "jurisdiction_id": "<jurisdiction-uuid>",
  "project_name": "optional",
  "city": "optional",
  "county": "optional",
  "zip": "90012",
  "force_create": false
}
```

`canonical_address`, `market_id`, and `jurisdiction_id` are required. The API
validates that the jurisdiction belongs to the selected market, derives the
legacy `projects.market` / `projects.jurisdiction` strings from those rows,
uses supplied city/county values when present, falls back to jurisdiction/market
labels otherwise, normalizes the address, and runs the existing conservative
matcher against the normalized address.

If the matcher finds duplicate candidates and `force_create` is false, the API
returns `created = false` with duplicate candidates and performs no write. The
Pipeline modal lets the researcher open the existing project or resubmit with
`force_create = true`.

Confirmed creates try server-side geocoding after duplicate checks and before
the project row is inserted. The API calls Geocodio first. If Geocodio is not a
high-confidence rooftop-style match, the API automatically retries with Esri
ArcGIS geocoding and accepts reliable `PointAddress` / `StreetAddress` results.
Reliable results populate `projects.lat`, `projects.lng`, `projects.location`,
and `projects.geocode_confidence`; failures or low-confidence results do not
block creation and leave coordinates null.

Confirmed creates insert a `projects` row, initial `Proposed` `status_history`,
project edit metadata, and a `change_log` row with
`change_type = researcher_confirmed`. The `change_log.new_value` payload includes
geocoding provider, confidence, fallback, and failure metadata for audit. Direct
PostgREST project writes remain blocked; creation uses the privileged FastAPI
database session.

True merge into an existing project is not implemented in C.g.

C.g does not collect APNs, permit/case identifiers, or source URLs at creation
time. Alembic `202604280016` adds a partial unique index on
`projects(market_id, canonical_address)` for rows with a non-null `market_id`.
If two manual creates race past the application-level matcher, the losing insert
is translated into the same `created = false` duplicate-candidate response
instead of a 500.

Before applying `202604280016` to a persistent environment, check for existing
duplicates:

```sql
SELECT market_id, canonical_address, COUNT(*) AS duplicate_count, ARRAY_AGG(id) AS project_ids
FROM projects
WHERE market_id IS NOT NULL
GROUP BY market_id, canonical_address
HAVING COUNT(*) > 1;
```

The migration also runs this preflight and fails before creating the index if
duplicates are still present.

## C.h Review Staging + Commit

Review queue state changes now call FastAPI:

```text
GET  /review/queue
GET  /review/queue/{item_id}
POST /review/{item_id}/decide
POST /review/{item_id}/revise
POST /review/{item_id}/unstage
POST /review/commit
```

`decide` and `revise` stage a `ReviewDecision` without applying it to project
state. The body is:

```json
{
  "decision_type": "accept_new | keep_old | custom | defer | candidate_0",
  "decision_value": "optional value or structured payload",
  "notes": "optional note",
  "source_url": "optional supporting URL"
}
```

Only one staged decision can be active for a `ReviewItem`. A competing stage
attempt returns `409` with the current staged actor details. `unstage` removes
the caller's staged decision and returns the item to `state = open`.

`review_decisions.decision_value` is the source of truth for staged decision
payloads. The legacy `field_overrides` column is still dual-written for
transition compatibility and should not be used by new Review Queue code.

`commit` applies the caller's non-deferred staged decisions in one transaction:

```json
{
  "jurisdiction_id": "optional jurisdiction UUID",
  "dry_run": false
}
```

Commits mark `review_items.state = committed`, mark the decision committed, and
write full audit identity where available (`staged_by`, `staged_by_email`,
`committed_by`, `committed_by_email`, and `change_log.reviewed_by_user_id` /
`reviewed_by_email`). Deferred decisions remain staged and are excluded from
commit counts.

`change_log.reviewed_by_user_id` is the authoritative reviewer identity for new
write paths. `reviewed_by` remains a legacy display label, and
`reviewed_by_email` exists so pre-C.h rows and transition-period tools can still
show full email context.

C.h requires the `202604270013` Alembic migration before deployed code runs
against a database. Dashboard, Coverage, and Project Detail still preserve the
legacy deferred count by mirroring `review_items.status`; open work is read from
`review_items.state = open`, while non-deferred staged work is surfaced as an
`In review` count.

## C.l Coverage Scrape + CoStar Upload

Coverage source actions now call FastAPI:

```text
POST /coverage/{jurisdiction_id}/scrape
POST /coverage/{jurisdiction_id}/costar-upload
GET  /scrape_jobs/{job_id}?jurisdiction_id={jurisdiction_id}
GET  /coverage/{jurisdiction_id}/scrape_jobs?source_name={source_name}&limit=5
GET  /scrape_workers/health
```

`scrape` enqueues a tracked `scrape_jobs` row for an active non-CoStar source
registration. The body is:

```json
{
  "source_name": "ladbs_permits"
}
```

The response includes the job id, status, timestamps, actor identity, and
progress payload. When `REDIS_URL` is configured, implemented Socrata sources
with local adapters are pushed to the RQ queue consumed by `tcg-pipeline worker`.
When `REDIS_URL` is unset, local/dev still runs the existing FastAPI background
task immediately after enqueue:

- `ladbs_permits`
- `ladbs_permit_activity`
- `ladbs_inspections`
- `ladbs_cofo`

Unsupported sources return `400` with a collector-unavailable message instead of
creating a job that nothing consumes. The Coverage UI polls the jurisdiction-
scoped status URL while the status is `queued` or `running`. Active duplicate
jobs are prevented by a partial unique index on `(jurisdiction_id, source_name)`
for `queued` / `running` rows; a duplicate click returns the existing active job
when visible to the request, or `409` if the database unique constraint wins a
race.

`GET /coverage/{jurisdiction_id}/scrape_jobs` returns the newest scrape jobs for
a jurisdiction/source and powers the Coverage "History" expansion. `GET
/scrape_workers/health` reports Redis/RQ queue availability, queued/started/failed
job counts, and visible worker count for authenticated operators. Application-
level collector and persistence failures are recorded on `scrape_jobs.status =
failed`; unexpected RQ failures remain visible in the queue's failed registry
until `SCRAPE_JOB_FAILURE_TTL_SECONDS` expires.

`costar-upload` accepts multipart form data with a single `file` field. Uploads
are capped at 50 MB. The API parses and persists the workbook through the
existing CoStar seed importer, records a jurisdiction-scoped `source_runs` row,
and writes a `costar_uploads` audit row with uploader id/email, file metadata,
row count, status, and error text. Failed imports return a normal response with
`status = failed` so the audit row can commit.

C.l requires the `202604270014` Alembic migration and the `python-multipart`
runtime dependency before deployed code handles uploads. C.l-bis requires
`202604270015` for active-job uniqueness. C.tail.1 adds the `redis` and `rq`
runtime dependencies plus the Render worker service.

## Legacy Storage Retirement

C.tail.2/C.tail.3 retire the transitional project columns with Alembic
`202604280017`. The migration refuses to run if any non-empty legacy override
value lacks a matching active `researcher_overrides` row, or if any non-empty
legacy latest-note column differs from the latest `project_notes` row for that
project/type.

Before applying `202604280017` to production:

1. Snapshot the production database and capture the dump SHA256.
2. Restore that snapshot to a scratch database and run `alembic upgrade head`.
3. Run the full Python test suite against the upgraded scratch database.
4. Apply to production when no Pipedream re-seed or other bulk write is running.
5. Smoke-check Coverage override counts against active `researcher_overrides`
   rows, Project Detail notes for a project with note history, and Project
   Detail Overrides for a project with active overrides.

## Auth Notes

The API verifies Supabase access tokens with the Supabase JWKS endpoint:

```text
<SUPABASE_URL>/auth/v1/.well-known/jwks.json
```

If the project is still on legacy/symmetric JWT signing and JWKS is unavailable,
the verifier falls back to Supabase Auth `/auth/v1/user` using `SUPABASE_ANON_KEY`.
Successful tokens must still have the configured audience/role and an allowed
email.

`API_AUTH_AUDIENCE` validates the JWT `aud` claim. `API_REQUIRED_ROLE` validates
the Supabase role claim so service/admin tokens do not pass as end-user writes.

## Verification

Run:

```powershell
pytest tests/test_api_scaffold.py -q
pytest -q
ruff check src/tcg_pipeline/api tests/test_api_scaffold.py
npm run typecheck
npm run lint
```

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
ALLOWED_EMAILS=ng@theconcordgroup.com
API_CORS_ORIGINS=http://localhost:3000,https://tcg-pipeline.vercel.app
API_AUTH_AUDIENCE=authenticated
API_REQUIRED_ROLE=authenticated
API_JWKS_CACHE_TTL_SECONDS=600
ENABLE_PREVIEW_WRITES=false
```

`DATABASE_URL` is the privileged server-side Postgres connection used by the API.
Browser clients must never receive it.

`ALLOWED_EMAILS` is required in every environment. An empty allowlist fails
closed, including local development, preview, and staging deployments.

`ENABLE_PREVIEW_WRITES` only affects the Next.js server action guard. Keep it
false unless a preview write session has an explicit target and owner.

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

The API writes/clears `researcher_overrides`, keeps the legacy
`projects.researcher_override` JSONB in sync during the transition, re-runs
`resolve_project(apply=True)`, updates project edit metadata, and writes a
`change_log` row with `change_type = researcher_override`.

New manual Core-field edits use `mode = review_protected`. They are not
permanent sticky locks: the manual value remains current, but when newer
evidence would change that field under the resolver's field rules, the resolver
creates an `override_contradiction` review item instead of silently overwriting
or silently ignoring the manual edit.

For C.d Core overrides, the trigger is intentionally strict: any newer
auto-resolved value that differs from the manual value gets surfaced for review.
Later C.i work can expand priority and batching rules without silently masking
manual-field updates.

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
`change_notes`. Each write inserts a `project_notes` row and also updates the
legacy latest-note column on `projects` so existing Project Detail reads stay
stable during the transition.

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

Relationship unlink/retype and explicit note clearing are not implemented in
C.f. Incorrect links still require admin cleanup until a later relationship
maintenance endpoint/UI exists.

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
  "zip": "90012",
  "force_create": false
}
```

`canonical_address`, `market_id`, and `jurisdiction_id` are required. The API
validates that the jurisdiction belongs to the selected market, derives the
legacy `projects.market` / `projects.jurisdiction` strings from those rows,
normalizes the address, and runs the existing conservative matcher against the
normalized address.

If the matcher finds duplicate candidates and `force_create` is false, the API
returns `created = false` with duplicate candidates and performs no write. The
Pipeline modal lets the researcher open the existing project or resubmit with
`force_create = true`.

Confirmed creates insert a `projects` row, initial `Proposed` `status_history`,
project edit metadata, and a `change_log` row with
`change_type = researcher_confirmed`. Direct PostgREST project writes remain
blocked; creation uses the privileged FastAPI database session.

True merge into an existing project is not implemented in C.g.

## C.c Migration Verification

Before C.d write endpoints are enabled, verify the `researcher_overrides`
migration against staging or a snapshotted database. Do not run the migration
blindly against the production project.

Safe pre-migration checks:

```powershell
python scripts/verify_researcher_overrides_migration.py summary --verbose
python scripts/verify_researcher_overrides_migration.py snapshot --output data/output/migration_checks/researcher_overrides_<env>_<utc>_before.json
```

Use a DB-specific filename (`<env>` and timestamp) for every snapshot. Do not
reuse one hardcoded JSON path across production and staging checks.

After taking a DB snapshot or targeting staging:

```powershell
alembic upgrade head
python scripts/verify_researcher_overrides_migration.py summary --verbose
python scripts/verify_researcher_overrides_migration.py compare --before data/output/migration_checks/researcher_overrides_<env>_<utc>_before.json
```

The pre-migration `compare` can be run as a tool sanity check, but it does not
verify the migration. The post-migration `compare` is mandatory.

Done criteria:

- `summary` reports the `researcher_overrides` table exists.
- Legacy override field-pair count equals active table row count.
- Legacy-only, table-only, and mismatched pair counts are all `0`.
- Unique active-field index, RLS enabled, authenticated read policy, and
  authenticated SELECT grant are all present.
- Snapshot comparison passes with no resolution differences.

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

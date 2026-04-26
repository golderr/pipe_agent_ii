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
```

`DATABASE_URL` is the privileged server-side Postgres connection used by the API.
Browser clients must never receive it.

`ALLOWED_EMAILS` is required in every environment. An empty allowlist fails
closed, including local development, preview, and staging deployments.

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

# Frontend Deployment

## B.1 Production Checklist

B.1 is not complete until the deployed app has passed a real browser auth smoke test.

Required Vercel environment variables:

- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- `ALLOWED_EMAILS`
- `NEXT_PUBLIC_SITE_URL`

Optional fallback:

- `VERCEL_PROJECT_PRODUCTION_URL` can provide the production host, but `NEXT_PUBLIC_SITE_URL` is preferred because magic-link redirects should be explicit and stable.

Smoke test before marking B.1 done:

1. Visit `/coverage` while logged out and confirm redirect to `/login?next=%2Fcoverage`.
2. Submit an allowed email, open the magic link, and confirm redirect to `/coverage`.
3. Confirm `/coverage` renders at least one jurisdiction row.
4. Submit a disallowed email and confirm the app does not send a magic link.

## Preview Environment Policy

For read-only Phase B, Vercel preview deployments use the same Supabase project and the same `ALLOWED_EMAILS` allowlist as production. This keeps previews useful for internal review without introducing a second data environment before writes exist.

Before Phase C write paths are enabled, revisit this policy. At that point previews should either use a separate staging Supabase project or route all writes through a staging FastAPI service.

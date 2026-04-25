# Frontend Deployment

## B.1 Production Checklist

B.1 is not complete until the deployed app has passed a real browser auth smoke test.

Current production deployment:

- Vercel team: `the-concord-group`
- Project: `tcg-pipeline`
- Production URL: `https://tcg-pipeline.vercel.app`
- Latest verified deployment: `https://tcg-pipeline-f9mxw9rnm-the-concord-group.vercel.app`
- Production env vars set: `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `ALLOWED_EMAILS`, `NEXT_PUBLIC_SITE_URL`
- Preview env vars are not set yet; the Vercel CLI required branch-scoped Preview vars in this session. Set them from the dashboard before relying on preview deployments.
- Production smoke completed:
  - `/login` returns 200 and renders the magic-link form.
  - Logged-out `/coverage` returns 307 to `/login?next=%2Fcoverage`.
  - Disallowed email submit returns 303 to `/login?error=not_allowed`.
  - Allowed email submit for `ng@theconcordgroup.com` returns 303 to `/login?sent=1`.
- Remaining smoke: click the real magic link from the inbox and confirm `/coverage` renders jurisdiction rows.

Required Vercel environment variables:

- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- `ALLOWED_EMAILS`
- `NEXT_PUBLIC_SITE_URL`

Optional fallback:

- Enable Vercel's System Environment Variables so the app can read `VERCEL_ENV`, `VERCEL_URL`, and `VERCEL_PROJECT_PRODUCTION_URL`.
- `NEXT_PUBLIC_SITE_URL` is preferred for production because magic-link redirects should be explicit and stable.
- Preview deployments use `VERCEL_URL` when `VERCEL_ENV=preview`, so preview auth smoke tests return to the preview deployment instead of the production URL.

CLI setup, if deploying from this workspace:

```powershell
npm install -g vercel
vercel login
vercel link
vercel env pull .env.vercel
vercel deploy
vercel deploy --prod
```

The project should use the default Next.js framework preset:

- Install command: `npm install`
- Build command: `npm run build`
- Output directory: Vercel auto-detects Next.js
- Root directory: repository root

Smoke test before marking B.1 done:

1. Visit `/coverage` while logged out and confirm redirect to `/login?next=%2Fcoverage`.
2. Submit an allowed email, open the magic link, and confirm redirect to `/coverage`.
3. Confirm `/coverage` renders at least one jurisdiction row.
4. Submit a disallowed email and confirm the app does not send a magic link.

Supabase Auth redirect URLs required before the magic-link smoke:

- `https://tcg-pipeline.vercel.app/**`
- `https://tcg-pipeline-the-concord-group.vercel.app/**`
- `https://*-the-concord-group.vercel.app/**`
- `http://localhost:3000/**`

## Preview Environment Policy

For read-only Phase B, Vercel preview deployments use the same Supabase project and the same `ALLOWED_EMAILS` allowlist as production. This keeps previews useful for internal review without introducing a second data environment before writes exist.

Before Phase C write paths are enabled, revisit this policy. At that point previews should either use a separate staging Supabase project or route all writes through a staging FastAPI service.

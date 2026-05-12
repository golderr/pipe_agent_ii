# Migration Runbook

Use this checklist before applying any schema migration to the configured
Supabase database. It is intentionally stricter than ad hoc development DB
practice because production is used as the stabilization environment until
`AGENT.reset` creates the clean baseline.

## Backup Requirement

Before `alembic upgrade`, create one of these rollback-capable backups:

- `pg_dump --format=custom --no-owner --no-privileges`, written outside git.
- A Supabase-managed backup / snapshot with a durable backup ID.

Partial JSON exports are not rollback-equivalent. They can be useful forensic
snapshots for small additive migrations, but they do not replace a real backup
because they cannot restore enum state, indexes, constraints, grants, or the
full database contents.

Record the backup path or Supabase backup ID in `ROADMAP.md`. For local dump
files, also record the SHA256 and byte size.

## Preflight

Before applying a migration:

1. Confirm the target database with a redacted `DATABASE_URL`.
2. Record the current Alembic version:

```sql
SELECT version_num FROM alembic_version ORDER BY version_num;
```

3. Confirm the repo migration head:

```powershell
alembic heads
```

4. Read the migration file and identify:
   - affected tables / enum types
   - whether the change is additive or destructive
   - required post-apply verification SQL
   - whether Render code has already deployed code that depends on the schema

If code depending on the migration may already be deployed, query recent
`scrape_jobs`, `source_runs`, `system_alerts`, and relevant app logs for errors
that match the missing column/type/function before applying the migration.

## Apply

Apply migrations from a clean working tree when possible:

```powershell
alembic upgrade head
```

If the working tree is not clean, verify the dirty files are unrelated to the
migration and record that in the work notes before applying.

## Post-Apply Verification

Immediately after apply:

1. Re-read `alembic_version`.
2. Verify each new column, table, index, enum value, or constraint expected by
   the migration.
3. Run the feature-specific smoke or a narrow SQL sanity check that proves the
   application can read/write the new shape.
4. Query recent job/source-run/system-alert rows for migration-related errors.

Example column check:

```sql
SELECT column_name
FROM information_schema.columns
WHERE table_name = '<table_name>' AND column_name = '<column_name>';
```

Example enum check:

```sql
SELECT e.enumlabel
FROM pg_enum e
JOIN pg_type t ON t.oid = e.enumtypid
WHERE t.typname = '<enum_type_name>'
ORDER BY e.enumsortorder;
```

## Decision Log Entry

Add a `ROADMAP.md` Decision Log row with:

- migration revision(s) applied
- backup path/ID, SHA256, and size when applicable
- target DB identification by redacted host/project ref
- pre-apply Alembic version and post-apply version
- verification SQL result summary
- any incident-window check performed if code may have been deployed before the
  migration

If a real backup was not possible, say so explicitly and explain why. Also state
what substitute artifact was created and why it is not a rollback-equivalent
backup.

# TCG Pipeline Tracker

Automated real estate development pipeline tracker for seeding, collecting, matching, diffing, and reviewing project data across markets.

## Current Status

This repository is scaffolded through Step `1.4`:

- Python project structure
- typed settings/config loading
- SQLAlchemy models for the revised core schema
- Alembic environment scaffolding
- initial market config files

The first live database action is still pending the Supabase Postgres connection string.

## Local Setup

1. Create a virtual environment.
2. Install the package:

```powershell
pip install -e .[dev]
```

3. Copy `.env.example` to `.env`.
4. Fill in `DATABASE_URL` with the Postgres connection string from the Supabase dashboard.
   `postgresql://...` and `postgresql+psycopg://...` are both accepted.
5. Run a quick config check:

```powershell
tcg-pipeline doctor
```

## Next DB Steps

Once `DATABASE_URL` is set:

```powershell
alembic revision --autogenerate -m "initial schema"
alembic upgrade head
```

## Credentials Still Needed

For Step `1.4`, the only required live credential is:

- `DATABASE_URL` from Supabase `Project Settings -> Database -> Connection string`

The project URL alone is useful for config, but not enough to connect or run migrations.

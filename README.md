# TCG Pipeline Tracker

Automated real estate development pipeline tracker for seeding, collecting, matching, diffing, and reviewing project data across markets.

## Current Status

This repository is scaffolded through Step `1.7`:

- Python project structure
- typed settings/config loading
- SQLAlchemy models for the revised core schema
- Alembic environment scaffolding
- initial market config files
- initial Supabase schema applied with PostGIS and `pg_trgm`
- address normalization module with targeted tests
- Pipedream workbook ingester with diagnostics, persistence helpers, and CLI preview/seed commands
- CoStar workbook ingester with header-name mapping, cross-file `PropertyID` dedupe, and CLI preview support

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

## Database Commands

With `DATABASE_URL` set:

```powershell
alembic revision --autogenerate -m "initial schema"
alembic upgrade head
```

To verify the local environment:

```powershell
tcg-pipeline doctor
pytest tests/test_normalizer.py -q
tcg-pipeline preview-pipedream .\data\seed\your_file.xlsm --market los_angeles
tcg-pipeline seed-pipedream .\data\seed\file1.xlsm .\data\seed\file2.xlsm --market los_angeles --dry-run
tcg-pipeline preview-costar .\data\seed\costar\ --market los_angeles
```

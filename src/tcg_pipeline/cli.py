from __future__ import annotations

from pathlib import Path

import typer

from tcg_pipeline.ingesters.pipedream import PipedreamIngester
from tcg_pipeline.settings import get_settings

app = typer.Typer(help="TCG pipeline tracker CLI.")


@app.callback()
def main() -> None:
    """Root CLI entrypoint."""


@app.command()
def doctor() -> None:
    """Print basic environment and configuration status."""
    settings = get_settings()

    typer.echo(f"Environment: {settings.app_env}")
    typer.echo(f"Supabase URL configured: {bool(settings.supabase_url)}")
    typer.echo(f"Supabase project ref: {settings.project_ref or 'missing'}")
    typer.echo(f"Database URL configured: {settings.has_database_url}")
    typer.echo(f"Seed directory: {settings.seed_dir}")
    typer.echo(f"Output directory: {settings.output_dir}")

    if not settings.has_database_url:
        typer.echo(
            "Next required credential: DATABASE_URL from Supabase Project Settings -> Database.",
            err=True,
        )


@app.command()
def preview_pipedream(
    workbook_path: Path,
    market: str = typer.Option(..., help="Market slug, e.g. los_angeles."),
    source_name: str = typer.Option("pipedream", help="Source name stored on source records."),
    allowed_city: str | None = typer.Option(
        None,
        help="Optional city filter for import preview, e.g. 'Los Angeles'.",
    ),
) -> None:
    """Preview a Pipedream workbook import without writing to the database."""
    allowed_cities = [allowed_city] if allowed_city else None
    ingester = PipedreamIngester(
        market=market,
        source_name=source_name,
        allowed_cities=allowed_cities,
    )
    result = ingester.ingest_workbook(workbook_path)

    typer.echo(f"Workbook: {workbook_path}")
    typer.echo(f"Imported projects: {result.imported_count}")
    typer.echo(f"Dismissed records: {result.dismissed_count}")
    typer.echo(f"Staged relationships: {len(result.staged_relationships)}")
    typer.echo(f"Skipped project ids: {len(result.skipped_project_ids)}")

from __future__ import annotations

import typer

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

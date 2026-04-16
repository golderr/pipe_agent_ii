from __future__ import annotations

from pathlib import Path

import typer

from tcg_pipeline.db.connection import get_session_factory
from tcg_pipeline.db.seed import (
    ingest_pipedream_workbooks,
    persist_pipedream_import_results,
)
from tcg_pipeline.ingesters.pipedream import PipedreamImportResult, PipedreamIngester
from tcg_pipeline.settings import get_settings
from tcg_pipeline.utils.logging import configure_logging

app = typer.Typer(help="TCG pipeline tracker CLI.")


@app.callback()
def main() -> None:
    """Root CLI entrypoint."""
    configure_logging(get_settings().log_level)


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
    typer.echo(f"Missing ProjectID rows: {result.missing_project_id_rows}")
    typer.echo(f"Issues: {result.issue_count}")
    for issue_type, count in sorted(result.issue_counts.items()):
        typer.echo(f"  {issue_type}: {count}")


@app.command()
def seed_pipedream(
    workbook_paths: list[Path],
    market: str = typer.Option(..., help="Market slug, e.g. los_angeles."),
    source_name: str = typer.Option("pipedream", help="Source name stored on source records."),
    allowed_city: list[str] = typer.Option(
        None,
        help="Optional city filter; may be provided multiple times.",
    ),
    dry_run: bool = typer.Option(False, help="Preview import results without writing to the database."),
) -> None:
    """Ingest one or more Pipedream workbooks and optionally persist them."""
    import_results = ingest_pipedream_workbooks(
        workbook_paths,
        market=market,
        source_name=source_name,
        allowed_cities=allowed_city or None,
    )
    _echo_pipedream_import_summary(import_results)

    if dry_run:
        return

    session_factory = get_session_factory()
    with session_factory() as session:
        persist_result = persist_pipedream_import_results(session, import_results)
        session.commit()

    typer.echo(f"Persisted projects: {persist_result.inserted_projects}")
    typer.echo(f"Persisted dismissed records: {persist_result.inserted_dismissed_records}")
    typer.echo(f"Created relationships: {persist_result.created_relationships}")
    typer.echo(f"Skipped existing relationships: {persist_result.skipped_existing_relationships}")
    typer.echo(f"Unresolved relationships: {persist_result.unresolved_relationship_count}")


def _echo_pipedream_import_summary(import_results: list[PipedreamImportResult]) -> None:
    total_imported = sum(result.imported_count for result in import_results)
    total_dismissed = sum(result.dismissed_count for result in import_results)
    total_relationships = sum(len(result.staged_relationships) for result in import_results)
    total_skipped = sum(len(result.skipped_project_ids) for result in import_results)
    total_missing_project_id = sum(result.missing_project_id_rows for result in import_results)
    total_issues = sum(result.issue_count for result in import_results)

    typer.echo(f"Workbooks: {len(import_results)}")
    typer.echo(f"Imported projects: {total_imported}")
    typer.echo(f"Dismissed records: {total_dismissed}")
    typer.echo(f"Staged relationships: {total_relationships}")
    typer.echo(f"Skipped project ids: {total_skipped}")
    typer.echo(f"Missing ProjectID rows: {total_missing_project_id}")
    typer.echo(f"Issues: {total_issues}")

    aggregated_issue_counts: dict[str, int] = {}
    for result in import_results:
        for issue_type, count in result.issue_counts.items():
            aggregated_issue_counts[issue_type] = aggregated_issue_counts.get(issue_type, 0) + count

    for issue_type, count in sorted(aggregated_issue_counts.items()):
        typer.echo(f"  {issue_type}: {count}")

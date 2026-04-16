from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import func, select

from tcg_pipeline.collectors.base import CollectionMode, CollectionRequest
from tcg_pipeline.collectors.factory import build_collector
from tcg_pipeline.db.collect import persist_collected_records
from tcg_pipeline.db.connection import get_session_factory
from tcg_pipeline.db.models import SourceRun
from tcg_pipeline.db.seed import (
    ingest_costar_workbooks,
    ingest_pipedream_workbooks,
    persist_costar_import_result,
    persist_pipedream_import_results,
)
from tcg_pipeline.ingesters.costar import CoStarImportResult, CoStarIngester
from tcg_pipeline.ingesters.pipedream import PipedreamImportResult, PipedreamIngester
from tcg_pipeline.market_config import get_market_config
from tcg_pipeline.settings import get_settings
from tcg_pipeline.status_rules import get_status_evidence_rule
from tcg_pipeline.utils.logging import configure_logging

app = typer.Typer(help="TCG pipeline tracker CLI.")
COLLECTION_MODE_OPTION = typer.Option(
    CollectionMode.FULL,
    help="Collection mode: full backfill or incremental from the last source-run cursor.",
)


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
    allowed_city: Annotated[
        list[str] | None,
        typer.Option(help="Optional city filter; may be provided multiple times."),
    ] = None,
    dry_run: bool = typer.Option(
        False,
        help="Preview import results without writing to the database.",
    ),
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
    typer.echo(f"Skipped existing projects: {persist_result.skipped_existing_project_count}")
    typer.echo(
        "Skipped existing dismissed records: "
        f"{persist_result.skipped_existing_dismissed_count}"
    )
    typer.echo(f"Created relationships: {persist_result.created_relationships}")
    typer.echo(f"Skipped existing relationships: {persist_result.skipped_existing_relationships}")
    typer.echo(f"Unresolved relationships: {persist_result.unresolved_relationship_count}")


@app.command()
def preview_costar(
    workbook_paths: list[Path],
    market: str = typer.Option(..., help="Market slug, e.g. los_angeles."),
    source_name: str = typer.Option("costar", help="Source name stored on source records."),
    allowed_city: Annotated[
        list[str] | None,
        typer.Option(help="Optional city filter; may be provided multiple times."),
    ] = None,
) -> None:
    """Preview one or more CoStar workbooks without writing to the database."""
    resolved_workbook_paths = _expand_workbook_inputs(workbook_paths, suffix=".xlsx")
    if not resolved_workbook_paths:
        raise typer.BadParameter("No .xlsx CoStar workbooks found in the provided paths.")

    ingester = CoStarIngester(
        market=market,
        source_name=source_name,
        allowed_cities=allowed_city or None,
    )
    result = ingester.ingest_workbooks(resolved_workbook_paths)
    _echo_costar_import_summary(result)


@app.command()
def seed_costar(
    workbook_paths: list[Path],
    market: str = typer.Option(..., help="Market slug, e.g. los_angeles."),
    source_name: str = typer.Option("costar", help="Source name stored on source records."),
    allowed_city: Annotated[
        list[str] | None,
        typer.Option(help="Optional city filter; may be provided multiple times."),
    ] = None,
    dry_run: bool = typer.Option(
        False,
        help="Preview import results without writing to the database.",
    ),
) -> None:
    """Ingest one or more CoStar workbooks and optionally persist them."""
    resolved_workbook_paths = _expand_workbook_inputs(workbook_paths, suffix=".xlsx")
    if not resolved_workbook_paths:
        raise typer.BadParameter("No .xlsx CoStar workbooks found in the provided paths.")

    import_result = ingest_costar_workbooks(
        resolved_workbook_paths,
        market=market,
        source_name=source_name,
        allowed_cities=allowed_city or None,
    )
    _echo_costar_import_summary(import_result)

    if dry_run:
        return

    session_factory = get_session_factory()
    with session_factory() as session:
        persist_result = persist_costar_import_result(session, import_result)
        session.commit()

    typer.echo(f"Persisted new projects: {persist_result.inserted_projects}")
    typer.echo(f"Matched existing projects: {persist_result.matched_existing_projects}")
    typer.echo(f"Matched by CoStar property id: {persist_result.matched_by_costar_property_id}")
    typer.echo(f"Matched by APN: {persist_result.matched_by_apn}")
    typer.echo(f"Matched by address: {persist_result.matched_by_address}")
    typer.echo(f"Inserted identifiers: {persist_result.inserted_identifiers}")
    typer.echo(f"Skipped existing identifiers: {persist_result.skipped_existing_identifiers}")
    typer.echo(f"Inserted source records: {persist_result.inserted_source_records}")
    typer.echo(f"Updated source records: {persist_result.updated_source_records}")
    typer.echo(f"Inserted status history entries: {persist_result.inserted_status_history_entries}")
    typer.echo(
        "Skipped existing status history entries: "
        f"{persist_result.skipped_existing_status_history_entries}"
    )
    typer.echo(f"Merged fields: {persist_result.merged_fields}")
    typer.echo(f"Ambiguous matches: {persist_result.ambiguous_match_count}")


@app.command()
def preview_source(
    source_name: str,
    market: str = typer.Option(..., help="Market slug, e.g. los_angeles."),
    limit: int = typer.Option(5, min=1, help="Maximum records to collect for the preview."),
) -> None:
    """Collect a small preview batch from a configured public source."""
    source_config = get_market_config(market).get_source(source_name).model_copy(
        update={"max_records": limit}
    )
    collector = build_collector(source_config, market=market)
    raw_records = asyncio.run(
        collector.collect(CollectionRequest(mode=CollectionMode.PREVIEW))
    )

    typer.echo(f"Market: {market}")
    typer.echo(f"Source: {source_name}")
    typer.echo(f"Mode: {CollectionMode.PREVIEW.value}")
    typer.echo(f"Collected records: {len(raw_records)}")
    for raw_record in raw_records[: min(limit, len(raw_records))]:
        direct_status = raw_record.mapped_fields.get("pipeline_status")
        suggested_status = None
        evidence_type = raw_record.mapped_fields.get("status_evidence_type")
        evidence_rule = get_status_evidence_rule(str(evidence_type) if evidence_type else None)
        if evidence_rule is not None:
            suggested_status = evidence_rule.suggested_status.value
        status_label = direct_status or suggested_status or "n/a"
        status_prefix = "status" if direct_status else "suggested_status"
        typer.echo(
            "  "
            f"{raw_record.source_record_id} | "
            f"{raw_record.canonical_address or 'NO_ADDRESS'} | "
            f"units={raw_record.mapped_fields.get('total_units', 'n/a')} | "
            f"{status_prefix}={status_label}"
        )


@app.command()
def collect_source(
    source_name: str,
    market: str = typer.Option(..., help="Market slug, e.g. los_angeles."),
    mode: CollectionMode = COLLECTION_MODE_OPTION,
    updated_since: str | None = typer.Option(
        None,
        help="Optional explicit lower bound for incremental mode, in ISO-8601 format.",
    ),
    limit: int | None = typer.Option(
        None,
        min=1,
        help="Optional max record count to collect for this run.",
    ),
    overlap_hours: int | None = typer.Option(
        None,
        min=0,
        help="Optional override for the incremental overlap window. Defaults to the source config.",
    ),
    dry_run: bool = typer.Option(
        False,
        help="Collect and summarize without writing to the database.",
    ),
) -> None:
    """Collect a configured public source and persist the first-pass review artifacts."""
    source_config = get_market_config(market).get_source(source_name)
    if limit is not None:
        source_config = source_config.model_copy(update={"max_records": limit})

    if updated_since is not None and mode != CollectionMode.INCREMENTAL:
        raise typer.BadParameter("--updated-since can only be used with --mode incremental.")

    request = CollectionRequest(mode=mode)
    if mode == CollectionMode.INCREMENTAL:
        if updated_since is not None:
            request.updated_since = _parse_cli_datetime(updated_since)
        else:
            effective_overlap_hours = (
                overlap_hours
                if overlap_hours is not None
                else source_config.incremental_overlap_hours
            )
            session_factory = get_session_factory()
            with session_factory() as session:
                request.updated_since = _resolve_incremental_cursor(
                    session,
                    market=market,
                    source_name=source_name,
                    overlap_hours=effective_overlap_hours,
                )
        if request.updated_since is None:
            typer.echo(
                "No prior source-run cursor metadata found; falling back to full collection mode."
            )
            request.mode = CollectionMode.FULL

    collector = build_collector(source_config, market=market)
    raw_records = asyncio.run(collector.collect(request))

    typer.echo(f"Market: {market}")
    typer.echo(f"Source: {source_name}")
    typer.echo(f"Mode: {request.mode.value}")
    if request.updated_since is not None:
        typer.echo(f"Incremental since: {request.updated_since.isoformat()}")
    typer.echo(f"Collected records: {len(raw_records)}")

    if dry_run:
        return

    session_factory = get_session_factory()
    with session_factory() as session:
        persist_result = persist_collected_records(
            session,
            market=market,
            source_name=source_name,
            raw_records=raw_records,
            collection_mode=request.mode.value,
            incremental_since=request.updated_since,
            create_new_candidates=source_config.create_new_candidates,
        )
        session.commit()

    typer.echo(f"Source run id: {persist_result.source_run_id}")
    if persist_result.source_min_updated_at is not None:
        typer.echo(f"Source min updated_at: {persist_result.source_min_updated_at.isoformat()}")
    if persist_result.source_max_updated_at is not None:
        typer.echo(f"Source max updated_at: {persist_result.source_max_updated_at.isoformat()}")
    typer.echo(f"Matched existing projects: {persist_result.matched_existing_projects}")
    typer.echo(f"Matched by source record: {persist_result.matched_by_source_record}")
    typer.echo(f"Matched by identifier: {persist_result.matched_by_identifier}")
    typer.echo(f"Matched by address: {persist_result.matched_by_address}")
    typer.echo(f"Inserted source records: {persist_result.inserted_source_records}")
    typer.echo(f"Updated source records: {persist_result.updated_source_records}")
    typer.echo(f"Unchanged source records: {persist_result.unchanged_source_records}")
    typer.echo(f"Inserted identifiers: {persist_result.inserted_identifiers}")
    typer.echo(f"New candidate review items: {persist_result.new_candidate_review_items}")
    typer.echo(f"Status change review items: {persist_result.status_change_review_items}")
    typer.echo(f"Possible match review items: {persist_result.possible_match_review_items}")


def _resolve_incremental_cursor(
    session,
    *,
    market: str,
    source_name: str,
    overlap_hours: int,
):
    max_seen_updated_at = session.execute(
        select(func.max(SourceRun.source_max_updated_at)).where(
            SourceRun.market == market,
            SourceRun.source_name == source_name,
            SourceRun.source_max_updated_at.is_not(None),
        )
    ).scalar_one()
    if max_seen_updated_at is None:
        return None
    return max_seen_updated_at - timedelta(hours=overlap_hours)


def _parse_cli_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise typer.BadParameter(
            f"Invalid datetime '{value}'. Use ISO-8601 format, for example 2026-04-16T00:00:00Z."
        ) from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


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


def _echo_costar_import_summary(result: CoStarImportResult) -> None:
    typer.echo(f"Workbooks: {len(result.source_paths)}")
    typer.echo(f"Imported projects: {result.imported_count}")
    typer.echo(f"Duplicate property ids: {result.duplicate_count}")
    typer.echo(f"Skipped property ids: {len(result.skipped_property_ids)}")
    typer.echo(f"Missing PropertyID rows: {result.missing_property_id_rows}")
    typer.echo(f"Issues: {result.issue_count}")
    for issue_type, count in sorted(result.issue_counts.items()):
        typer.echo(f"  {issue_type}: {count}")


def _expand_workbook_inputs(paths: list[Path], *, suffix: str) -> list[Path]:
    expanded_paths: list[Path] = []
    for path in paths:
        if path.is_dir():
            expanded_paths.extend(
                sorted(
                    child
                    for child in path.glob(f"*{suffix}")
                    if child.is_file()
                )
            )
            continue
        expanded_paths.append(path)
    return expanded_paths

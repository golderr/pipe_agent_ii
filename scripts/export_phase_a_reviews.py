from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Any

try:
    from scripts.phase_a_review_common import (
        DEFAULT_ESTIMATE_SAMPLE_SIZE,
        DEFAULT_MARKET,
        DEFAULT_OUTPUT_DIR,
        assert_shadow_row_matches_context,
        base_review_row,
        developer_review_cluster,
        developer_review_sort_key,
        is_likely_alias_candidate,
        load_project_review_context,
        load_resolution_log_rows,
        select_delivery_estimate_spotcheck,
        write_csv,
    )
except ModuleNotFoundError:  # pragma: no cover - script entrypoint fallback
    from phase_a_review_common import (  # type: ignore[no-redef]
        DEFAULT_ESTIMATE_SAMPLE_SIZE,
        DEFAULT_MARKET,
        DEFAULT_OUTPUT_DIR,
        assert_shadow_row_matches_context,
        base_review_row,
        developer_review_cluster,
        developer_review_sort_key,
        is_likely_alias_candidate,
        load_project_review_context,
        load_resolution_log_rows,
        select_delivery_estimate_spotcheck,
        write_csv,
    )
from tcg_pipeline.db.connection import get_session_factory


STATUS_HEADERS = [
    "decision",
    "notes",
    "project_id",
    "project_name",
    "canonical_address",
    "field",
    "current_value",
    "resolved_value",
    "rule_applied",
    "resolution_confidence",
    "project_confidence",
    "delta_shape",
    "last_evidence_date",
    "evidence_count",
    "supporting_evidence_count",
    "evidence_ids",
    "winning_source_type",
    "winning_source_tier",
    "winning_evidence_date",
    "winning_collected_at",
    "winning_source_record_id",
    "status_evidence_type",
    "status_evidence_reason",
    "status_requires_review",
    "status_review_reason",
    "inspection_name",
    "inspection_result",
    "permit_status",
]
UNITS_HEADERS = [
    "decision",
    "notes",
    "project_id",
    "project_name",
    "canonical_address",
    "field",
    "current_value",
    "resolved_value",
    "rule_applied",
    "resolution_confidence",
    "project_confidence",
    "delta_shape",
    "last_evidence_date",
    "evidence_count",
    "supporting_evidence_count",
    "evidence_ids",
    "winning_source_type",
    "winning_source_tier",
    "winning_evidence_date",
    "winning_collected_at",
    "winning_source_record_id",
    "delta_signed",
    "delta_abs",
    "winning_observed_value",
]
DELIVERY_HEADERS = [
    "decision",
    "notes",
    "project_id",
    "project_name",
    "canonical_address",
    "field",
    "current_value",
    "resolved_value",
    "rule_applied",
    "resolution_confidence",
    "project_confidence",
    "delta_shape",
    "last_evidence_date",
    "evidence_count",
    "supporting_evidence_count",
    "evidence_ids",
    "winning_source_type",
    "winning_source_tier",
    "winning_evidence_date",
    "winning_collected_at",
    "winning_source_record_id",
    "delivery_provenance",
    "delivery_date_type",
    "resolved_status",
    "resolved_total_units",
    "estimate_status_input",
    "estimate_total_units_input",
    "description",
]
DEVELOPER_HEADERS = [
    "decision",
    "notes",
    "project_id",
    "project_name",
    "canonical_address",
    "field",
    "current_value",
    "resolved_value",
    "rule_applied",
    "resolution_confidence",
    "project_confidence",
    "delta_shape",
    "last_evidence_date",
    "evidence_count",
    "supporting_evidence_count",
    "evidence_ids",
    "winning_source_type",
    "winning_source_tier",
    "winning_evidence_date",
    "winning_collected_at",
    "winning_source_record_id",
    "raw_value",
    "canonical_name",
    "match_type",
    "match_score",
    "requires_review",
    "review_cluster",
]
DEVELOPER_CATEGORY_HEADERS = [
    "project_id",
    "project_name",
    "canonical_address",
    "field",
    "current_value",
    "resolved_value",
    "rule_applied",
    "resolution_confidence",
    "project_confidence",
    "last_evidence_date",
    "evidence_count",
    "raw_value",
    "canonical_name",
    "match_type",
    "match_score",
    "requires_review",
]
DEVELOPER_CANONICAL_CLEANUP_HEADERS = [
    "project_id",
    "project_name",
    "canonical_address",
    "field",
    "current_value",
    "resolved_value",
    "rule_applied",
    "resolution_confidence",
    "project_confidence",
    "last_evidence_date",
    "evidence_count",
    "raw_value",
    "canonical_name",
    "match_type",
    "match_score",
    "requires_review",
]
DEVELOPER_ALIAS_HEADERS = [
    "alias_name",
    "canonical_name",
    "affected_project_count",
    "affected_project_ids",
    "affected_addresses",
    "suggested_action",
    "note",
]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export Phase A validation review CSVs from resolution_log shadow data.",
    )
    parser.add_argument(
        "--market",
        default=DEFAULT_MARKET,
        help=f"Market to export (default: {DEFAULT_MARKET}).",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Directory for review CSV outputs (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--estimate-sample-size",
        type=int,
        default=DEFAULT_ESTIMATE_SAMPLE_SIZE,
        help=(
            "How many estimated delivery rows to sample for the spot-check packet "
            f"(default: {DEFAULT_ESTIMATE_SAMPLE_SIZE})."
        ),
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    counts = export_phase_a_reviews(
        market=args.market,
        output_dir=output_dir,
        estimate_sample_size=args.estimate_sample_size,
    )
    print(f"Market: {args.market}")
    print(f"Output directory: {output_dir}")
    for name, count in counts.items():
        print(f"{name}: {count}")


def export_phase_a_reviews(
    *,
    market: str,
    output_dir: Path,
    estimate_sample_size: int,
) -> dict[str, int]:
    session_factory = get_session_factory()
    with session_factory() as session:
        context_cache: dict[str, Any] = {}
        status_rows = _build_status_rows(session, market=market, context_cache=context_cache)
        units_rows = _build_units_rows(session, market=market, context_cache=context_cache)
        delivery_rows = _build_delivery_rows(session, market=market, context_cache=context_cache)
        developer_rows = _build_developer_rows(session, market=market, context_cache=context_cache)

    explicit_delivery_rows = [
        row for row in delivery_rows if row["rule_applied"] == "explicit_delivery_date"
    ]
    estimated_delivery_rows = [
        row for row in delivery_rows if row["rule_applied"] == "estimated_calc"
    ]
    estimate_spotcheck_rows = select_delivery_estimate_spotcheck(
        estimated_delivery_rows,
        sample_size=estimate_sample_size,
    )
    developer_category_cleanup_rows = [
        _drop_review_columns(row, drop_decision_columns=True)
        for row in developer_rows
        if row["current_value"] == "Category"
    ]
    developer_canonical_cleanup_rows = [
        _drop_review_columns(row, drop_decision_columns=True)
        for row in developer_rows
        if _is_developer_canonical_cleanup_row(row)
    ]
    developer_review_rows = [
        row
        for row in developer_rows
        if row["current_value"] != "Category"
        and not _is_developer_canonical_cleanup_row(row)
    ]
    developer_review_rows.sort(key=developer_review_sort_key)
    developer_helio_rows = [
        _drop_review_columns(row, drop_decision_columns=False)
        for row in developer_review_rows
        if row["review_cluster"] == "helio_ucla"
    ]
    developer_alias_rows = _build_alias_candidate_rows(developer_review_rows)

    units_rows.sort(
        key=lambda row: (
            -(abs(int(row["delta_signed"])) if row["delta_signed"] not in {"", None} else -1),
            str(row["canonical_address"]),
        )
    )
    status_rows.sort(
        key=lambda row: (
            str(row["current_value"]),
            str(row["resolved_value"]),
            str(row["canonical_address"]),
        )
    )
    explicit_delivery_rows.sort(
        key=lambda row: (
            str(row["current_value"]),
            str(row["resolved_value"]),
            str(row["canonical_address"]),
        )
    )

    write_csv(output_dir / "status_review.csv", fieldnames=STATUS_HEADERS, rows=status_rows)
    write_csv(output_dir / "units_review.csv", fieldnames=UNITS_HEADERS, rows=units_rows)
    write_csv(
        output_dir / "delivery_review.csv",
        fieldnames=DELIVERY_HEADERS,
        rows=explicit_delivery_rows,
    )
    write_csv(
        output_dir / "delivery_estimate_spotcheck.csv",
        fieldnames=[header for header in DELIVERY_HEADERS if header not in {"decision", "notes"}],
        rows=[_drop_review_columns(row, drop_decision_columns=True) for row in estimate_spotcheck_rows],
    )
    write_csv(
        output_dir / "developer_review.csv",
        fieldnames=DEVELOPER_HEADERS,
        rows=developer_review_rows,
    )
    write_csv(
        output_dir / "developer_category_cleanup.csv",
        fieldnames=DEVELOPER_CATEGORY_HEADERS,
        rows=developer_category_cleanup_rows,
    )
    write_csv(
        output_dir / "developer_canonical_cleanup.csv",
        fieldnames=DEVELOPER_CANONICAL_CLEANUP_HEADERS,
        rows=developer_canonical_cleanup_rows,
    )
    write_csv(
        output_dir / "developer_helio_ucla_cluster.csv",
        fieldnames=[header for header in DEVELOPER_HEADERS if header not in {"decision", "notes"}],
        rows=developer_helio_rows,
    )
    write_csv(
        output_dir / "developer_alias_candidates.csv",
        fieldnames=DEVELOPER_ALIAS_HEADERS,
        rows=developer_alias_rows,
    )

    return {
        "status_review_rows": len(status_rows),
        "units_review_rows": len(units_rows),
        "delivery_review_rows": len(explicit_delivery_rows),
        "delivery_estimate_spotcheck_rows": len(estimate_spotcheck_rows),
        "delivery_estimated_fill_rows": len(estimated_delivery_rows),
        "developer_review_rows": len(developer_review_rows),
        "developer_category_cleanup_rows": len(developer_category_cleanup_rows),
        "developer_canonical_cleanup_rows": len(developer_canonical_cleanup_rows),
        "developer_helio_ucla_cluster_rows": len(developer_helio_rows),
        "developer_alias_candidate_rows": len(developer_alias_rows),
    }


def _build_status_rows(session, *, market: str, context_cache: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for log_row, _project in load_resolution_log_rows(session, market=market, field_name="pipeline_status"):
        context = load_project_review_context(
            session,
            log_row.project_id,
            cache=context_cache,
        )
        resolution = context.resolution_result.field_resolutions["pipeline_status"]
        current_value = context.project.pipeline_status
        resolved_value = resolution.value
        assert_shadow_row_matches_context(
            log_row,
            current_value=current_value,
            resolved_value=resolved_value,
        )
        frontier = resolution.metadata.get("evidence_frontier", {})
        winning_evidence = context.evidence_by_id.get(str(resolution.evidence_ids[0])) if resolution.evidence_ids else None
        extracted = winning_evidence.extracted_fields if winning_evidence and isinstance(winning_evidence.extracted_fields, dict) else {}
        row = base_review_row(
            log_row=log_row,
            context=context,
            current_value=current_value,
            resolved_value=resolved_value,
            rule_applied=resolution.rule_applied,
            resolution_confidence=resolution.confidence,
            evidence_ids=resolution.evidence_ids,
            frontier=frontier,
            winning_evidence=winning_evidence,
        )
        row.update(
            {
                "status_evidence_type": _extracted_field_value(extracted, "status_evidence_type"),
                "status_evidence_reason": _extracted_field_value(extracted, "status_evidence_reason"),
                "status_requires_review": resolution.metadata.get("requires_review"),
                "status_review_reason": resolution.metadata.get("review_reason"),
                "inspection_name": _extracted_field_value(extracted, "inspection"),
                "inspection_result": _extracted_field_value(extracted, "inspection_result"),
                "permit_status": _extracted_field_value(extracted, "permit_status"),
            }
        )
        rows.append(row)
    return rows


def _build_units_rows(session, *, market: str, context_cache: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for log_row, _project in load_resolution_log_rows(session, market=market, field_name="total_units"):
        context = load_project_review_context(
            session,
            log_row.project_id,
            cache=context_cache,
        )
        resolution = context.resolution_result.field_resolutions["total_units"]
        current_value = context.project.total_units
        resolved_value = resolution.value
        assert_shadow_row_matches_context(
            log_row,
            current_value=current_value,
            resolved_value=resolved_value,
        )
        frontier = resolution.metadata.get("evidence_frontier", {})
        winning_evidence = context.evidence_by_id.get(str(resolution.evidence_ids[0])) if resolution.evidence_ids else None
        extracted = winning_evidence.extracted_fields if winning_evidence and isinstance(winning_evidence.extracted_fields, dict) else {}
        delta_signed = None
        if current_value is not None and resolved_value is not None:
            delta_signed = int(resolved_value) - int(current_value)
        row = base_review_row(
            log_row=log_row,
            context=context,
            current_value=current_value,
            resolved_value=resolved_value,
            rule_applied=resolution.rule_applied,
            resolution_confidence=resolution.confidence,
            evidence_ids=resolution.evidence_ids,
            frontier=frontier,
            winning_evidence=winning_evidence,
        )
        row.update(
            {
                "delta_signed": delta_signed,
                "delta_abs": abs(delta_signed) if delta_signed is not None else None,
                "winning_observed_value": _extracted_field_value(extracted, "total_units"),
            }
        )
        rows.append(row)
    return rows


def _build_delivery_rows(session, *, market: str, context_cache: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for log_row, _project in load_resolution_log_rows(session, market=market, field_name="date_delivery"):
        context = load_project_review_context(
            session,
            log_row.project_id,
            cache=context_cache,
        )
        resolution = context.resolution_result.field_resolutions["date_delivery"]
        current_value = context.project.date_delivery
        resolved_value = resolution.value
        assert_shadow_row_matches_context(
            log_row,
            current_value=current_value,
            resolved_value=resolved_value,
        )
        frontier = resolution.metadata.get("evidence_frontier", {})
        winning_evidence = context.evidence_by_id.get(str(resolution.evidence_ids[0])) if resolution.evidence_ids else None
        estimate_inputs = resolution.metadata.get("estimate_inputs") or {}
        row = base_review_row(
            log_row=log_row,
            context=context,
            current_value=current_value,
            resolved_value=resolved_value,
            rule_applied=resolution.rule_applied,
            resolution_confidence=resolution.confidence,
            evidence_ids=resolution.evidence_ids,
            frontier=frontier,
            winning_evidence=winning_evidence,
        )
        row.update(
            {
                "delivery_provenance": resolution.metadata.get("provenance"),
                "delivery_date_type": resolution.metadata.get("delivery_date_type"),
                "resolved_status": context.resolution_result.field_resolutions["pipeline_status"].value,
                "resolved_total_units": context.resolution_result.field_resolutions["total_units"].value,
                "estimate_status_input": estimate_inputs.get("status"),
                "estimate_total_units_input": estimate_inputs.get("total_units"),
                "description": resolution.metadata.get("description"),
            }
        )
        rows.append(row)
    return rows


def _build_developer_rows(session, *, market: str, context_cache: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for log_row, _project in load_resolution_log_rows(session, market=market, field_name="developer"):
        context = load_project_review_context(
            session,
            log_row.project_id,
            cache=context_cache,
        )
        resolution = context.resolution_result.field_resolutions["developer"]
        current_value = context.project.developer
        resolved_value = resolution.value
        assert_shadow_row_matches_context(
            log_row,
            current_value=current_value,
            resolved_value=resolved_value,
        )
        frontier = resolution.metadata.get("evidence_frontier", {})
        winning_evidence = context.evidence_by_id.get(str(resolution.evidence_ids[0])) if resolution.evidence_ids else None
        row = base_review_row(
            log_row=log_row,
            context=context,
            current_value=current_value,
            resolved_value=resolved_value,
            rule_applied=resolution.rule_applied,
            resolution_confidence=resolution.confidence,
            evidence_ids=resolution.evidence_ids,
            frontier=frontier,
            winning_evidence=winning_evidence,
        )
        row.update(
            {
                "raw_value": resolution.metadata.get("raw_value"),
                "canonical_name": resolution.metadata.get("canonical_name"),
                "match_type": resolution.metadata.get("match_type"),
                "match_score": resolution.metadata.get("score"),
                "requires_review": resolution.metadata.get("requires_review"),
                "review_cluster": developer_review_cluster(current_value),
            }
        )
        rows.append(row)
    return rows


def _build_alias_candidate_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if not is_likely_alias_candidate(row):
            continue
        key = (str(row["current_value"]), str(row["resolved_value"]))
        grouped.setdefault(key, []).append(row)

    alias_rows: list[dict[str, Any]] = []
    for (alias_name, canonical_name), grouped_rows in sorted(grouped.items()):
        alias_rows.append(
            {
                "alias_name": alias_name,
                "canonical_name": canonical_name,
                "affected_project_count": len(grouped_rows),
                "affected_project_ids": [row["project_id"] for row in grouped_rows],
                "affected_addresses": [row["canonical_address"] for row in grouped_rows],
                "suggested_action": "add_registry_alias_then_rerun_shadow",
                "note": (
                    "Likely missing alias pair surfaced during Phase A shadow validation."
                ),
            }
        )
    return alias_rows


def _is_developer_canonical_cleanup_row(row: dict[str, Any]) -> bool:
    match_type = str(row.get("match_type") or "")
    current_value = row.get("current_value")
    resolved_value = row.get("resolved_value")
    canonical_name = row.get("canonical_name")
    if match_type not in {"exact_alias", "exact_canonical"}:
        return False
    if current_value in {None, ""} or resolved_value in {None, ""}:
        return False
    if current_value == resolved_value:
        return False
    return resolved_value == canonical_name


def _drop_review_columns(
    row: dict[str, Any],
    *,
    drop_decision_columns: bool,
) -> dict[str, Any]:
    filtered = dict(row)
    if drop_decision_columns:
        filtered.pop("decision", None)
        filtered.pop("notes", None)
    return filtered


def _extracted_field_value(extracted: dict[str, Any], field_name: str) -> Any:
    payload = extracted.get(field_name)
    if not isinstance(payload, dict):
        return None
    return payload.get("value")


if __name__ == "__main__":
    main()

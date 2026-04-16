from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from geoalchemy2.elements import WKTElement
from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from tcg_pipeline.db.models import (
    AgeRestriction,
    DismissReason,
    DismissedRecord,
    GeocodeConfidence,
    IdentifierType,
    PipelineStatus,
    ProductType,
    Project,
    ProjectIdentifier,
    ProjectSourceRecord,
    RelationshipType,
    RentOrSale,
    StatusConfidence,
    StatusHistory,
)
from tcg_pipeline.matching.normalizer import normalize_address, normalize_city, normalize_postal_code

HEADER_ROW_INDEX = 3
DATA_START_ROW_INDEX = 4
DATA_STORAGE_TAB_NAME = "DataStorage"
NULL_SENTINELS = {"", "--"}
PIPEDREAM_SOURCE_NAME = "pipedream"
PIPEDREAM_CREATED_BY = "pipedream_import"
HEADER_COLUMNS = (
    "ProjectID",
    "Name",
    "Developer",
    "Address",
    "State",
    "County",
    "City",
    "Zip",
    "Region",
    "Lat",
    "Long",
    "RentFS",
    "MRUnits",
    "AffUnits",
    "TotUnits",
    "Acres",
    "RetailSF",
    "OfficeSF",
    "HKeys",
    "ProdType",
    "Elevation",
    "Senior",
    "PercS",
    "Perc1B",
    "Perc2B",
    "PercOther",
    "CurrStatus",
    "CurrStatusDate",
    "Jurisdiction",
    "RefNum",
    "APN",
    "Plan1Name",
    "Plan1City",
    "Plan1Email",
    "Plan1Phone",
    "Plan2Name",
    "Plan2City",
    "Plan2Email",
    "Plan2Phone",
    "Notes",
    "Site1",
    "Site2",
    "Site3",
    "Site4",
    "PersonalNotes",
    "ChangeNotes",
    "PStat1",
    "PStatDate1",
    "PStat2",
    "PStatDate2",
    "PStat3",
    "PStatDate3",
    "PStat4",
    "PStatDate4",
    "PStat5",
    "PStatDate5",
    "PStat6",
    "PStatDate6",
    "PrevName1",
    "PrevName2",
    "CorrP",
    "PCPart",
    "RelP1",
    "RelP2",
    "RelP3",
    "RelP4",
    "RelP5",
    "RelP6",
    "DeliveryDate",
    "Editor",
    "EditDate",
)
PROJECT_ID_FIELDS = {"ProjectID", "CorrP", "PCPart", "RelP1", "RelP2", "RelP3", "RelP4", "RelP5", "RelP6"}
STATUS_FIELD_PAIRS = (
    ("PStat6", "PStatDate6"),
    ("PStat5", "PStatDate5"),
    ("PStat4", "PStatDate4"),
    ("PStat3", "PStatDate3"),
    ("PStat2", "PStatDate2"),
    ("PStat1", "PStatDate1"),
)
SOURCE_URL_FIELDS = ("Site1", "Site2", "Site3", "Site4")
PREVIOUS_NAME_FIELDS = ("PrevName1", "PrevName2")
RELATIONSHIP_FIELD_MAP = {
    "CorrP": RelationshipType.DUPLICATE,
    "PCPart": RelationshipType.COUNTERPART,
    "RelP1": RelationshipType.PHASE,
    "RelP2": RelationshipType.PHASE,
    "RelP3": RelationshipType.PHASE,
    "RelP4": RelationshipType.PHASE,
    "RelP5": RelationshipType.PHASE,
    "RelP6": RelationshipType.PHASE,
}
DELETE_STATUS_REASON_MAP = {
    "Delete - Duplicate": DismissReason.DUPLICATE,
    "Delete - Outside Market Area": DismissReason.OUTSIDE_MARKET,
    "Delete - Not Residential": DismissReason.NOT_RESIDENTIAL,
}
PIPELINE_STATUS_MAP = {
    PipelineStatus.CONCEPTUAL.value: PipelineStatus.CONCEPTUAL,
    PipelineStatus.PROPOSED.value: PipelineStatus.PROPOSED,
    PipelineStatus.PENDING.value: PipelineStatus.PENDING,
    PipelineStatus.APPROVED.value: PipelineStatus.APPROVED,
    PipelineStatus.UNDER_CONSTRUCTION.value: PipelineStatus.UNDER_CONSTRUCTION,
    PipelineStatus.PRE_LEASING_PRE_SELLING.value: PipelineStatus.PRE_LEASING_PRE_SELLING,
    PipelineStatus.COMPLETE.value: PipelineStatus.COMPLETE,
    PipelineStatus.STALLED.value: PipelineStatus.STALLED,
    PipelineStatus.INACTIVE.value: PipelineStatus.INACTIVE,
    "Delete - Duplicate": PipelineStatus.DELETE_DUPLICATE,
    "Delete - Outside Market Area": PipelineStatus.DELETE_OUTSIDE_MARKET_AREA,
    "Delete - Not Residential": PipelineStatus.DELETE_NOT_RESIDENTIAL,
}
RENT_OR_SALE_MAP = {
    RentOrSale.RENTAL.value: RentOrSale.RENTAL,
    RentOrSale.FOR_SALE.value: RentOrSale.FOR_SALE,
    RentOrSale.BOTH.value: RentOrSale.BOTH,
    "Both (Rental & FS)": RentOrSale.BOTH,
    RentOrSale.UNKNOWN.value: RentOrSale.UNKNOWN,
}
PRODUCT_TYPE_MAP = {
    ProductType.APARTMENT.value: ProductType.APARTMENT,
    ProductType.CONDO.value: ProductType.CONDO,
    ProductType.SINGLE_FAMILY.value: ProductType.SINGLE_FAMILY,
    "Single Family": ProductType.SINGLE_FAMILY,
    ProductType.TOWNHOME.value: ProductType.TOWNHOME,
    ProductType.MICRO_CO_LIVING.value: ProductType.MICRO_CO_LIVING,
    ProductType.OTHER.value: ProductType.OTHER,
    ProductType.UNKNOWN.value: ProductType.UNKNOWN,
}
AGE_RESTRICTION_MAP = {
    AgeRestriction.NON_AGE_RESTRICTED.value: AgeRestriction.NON_AGE_RESTRICTED,
    AgeRestriction.SENIOR.value: AgeRestriction.SENIOR,
    AgeRestriction.STUDENT.value: AgeRestriction.STUDENT,
    AgeRestriction.UNKNOWN.value: AgeRestriction.UNKNOWN,
}


@dataclass(slots=True)
class StagedProjectRelationship:
    project_identifier_value: str
    related_project_identifier_value: str
    relationship_type: RelationshipType
    source_field: str
    notes: str | None = None


@dataclass(slots=True)
class PipedreamProjectRecord:
    row_number: int
    project_identifier_value: str
    project: Project
    identifiers: list[ProjectIdentifier]
    status_history: list[StatusHistory]
    source_record: ProjectSourceRecord


@dataclass(slots=True)
class PipedreamImportResult:
    source_path: Path
    project_records: list[PipedreamProjectRecord] = field(default_factory=list)
    dismissed_records: list[DismissedRecord] = field(default_factory=list)
    staged_relationships: list[StagedProjectRelationship] = field(default_factory=list)
    skipped_project_ids: list[str] = field(default_factory=list)

    @property
    def imported_count(self) -> int:
        return len(self.project_records)

    @property
    def dismissed_count(self) -> int:
        return len(self.dismissed_records)


class PipedreamIngester:
    def __init__(
        self,
        *,
        market: str,
        source_name: str = PIPEDREAM_SOURCE_NAME,
        allowed_cities: Iterable[str] | None = None,
    ) -> None:
        self.market = market
        self.source_name = source_name
        self.allowed_cities = {
            normalized
            for city in (allowed_cities or [])
            if (normalized := normalize_city(city, market=market))
        }

    def ingest_workbook(self, workbook_path: str | Path) -> PipedreamImportResult:
        path = Path(workbook_path)
        workbook = load_workbook(path, data_only=True, read_only=True)
        try:
            if DATA_STORAGE_TAB_NAME not in workbook.sheetnames:
                raise ValueError(
                    f"{path.name} is missing the required '{DATA_STORAGE_TAB_NAME}' worksheet."
                )

            worksheet = workbook[DATA_STORAGE_TAB_NAME]
            column_to_header = _extract_headers(worksheet)
            imported_at = datetime.now(timezone.utc)
            result = PipedreamImportResult(source_path=path)

            for row_number, row in enumerate(
                worksheet.iter_rows(min_row=DATA_START_ROW_INDEX),
                start=DATA_START_ROW_INDEX,
            ):
                payload = _build_row_payload(row, column_to_header)
                project_identifier = _clean_project_identifier(payload.get("ProjectID"))
                if not project_identifier:
                    continue

                if self.allowed_cities:
                    normalized_city = normalize_city(payload.get("City"), market=self.market)
                    if normalized_city not in self.allowed_cities:
                        result.skipped_project_ids.append(project_identifier)
                        continue

                raw_status = _clean_text(payload.get("CurrStatus"))
                result.staged_relationships.extend(
                    _build_relationships(payload, project_identifier)
                )

                if raw_status in DELETE_STATUS_REASON_MAP:
                    result.dismissed_records.append(
                        _build_dismissed_record(
                            payload=payload,
                            project_identifier=project_identifier,
                            reason=DELETE_STATUS_REASON_MAP[raw_status],
                            source_name=self.source_name,
                            market=self.market,
                        )
                    )
                    continue

                project_record = _build_project_record(
                    payload=payload,
                    row_number=row_number,
                    project_identifier=project_identifier,
                    market=self.market,
                    source_name=self.source_name,
                    imported_at=imported_at,
                )
                result.project_records.append(project_record)

            return result
        finally:
            workbook.close()


def _extract_headers(worksheet: Worksheet) -> dict[int, str]:
    headers: dict[int, str] = {}
    for cell in worksheet[HEADER_ROW_INDEX]:
        value = _clean_text(cell.value)
        if value:
            headers[cell.column] = value
    return headers


def _build_row_payload(row: tuple[Any, ...], column_to_header: dict[int, str]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for column_index, header in column_to_header.items():
        if column_index > len(row):
            continue
        cell_value = row[column_index - 1].value
        if header in PROJECT_ID_FIELDS:
            payload[header] = _clean_project_identifier(cell_value)
        else:
            payload[header] = cell_value
    return payload


def _build_project_record(
    *,
    payload: dict[str, Any],
    row_number: int,
    project_identifier: str,
    market: str,
    source_name: str,
    imported_at: datetime,
) -> PipedreamProjectRecord:
    normalized_address = normalize_address(
        _clean_text(payload.get("Address")) or "",
        city=_clean_text(payload.get("City")),
        state=_clean_text(payload.get("State")),
        postal_code=_clean_text(payload.get("Zip")),
        market=market,
    )
    project = Project(
        canonical_address=normalized_address.canonical_address or normalized_address.raw_address,
        raw_addresses=_dedupe_strings([_clean_text(payload.get("Address"))]),
        lat=_parse_float(payload.get("Lat")),
        lng=_parse_float(payload.get("Long")),
        location=_build_location(payload.get("Lat"), payload.get("Long")),
        geocode_confidence=_determine_geocode_confidence(payload),
        market=market,
        city=normalized_address.city or _clean_text(payload.get("City")) or "",
        state=normalized_address.state or _clean_text(payload.get("State")) or "",
        county=_clean_text(payload.get("County")) or "",
        zip=normalize_postal_code(payload.get("Zip")),
        tcg_region=_clean_text(payload.get("Region")),
        jurisdiction=_clean_text(payload.get("Jurisdiction")),
        project_name=_clean_text(payload.get("Name")),
        previous_names=_dedupe_strings(_clean_text(payload.get(field)) for field in PREVIOUS_NAME_FIELDS),
        developer=_clean_text(payload.get("Developer")),
        rent_or_sale=_parse_rent_or_sale(payload.get("RentFS")),
        product_type=_parse_product_type(payload.get("ProdType")),
        age_restriction=_parse_age_restriction(payload.get("Senior")),
        stories=_parse_int(payload.get("Elevation")),
        total_units=_parse_int(payload.get("TotUnits")),
        market_rate_units=_parse_int(payload.get("MRUnits")),
        affordable_units=_parse_int(payload.get("AffUnits")),
        pct_studio=_parse_float(payload.get("PercS")),
        pct_1bed=_parse_float(payload.get("Perc1B")),
        pct_2bed=_parse_float(payload.get("Perc2B")),
        pct_other_bed=_parse_float(payload.get("PercOther")),
        acres=_parse_float(payload.get("Acres")),
        retail_sf=_parse_int(payload.get("RetailSF")),
        office_sf=_parse_int(payload.get("OfficeSF")),
        hotel_keys=_parse_int(payload.get("HKeys")),
        pipeline_status=_parse_pipeline_status(payload.get("CurrStatus")),
        status_date=_parse_date(payload.get("CurrStatusDate")),
        status_confidence=StatusConfidence.HIGH,
        status_source=source_name,
        date_delivery=_parse_date(payload.get("DeliveryDate")),
        planner_1_name=_clean_text(payload.get("Plan1Name")),
        planner_1_city=_clean_text(payload.get("Plan1City")),
        planner_1_email=_clean_text(payload.get("Plan1Email")),
        planner_1_phone=_clean_text(payload.get("Plan1Phone")),
        planner_2_name=_clean_text(payload.get("Plan2Name")),
        planner_2_city=_clean_text(payload.get("Plan2City")),
        planner_2_email=_clean_text(payload.get("Plan2Email")),
        planner_2_phone=_clean_text(payload.get("Plan2Phone")),
        researcher_notes=_clean_text(payload.get("Notes")),
        personal_notes=_clean_text(payload.get("PersonalNotes")),
        change_notes=_clean_text(payload.get("ChangeNotes")),
        source_urls=_dedupe_strings(_clean_text(payload.get(field)) for field in SOURCE_URL_FIELDS),
        last_editor=_clean_text(payload.get("Editor")),
        last_edit_date=_parse_date(payload.get("EditDate")),
        created_by=PIPEDREAM_CREATED_BY,
    )

    identifiers = _build_identifiers(project, payload, source_name, imported_at, project_identifier)
    status_history = _build_status_history(project, payload, source_name)
    mapped_fields = {
        "project_name": project.project_name,
        "canonical_address": project.canonical_address,
        "pipeline_status": project.pipeline_status.value,
        "status_date": _serialize_json_value(project.status_date),
        "city": project.city,
        "state": project.state,
        "zip": project.zip,
        "total_units": project.total_units,
        "market_rate_units": project.market_rate_units,
        "affordable_units": project.affordable_units,
        "developer": project.developer,
    }
    source_record = ProjectSourceRecord(
        project=project,
        source_name=source_name,
        source_record_id=project_identifier,
        source_url=project.source_urls[0] if project.source_urls else None,
        first_seen_at=imported_at,
        last_seen_at=imported_at,
        last_pulled_at=imported_at,
        raw_payload={key: _serialize_json_value(payload.get(key)) for key in HEADER_COLUMNS if key in payload},
        mapped_fields=mapped_fields,
        field_provenance={
            key: {"source": source_name, "confidence": StatusConfidence.HIGH.value}
            for key in mapped_fields
        },
    )

    return PipedreamProjectRecord(
        row_number=row_number,
        project_identifier_value=project_identifier,
        project=project,
        identifiers=identifiers,
        status_history=status_history,
        source_record=source_record,
    )


def _build_identifiers(
    project: Project,
    payload: dict[str, Any],
    source_name: str,
    imported_at: datetime,
    project_identifier: str,
) -> list[ProjectIdentifier]:
    identifiers = [
        ProjectIdentifier(
            project=project,
            identifier_type=IdentifierType.TCG_PIPEDREAM_ID,
            value=project_identifier,
            source=source_name,
            is_primary=True,
            first_seen_at=imported_at,
            last_seen_at=imported_at,
        )
    ]

    case_number = _clean_text(payload.get("RefNum"))
    if case_number:
        identifiers.append(
            ProjectIdentifier(
                project=project,
                identifier_type=IdentifierType.CASE_NUMBER,
                value=case_number,
                source=source_name,
                first_seen_at=imported_at,
                last_seen_at=imported_at,
            )
        )

    apn = _clean_identifier_text(payload.get("APN"))
    if apn:
        identifiers.append(
            ProjectIdentifier(
                project=project,
                identifier_type=IdentifierType.APN,
                value=apn,
                source=source_name,
                first_seen_at=imported_at,
                last_seen_at=imported_at,
            )
        )

    return identifiers


def _build_status_history(
    project: Project,
    payload: dict[str, Any],
    source_name: str,
) -> list[StatusHistory]:
    status_history: list[StatusHistory] = []
    for status_field, date_field in STATUS_FIELD_PAIRS:
        raw_status = _clean_text(payload.get(status_field))
        if not raw_status:
            continue
        status_history.append(
            StatusHistory(
                project=project,
                status=_parse_pipeline_status(raw_status),
                status_date=_parse_date(payload.get(date_field)),
                source=source_name,
                notes=f"Imported from {status_field}",
            )
        )

    current_status = _clean_text(payload.get("CurrStatus"))
    if current_status:
        status_history.append(
            StatusHistory(
                project=project,
                status=_parse_pipeline_status(current_status),
                status_date=_parse_date(payload.get("CurrStatusDate")),
                source=source_name,
                notes="Imported from current Pipedream status",
            )
        )

    return status_history


def _build_relationships(
    payload: dict[str, Any],
    project_identifier: str,
) -> list[StagedProjectRelationship]:
    relationships: list[StagedProjectRelationship] = []
    for field_name, relationship_type in RELATIONSHIP_FIELD_MAP.items():
        related_identifier = _clean_project_identifier(payload.get(field_name))
        if not related_identifier or related_identifier == project_identifier:
            continue
        relationships.append(
            StagedProjectRelationship(
                project_identifier_value=project_identifier,
                related_project_identifier_value=related_identifier,
                relationship_type=relationship_type,
                source_field=field_name,
                notes=f"Imported from {field_name}",
            )
        )
    return relationships


def _build_dismissed_record(
    *,
    payload: dict[str, Any],
    project_identifier: str,
    reason: DismissReason,
    source_name: str,
    market: str,
) -> DismissedRecord:
    normalized_address = normalize_address(
        _clean_text(payload.get("Address")) or "",
        city=_clean_text(payload.get("City")),
        state=_clean_text(payload.get("State")),
        postal_code=_clean_text(payload.get("Zip")),
        market=market,
    )
    corrp = _clean_project_identifier(payload.get("CorrP"))
    notes_parts = [_clean_text(payload.get("CurrStatus"))]
    if corrp:
        notes_parts.append(f"CorrP={corrp}")
    return DismissedRecord(
        source=source_name,
        source_record_id=project_identifier,
        canonical_address=normalized_address.canonical_address,
        reason=reason,
        dismissed_by=PIPEDREAM_CREATED_BY,
        notes="; ".join(part for part in notes_parts if part),
    )


def _parse_pipeline_status(value: Any) -> PipelineStatus:
    cleaned = _clean_text(value)
    if not cleaned:
        return PipelineStatus.PROPOSED
    return PIPELINE_STATUS_MAP.get(cleaned, PipelineStatus.PROPOSED)


def _parse_rent_or_sale(value: Any) -> RentOrSale:
    cleaned = _clean_text(value)
    if not cleaned:
        return RentOrSale.UNKNOWN
    return RENT_OR_SALE_MAP.get(cleaned, RentOrSale.UNKNOWN)


def _parse_product_type(value: Any) -> ProductType:
    cleaned = _clean_text(value)
    if not cleaned:
        return ProductType.UNKNOWN
    return PRODUCT_TYPE_MAP.get(cleaned, ProductType.OTHER)


def _parse_age_restriction(value: Any) -> AgeRestriction:
    cleaned = _clean_text(value)
    if not cleaned:
        return AgeRestriction.UNKNOWN
    return AGE_RESTRICTION_MAP.get(cleaned, AgeRestriction.UNKNOWN)


def _build_location(lat_value: Any, lng_value: Any) -> WKTElement | None:
    lat = _parse_float(lat_value)
    lng = _parse_float(lng_value)
    if lat is None or lng is None:
        return None
    return WKTElement(f"POINT({lng} {lat})", srid=4326)


def _determine_geocode_confidence(payload: dict[str, Any]) -> GeocodeConfidence:
    if _parse_float(payload.get("Lat")) is not None and _parse_float(payload.get("Long")) is not None:
        return GeocodeConfidence.HIGH
    return GeocodeConfidence.NONE


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if text in NULL_SENTINELS:
        return None
    return text


def _clean_identifier_text(value: Any) -> str | None:
    cleaned = _clean_text(value)
    if cleaned is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = _parse_float(value)
        if numeric is None:
            return cleaned
        return str(int(numeric)) if numeric.is_integer() else str(numeric)
    return cleaned


def _clean_project_identifier(value: Any) -> str | None:
    cleaned = _clean_text(value)
    if cleaned is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{float(value):.5f}"
    if "." in cleaned:
        prefix, suffix = cleaned.split(".", 1)
        if prefix.isdigit() and suffix.isdigit():
            return f"{prefix}.{suffix.ljust(5, '0')[:5]}"
    return cleaned


def _parse_int(value: Any) -> int | None:
    numeric = _parse_float(value)
    if numeric is None:
        return None
    return int(round(numeric))


def _parse_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    cleaned = _clean_text(value)
    if cleaned is None:
        return None

    normalized = cleaned.replace(",", "").replace("%", "")
    try:
        return float(normalized)
    except ValueError:
        return None


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    cleaned = _clean_text(value)
    if cleaned is None:
        return None

    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def _dedupe_strings(values: Iterable[str | None]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        if not value or value in deduped:
            continue
        deduped.append(value)
    return deduped


def _serialize_json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value

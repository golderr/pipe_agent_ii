from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from geoalchemy2 import Geography
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
    true,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    return [member.value for member in enum_cls]


class PipelineStatus(str, enum.Enum):
    CONCEPTUAL = "Conceptual"
    PROPOSED = "Proposed"
    PENDING = "Pending"
    APPROVED = "Approved"
    UNDER_CONSTRUCTION = "Under Construction"
    PRE_LEASING_PRE_SELLING = "Pre-Leasing/Pre-Selling"
    COMPLETE = "Complete"
    STALLED = "Stalled"
    INACTIVE = "Inactive"
    DELETE_DUPLICATE = "Delete-Duplicate"
    DELETE_OUTSIDE_MARKET_AREA = "Delete-Outside Market Area"
    DELETE_NOT_RESIDENTIAL = "Delete-Not Residential"


class RentOrSale(str, enum.Enum):
    RENTAL = "Rental"
    FOR_SALE = "For-Sale"
    BOTH = "Both"
    UNKNOWN = "Unknown"


class ProductType(str, enum.Enum):
    APARTMENT = "Apartment"
    CONDO = "Condo"
    SINGLE_FAMILY = "Single-Family"
    TOWNHOME = "Townhome"
    MICRO_CO_LIVING = "Micro/Co-Living"
    OTHER = "Other"
    UNKNOWN = "Unknown"


class AgeRestriction(str, enum.Enum):
    NON_AGE_RESTRICTED = "Non Age-Restricted"
    SENIOR = "Senior"
    STUDENT = "Student"
    UNKNOWN = "Unknown"


class StatusConfidence(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class GeocodeConfidence(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


class IdentifierType(str, enum.Enum):
    APN = "apn"
    ZIMAS_PIN = "zimas_pin"
    CASE_NUMBER = "case_number"
    PERMIT_NUMBER = "permit_number"
    COSTAR_PROPERTY_ID = "costar_property_id"
    TCG_PIPEDREAM_ID = "tcg_pipedream_id"


class RelationshipType(str, enum.Enum):
    PHASE = "phase"
    MASTER_PLAN = "master_plan"
    COUNTERPART = "counterpart"
    DUPLICATE = "duplicate"
    SUPERSEDES = "supersedes"


class ReviewItemType(str, enum.Enum):
    NEW_CANDIDATE = "new_candidate"
    STATUS_CHANGE = "status_change"
    POSSIBLE_MATCH = "possible_match"
    POTENTIAL_STALL = "potential_stall"
    LOW_CONFIDENCE = "low_confidence"
    OVERRIDE_CONTRADICTION = "override_contradiction"


class ReviewItemStatus(str, enum.Enum):
    OPEN = "open"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DEFERRED = "deferred"
    AUTO_ACCEPTED = "auto_accepted"


class ReviewDecisionAction(str, enum.Enum):
    ACCEPT = "accept"
    REJECT = "reject"
    OVERRIDE = "override"
    DEFER = "defer"
    NOTE = "note"


class ChangeType(str, enum.Enum):
    AUTO_ACCEPTED = "auto_accepted"
    RESEARCHER_CONFIRMED = "researcher_confirmed"
    RESEARCHER_REJECTED = "researcher_rejected"
    RESEARCHER_OVERRIDE = "researcher_override"


class Priority(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class DismissReason(str, enum.Enum):
    NOT_RESIDENTIAL = "not_residential"
    OUTSIDE_MARKET = "outside_market"
    DUPLICATE = "duplicate"
    TOO_SMALL = "too_small"
    OTHER = "other"


class ScrapeJobStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ScrapeTriggerType(enum.StrEnum):
    USER_INITIATED = "user_initiated"
    SCHEDULED = "scheduled"


class ScrapeJobKind(enum.StrEnum):
    COLLECTOR_RUN = "collector_run"
    NEWS_SCRAPE = "news_scrape"
    NEWS_PASTE_A_LINK = "news_paste_a_link"
    NEWS_REEXTRACT = "news_reextract"
    NEWS_BACKFILL_CHUNK = "news_backfill_chunk"


class NewsFetchStatus(enum.StrEnum):
    PENDING = "pending"
    FETCHED = "fetched"
    FETCH_FAILED = "fetch_failed"
    PARSE_FAILED = "parse_failed"
    PAYWALLED = "paywalled"
    DEAD_LINK = "dead_link"


class NewsTriageStatus(enum.StrEnum):
    PENDING = "pending"
    RELEVANT = "relevant"
    NOT_RELEVANT = "not_relevant"
    ERROR = "error"


class NewsExtractionPass(enum.StrEnum):
    TRIAGE = "triage"
    EXTRACTION = "extraction"
    REEXTRACTION = "reextraction"


class NewsExtractionParseStatus(enum.StrEnum):
    OK = "ok"
    PARSE_ERROR = "parse_error"
    SCHEMA_INVALID = "schema_invalid"
    REFUSED = "refused"
    TRUNCATED = "truncated"


class NewsMatchStatus(enum.StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    POSSIBLE = "possible"
    NEW_CANDIDATE = "new_candidate"
    DISCARDED = "discarded"
    MANUAL_RELINK = "manual_relink"
    SUPERSEDED_BY_REEXTRACTION = "superseded_by_reextraction"


class NewsReferenceConfidence(enum.StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class CoStarUploadStatus(enum.StrEnum):
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


PIPELINE_STATUS_ENUM = SAEnum(
    PipelineStatus,
    name="pipeline_status_enum",
    values_callable=_enum_values,
)
RENT_OR_SALE_ENUM = SAEnum(RentOrSale, name="rent_or_sale_enum", values_callable=_enum_values)
PRODUCT_TYPE_ENUM = SAEnum(ProductType, name="product_type_enum", values_callable=_enum_values)
AGE_RESTRICTION_ENUM = SAEnum(
    AgeRestriction,
    name="age_restriction_enum",
    values_callable=_enum_values,
)
STATUS_CONFIDENCE_ENUM = SAEnum(
    StatusConfidence,
    name="status_confidence_enum",
    values_callable=_enum_values,
)
GEOCODE_CONFIDENCE_ENUM = SAEnum(
    GeocodeConfidence,
    name="geocode_confidence_enum",
    values_callable=_enum_values,
)
IDENTIFIER_TYPE_ENUM = SAEnum(
    IdentifierType,
    name="identifier_type_enum",
    values_callable=_enum_values,
)
RELATIONSHIP_TYPE_ENUM = SAEnum(
    RelationshipType,
    name="relationship_type_enum",
    values_callable=_enum_values,
)
REVIEW_ITEM_TYPE_ENUM = SAEnum(
    ReviewItemType,
    name="review_item_type_enum",
    values_callable=_enum_values,
)
REVIEW_ITEM_STATUS_ENUM = SAEnum(
    ReviewItemStatus,
    name="review_item_status_enum",
    values_callable=_enum_values,
)
REVIEW_DECISION_ACTION_ENUM = SAEnum(
    ReviewDecisionAction,
    name="review_decision_action_enum",
    values_callable=_enum_values,
)
CHANGE_TYPE_ENUM = SAEnum(ChangeType, name="change_type_enum", values_callable=_enum_values)
PRIORITY_ENUM = SAEnum(Priority, name="priority_enum", values_callable=_enum_values)
DISMISS_REASON_ENUM = SAEnum(
    DismissReason,
    name="dismiss_reason_enum",
    values_callable=_enum_values,
)
SCRAPE_JOB_STATUS_ENUM = SAEnum(
    ScrapeJobStatus,
    name="scrape_job_status_enum",
    values_callable=_enum_values,
)
SCRAPE_TRIGGER_TYPE_ENUM = SAEnum(
    ScrapeTriggerType,
    name="scrape_trigger_type_enum",
    values_callable=_enum_values,
)
COSTAR_UPLOAD_STATUS_ENUM = SAEnum(
    CoStarUploadStatus,
    name="costar_upload_status_enum",
    values_callable=_enum_values,
)


naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=naming_convention)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Market(Base, TimestampMixin):
    __tablename__ = "markets"
    __table_args__ = (
        Index("ix_markets_state", "state"),
        Index("ix_markets_parent_market_id", "parent_market_id"),
        Index("ix_markets_slug", "slug"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(2), nullable=False)
    market_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_market_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("markets.id"),
        nullable=True,
    )

    parent_market: Mapped["Market | None"] = relationship(
        remote_side="Market.id",
        back_populates="child_markets",
    )
    child_markets: Mapped[list["Market"]] = relationship(back_populates="parent_market")
    jurisdictions: Mapped[list["Jurisdiction"]] = relationship(back_populates="market")
    projects: Mapped[list["Project"]] = relationship(back_populates="market_ref")
    news_sources: Mapped[list["NewsSource"]] = relationship(back_populates="market")


class Jurisdiction(Base, TimestampMixin):
    __tablename__ = "jurisdictions"
    __table_args__ = (
        UniqueConstraint("state", "slug"),
        Index("ix_jurisdictions_market_id", "market_id"),
        Index("ix_jurisdictions_state", "state"),
        Index("ix_jurisdictions_slug", "slug"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(2), nullable=False)
    market_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("markets.id"),
        nullable=False,
    )
    entity_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    geom: Mapped[object | None] = mapped_column(
        Geography(geometry_type="MULTIPOLYGON", srid=4326),
        nullable=True,
    )

    market: Mapped[Market] = relationship(back_populates="jurisdictions")
    projects: Mapped[list["Project"]] = relationship(back_populates="jurisdiction_ref")
    source_registrations: Mapped[list["SourceRegistration"]] = relationship(
        back_populates="jurisdiction",
    )
    source_runs: Mapped[list["SourceRun"]] = relationship(back_populates="jurisdiction")
    scrape_jobs: Mapped[list["ScrapeJob"]] = relationship(back_populates="jurisdiction")
    costar_uploads: Mapped[list["CoStarUpload"]] = relationship(back_populates="jurisdiction")
    news_sources: Mapped[list["NewsSource"]] = relationship(back_populates="jurisdiction")


class Project(Base, TimestampMixin):
    __tablename__ = "projects"
    __table_args__ = (
        Index("ix_projects_market", "market"),
        Index("ix_projects_market_id", "market_id"),
        Index("ix_projects_jurisdiction_id", "jurisdiction_id"),
        Index("ix_projects_pipeline_status", "pipeline_status"),
        Index("ix_projects_canonical_address", "canonical_address"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    canonical_address: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_addresses: Mapped[list[str]] = mapped_column(ARRAY(String()), default=list, nullable=False)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    location: Mapped[object | None] = mapped_column(
        Geography(geometry_type="POINT", srid=4326),
        nullable=True,
    )
    geocode_confidence: Mapped[GeocodeConfidence] = mapped_column(
        GEOCODE_CONFIDENCE_ENUM,
        nullable=False,
        default=GeocodeConfidence.NONE,
    )

    market: Mapped[str] = mapped_column(String(100), nullable=False)
    market_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("markets.id"),
        nullable=True,
    )
    city: Mapped[str] = mapped_column(String(120), nullable=False)
    state: Mapped[str] = mapped_column(String(2), nullable=False)
    county: Mapped[str] = mapped_column(String(120), nullable=False)
    zip: Mapped[str | None] = mapped_column(String(10), nullable=True)
    tcg_region: Mapped[str | None] = mapped_column(String(150), nullable=True)
    jurisdiction: Mapped[str | None] = mapped_column(String(150), nullable=True)
    jurisdiction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jurisdictions.id"),
        nullable=True,
    )
    costar_submarket: Mapped[str | None] = mapped_column(String(150), nullable=True)
    zoning: Mapped[str | None] = mapped_column(String(120), nullable=True)

    project_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    previous_names: Mapped[list[str]] = mapped_column(ARRAY(String()), default=list, nullable=False)
    developer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    applicant: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    rent_or_sale: Mapped[RentOrSale] = mapped_column(
        RENT_OR_SALE_ENUM,
        nullable=False,
        default=RentOrSale.UNKNOWN,
    )
    product_type: Mapped[ProductType] = mapped_column(
        PRODUCT_TYPE_ENUM,
        nullable=False,
        default=ProductType.UNKNOWN,
    )
    age_restriction: Mapped[AgeRestriction] = mapped_column(
        AGE_RESTRICTION_ENUM,
        nullable=False,
        default=AgeRestriction.UNKNOWN,
    )
    stories: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    market_rate_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    affordable_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pct_studio: Mapped[float | None] = mapped_column(Float, nullable=True)
    pct_1bed: Mapped[float | None] = mapped_column(Float, nullable=True)
    pct_2bed: Mapped[float | None] = mapped_column(Float, nullable=True)
    pct_other_bed: Mapped[float | None] = mapped_column(Float, nullable=True)
    acres: Mapped[float | None] = mapped_column(Float, nullable=True)
    retail_sf: Mapped[int | None] = mapped_column(Integer, nullable=True)
    office_sf: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hotel_keys: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_sf: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parking_spaces: Mapped[int | None] = mapped_column(Integer, nullable=True)
    style: Mapped[str | None] = mapped_column(String(100), nullable=True)
    property_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    affordable_type: Mapped[str | None] = mapped_column(String(120), nullable=True)

    owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    true_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    architect: Mapped[str | None] = mapped_column(String(255), nullable=True)

    pipeline_status: Mapped[PipelineStatus] = mapped_column(
        PIPELINE_STATUS_ENUM,
        nullable=False,
        default=PipelineStatus.PROPOSED,
    )
    status_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status_confidence: Mapped[StatusConfidence] = mapped_column(
        STATUS_CONFIDENCE_ENUM,
        nullable=False,
        default=StatusConfidence.LOW,
    )
    confidence: Mapped[StatusConfidence] = mapped_column(
        STATUS_CONFIDENCE_ENUM,
        nullable=False,
        default=StatusConfidence.LOW,
        server_default=StatusConfidence.LOW.value,
    )
    confidence_reason: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    likelihood: Mapped[float | None] = mapped_column(Float, nullable=True)
    likelihood_breakdown: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    delivery_year_provenance: Mapped[str | None] = mapped_column(String(30), nullable=True)
    last_evidence_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status_source: Mapped[str | None] = mapped_column(String(120), nullable=True)
    date_delivery: Mapped[date | None] = mapped_column(Date, nullable=True)
    date_construction_start: Mapped[date | None] = mapped_column(Date, nullable=True)

    entitlement_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    appeal_status: Mapped[str | None] = mapped_column(String(120), nullable=True)
    ceqa_status: Mapped[str | None] = mapped_column(String(120), nullable=True)

    planner_1_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    planner_1_city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    planner_1_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    planner_1_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    planner_2_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    planner_2_city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    planner_2_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    planner_2_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)

    source_urls: Mapped[list[str]] = mapped_column(ARRAY(String()), default=list, nullable=False)

    last_editor: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_edit_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_reviewed_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_reviewed_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    inclusion_in_analysis: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=true(),
    )
    inclusion_in_exhibit: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=true(),
    )
    inclusion_note: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_by: Mapped[str | None] = mapped_column(String(120), nullable=True)

    market_ref: Mapped[Market | None] = relationship(back_populates="projects")
    jurisdiction_ref: Mapped[Jurisdiction | None] = relationship(back_populates="projects")
    status_history: Mapped[list["StatusHistory"]] = relationship(back_populates="project")
    identifiers: Mapped[list["ProjectIdentifier"]] = relationship(back_populates="project")
    outgoing_relationships: Mapped[list["ProjectRelationship"]] = relationship(
        back_populates="project",
        foreign_keys="ProjectRelationship.project_id",
    )
    incoming_relationships: Mapped[list["ProjectRelationship"]] = relationship(
        back_populates="related_project",
        foreign_keys="ProjectRelationship.related_project_id",
    )
    source_records: Mapped[list["ProjectSourceRecord"]] = relationship(back_populates="project")
    evidence_rows: Mapped[list["Evidence"]] = relationship(back_populates="project")
    review_items: Mapped[list["ReviewItem"]] = relationship(back_populates="project")
    change_log_entries: Mapped[list["ChangeLog"]] = relationship(back_populates="project")
    resolution_logs: Mapped[list["ResolutionLog"]] = relationship(back_populates="project")
    researcher_overrides: Mapped[list[ResearcherOverride]] = relationship(
        back_populates="project"
    )
    project_notes: Mapped[list["ProjectNote"]] = relationship(back_populates="project")


class ProjectNote(Base):
    __tablename__ = "project_notes"
    __table_args__ = (
        Index(
            "ix_project_notes_project_id_type_created_at",
            "project_id",
            "note_type",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    note_type: Mapped[str] = mapped_column(String(50), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_by_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    project: Mapped[Project] = relationship(back_populates="project_notes")


class StatusHistory(Base):
    __tablename__ = "status_history"
    __table_args__ = (
        Index("ix_status_history_project_id_status_date", "project_id", "status_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[PipelineStatus] = mapped_column(PIPELINE_STATUS_ENUM, nullable=False)
    status_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    source: Mapped[str] = mapped_column(String(120), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    project: Mapped[Project] = relationship(back_populates="status_history")


class ResearcherOverride(Base):
    __tablename__ = "researcher_overrides"
    __table_args__ = (
        Index(
            "ix_researcher_overrides_project_id_active",
            "project_id",
            postgresql_where=text("cleared_at IS NULL"),
        ),
        Index(
            "uq_researcher_overrides_active_field",
            "project_id",
            "field_name",
            unique=True,
            postgresql_where=text("cleared_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    field_name: Mapped[str] = mapped_column(String(120), nullable=False)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(
        JSONB,
        nullable=False,
    )
    set_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    set_by_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    set_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    reaffirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cleared_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    mode: Mapped[str | None] = mapped_column(String(50), nullable=True)
    baseline: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    project: Mapped[Project] = relationship(back_populates="researcher_overrides")


class ProjectIdentifier(Base):
    __tablename__ = "project_identifiers"
    __table_args__ = (
        UniqueConstraint("identifier_type", "value"),
        Index("ix_project_identifiers_project_id", "project_id"),
        Index("ix_project_identifiers_value", "value"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    identifier_type: Mapped[IdentifierType] = mapped_column(IDENTIFIER_TYPE_ENUM, nullable=False)
    value: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    project: Mapped[Project] = relationship(back_populates="identifiers")


class ProjectRelationship(Base):
    __tablename__ = "project_relationships"
    __table_args__ = (
        UniqueConstraint("project_id", "related_project_id", "relationship_type"),
        Index("ix_project_relationships_related_project_id", "related_project_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    related_project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    relationship_type: Mapped[RelationshipType] = mapped_column(
        RELATIONSHIP_TYPE_ENUM,
        nullable=False,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    project: Mapped[Project] = relationship(
        back_populates="outgoing_relationships",
        foreign_keys=[project_id],
    )
    related_project: Mapped[Project] = relationship(
        back_populates="incoming_relationships",
        foreign_keys=[related_project_id],
    )


class ProjectSourceRecord(Base):
    __tablename__ = "project_source_records"
    __table_args__ = (
        UniqueConstraint("source_name", "source_record_id"),
        Index("ix_project_source_records_project_id", "project_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_name: Mapped[str] = mapped_column(String(120), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_row_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    source_row_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_pulled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    mapped_fields: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    field_provenance: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    project: Mapped[Project] = relationship(back_populates="source_records")


class Evidence(Base):
    __tablename__ = "evidence"
    __table_args__ = (
        Index("ix_evidence_project_id", "project_id"),
        Index("ix_evidence_source_type", "source_type"),
        Index("ix_evidence_evidence_date", "evidence_date"),
        Index("ix_evidence_collected_at", "collected_at"),
        Index(
            "ix_evidence_active_project_resolution",
            "project_id",
            text("evidence_date DESC NULLS LAST"),
            text("collected_at DESC"),
            "source_tier",
            postgresql_where=text("superseded_at IS NULL"),
        ),
        Index(
            "uq_evidence_source_type_source_record_id_raw_data_hash",
            "source_type",
            "source_record_id",
            "raw_data_hash",
            unique=True,
            postgresql_where=text(
                "source_record_id IS NOT NULL AND raw_data_hash IS NOT NULL"
            ),
        ),
        Index(
            "ix_evidence_news_article_id_active",
            text("(raw_data ->> 'article_id')"),
            postgresql_where=text(
                "source_type = 'news_article' AND superseded_at IS NULL"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
    )
    source_type: Mapped[str] = mapped_column(String(120), nullable=False)
    source_tier: Mapped[int] = mapped_column(Integer, nullable=False)
    ingest_method: Mapped[str] = mapped_column(String(30), nullable=False)
    source_record_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    evidence_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    raw_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    raw_data_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extracted_fields: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    signal_flags: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    project: Mapped[Project | None] = relationship(back_populates="evidence_rows")
    news_project_references: Mapped[list["NewsProjectReference"]] = relationship(
        back_populates="matched_evidence"
    )


class DeveloperRegistry(Base, TimestampMixin):
    __tablename__ = "developer_registry"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    is_top_tier: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    aliases: Mapped[list["DeveloperAlias"]] = relationship(back_populates="developer")


class DeveloperAlias(Base):
    __tablename__ = "developer_alias"
    __table_args__ = (
        Index("ix_developer_alias_developer_id", "developer_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    developer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("developer_registry.id", ondelete="CASCADE"),
        nullable=False,
    )
    alias_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    developer: Mapped[DeveloperRegistry] = relationship(back_populates="aliases")


class SourceRegistration(Base, TimestampMixin):
    __tablename__ = "source_registrations"
    __table_args__ = (
        UniqueConstraint("jurisdiction_id", "source_name"),
        Index("ix_source_registrations_jurisdiction_id", "jurisdiction_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    jurisdiction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jurisdictions.id"),
        nullable=False,
    )
    source_name: Mapped[str] = mapped_column(Text, nullable=False)
    source_class: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=true(),
    )
    schedule_cron: Mapped[str | None] = mapped_column(Text, nullable=True)
    config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    jurisdiction: Mapped[Jurisdiction] = relationship(back_populates="source_registrations")


class SourceRun(Base):
    __tablename__ = "source_runs"
    __table_args__ = (
        Index("ix_source_runs_market_source_name", "market", "source_name"),
        Index("ix_source_runs_jurisdiction_id_source_name", "jurisdiction_id", "source_name"),
        Index("ix_source_runs_finished_at", "finished_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    market: Mapped[str] = mapped_column(String(100), nullable=False)
    jurisdiction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jurisdictions.id"),
        nullable=True,
    )
    source_name: Mapped[str] = mapped_column(String(120), nullable=False)
    collection_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="full")
    trigger_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="scheduled",
        server_default="scheduled",
    )
    initiated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    run_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    incremental_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    source_min_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    source_max_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    records_pulled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_matches: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updates_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_candidates: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_inserted: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rows_updated: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rows_unchanged: Mapped[int | None] = mapped_column(Integer, nullable=True)
    errors: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    jurisdiction: Mapped[Jurisdiction | None] = relationship(back_populates="source_runs")
    review_items: Mapped[list["ReviewItem"]] = relationship(back_populates="source_run")
    scrape_jobs: Mapped[list["ScrapeJob"]] = relationship(back_populates="source_run")
    costar_uploads: Mapped[list["CoStarUpload"]] = relationship(back_populates="source_run")


class ScrapeJob(Base):
    __tablename__ = "scrape_jobs"
    __table_args__ = (
        Index("ix_scrape_jobs_jurisdiction_id_status", "jurisdiction_id", "status"),
        Index(
            "ix_scrape_jobs_kind_status",
            "kind",
            "status",
            "queued_at",
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        Index(
            "ix_scrape_jobs_status_queued_at",
            "status",
            "queued_at",
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        Index(
            "ix_scrape_jobs_article_id_kind_status_queued_at",
            text("(target_payload ->> 'article_id')"),
            "kind",
            "status",
            text("queued_at DESC"),
            postgresql_where=text("target_payload ? 'article_id'"),
        ),
        Index(
            "uq_scrape_jobs_one_active_collector",
            "jurisdiction_id",
            "source_name",
            unique=True,
            postgresql_where=text("kind = 'collector_run' AND status IN ('queued', 'running')"),
        ),
        Index(
            "uq_scrape_jobs_one_active_news_scrape",
            "source_name",
            unique=True,
            postgresql_where=text("kind = 'news_scrape' AND status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    jurisdiction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jurisdictions.id"),
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
        default=ScrapeJobKind.COLLECTOR_RUN.value,
        server_default=ScrapeJobKind.COLLECTOR_RUN.value,
    )
    source_name: Mapped[str] = mapped_column(Text, nullable=False)
    target_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    trigger_type: Mapped[ScrapeTriggerType] = mapped_column(
        SCRAPE_TRIGGER_TYPE_ENUM,
        nullable=False,
        default=ScrapeTriggerType.USER_INITIATED,
    )
    initiated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    initiated_by_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ScrapeJobStatus] = mapped_column(
        SCRAPE_JOB_STATUS_ENUM,
        nullable=False,
        default=ScrapeJobStatus.QUEUED,
    )
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    jurisdiction: Mapped[Jurisdiction | None] = relationship(back_populates="scrape_jobs")
    source_run: Mapped[SourceRun | None] = relationship(back_populates="scrape_jobs")


class CoStarUpload(Base):
    __tablename__ = "costar_uploads"
    __table_args__ = (
        Index("ix_costar_uploads_jurisdiction_id", "jurisdiction_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    jurisdiction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jurisdictions.id"),
        nullable=False,
    )
    uploaded_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    uploaded_by_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[CoStarUploadStatus] = mapped_column(
        COSTAR_UPLOAD_STATUS_ENUM,
        nullable=False,
        default=CoStarUploadStatus.PROCESSING,
    )
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    jurisdiction: Mapped[Jurisdiction] = relationship(back_populates="costar_uploads")
    source_run: Mapped[SourceRun | None] = relationship(back_populates="costar_uploads")


class NewsSource(Base, TimestampMixin):
    __tablename__ = "news_sources"
    __table_args__ = (
        Index("ix_news_sources_active", "active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    collector_class: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=true(),
    )
    schedule_cron: Mapped[str | None] = mapped_column(Text, nullable=True)
    schedule_timezone: Mapped[str | None] = mapped_column(Text, nullable=True)
    config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    market_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("markets.id", ondelete="SET NULL"),
        nullable=True,
    )
    jurisdiction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jurisdictions.id", ondelete="SET NULL"),
        nullable=True,
    )

    market: Mapped[Market | None] = relationship(back_populates="news_sources")
    jurisdiction: Mapped[Jurisdiction | None] = relationship(back_populates="news_sources")
    articles: Mapped[list["NewsArticle"]] = relationship(back_populates="source")


class NewsArticle(Base, TimestampMixin):
    __tablename__ = "news_articles"
    __table_args__ = (
        Index("ix_news_articles_news_source_id", "news_source_id"),
        Index("ix_news_articles_published_at", text("published_at DESC NULLS LAST")),
        Index("ix_news_articles_fetch_status", "fetch_status"),
        Index("ix_news_articles_triage_status", "triage_status"),
        Index("ix_news_articles_body_text_hash", "body_text_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    news_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("news_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    url_canonical: Mapped[str] = mapped_column(Text, nullable=False)
    url_original: Mapped[str] = mapped_column(Text, nullable=False)
    url_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    fetch_status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=NewsFetchStatus.PENDING.value,
    )
    fetch_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    first_attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetch_error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_html_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_text_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    byline_author: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    publication_section: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text()), nullable=True)
    external_article_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str] = mapped_column(Text, nullable=False, default="en", server_default="en")
    paywall_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    structural_signals: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    structural_signals_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    triage_status: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=NewsTriageStatus.PENDING.value,
    )
    triage_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    triage_extraction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("news_extractions.id", ondelete="SET NULL"),
        nullable=True,
    )
    current_extraction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("news_extractions.id", ondelete="SET NULL"),
        nullable=True,
    )
    current_extraction_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    ingest_method: Mapped[str] = mapped_column(Text, nullable=False)
    ingested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    source: Mapped[NewsSource] = relationship(back_populates="articles")
    extractions: Mapped[list["NewsExtraction"]] = relationship(
        back_populates="article",
        foreign_keys="NewsExtraction.article_id",
    )
    project_references: Mapped[list["NewsProjectReference"]] = relationship(
        back_populates="article"
    )


class NewsExtraction(Base):
    __tablename__ = "news_extractions"
    __table_args__ = (
        Index("ix_news_extractions_article_id_created_at", "article_id", text("created_at DESC")),
        Index("ix_news_extractions_prompt_id_version", "prompt_id", "prompt_version"),
        Index("ix_news_extractions_pass_triggered_by", "pass", "triggered_by"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    article_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("news_articles.id", ondelete="CASCADE"),
        nullable=False,
    )
    pass_name: Mapped[str] = mapped_column(
        "pass",
        Text,
        nullable=False,
        default=NewsExtractionPass.TRIAGE.value,
    )
    triggered_by: Mapped[str] = mapped_column(Text, nullable=False)
    supersedes_extraction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("news_extractions.id", ondelete="SET NULL"),
        nullable=True,
    )
    prompt_id: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_hash: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    model_provider: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="anthropic",
        server_default="anthropic",
    )
    input_tokens_uncached: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens_cache_creation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens_cached: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 6), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    raw_response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    parse_status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=NewsExtractionParseStatus.OK.value,
    )
    parse_error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    diagnostic: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    triggered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    article: Mapped[NewsArticle] = relationship(
        back_populates="extractions",
        foreign_keys=[article_id],
    )
    project_references: Mapped[list["NewsProjectReference"]] = relationship(
        back_populates="extraction"
    )


class NewsProjectReference(Base, TimestampMixin):
    __tablename__ = "news_project_references"
    __table_args__ = (
        UniqueConstraint("extraction_id", "reference_index"),
        Index("ix_news_project_references_article_id", "article_id"),
        Index("ix_news_project_references_matched_project_id", "matched_project_id"),
        Index("ix_news_project_references_match_status", "match_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    extraction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("news_extractions.id", ondelete="CASCADE"),
        nullable=False,
    )
    article_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("news_articles.id", ondelete="CASCADE"),
        nullable=False,
    )
    reference_index: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_developer: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_unit_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    candidate_unit_affordable: Mapped[int | None] = mapped_column(Integer, nullable=True)
    candidate_unit_market_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    candidate_product_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_age_restriction: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_status_signal: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_delivery_year_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_delivery_year_normalized: Mapped[date | None] = mapped_column(Date, nullable=True)
    candidate_signal_flags: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    candidate_identifiers: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    candidate_neighborhood: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    candidate_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    candidate_confidence: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=NewsReferenceConfidence.LOW.value,
    )
    passage_excerpts: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    match_status: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=NewsMatchStatus.PENDING.value,
    )
    matched_project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
    )
    match_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    match_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    match_candidates: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    match_decision_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    matched_evidence_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evidence.id", ondelete="SET NULL"),
        nullable=True,
    )
    review_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("review_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    manual_relink_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    manual_relink_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    manual_relink_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    extraction: Mapped[NewsExtraction] = relationship(back_populates="project_references")
    article: Mapped[NewsArticle] = relationship(back_populates="project_references")
    matched_project: Mapped[Project | None] = relationship()
    matched_evidence: Mapped[Evidence | None] = relationship(
        back_populates="news_project_references"
    )
    review_item: Mapped[ReviewItem | None] = relationship()


class NewsExtractionCost(Base):
    __tablename__ = "news_extraction_costs"
    __table_args__ = (
        UniqueConstraint("cost_date", "pass", "model"),
        Index("ix_news_extraction_costs_cost_date", text("cost_date DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cost_date: Mapped[date] = mapped_column(Date, nullable=False)
    pass_name: Mapped[str] = mapped_column("pass", Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    input_tokens_uncached: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )
    input_tokens_cache_creation: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )
    input_tokens_cached: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )
    output_tokens: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )
    cost_usd: Mapped[float] = mapped_column(
        Numeric(12, 6),
        nullable=False,
        default=0,
        server_default="0",
    )


class NewsCostCap(Base, TimestampMixin):
    __tablename__ = "news_cost_caps"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    daily_warn_usd: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False)
    daily_hard_usd: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False)
    override_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    override_hard_usd: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
    override_set_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    override_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class NewsSignalFlag(Base):
    __tablename__ = "news_signal_flag_registry"
    __table_args__ = (
        Index(
            "ix_news_signal_flag_registry_active",
            "active",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    flag_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    display_label: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    example_phrases: Mapped[list[str] | None] = mapped_column(ARRAY(Text()), nullable=True)
    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=true(),
    )
    added_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ServiceCredential(Base):
    __tablename__ = "service_credentials"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    payload_kid: Mapped[str] = mapped_column(Text, nullable=False)
    set_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    set_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class SystemAlert(Base):
    __tablename__ = "system_alerts"
    __table_args__ = (
        Index(
            "uq_system_alerts_active_key_scope",
            "alert_key",
            text("COALESCE(scope::text, '{}')"),
            unique=True,
            postgresql_where=text("cleared_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    alert_key: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    raised_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    email_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cleared_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    cleared_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"

    worker_name: Mapped[str] = mapped_column(Text, primary_key=True)
    last_heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    process_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    active_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    active_job_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    heartbeat_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)


class ServiceCredentialValidation(Base):
    __tablename__ = "service_credential_validations"
    __table_args__ = (
        Index(
            "ix_service_credential_validations_credential_validated_at",
            "credential_slug",
            text("validated_at DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    credential_slug: Mapped[str] = mapped_column(Text, nullable=False)
    validated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    outcome_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    validated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    validated_by_process: Mapped[str | None] = mapped_column(Text, nullable=True)


class NewsAdminAction(Base):
    __tablename__ = "news_admin_actions"
    __table_args__ = (
        Index(
            "ix_news_admin_actions_kind_performed_at",
            "action_kind",
            text("performed_at DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    action_kind: Mapped[str] = mapped_column(Text, nullable=False)
    performed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    performed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    performed_by_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class ReviewItem(Base):
    __tablename__ = "review_items"
    __table_args__ = (
        Index("ix_review_items_status_priority", "status", "priority"),
        Index("ix_review_items_state_priority", "state", "priority"),
        Index("ix_review_items_project_id_state", "project_id", "state"),
        Index("ix_review_items_project_field_state", "project_id", "field_name", "state"),
        Index(
            "uq_review_items_active_project_field_type",
            "project_id",
            "field_name",
            "item_type",
            unique=True,
            postgresql_where=text(
                "state IN ('open', 'staged') "
                "AND field_name IS NOT NULL "
                "AND project_id IS NOT NULL"
            ),
        ),
        CheckConstraint(
            "state IN ('open', 'staged', 'committed', 'invalidated')",
            name="state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    item_type: Mapped[ReviewItemType] = mapped_column(REVIEW_ITEM_TYPE_ENUM, nullable=False)
    status: Mapped[ReviewItemStatus] = mapped_column(
        REVIEW_ITEM_STATUS_ENUM,
        nullable=False,
        default=ReviewItemStatus.OPEN,
    )
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    priority: Mapped[Priority] = mapped_column(PRIORITY_ENUM, nullable=False)
    match_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    field_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    winning_evidence_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evidence.id", ondelete="SET NULL"),
        nullable=True,
    )
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    contradicted_override_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("researcher_overrides.id", ondelete="SET NULL"),
        nullable=True,
    )
    contradiction_priority: Mapped[str | None] = mapped_column(String(20), nullable=True)
    assigned_to: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(50), nullable=True)

    project: Mapped[Project | None] = relationship(back_populates="review_items")
    source_run: Mapped[SourceRun | None] = relationship(back_populates="review_items")
    winning_evidence: Mapped[Evidence | None] = relationship()
    decisions: Mapped[list["ReviewDecision"]] = relationship(back_populates="review_item")
    change_log_entries: Mapped[list["ChangeLog"]] = relationship(back_populates="review_item")


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    __table_args__ = (
        Index(
            "ix_review_decisions_state_staged_by",
            "state",
            "staged_by",
            postgresql_where=text("state = 'staged'"),
        ),
        Index(
            "uq_review_decisions_one_staged_per_item",
            "review_item_id",
            unique=True,
            postgresql_where=text("state = 'staged'"),
        ),
        CheckConstraint(
            "state IN ('staged', 'committed')",
            name="state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    review_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("review_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    action: Mapped[ReviewDecisionAction] = mapped_column(
        REVIEW_DECISION_ACTION_ENUM,
        nullable=False,
    )
    actor: Mapped[str] = mapped_column(String(50), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    field_overrides: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="staged")
    decision_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    staged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    staged_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    staged_by_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    committed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    committed_by_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    decision_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    review_item: Mapped[ReviewItem] = relationship(back_populates="decisions")


class ChangeLog(Base):
    __tablename__ = "change_log"
    __table_args__ = (
        Index("ix_change_log_project_id_timestamp", "project_id", "timestamp"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    review_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("review_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    source: Mapped[str] = mapped_column(String(120), nullable=False)
    field: Mapped[str] = mapped_column(String(120), nullable=False)
    old_value: Mapped[dict | list | str | int | float | None] = mapped_column(JSONB, nullable=True)
    new_value: Mapped[dict | list | str | int | float | None] = mapped_column(JSONB, nullable=True)
    change_type: Mapped[ChangeType] = mapped_column(CHANGE_TYPE_ENUM, nullable=False)
    priority: Mapped[Priority] = mapped_column(PRIORITY_ENUM, nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reviewed_by_email: Mapped[str | None] = mapped_column(Text, nullable=True)

    project: Mapped[Project] = relationship(back_populates="change_log_entries")
    review_item: Mapped[ReviewItem | None] = relationship(back_populates="change_log_entries")


class ResolutionLog(Base):
    """Discrepancy-only audit rows for Phase 2 and ongoing resolution validation."""

    __tablename__ = "resolution_log"
    __table_args__ = (
        Index("ix_resolution_log_project_id_created_at", "project_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    field: Mapped[str] = mapped_column(String(120), nullable=False)
    current_value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    resolved_value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    evidence_ids: Mapped[list[uuid.UUID] | None] = mapped_column(
        ARRAY(UUID(as_uuid=True)),
        nullable=True,
    )
    rule_applied: Mapped[str | None] = mapped_column(String(120), nullable=True)
    confidence: Mapped[StatusConfidence | None] = mapped_column(
        STATUS_CONFIDENCE_ENUM,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    project: Mapped[Project] = relationship(back_populates="resolution_logs")


class DismissedRecord(Base):
    __tablename__ = "dismissed_records"
    __table_args__ = (
        UniqueConstraint("source", "source_record_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(120), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reason: Mapped[DismissReason] = mapped_column(DISMISS_REASON_ENUM, nullable=False)
    dismissed_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    dismissed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

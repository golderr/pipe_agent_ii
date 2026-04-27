from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from geoalchemy2 import Geography
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
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

    researcher_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    personal_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    change_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    researcher_override: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

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
            "uq_evidence_source_type_source_record_id_raw_data_hash",
            "source_type",
            "source_record_id",
            "raw_data_hash",
            unique=True,
            postgresql_where=text(
                "source_record_id IS NOT NULL AND raw_data_hash IS NOT NULL"
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
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    project: Mapped[Project | None] = relationship(back_populates="evidence_rows")


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


class ReviewItem(Base):
    __tablename__ = "review_items"
    __table_args__ = (
        Index("ix_review_items_status_priority", "status", "priority"),
        Index("ix_review_items_state_priority", "state", "priority"),
        Index("ix_review_items_project_id_state", "project_id", "state"),
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
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(50), nullable=True)

    project: Mapped[Project | None] = relationship(back_populates="review_items")
    source_run: Mapped[SourceRun | None] = relationship(back_populates="review_items")
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

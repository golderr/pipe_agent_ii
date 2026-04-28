import { createSupabaseServerClient } from "@/lib/supabase/server";
import type {
  EvidenceSummary,
  FieldClass,
  FieldProvenance,
  ProjectChangeLogRow,
  ProjectDetailData,
  ProjectEvidenceFilterOption,
  ProjectEvidenceRow,
  ProjectNoteHistoryRow,
  ProjectOverrideRow,
  ProjectResolutionRow,
  ProjectRelationshipRow,
  ProjectStatusHistoryRow,
  ProjectDetailSection,
  FieldEditConfig,
  ProjectField,
  SourceBadge
} from "@/lib/project-detail/types";

type SupabaseServerClient = Awaited<ReturnType<typeof createSupabaseServerClient>>;

type RawProject = Record<string, unknown> & {
  id: string;
  canonical_address: string;
  lat: number | null;
  lng: number | null;
  city: string;
  state: string;
  county: string;
  zip: string | null;
  market: string;
  jurisdiction_id: string | null;
  project_name: string | null;
  pipeline_status: string;
  confidence: string | null;
  status_confidence: string | null;
  last_evidence_date: string | null;
  inclusion_in_analysis: boolean;
  inclusion_in_exhibit: boolean;
  inclusion_note: string | null;
};

type RawJurisdiction = {
  id: string;
  name: string;
  display_name: string | null;
};

type RawIdentifier = {
  identifier_type: string;
  value: string;
  source: string | null;
  is_primary: boolean;
};

type RawEvidence = {
  id: string;
  source_type: string;
  source_tier: number;
  ingest_method: string;
  source_record_id: string | null;
  collected_at: string;
  evidence_date: string | null;
  raw_data: Record<string, unknown> | null;
  extracted_fields: Record<string, unknown> | null;
  signal_flags: Record<string, unknown> | null;
  notes: string | null;
};

type RawProjectSourceRecord = {
  source_name: string;
  source_record_id: string;
  source_url: string | null;
};

type RawFieldResolution = {
  field: string;
  current_value: unknown;
  resolved_value: unknown;
  evidence_ids: string[] | null;
  rule_applied: string | null;
  confidence: string | null;
  created_at: string;
};

type RawReviewItem = {
  item_type: string;
  status: string;
  state: string | null;
  priority: string;
  payload: Record<string, unknown> | null;
};

type RawRelationship = {
  id: string;
  relationship_type: string;
  related_project_id?: string;
  project_id?: string;
  notes: string | null;
};

type RawRelationshipProject = {
  id: string;
  project_name: string | null;
  canonical_address: string;
  city: string;
  state: string;
  zip: string | null;
  pipeline_status: string;
};

type RawStatusHistory = {
  status: string;
  status_date: string | null;
  source: string;
  notes: string | null;
};

type RawChangeLog = {
  id: string;
  timestamp: string;
  source: string;
  field: string;
  old_value: unknown;
  new_value: unknown;
  change_type: string;
  priority: string;
  reviewed_by: string | null;
  reviewed_by_user_id: string | null;
  reviewed_by_email: string | null;
  review_item_id: string | null;
};

type RawProjectNote = {
  id: string;
  note_type: string;
  body: string;
  created_by_user_id: string | null;
  created_by_label: string | null;
  created_at: string;
};

type RawResearcherOverride = {
  field_name: string;
  value: unknown;
  set_by_label: string | null;
  set_at: string | null;
  note: string | null;
  source_url: string | null;
  mode: string | null;
  baseline: Record<string, unknown> | null;
};

type ProjectDetailResult =
  | { data: ProjectDetailData; error: null; notFound?: false }
  | { data: null; error: string; notFound?: false }
  | { data: null; error: null; notFound: true };

type FieldDefinition = {
  key: string;
  label: string;
  className: FieldClass;
  note?: string;
  edit?: Omit<FieldEditConfig, "enabled" | "value" | "isOverridden">;
};

const PIPELINE_STATUS_OPTIONS = [
  "Conceptual",
  "Proposed",
  "Pending",
  "Approved",
  "Under Construction",
  "Pre-Leasing/Pre-Selling",
  "Complete",
  "Stalled",
  "Inactive",
  "Delete-Duplicate",
  "Delete-Outside Market Area",
  "Delete-Not Residential"
];

const PRODUCT_TYPE_OPTIONS = [
  "Apartment",
  "Condo",
  "Single-Family",
  "Townhome",
  "Micro/Co-Living",
  "Other",
  "Unknown"
];

const AGE_RESTRICTION_OPTIONS = [
  "Non Age-Restricted",
  "Senior",
  "Student",
  "Unknown"
];

const CORE_OVERRIDE_INFO = "Your edit stays current until reviewed. New differing evidence creates a review item.";
const DIRECT_FIELD_INFO = "Your edit is the source of truth for this field.";
const APPEND_NOTE_INFO = "Notes are append-only. New notes are added to the project history.";

const PROJECT_SELECT = [
  "id",
  "canonical_address",
  "raw_addresses",
  "lat",
  "lng",
  "geocode_confidence",
  "market",
  "city",
  "state",
  "county",
  "zip",
  "tcg_region",
  "jurisdiction",
  "jurisdiction_id",
  "costar_submarket",
  "zoning",
  "project_name",
  "previous_names",
  "developer",
  "applicant",
  "description",
  "rent_or_sale",
  "product_type",
  "age_restriction",
  "stories",
  "total_units",
  "market_rate_units",
  "affordable_units",
  "pct_studio",
  "pct_1bed",
  "pct_2bed",
  "pct_other_bed",
  "acres",
  "retail_sf",
  "office_sf",
  "hotel_keys",
  "total_sf",
  "parking_spaces",
  "style",
  "property_type",
  "affordable_type",
  "owner",
  "true_owner",
  "architect",
  "pipeline_status",
  "status_date",
  "status_confidence",
  "confidence",
  "confidence_reason",
  "likelihood",
  "likelihood_breakdown",
  "delivery_year_provenance",
  "last_evidence_date",
  "status_source",
  "date_delivery",
  "date_construction_start",
  "entitlement_type",
  "appeal_status",
  "ceqa_status",
  "planner_1_name",
  "planner_1_city",
  "planner_1_email",
  "planner_1_phone",
  "planner_2_name",
  "planner_2_city",
  "planner_2_email",
  "planner_2_phone",
  "source_urls",
  "last_editor",
  "last_edit_date",
  "last_reviewed_by",
  "last_reviewed_date",
  "inclusion_in_analysis",
  "inclusion_in_exhibit",
  "inclusion_note",
  "created_by",
  "created_at",
  "updated_at"
].join(", ");

const CORE_FIELDS: FieldDefinition[] = [
  {
    key: "pipeline_status",
    label: "Status",
    className: "evidence",
    edit: { mutation: "override", kind: "select", options: PIPELINE_STATUS_OPTIONS, info: CORE_OVERRIDE_INFO }
  },
  { key: "status_date", label: "Status date", className: "computed" },
  {
    key: "total_units",
    label: "Total units",
    className: "evidence",
    edit: { mutation: "override", kind: "number", options: null, info: CORE_OVERRIDE_INFO }
  },
  {
    key: "affordable_units",
    label: "Affordable units",
    className: "evidence",
    edit: { mutation: "override", kind: "number", options: null, info: CORE_OVERRIDE_INFO }
  },
  {
    key: "market_rate_units",
    label: "Market-rate units",
    className: "evidence",
    edit: { mutation: "override", kind: "number", options: null, info: CORE_OVERRIDE_INFO }
  },
  {
    key: "developer",
    label: "Developer",
    className: "evidence",
    edit: { mutation: "override", kind: "text", options: null, info: CORE_OVERRIDE_INFO }
  },
  {
    key: "product_type",
    label: "Product type",
    className: "evidence",
    edit: { mutation: "override", kind: "select", options: PRODUCT_TYPE_OPTIONS, info: CORE_OVERRIDE_INFO }
  },
  {
    key: "age_restriction",
    label: "Age restriction",
    className: "evidence",
    edit: { mutation: "override", kind: "select", options: AGE_RESTRICTION_OPTIONS, info: CORE_OVERRIDE_INFO }
  },
  {
    key: "date_delivery",
    label: "Delivery",
    className: "evidence",
    edit: { mutation: "override", kind: "date", options: null, info: CORE_OVERRIDE_INFO }
  }
];

const SOURCE_FACT_FIELDS: FieldDefinition[] = [
  { key: "rent_or_sale", label: "Rent / sale", className: "source", note: "Managed by source updates in MVP." },
  { key: "costar_submarket", label: "CoStar submarket", className: "source" },
  { key: "applicant", label: "Applicant", className: "source" },
  { key: "description", label: "Description", className: "source" },
  { key: "stories", label: "Stories", className: "source" },
  { key: "acres", label: "Acres", className: "source" },
  { key: "retail_sf", label: "Retail SF", className: "source" },
  { key: "office_sf", label: "Office SF", className: "source" },
  { key: "hotel_keys", label: "Hotel keys", className: "source" },
  { key: "total_sf", label: "Total SF", className: "source" },
  { key: "parking_spaces", label: "Parking", className: "source" },
  { key: "pct_studio", label: "Studio mix", className: "source" },
  { key: "pct_1bed", label: "1BR mix", className: "source" },
  { key: "pct_2bed", label: "2BR mix", className: "source" },
  { key: "pct_other_bed", label: "Other bed mix", className: "source" },
  { key: "property_type", label: "Property type", className: "source" },
  { key: "affordable_type", label: "Affordable type", className: "source" },
  { key: "owner", label: "Owner", className: "source" },
  { key: "true_owner", label: "True owner", className: "source" },
  { key: "architect", label: "Architect", className: "source" },
  { key: "zoning", label: "Zoning", className: "source" },
  { key: "date_construction_start", label: "Construction start", className: "source" },
  { key: "entitlement_type", label: "Entitlement", className: "source" },
  { key: "appeal_status", label: "Appeal", className: "source" },
  { key: "ceqa_status", label: "CEQA", className: "source" }
];

const IDENTITY_FIELDS: FieldDefinition[] = [
  { key: "project_name", label: "Project name", className: "researcher", edit: { mutation: "field", kind: "text", options: null, info: DIRECT_FIELD_INFO } },
  { key: "previous_names", label: "Previous names", className: "researcher", edit: { mutation: "field", kind: "textarea", options: null, info: DIRECT_FIELD_INFO } },
  { key: "canonical_address", label: "Canonical address", className: "computed" },
  { key: "raw_addresses", label: "Raw addresses", className: "researcher", edit: { mutation: "field", kind: "textarea", options: null, info: DIRECT_FIELD_INFO } },
  { key: "city", label: "City", className: "researcher", edit: { mutation: "field", kind: "text", options: null, info: DIRECT_FIELD_INFO } },
  { key: "state", label: "State", className: "researcher", edit: { mutation: "field", kind: "text", options: null, info: DIRECT_FIELD_INFO } },
  { key: "county", label: "County", className: "researcher", edit: { mutation: "field", kind: "text", options: null, info: DIRECT_FIELD_INFO } },
  { key: "zip", label: "ZIP", className: "researcher", edit: { mutation: "field", kind: "text", options: null, info: DIRECT_FIELD_INFO } },
  { key: "market", label: "Market", className: "relationship" },
  { key: "jurisdiction_display", label: "Jurisdiction", className: "relationship" },
  { key: "tcg_region", label: "Region", className: "researcher", edit: { mutation: "field", kind: "text", options: null, info: DIRECT_FIELD_INFO } },
  { key: "lat_lng", label: "Coordinates", className: "researcher" },
  { key: "source_urls", label: "Source URLs", className: "researcher", edit: { mutation: "field", kind: "textarea", options: null, info: DIRECT_FIELD_INFO } }
];

const NOTE_FIELDS: FieldDefinition[] = [
  { key: "researcher_notes", label: "Researcher notes", className: "researcher", edit: { mutation: "note", kind: "textarea", options: null, info: APPEND_NOTE_INFO } },
  { key: "personal_notes", label: "Personal notes", className: "researcher", edit: { mutation: "note", kind: "textarea", options: null, info: APPEND_NOTE_INFO } },
  { key: "change_notes", label: "Change notes", className: "researcher", edit: { mutation: "note", kind: "textarea", options: null, info: APPEND_NOTE_INFO } },
  { key: "last_reviewed", label: "Last reviewed", className: "computed" },
  { key: "last_edited", label: "Last edited", className: "computed" }
];

const COMPUTED_FIELDS: FieldDefinition[] = [
  { key: "confidence", label: "Confidence", className: "computed" },
  { key: "status_confidence", label: "Status confidence", className: "computed" },
  { key: "likelihood", label: "Likelihood", className: "computed" },
  { key: "delivery_year_provenance", label: "Delivery provenance", className: "computed" },
  { key: "last_evidence_date", label: "Last evidence", className: "computed" },
  { key: "status_source", label: "Status source", className: "computed" },
  { key: "geocode_confidence", label: "Geocode", className: "computed" },
  { key: "created_by", label: "Created by", className: "computed" },
  { key: "created_at", label: "Created", className: "computed" },
  { key: "updated_at", label: "Updated", className: "computed" }
];

const ALL_FIELD_DEFINITIONS = [
  ...CORE_FIELDS,
  ...SOURCE_FACT_FIELDS,
  ...IDENTITY_FIELDS,
  ...NOTE_FIELDS,
  ...COMPUTED_FIELDS
];
const FIELD_LABELS = new Map(ALL_FIELD_DEFINITIONS.map((field) => [field.key, field.label]));

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function coerceEvidenceValue(value: unknown) {
  if (isObject(value) && "value" in value) {
    return value.value;
  }
  return value;
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  if (typeof value === "boolean") {
    return value ? "Yes" : "No";
  }
  if (typeof value === "number") {
    return new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(value);
  }
  if (Array.isArray(value)) {
    return value.length ? value.map((item) => formatValue(item)).join(", ") : "-";
  }
  if (isObject(value)) {
    return Object.entries(value)
      .slice(0, 4)
      .map(([key, nested]) => `${key}: ${formatValue(nested)}`)
      .join("; ");
  }
  if (/^\d{4}-\d{2}-\d{2}/.test(String(value))) {
    return formatDate(String(value));
  }
  return String(value);
}

function formatDate(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric"
  }).format(new Date(value));
}

function comparableValue(value: unknown) {
  if (value === null || value === undefined || value === "") {
    return "";
  }

  if (Array.isArray(value)) {
    return JSON.stringify(value);
  }

  if (isObject(value)) {
    return JSON.stringify(
      Object.keys(value)
        .sort()
        .reduce<Record<string, unknown>>((normalized, key) => {
          normalized[key] = value[key];
          return normalized;
        }, {})
    );
  }

  return String(value);
}

function evidenceFields(evidence: RawEvidence) {
  return Object.keys(evidence.extracted_fields ?? {});
}

function evidenceTeaser(evidence: RawEvidence) {
  if (evidence.notes) {
    return evidence.notes;
  }

  const fields = Object.entries(evidence.extracted_fields ?? {})
    .filter(([, value]) => value !== null && value !== undefined && value !== "")
    .slice(0, 3)
    .map(([key, value]) => `${fieldLabel(key)}: ${formatValue(coerceEvidenceValue(value))}`);

  return fields.length > 0 ? fields.join(" | ") : null;
}

function humanizeToken(value: string) {
  return value
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
    .replace(/\bSf\b/g, "SF")
    .replace(/\bBr\b/g, "BR")
    .replace(/\bUrl\b/g, "URL")
    .replace(/\bId\b/g, "ID")
    .replace(/\bPcis\b/g, "PCIS")
    .replace(/\bLadbs\b/g, "LADBS")
    .replace(/\bLahd\b/g, "LAHD")
    .replace(/\bZimas\b/g, "ZIMAS")
    .replace(/\bCostar\b/g, "CoStar");
}

function fieldLabel(fieldKey: string) {
  return FIELD_LABELS.get(fieldKey) ?? humanizeToken(fieldKey);
}

function logicalSourceType(sourceName: string) {
  const logicalTypes: Record<string, string> = {
    ladbs_permits: "ladbs_permit",
    ladbs_permit_activity: "ladbs_permit",
    ladbs_inspections: "ladbs_inspection",
    ladbs_cofo: "ladbs_cofo",
    pipedream: "pipedream",
    costar: "costar"
  };

  return logicalTypes[sourceName] ?? sourceName;
}

function sourceTypeLabel(sourceType: string) {
  const labels: Record<string, string> = {
    ladbs_permit: "LADBS permit",
    ladbs_inspection: "LADBS inspection",
    ladbs_cofo: "LADBS CofO",
    zimas_pdis: "ZIMAS PDIS",
    zimas_arcgis: "ZIMAS ArcGIS",
    la_case_report: "LA case report",
    lahd_affordable: "LAHD affordable",
    costar: "CoStar",
    costar_export: "CoStar",
    pipedream: "Pipedream",
    pipedream_snapshot: "Pipedream",
    news_article: "News article",
    developer_website: "Developer website",
    researcher_override: "TCG override"
  };

  return labels[sourceType] ?? humanizeToken(sourceType);
}

function changeSourceLabel(source: string) {
  const labels: Record<string, string> = {
    inline_field: "Inline field",
    inline_override: "Inline override",
    manual_project: "Manual project",
    project_note: "Project note",
    project_relationship: "Project relationship",
    resolution_engine: "Resolution engine",
    contradiction_detection: "Contradiction detection"
  };

  return labels[source] ?? sourceTypeLabel(source);
}

function displayActor(
  email: string | null | undefined,
  label: string | null | undefined,
  userId: string | null | undefined
) {
  if (email) {
    return email;
  }
  if (label) {
    return label;
  }
  if (userId && userId.length > 8) {
    return `${userId.slice(0, 4)}...${userId.slice(-4)}`;
  }
  return userId ?? "System";
}

function uniqueOptions(options: ProjectEvidenceFilterOption[]) {
  const byValue = new Map<string, ProjectEvidenceFilterOption>();
  for (const option of options) {
    byValue.set(option.value, option);
  }

  return [...byValue.values()].sort((a, b) => a.label.localeCompare(b.label));
}

function toEvidenceSummary(evidence: RawEvidence): EvidenceSummary {
  return {
    id: evidence.id,
    sourceType: evidence.source_type,
    evidenceDate: evidence.evidence_date,
    collectedAt: evidence.collected_at,
    notes: evidence.notes,
    fields: evidenceFields(evidence),
    teaser: evidenceTeaser(evidence)
  };
}

function sourceRecordUrlForEvidence(
  evidence: RawEvidence,
  sourceRecordUrls: Map<string, string | null>
) {
  if (!evidence.source_record_id) {
    return null;
  }

  return sourceRecordUrls.get(`${evidence.source_type}:${evidence.source_record_id}`) ?? null;
}

function toProjectEvidenceRow(
  evidence: RawEvidence,
  sourceRecordUrls: Map<string, string | null>,
  linkedFields: ProjectEvidenceFilterOption[]
): ProjectEvidenceRow {
  const rawFields = evidenceFields(evidence);
  const displayFields = linkedFields.length
    ? linkedFields.map((field) => field.label)
    : rawFields.map((field) => fieldLabel(field));

  return {
    ...toEvidenceSummary(evidence),
    sourceTier: evidence.source_tier,
    ingestMethod: evidence.ingest_method,
    sourceRecordId: evidence.source_record_id,
    sourceUrl: sourceRecordUrlForEvidence(evidence, sourceRecordUrls),
    sourceBadge: sourceBadgeFromEvidence(evidence),
    sourceLabel: sourceTypeLabel(evidence.source_type),
    linkedFields,
    displayFields,
    rawData: evidence.raw_data,
    extractedFields: evidence.extracted_fields,
    signalFlags: evidence.signal_flags
  };
}

function sourceBadgeFromEvidence(evidence: RawEvidence | null): SourceBadge {
  if (!evidence) {
    return { label: "Unlinked", tone: "none", sourceType: null, date: null };
  }

  const sourceType = evidence.source_type;
  if (sourceType.includes("costar")) {
    return { label: "CoStar", tone: "costar", sourceType, date: evidence.evidence_date ?? evidence.collected_at };
  }
  if (sourceType.includes("pipedream")) {
    return { label: "Pipedream", tone: "pipedream", sourceType, date: evidence.evidence_date ?? evidence.collected_at };
  }
  if (sourceType.includes("news")) {
    return { label: "News", tone: "news", sourceType, date: evidence.evidence_date ?? evidence.collected_at };
  }
  if (sourceType.includes("developer_website")) {
    return { label: "Web", tone: "web", sourceType, date: evidence.evidence_date ?? evidence.collected_at };
  }
  if (
    sourceType.startsWith("ladbs") ||
    sourceType.startsWith("lahd") ||
    sourceType.startsWith("zimas") ||
    sourceType.startsWith("la_")
  ) {
    return { label: "Gov", tone: "gov", sourceType, date: evidence.evidence_date ?? evidence.collected_at };
  }

  return { label: "Source", tone: "source", sourceType, date: evidence.evidence_date ?? evidence.collected_at };
}

function systemBadge(): SourceBadge {
  return { label: "System", tone: "system", sourceType: null, date: null };
}

function userBadge(label = "TCG", date: string | null = null): SourceBadge {
  return { label, tone: "user", sourceType: "researcher_override", date };
}

function activeOverridePayload(
  overrides: Record<string, unknown> | null,
  fieldName: string
) {
  if (!isObject(overrides)) {
    return null;
  }
  const payload = overrides[fieldName];
  return isObject(payload) ? payload : payload === undefined ? null : { value: payload };
}

function overrideBadgeLabel(payload: Record<string, unknown>) {
  const setBy = typeof payload.set_by === "string" ? payload.set_by.trim() : "";
  if (!setBy) {
    return "TCG";
  }
  const emailLocalPart = setBy.split("@", 1)[0] ?? setBy;
  const initials = emailLocalPart
    .split(/[._\-\s]+/)
    .filter(Boolean)
    .map((part) => part[0]?.toUpperCase())
    .join("");
  return initials || "TCG";
}

function buildFieldProvenance(
  field: FieldDefinition,
  resolutionByField: Map<string, RawFieldResolution>,
  evidenceById: Map<string, RawEvidence>,
  overrides: Record<string, unknown> | null
): FieldProvenance {
  const activeOverride = activeOverridePayload(overrides, field.key);
  if (activeOverride) {
    return {
      sourceBadge: userBadge(
        overrideBadgeLabel(activeOverride),
        typeof activeOverride.set_at === "string" ? activeOverride.set_at : null
      ),
      rule: "researcher_override",
      confidence: "high",
      evidence: []
    };
  }

  const resolution = resolutionByField.get(field.key);
  const evidence = (resolution?.evidence_ids ?? [])
    .map((id) => evidenceById.get(id))
    .filter((row): row is RawEvidence => Boolean(row));

  if (evidence.length > 0) {
    return {
      sourceBadge: sourceBadgeFromEvidence(evidence[0]),
      rule: resolution?.rule_applied ?? null,
      confidence: resolution?.confidence ?? null,
      evidence: evidence.map(toEvidenceSummary)
    };
  }

  if (field.className === "source" || field.className === "evidence") {
    // Snapshot provenance is intentionally limited to evidence explicitly linked by
    // the latest resolution log row. Avoid guessing from source-native extracted keys.
    return {
      sourceBadge: sourceBadgeFromEvidence(null),
      rule: resolution?.rule_applied ?? null,
      confidence: resolution?.confidence ?? null,
      evidence: []
    };
  }

  return {
    sourceBadge: field.className === "computed" ? systemBadge() : userBadge(),
    rule: resolution?.rule_applied ?? null,
    confidence: resolution?.confidence ?? null,
    evidence: []
  };
}

function buildLinkedFieldsByEvidenceId(resolutions: RawFieldResolution[]) {
  const linkedFieldsByEvidenceId = new Map<string, ProjectEvidenceFilterOption[]>();

  for (const resolution of resolutions) {
    const evidenceIds = resolution.evidence_ids ?? [];
    if (evidenceIds.length === 0) {
      continue;
    }

    const field = {
      value: resolution.field,
      label: fieldLabel(resolution.field)
    };

    for (const evidenceId of evidenceIds) {
      const linkedFields = linkedFieldsByEvidenceId.get(evidenceId) ?? [];
      if (!linkedFields.some((existing) => existing.value === field.value)) {
        linkedFields.push(field);
      }
      linkedFieldsByEvidenceId.set(evidenceId, linkedFields);
    }
  }

  for (const [evidenceId, fields] of linkedFieldsByEvidenceId) {
    linkedFieldsByEvidenceId.set(
      evidenceId,
      fields.sort((a, b) => a.label.localeCompare(b.label))
    );
  }

  return linkedFieldsByEvidenceId;
}

function toResolutionRows(
  resolutions: RawFieldResolution[],
  evidenceById: Map<string, RawEvidence>
): ProjectResolutionRow[] {
  return resolutions
    .map((resolution) => {
      const evidenceIds = resolution.evidence_ids ?? [];
      const evidence = evidenceIds
        .map((id) => evidenceById.get(id))
        .filter((row): row is RawEvidence => Boolean(row))
        .map(toEvidenceSummary);

      return {
        field: resolution.field,
        fieldLabel: fieldLabel(resolution.field),
        currentValue: formatValue(resolution.current_value),
        resolvedValue: formatValue(resolution.resolved_value),
        changed: comparableValue(resolution.current_value) !== comparableValue(resolution.resolved_value),
        evidenceIds,
        evidence,
        rule: resolution.rule_applied,
        confidence: resolution.confidence,
        createdAt: resolution.created_at
      };
    })
    .sort((a, b) => Number(b.changed) - Number(a.changed) || a.fieldLabel.localeCompare(b.fieldLabel));
}

function toChangeRows(changeRows: RawChangeLog[]): ProjectChangeLogRow[] {
  return [...changeRows]
    .sort((a, b) => String(b.timestamp).localeCompare(String(a.timestamp)))
    .map((row) => ({
      id: row.id,
      timestamp: row.timestamp,
      source: row.source,
      sourceLabel: changeSourceLabel(row.source),
      field: row.field,
      fieldLabel: fieldLabel(row.field),
      oldValue: formatValue(row.old_value),
      newValue: formatValue(row.new_value),
      changeType: row.change_type,
      changeTypeLabel: humanizeToken(row.change_type),
      priority: row.priority,
      reviewedBy: row.reviewed_by,
      reviewedByUserId: row.reviewed_by_user_id,
      reviewedByEmail: row.reviewed_by_email,
      actorLabel: displayActor(row.reviewed_by_email, row.reviewed_by, row.reviewed_by_user_id),
      reviewItemId: row.review_item_id
    }));
}

function toNoteRows(noteRows: RawProjectNote[]): ProjectNoteHistoryRow[] {
  return [...noteRows]
    .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))
    .map((row) => ({
      id: row.id,
      noteType: row.note_type,
      noteTypeLabel: fieldLabel(row.note_type),
      body: row.body,
      createdByUserId: row.created_by_user_id,
      createdByLabel: row.created_by_label,
      actorLabel: displayActor(null, row.created_by_label, row.created_by_user_id),
      createdAt: row.created_at,
      source: "project_note",
      sourceLabel: changeSourceLabel("project_note")
    }));
}

function toStatusRows(statusRows: RawStatusHistory[]): ProjectStatusHistoryRow[] {
  return [...statusRows]
    .sort((a, b) => String(b.status_date ?? "").localeCompare(String(a.status_date ?? "")))
    .map((row) => ({
      status: row.status,
      statusDate: row.status_date,
      source: row.source,
      sourceLabel: changeSourceLabel(row.source),
      notes: row.notes
    }));
}

function toOverrideRows(overrides: Record<string, unknown> | null): ProjectOverrideRow[] {
  if (!isObject(overrides)) {
    return [];
  }

  return Object.entries(overrides)
    .map(([field, payload]) => {
      const normalized = isObject(payload) ? payload : { value: payload };
      const baseline = isObject(normalized.baseline) ? normalized.baseline : null;

      return {
        field,
        fieldLabel: fieldLabel(field),
        value: formatValue(normalized.value),
        mode: typeof normalized.mode === "string" ? normalized.mode : null,
        setBy: typeof normalized.set_by === "string" ? normalized.set_by : null,
        setAt: typeof normalized.set_at === "string" ? normalized.set_at : null,
        note: typeof normalized.note === "string" ? normalized.note : null,
        baseline,
        raw: normalized
      };
    })
    .sort((a, b) => a.fieldLabel.localeCompare(b.fieldLabel));
}

function toOverridePayloads(overrideRows: RawResearcherOverride[]): Record<string, unknown> {
  return Object.fromEntries(
    overrideRows.map((row) => [
      row.field_name,
      {
        value: row.value,
        set_by: row.set_by_label,
        set_at: row.set_at,
        note: row.note,
        source_url: row.source_url,
        mode: row.mode,
        baseline: row.baseline
      }
    ])
  );
}

function latestNoteValues(noteRows: ProjectNoteHistoryRow[]): Record<string, string> {
  const values: Record<string, string> = {};
  for (const row of noteRows) {
    if (values[row.noteType] === undefined) {
      values[row.noteType] = row.body;
    }
  }
  return values;
}

function extractReviewFields(reviewItems: RawReviewItem[]) {
  const fields = new Set<string>();

  for (const item of reviewItems) {
    const payload = item.payload ?? {};
    if (item.item_type === "status_change" || isObject(payload.status_suggestion)) {
      fields.add("pipeline_status");
      fields.add("status_date");
    }

    const changes = Array.isArray(payload.changes) ? payload.changes : [];
    for (const change of changes) {
      if (!isObject(change)) {
        continue;
      }
      const field = change.field ?? change.field_name;
      if (typeof field === "string") {
        fields.add(field);
      }
    }

    const reviewFlags = Array.isArray(payload.review_flags) ? payload.review_flags : [];
    for (const flag of reviewFlags) {
      if (!isObject(flag)) {
        continue;
      }
      const field = flag.field ?? flag.field_name;
      if (typeof field === "string") {
        fields.add(field);
      }
    }
  }

  return fields;
}

function valueForField(
  project: RawProject,
  key: string,
  jurisdictionName: string | null,
  notesByType: Record<string, string>
) {
  if (key === "jurisdiction_display") {
    return jurisdictionName;
  }
  if (key in notesByType) {
    return notesByType[key];
  }
  if (key === "lat_lng") {
    return project.lat !== null && project.lng !== null ? `${project.lat}, ${project.lng}` : null;
  }
  if (key === "last_reviewed") {
    return [project.last_reviewed_by, project.last_reviewed_date ? formatDate(String(project.last_reviewed_date)) : null]
      .filter(Boolean)
      .join(" | ");
  }
  if (key === "last_edited") {
    return [project.last_editor, project.last_edit_date ? formatDate(String(project.last_edit_date)) : null]
      .filter(Boolean)
      .join(" | ");
  }
  return project[key];
}

function editValueForField(
  project: RawProject,
  key: string,
  notesByType: Record<string, string>
) {
  const value = key in notesByType ? notesByType[key] : project[key];
  if (value === null || value === undefined) {
    return "";
  }
  if (Array.isArray(value)) {
    return value.join("\n");
  }
  if (typeof value === "boolean") {
    return value ? "Yes" : "No";
  }
  if (key === "date_delivery" && typeof value === "string") {
    return value.slice(0, 10);
  }
  return String(value);
}

function editConfigForField(
  definition: FieldDefinition,
  project: RawProject,
  overrides: Record<string, unknown> | null,
  notesByType: Record<string, string>
): FieldEditConfig | null {
  if (!definition.edit) {
    return null;
  }

  return {
    ...definition.edit,
    enabled: true,
    value: editValueForField(project, definition.key, notesByType),
    isOverridden: Boolean(activeOverridePayload(overrides, definition.key))
  };
}

function buildFields(
  definitions: FieldDefinition[],
  project: RawProject,
  jurisdictionName: string | null,
  pendingFields: Set<string>,
  resolutionByField: Map<string, RawFieldResolution>,
  evidenceById: Map<string, RawEvidence>,
  overrides: Record<string, unknown> | null,
  notesByType: Record<string, string>
): ProjectField[] {
  return definitions.map((definition) => ({
    key: definition.key,
    label: definition.label,
    value: formatValue(valueForField(project, definition.key, jurisdictionName, notesByType)),
    fieldClass: definition.className,
    state: pendingFields.has(definition.key) ? "review" : "default",
    note: definition.note ?? null,
    provenance: buildFieldProvenance(definition, resolutionByField, evidenceById, overrides),
    edit: editConfigForField(definition, project, overrides, notesByType)
  }));
}

function relationshipFields(
  identifiers: RawIdentifier[],
  relationshipRows: ProjectRelationshipRow[],
  statusHistory: RawStatusHistory[]
): ProjectField[] {
  const identifierSummary = identifiers.length
    ? identifiers.map((identifier) => `${identifier.identifier_type}: ${identifier.value}`).join(", ")
    : "-";
  const outgoingCount = relationshipRows.filter((row) => row.direction === "outgoing").length;
  const incomingCount = relationshipRows.length - outgoingCount;
  const relationshipSummary = relationshipRows.length
    ? `${outgoingCount} outgoing, ${incomingCount} incoming`
    : "-";
  const statusSummary = statusHistory.length
    ? statusHistory
        .slice(0, 4)
        .map((status) => `${status.status}${status.status_date ? ` (${formatDate(status.status_date)})` : ""}`)
        .join(", ")
    : "-";

  return [
    makeRelationshipField("identifiers", "Identifiers", identifierSummary),
    makeRelationshipField("relationships", "Project links", relationshipSummary),
    makeRelationshipField("status_history", "Status history", statusSummary)
  ];
}

function toRelationshipRows(
  outgoing: RawRelationship[],
  incoming: RawRelationship[],
  projectsById: Map<string, RawRelationshipProject>
): ProjectRelationshipRow[] {
  return [
    ...outgoing.flatMap((relationship) =>
      relationship.related_project_id
        ? [
            toRelationshipRow(
              relationship,
              "outgoing",
              relationship.related_project_id,
              projectsById
            )
          ]
        : []
    ),
    ...incoming.flatMap((relationship) =>
      relationship.project_id
        ? [toRelationshipRow(relationship, "incoming", relationship.project_id, projectsById)]
        : []
    )
  ].sort((a, b) => a.relationshipType.localeCompare(b.relationshipType));
}

function toRelationshipRow(
  relationship: RawRelationship,
  direction: "outgoing" | "incoming",
  relatedProjectId: string,
  projectsById: Map<string, RawRelationshipProject>
): ProjectRelationshipRow {
  const relatedProject = projectsById.get(relatedProjectId);
  return {
    id: relationship.id,
    direction,
    relationshipType: relationship.relationship_type,
    relatedProjectId,
    relatedProjectName:
      relatedProject?.project_name ?? relatedProject?.canonical_address ?? relatedProjectId,
    relatedProjectAddress: relatedProject?.canonical_address ?? "-",
    relatedProjectStatus: relatedProject?.pipeline_status ?? "-",
    relatedProjectLocation: relatedProject
      ? [relatedProject.city, relatedProject.state, relatedProject.zip].filter(Boolean).join(", ")
      : "-",
    notes: relationship.notes
  };
}

function makeRelationshipField(key: string, label: string, value: string): ProjectField {
  return {
    key,
    label,
    value,
    fieldClass: "relationship",
    state: "default",
    note: null,
    provenance: {
      sourceBadge: systemBadge(),
      rule: null,
      confidence: null,
      evidence: []
    },
    edit: null
  };
}

async function fetchProjectRows<T>(
  supabase: SupabaseServerClient,
  table: string,
  select: string,
  projectId: string
): Promise<{ data: T[]; error: string | null }> {
  const { data, error } = await supabase.from(table).select(select).eq("project_id", projectId);
  return { data: (data ?? []) as T[], error: error?.message ?? null };
}

async function fetchActiveOverrideRows(
  supabase: SupabaseServerClient,
  projectId: string
): Promise<{ data: RawResearcherOverride[]; error: string | null }> {
  const { data, error } = await supabase
    .from("researcher_overrides")
    .select("field_name, value, set_by_label, set_at, note, source_url, mode, baseline")
    .eq("project_id", projectId)
    .is("cleared_at", null)
    .order("field_name", { ascending: true });
  return { data: (data ?? []) as RawResearcherOverride[], error: error?.message ?? null };
}

export async function getProjectDetailData(projectId: string): Promise<ProjectDetailResult> {
  const supabase = await createSupabaseServerClient();

  const { data: project, error: projectError } = await supabase
    .from("projects")
    .select(PROJECT_SELECT)
    .eq("id", projectId)
    .maybeSingle();

  if (projectError) {
    return { data: null, error: projectError.message };
  }
  if (!project) {
    return { data: null, error: null, notFound: true };
  }

  const rawProject = project as unknown as RawProject;
  const [
    jurisdiction,
    identifiers,
    evidenceRows,
    sourceRecords,
    resolutions,
    reviewItems,
    relationships,
    incomingRelationships,
    statusHistory,
    changeRows,
    noteRows,
    overrideRowsResult
  ] = await Promise.all([
    rawProject.jurisdiction_id
      ? supabase
          .from("jurisdictions")
          .select("id, name, display_name")
          .eq("id", rawProject.jurisdiction_id)
          .maybeSingle()
      : Promise.resolve({ data: null, error: null }),
    fetchProjectRows<RawIdentifier>(
      supabase,
      "project_identifiers",
      "identifier_type, value, source, is_primary",
      projectId
    ),
    fetchProjectRows<RawEvidence>(
      supabase,
      "evidence",
      "id, source_type, source_tier, ingest_method, source_record_id, collected_at, evidence_date, raw_data, extracted_fields, signal_flags, notes",
      projectId
    ),
    fetchProjectRows<RawProjectSourceRecord>(
      supabase,
      "project_source_records",
      "source_name, source_record_id, source_url",
      projectId
    ),
    fetchProjectRows<RawFieldResolution>(
      supabase,
      "project_field_resolution",
      "field, current_value, resolved_value, evidence_ids, rule_applied, confidence, created_at",
      projectId
    ),
    fetchProjectRows<RawReviewItem>(supabase, "review_items", "item_type, status, state, priority, payload", projectId),
    fetchProjectRows<RawRelationship>(
      supabase,
      "project_relationships",
      "id, relationship_type, related_project_id, notes",
      projectId
    ),
    supabase
      .from("project_relationships")
      .select("id, relationship_type, project_id, notes")
      .eq("related_project_id", projectId),
    fetchProjectRows<RawStatusHistory>(
      supabase,
      "status_history",
      "status, status_date, source, notes",
      projectId
    ),
    fetchProjectRows<RawChangeLog>(
      supabase,
      "change_log",
      "id, timestamp, source, field, old_value, new_value, change_type, priority, reviewed_by, reviewed_by_user_id, reviewed_by_email, review_item_id",
      projectId
    ),
    fetchProjectRows<RawProjectNote>(
      supabase,
      "project_notes",
      "id, note_type, body, created_by_user_id, created_by_label, created_at",
      projectId
    ),
    fetchActiveOverrideRows(supabase, projectId)
  ]);

  const error =
    jurisdiction.error?.message ??
    identifiers.error ??
    evidenceRows.error ??
    sourceRecords.error ??
    resolutions.error ??
    reviewItems.error ??
    relationships.error ??
    incomingRelationships.error?.message ??
    statusHistory.error ??
    changeRows.error ??
    noteRows.error ??
    overrideRowsResult.error;

  if (error) {
    return { data: null, error };
  }

  const relationshipProjectIds = [
    ...relationships.data.map((relationship) => relationship.related_project_id),
    ...((incomingRelationships.data ?? []) as unknown as RawRelationship[]).map(
      (relationship) => relationship.project_id
    )
  ].filter((value): value is string => Boolean(value));
  const uniqueRelationshipProjectIds = [...new Set(relationshipProjectIds)];
  const relationshipProjects = uniqueRelationshipProjectIds.length
    ? await supabase
        .from("projects")
        .select("id, project_name, canonical_address, city, state, zip, pipeline_status")
        .in("id", uniqueRelationshipProjectIds)
    : { data: [], error: null };
  if (relationshipProjects.error) {
    return { data: null, error: relationshipProjects.error.message };
  }

  const sortedEvidenceRows = evidenceRows.data.sort((a, b) =>
    String(b.evidence_date ?? b.collected_at).localeCompare(String(a.evidence_date ?? a.collected_at))
  );
  const sourceRecordUrls = new Map<string, string | null>();
  for (const sourceRecord of sourceRecords.data) {
    sourceRecordUrls.set(
      `${logicalSourceType(sourceRecord.source_name)}:${sourceRecord.source_record_id}`,
      sourceRecord.source_url
    );
  }
  const linkedFieldsByEvidenceId = buildLinkedFieldsByEvidenceId(resolutions.data);
  const projectEvidenceRows = sortedEvidenceRows.map((evidence) =>
    toProjectEvidenceRow(evidence, sourceRecordUrls, linkedFieldsByEvidenceId.get(evidence.id) ?? [])
  );
  const evidenceById = new Map(sortedEvidenceRows.map((evidence) => [evidence.id, evidence]));
  const resolutionByField = new Map(resolutions.data.map((resolution) => [resolution.field, resolution]));
  const resolutionRows = toResolutionRows(resolutions.data, evidenceById);
  const projectChangeRows = toChangeRows(changeRows.data);
  const projectNoteRows = toNoteRows(noteRows.data);
  const projectStatusRows = toStatusRows(statusHistory.data);
  const overridePayloads = toOverridePayloads(overrideRowsResult.data);
  const overrideRows = toOverrideRows(overridePayloads);
  const notesByType = latestNoteValues(projectNoteRows);
  const relationshipRows = toRelationshipRows(
    relationships.data,
    (incomingRelationships.data ?? []) as unknown as RawRelationship[],
    new Map(
      ((relationshipProjects.data ?? []) as RawRelationshipProject[]).map((projectRow) => [
        projectRow.id,
        projectRow
      ])
    )
  );
  const activeReviewItems = reviewItems.data.filter((item) =>
    ["open", "staged"].includes(item.state ?? "open")
  );
  const pendingFields = extractReviewFields(activeReviewItems);
  const jurisdictionName = jurisdiction.data
    ? ((jurisdiction.data as RawJurisdiction).display_name ?? (jurisdiction.data as RawJurisdiction).name)
    : rawProject.jurisdiction_id;

  const sections: ProjectDetailSection[] = [
    {
      id: "core",
      title: "Core",
      description: "Evidence-derived fields owned by the resolution engine.",
      fields: buildFields(
        CORE_FIELDS,
        rawProject,
        jurisdictionName,
        pendingFields,
        resolutionByField,
        evidenceById,
        overridePayloads,
        notesByType
      )
    },
    {
      id: "source-facts",
      title: "Source Facts",
      description: "Source-populated direct fields are read-only for MVP.",
      fields: buildFields(
        SOURCE_FACT_FIELDS,
        rawProject,
        jurisdictionName,
        pendingFields,
        resolutionByField,
        evidenceById,
        overridePayloads,
        notesByType
      )
    },
    {
      id: "identity",
      title: "Identity",
      description: "Researcher-authored identity and location fields.",
      fields: buildFields(
        IDENTITY_FIELDS,
        rawProject,
        jurisdictionName,
        pendingFields,
        resolutionByField,
        evidenceById,
        overridePayloads,
        notesByType
      )
    },
    {
      id: "notes",
      title: "Notes",
      description: "Researcher notes and workflow flags.",
      fields: buildFields(
        NOTE_FIELDS,
        rawProject,
        jurisdictionName,
        pendingFields,
        resolutionByField,
        evidenceById,
        overridePayloads,
        notesByType
      )
    },
    {
      id: "relationships",
      title: "Relationships",
      description: "Identifiers, project relationships, and lifecycle history.",
      fields: relationshipFields(
        identifiers.data,
        relationshipRows,
        statusHistory.data
      )
    },
    {
      id: "computed",
      title: "Computed",
      description: "System-generated fields used for audit and filtering.",
      fields: buildFields(
        COMPUTED_FIELDS,
        rawProject,
        jurisdictionName,
        pendingFields,
        resolutionByField,
        evidenceById,
        overridePayloads,
        notesByType
      )
    }
  ].filter((section) => section.fields.length > 0);

  return {
    data: {
      project: {
        id: rawProject.id,
        name: rawProject.project_name ?? rawProject.canonical_address,
        canonicalAddress: rawProject.canonical_address,
        city: rawProject.city,
        state: rawProject.state,
        zip: rawProject.zip,
        market: rawProject.market,
        jurisdiction: jurisdictionName,
        status: rawProject.pipeline_status,
        confidence: rawProject.confidence ?? rawProject.status_confidence,
        lastEvidenceDate: rawProject.last_evidence_date,
        evidenceCount: sortedEvidenceRows.length,
        openReviewCount: activeReviewItems.length,
        inclusion: {
          inAnalysis: rawProject.inclusion_in_analysis,
          inExhibit: rawProject.inclusion_in_exhibit,
          note: rawProject.inclusion_note
        }
      },
      sections,
      evidenceRows: projectEvidenceRows,
      evidenceFilters: {
        fields: uniqueOptions(projectEvidenceRows.flatMap((row) => row.linkedFields)),
        sources: uniqueOptions(
          projectEvidenceRows.map((row) => ({
            value: row.sourceType,
            label: row.sourceLabel
          }))
        )
      },
      resolutionRows,
      changeRows: projectChangeRows,
      noteRows: projectNoteRows,
      statusRows: projectStatusRows,
      overrideRows,
      relationshipRows
    },
    error: null
  };
}

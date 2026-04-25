import { createSupabaseServerClient } from "@/lib/supabase/server";
import type {
  EvidenceSummary,
  FieldClass,
  FieldProvenance,
  ProjectDetailData,
  ProjectDetailSection,
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
  collected_at: string;
  evidence_date: string | null;
  extracted_fields: Record<string, unknown> | null;
  notes: string | null;
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
  priority: string;
  payload: Record<string, unknown> | null;
};

type RawRelationship = {
  relationship_type: string;
  related_project_id?: string;
  project_id?: string;
  notes: string | null;
};

type RawStatusHistory = {
  status: string;
  status_date: string | null;
  source: string;
  notes: string | null;
};

type ProjectDetailResult =
  | { data: ProjectDetailData; error: null; notFound?: false }
  | { data: null; error: string; notFound?: false }
  | { data: null; error: null; notFound: true };

type FieldDefinition = {
  key: string;
  label: string;
  className: FieldClass;
  aliases?: string[];
  note?: string;
};

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
  "researcher_notes",
  "personal_notes",
  "change_notes",
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
  { key: "pipeline_status", label: "Status", className: "evidence", aliases: ["status", "status_evidence_type"] },
  { key: "status_date", label: "Status date", className: "evidence", aliases: ["status_evidence_date"] },
  { key: "total_units", label: "Total units", className: "evidence", aliases: ["units", "tot_units"] },
  { key: "affordable_units", label: "Affordable units", className: "evidence" },
  { key: "market_rate_units", label: "Market-rate units", className: "evidence" },
  { key: "developer", label: "Developer", className: "evidence" },
  { key: "product_type", label: "Product type", className: "evidence" },
  { key: "age_restriction", label: "Age restriction", className: "evidence" },
  { key: "date_delivery", label: "Delivery", className: "evidence", aliases: ["delivery_date"] }
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
  { key: "project_name", label: "Project name", className: "researcher" },
  { key: "previous_names", label: "Previous names", className: "researcher" },
  { key: "canonical_address", label: "Canonical address", className: "computed" },
  { key: "raw_addresses", label: "Raw addresses", className: "researcher" },
  { key: "city", label: "City", className: "researcher" },
  { key: "state", label: "State", className: "researcher" },
  { key: "county", label: "County", className: "researcher" },
  { key: "zip", label: "ZIP", className: "researcher" },
  { key: "market", label: "Market", className: "relationship" },
  { key: "jurisdiction_display", label: "Jurisdiction", className: "relationship" },
  { key: "tcg_region", label: "Region", className: "researcher" },
  { key: "lat_lng", label: "Coordinates", className: "researcher" },
  { key: "source_urls", label: "Source URLs", className: "researcher" }
];

const NOTE_FIELDS: FieldDefinition[] = [
  { key: "researcher_notes", label: "Researcher notes", className: "researcher" },
  { key: "personal_notes", label: "Personal notes", className: "researcher" },
  { key: "change_notes", label: "Change notes", className: "researcher" },
  { key: "inclusion_in_analysis", label: "In analysis", className: "researcher" },
  { key: "inclusion_in_exhibit", label: "In exhibit", className: "researcher" },
  { key: "inclusion_note", label: "Inclusion note", className: "researcher" },
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
    .map(([key, value]) => `${key}: ${formatValue(coerceEvidenceValue(value))}`);

  return fields.length > 0 ? fields.join(" | ") : null;
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

function sourceBadgeFromEvidence(evidence: RawEvidence | null): SourceBadge {
  if (!evidence) {
    return { label: "-", tone: "none", sourceType: null, date: null };
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

function userBadge(): SourceBadge {
  return { label: "TCG", tone: "user", sourceType: null, date: null };
}

function evidenceContainsField(evidence: RawEvidence, field: FieldDefinition) {
  const keys = new Set([field.key, ...(field.aliases ?? [])]);
  return Object.keys(evidence.extracted_fields ?? {}).some((key) => keys.has(key));
}

function findLatestFieldEvidence(field: FieldDefinition, evidenceRows: RawEvidence[]) {
  return evidenceRows.find((evidence) => evidenceContainsField(evidence, field)) ?? null;
}

function buildFieldProvenance(
  field: FieldDefinition,
  resolutionByField: Map<string, RawFieldResolution>,
  evidenceById: Map<string, RawEvidence>,
  evidenceRows: RawEvidence[]
): FieldProvenance {
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
    const latestEvidence = findLatestFieldEvidence(field, evidenceRows);
    return {
      sourceBadge: sourceBadgeFromEvidence(latestEvidence),
      rule: resolution?.rule_applied ?? null,
      confidence: resolution?.confidence ?? null,
      evidence: latestEvidence ? [toEvidenceSummary(latestEvidence)] : []
    };
  }

  return {
    sourceBadge: field.className === "computed" ? systemBadge() : userBadge(),
    rule: resolution?.rule_applied ?? null,
    confidence: resolution?.confidence ?? null,
    evidence: []
  };
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

function valueForField(project: RawProject, key: string, jurisdictionName: string | null) {
  if (key === "jurisdiction_display") {
    return jurisdictionName;
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

function buildFields(
  definitions: FieldDefinition[],
  project: RawProject,
  jurisdictionName: string | null,
  pendingFields: Set<string>,
  resolutionByField: Map<string, RawFieldResolution>,
  evidenceById: Map<string, RawEvidence>,
  evidenceRows: RawEvidence[]
): ProjectField[] {
  return definitions.map((definition) => ({
    key: definition.key,
    label: definition.label,
    value: formatValue(valueForField(project, definition.key, jurisdictionName)),
    fieldClass: definition.className,
    state: pendingFields.has(definition.key) ? "review" : "default",
    note: definition.note ?? null,
    provenance: buildFieldProvenance(definition, resolutionByField, evidenceById, evidenceRows)
  }));
}

function relationshipFields(
  identifiers: RawIdentifier[],
  relationships: RawRelationship[],
  incomingRelationships: RawRelationship[],
  statusHistory: RawStatusHistory[]
): ProjectField[] {
  const identifierSummary = identifiers.length
    ? identifiers.map((identifier) => `${identifier.identifier_type}: ${identifier.value}`).join(", ")
    : "-";
  const relationshipSummary =
    relationships.length + incomingRelationships.length > 0
      ? `${relationships.length} outgoing, ${incomingRelationships.length} incoming`
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
    }
  };
}

function compactNonEmpty(section: ProjectDetailSection): ProjectDetailSection {
  return {
    ...section,
    fields: section.fields.filter((field) => field.value !== "-" || field.state !== "default")
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
    resolutions,
    reviewItems,
    relationships,
    incomingRelationships,
    statusHistory
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
      "id, source_type, collected_at, evidence_date, extracted_fields, notes",
      projectId
    ),
    fetchProjectRows<RawFieldResolution>(
      supabase,
      "project_field_resolution",
      "field, current_value, resolved_value, evidence_ids, rule_applied, confidence, created_at",
      projectId
    ),
    fetchProjectRows<RawReviewItem>(supabase, "review_items", "item_type, status, priority, payload", projectId),
    fetchProjectRows<RawRelationship>(
      supabase,
      "project_relationships",
      "relationship_type, related_project_id, notes",
      projectId
    ),
    supabase
      .from("project_relationships")
      .select("relationship_type, project_id, notes")
      .eq("related_project_id", projectId),
    fetchProjectRows<RawStatusHistory>(
      supabase,
      "status_history",
      "status, status_date, source, notes",
      projectId
    )
  ]);

  const error =
    jurisdiction.error?.message ??
    identifiers.error ??
    evidenceRows.error ??
    resolutions.error ??
    reviewItems.error ??
    relationships.error ??
    incomingRelationships.error?.message ??
    statusHistory.error;

  if (error) {
    return { data: null, error };
  }

  const sortedEvidenceRows = evidenceRows.data.sort((a, b) =>
    String(b.evidence_date ?? b.collected_at).localeCompare(String(a.evidence_date ?? a.collected_at))
  );
  const evidenceById = new Map(sortedEvidenceRows.map((evidence) => [evidence.id, evidence]));
  const resolutionByField = new Map(resolutions.data.map((resolution) => [resolution.field, resolution]));
  const openReviewItems = reviewItems.data.filter((item) => item.status === "open");
  const pendingFields = extractReviewFields(openReviewItems);
  const jurisdictionName = jurisdiction.data
    ? ((jurisdiction.data as RawJurisdiction).display_name ?? (jurisdiction.data as RawJurisdiction).name)
    : rawProject.jurisdiction_id;

  const sections: ProjectDetailSection[] = [
    compactNonEmpty({
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
        sortedEvidenceRows
      )
    }),
    compactNonEmpty({
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
        sortedEvidenceRows
      )
    }),
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
        sortedEvidenceRows
      )
    },
    compactNonEmpty({
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
        sortedEvidenceRows
      )
    }),
    {
      id: "relationships",
      title: "Relationships",
      description: "Identifiers, project relationships, and lifecycle history.",
      fields: relationshipFields(
        identifiers.data,
        relationships.data,
        (incomingRelationships.data ?? []) as unknown as RawRelationship[],
        statusHistory.data
      )
    },
    compactNonEmpty({
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
        sortedEvidenceRows
      )
    })
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
        openReviewCount: openReviewItems.length
      },
      sections
    },
    error: null
  };
}

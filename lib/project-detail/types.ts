export type FieldClass = "evidence" | "source" | "researcher" | "relationship" | "computed";
export type FieldState = "default" | "review";

export type SourceBadge = {
  label: string;
  tone: "gov" | "news" | "costar" | "pipedream" | "user" | "web" | "system" | "source" | "none";
  sourceType: string | null;
  date: string | null;
};

export type EvidenceSummary = {
  id: string;
  sourceType: string;
  evidenceDate: string | null;
  collectedAt: string;
  notes: string | null;
  fields: string[];
  teaser: string | null;
};

export type ProjectEvidenceFilterOption = {
  value: string;
  label: string;
};

export type ProjectEvidenceRow = EvidenceSummary & {
  sourceTier: number;
  ingestMethod: string;
  sourceRecordId: string | null;
  sourceUrl: string | null;
  sourceBadge: SourceBadge;
  sourceLabel: string;
  linkedFields: ProjectEvidenceFilterOption[];
  displayFields: string[];
  rawData: Record<string, unknown> | null;
  extractedFields: Record<string, unknown> | null;
  signalFlags: Record<string, unknown> | null;
};

export type ProjectEvidenceFilters = {
  fields: ProjectEvidenceFilterOption[];
  sources: ProjectEvidenceFilterOption[];
};

export type ProjectResolutionRow = {
  field: string;
  fieldLabel: string;
  currentValue: string;
  resolvedValue: string;
  changed: boolean;
  evidenceIds: string[];
  evidence: EvidenceSummary[];
  rule: string | null;
  confidence: string | null;
  createdAt: string;
};

export type ProjectChangeLogRow = {
  id: string;
  timestamp: string;
  source: string;
  field: string;
  fieldLabel: string;
  oldValue: string;
  newValue: string;
  changeType: string;
  priority: string;
  reviewedBy: string | null;
  reviewItemId: string | null;
};

export type ProjectStatusHistoryRow = {
  status: string;
  statusDate: string | null;
  source: string;
  notes: string | null;
};

export type ProjectOverrideRow = {
  field: string;
  fieldLabel: string;
  value: string;
  mode: string | null;
  setBy: string | null;
  setAt: string | null;
  note: string | null;
  baseline: Record<string, unknown> | null;
  raw: Record<string, unknown>;
};

export type FieldProvenance = {
  sourceBadge: SourceBadge;
  rule: string | null;
  confidence: string | null;
  evidence: EvidenceSummary[];
};

export type FieldEditConfig = {
  enabled: boolean;
  mutation: "override" | "field" | "note";
  kind: "text" | "textarea" | "number" | "date" | "select";
  value: string | null;
  options: string[] | null;
  isOverridden: boolean;
  info: string;
};

export type ProjectField = {
  key: string;
  label: string;
  value: string;
  fieldClass: FieldClass;
  state: FieldState;
  note: string | null;
  provenance: FieldProvenance;
  edit: FieldEditConfig | null;
};

export type ProjectDetailSection = {
  id: string;
  title: string;
  description: string;
  fields: ProjectField[];
};

export type ProjectDetailData = {
  project: {
    id: string;
    name: string;
    canonicalAddress: string;
    city: string;
    state: string;
    zip: string | null;
    market: string;
    jurisdiction: string | null;
    status: string;
    confidence: string | null;
    lastEvidenceDate: string | null;
    evidenceCount: number;
    openReviewCount: number;
  };
  sections: ProjectDetailSection[];
  evidenceRows: ProjectEvidenceRow[];
  evidenceFilters: ProjectEvidenceFilters;
  resolutionRows: ProjectResolutionRow[];
  changeRows: ProjectChangeLogRow[];
  statusRows: ProjectStatusHistoryRow[];
  overrideRows: ProjectOverrideRow[];
};

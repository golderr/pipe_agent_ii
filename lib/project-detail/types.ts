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

export type FieldProvenance = {
  sourceBadge: SourceBadge;
  rule: string | null;
  confidence: string | null;
  evidence: EvidenceSummary[];
};

export type ProjectField = {
  key: string;
  label: string;
  value: string;
  fieldClass: FieldClass;
  state: FieldState;
  note: string | null;
  provenance: FieldProvenance;
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
};

export type ReviewDecisionSummary = {
  decisionId: string;
  state: string;
  decisionType: string | null;
  stagedAt: string | null;
  stagedBy: string | null;
  stagedByEmail: string | null;
  committedAt: string | null;
  committedBy: string | null;
  committedByEmail: string | null;
  decisionValue: unknown;
  decisionNotes: string | null;
  sourceUrl: string | null;
};

export type ReviewQueueItem = {
  id: string;
  projectId: string | null;
  sourceRunId: string | null;
  itemType: string;
  status: string;
  state: string;
  priority: string;
  matchConfidence: number | null;
  fieldName: string | null;
  winningEvidenceId: string | null;
  payload: Record<string, unknown> | null;
  assignedTo: string | null;
  createdAt: string;
  resolvedAt: string | null;
  resolvedBy: string | null;
  activeDecision: ReviewDecisionSummary | null;
  evidenceSummaries: ReviewEvidenceSummary[];
};

export type ReviewEvidenceSummary = {
  evidenceId: string;
  stance: "supporting" | "against" | "silent";
  isWinning: boolean;
  sourceType: string;
  sourceTier: number;
  sourceRecordId: string | null;
  evidenceDate: string | null;
  collectedAt: string;
  summary: string;
  detail: string;
  /** Source-type-specific structured fields (permit_number/type/status for
   * ladbs_permit, costar_property_id/upload_date for costar, etc.). Populated
   * by the backend SnippetPayload.source_fields. May be empty for source
   * types that don't define structured fields yet. */
  sourceFields: Record<string, unknown>;
  externalLink: string | null;
  highlights: Array<Record<string, unknown>>;
  extractedValue: unknown;
};

export type ReviewProjectSummary = {
  id: string;
  projectName: string;
  canonicalAddress: string;
  city: string | null;
  state: string | null;
  zip: string | null;
  market: string;
  jurisdictionId: string | null;
  pipelineStatus: string;
  developer: string | null;
  totalUnits: number | null;
  dateDelivery: string | null;
};

export type ReviewSourceRunSummary = {
  id: string;
  sourceName: string;
  runTimestamp: string;
  finishedAt: string | null;
};

export type ReviewQueueData = {
  items: ReviewQueueItem[];
  reviewedItems: ReviewQueueItem[];
  projects: Record<string, ReviewProjectSummary>;
  sourceRuns: Record<string, ReviewSourceRunSummary>;
  generatedAt: string;
};

export type ReviewItemNavigation = {
  previousItemId: string | null;
  nextItemId: string | null;
  position: number | null;
  total: number;
  jurisdictionId: string | null;
};

export type ReviewProcessedChange = {
  id: string;
  timestamp: string;
  source: string;
  field: string;
  oldValue: unknown;
  newValue: unknown;
  changeType: string;
  priority: string;
  reviewedBy: string | null;
  reviewedByUserId: string | null;
  reviewedByEmail: string | null;
  reviewItemId: string | null;
};

export type ReviewItemDetailData = {
  item: ReviewQueueItem;
  project: ReviewProjectSummary | null;
  candidateProjects: ReviewProjectSummary[];
  sourceRun: ReviewSourceRunSummary | null;
  navigation: ReviewItemNavigation;
  processedChanges: ReviewProcessedChange[];
  generatedAt: string;
};

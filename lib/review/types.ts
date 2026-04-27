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
  payload: Record<string, unknown> | null;
  assignedTo: string | null;
  createdAt: string;
  resolvedAt: string | null;
  resolvedBy: string | null;
  activeDecision: ReviewDecisionSummary | null;
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
  projects: Record<string, ReviewProjectSummary>;
  sourceRuns: Record<string, ReviewSourceRunSummary>;
  generatedAt: string;
};

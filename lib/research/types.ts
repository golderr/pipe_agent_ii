export type ResearchScrapeJob = {
  id: string;
  jurisdictionId: string | null;
  kind: string;
  sourceName: string;
  targetPayload: unknown;
  triggerType: string;
  initiatedByUserId: string | null;
  initiatedByEmail: string | null;
  status: string;
  queuedAt: string;
  startedAt: string | null;
  completedAt: string | null;
  sourceRunId: string | null;
  errorText: string | null;
  progress: unknown;
};

export type ResearchArticle = {
  id: string;
  newsSourceId: string;
  sourceName: string;
  urlCanonical: string;
  urlOriginal: string;
  fetchStatus: string;
  fetchAttempts: number;
  fetchedAt: string | null;
  fetchErrorText: string | null;
  httpStatus: number | null;
  title: string | null;
  bylineAuthor: string | null;
  publishedAt: string | null;
  publicationSection: string | null;
  tags: string[] | null;
  externalArticleId: string | null;
  language: string;
  paywallState: string | null;
  bodyText: string | null;
  bodyTextHash: string | null;
  rawHtmlHash: string | null;
  structuralSignalsAt: string | null;
  triageStatus: string | null;
  triageAt: string | null;
  triageExtractionId: string | null;
  currentExtractionId: string | null;
  currentExtractionVersion: number;
  ingestMethod: string;
  ingestedByUserId: string | null;
  notes: string | null;
  createdAt: string;
  updatedAt: string;
};

export type ResearchExtraction = {
  id: string;
  passName: string;
  triggeredBy: string;
  promptId: string;
  promptVersion: string;
  model: string;
  parseStatus: string;
  createdAt: string;
};

export type ResearchReference = {
  id: string;
  extractionId: string;
  referenceIndex: number;
  candidateName: string | null;
  candidateAddress: string | null;
  candidateDeveloper: string | null;
  matchStatus: string;
  matchedProjectId: string | null;
};

export type ResearchArticleDetailData = {
  article: ResearchArticle;
  scrapeJobs: ResearchScrapeJob[];
  extractions: ResearchExtraction[];
  references: ResearchReference[];
};

export type CoverageSourceClass = "gov" | "news" | "costar" | "web" | "pipedream_seed" | string;

export type CoverageSourceSummary = {
  id: string;
  sourceName: string;
  sourceClass: CoverageSourceClass;
  active: boolean;
  scheduleCron: string | null;
  lastRunAt: string | null;
  lastRunScope: "jurisdiction" | "market_historical" | "unknown";
  lastRunHadError: boolean;
  recordsPulled: number | null;
  rowsInserted: number | null;
  rowsUpdated: number | null;
  rowsUnchanged: number | null;
};

export type CoverageNewsSourceHealth = {
  id: string;
  slug: string;
  name: string;
  active: boolean;
  paused: boolean;
  fetchPath: string;
  scheduleCron: string | null;
  scheduleTimezone: string | null;
  lastRunAt: string | null;
  lastRunFinishedAt: string | null;
  lastRunHadError: boolean;
  discoveredCount: number | null;
  fetchedCount: number | null;
  failedCount: number | null;
  blockLikeFailureCount: number | null;
  transientFailureCount: number | null;
  costCapSkippedCount: number | null;
  lastAlertKey: string | null;
  lastAlertSeverity: string | null;
  lastAlertMessage: string | null;
  lastAlertAt: string | null;
};

export type CoverageJurisdiction = {
  id: string;
  slug: string;
  name: string;
  displayName: string;
  state: string;
  entityType: string | null;
  market: {
    id: string;
    slug: string;
    name: string;
    displayName: string;
  } | null;
  projectCount: number;
  underConstructionCount: number;
  queue: {
    pending: number;
    staged: number;
    deferred: number;
    high: number;
    medium: number;
    low: number;
    severity: "high" | "medium" | "low" | "cleared";
  };
  lastIngested: {
    gov: string | null;
    news: string | null;
    costar: string | null;
  };
  lastReviewedAt: string | null;
  openOverrides: number;
  sources: CoverageSourceSummary[];
};

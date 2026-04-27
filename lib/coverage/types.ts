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

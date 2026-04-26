export type DashboardStatusBucket = {
  status: string;
  count: number;
};

export type DashboardPriorityCounts = {
  high: number;
  medium: number;
  low: number;
};

export type DashboardActivityLine = {
  id: string;
  label: string;
  detail: string;
  timestamp: string;
};

export type DashboardData = {
  marketLabel: string;
  generatedAt: string;
  needsAttention: {
    total: number;
    deferred: number;
    priorities: DashboardPriorityCounts;
    types: Array<{ type: string; count: number }>;
  };
  stalled: {
    total: number;
    cutoffDate: string;
    statusBuckets: DashboardStatusBucket[];
  };
  contradictions: {
    active: boolean;
    total: number;
    priorities: DashboardPriorityCounts;
  };
  pipelineByStatus: {
    total: number;
    buckets: DashboardStatusBucket[];
  };
  recentActivity: {
    sinceDate: string;
    evidenceRows: number;
    newsRows: number;
    sourceRowsChanged: number;
    sourceRuns: number;
    lines: DashboardActivityLine[];
  };
};

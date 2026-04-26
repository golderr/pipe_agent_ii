import { createSupabaseServerClient } from "@/lib/supabase/server";
import type { DashboardActivityLine, DashboardData, DashboardPriorityCounts, DashboardStatusBucket } from "@/lib/dashboard/types";

const PAGE_SIZE = 1000;
const STALL_STATUSES = new Set(["Approved", "Under Construction"]);
const STATUS_ORDER = [
  "Under Construction",
  "Approved",
  "Pending",
  "Proposed",
  "Conceptual",
  "Complete",
  "Stalled",
  "Inactive"
];

type SupabaseServerClient = Awaited<ReturnType<typeof createSupabaseServerClient>>;

type RawProject = {
  market: string;
  pipeline_status: string;
  last_evidence_date: string | null;
};

type RawReviewItem = {
  item_type: string;
  status: string;
  priority: string;
};

type RawEvidenceActivity = {
  id: string;
  source_type: string;
  collected_at: string;
};

type RawSourceRunActivity = {
  id: string;
  source_name: string;
  run_timestamp: string | null;
  finished_at: string | null;
  rows_inserted: number | null;
  rows_updated: number | null;
};

type RawMarket = {
  name: string;
  display_name: string | null;
};

type DashboardDataResult = { data: DashboardData; error: null } | { data: null; error: string };

async function fetchAllRows<T>(
  supabase: SupabaseServerClient,
  table: string,
  select: string
): Promise<{ rows: T[]; error: string | null }> {
  const rows: T[] = [];

  for (let from = 0; ; from += PAGE_SIZE) {
    const to = from + PAGE_SIZE - 1;
    const { data, error } = await supabase.from(table).select(select).range(from, to);

    if (error) {
      return { rows, error: error.message };
    }

    const page = (data ?? []) as T[];
    rows.push(...page);

    if (page.length < PAGE_SIZE) {
      return { rows, error: null };
    }
  }
}

async function fetchRecentEvidence(
  supabase: SupabaseServerClient,
  sinceIso: string
): Promise<{ rows: RawEvidenceActivity[]; error: string | null }> {
  const rows: RawEvidenceActivity[] = [];

  for (let from = 0; ; from += PAGE_SIZE) {
    const to = from + PAGE_SIZE - 1;
    const { data, error } = await supabase
      .from("evidence")
      .select("id, source_type, collected_at")
      .gte("collected_at", sinceIso)
      .order("collected_at", { ascending: false })
      .range(from, to);

    if (error) {
      return { rows, error: error.message };
    }

    const page = (data ?? []) as RawEvidenceActivity[];
    rows.push(...page);

    if (page.length < PAGE_SIZE) {
      return { rows, error: null };
    }
  }
}

async function fetchRecentSourceRuns(
  supabase: SupabaseServerClient,
  sinceIso: string
): Promise<{ rows: RawSourceRunActivity[]; error: string | null }> {
  const rows: RawSourceRunActivity[] = [];

  for (let from = 0; ; from += PAGE_SIZE) {
    const to = from + PAGE_SIZE - 1;
    const { data, error } = await supabase
      .from("source_runs")
      .select("id, source_name, run_timestamp, finished_at, rows_inserted, rows_updated")
      .or(`finished_at.gte.${sinceIso},run_timestamp.gte.${sinceIso}`)
      .order("run_timestamp", { ascending: false })
      .range(from, to);

    if (error) {
      return { rows, error: error.message };
    }

    const page = (data ?? []) as RawSourceRunActivity[];
    rows.push(...page);

    if (page.length < PAGE_SIZE) {
      return { rows, error: null };
    }
  }
}

function addDays(date: Date, days: number) {
  const copy = new Date(date);
  copy.setUTCDate(copy.getUTCDate() + days);
  return copy;
}

function addMonths(date: Date, months: number) {
  const copy = new Date(date);
  copy.setUTCMonth(copy.getUTCMonth() + months);
  return copy;
}

function isoDate(date: Date) {
  return date.toISOString().slice(0, 10);
}

function priorityCounts(items: RawReviewItem[]): DashboardPriorityCounts {
  return {
    high: items.filter((item) => item.priority === "high").length,
    medium: items.filter((item) => item.priority === "medium").length,
    low: items.filter((item) => item.priority === "low").length
  };
}

function groupCounts(values: string[]) {
  const counts = new Map<string, number>();
  for (const value of values) {
    counts.set(value, (counts.get(value) ?? 0) + 1);
  }
  return counts;
}

function statusSort(a: string, b: string) {
  const indexA = STATUS_ORDER.indexOf(a);
  const indexB = STATUS_ORDER.indexOf(b);
  if (indexA === -1 && indexB === -1) {
    return a.localeCompare(b);
  }
  if (indexA === -1) {
    return 1;
  }
  if (indexB === -1) {
    return -1;
  }
  return indexA - indexB;
}

function statusBuckets(projects: RawProject[]): DashboardStatusBucket[] {
  return [...groupCounts(projects.map((project) => project.pipeline_status)).entries()]
    .sort(([a], [b]) => statusSort(a, b))
    .map(([status, count]) => ({
      status,
      count
    }));
}

function topReviewItemTypes(items: RawReviewItem[]) {
  return [...groupCounts(items.map((item) => item.item_type)).entries()]
    .sort(([, a], [, b]) => b - a)
    .slice(0, 4)
    .map(([type, count]) => ({ type, count }));
}

function marketLabel(markets: RawMarket[], projects: RawProject[]) {
  const firstMarket = markets[0];
  if (firstMarket) {
    // Phase B has one active market; future market scoping should come from user preferences.
    return firstMarket.display_name ?? firstMarket.name;
  }

  const firstProjectMarket = projects[0]?.market;
  if (!firstProjectMarket) {
    return "Current market";
  }

  return firstProjectMarket
    .split("_")
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function sourceLooksLikeNews(sourceType: string) {
  const value = sourceType.toLowerCase();
  return value.includes("news") || value.includes("article") || value.includes("bizjournals");
}

function activityTimestamp(run: RawSourceRunActivity) {
  return run.finished_at ?? run.run_timestamp ?? "";
}

function sourceRowsChanged(sourceRuns: RawSourceRunActivity[]) {
  return sourceRuns.reduce((sum, row) => sum + (row.rows_inserted ?? 0) + (row.rows_updated ?? 0), 0);
}

function recentActivityLines(
  evidenceRows: RawEvidenceActivity[],
  sourceRuns: RawSourceRunActivity[]
): DashboardActivityLine[] {
  const evidenceLines = evidenceRows.slice(0, 5).map((row) => ({
    id: `evidence-${row.id}`,
    label: row.source_type,
    detail: "Evidence row ingested",
    timestamp: row.collected_at
  }));
  const runLines = sourceRuns.slice(0, 5).map((row) => {
    const changed = (row.rows_inserted ?? 0) + (row.rows_updated ?? 0);
    return {
      id: `source-run-${row.id}`,
      label: row.source_name,
      detail: changed > 0 ? `${changed.toLocaleString()} changed rows` : "Source run completed",
      timestamp: activityTimestamp(row)
    };
  });

  return [...evidenceLines, ...runLines]
    .filter((line) => line.timestamp)
    .sort((a, b) => b.timestamp.localeCompare(a.timestamp))
    .slice(0, 5);
}

export async function getDashboardData(): Promise<DashboardDataResult> {
  const supabase = await createSupabaseServerClient();
  const now = new Date();
  const recentSince = addDays(now, -7).toISOString();
  const stalledCutoff = isoDate(addMonths(now, -12));

  const [projects, reviewItems, recentEvidence, recentSourceRuns, markets] = await Promise.all([
    fetchAllRows<RawProject>(
      supabase,
      "projects",
      "market, pipeline_status, last_evidence_date"
    ),
    fetchAllRows<RawReviewItem>(supabase, "review_items", "item_type, status, priority"),
    fetchRecentEvidence(supabase, recentSince),
    fetchRecentSourceRuns(supabase, recentSince),
    fetchAllRows<RawMarket>(supabase, "markets", "name, display_name")
  ]);

  const error = projects.error ?? reviewItems.error ?? recentEvidence.error ?? recentSourceRuns.error ?? markets.error;
  if (error) {
    return { data: null, error };
  }

  const openItems = reviewItems.rows.filter((item) => item.status === "open");
  const deferredItems = reviewItems.rows.filter((item) => item.status === "deferred");
  const contradictionTypeSeen = reviewItems.rows.some((item) => item.item_type.includes("contradiction"));
  const contradictionItems = openItems.filter((item) => item.item_type.includes("contradiction"));
  const stalledProjects = projects.rows.filter((project) => {
    if (!STALL_STATUSES.has(project.pipeline_status)) {
      return false;
    }
    const lastEvidenceDate = project.last_evidence_date;
    if (!lastEvidenceDate) {
      return false;
    }
    return lastEvidenceDate <= stalledCutoff;
  });

  return {
    data: {
      marketLabel: marketLabel(markets.rows, projects.rows),
      generatedAt: now.toISOString(),
      needsAttention: {
        total: openItems.length,
        deferred: deferredItems.length,
        priorities: priorityCounts(openItems),
        types: topReviewItemTypes(openItems)
      },
      stalled: {
        total: stalledProjects.length,
        cutoffDate: stalledCutoff,
        statusBuckets: statusBuckets(stalledProjects)
      },
      contradictions: {
        active: contradictionTypeSeen,
        total: contradictionItems.length,
        priorities: priorityCounts(contradictionItems)
      },
      pipelineByStatus: {
        total: projects.rows.length,
        buckets: statusBuckets(projects.rows)
      },
      recentActivity: {
        sinceDate: isoDate(new Date(recentSince)),
        evidenceRows: recentEvidence.rows.length,
        newsRows: recentEvidence.rows.filter((row) => sourceLooksLikeNews(row.source_type)).length,
        sourceRowsChanged: sourceRowsChanged(recentSourceRuns.rows),
        sourceRuns: recentSourceRuns.rows.length,
        lines: recentActivityLines(recentEvidence.rows, recentSourceRuns.rows)
      }
    },
    error: null
  };
}

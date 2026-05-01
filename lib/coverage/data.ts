import { createSupabaseServerClient } from "@/lib/supabase/server";
import type {
  CoverageJurisdiction,
  CoverageNewsSourceHealth,
  CoverageSourceSummary
} from "@/lib/coverage/types";

const PAGE_SIZE = 1000;

type SupabaseServerClient = Awaited<ReturnType<typeof createSupabaseServerClient>>;

type RawMarket = {
  id: string;
  slug: string;
  name: string;
  display_name: string | null;
};

type RawJurisdiction = {
  id: string;
  slug: string;
  name: string;
  display_name: string | null;
  state: string;
  entity_type: string | null;
  markets: RawMarket | RawMarket[] | null;
};

type RawProject = {
  id: string;
  jurisdiction_id: string | null;
  pipeline_status: string | null;
  last_reviewed_date: string | null;
};

type RawResearcherOverride = {
  project_id: string;
  cleared_at: string | null;
};

type RawReviewItem = {
  id: string;
  project_id: string | null;
  status: string | null;
  state: string | null;
  priority: string | null;
};

type RawSourceRegistration = {
  id: string;
  jurisdiction_id: string;
  source_name: string;
  source_class: string;
  active: boolean;
  schedule_cron: string | null;
};

type RawSourceRun = {
  id: string;
  market: string;
  jurisdiction_id: string | null;
  source_name: string;
  run_timestamp: string | null;
  finished_at: string | null;
  records_pulled: number | null;
  rows_inserted: number | null;
  rows_updated: number | null;
  rows_unchanged: number | null;
  errors: string | null;
  error_text: string | null;
};

type RawNewsSource = {
  id: string;
  slug: string;
  name: string;
  active: boolean;
  schedule_cron: string | null;
  schedule_timezone: string | null;
  config: Record<string, unknown> | null;
};

type RawSystemAlert = {
  alert_key: string;
  severity: string;
  scope: Record<string, unknown> | null;
  message: string;
  raised_at: string | null;
  last_seen_at: string | null;
  cleared_at: string | null;
};

type CoverageDataResult =
  | { data: CoverageJurisdiction[]; newsSources: CoverageNewsSourceHealth[]; error: null }
  | { data: CoverageJurisdiction[]; newsSources: CoverageNewsSourceHealth[]; error: string };

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

function singleMarket(markets: RawJurisdiction["markets"]) {
  return Array.isArray(markets) ? (markets[0] ?? null) : markets;
}

function maxIsoDate(values: Array<string | null | undefined>) {
  const dated = values.filter(Boolean) as string[];
  if (dated.length === 0) {
    return null;
  }

  return dated.reduce((latest, value) => (new Date(value) > new Date(latest) ? value : latest));
}

function runTimestamp(run: RawSourceRun) {
  return run.finished_at ?? run.run_timestamp;
}

function isRunNewer(a: RawSourceRun, b: RawSourceRun | null) {
  if (!b) {
    return true;
  }

  const aDate = runTimestamp(a);
  const bDate = runTimestamp(b);
  if (!aDate) {
    return false;
  }
  if (!bDate) {
    return true;
  }

  return new Date(aDate) > new Date(bDate);
}

function latestRunForSource(
  runs: RawSourceRun[],
  jurisdictionId: string,
  marketSlug: string | null,
  sourceName: string
) {
  let latest: RawSourceRun | null = null;

  for (const run of runs) {
    const matchesSource = run.source_name === sourceName;
    const matchesJurisdiction = run.jurisdiction_id === jurisdictionId;
    const matchesHistoricalMarket =
      run.jurisdiction_id === null && Boolean(marketSlug) && run.market === marketSlug;

    if (matchesSource && (matchesJurisdiction || matchesHistoricalMarket) && isRunNewer(run, latest)) {
      latest = run;
    }
  }

  return latest;
}

function latestRunForNewsSource(runs: RawSourceRun[], sourceName: string) {
  let latest: RawSourceRun | null = null;

  for (const run of runs) {
    if (run.source_name === sourceName && isRunNewer(run, latest)) {
      latest = run;
    }
  }

  return latest;
}

function latestAlertForNewsSource(alerts: RawSystemAlert[], sourceName: string) {
  let latest: RawSystemAlert | null = null;

  for (const alert of alerts) {
    if (alert.cleared_at !== null || alert.scope?.source_name !== sourceName) {
      continue;
    }
    const alertDate = alert.last_seen_at ?? alert.raised_at;
    const latestDate = latest?.last_seen_at ?? latest?.raised_at;
    if (!latest || (alertDate && (!latestDate || new Date(alertDate) > new Date(latestDate)))) {
      latest = alert;
    }
  }

  return latest;
}

function newsSourceHealthSummary(
  source: RawNewsSource,
  runs: RawSourceRun[],
  alerts: RawSystemAlert[]
): CoverageNewsSourceHealth {
  const latest = latestRunForNewsSource(runs, source.slug);
  const latestAlert = latestAlertForNewsSource(alerts, source.slug);
  const recordsPulled = latest?.records_pulled ?? null;
  const fetched = latest?.rows_updated ?? null;
  const existing = latest?.rows_unchanged ?? null;
  const failed =
    recordsPulled !== null && fetched !== null
      ? Math.max(0, recordsPulled - fetched - (existing ?? 0))
      : null;

  return {
    id: source.id,
    slug: source.slug,
    name: source.name,
    active: source.active,
    paused: !source.active,
    fetchPath: typeof source.config?.fetch_path === "string" ? source.config.fetch_path : "polite",
    scheduleCron: source.schedule_cron,
    scheduleTimezone: source.schedule_timezone,
    lastRunAt: latest ? runTimestamp(latest) : null,
    lastRunFinishedAt: latest?.finished_at ?? null,
    lastRunHadError: Boolean(latest?.errors ?? latest?.error_text),
    discoveredCount: recordsPulled,
    fetchedCount: fetched,
    failedCount: failed,
    lastAlertKey: latestAlert?.alert_key ?? null,
    lastAlertSeverity: latestAlert?.severity ?? null,
    lastAlertMessage: latestAlert?.message ?? null,
    lastAlertAt: latestAlert ? (latestAlert.last_seen_at ?? latestAlert.raised_at) : null
  };
}

function sourceSummary(
  registration: RawSourceRegistration,
  runs: RawSourceRun[],
  jurisdictionId: string,
  marketSlug: string | null
): CoverageSourceSummary {
  const latest = latestRunForSource(runs, jurisdictionId, marketSlug, registration.source_name);
  const latestScope =
    latest?.jurisdiction_id === jurisdictionId
      ? "jurisdiction"
      : latest?.jurisdiction_id === null
        ? "market_historical"
        : "unknown";

  return {
    id: registration.id,
    sourceName: registration.source_name,
    sourceClass: registration.source_class,
    active: registration.active,
    scheduleCron: registration.schedule_cron,
    lastRunAt: latest ? runTimestamp(latest) : null,
    lastRunScope: latest ? latestScope : "unknown",
    lastRunHadError: Boolean(latest?.errors ?? latest?.error_text),
    recordsPulled: latest?.records_pulled ?? null,
    rowsInserted: latest?.rows_inserted ?? null,
    rowsUpdated: latest?.rows_updated ?? null,
    rowsUnchanged: latest?.rows_unchanged ?? null
  };
}

function queueSeverity(high: number, medium: number, low: number) {
  if (high > 0) {
    return "high";
  }
  if (medium > 0) {
    return "medium";
  }
  if (low > 0) {
    return "low";
  }
  return "cleared";
}

export async function getCoverageData(): Promise<CoverageDataResult> {
  const supabase = await createSupabaseServerClient();

  const { data: jurisdictionData, error: jurisdictionError } = await supabase
    .from("jurisdictions")
    .select("id, slug, name, display_name, state, entity_type, markets:market_id(id, slug, name, display_name)")
    .order("name", { ascending: true });

  if (jurisdictionError) {
    return { data: [], newsSources: [], error: jurisdictionError.message };
  }

  const [
    projects,
    reviewItems,
    sourceRegistrations,
    sourceRuns,
    researcherOverrides,
    newsSources,
    systemAlerts
  ] = await Promise.all([
    fetchAllRows<RawProject>(
      supabase,
      "projects",
      "id, jurisdiction_id, pipeline_status, last_reviewed_date"
    ),
    fetchAllRows<RawReviewItem>(supabase, "review_items", "id, project_id, status, state, priority"),
    fetchAllRows<RawSourceRegistration>(
      supabase,
      "source_registrations",
      "id, jurisdiction_id, source_name, source_class, active, schedule_cron"
    ),
    fetchAllRows<RawSourceRun>(
      supabase,
      "source_runs",
      "id, market, jurisdiction_id, source_name, run_timestamp, finished_at, records_pulled, rows_inserted, rows_updated, rows_unchanged, errors, error_text"
    ),
    fetchAllRows<RawResearcherOverride>(
      supabase,
      "researcher_overrides",
      "project_id, cleared_at"
    ),
    fetchAllRows<RawNewsSource>(
      supabase,
      "news_sources",
      "id, slug, name, active, schedule_cron, schedule_timezone, config"
    ),
    fetchAllRows<RawSystemAlert>(
      supabase,
      "system_alerts",
      "alert_key, severity, scope, message, raised_at, last_seen_at, cleared_at"
    )
  ]);

  const error =
    projects.error ??
    reviewItems.error ??
    sourceRegistrations.error ??
    sourceRuns.error ??
    researcherOverrides.error ??
    newsSources.error ??
    systemAlerts.error;
  if (error) {
    return { data: [], newsSources: [], error };
  }

  const projectJurisdiction = new Map<string, string>();
  for (const project of projects.rows) {
    if (project.jurisdiction_id) {
      projectJurisdiction.set(project.id, project.jurisdiction_id);
    }
  }
  const activeOverrideProjectIds = new Set(
    researcherOverrides.rows
      .filter((override) => override.cleared_at === null)
      .map((override) => override.project_id)
  );

  const jurisdictions = ((jurisdictionData ?? []) as RawJurisdiction[]).map((jurisdiction) => {
    const market = singleMarket(jurisdiction.markets);
    const jurisdictionProjects = projects.rows.filter(
      (project) => project.jurisdiction_id === jurisdiction.id
    );
    const jurisdictionReviewItems = reviewItems.rows.filter((item) => {
      const projectId = item.project_id;
      return projectId ? projectJurisdiction.get(projectId) === jurisdiction.id : false;
    });
    const pendingItems = jurisdictionReviewItems.filter((item) => (item.state ?? "open") === "open");
    const stagedItems = jurisdictionReviewItems.filter(
      (item) => item.state === "staged" && item.status !== "deferred"
    );
    const deferredItems = jurisdictionReviewItems.filter((item) => item.status === "deferred");
    const high = pendingItems.filter((item) => item.priority === "high").length;
    const medium = pendingItems.filter((item) => item.priority === "medium").length;
    const low = pendingItems.filter((item) => item.priority === "low").length;
    const sources = sourceRegistrations.rows
      .filter((registration) => registration.jurisdiction_id === jurisdiction.id)
      .map((registration) =>
        sourceSummary(registration, sourceRuns.rows, jurisdiction.id, market?.slug ?? null)
      )
      .sort((a, b) => a.sourceClass.localeCompare(b.sourceClass) || a.sourceName.localeCompare(b.sourceName));

    return {
      id: jurisdiction.id,
      slug: jurisdiction.slug,
      name: jurisdiction.name,
      displayName: jurisdiction.display_name ?? jurisdiction.name,
      state: jurisdiction.state,
      entityType: jurisdiction.entity_type,
      market: market
        ? {
            id: market.id,
            slug: market.slug,
            name: market.name,
            displayName: market.display_name ?? market.name
          }
        : null,
      projectCount: jurisdictionProjects.length,
      underConstructionCount: jurisdictionProjects.filter(
        (project) => project.pipeline_status === "Under Construction"
      ).length,
      queue: {
        pending: pendingItems.length,
        staged: stagedItems.length,
        deferred: deferredItems.length,
        high,
        medium,
        low,
        severity: queueSeverity(high, medium, low)
      },
      lastIngested: {
        gov: maxIsoDate(sources.filter((source) => source.sourceClass === "gov").map((source) => source.lastRunAt)),
        news: maxIsoDate(sources.filter((source) => source.sourceClass === "news").map((source) => source.lastRunAt)),
        costar: maxIsoDate(
          sources.filter((source) => source.sourceClass === "costar").map((source) => source.lastRunAt)
        )
      },
      lastReviewedAt: maxIsoDate(jurisdictionProjects.map((project) => project.last_reviewed_date)),
      openOverrides: jurisdictionProjects.filter((project) => activeOverrideProjectIds.has(project.id)).length,
      sources
    } satisfies CoverageJurisdiction;
  });

  const newsSourceHealth = newsSources.rows
    .map((source) => newsSourceHealthSummary(source, sourceRuns.rows, systemAlerts.rows))
    .sort((a, b) => a.name.localeCompare(b.name));

  return { data: jurisdictions, newsSources: newsSourceHealth, error: null };
}

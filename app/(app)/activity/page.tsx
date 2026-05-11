import Link from "next/link";
import {
  AlertCircle,
  Bot,
  CheckCircle2,
  ExternalLink,
  Filter,
  GitCommit,
  Sparkles
} from "lucide-react";
import { getActivityData, getActivitySemanticMetrics } from "@/lib/activity/data";
import type {
  ActivityEvidenceSummary,
  ActivityEvent,
  ActivityQuery,
  ActivitySemanticMetric,
  ActivitySemanticParseHealth
} from "@/lib/activity/types";
import { cn } from "@/lib/utils";

export const dynamic = "force-dynamic";

type ActivityPageProps = {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

const VIEW_PRESETS = [
  { value: "all", label: "All activity" },
  { value: "agent", label: "Agent decisions" },
  { value: "auto_applied", label: "Auto-applied" },
  { value: "semantic", label: "Semantic status" }
];

const EVENT_TYPES = [
  { value: "", label: "All types" },
  { value: "change", label: "Change log" },
  { value: "resolution", label: "Resolution" },
  { value: "agent", label: "Agent run" },
  { value: "semantic", label: "Semantic" }
];

const FIELD_OPTIONS = [
  // TODO(AGENT.2 step 11): derive this from the canonical field registry.
  { value: "", label: "All fields" },
  { value: "pipeline_status", label: "Status" },
  { value: "total_units", label: "Total units" },
  { value: "affordable_units", label: "Affordable units" },
  { value: "market_rate_units", label: "Market-rate units" },
  { value: "workforce_units", label: "Workforce units" },
  { value: "date_delivery", label: "Delivery date" },
  { value: "developer", label: "Developer" }
];

const SOURCE_OPTIONS = [
  // TODO(AGENT.2 step 11): derive news-source slugs from active news_sources rows.
  { value: "", label: "All sources" },
  { value: "semantic.news_v1", label: "Semantic Pass 2c" },
  { value: "news_article", label: "News article" },
  { value: "urbanize_la", label: "Urbanize LA" },
  { value: "resolution_engine", label: "Resolution engine" },
  { value: "costar", label: "CoStar" },
  { value: "pipedream", label: "Pipedream" },
  { value: "ladbs_permit", label: "LADBS permit" },
  { value: "inline_override", label: "Inline override" },
  { value: "manual_project", label: "Manual project" }
];

function firstQueryValue(value: string | string[] | undefined) {
  return Array.isArray(value) ? value[0] : value;
}

function activityHrefForView(query: ActivityQuery, view: string) {
  return activityHref(query, { cursor: null, eventType: view === "all" ? query.eventType : null, view });
}

function activityHrefForCursor(query: ActivityQuery, cursor: string | null) {
  return activityHref(query, { cursor });
}

function activityHref(
  query: ActivityQuery,
  overrides: Partial<ActivityQuery> = {}
) {
  const resolvedQuery = { ...query, ...overrides };
  const params = new URLSearchParams();
  params.set("view", resolvedQuery.view ?? "all");
  if ((resolvedQuery.view ?? "all") === "all") {
    setQueryParam(params, "type", resolvedQuery.eventType);
  }
  setQueryParam(params, "source", resolvedQuery.source);
  setQueryParam(params, "field", resolvedQuery.field);
  setQueryParam(params, "actor", resolvedQuery.actor);
  setQueryParam(params, "project_id", resolvedQuery.projectId);
  setQueryParam(params, "market", resolvedQuery.market);
  setQueryParam(params, "jurisdiction", resolvedQuery.jurisdiction);
  setQueryParam(params, "from", resolvedQuery.from);
  setQueryParam(params, "to", resolvedQuery.to);
  setQueryParam(params, "cursor", resolvedQuery.cursor);
  return `/activity?${params.toString()}`;
}

function setQueryParam(params: URLSearchParams, key: string, value: string | null) {
  if (value) {
    params.set(key, value);
  }
}

export default async function ActivityPage({ searchParams }: ActivityPageProps) {
  const params = searchParams ? await searchParams : {};
  const query: ActivityQuery = {
    view: firstQueryValue(params.view) ?? "all",
    eventType: firstQueryValue(params.type) ?? null,
    source: firstQueryValue(params.source) ?? null,
    field: firstQueryValue(params.field) ?? null,
    actor: firstQueryValue(params.actor) ?? null,
    projectId: firstQueryValue(params.project_id) ?? null,
    market: firstQueryValue(params.market) ?? null,
    jurisdiction: firstQueryValue(params.jurisdiction) ?? null,
    from: firstQueryValue(params.from) ?? null,
    to: firstQueryValue(params.to) ?? null,
    cursor: firstQueryValue(params.cursor) ?? null
  };
  const [result, semanticMetricsResult] = await Promise.all([
    getActivityData(query),
    getActivitySemanticMetrics(query)
  ]);

  if (result.error || !result.data) {
    return (
      <main className="px-5 py-6">
        <div className="flex max-w-2xl items-start gap-3 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <div>
            <p className="font-medium">Could not load Activity.</p>
            <p>{result.error ?? "Activity data was not returned."}</p>
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="px-5 py-6">
      <div className="mb-5 flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-normal text-slate-950">Activity</h1>
          <p className="mt-1 text-sm text-slate-500">
            {result.data.events.length} rows generated {formatDateTime(result.data.generated_at)}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {VIEW_PRESETS.map((preset) => (
            <Link
              className={cn(
                "rounded-md border px-3 py-1.5 text-sm font-medium",
                (query.view ?? "all") === preset.value
                  ? "border-teal-700 bg-teal-700 text-white"
                  : "border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:text-slate-950"
              )}
              href={activityHrefForView(query, preset.value)}
              key={preset.value}
            >
              {preset.label}
            </Link>
          ))}
        </div>
      </div>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_20rem]">
        <section className="rounded-md border border-slate-200 bg-white">
          <div className="border-b border-slate-200 px-4 py-3">
            <h2 className="text-sm font-semibold text-slate-950">Audit Feed</h2>
          </div>
          {result.data.events.length ? (
            <div className="divide-y divide-slate-100">
              {result.data.events.map((event) => (
                <ActivityRow event={event} key={event.id} />
              ))}
            </div>
          ) : (
            <div className="px-4 py-10 text-center">
              <p className="text-sm font-medium text-slate-950">No activity rows</p>
              <p className="mt-1 text-sm text-slate-500">No audit rows match the current filters.</p>
            </div>
          )}
          <ActivityPagination nextCursor={result.data.next_cursor} query={query} />
        </section>

        <aside className="h-fit rounded-md border border-slate-200 bg-white p-4">
          <div className="flex items-center gap-2">
            <Filter className="size-4 text-slate-500" aria-hidden="true" />
            <h2 className="text-sm font-semibold text-slate-950">Filters</h2>
          </div>
          <form action="/activity" className="mt-4 space-y-3">
            <FilterSelect label="View" name="view" options={VIEW_PRESETS} value={query.view ?? "all"} />
            <FilterSelect label="Type" name="type" options={EVENT_TYPES} value={query.eventType ?? ""} />
            <FilterSelect label="Source" name="source" options={SOURCE_OPTIONS} value={query.source ?? ""} />
            <FilterSelect label="Field" name="field" options={FIELD_OPTIONS} value={query.field ?? ""} />
            <FilterInput label="Actor/profile" name="actor" value={query.actor ?? ""} />
            <FilterInput label="Project ID" name="project_id" value={query.projectId ?? ""} />
            <FilterInput label="Market" name="market" value={query.market ?? ""} />
            <FilterInput label="Jurisdiction" name="jurisdiction" value={query.jurisdiction ?? ""} />
            <FilterInput label="From" name="from" type="date" value={query.from ?? ""} />
            <FilterInput label="To" name="to" type="date" value={query.to ?? ""} />
            <button
              className="w-full rounded-md bg-teal-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-teal-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700"
              type="submit"
            >
              Apply
            </button>
          </form>
          <SemanticMetricsPanel
            error={semanticMetricsResult.error}
            metrics={semanticMetricsResult.data?.metrics ?? []}
            parseHealth={semanticMetricsResult.data?.parse_health ?? null}
            thresholds={semanticMetricsResult.data?.thresholds ?? {}}
          />
        </aside>
      </div>
    </main>
  );
}

function ActivityPagination({
  nextCursor,
  query
}: {
  nextCursor: string | null;
  query: ActivityQuery;
}) {
  if (!nextCursor && !query.cursor) {
    return null;
  }
  return (
    <div className="flex items-center justify-between gap-3 border-t border-slate-200 px-4 py-3 text-sm">
      {query.cursor ? (
        <Link className="font-medium text-slate-600 hover:text-slate-950" href={activityHrefForCursor(query, null)}>
          First page
        </Link>
      ) : (
        <span className="text-slate-400">First page</span>
      )}
      {nextCursor ? (
        <Link className="font-medium text-teal-700 hover:text-teal-900" href={activityHrefForCursor(query, nextCursor)}>
          Next page
        </Link>
      ) : (
        <span className="text-slate-400">No more rows</span>
      )}
    </div>
  );
}

function ActivityRow({ event }: { event: ActivityEvent }) {
  return (
    <details className="group px-4 py-3">
      <summary
        className="grid cursor-pointer list-none gap-3 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700 lg:grid-cols-[8.5rem_9rem_minmax(0,1fr)_12rem_7rem]"
        tabIndex={0}
      >
        <span className="text-slate-500">{formatDateTime(event.occurred_at)}</span>
        <span className="inline-flex items-center gap-1.5 font-medium text-slate-950">
          <ActivityEventIcon eventType={event.event_type} />
          {eventTypeLabel(event.event_type)}
        </span>
        <span className="min-w-0">
          <span className="block truncate font-medium text-slate-900">{event.title}</span>
          <span className="block truncate text-slate-500">{event.summary}</span>
        </span>
        <ProjectLink event={event} />
        <span className="w-fit rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-[11px] text-slate-700">
          {event.source_label}
        </span>
      </summary>
      <div className="mt-3 rounded-md border border-slate-200 bg-slate-50 p-3">
        <DetailRows event={event} />
      </div>
    </details>
  );
}

function DetailRows({ event }: { event: ActivityEvent }) {
  const evidenceSummaries = event.evidence_summaries ?? [];
  const evidenceCount = detailNumber(event, "evidence_count") ?? evidenceSummaries.length;
  const evidenceSummaryCap = detailNumber(event, "evidence_summary_cap") ?? evidenceSummaries.length;
  const evidenceLabel = event.event_type === "agent" ? "Evidence consulted" : "Evidence";

  return (
    <dl className="grid gap-2 text-sm md:grid-cols-[9rem_minmax(0,1fr)]">
      <DetailRow label="Field" value={event.field_label ?? "-"} />
      <DetailRow label="Actor" value={event.actor_label ?? "-"} />
      <DetailRow label="Source" value={event.source_label} />
      <DetailRow label="Old value" value={formatUnknown(event.old_value)} />
      <DetailRow label="New value" value={formatUnknown(event.new_value)} />
      {event.article ? (
        <DetailRow
          label="Article"
          value={
            <Link className="font-medium text-teal-700 hover:text-teal-900" href={`/research/articles/${event.article.id}`}>
              {event.article.title ?? event.article.url}
            </Link>
          }
        />
      ) : null}
      {!event.article && event.intake_summary ? (
        <DetailRow label="Intake" value={intakeSummaryLabel(event.intake_summary)} />
      ) : null}
      {event.article_fetched_at || event.agent_created_at ? (
        <DetailRow
          label="News timing"
          value={`fetched ${formatDateTime(event.article_fetched_at)} / agent ${formatDateTime(event.agent_created_at)}`}
        />
      ) : null}
      {event.agent_triggers.length ? <DetailRow label="Triggers" value={event.agent_triggers.join(", ")} /> : null}
      {event.agent_outcome ? <DetailRow label="Outcome" value={event.agent_outcome} /> : null}
      {event.cost_usd !== null ? <DetailRow label="Cost" value={`$${event.cost_usd.toFixed(6)}`} /> : null}
      {detailString(event, "reason_code") ? <DetailRow label="Reason code" value={detailString(event, "reason_code")} /> : null}
      {detailString(event, "confidence") ? <DetailRow label="Confidence" value={detailString(event, "confidence")} /> : null}
      {evidenceSummaries.length || evidenceCount > 0 ? (
        <DetailRow
          label={evidenceLabel}
          value={
            <EvidenceSummaryList
              projectId={event.project?.id ?? null}
              rows={evidenceSummaries}
              summaryCap={evidenceSummaryCap}
              totalCount={evidenceCount}
              truncated={detailBoolean(event, "evidence_summaries_truncated") ?? false}
            />
          }
        />
      ) : null}
      {event.review_item_id ? (
        <DetailRow
          label="Review item"
          value={
            <Link className="font-medium text-teal-700 hover:text-teal-900" href={`/review/${event.review_item_id}`}>
              <CompactId id={event.review_item_id} />
            </Link>
          }
        />
      ) : null}
      {event.review_item_ids.length ? (
        <DetailRow
          label="Review items"
          value={event.review_item_ids.map((id) => (
            <Link className="mr-2 font-medium text-teal-700 hover:text-teal-900" href={`/review/${id}`} key={id}>
              <CompactId id={id} />
            </Link>
          ))}
        />
      ) : null}
      {event.agent_reasoning_trace ? <DetailRow label="Reasoning" value={event.agent_reasoning_trace} /> : null}
      {event.article?.url ? (
        <DetailRow
          label="Source URL"
          value={
            <a className="inline-flex items-center gap-1 font-medium text-teal-700 hover:text-teal-900" href={event.article.url} rel="noreferrer" target="_blank">
              Open article
              <ExternalLink className="size-3.5" aria-hidden="true" />
            </a>
          }
        />
      ) : null}
    </dl>
  );
}

function EvidenceSummaryList({
  projectId,
  rows,
  summaryCap,
  totalCount,
  truncated
}: {
  projectId: string | null;
  rows: ActivityEvidenceSummary[];
  summaryCap: number;
  totalCount: number;
  truncated: boolean;
}) {
  const attemptedCount = Math.min(totalCount, summaryCap);
  const missingCount = Math.max(attemptedCount - rows.length, 0);
  return (
    <div className="space-y-2">
      {rows.map((row) => (
        <EvidenceSummaryRow key={row.evidence_id} row={row} />
      ))}
      {truncated ? (
        <p className="text-xs text-slate-500">
          {rows.length} of {totalCount} shown - see{" "}
          {projectId ? (
            <Link className="font-medium text-teal-700 hover:text-teal-900" href={`/pipeline/${projectId}?tab=evidence`}>
              Project Detail Evidence
            </Link>
          ) : (
            "Project Detail Evidence"
          )}{" "}
          for the rest.
        </p>
      ) : null}
      {missingCount > 0 ? (
        <p className="text-xs text-amber-700">
          {missingCount} of {attemptedCount} evidence references could not be loaded.
        </p>
      ) : null}
    </div>
  );
}

function EvidenceSummaryRow({ row }: { row: ActivityEvidenceSummary }) {
  const highlightedPassages = row.highlights
    .map((highlight) => highlight.passage)
    .filter((passage) => passage !== null && passage !== undefined)
    .slice(0, 2);
  return (
    <div className="rounded-md border border-slate-200 bg-white px-3 py-2 text-xs">
      <div className="flex items-start gap-2">
        <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-slate-300" />
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <p className="min-w-0 break-words font-medium text-slate-900">{row.summary}</p>
            {row.role ? (
              <span className="shrink-0 rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-[11px] font-medium text-slate-600">
                {humanize(row.role)}
              </span>
            ) : null}
          </div>
          {row.detail ? <p className="mt-1 break-words text-slate-500">{row.detail}</p> : null}
          {highlightedPassages.length ? (
            <div className="mt-2 space-y-1">
              {highlightedPassages.map((passage, index) => (
                <p
                  className="break-words rounded border border-slate-200 bg-slate-50 px-2 py-1 text-slate-700"
                  key={`${row.evidence_id}-highlight-${index}`}
                >
                  {formatUnknown(passage)}
                </p>
              ))}
            </div>
          ) : null}
          <p className="mt-1 text-slate-500">
            {humanize(row.source_type)}
            {row.evidence_date ? ` - ${formatDate(row.evidence_date)}` : ""}
            {row.source_record_id ? ` - ${row.source_record_id}` : ""}
          </p>
          {row.extracted_value !== null && row.extracted_value !== undefined ? (
            <p className="mt-2 rounded border border-slate-200 bg-slate-50 px-2 py-1 text-slate-700">
              {formatUnknown(row.extracted_value)}
            </p>
          ) : null}
          {row.external_link ? (
            <a
              className="mt-2 inline-flex items-center gap-1 font-medium text-teal-700 hover:text-teal-900"
              href={row.external_link}
              rel="noreferrer"
              target="_blank"
            >
              Source
              <ExternalLink className="size-3.5" aria-hidden="true" />
            </a>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function DetailRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <>
      <dt className="text-xs font-medium uppercase tracking-normal text-slate-500">{label}</dt>
      <dd className="min-w-0 break-words text-slate-800">{value}</dd>
    </>
  );
}

function ProjectLink({ event }: { event: ActivityEvent }) {
  if (!event.project) {
    return <span className="min-w-0 truncate text-xs text-slate-500">No linked project</span>;
  }
  return (
    <Link className="min-w-0 truncate text-xs font-medium text-teal-700 hover:text-teal-900" href={`/pipeline/${event.project.id}`}>
      {event.project.project_name ?? event.project.canonical_address}
    </Link>
  );
}

function FilterSelect({
  label,
  name,
  options,
  value
}: {
  label: string;
  name: string;
  options: { value: string; label: string }[];
  value: string;
}) {
  return (
    <label className="block">
      <span className="text-xs font-medium text-slate-600">{label}</span>
      <select
        className="mt-1 w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 text-sm text-slate-900 focus:border-teal-600 focus:outline-none"
        defaultValue={value}
        name={name}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function FilterInput({
  label,
  name,
  type = "text",
  value
}: {
  label: string;
  name: string;
  type?: string;
  value: string;
}) {
  return (
    <label className="block">
      <span className="text-xs font-medium text-slate-600">{label}</span>
      <input
        className="mt-1 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm text-slate-900 focus:border-teal-600 focus:outline-none"
        defaultValue={value}
        name={name}
        type={type}
      />
    </label>
  );
}

function ActivityEventIcon({ eventType }: { eventType: ActivityEvent["event_type"] }) {
  if (eventType === "agent") {
    return <Bot className="size-4 text-slate-500" aria-hidden="true" />;
  }
  if (eventType === "semantic") {
    return <Sparkles className="size-4 text-slate-500" aria-hidden="true" />;
  }
  if (eventType === "resolution") {
    return <CheckCircle2 className="size-4 text-slate-500" aria-hidden="true" />;
  }
  return <GitCommit className="size-4 text-slate-500" aria-hidden="true" />;
}

function eventTypeLabel(eventType: ActivityEvent["event_type"]) {
  if (eventType === "agent") {
    return "Agent";
  }
  if (eventType === "resolution") {
    return "Resolution";
  }
  if (eventType === "semantic") {
    return "Semantic";
  }
  return "Change";
}

function SemanticMetricsPanel({
  error,
  metrics,
  parseHealth,
  thresholds
}: {
  error: string | null;
  metrics: ActivitySemanticMetric[];
  parseHealth: ActivitySemanticParseHealth | null;
  thresholds: Record<string, number>;
}) {
  const visible = metrics.slice(0, 6);
  const gapThreshold = thresholds.glossary_gap_rate ?? 0.15;
  const unmappableThreshold = thresholds.unmappable_rate ?? 0.05;
  const healthStatuses = parseHealth?.statuses ?? [];
  return (
    <div className="mt-5 border-t border-slate-200 pt-4">
      <div className="flex items-center gap-2">
        <Sparkles className="size-4 text-slate-500" aria-hidden="true" />
        <h2 className="text-sm font-semibold text-slate-950">Semantic Metrics</h2>
      </div>
      {error ? <p className="mt-2 text-xs text-red-700">{error}</p> : null}
      {!error && parseHealth ? (
        <div className="mt-3 rounded-md border border-slate-200 bg-slate-50 px-2 py-2 text-xs">
          <div className="flex items-start justify-between gap-2">
            <span className="font-medium text-slate-900">
              Pass 2c parse health, all fields
            </span>
            <span className={parseHealth.failure_count ? "text-amber-700" : "text-slate-500"}>
              {parseHealth.failure_count} failed
            </span>
          </div>
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-slate-600">
            <span>{parseHealth.total_count} calls</span>
            <span>{(parseHealth.ok_rate * 100).toFixed(0)}% ok</span>
            <span>{(parseHealth.failure_rate * 100).toFixed(0)}% failed</span>
          </div>
          {healthStatuses.length ? (
            <div className="mt-2 flex flex-wrap gap-1">
              {healthStatuses.map((status) => (
                <span
                  className="rounded border border-slate-200 bg-white px-1.5 py-0.5 text-slate-600"
                  key={status.parse_status}
                >
                  {parseStatusLabel(status.parse_status)} {status.total_count}
                </span>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
      {!error && visible.length ? (
        <div className="mt-3 space-y-2">
          {visible.map((metric) => {
            const alert =
              metric.glossary_gap_rate > gapThreshold ||
              metric.unmappable_rate > unmappableThreshold;
            return (
              <div
                className={cn(
                  "rounded-md border px-2 py-2 text-xs",
                  alert
                    ? "border-amber-300 bg-amber-50 text-amber-950"
                    : "border-slate-200 bg-slate-50"
                )}
                key={`${metric.market ?? "none"}:${metric.source_slug ?? "none"}:${metric.field_name}:${metric.reason_code}`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-medium text-slate-900">
                    {metric.field_label}
                  </span>
                  <span className="text-slate-500">{metric.total_count}</span>
                </div>
                <p className="mt-1 truncate text-slate-500">{metric.reason_code}</p>
                <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-slate-600">
                  <span>gap {(metric.glossary_gap_rate * 100).toFixed(0)}%</span>
                  <span>unmappable {(metric.unmappable_rate * 100).toFixed(0)}%</span>
                  {metric.reviewer_rejection_rate !== null ? (
                    <span>
                      rejected {(metric.reviewer_rejection_rate * 100).toFixed(0)}%/
                      {metric.reviewer_decision_count}
                    </span>
                  ) : null}
                  {metric.market ? <span>{metric.market}</span> : null}
                  {alert ? <span className="font-medium text-amber-800">threshold exceeded</span> : null}
                </div>
              </div>
            );
          })}
        </div>
      ) : null}
      {!error && !visible.length ? (
        <p className="mt-2 text-xs text-slate-500">No semantic rows</p>
      ) : null}
    </div>
  );
}

function parseStatusLabel(value: string) {
  return value.replaceAll("_", " ");
}

function humanize(value: string) {
  return value.replaceAll("_", " ").replaceAll("-", " ");
}

function formatDate(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  const dateOnly = value.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  const parsed = dateOnly
    ? new Date(Number(dateOnly[1]), Number(dateOnly[2]) - 1, Number(dateOnly[3]))
    : new Date(value);
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric"
  }).format(parsed);
}

function formatDateTime(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit"
  }).format(new Date(value));
}

function formatUnknown(value: unknown) {
  if (value === null || value === undefined) {
    return "-";
  }
  if (typeof value === "string") {
    return value;
  }
  return JSON.stringify(value);
}

function CompactId({ id }: { id: string }) {
  return <span className="font-mono text-xs">{id.slice(0, 8)}</span>;
}

function detailString(event: ActivityEvent, key: string) {
  const value = event.detail[key];
  return typeof value === "string" && value.length ? value : null;
}

function detailNumber(event: ActivityEvent, key: string) {
  const value = event.detail[key];
  return typeof value === "number" ? value : null;
}

function detailBoolean(event: ActivityEvent, key: string) {
  const value = event.detail[key];
  return typeof value === "boolean" ? value : null;
}

function intakeSummaryLabel(summary: NonNullable<ActivityEvent["intake_summary"]>) {
  if (summary.permit) {
    return [
      summary.permit.permit_number ?? summary.permit.source_record_id,
      summary.permit.permit_type,
      summary.permit.issue_date,
      summary.permit.address,
    ]
      .filter(Boolean)
      .join(" | ") || summary.label || summary.kind;
  }
  return summary.label ?? summary.kind;
}

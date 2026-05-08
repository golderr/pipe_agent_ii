import Link from "next/link";
import {
  AlertCircle,
  Bot,
  CheckCircle2,
  ExternalLink,
  Filter,
  GitCommit
} from "lucide-react";
import { getActivityData } from "@/lib/activity/data";
import type { ActivityEvent, ActivityQuery } from "@/lib/activity/types";
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
  { value: "agent", label: "Agent run" }
];

const FIELD_OPTIONS = [
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
  { value: "", label: "All sources" },
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

export default async function ActivityPage({ searchParams }: ActivityPageProps) {
  const params = searchParams ? await searchParams : {};
  const query: ActivityQuery = {
    view: firstQueryValue(params.view) ?? "all",
    eventType: firstQueryValue(params.type) ?? null,
    source: firstQueryValue(params.source) ?? null,
    field: firstQueryValue(params.field) ?? null,
    actor: firstQueryValue(params.actor) ?? null,
    projectId: firstQueryValue(params.project_id) ?? null,
    from: firstQueryValue(params.from) ?? null,
    to: firstQueryValue(params.to) ?? null
  };
  const result = await getActivityData(query);

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
              href={`/activity?view=${encodeURIComponent(preset.value)}`}
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
            <FilterInput label="From" name="from" type="date" value={query.from ?? ""} />
            <FilterInput label="To" name="to" type="date" value={query.to ?? ""} />
            <button
              className="w-full rounded-md bg-teal-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-teal-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700"
              type="submit"
            >
              Apply
            </button>
          </form>
        </aside>
      </div>
    </main>
  );
}

function ActivityRow({ event }: { event: ActivityEvent }) {
  const Icon = eventIcon(event.event_type);
  return (
    <details className="group px-4 py-3">
      <summary
        className="grid cursor-pointer list-none gap-3 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700 lg:grid-cols-[8.5rem_9rem_minmax(0,1fr)_12rem_7rem]"
        tabIndex={0}
      >
        <span className="text-slate-500">{formatDateTime(event.occurred_at)}</span>
        <span className="inline-flex items-center gap-1.5 font-medium text-slate-950">
          <Icon className="size-4 text-slate-500" aria-hidden="true" />
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
      {event.article_fetched_at || event.agent_created_at ? (
        <DetailRow
          label="News timing"
          value={`fetched ${formatDateTime(event.article_fetched_at)} / agent ${formatDateTime(event.agent_created_at)}`}
        />
      ) : null}
      {event.agent_triggers.length ? <DetailRow label="Triggers" value={event.agent_triggers.join(", ")} /> : null}
      {event.agent_outcome ? <DetailRow label="Outcome" value={event.agent_outcome} /> : null}
      {event.cost_usd !== null ? <DetailRow label="Cost" value={`$${event.cost_usd.toFixed(6)}`} /> : null}
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

function eventIcon(eventType: ActivityEvent["event_type"]) {
  if (eventType === "agent") {
    return Bot;
  }
  if (eventType === "resolution") {
    return CheckCircle2;
  }
  return GitCommit;
}

function eventTypeLabel(eventType: ActivityEvent["event_type"]) {
  if (eventType === "agent") {
    return "Agent";
  }
  if (eventType === "resolution") {
    return "Resolution";
  }
  return "Change";
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

import Link from "next/link";
import type { ReactNode } from "react";
import { AlertCircle, ArrowRight, BarChart3, ClipboardList, Clock, GitCompareArrows, History } from "lucide-react";
import { getDashboardData } from "@/lib/dashboard/data";
import type { DashboardActivityLine, DashboardData, DashboardPriorityCounts, DashboardStatusBucket } from "@/lib/dashboard/types";
import { compactStatus, statusStyle } from "@/lib/status";
import { cn } from "@/lib/utils";

export const dynamic = "force-dynamic";

function number(value: number) {
  return new Intl.NumberFormat("en-US").format(value);
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric"
  }).format(new Date(value));
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit"
  }).format(new Date(value));
}

function humanizeToken(value: string) {
  return value
    .split(/[_-]/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function pipelineStatusHref(status: string) {
  return `/pipeline?status=${encodeURIComponent(status)}`;
}

export default async function DashboardPage() {
  const result = await getDashboardData();

  if (result.error || !result.data) {
    return (
      <main className="px-5 py-6">
        <div className="flex max-w-2xl items-start gap-3 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <div>
            <p className="font-medium">Could not load Dashboard.</p>
            <p>{result.error ?? "Dashboard data was not returned."}</p>
          </div>
        </div>
      </main>
    );
  }

  return <DashboardView data={result.data} />;
}

function DashboardView({ data }: { data: DashboardData }) {
  return (
    <main className="px-5 py-5">
      <div className="mb-5 flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="text-xs font-medium uppercase tracking-normal text-slate-500">Dashboard</p>
          <h1 className="mt-1 text-xl font-semibold tracking-normal text-slate-950">{data.marketLabel}</h1>
        </div>
        <p className="text-xs text-slate-500">Updated {formatTime(data.generatedAt)}</p>
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,0.92fr)_minmax(24rem,1.08fr)]">
        <div className="grid gap-4 md:grid-cols-3 xl:grid-cols-1">
          <NeedsAttentionTile data={data} />
          <StalledTile data={data} />
          <ContradictionsTile data={data} />
        </div>
        <div className="grid gap-4 lg:grid-cols-2">
          <PipelineStatusTile data={data} />
          <RecentActivityTile data={data} />
        </div>
      </div>
    </main>
  );
}

function TileShell({
  title,
  icon,
  value,
  label,
  href,
  linkLabel,
  children
}: {
  title: string;
  icon: ReactNode;
  value: string;
  label: string;
  href: string;
  linkLabel: string;
  children: ReactNode;
}) {
  return (
    <section className="flex min-h-52 flex-col rounded-md border border-slate-200 bg-white p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-950">{title}</h2>
          <p className="mt-3 text-4xl font-semibold tracking-normal text-slate-950">{value}</p>
          <p className="mt-1 text-sm text-slate-500">{label}</p>
        </div>
        <div className="rounded-md border border-slate-200 bg-slate-50 p-2 text-slate-500">{icon}</div>
      </div>
      <div className="mt-4 flex-1">{children}</div>
      <Link className="mt-4 inline-flex items-center gap-1 text-sm font-medium text-teal-700 hover:text-teal-900" href={href}>
        {linkLabel}
        <ArrowRight className="size-4" aria-hidden="true" />
      </Link>
    </section>
  );
}

function NeedsAttentionTile({ data }: { data: DashboardData }) {
  const priorities = data.needsAttention.priorities;

  return (
    <TileShell
      title="Needs Attention"
      icon={<ClipboardList className="size-4" aria-hidden="true" />}
      value={number(data.needsAttention.total)}
      label="open review items"
      href="/coverage"
      linkLabel="Open Coverage"
    >
      <PriorityBars priorities={priorities} total={data.needsAttention.total} />
      {data.needsAttention.types.length ? (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {data.needsAttention.types.map((item) => (
            <span className="rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-[11px] text-slate-600" key={item.type}>
              {humanizeToken(item.type)} {number(item.count)}
            </span>
          ))}
        </div>
      ) : (
        <p className="mt-3 text-xs text-slate-500">Review queue is clear.</p>
      )}
      {data.needsAttention.deferred ? (
        <p className="mt-3 text-xs text-slate-500">{number(data.needsAttention.deferred)} deferred items remain parked.</p>
      ) : null}
      {data.needsAttention.staged ? (
        <p className="mt-3 text-xs text-slate-500">{number(data.needsAttention.staged)} items are in review.</p>
      ) : null}
    </TileShell>
  );
}

function StalledTile({ data }: { data: DashboardData }) {
  return (
    <TileShell
      title="Stalled Candidates"
      icon={<Clock className="size-4" aria-hidden="true" />}
      value={number(data.stalled.total)}
      label={`no evidence since ${formatDate(data.stalled.cutoffDate)}`}
      href="/pipeline?status=Approved&status=Under%20Construction"
      linkLabel="Review Approved + U/C"
    >
      <StatusBars buckets={data.stalled.statusBuckets} maxRows={3} />
      {data.stalled.total ? (
        <p className="mt-3 text-xs text-slate-500">Approved and U/C projects only. Phase E will generate formal stall flags.</p>
      ) : (
        <p className="mt-3 text-xs text-slate-500">No stale Approved or U/C projects found.</p>
      )}
    </TileShell>
  );
}

function ContradictionsTile({ data }: { data: DashboardData }) {
  if (!data.contradictions.active) {
    return (
      <TileShell
        title="Contradictions"
        icon={<GitCompareArrows className="size-4" aria-hidden="true" />}
        value="Phase C"
        label="detection not active yet"
        href="/coverage"
        linkLabel="Open Coverage"
      >
        <p className="text-xs text-slate-500">
          This tile will show override-vs-evidence conflicts after the Phase C contradiction detector starts writing
          contradiction review items.
        </p>
      </TileShell>
    );
  }

  return (
    <TileShell
      title="Contradictions"
      icon={<GitCompareArrows className="size-4" aria-hidden="true" />}
      value={number(data.contradictions.total)}
      label="override contradiction items"
      href="/coverage"
      linkLabel="Open Coverage"
    >
      <PriorityBars priorities={data.contradictions.priorities} total={data.contradictions.total} />
      <p className="mt-3 text-xs text-slate-500">
        Counts open review items whose type includes contradiction. Dedicated detection ships in Phase C.
      </p>
    </TileShell>
  );
}

function PipelineStatusTile({ data }: { data: DashboardData }) {
  const topStatus = data.pipelineByStatus.buckets[0];

  return (
    <TileShell
      title="Pipeline By Status"
      icon={<BarChart3 className="size-4" aria-hidden="true" />}
      value={number(data.pipelineByStatus.total)}
      label={topStatus ? `${compactStatus(topStatus.status)} is largest bucket` : "projects tracked"}
      href="/pipeline"
      linkLabel="Open Pipeline"
    >
      <StatusBars buckets={data.pipelineByStatus.buckets} maxRows={8} linked />
    </TileShell>
  );
}

function RecentActivityTile({ data }: { data: DashboardData }) {
  return (
    <TileShell
      title="Recent Activity"
      icon={<History className="size-4" aria-hidden="true" />}
      value={number(data.recentActivity.evidenceRows)}
      label={`evidence rows since ${formatDate(data.recentActivity.sinceDate)}`}
      href="/pipeline"
      linkLabel="Browse Pipeline"
    >
      <div className="grid grid-cols-3 gap-2">
        <MiniMetric label="News" value={data.recentActivity.newsRows} />
        <MiniMetric label="Changed" value={data.recentActivity.sourceRowsChanged} />
        <MiniMetric label="Runs" value={data.recentActivity.sourceRuns} />
      </div>
      {data.recentActivity.lines.length ? (
        <div className="mt-3 space-y-2">
          {data.recentActivity.lines.slice(0, 3).map((line) => (
            <ActivityLine line={line} key={line.id} />
          ))}
        </div>
      ) : (
        <p className="mt-3 text-xs text-slate-500">No evidence or source-run activity in the last 7 days.</p>
      )}
    </TileShell>
  );
}

function PriorityBars({ priorities, total }: { priorities: DashboardPriorityCounts; total: number }) {
  const rows = [
    { label: "High", value: priorities.high, className: "bg-red-600" },
    { label: "Medium", value: priorities.medium, className: "bg-amber-500" },
    { label: "Low", value: priorities.low, className: "bg-slate-400" }
  ];

  return (
    <div className="space-y-2">
      {rows.map((row) => (
        <div className="grid grid-cols-[4.5rem_minmax(0,1fr)_2.5rem] items-center gap-2 text-xs" key={row.label}>
          <span className="text-slate-500">{row.label}</span>
          <div className="h-2 rounded bg-slate-100" aria-label={`${row.label}: ${row.value}`}>
            <div className={cn("h-2 rounded", row.className)} style={{ width: `${total ? (row.value / total) * 100 : 0}%` }} />
          </div>
          <span className="text-right tabular-nums text-slate-700">{number(row.value)}</span>
        </div>
      ))}
    </div>
  );
}

function StatusBars({
  buckets,
  maxRows,
  linked = false
}: {
  buckets: DashboardStatusBucket[];
  maxRows: number;
  linked?: boolean;
}) {
  const maxCount = Math.max(1, ...buckets.map((bucket) => bucket.count));
  const visible = buckets.slice(0, maxRows);

  if (!visible.length) {
    return <p className="text-xs text-slate-500">No matching projects.</p>;
  }

  return (
    <div className="space-y-2">
      {visible.map((bucket) => {
        const style = statusStyle(bucket.status);
        const row = (
          <div className="grid grid-cols-[7.5rem_minmax(0,1fr)_3rem] items-center gap-2 text-xs">
            <span className="truncate text-slate-600">{compactStatus(bucket.status)}</span>
            <div className="h-2 rounded bg-slate-100" aria-label={`${bucket.status}: ${bucket.count}`}>
              <div className="h-2 rounded" style={{ width: `${(bucket.count / maxCount) * 100}%`, backgroundColor: style.color }} />
            </div>
            <span className="text-right tabular-nums text-slate-700">{number(bucket.count)}</span>
          </div>
        );

        return linked ? (
          <Link className="block rounded-sm hover:bg-slate-50" href={pipelineStatusHref(bucket.status)} key={bucket.status}>
            {row}
          </Link>
        ) : (
          <div key={bucket.status}>{row}</div>
        );
      })}
    </div>
  );
}

function MiniMetric({ label, value }: { label: string; value: number }) {
  return (
    <div className="border-l border-slate-200 pl-2">
      <p className="text-sm font-semibold text-slate-950">{number(value)}</p>
      <p className="text-[11px] text-slate-500">{label}</p>
    </div>
  );
}

function ActivityLine({ line }: { line: DashboardActivityLine }) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_5.5rem] gap-2 text-xs">
      <div className="min-w-0">
        <p className="truncate font-medium text-slate-700">{humanizeToken(line.label)}</p>
        <p className="truncate text-slate-500">{line.detail}</p>
      </div>
      <time className="text-right text-slate-400" dateTime={line.timestamp}>
        {formatTime(line.timestamp)}
      </time>
    </div>
  );
}

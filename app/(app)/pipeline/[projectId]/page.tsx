import Link from "next/link";
import { notFound } from "next/navigation";
import { AlertCircle, ArrowLeft, ChevronRight, Circle, Clock, ExternalLink, FileJson, Filter, MapPin } from "lucide-react";
import { getProjectDetailData } from "@/lib/project-detail/data";
import { compactStatus, statusStyle } from "@/lib/status";
import type {
  EvidenceSummary,
  FieldClass,
  ProjectDetailSection,
  ProjectEvidenceFilterOption,
  ProjectEvidenceFilters,
  ProjectEvidenceRow,
  ProjectField,
  SourceBadge
} from "@/lib/project-detail/types";
import { cn } from "@/lib/utils";

export const dynamic = "force-dynamic";

type ProjectDetailPageProps = {
  params: Promise<{ projectId: string }>;
  searchParams: Promise<{
    tab?: string;
    field?: string;
    source?: string;
    from?: string;
    to?: string;
  }>;
};

type ProjectDetailTab = "snapshot" | "evidence";
type EvidenceQuery = {
  field: string | null;
  source: string | null;
  from: string | null;
  to: string | null;
};

const SOURCE_TONES: Record<SourceBadge["tone"], string> = {
  gov: "border-green-200 bg-green-50 text-green-800",
  news: "border-amber-200 bg-amber-50 text-amber-900",
  costar: "border-purple-200 bg-purple-50 text-purple-800",
  pipedream: "border-teal-200 bg-teal-50 text-teal-800",
  user: "border-blue-200 bg-blue-50 text-blue-800",
  web: "border-slate-200 bg-slate-50 text-slate-700",
  system: "border-gray-200 bg-gray-50 text-gray-700",
  source: "border-slate-200 bg-slate-50 text-slate-700",
  none: "border-slate-200 bg-white text-slate-400"
};

const CLASS_LABELS: Record<FieldClass, string> = {
  evidence: "Evidence",
  source: "Source fact",
  researcher: "TCG",
  relationship: "Link",
  computed: "System"
};

const CLASS_TONES: Record<FieldClass, string> = {
  evidence: "border-teal-200 bg-teal-50 text-teal-800",
  source: "border-slate-200 bg-slate-50 text-slate-700",
  researcher: "border-blue-200 bg-blue-50 text-blue-800",
  relationship: "border-indigo-200 bg-indigo-50 text-indigo-800",
  computed: "border-gray-200 bg-gray-50 text-gray-700"
};

function formatDate(value: string | null) {
  if (!value) {
    return "-";
  }

  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric"
  }).format(new Date(value));
}

function sourceBadgeTitle(badge: SourceBadge) {
  return [badge.sourceType ?? (badge.label === "Unlinked" ? "No linked resolution evidence yet" : null), badge.date ? formatDate(badge.date) : null]
    .filter(Boolean)
    .join(" | ");
}

function normalizeTab(value: string | undefined): ProjectDetailTab {
  return value === "evidence" ? "evidence" : "snapshot";
}

function normalizeQueryValue(value: string | undefined) {
  return value && value.trim() ? value.trim() : null;
}

function normalizeDateQueryValue(value: string | undefined) {
  const normalized = normalizeQueryValue(value);
  return normalized && /^\d{4}-\d{2}-\d{2}$/.test(normalized) ? normalized : null;
}

function evidenceDateKey(evidence: ProjectEvidenceRow) {
  return String(evidence.evidenceDate ?? evidence.collectedAt).slice(0, 10);
}

function evidenceMonthLabel(evidence: ProjectEvidenceRow) {
  const value = evidence.evidenceDate ?? evidence.collectedAt;
  return new Intl.DateTimeFormat("en-US", {
    month: "long",
    year: "numeric"
  }).format(new Date(value));
}

function filterEvidenceRows(
  evidenceRows: ProjectEvidenceRow[],
  filters: EvidenceQuery
) {
  return evidenceRows.filter((evidence) => {
    if (filters.field && !evidence.linkedFields.some((field) => field.value === filters.field)) {
      return false;
    }
    if (filters.source && evidence.sourceType !== filters.source) {
      return false;
    }
    const dateKey = evidenceDateKey(evidence);
    if (filters.from && dateKey < filters.from) {
      return false;
    }
    if (filters.to && dateKey > filters.to) {
      return false;
    }
    return true;
  });
}

function groupEvidenceByMonth(evidenceRows: ProjectEvidenceRow[]) {
  const groups: Array<{ month: string; rows: ProjectEvidenceRow[] }> = [];

  for (const evidence of evidenceRows) {
    const month = evidenceMonthLabel(evidence);
    const last = groups.at(-1);
    if (last?.month === month) {
      last.rows.push(evidence);
    } else {
      groups.push({ month, rows: [evidence] });
    }
  }

  return groups;
}

function safeExternalUrl(value: string | null) {
  if (!value) {
    return null;
  }

  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? value : null;
  } catch {
    return null;
  }
}

function prettyJson(value: Record<string, unknown> | null) {
  return value ? JSON.stringify(value, null, 2) : "{}";
}

function displayEvidenceFieldValue(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  if (typeof value === "object" && !Array.isArray(value) && "value" in (value as Record<string, unknown>)) {
    return displayEvidenceFieldValue((value as Record<string, unknown>).value);
  }
  if (Array.isArray(value)) {
    return value.map((item) => displayEvidenceFieldValue(item)).join(", ");
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

function displayRawFieldKey(value: string) {
  return value
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
    .replace(/\bSf\b/g, "SF")
    .replace(/\bBr\b/g, "BR")
    .replace(/\bUrl\b/g, "URL")
    .replace(/\bId\b/g, "ID")
    .replace(/\bPcis\b/g, "PCIS")
    .replace(/\bLadbs\b/g, "LADBS")
    .replace(/\bLahd\b/g, "LAHD")
    .replace(/\bZimas\b/g, "ZIMAS")
    .replace(/\bCostar\b/g, "CoStar");
}

function withActiveOption(
  options: ProjectEvidenceFilterOption[],
  value: string | null,
  fallbackLabel: (value: string) => string
) {
  if (!value || options.some((option) => option.value === value)) {
    return options;
  }

  return [{ value, label: fallbackLabel(value) }, ...options];
}

export default async function ProjectDetailPage({ params, searchParams }: ProjectDetailPageProps) {
  const { projectId } = await params;
  const query = await searchParams;
  const result = await getProjectDetailData(projectId);

  if (result.notFound) {
    notFound();
  }

  if (result.error || !result.data) {
    return (
      <main className="px-5 py-6">
        <div className="flex max-w-2xl items-start gap-3 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <div>
            <p className="font-medium">Could not load Project Detail.</p>
            <p>{result.error ?? "Unknown error"}</p>
          </div>
        </div>
      </main>
    );
  }

  const { project, sections, evidenceRows, evidenceFilters } = result.data;
  const activeTab = normalizeTab(query.tab);
  const evidenceQuery = {
    field: normalizeQueryValue(query.field),
    source: normalizeQueryValue(query.source),
    from: normalizeDateQueryValue(query.from),
    to: normalizeDateQueryValue(query.to)
  };
  const filteredEvidenceRows = filterEvidenceRows(evidenceRows, evidenceQuery);

  return (
    <main className="px-5 py-5">
      <div className="mb-4">
        <Link
          className="inline-flex items-center gap-2 rounded-md px-2 py-1 text-sm text-slate-600 hover:bg-slate-100 hover:text-slate-950"
          href="/pipeline"
        >
          <ArrowLeft className="size-4" aria-hidden="true" />
          Pipeline
        </Link>
      </div>

      <div className="border-b border-slate-200 pb-4">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-xl font-semibold tracking-normal text-slate-950">{project.name}</h1>
              <span className={cn("inline-flex rounded border px-1.5 py-0.5 text-xs", statusStyle(project.status).className)}>
                {compactStatus(project.status)}
              </span>
            </div>
            <p className="mt-1 text-sm text-slate-600">{project.canonicalAddress}</p>
            <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-slate-500">
              <span className="inline-flex items-center gap-1">
                <MapPin className="size-3.5" aria-hidden="true" />
                {[project.jurisdiction, project.city, project.state, project.zip].filter(Boolean).join(", ")}
              </span>
              <span>{project.market}</span>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <HeaderMetric label="Confidence" value={project.confidence ?? "-"} />
            <HeaderMetric label="Evidence" value={String(project.evidenceCount)} />
            <HeaderMetric label="Last evidence" value={formatDate(project.lastEvidenceDate)} />
            <HeaderMetric label="Open review" value={String(project.openReviewCount)} />
          </div>
        </div>

        <div className="mt-4 flex flex-wrap gap-2 border-t border-slate-200 pt-3" role="tablist" aria-label="Project detail tabs">
          <DetailTabLink active={activeTab === "snapshot"} href={`/pipeline/${project.id}`} label="Snapshot" />
          <DetailTabLink active={activeTab === "evidence"} href={`/pipeline/${project.id}?tab=evidence`} label="Evidence" />
          {["Resolution", "Changes", "Overrides"].map((tab) => (
            <button
              aria-disabled="true"
              aria-selected="false"
              className="cursor-not-allowed rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-400"
              key={tab}
              role="tab"
              title={`${tab} tab is scheduled later in Phase B.`}
              type="button"
            >
              {tab}
            </button>
          ))}
        </div>
      </div>

      {activeTab === "evidence" ? (
        <EvidenceTab
          evidenceFilters={evidenceFilters}
          evidenceQuery={evidenceQuery}
          evidenceRows={filteredEvidenceRows}
          projectId={project.id}
          totalEvidenceRows={evidenceRows.length}
        />
      ) : (
        <SnapshotTab projectId={project.id} sections={sections} />
      )}
    </main>
  );
}

function DetailTabLink({ active, href, label }: { active: boolean; href: string; label: string }) {
  return (
    <Link
      aria-selected={active}
      className={cn(
        "rounded-md px-3 py-1.5 text-sm font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700",
        active
          ? "bg-teal-700 text-white"
          : "border border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:bg-slate-50 hover:text-slate-950"
      )}
      href={href}
      role="tab"
    >
      {label}
    </Link>
  );
}

function SnapshotTab({ projectId, sections }: { projectId: string; sections: ProjectDetailSection[] }) {
  return (
    <div className="mt-5 grid gap-5 2xl:grid-cols-[minmax(0,1fr)_22rem]">
      <div className="space-y-5">
        {sections.map((section) => (
          <section className="rounded-md border border-slate-200 bg-white" key={section.id}>
            <div className="border-b border-slate-200 px-4 py-3">
              <h2 className="text-sm font-semibold text-slate-950">{section.title}</h2>
              <p className="mt-0.5 text-xs text-slate-500">{section.description}</p>
            </div>
            <div className="divide-y divide-slate-100">
              {section.fields.map((field) => (
                <FieldRow field={field} key={field.key} projectId={projectId} />
              ))}
            </div>
          </section>
        ))}
      </div>

      <aside className="h-fit rounded-md border border-slate-200 bg-white p-4">
        <h2 className="text-sm font-semibold text-slate-950">Snapshot Legend</h2>
        <div className="mt-3 space-y-3 text-sm">
          <LegendItem className="bg-amber-50 text-amber-900" label="In review batch" />
          <LegendItem className="bg-white text-slate-700" label="Unchanged" />
          <LegendItem className="bg-slate-50 text-slate-700" label="Read-only in Phase B" />
        </div>
        <div className="mt-4 border-t border-slate-200 pt-3">
          <p className="text-xs font-medium uppercase text-slate-500">Source badges</p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {(["gov", "news", "costar", "pipedream", "user", "system"] as SourceBadge["tone"][]).map((tone) => (
              <span className={cn("rounded border px-1.5 py-0.5 text-[11px]", SOURCE_TONES[tone])} key={tone}>
                {tone}
              </span>
            ))}
          </div>
        </div>
      </aside>
    </div>
  );
}

function EvidenceTab({
  evidenceFilters,
  evidenceQuery,
  evidenceRows,
  projectId,
  totalEvidenceRows
}: {
  evidenceFilters: ProjectEvidenceFilters;
  evidenceQuery: EvidenceQuery;
  evidenceRows: ProjectEvidenceRow[];
  projectId: string;
  totalEvidenceRows: number;
}) {
  const groupedRows = groupEvidenceByMonth(evidenceRows);
  const hasActiveFilter = Boolean(evidenceQuery.field || evidenceQuery.source || evidenceQuery.from || evidenceQuery.to);

  return (
    <div className="mt-5 grid gap-5 xl:grid-cols-[minmax(0,1fr)_20rem]">
      <section className="rounded-md border border-slate-200 bg-white">
        <div className="flex flex-col gap-3 border-b border-slate-200 px-4 py-3 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h2 className="text-sm font-semibold text-slate-950">Evidence</h2>
            <p className="mt-0.5 text-xs text-slate-500">
              {evidenceRows.length} of {totalEvidenceRows} rows shown. Rows are sorted by evidence date, then collection time.
            </p>
          </div>
          <Link
            className="inline-flex items-center gap-1.5 rounded-md border border-slate-200 px-2.5 py-1.5 text-xs font-medium text-slate-600 hover:border-slate-300 hover:bg-slate-50 hover:text-slate-950"
            href={`/pipeline/${projectId}?tab=evidence`}
          >
            Clear filters
          </Link>
        </div>

        {groupedRows.length ? (
          <div className="divide-y divide-slate-200">
            {groupedRows.map((group) => (
              <div key={group.month}>
                <div className="bg-slate-50 px-4 py-2 text-xs font-semibold uppercase tracking-normal text-slate-500">
                  {group.month}
                </div>
                <div className="divide-y divide-slate-100">
                  {group.rows.map((evidence) => (
                    <EvidenceTimelineRow evidence={evidence} key={evidence.id} />
                  ))}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="px-4 py-10 text-center text-sm text-slate-500">
            {hasActiveFilter ? "No evidence rows match these filters." : "No evidence rows are linked to this project yet."}
          </div>
        )}
      </section>

      <aside className="h-fit rounded-md border border-slate-200 bg-white p-4">
        <div className="flex items-center gap-2">
          <Filter className="size-4 text-slate-500" aria-hidden="true" />
          <h2 className="text-sm font-semibold text-slate-950">Filters</h2>
        </div>
        <form action={`/pipeline/${projectId}`} className="mt-4 space-y-3">
          <input name="tab" type="hidden" value="evidence" />
          <FilterSelect
            label="Field"
            name="field"
            options={withActiveOption(evidenceFilters.fields, evidenceQuery.field, displayRawFieldKey)}
            value={evidenceQuery.field}
          />
          <FilterSelect
            label="Source"
            name="source"
            options={withActiveOption(evidenceFilters.sources, evidenceQuery.source, displayRawFieldKey)}
            value={evidenceQuery.source}
          />
          <label className="block">
            <span className="text-xs font-medium text-slate-600">From</span>
            <input
              className="mt-1 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm text-slate-900 focus:border-teal-600 focus:outline-none"
              defaultValue={evidenceQuery.from ?? ""}
              name="from"
              type="date"
            />
          </label>
          <label className="block">
            <span className="text-xs font-medium text-slate-600">To</span>
            <input
              className="mt-1 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm text-slate-900 focus:border-teal-600 focus:outline-none"
              defaultValue={evidenceQuery.to ?? ""}
              name="to"
              type="date"
            />
          </label>
          <button
            className="w-full rounded-md bg-teal-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-teal-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700"
            type="submit"
          >
            Apply
          </button>
        </form>
        <p className="mt-4 border-t border-slate-200 pt-3 text-xs text-slate-500">
          B.5 uses a generic snippet renderer. Source-specific snippets and suspect-row writes are scheduled after the read-only tabs.
        </p>
      </aside>
    </div>
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
  options: ProjectEvidenceFilterOption[];
  value: string | null;
}) {
  return (
    <label className="block">
      <span className="text-xs font-medium text-slate-600">{label}</span>
      <select
        className="mt-1 w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 text-sm text-slate-900 focus:border-teal-600 focus:outline-none"
        defaultValue={value ?? ""}
        name={name}
      >
        <option value="">Any</option>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function EvidenceTimelineRow({ evidence }: { evidence: ProjectEvidenceRow }) {
  const sourceUrl = safeExternalUrl(evidence.sourceUrl);
  const displayFields = evidence.displayFields.length ? evidence.displayFields : ["Raw observation"];
  const fieldSummary = displayFields.slice(0, 5).join(" / ");
  const extraFieldCount = Math.max(0, displayFields.length - 5);
  const rawJsonCount = Number(Boolean(evidence.rawData)) + Number(Boolean(evidence.signalFlags));

  return (
    <details className="group px-4 py-3">
      <summary
        className="grid cursor-pointer list-none gap-3 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700 md:grid-cols-[5rem_8rem_minmax(0,1fr)_auto] md:items-start"
        tabIndex={0}
      >
        <span className="font-medium text-slate-700">{formatDate(evidence.evidenceDate ?? evidence.collectedAt)}</span>
        <span className={cn("w-fit rounded border px-1.5 py-0.5 text-[11px]", SOURCE_TONES[evidence.sourceBadge.tone])}>
          {evidence.sourceBadge.label}
        </span>
        <span className="min-w-0">
          <span className="block font-medium text-slate-950">
            {fieldSummary}
            {extraFieldCount ? ` +${extraFieldCount}` : ""}
          </span>
          <span className="mt-0.5 block truncate text-slate-500">
            {evidence.teaser ?? evidence.sourceRecordId ?? evidence.sourceType}
          </span>
        </span>
        <span className="text-xs text-slate-400 group-open:hidden">Expand</span>
        <span className="hidden text-xs text-slate-400 group-open:block">Collapse</span>
      </summary>

      <div className="mt-3 grid gap-3 rounded-md border border-slate-200 bg-slate-50 p-3 lg:grid-cols-[minmax(0,1fr)_18rem]">
        <div>
          <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
            <span>{evidence.sourceLabel}</span>
            <span>Tier {evidence.sourceTier}</span>
            <span>{evidence.ingestMethod}</span>
            {evidence.sourceRecordId ? <span>Record {evidence.sourceRecordId}</span> : null}
          </div>
          <p className="mt-2 text-sm text-slate-700">{evidence.teaser ?? "No text snippet available yet."}</p>
          {sourceUrl ? (
            <a
              className="mt-2 inline-flex items-center gap-1.5 text-xs font-medium text-teal-700 hover:text-teal-900"
              href={sourceUrl}
              rel="noreferrer"
              target="_blank"
            >
              Source URL
              <ExternalLink className="size-3" aria-hidden="true" />
            </a>
          ) : null}
        </div>

        <div className="rounded-md border border-slate-200 bg-white p-3">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-normal text-slate-500">
            <FileJson className="size-3.5" aria-hidden="true" />
            Extracted fields
          </div>
          <dl className="mt-2 space-y-1.5 text-xs">
            {Object.entries(evidence.extractedFields ?? {}).length ? (
              Object.entries(evidence.extractedFields ?? {})
                .slice(0, 12)
                .map(([key, value]) => (
                  <div className="grid grid-cols-[7rem_minmax(0,1fr)] gap-2" key={key}>
                    <dt className="truncate text-slate-500" title={key}>
                      {displayRawFieldKey(key)}
                    </dt>
                    <dd className="break-words font-medium text-slate-800">{displayEvidenceFieldValue(value)}</dd>
                  </div>
                ))
            ) : (
              <p className="text-slate-500">No extracted fields.</p>
            )}
          </dl>
        </div>

        <details className="group/raw-json lg:col-span-2">
          <summary className="inline-flex cursor-pointer list-none items-center gap-1.5 text-xs font-medium text-slate-600 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700">
            <ChevronRight className="size-3 transition-transform group-open/raw-json:rotate-90" aria-hidden="true" />
            Raw JSON
            <span className="rounded border border-slate-200 bg-white px-1.5 py-0.5 text-[11px] text-slate-500">
              {rawJsonCount} blocks
            </span>
          </summary>
          <div className="mt-2 grid gap-3 lg:grid-cols-2">
            <JsonBlock label="raw_data" value={evidence.rawData} />
            <JsonBlock label="signal_flags" value={evidence.signalFlags} />
          </div>
        </details>
      </div>
    </details>
  );
}

function JsonBlock({ label, value }: { label: string; value: Record<string, unknown> | null }) {
  return (
    <div>
      <p className="mb-1 text-xs font-medium text-slate-500">{label}</p>
      <pre className="max-h-80 overflow-auto rounded-md border border-slate-200 bg-white p-3 text-[11px] leading-relaxed text-slate-700">
        {prettyJson(value)}
      </pre>
    </div>
  );
}

function HeaderMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-32 rounded-md border border-slate-200 bg-white px-3 py-2">
      <p className="text-xs text-slate-500">{label}</p>
      <p className="mt-1 text-sm font-semibold text-slate-950">{value}</p>
    </div>
  );
}

function FieldRow({ field, projectId }: { field: ProjectField; projectId: string }) {
  const sourceBadge = (
    <span
      className={cn("rounded border px-1.5 py-0.5 text-[11px]", SOURCE_TONES[field.provenance.sourceBadge.tone])}
      title={sourceBadgeTitle(field.provenance.sourceBadge)}
    >
      {field.provenance.sourceBadge.label}
    </span>
  );

  return (
    <div
      className={cn(
        "group relative grid gap-2 px-4 py-3 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-inset focus-visible:outline-teal-700 md:grid-cols-[12rem_minmax(0,1fr)_auto]",
        field.state === "review" && "bg-amber-50/70"
      )}
      tabIndex={0}
    >
      <div className="flex min-w-0 items-center gap-2">
        {field.state === "review" ? (
          <Clock className="size-3.5 shrink-0 text-amber-700" aria-hidden="true" />
        ) : (
          <Circle className="size-2 shrink-0 fill-slate-300 text-slate-300" aria-hidden="true" />
        )}
        <p className="truncate font-medium text-slate-700">{field.label}</p>
      </div>
      <div className="min-w-0">
        <p className="break-words font-medium text-slate-950">{field.value}</p>
      </div>
      <div className="flex flex-wrap items-center gap-1.5 md:justify-end">
        <span className={cn("rounded border px-1.5 py-0.5 text-[11px]", CLASS_TONES[field.fieldClass])}>
          {CLASS_LABELS[field.fieldClass]}
        </span>
        {field.fieldClass === "evidence" || field.fieldClass === "source" ? (
          <Link
            className="rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700"
            href={`/pipeline/${projectId}?tab=evidence&field=${encodeURIComponent(field.key)}`}
          >
            {sourceBadge}
          </Link>
        ) : (
          sourceBadge
        )}
      </div>
      <EvidencePopover field={field} />
    </div>
  );
}

function EvidencePopover({ field }: { field: ProjectField }) {
  return (
    <div className="pointer-events-auto absolute right-3 top-10 z-30 hidden w-[min(28rem,calc(100vw-3rem))] rounded-md border border-slate-200 bg-white p-3 text-xs shadow-xl group-hover:block group-focus-within:block">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-semibold text-slate-950">{field.label}</p>
          <p className="mt-0.5 text-slate-600">Value: {field.value}</p>
        </div>
        <span className={cn("rounded border px-1.5 py-0.5", SOURCE_TONES[field.provenance.sourceBadge.tone])}>
          {field.provenance.sourceBadge.label}
        </span>
      </div>
      <div className="mt-3 space-y-1 text-slate-600">
        {field.note ? <p>{field.note}</p> : null}
        {field.provenance.rule ? <p>Rule: {field.provenance.rule}</p> : null}
        {field.provenance.confidence ? <p>Confidence: {field.provenance.confidence}</p> : null}
        <p>Supporting: {field.provenance.evidence.length} evidence rows</p>
      </div>
      {field.provenance.evidence.length ? (
        <div className="mt-3 space-y-2">
          {field.provenance.evidence.slice(0, 3).map((evidence) => (
            <EvidenceLine evidence={evidence} key={evidence.id} />
          ))}
        </div>
      ) : (
        <p className="mt-3 text-slate-500">No supporting evidence row is linked yet.</p>
      )}
    </div>
  );
}

function EvidenceLine({ evidence }: { evidence: EvidenceSummary }) {
  return (
    <div className="rounded border border-slate-100 bg-slate-50 p-2">
      <div className="flex items-center justify-between gap-2">
        <p className="font-medium text-slate-800">{evidence.sourceType}</p>
        <p className="text-slate-500">{formatDate(evidence.evidenceDate ?? evidence.collectedAt)}</p>
      </div>
      <p className="mt-1 line-clamp-2 text-slate-600">{evidence.teaser ?? (evidence.fields.join(", ") || "No snippet")}</p>
    </div>
  );
}

function LegendItem({ className, label }: { className: string; label: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className={cn("size-4 rounded border border-slate-200", className)} />
      <span className="text-slate-600">{label}</span>
    </div>
  );
}

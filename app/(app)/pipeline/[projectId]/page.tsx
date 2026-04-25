import Link from "next/link";
import { notFound } from "next/navigation";
import { AlertCircle, ArrowLeft, Circle, Clock, MapPin } from "lucide-react";
import { getProjectDetailData } from "@/lib/project-detail/data";
import type { EvidenceSummary, FieldClass, ProjectField, SourceBadge } from "@/lib/project-detail/types";
import { cn } from "@/lib/utils";

export const dynamic = "force-dynamic";

type ProjectDetailPageProps = {
  params: Promise<{ projectId: string }>;
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

function compactStatus(status: string) {
  return status === "Under Construction" ? "U/C" : status;
}

function sourceBadgeTitle(badge: SourceBadge) {
  return [badge.sourceType, badge.date ? formatDate(badge.date) : null].filter(Boolean).join(" | ");
}

export default async function ProjectDetailPage({ params }: ProjectDetailPageProps) {
  const { projectId } = await params;
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

  const { project, sections } = result.data;

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
              <span className="inline-flex rounded border border-slate-200 bg-white px-1.5 py-0.5 text-xs text-slate-700">
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

        <div className="mt-4 flex flex-wrap gap-2 border-t border-slate-200 pt-3">
          <span className="rounded-md bg-teal-700 px-3 py-1.5 text-sm font-medium text-white">Snapshot</span>
          {["Evidence", "Resolution", "Changes", "Overrides"].map((tab) => (
            <span
              className="rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-400"
              key={tab}
              title={`${tab} tab is scheduled later in Phase B.`}
            >
              {tab}
            </span>
          ))}
        </div>
      </div>

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
                  <FieldRow field={field} key={field.key} />
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
    </main>
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

function FieldRow({ field }: { field: ProjectField }) {
  return (
    <div
      className={cn(
        "group relative grid gap-2 px-4 py-3 text-sm md:grid-cols-[12rem_minmax(0,1fr)_auto]",
        field.state === "review" && "bg-amber-50/70"
      )}
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
        {field.note ? <p className="mt-1 text-xs text-slate-500">{field.note}</p> : null}
      </div>
      <div className="flex flex-wrap items-center gap-1.5 md:justify-end">
        <span className={cn("rounded border px-1.5 py-0.5 text-[11px]", CLASS_TONES[field.fieldClass])}>
          {CLASS_LABELS[field.fieldClass]}
        </span>
        <span
          className={cn("rounded border px-1.5 py-0.5 text-[11px]", SOURCE_TONES[field.provenance.sourceBadge.tone])}
          title={sourceBadgeTitle(field.provenance.sourceBadge)}
        >
          {field.provenance.sourceBadge.label}
        </span>
      </div>
      <EvidencePopover field={field} />
    </div>
  );
}

function EvidencePopover({ field }: { field: ProjectField }) {
  return (
    <div className="pointer-events-none absolute right-3 top-10 z-30 hidden w-[min(28rem,calc(100vw-3rem))] rounded-md border border-slate-200 bg-white p-3 text-xs shadow-xl group-hover:block group-focus-within:block">
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

"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AlertCircle,
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  ChevronDown,
  Check,
  Clock,
  ExternalLink,
  GitCompareArrows,
  Newspaper,
  RotateCcw,
  Save,
  Star
} from "lucide-react";
import { useEffect, useMemo, useState, useTransition } from "react";
import {
  stageReviewDecisionAction,
  unstageReviewDecisionAction
} from "@/app/(app)/review/actions";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  acceptDecisionValue,
  asString,
  currentValueForItem,
  dissentingEvidenceForItem,
  displayActor,
  fieldNameForItem,
  flattenPayload,
  formatDate,
  formatDateTime,
  formatInputValue,
  formatValue,
  humanize,
  isStagedByMe,
  isStagedByOther,
  newsContextForItem,
  proposedValueForItem,
  sourceTextForItem,
  supportingEvidenceForItem,
  type NewsContext,
  warningForItem
} from "@/lib/review/payload";
import { compactStatus, statusStyle } from "@/lib/status";
import { cn } from "@/lib/utils";
import type {
  ReviewItemDetailData,
  ReviewEvidenceSummary,
  ReviewProcessedChange,
  ReviewProjectSummary,
  ReviewQueueItem
} from "@/lib/review/types";

type ReviewItemDetailClientProps = {
  data: ReviewItemDetailData;
  currentUserId: string;
  currentUserEmail: string | null;
};

type Banner = {
  tone: "success" | "error";
  message: string;
} | null;

export function ReviewItemDetailClient({
  data,
  currentUserId,
  currentUserEmail
}: ReviewItemDetailClientProps) {
  const router = useRouter();
  const { item, project, candidateProjects, sourceRun, navigation, processedChanges } = data;
  const [customValue, setCustomValue] = useState(formatInputValue(proposedValueForItem(item)));
  const [notes, setNotes] = useState(item.activeDecision?.decisionNotes ?? "");
  const [sourceUrl, setSourceUrl] = useState(item.activeDecision?.sourceUrl ?? "");
  const [banner, setBanner] = useState<Banner>(null);
  const [isPending, startTransition] = useTransition();
  const stagedDecision = item.activeDecision?.state === "staged" ? item.activeDecision : null;
  const stagedByMe = isStagedByMe(item, currentUserId, currentUserEmail);
  const stagedByOther = isStagedByOther(item, currentUserId, currentUserEmail);
  const fieldName = fieldNameForItem(item);
  const canUseCustom = item.itemType !== "new_candidate" && item.itemType !== "possible_match";
  const hasSingleCandidate = item.itemType === "possible_match" && candidateProjects.length === 1;
  const acceptLabel = item.itemType === "new_candidate" ? "Create project" : "Accept new";
  const sourceLabel = sourceRun
    ? `${sourceRun.sourceName} - ${formatDate(sourceRun.finishedAt ?? sourceRun.runTimestamp)}`
    : sourceTextForItem(item);
  const newsContext = newsContextForItem(item);
  const supportingEvidence = supportingEvidenceForItem(item);
  const dissentingEvidence = dissentingEvidenceForItem(item);
  const payloadRows = useMemo(() => flattenPayload(item.payload), [item.payload]);
  const queueHref = reviewQueueHref(navigation.jurisdictionId);
  const previousHref = navigation.previousItemId
    ? reviewItemHref(navigation.previousItemId, navigation.jurisdictionId)
    : null;
  const nextHref = navigation.nextItemId
    ? reviewItemHref(navigation.nextItemId, navigation.jurisdictionId)
    : null;

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (
        target?.tagName === "INPUT" ||
        target?.tagName === "TEXTAREA" ||
        target?.tagName === "SELECT"
      ) {
        return;
      }
      if (event.key === "[" && previousHref) {
        event.preventDefault();
        router.push(previousHref);
      }
      if (event.key === "]" && nextHref) {
        event.preventDefault();
        router.push(nextHref);
      }
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [nextHref, previousHref, router]);

  function stageDecision(decisionType: string, decisionValue?: unknown) {
    if (stagedByOther) {
      return;
    }
    setBanner(null);
    startTransition(async () => {
      const result = await stageReviewDecisionAction({
        reviewItemId: item.id,
        decisionType,
        decisionValue,
        notes: notes.trim() || null,
        sourceUrl: sourceUrl.trim() || null,
        revise: stagedByMe
      });
      setBanner({ tone: result.ok ? "success" : "error", message: result.message });
      if (result.ok) {
        router.refresh();
      }
    });
  }

  function unstageDecision() {
    if (!stagedByMe) {
      return;
    }
    setBanner(null);
    startTransition(async () => {
      const result = await unstageReviewDecisionAction(item.id);
      setBanner({ tone: result.ok ? "success" : "error", message: result.message });
      if (result.ok) {
        router.refresh();
      }
    });
  }

  return (
    <main className="pb-12">
      <div className="border-b border-slate-200 bg-white px-5 py-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <Link className="text-sm font-medium text-teal-700 hover:text-teal-900" href={queueHref}>
              Back to Review Queue
            </Link>
            <p className="mt-3 text-xs font-medium uppercase tracking-normal text-slate-500">
              Review Item
            </p>
            <h1 className="mt-1 text-xl font-semibold tracking-normal text-slate-950">
              {fieldLabel(fieldName)}
            </h1>
          </div>
          <div className="flex flex-col gap-2 lg:items-end">
            <div className="flex flex-wrap gap-2 text-xs">
              <span className={priorityBadgeClass(item.priority)}>{humanize(item.priority)}</span>
              <span className="rounded border border-slate-200 bg-slate-50 px-2 py-1 text-slate-600">
                {humanize(item.itemType)}
              </span>
              <span className="rounded border border-slate-200 bg-slate-50 px-2 py-1 text-slate-600">
                {humanize(item.state)}
              </span>
            </div>
            <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
              <span>
                {navigation.position !== null
                  ? `${navigation.position.toLocaleString()} of ${navigation.total.toLocaleString()}`
                  : `${navigation.total.toLocaleString()} active items`}
              </span>
              <NavigationLink href={previousHref} label="Previous" keycap="[" direction="previous" />
              <NavigationLink href={nextHref} label="Next" keycap="]" direction="next" />
            </div>
          </div>
        </div>
        {banner ? (
          <div
            className={cn(
              "mt-3 flex items-start gap-2 rounded-md border px-3 py-2 text-sm",
              banner.tone === "success"
                ? "border-teal-200 bg-teal-50 text-teal-900"
                : "border-red-200 bg-red-50 text-red-800"
            )}
          >
            {banner.tone === "success" ? (
              <Check className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            ) : (
              <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            )}
            <p>{banner.message}</p>
          </div>
        ) : null}
      </div>

      <div className="grid gap-4 px-5 py-5 xl:grid-cols-[minmax(0,1.35fr)_minmax(22rem,0.65fr)]">
        <div className="space-y-4">
          <section className="rounded-md border border-slate-200 bg-white p-4">
            <div className="mb-3 flex items-center gap-2">
              <GitCompareArrows className="size-4 text-slate-500" aria-hidden="true" />
              <h2 className="text-sm font-semibold text-slate-950">Decision Context</h2>
            </div>
            <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] lg:items-stretch">
              <ValuePanel label="Current" value={currentValueForItem(item)} />
              <ArrowRight className="hidden size-4 self-center text-slate-300 lg:block" aria-hidden="true" />
              <ValuePanel label="Proposed" value={proposedValueForItem(item)} />
            </div>
            {warningForItem(item) ? (
              <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                {warningForItem(item)}
              </div>
            ) : null}
          </section>

          {supportingEvidence.length || dissentingEvidence.length ? (
            <section className="rounded-md border border-slate-200 bg-white p-4">
              <h2 className="text-sm font-semibold text-slate-950">Evidence Context</h2>
              <div className="mt-3 grid gap-3 lg:grid-cols-2">
                <EvidenceDetailSection label="Supporting" rows={supportingEvidence} defaultOpen />
                <EvidenceDetailSection label="Against" rows={dissentingEvidence} defaultOpen />
              </div>
            </section>
          ) : null}

          {item.itemType === "possible_match" && candidateProjects.length ? (
            <section className="rounded-md border border-slate-200 bg-white p-4">
              <h2 className="text-sm font-semibold text-slate-950">Candidate Projects</h2>
              <div className="mt-3 grid gap-3 lg:grid-cols-2">
                {candidateProjects.map((candidate) => (
                  <CandidateProjectCard
                    key={candidate.id}
                    project={candidate}
                    disabled={isPending || stagedByOther}
                    onAccept={() => stageDecision("accept_new", { project_id: candidate.id })}
                  />
                ))}
              </div>
            </section>
          ) : null}

          <section className="rounded-md border border-slate-200 bg-white p-4">
            <h2 className="text-sm font-semibold text-slate-950">Stage Decision</h2>
            {stagedDecision ? (
              <div className="mt-3 rounded-md border border-sky-200 bg-sky-50 px-3 py-2 text-sm text-sky-900">
                Staged {humanize(stagedDecision.decisionType ?? "decision")} by{" "}
                {isStagedByMe(item, currentUserId, currentUserEmail)
                  ? "you"
                  : displayActor(stagedDecision.stagedByEmail, stagedDecision.stagedBy)}
                {stagedDecision.stagedAt ? ` on ${formatDateTime(stagedDecision.stagedAt)}` : ""}.
              </div>
            ) : null}
            <div className="mt-4 flex flex-wrap gap-2">
              <Button
                type="button"
                onClick={() => stageDecision("accept_new", acceptDecisionValue(item))}
                disabled={isPending || stagedByOther || !canAcceptItem(item, candidateProjects)}
              >
                <Check className="size-4" aria-hidden="true" />
                {hasSingleCandidate ? "Accept match" : acceptLabel}
              </Button>
              <Button
                type="button"
                variant="outline"
                onClick={() => stageDecision("keep_old")}
                disabled={isPending || stagedByOther}
              >
                Keep old
              </Button>
              <Button
                type="button"
                variant="outline"
                onClick={() => stageDecision("defer")}
                disabled={isPending || stagedByOther}
              >
                <Clock className="size-4" aria-hidden="true" />
                Defer
              </Button>
              {stagedByMe ? (
                <Button type="button" variant="ghost" onClick={unstageDecision} disabled={isPending}>
                  <RotateCcw className="size-4" aria-hidden="true" />
                  Unstage
                </Button>
              ) : null}
            </div>

            {canUseCustom ? (
              <div className="mt-5 border-t border-slate-200 pt-4">
                <h3 className="text-sm font-semibold text-slate-950">Custom Value</h3>
                <div className="mt-3 grid gap-3">
                  <label className="text-sm">
                    <span className="mb-1 block text-xs font-medium text-slate-600">Value</span>
                    <Input value={customValue} onChange={(event) => setCustomValue(event.target.value)} />
                  </label>
                  <label className="text-sm">
                    <span className="mb-1 block text-xs font-medium text-slate-600">Notes</span>
                    <textarea
                      value={notes}
                      onChange={(event) => setNotes(event.target.value)}
                      className="min-h-24 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-950 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-100"
                    />
                  </label>
                  <label className="text-sm">
                    <span className="mb-1 block text-xs font-medium text-slate-600">Source URL</span>
                    <Input value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} />
                  </label>
                  <div>
                    <Button
                      type="button"
                      disabled={isPending || stagedByOther || !customValue.trim()}
                      onClick={() => stageDecision("custom", { value: customValue.trim() })}
                    >
                      <Save className="size-4" aria-hidden="true" />
                      Stage custom
                    </Button>
                  </div>
                </div>
              </div>
            ) : null}
          </section>

          <ProcessedChangesSection changes={processedChanges} />

          <section className="rounded-md border border-slate-200 bg-white p-4">
            <h2 className="text-sm font-semibold text-slate-950">Review Payload</h2>
            <div className="mt-3 overflow-hidden rounded-md border border-slate-200">
              {payloadRows.length ? (
                payloadRows.map((row) => (
                  <div
                    key={row.key}
                    className="grid gap-2 border-b border-slate-100 px-3 py-2 text-sm last:border-b-0 md:grid-cols-[14rem_minmax(0,1fr)]"
                  >
                    <p className="text-slate-500">{fieldLabel(row.key)}</p>
                    <p className="break-words text-slate-950">{row.value}</p>
                  </div>
                ))
              ) : (
                <p className="px-3 py-2 text-sm text-slate-500">No payload fields.</p>
              )}
            </div>
          </section>
        </div>

        <aside className="space-y-4">
          <section className="rounded-md border border-slate-200 bg-white p-4">
            <h2 className="text-sm font-semibold text-slate-950">Project</h2>
            {project ? (
              <ProjectSummary project={project} />
            ) : (
              <p className="mt-2 text-sm text-slate-500">No project is linked yet.</p>
            )}
          </section>

          <section className="rounded-md border border-slate-200 bg-white p-4">
            <h2 className="text-sm font-semibold text-slate-950">Source</h2>
            <div className="mt-3 space-y-2 text-sm text-slate-600">
              <p>{sourceLabel ?? "No source run linked."}</p>
              {sourceRun ? <p>Run {formatDateTime(sourceRun.runTimestamp)}</p> : null}
              {asString(item.payload?.source_record_id) ? (
                <p>Record {asString(item.payload?.source_record_id)}</p>
              ) : null}
            </div>
            <NewsContextPanel context={newsContext} />
          </section>
        </aside>
      </div>
    </main>
  );
}

function NewsContextPanel({ context }: { context: NewsContext | null }) {
  if (!context) {
    return null;
  }
  return (
    <div className="mt-4 rounded-md border border-slate-200 bg-slate-50 p-3">
      <div className="flex items-start gap-2">
        <Newspaper className="mt-0.5 size-4 shrink-0 text-slate-500" aria-hidden="true" />
        <div className="min-w-0">
          <p className="break-words text-sm font-medium text-slate-950">
            {context.articleTitle ?? "News article"}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            {context.publishedAt ? formatDate(context.publishedAt) : "Publication date unknown"}
            {context.referenceIndex !== null ? ` - ref ${context.referenceIndex + 1}` : ""}
          </p>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-2 text-xs">
        {context.extractionConfidence ? (
          <span className="rounded border border-sky-200 bg-sky-50 px-2 py-1 text-sky-800">
            {humanize(context.extractionConfidence)} confidence
          </span>
        ) : null}
        {context.structuralDisagreement ? (
          <span className="inline-flex items-center gap-1 rounded border border-amber-200 bg-amber-50 px-2 py-1 text-amber-800">
            <AlertTriangle className="size-3.5" aria-hidden="true" />
            Structural disagreement
          </span>
        ) : null}
        {context.promptId ? (
          <span className="rounded border border-slate-200 bg-white px-2 py-1 text-slate-600">
            {context.promptId}
            {context.promptVersion ? ` ${context.promptVersion}` : ""}
          </span>
        ) : null}
      </div>
      {context.structuralDisagreement ? (
        <p className="mt-2 break-words text-xs text-slate-600">
          {formatValue(context.structuralDisagreement)}
        </p>
      ) : null}
      {context.url ? (
        <a
          className="mt-3 inline-flex h-8 items-center justify-center gap-2 rounded-md border border-slate-300 bg-white px-2 text-xs font-medium text-slate-800 hover:bg-slate-50"
          href={context.url}
          target="_blank"
          rel="noreferrer"
        >
          <ExternalLink className="size-3.5" aria-hidden="true" />
          Article
        </a>
      ) : null}
    </div>
  );
}

function EvidenceDetailSection({
  label,
  rows,
  defaultOpen = false
}: {
  label: string;
  rows: ReviewEvidenceSummary[];
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-md border border-slate-200">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-2 bg-slate-50 px-3 py-2 text-left text-sm font-medium text-slate-800"
        onClick={() => setOpen((value) => !value)}
      >
        <span>
          {label} ({rows.length})
        </span>
        <ChevronDown
          className={cn("size-4 transition-transform", open && "rotate-180")}
          aria-hidden="true"
        />
      </button>
      {open ? (
        <div className="divide-y divide-slate-100">
          {rows.length ? (
            rows.map((row) => <EvidenceDetailRow key={row.evidenceId} row={row} />)
          ) : (
            <p className="px-3 py-3 text-sm text-slate-500">No evidence in this bucket.</p>
          )}
        </div>
      ) : null}
    </div>
  );
}

function EvidenceDetailRow({ row }: { row: ReviewEvidenceSummary }) {
  return (
    <div className="px-3 py-3 text-sm">
      <div className="flex items-start gap-2">
        {row.isWinning ? (
          <Star className="mt-0.5 size-4 shrink-0 fill-amber-400 text-amber-500" aria-hidden="true" />
        ) : (
          <span className="mt-2 size-1.5 shrink-0 rounded-full bg-slate-300" />
        )}
        <div className="min-w-0">
          <p className="break-words font-medium text-slate-950">{row.summary}</p>
          <p className="mt-1 text-xs text-slate-500">
            {humanize(row.sourceType)}
            {row.evidenceDate ? ` - ${formatDate(row.evidenceDate)}` : ""}
            {row.sourceRecordId ? ` - ${row.sourceRecordId}` : ""}
            {row.isWinning ? " - winning evidence" : ""}
          </p>
          {row.extractedValue !== null && row.extractedValue !== undefined ? (
            <p className="mt-2 rounded border border-slate-200 bg-slate-50 px-2 py-1 text-xs text-slate-700">
              {formatValue(row.extractedValue)}
            </p>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function ProjectSummary({ project }: { project: ReviewProjectSummary }) {
  const style = statusStyle(project.pipelineStatus);

  return (
    <div className="mt-3 space-y-3 text-sm">
      <div>
        <p className="font-medium text-slate-950">{project.projectName}</p>
        <p className="mt-1 text-slate-500">{project.canonicalAddress}</p>
      </div>
      <div className="flex flex-wrap gap-2 text-xs">
        <span className="rounded border px-2 py-1" style={{ borderColor: style.color, color: style.color }}>
          {compactStatus(project.pipelineStatus)}
        </span>
        {project.totalUnits !== null ? (
          <span className="rounded border border-slate-200 px-2 py-1">
            {project.totalUnits.toLocaleString()} units
          </span>
        ) : null}
        {project.dateDelivery ? (
          <span className="rounded border border-slate-200 px-2 py-1">
            Delivery {formatDate(project.dateDelivery)}
          </span>
        ) : null}
      </div>
      {project.developer ? <p className="text-slate-600">{project.developer}</p> : null}
      <Link
        className="inline-flex h-9 items-center justify-center gap-2 rounded-md border border-slate-300 bg-white px-3 text-sm font-medium text-slate-800 hover:bg-slate-50"
        href={`/pipeline/${project.id}`}
      >
        <ExternalLink className="size-4" aria-hidden="true" />
        Open project
      </Link>
    </div>
  );
}

function CandidateProjectCard({
  project,
  disabled,
  onAccept
}: {
  project: ReviewProjectSummary;
  disabled: boolean;
  onAccept: () => void;
}) {
  return (
    <div className="rounded-md border border-slate-200 p-3">
      <p className="font-medium text-slate-950">{project.projectName}</p>
      <p className="mt-1 text-sm text-slate-500">{project.canonicalAddress}</p>
      <p className="mt-2 text-xs text-slate-500">
        {compactStatus(project.pipelineStatus)}
        {project.totalUnits !== null ? ` - ${project.totalUnits.toLocaleString()} units` : ""}
      </p>
      <Button className="mt-3" type="button" variant="outline" disabled={disabled} onClick={onAccept}>
        Accept this match
      </Button>
    </div>
  );
}

function NavigationLink({
  href,
  label,
  keycap,
  direction
}: {
  href: string | null;
  label: string;
  keycap: string;
  direction: "previous" | "next";
}) {
  const Icon = direction === "previous" ? ArrowLeft : ArrowRight;
  const className =
    "inline-flex h-8 items-center justify-center gap-1.5 rounded-md border border-slate-300 bg-white px-2 text-xs font-medium text-slate-800 hover:bg-slate-50";
  if (!href) {
    return (
      <span className={cn(className, "pointer-events-none opacity-45")}>
        <Icon className="size-3.5" aria-hidden="true" />
        {label}
        <span className="rounded border border-slate-300 px-1 text-[10px] text-slate-500">{keycap}</span>
      </span>
    );
  }
  return (
    <Link className={className} href={href}>
      <Icon className="size-3.5" aria-hidden="true" />
      {label}
      <span className="rounded border border-slate-300 px-1 text-[10px] text-slate-500">{keycap}</span>
    </Link>
  );
}

function ProcessedChangesSection({ changes }: { changes: ReviewProcessedChange[] }) {
  return (
    <section className="rounded-md border border-slate-200 bg-white p-4">
      <h2 className="text-sm font-semibold text-slate-950">Recent Field Changes</h2>
      {changes.length ? (
        <div className="mt-3 overflow-hidden rounded-md border border-slate-200">
          {changes.map((change) => (
            <div key={change.id} className="border-b border-slate-100 px-3 py-3 last:border-b-0">
              <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
                <span>{formatDateTime(change.timestamp)}</span>
                <span>{displayActor(change.reviewedByEmail, change.reviewedByUserId ?? change.reviewedBy)}</span>
              </div>
              <div className="mt-2 grid gap-2 text-sm md:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] md:items-center">
                <p className="break-words rounded border border-slate-200 bg-slate-50 px-2 py-1 text-slate-700">
                  {formatValue(change.oldValue)}
                </p>
                <ArrowRight className="hidden size-3.5 text-slate-300 md:block" aria-hidden="true" />
                <p className="break-words rounded border border-slate-200 bg-slate-50 px-2 py-1 text-slate-950">
                  {formatValue(change.newValue)}
                </p>
              </div>
              <p className="mt-2 text-xs text-slate-500">
                {humanize(change.changeType)} via {change.source}
              </p>
            </div>
          ))}
        </div>
      ) : (
        <p className="mt-2 text-sm text-slate-500">No prior committed changes for this project field.</p>
      )}
    </section>
  );
}

function ValuePanel({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="min-h-28 rounded-md border border-slate-200 bg-slate-50 p-3">
      <p className="text-xs font-medium uppercase tracking-normal text-slate-500">{label}</p>
      <p className="mt-2 break-words text-base text-slate-950">{formatValue(value)}</p>
    </div>
  );
}

function canAcceptItem(item: ReviewQueueItem, candidateProjects: ReviewProjectSummary[]) {
  if (item.itemType === "new_candidate") {
    return true;
  }
  if (item.itemType === "possible_match") {
    return candidateProjects.length === 1;
  }
  return true;
}

function priorityBadgeClass(priority: string) {
  const normalized = priority.toLowerCase();
  return cn(
    "inline-flex items-center rounded border px-2 py-1 text-xs font-medium",
    normalized === "high" && "border-red-200 bg-red-50 text-red-700",
    normalized === "medium" && "border-amber-200 bg-amber-50 text-amber-800",
    normalized === "low" && "border-slate-200 bg-slate-50 text-slate-600"
  );
}

function fieldLabel(value: string) {
  return humanize(value);
}

function reviewQueueHref(jurisdictionId: string | null) {
  const params = new URLSearchParams();
  if (jurisdictionId) {
    params.set("jurisdiction_id", jurisdictionId);
  }
  const query = params.toString();
  return query ? `/review?${query}` : "/review";
}

function reviewItemHref(itemId: string, jurisdictionId: string | null) {
  const params = new URLSearchParams();
  if (jurisdictionId) {
    params.set("jurisdiction_id", jurisdictionId);
  }
  const query = params.toString();
  return query ? `/review/${itemId}?${query}` : `/review/${itemId}`;
}

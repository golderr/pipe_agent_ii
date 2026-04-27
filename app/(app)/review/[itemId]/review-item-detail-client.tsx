"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AlertCircle,
  Check,
  Clock,
  ExternalLink,
  GitCompareArrows,
  RotateCcw,
  Save
} from "lucide-react";
import { useMemo, useState, useTransition } from "react";
import {
  stageReviewDecisionAction,
  unstageReviewDecisionAction
} from "@/app/(app)/review/actions";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { compactStatus, statusStyle } from "@/lib/status";
import { cn } from "@/lib/utils";
import type {
  ReviewItemDetailData,
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
  const { item, project, candidateProjects, sourceRun } = data;
  const [customValue, setCustomValue] = useState(formatInputValue(proposedValueForItem(item)));
  const [notes, setNotes] = useState(item.activeDecision?.decisionNotes ?? "");
  const [sourceUrl, setSourceUrl] = useState(item.activeDecision?.sourceUrl ?? "");
  const [banner, setBanner] = useState<Banner>(null);
  const [isPending, startTransition] = useTransition();
  const stagedByMe = isStagedByMe(item, currentUserId, currentUserEmail);
  const stagedByOther = isStagedByOther(item, currentUserId, currentUserEmail);
  const fieldName = fieldNameForItem(item);
  const canUseCustom = item.itemType !== "new_candidate" && item.itemType !== "possible_match";
  const hasSingleCandidate = item.itemType === "possible_match" && candidateProjects.length === 1;
  const acceptLabel = item.itemType === "new_candidate" ? "Create project" : "Accept new";
  const sourceLabel = sourceRun
    ? `${sourceRun.sourceName} - ${formatDate(sourceRun.finishedAt ?? sourceRun.runTimestamp)}`
    : sourceTextForItem(item);
  const payloadRows = useMemo(() => flattenPayload(item.payload), [item.payload]);

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
            <Link className="text-sm font-medium text-teal-700 hover:text-teal-900" href="/review">
              Back to Review Queue
            </Link>
            <p className="mt-3 text-xs font-medium uppercase tracking-normal text-slate-500">
              Review Item
            </p>
            <h1 className="mt-1 text-xl font-semibold tracking-normal text-slate-950">
              {fieldLabel(fieldName)}
            </h1>
          </div>
          <div className="flex flex-wrap gap-2 text-xs">
            <span className={priorityBadgeClass(item.priority)}>{humanize(item.priority)}</span>
            <span className="rounded border border-slate-200 bg-slate-50 px-2 py-1 text-slate-600">
              {humanize(item.itemType)}
            </span>
            <span className="rounded border border-slate-200 bg-slate-50 px-2 py-1 text-slate-600">
              {humanize(item.state)}
            </span>
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
              <div className="hidden items-center text-slate-300 lg:flex">-&gt;</div>
              <ValuePanel label="Proposed" value={proposedValueForItem(item)} />
            </div>
            {warningForItem(item) ? (
              <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                {warningForItem(item)}
              </div>
            ) : null}
          </section>

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
            {item.activeDecision ? (
              <div className="mt-3 rounded-md border border-sky-200 bg-sky-50 px-3 py-2 text-sm text-sky-900">
                Staged {humanize(item.activeDecision.decisionType ?? "decision")} by{" "}
                {isStagedByMe(item, currentUserId, currentUserEmail)
                  ? "you"
                  : displayActor(item.activeDecision.stagedByEmail, item.activeDecision.stagedBy)}
                .
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
          </section>
        </aside>
      </div>
    </main>
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

function ValuePanel({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="min-h-28 rounded-md border border-slate-200 bg-slate-50 p-3">
      <p className="text-xs font-medium uppercase tracking-normal text-slate-500">{label}</p>
      <p className="mt-2 break-words text-base text-slate-950">{formatValue(value)}</p>
    </div>
  );
}

function isStagedByMe(
  item: ReviewQueueItem,
  currentUserId: string,
  currentUserEmail: string | null
) {
  const decision = item.activeDecision;
  if (!decision) {
    return false;
  }
  return decision.stagedBy === currentUserId || Boolean(currentUserEmail && decision.stagedByEmail === currentUserEmail);
}

function isStagedByOther(
  item: ReviewQueueItem,
  currentUserId: string,
  currentUserEmail: string | null
) {
  return Boolean(item.activeDecision) && !isStagedByMe(item, currentUserId, currentUserEmail);
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

function acceptDecisionValue(item: ReviewQueueItem) {
  if (item.itemType === "new_candidate") {
    return { create_new: true, new_project_data: newProjectDataForItem(item) };
  }
  const targetProjectId = candidateProjectIdsForItem(item)[0];
  return targetProjectId ? { project_id: targetProjectId } : undefined;
}

function candidateProjectIdsForItem(item: ReviewQueueItem) {
  const payload = item.payload;
  const match = asRecord(payload?.match);
  return asStringArray(match?.candidate_project_ids ?? payload?.candidate_project_ids);
}

function newProjectDataForItem(item: ReviewQueueItem) {
  const mappedFields = asRecord(item.payload?.mapped_fields);
  return {
    canonical_address: asString(item.payload?.canonical_address) ?? undefined,
    project_name: asString(mappedFields?.project_name) ?? undefined,
    city: asString(mappedFields?.city) ?? undefined,
    state: asString(mappedFields?.state) ?? undefined,
    county: asString(mappedFields?.county) ?? undefined,
    zip: asString(mappedFields?.zip) ?? undefined
  };
}

function fieldNameForItem(item: ReviewQueueItem) {
  const payload = item.payload;
  const change = firstChange(item);
  const statusSuggestion = asRecord(payload?.status_suggestion);
  return (
    asString(payload?.field_name) ??
    asString(change?.field) ??
    asString(change?.field_name) ??
    (statusSuggestion ? "pipeline_status" : null) ??
    item.itemType
  );
}

function currentValueForItem(item: ReviewQueueItem) {
  const payload = item.payload;
  const currentOverride = asRecord(payload?.current_override);
  const statusSuggestion = asRecord(payload?.status_suggestion);
  const change = firstChange(item);
  if (currentOverride && "value" in currentOverride) {
    return currentOverride.value;
  }
  if (payload && "current_value" in payload) {
    return payload.current_value;
  }
  if (statusSuggestion && "current_status" in statusSuggestion) {
    return statusSuggestion.current_status;
  }
  if (change && "old_value" in change) {
    return change.old_value;
  }
  return null;
}

function proposedValueForItem(item: ReviewQueueItem) {
  const payload = item.payload;
  const candidate = asRecord(payload?.candidate);
  const statusSuggestion = asRecord(payload?.status_suggestion);
  const change = firstChange(item);
  if (payload && "proposed_value" in payload) {
    return payload.proposed_value;
  }
  if (candidate && "value" in candidate) {
    return candidate.value;
  }
  if (statusSuggestion && "suggested_status" in statusSuggestion) {
    return statusSuggestion.suggested_status;
  }
  if (change && "new_value" in change) {
    return change.new_value;
  }
  if (payload && "canonical_address" in payload) {
    return payload.canonical_address;
  }
  return null;
}

function sourceTextForItem(item: ReviewQueueItem) {
  const payload = item.payload;
  const candidate = asRecord(payload?.candidate);
  const frontier = asRecord(candidate?.evidence_frontier);
  const source = asString(frontier?.source_type) ?? asString(payload?.source_record_id);
  const date = asString(candidate?.evidence_date);
  if (source && date) {
    return `${source} - ${formatDate(date)}`;
  }
  return source;
}

function warningForItem(item: ReviewQueueItem) {
  const payload = item.payload;
  const flags = asRecordArray(payload?.review_flags);
  const firstFlag = flags[0];
  return (
    asString(payload?.message) ??
    asString(firstFlag?.message) ??
    (item.itemType.includes("contradiction") ? "This item conflicts with a manual override." : null)
  );
}

function firstChange(item: ReviewQueueItem) {
  return asRecordArray(item.payload?.changes)[0] ?? null;
}

function flattenPayload(payload: Record<string, unknown> | null) {
  if (!payload) {
    return [];
  }
  return Object.entries(payload)
    .filter(([, value]) => value !== null && value !== undefined && value !== "")
    .map(([key, value]) => ({ key, value: formatValue(value) }))
    .slice(0, 24);
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

function humanize(value: string) {
  return value
    .split(/[_-]/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function displayActor(email: string | null | undefined, fallback: string | null | undefined) {
  if (email) {
    return email;
  }
  if (fallback) {
    return fallback.slice(0, 8);
  }
  return "unknown";
}

function formatDate(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric"
  }).format(new Date(value));
}

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit"
  }).format(new Date(value));
}

function formatInputValue(value: unknown) {
  if (value === null || value === undefined) {
    return "";
  }
  if (typeof value === "string") {
    return value;
  }
  return formatValue(value);
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  if (typeof value === "number") {
    return value.toLocaleString();
  }
  if (typeof value === "boolean") {
    return value ? "Yes" : "No";
  }
  if (typeof value === "string") {
    return value;
  }
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asRecordArray(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.map(asRecord).filter((row): row is Record<string, unknown> => Boolean(row)) : [];
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && Boolean(item)) : [];
}

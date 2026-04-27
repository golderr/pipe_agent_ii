"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AlertCircle,
  Check,
  ExternalLink,
  GitCompareArrows,
  ListChecks,
  RotateCcw,
  Save
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState, useTransition } from "react";
import {
  commitReviewDecisionsAction,
  stageReviewDecisionAction,
  unstageReviewDecisionAction
} from "./actions";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { compactStatus, statusStyle } from "@/lib/status";
import { cn } from "@/lib/utils";
import type {
  ReviewProjectSummary,
  ReviewQueueData,
  ReviewQueueItem,
  ReviewSourceRunSummary
} from "@/lib/review/types";

type ReviewQueueClientProps = {
  data: ReviewQueueData;
  currentUserId: string;
  currentUserEmail: string | null;
};

type ReviewGroup = {
  key: string;
  project: ReviewProjectSummary | null;
  title: string;
  subtitle: string;
  items: ReviewQueueItem[];
  priority: string;
  highestPriorityRank: number;
  stagedMineCount: number;
  stagedOtherCount: number;
  undecidedCount: number;
  deferredCount: number;
  allDeferred: boolean;
  allStaged: boolean;
};

type ReviewSection = {
  key: string;
  label: string;
  groups: ReviewGroup[];
};

type Banner = {
  tone: "success" | "error";
  message: string;
} | null;

const PRIORITY_RANK: Record<string, number> = {
  high: 0,
  medium: 1,
  low: 2
};

const SECTION_LABELS: Record<string, string> = {
  high: "High",
  medium: "Medium",
  low: "Low",
  deferred: "Deferred"
};

export function ReviewQueueClient({
  data,
  currentUserId,
  currentUserEmail
}: ReviewQueueClientProps) {
  const router = useRouter();
  const [search, setSearch] = useState("");
  const [priorityFilter, setPriorityFilter] = useState("all");
  const [selectedGroupKey, setSelectedGroupKey] = useState<string | null>(null);
  const [focusedItemId, setFocusedItemId] = useState<string | null>(null);
  const [pendingItemId, setPendingItemId] = useState<string | null>(null);
  const [banner, setBanner] = useState<Banner>(null);
  const [isPending, startTransition] = useTransition();

  const sections = useMemo(
    () =>
      buildSections({
        data,
        currentUserId,
        currentUserEmail,
        search,
        priorityFilter
      }),
    [currentUserEmail, currentUserId, data, priorityFilter, search]
  );
  const groups = useMemo(() => sections.flatMap((section) => section.groups), [sections]);
  const selectedGroup = groups.find((group) => group.key === selectedGroupKey) ?? groups[0] ?? null;
  const focusedItem =
    selectedGroup?.items.find((item) => item.id === focusedItemId) ?? selectedGroup?.items[0] ?? null;

  const totalOpen = data.items.filter((item) => item.state === "open").length;
  const stagedMineCount = data.items.filter((item) =>
    isStagedByMe(item, currentUserId, currentUserEmail)
  ).length;
  const deferredCount = data.items.filter((item) => item.activeDecision?.decisionType === "defer").length;
  const undecidedCount = data.items.filter((item) => !item.activeDecision).length;

  const stageItem = useCallback(
    (item: ReviewQueueItem, decisionType: string, decisionValue?: unknown) => {
      if (isStagedByOther(item, currentUserId, currentUserEmail)) {
        return;
      }

      let normalizedValue = decisionValue;
      if (decisionType === "accept_new") {
        normalizedValue = acceptDecisionValue(item);
      }
      if (decisionType === "custom" && normalizedValue === undefined) {
        const proposedValue = proposedValueForItem(item);
        const entered = window.prompt("Custom value", formatValue(proposedValue));
        if (entered === null) {
          return;
        }
        normalizedValue = { value: entered };
      }

      setPendingItemId(item.id);
      setBanner(null);
      startTransition(async () => {
        const result = await stageReviewDecisionAction({
          reviewItemId: item.id,
          decisionType,
          decisionValue: normalizedValue,
          revise: isStagedByMe(item, currentUserId, currentUserEmail)
        });
        setPendingItemId(null);
        setBanner({
          tone: result.ok ? "success" : "error",
          message: result.message
        });
        if (result.ok) {
          router.refresh();
        }
      });
    },
    [currentUserEmail, currentUserId, router]
  );

  const unstageItem = useCallback(
    (item: ReviewQueueItem) => {
      if (!isStagedByMe(item, currentUserId, currentUserEmail)) {
        return;
      }
      setPendingItemId(item.id);
      setBanner(null);
      startTransition(async () => {
        const result = await unstageReviewDecisionAction(item.id);
        setPendingItemId(null);
        setBanner({
          tone: result.ok ? "success" : "error",
          message: result.message
        });
        if (result.ok) {
          router.refresh();
        }
      });
    },
    [currentUserEmail, currentUserId, router]
  );

  const stageProject = useCallback(
    (group: ReviewGroup, decisionType: "accept_new" | "keep_old" | "defer") => {
      setBanner(null);
      startTransition(async () => {
        let changed = 0;
        for (const item of group.items) {
          if (isStagedByOther(item, currentUserId, currentUserEmail)) {
            continue;
          }
          if (decisionType === "accept_new" && !canAcceptItem(item)) {
            continue;
          }
          const result = await stageReviewDecisionAction({
            reviewItemId: item.id,
            decisionType,
            decisionValue: decisionType === "accept_new" ? acceptDecisionValue(item) : undefined,
            revise: isStagedByMe(item, currentUserId, currentUserEmail)
          });
          if (!result.ok) {
            setBanner({ tone: "error", message: result.message });
            router.refresh();
            return;
          }
          changed += 1;
        }
        setBanner({ tone: "success", message: `${changed} decisions staged.` });
        router.refresh();
      });
    },
    [currentUserEmail, currentUserId, router]
  );

  const commitQueue = useCallback(() => {
    const confirmed = window.confirm(`Commit ${stagedMineCount} staged decisions?`);
    if (!confirmed) {
      return;
    }
    setBanner(null);
    startTransition(async () => {
      const result = await commitReviewDecisionsAction();
      setBanner({
        tone: result.ok ? "success" : "error",
        message: result.message
      });
      if (result.ok) {
        router.refresh();
      }
    });
  }, [router, stagedMineCount]);

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
      if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
        event.preventDefault();
        commitQueue();
        return;
      }
      if (!focusedItem || isPending) {
        return;
      }
      const currentIndex = selectedGroup?.items.findIndex((item) => item.id === focusedItem.id) ?? -1;
      if (event.key.toLowerCase() === "j" && selectedGroup && currentIndex >= 0) {
        event.preventDefault();
        setFocusedItemId(selectedGroup.items[Math.min(currentIndex + 1, selectedGroup.items.length - 1)]?.id ?? null);
      }
      if (event.key.toLowerCase() === "k" && selectedGroup && currentIndex >= 0) {
        event.preventDefault();
        setFocusedItemId(selectedGroup.items[Math.max(currentIndex - 1, 0)]?.id ?? null);
      }
      if (event.key.toLowerCase() === "a" && canAcceptItem(focusedItem)) {
        event.preventDefault();
        stageItem(focusedItem, "accept_new");
      }
      if (event.key.toLowerCase() === "s") {
        event.preventDefault();
        stageItem(focusedItem, "keep_old");
      }
      if (event.key.toLowerCase() === "d") {
        event.preventDefault();
        stageItem(focusedItem, "defer");
      }
      if (event.key.toLowerCase() === "f") {
        event.preventDefault();
        stageItem(focusedItem, "custom");
      }
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [commitQueue, focusedItem, isPending, selectedGroup, stageItem]);

  return (
    <main className="pb-24">
      <div className="border-b border-slate-200 bg-white px-5 py-4">
        <div className="flex flex-col gap-3 xl:flex-row xl:items-end xl:justify-between">
          <div>
            <p className="text-xs font-medium uppercase tracking-normal text-slate-500">Review Queue</p>
            <h1 className="mt-1 text-xl font-semibold tracking-normal text-slate-950">
              {data.items.length.toLocaleString()} active items
            </h1>
          </div>
          <div className="grid grid-cols-4 gap-2 text-sm sm:w-[34rem]">
            <Metric label="Open" value={totalOpen} />
            <Metric label="Mine" value={stagedMineCount} />
            <Metric label="Deferred" value={deferredCount} />
            <Metric label="Undecided" value={undecidedCount} />
          </div>
        </div>
        <div className="mt-4 flex flex-col gap-2 lg:flex-row lg:items-center">
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search project, field, value, source"
            className="lg:max-w-md"
          />
          <select
            value={priorityFilter}
            onChange={(event) => setPriorityFilter(event.target.value)}
            className="h-10 rounded-md border border-slate-300 bg-white px-3 text-sm text-slate-950 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-100"
          >
            <option value="all">All priorities</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
            <option value="deferred">Deferred</option>
          </select>
          {search || priorityFilter !== "all" ? (
            <Button
              variant="ghost"
              type="button"
              onClick={() => {
                setSearch("");
                setPriorityFilter("all");
              }}
            >
              <RotateCcw className="size-4" aria-hidden="true" />
              Clear
            </Button>
          ) : null}
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

      {groups.length === 0 ? (
        <div className="px-5 py-8">
          <div className="rounded-md border border-slate-200 bg-white p-6 text-sm text-slate-600">
            Review queue is clear for the current filters.
          </div>
        </div>
      ) : (
        <div className="grid min-h-[calc(100dvh-15rem)] xl:grid-cols-[21rem_minmax(0,1fr)]">
          <aside className="border-b border-slate-200 bg-white xl:border-b-0 xl:border-r">
            <div className="max-h-[calc(100dvh-14rem)] overflow-auto p-3">
              {sections.map((section) => (
                <ProjectSection
                  key={section.key}
                  section={section}
                  selectedGroupKey={selectedGroup?.key ?? null}
                  onSelect={(group) => {
                    setSelectedGroupKey(group.key);
                    setFocusedItemId(group.items[0]?.id ?? null);
                  }}
                />
              ))}
            </div>
          </aside>
          <section className="min-w-0 px-5 py-4">
            {selectedGroup ? (
              <ProjectReviewPanel
                group={selectedGroup}
                sourceRuns={data.sourceRuns}
                currentUserId={currentUserId}
                currentUserEmail={currentUserEmail}
                focusedItemId={focusedItem?.id ?? null}
                pendingItemId={pendingItemId}
                isPending={isPending}
                onFocus={setFocusedItemId}
                onStage={stageItem}
                onUnstage={unstageItem}
                onStageProject={stageProject}
              />
            ) : null}
          </section>
        </div>
      )}

      <div className="fixed inset-x-0 bottom-0 z-20 border-t border-slate-200 bg-white/95 px-5 py-3 backdrop-blur md:left-56">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-sm text-slate-600">
            Commit {stagedMineCount.toLocaleString()} decisions - {undecidedCount.toLocaleString()} undecided
          </p>
          <Button type="button" onClick={commitQueue} disabled={isPending}>
            <Save className="size-4" aria-hidden="true" />
            Commit {stagedMineCount.toLocaleString()} decisions
          </Button>
        </div>
      </div>
    </main>
  );
}

function ProjectSection({
  section,
  selectedGroupKey,
  onSelect
}: {
  section: ReviewSection;
  selectedGroupKey: string | null;
  onSelect: (group: ReviewGroup) => void;
}) {
  if (section.groups.length === 0) {
    return null;
  }

  return (
    <div className="mb-4">
      <div className="mb-1 flex items-center justify-between px-1">
        <p className="text-xs font-semibold uppercase tracking-normal text-slate-500">
          {section.label}
        </p>
        <p className="text-xs text-slate-400">{section.groups.length}</p>
      </div>
      <div className="space-y-1">
        {section.groups.map((group) => (
          <button
            key={group.key}
            type="button"
            onClick={() => onSelect(group)}
            className={cn(
              "w-full rounded-md border px-2 py-2 text-left text-sm transition-colors",
              selectedGroupKey === group.key
                ? "border-teal-200 bg-teal-50"
                : "border-transparent hover:border-slate-200 hover:bg-slate-50"
            )}
          >
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="truncate font-medium text-slate-950">{group.title}</p>
                <p className="truncate text-xs text-slate-500">{group.subtitle}</p>
              </div>
              <span className={priorityBadgeClass(group.priority)}>{group.items.length}</span>
            </div>
            <div className="mt-1 flex flex-wrap gap-1 text-[11px] text-slate-500">
              {group.stagedMineCount ? <span>mine {group.stagedMineCount}</span> : null}
              {group.stagedOtherCount ? <span>other {group.stagedOtherCount}</span> : null}
              {group.deferredCount ? <span>deferred {group.deferredCount}</span> : null}
              {group.allStaged ? <span>all staged</span> : null}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

function ProjectReviewPanel({
  group,
  sourceRuns,
  currentUserId,
  currentUserEmail,
  focusedItemId,
  pendingItemId,
  isPending,
  onFocus,
  onStage,
  onUnstage,
  onStageProject
}: {
  group: ReviewGroup;
  sourceRuns: Record<string, ReviewSourceRunSummary>;
  currentUserId: string;
  currentUserEmail: string | null;
  focusedItemId: string | null;
  pendingItemId: string | null;
  isPending: boolean;
  onFocus: (itemId: string) => void;
  onStage: (item: ReviewQueueItem, decisionType: string, decisionValue?: unknown) => void;
  onUnstage: (item: ReviewQueueItem) => void;
  onStageProject: (group: ReviewGroup, decisionType: "accept_new" | "keep_old" | "defer") => void;
}) {
  const status = group.project ? statusStyle(group.project.pipelineStatus) : null;

  return (
    <div className="space-y-4">
      <div className="rounded-md border border-slate-200 bg-white p-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="truncate text-lg font-semibold text-slate-950">{group.title}</h2>
              <span className={priorityBadgeClass(group.priority)}>{humanize(group.priority)}</span>
              {group.stagedMineCount || group.stagedOtherCount ? (
                <span className="rounded border border-sky-200 bg-sky-50 px-2 py-0.5 text-xs text-sky-800">
                  {group.allStaged ? "all staged" : "in review"}
                </span>
              ) : null}
            </div>
            <p className="mt-1 text-sm text-slate-500">{group.subtitle}</p>
            {group.project ? (
              <div className="mt-3 flex flex-wrap gap-2 text-xs text-slate-600">
                <span
                  className="rounded border px-2 py-1"
                  style={{
                    borderColor: status?.color ?? "#cbd5e1",
                    color: status?.color ?? "#475569"
                  }}
                >
                  {compactStatus(group.project.pipelineStatus)}
                </span>
                {group.project.totalUnits !== null ? (
                  <span className="rounded border border-slate-200 px-2 py-1">
                    {group.project.totalUnits.toLocaleString()} units
                  </span>
                ) : null}
                {group.project.developer ? (
                  <span className="rounded border border-slate-200 px-2 py-1">
                    {group.project.developer}
                  </span>
                ) : null}
                {group.project.dateDelivery ? (
                  <span className="rounded border border-slate-200 px-2 py-1">
                    Delivery {formatDate(group.project.dateDelivery)}
                  </span>
                ) : null}
              </div>
            ) : null}
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => onStageProject(group, "accept_new")}
              disabled={isPending || !group.items.some(canAcceptItem)}
            >
              <Check className="size-4" aria-hidden="true" />
              Accept all
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={() => onStageProject(group, "keep_old")}
              disabled={isPending}
            >
              <ListChecks className="size-4" aria-hidden="true" />
              Keep all
            </Button>
            {group.project ? (
              <Link
                className="inline-flex h-9 items-center justify-center gap-2 rounded-md border border-slate-300 bg-white px-3 text-sm font-medium text-slate-800 hover:bg-slate-50"
                href={`/pipeline/${group.project.id}`}
              >
                <ExternalLink className="size-4" aria-hidden="true" />
                Open project
              </Link>
            ) : null}
          </div>
        </div>
      </div>

      <div className="space-y-3">
        {group.items.map((item, index) => (
          <ReviewItemRow
            key={item.id}
            item={item}
            index={index}
            sourceRun={item.sourceRunId ? sourceRuns[item.sourceRunId] : undefined}
            currentUserId={currentUserId}
            currentUserEmail={currentUserEmail}
            focused={item.id === focusedItemId}
            pending={pendingItemId === item.id}
            isPending={isPending}
            onFocus={() => onFocus(item.id)}
            onStage={onStage}
            onUnstage={onUnstage}
          />
        ))}
      </div>
    </div>
  );
}

function ReviewItemRow({
  item,
  index,
  sourceRun,
  currentUserId,
  currentUserEmail,
  focused,
  pending,
  isPending,
  onFocus,
  onStage,
  onUnstage
}: {
  item: ReviewQueueItem;
  index: number;
  sourceRun: ReviewSourceRunSummary | undefined;
  currentUserId: string;
  currentUserEmail: string | null;
  focused: boolean;
  pending: boolean;
  isPending: boolean;
  onFocus: () => void;
  onStage: (item: ReviewQueueItem, decisionType: string, decisionValue?: unknown) => void;
  onUnstage: (item: ReviewQueueItem) => void;
}) {
  const stagedByMe = isStagedByMe(item, currentUserId, currentUserEmail);
  const stagedByOther = isStagedByOther(item, currentUserId, currentUserEmail);
  const disabled = isPending || stagedByOther;
  const candidates = candidateValuesForItem(item);
  const warningText = warningForItem(item);

  return (
    <article
      className={cn(
        "rounded-md border bg-white p-4 transition-colors",
        focused ? "border-teal-300 ring-2 ring-teal-100" : "border-slate-200",
        stagedByMe && "bg-teal-50/40",
        stagedByOther && "bg-slate-50"
      )}
      onClick={onFocus}
    >
      <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="flex size-6 items-center justify-center rounded border border-slate-200 bg-slate-50 text-xs text-slate-500">
              {index + 1}
            </span>
            <h3 className="text-sm font-semibold text-slate-950">{fieldLabel(fieldNameForItem(item))}</h3>
            <span className={priorityBadgeClass(item.priority)}>{humanize(item.priority)}</span>
            <span className="rounded border border-slate-200 bg-slate-50 px-2 py-0.5 text-xs text-slate-600">
              {humanize(item.itemType)}
            </span>
            {item.activeDecision ? (
              <DecisionBadge item={item} currentUserId={currentUserId} currentUserEmail={currentUserEmail} />
            ) : null}
          </div>

          <div className="mt-3 grid gap-2 md:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] md:items-center">
            <ValueBlock label="Current" value={currentValueForItem(item)} />
            <div className="hidden text-slate-300 md:block">-&gt;</div>
            <ValueBlock label="Proposed" value={proposedValueForItem(item)} />
          </div>

          <div className="mt-3 flex flex-wrap gap-2 text-xs text-slate-500">
            {sourceRun ? (
              <span className="rounded border border-slate-200 px-2 py-1">
                {sourceRun.sourceName} - {formatDate(sourceRun.finishedAt ?? sourceRun.runTimestamp)}
              </span>
            ) : null}
            {sourceTextForItem(item) ? (
              <span className="rounded border border-slate-200 px-2 py-1">{sourceTextForItem(item)}</span>
            ) : null}
            {item.matchConfidence !== null ? (
              <span className="rounded border border-slate-200 px-2 py-1">
                match {Math.round(item.matchConfidence * 100)}%
              </span>
            ) : null}
          </div>

          {warningText ? (
            <div className="mt-3 flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
              <GitCompareArrows className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              <p>{warningText}</p>
            </div>
          ) : null}

          {candidates.length > 1 ? (
            <div className="mt-3 flex flex-wrap gap-2">
              {candidates.map((candidate, candidateIndex) => (
                <Button
                  key={`${item.id}-candidate-${candidateIndex}`}
                  type="button"
                  variant="outline"
                  className="h-8 px-2 text-xs"
                  disabled={disabled}
                  onClick={(event) => {
                    event.stopPropagation();
                    onStage(item, `candidate_${candidateIndex + 1}`);
                  }}
                >
                  {candidateIndex + 1} {formatValue(candidate)}
                </Button>
              ))}
            </div>
          ) : null}
        </div>

        <div className="flex shrink-0 flex-wrap gap-2 xl:max-w-72 xl:justify-end">
          <DecisionButton
            keycap="a"
            label="Accept new"
            disabled={disabled || !canAcceptItem(item)}
            pending={pending}
            onClick={() => onStage(item, "accept_new")}
          />
          <DecisionButton
            keycap="s"
            label="Keep old"
            disabled={disabled}
            pending={pending}
            onClick={() => onStage(item, "keep_old")}
          />
          <DecisionButton
            keycap="d"
            label="Defer"
            disabled={disabled}
            pending={pending}
            onClick={() => onStage(item, "defer")}
          />
          <DecisionButton
            keycap="f"
            label="Custom"
            disabled={disabled || item.itemType === "new_candidate" || item.itemType === "possible_match"}
            pending={pending}
            onClick={() => onStage(item, "custom")}
          />
          {stagedByMe ? (
            <Button
              type="button"
              variant="ghost"
              className="h-8 px-2 text-xs"
              disabled={isPending}
              onClick={(event) => {
                event.stopPropagation();
                onUnstage(item);
              }}
            >
              <RotateCcw className="size-3.5" aria-hidden="true" />
              Unstage
            </Button>
          ) : null}
        </div>
      </div>
    </article>
  );
}

function DecisionButton({
  keycap,
  label,
  disabled,
  pending,
  onClick
}: {
  keycap: string;
  label: string;
  disabled: boolean;
  pending: boolean;
  onClick: () => void;
}) {
  return (
    <Button
      type="button"
      variant="outline"
      className="h-8 px-2 text-xs"
      disabled={disabled}
      onClick={(event) => {
        event.stopPropagation();
        onClick();
      }}
    >
      <span className="rounded border border-slate-300 px-1 text-[10px] text-slate-500">
        {keycap}
      </span>
      {pending ? "Saving" : label}
    </Button>
  );
}

function DecisionBadge({
  item,
  currentUserId,
  currentUserEmail
}: {
  item: ReviewQueueItem;
  currentUserId: string;
  currentUserEmail: string | null;
}) {
  const decision = item.activeDecision;
  if (!decision) {
    return null;
  }
  const mine = isStagedByMe(item, currentUserId, currentUserEmail);
  const label = decision.decisionType === "defer" ? "deferred" : humanize(decision.decisionType ?? "staged");
  const owner = mine ? "me" : displayActor(decision.stagedByEmail, decision.stagedBy);

  return (
    <span
      className={cn(
        "rounded border px-2 py-0.5 text-xs",
        mine ? "border-teal-200 bg-teal-50 text-teal-800" : "border-slate-200 bg-slate-50 text-slate-600"
      )}
    >
      {label} - {owner}
    </span>
  );
}

function ValueBlock({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="min-w-0 rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
      <p className="text-xs font-medium uppercase tracking-normal text-slate-500">{label}</p>
      <p className="mt-1 break-words text-sm text-slate-950">{formatValue(value)}</p>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
      <p className="text-xs text-slate-500">{label}</p>
      <p className="text-lg font-semibold text-slate-950">{value.toLocaleString()}</p>
    </div>
  );
}

function buildSections({
  data,
  currentUserId,
  currentUserEmail,
  search,
  priorityFilter
}: {
  data: ReviewQueueData;
  currentUserId: string;
  currentUserEmail: string | null;
  search: string;
  priorityFilter: string;
}): ReviewSection[] {
  const groupMap = new Map<string, ReviewQueueItem[]>();
  for (const item of data.items) {
    if (!itemMatchesFilters(item, data, search, priorityFilter)) {
      continue;
    }
    const key = item.projectId ?? `item-${item.id}`;
    groupMap.set(key, [...(groupMap.get(key) ?? []), item]);
  }

  const groups = [...groupMap.entries()].map(([key, items]) =>
    buildGroup(key, items, data.projects[key] ?? null, currentUserId, currentUserEmail)
  );
  groups.sort((a, b) => {
    if (a.allDeferred !== b.allDeferred) {
      return a.allDeferred ? 1 : -1;
    }
    if (a.highestPriorityRank !== b.highestPriorityRank) {
      return a.highestPriorityRank - b.highestPriorityRank;
    }
    if (a.items.length !== b.items.length) {
      return b.items.length - a.items.length;
    }
    return a.title.localeCompare(b.title);
  });

  return ["high", "medium", "low", "deferred"].map((key) => ({
    key,
    label: SECTION_LABELS[key],
    groups: groups.filter((group) => sectionKeyForGroup(group) === key)
  }));
}

function buildGroup(
  key: string,
  items: ReviewQueueItem[],
  project: ReviewProjectSummary | null,
  currentUserId: string,
  currentUserEmail: string | null
): ReviewGroup {
  const sortedItems = [...items].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  const highestPriorityRank = Math.min(...sortedItems.map((item) => priorityRank(item.priority)));
  const priority = sortedItems.find((item) => priorityRank(item.priority) === highestPriorityRank)?.priority ?? "low";
  const stagedMineCount = sortedItems.filter((item) =>
    isStagedByMe(item, currentUserId, currentUserEmail)
  ).length;
  const stagedOtherCount = sortedItems.filter((item) =>
    isStagedByOther(item, currentUserId, currentUserEmail)
  ).length;
  const undecidedCount = sortedItems.filter((item) => !item.activeDecision).length;
  const deferredCount = sortedItems.filter((item) => item.activeDecision?.decisionType === "defer").length;
  const allDeferred = deferredCount === sortedItems.length;
  const allStaged = sortedItems.every((item) => Boolean(item.activeDecision));
  const title = project?.projectName ?? titleForUnmatchedItem(sortedItems[0]);
  const subtitle = project
    ? [project.canonicalAddress, project.city, project.state, project.zip].filter(Boolean).join(" - ")
    : subtitleForUnmatchedItem(sortedItems[0]);

  return {
    key,
    project,
    title,
    subtitle,
    items: sortedItems,
    priority,
    highestPriorityRank,
    stagedMineCount,
    stagedOtherCount,
    undecidedCount,
    deferredCount,
    allDeferred,
    allStaged
  };
}

function itemMatchesFilters(
  item: ReviewQueueItem,
  data: ReviewQueueData,
  search: string,
  priorityFilter: string
) {
  if (priorityFilter === "deferred") {
    if (item.activeDecision?.decisionType !== "defer") {
      return false;
    }
  } else if (priorityFilter !== "all" && item.priority.toLowerCase() !== priorityFilter) {
    return false;
  }
  if (!search.trim()) {
    return true;
  }
  const project = item.projectId ? data.projects[item.projectId] : null;
  const sourceRun = item.sourceRunId ? data.sourceRuns[item.sourceRunId] : null;
  const haystack = [
    project?.projectName,
    project?.canonicalAddress,
    project?.developer,
    sourceRun?.sourceName,
    item.itemType,
    item.priority,
    fieldNameForItem(item),
    formatValue(currentValueForItem(item)),
    formatValue(proposedValueForItem(item))
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return haystack.includes(search.trim().toLowerCase());
}

function sectionKeyForGroup(group: ReviewGroup) {
  if (group.allDeferred) {
    return "deferred";
  }
  const priority = group.priority.toLowerCase();
  return priority in SECTION_LABELS ? priority : "low";
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

function canAcceptItem(item: ReviewQueueItem) {
  if (item.itemType === "new_candidate") {
    return false;
  }
  if (item.itemType === "possible_match") {
    return Boolean(discoveryTargetProjectId(item));
  }
  return true;
}

function acceptDecisionValue(item: ReviewQueueItem) {
  const targetProjectId = discoveryTargetProjectId(item);
  return targetProjectId ? { project_id: targetProjectId } : undefined;
}

function discoveryTargetProjectId(item: ReviewQueueItem) {
  const payload = item.payload;
  const match = asRecord(payload?.match);
  const candidates = asStringArray(match?.candidate_project_ids ?? payload?.candidate_project_ids);
  return candidates[0] ?? null;
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

function candidateValuesForItem(item: ReviewQueueItem) {
  const candidates = asRecordArray(item.payload?.candidates);
  return candidates.map((candidate) => ("value" in candidate ? candidate.value : candidate));
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

function titleForUnmatchedItem(item: ReviewQueueItem | undefined) {
  if (!item) {
    return "Unmatched review item";
  }
  return asString(item.payload?.canonical_address) ?? humanize(item.itemType);
}

function subtitleForUnmatchedItem(item: ReviewQueueItem | undefined) {
  if (!item) {
    return "No project linked";
  }
  return [humanize(item.itemType), asString(item.payload?.source_record_id)].filter(Boolean).join(" - ");
}

function priorityRank(priority: string) {
  return PRIORITY_RANK[priority.toLowerCase()] ?? 2;
}

function priorityBadgeClass(priority: string) {
  const normalized = priority.toLowerCase();
  return cn(
    "inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium",
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

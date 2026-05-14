"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AlertCircle,
  AlertTriangle,
  ArrowRight,
  Check,
  ChevronDown,
  ExternalLink,
  GitCompareArrows,
  ListChecks,
  Newspaper,
  RotateCcw,
  Save,
  Star
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState, useTransition, type MouseEvent } from "react";
import {
  commitReviewDecisionsAction,
  stageReviewDecisionAction,
  unstageReviewDecisionAction
} from "./actions";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ThreeFieldEditor } from "@/components/review/three-field-editor";
import {
  acceptDecisionValue,
  asString,
  candidateProjectIdsForItem,
  candidateValuesForItem,
  currentValueForItem,
  dissentingEvidenceForItem,
  displayActor,
  fieldNameForItem,
  formatDate,
  formatDateTime,
  formatInputValue,
  formatValue,
  humanSummaryForItem,
  humanize,
  isStagedByMe,
  isStagedByOther,
  newsContextForItem,
  proposedValueForItem,
  resultDefaultValueForItem,
  sourceTextForItem,
  structuralDisagreementText,
  supportingEvidenceForItem,
  type NewsContext,
  valueChangeForItem,
  warningForItem
} from "@/lib/review/payload";
import {
  buildReviewedFilterOptions,
  buildReviewedRows,
  filterReviewedRows,
  type ReviewedDecisionFilters,
  type ReviewedDecisionRow
} from "@/lib/review/reviewed";
import {
  buildDiscoveryCards,
  isDiscoveryItem,
  type DiscoveryCard
} from "@/lib/review/discovery";
import { compactStatus, statusStyle } from "@/lib/status";
import { cn } from "@/lib/utils";
import type {
  ReviewProjectSummary,
  ReviewEvidenceSummary,
  ReviewQueueData,
  ReviewQueueItem,
  ReviewSourceRunSummary
} from "@/lib/review/types";

type ReviewQueueClientProps = {
  activeTab: ReviewTab;
  data: ReviewQueueData;
  jurisdictionId: string | null;
  initialDiscoveryCardId: string | null;
  currentUserId: string;
  currentUserEmail: string | null;
};

type ReviewTab = "queue" | "discovery" | "reviewed";

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

type SourceFamily = "news" | "permit" | "costar" | "pipedream" | "other";
type SourceFilter = "all" | SourceFamily | "multiple";

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

const SOURCE_FAMILY_LABELS: Record<SourceFamily, string> = {
  news: "Article",
  permit: "Permit",
  costar: "CoStar",
  pipedream: "Pipedream",
  other: "Other"
};

const SOURCE_FAMILY_ORDER: SourceFamily[] = ["news", "permit", "costar", "pipedream", "other"];

const SOURCE_FILTER_OPTIONS: Array<{ value: SourceFilter; label: string }> = [
  { value: "all", label: "All sources" },
  { value: "news", label: "Article" },
  { value: "permit", label: "Permit" },
  { value: "costar", label: "CoStar" },
  { value: "pipedream", label: "Pipedream" },
  { value: "multiple", label: "Multiple" },
  { value: "other", label: "Other" }
];

export function ReviewQueueClient({
  activeTab,
  data,
  jurisdictionId,
  initialDiscoveryCardId,
  currentUserId,
  currentUserEmail
}: ReviewQueueClientProps) {
  const router = useRouter();
  const [search, setSearch] = useState("");
  const [priorityFilter, setPriorityFilter] = useState("all");
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>("all");
  const [reviewedFieldFilter, setReviewedFieldFilter] = useState("");
  const [reviewedOutcomeFilter, setReviewedOutcomeFilter] = useState("");
  const [reviewedDeciderFilter, setReviewedDeciderFilter] = useState("");
  const [reviewedSort, setReviewedSort] = useState<ReviewedDecisionFilters["sort"]>("date_desc");
  const [selectedGroupKey, setSelectedGroupKey] = useState<string | null>(null);
  const [focusedItemId, setFocusedItemId] = useState<string | null>(null);
  const [focusedDiscoveryCardId, setFocusedDiscoveryCardId] = useState<string | null>(
    initialDiscoveryCardId
  );
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
        priorityFilter,
        sourceFilter
      }),
    [currentUserEmail, currentUserId, data, priorityFilter, search, sourceFilter]
  );
  const groups = useMemo(() => sections.flatMap((section) => section.groups), [sections]);
  const filteredItemCount = groups.reduce((sum, group) => sum + group.items.length, 0);
  const selectedGroup = groups.find((group) => group.key === selectedGroupKey) ?? groups[0] ?? null;
  const focusedItem =
    selectedGroup?.items.find((item) => item.id === focusedItemId) ?? selectedGroup?.items[0] ?? null;
  const discoveryCards = useMemo(
    () =>
      buildDiscoveryCards(
        data.items.filter(
          (item) =>
            isDiscoveryItem(item) &&
            itemMatchesFilters(item, data, search, priorityFilter, sourceFilter)
        )
      ),
    [data, priorityFilter, search, sourceFilter]
  );
  const focusedDiscoveryCard =
    discoveryCards.find((card) => card.key === focusedDiscoveryCardId) ?? discoveryCards[0] ?? null;
  const reviewedRows = useMemo(
    () => buildReviewedRows(data.reviewedItems, data.projects),
    [data.projects, data.reviewedItems]
  );
  const reviewedFilters = useMemo(
    () => buildReviewedFilterOptions(reviewedRows),
    [reviewedRows]
  );
  const filteredReviewedRows = useMemo(
    () =>
      filterReviewedRows(reviewedRows, {
        search,
        field: reviewedFieldFilter,
        outcome: reviewedOutcomeFilter,
        decider: reviewedDeciderFilter,
        sort: reviewedSort
      }),
    [reviewedDeciderFilter, reviewedFieldFilter, reviewedOutcomeFilter, reviewedRows, reviewedSort, search]
  );

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
      const nextFocus = nextFocusAfterItem(groups, item.id);

      let normalizedValue = decisionValue;
      if (decisionType === "accept_new") {
        normalizedValue = acceptDecisionValue(item);
      }
      if (decisionType === "custom" && normalizedValue === undefined) {
        router.push(reviewItemHref(item.id, jurisdictionId));
        return;
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
          if (nextFocus) {
            setSelectedGroupKey(nextFocus.groupKey);
            setFocusedItemId(nextFocus.itemId);
          }
          router.refresh();
        }
      });
    },
    [currentUserEmail, currentUserId, groups, jurisdictionId, router]
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
      const result = await commitReviewDecisionsAction({ jurisdictionId });
      setBanner({
        tone: result.ok ? "success" : "error",
        message: result.message
      });
      if (result.ok) {
        router.refresh();
      }
    });
  }, [jurisdictionId, router, stagedMineCount]);

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
      if (activeTab !== "queue") {
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
      if (event.key.toLowerCase() === "c" && valueChangeForItem(focusedItem)) {
        event.preventDefault();
        stageItem(focusedItem, "custom", { value: resultDefaultValueForItem(focusedItem) });
      }
      if (event.key.toLowerCase() === "a" && canAcceptItem(focusedItem) && !valueChangeForItem(focusedItem)) {
        event.preventDefault();
        stageItem(focusedItem, "accept_new");
      }
      if (event.key.toLowerCase() === "s" && !valueChangeForItem(focusedItem)) {
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
  }, [activeTab, commitQueue, focusedItem, isPending, selectedGroup, stageItem]);

  return (
    <main className="pb-24">
      <div className="border-b border-slate-200 bg-white px-5 py-4">
        <div className="flex flex-col gap-3 xl:flex-row xl:items-end xl:justify-between">
          <div>
            <p className="text-xs font-medium uppercase tracking-normal text-slate-500">Review Queue</p>
            <h1 className="mt-1 text-xl font-semibold tracking-normal text-slate-950">
              {activeTab === "reviewed"
                ? `${filteredReviewedRows.length.toLocaleString()} reviewed decisions`
                : activeTab === "discovery"
                  ? `${discoveryCards.length.toLocaleString()} discovery items`
                : `${filteredItemCount.toLocaleString()} active items`}
            </h1>
          </div>
          <div className="grid grid-cols-4 gap-2 text-sm sm:w-[34rem]">
            <Metric label="Open" value={totalOpen} />
            <Metric label="Mine" value={stagedMineCount} />
            <Metric label="Deferred" value={deferredCount} />
            <Metric label="Reviewed" value={reviewedRows.length} />
          </div>
        </div>
        <div className="mt-4 flex flex-wrap gap-2 border-t border-slate-200 pt-3" role="tablist" aria-label="Review tabs">
          <ReviewTabLink
            active={activeTab === "queue"}
            href={reviewPageHref("queue", jurisdictionId)}
            label="Queue"
          />
          <ReviewTabLink
            active={activeTab === "discovery"}
            href={reviewPageHref("discovery", jurisdictionId)}
            label="Discovery"
          />
          <ReviewTabLink
            active={activeTab === "reviewed"}
            href={reviewPageHref("reviewed", jurisdictionId)}
            label="Reviewed"
          />
        </div>
        <div className="mt-4 flex flex-col gap-2 lg:flex-row lg:items-center">
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search project, field, value, source"
            className="lg:max-w-md"
          />
          {activeTab === "reviewed" ? (
            <>
              <ReviewedSelect
                label="Field"
                value={reviewedFieldFilter}
                onChange={setReviewedFieldFilter}
                options={reviewedFilters.fields}
              />
              <ReviewedSelect
                label="Outcome"
                value={reviewedOutcomeFilter}
                onChange={setReviewedOutcomeFilter}
                options={reviewedFilters.outcomes}
              />
              <ReviewedSelect
                label="Decider"
                value={reviewedDeciderFilter}
                onChange={setReviewedDeciderFilter}
                options={reviewedFilters.deciders}
              />
              <select
                aria-label="Reviewed sort"
                value={reviewedSort}
                onChange={(event) => setReviewedSort(event.target.value as ReviewedDecisionFilters["sort"])}
                className="h-10 rounded-md border border-slate-300 bg-white px-3 text-sm text-slate-950 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-100"
              >
                <option value="date_desc">Newest first</option>
                <option value="date_asc">Oldest first</option>
                <option value="decider">Decider</option>
                <option value="project">Project</option>
              </select>
            </>
          ) : (
            <>
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
              <select
                aria-label="Source"
                value={sourceFilter}
                onChange={(event) => setSourceFilter(event.target.value as SourceFilter)}
                className="h-10 rounded-md border border-slate-300 bg-white px-3 text-sm text-slate-950 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-100"
              >
                {SOURCE_FILTER_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </>
          )}
          {hasActiveSearchOrFilter({
            activeTab,
            search,
            priorityFilter,
            sourceFilter,
            reviewedFieldFilter,
            reviewedOutcomeFilter,
            reviewedDeciderFilter
          }) ? (
            <Button
              variant="ghost"
              type="button"
              onClick={() => {
                setSearch("");
                setPriorityFilter("all");
                setSourceFilter("all");
                setReviewedFieldFilter("");
                setReviewedOutcomeFilter("");
                setReviewedDeciderFilter("");
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

      {activeTab === "reviewed" ? (
        <ReviewedDecisionsView rows={filteredReviewedRows} jurisdictionId={jurisdictionId} />
      ) : activeTab === "discovery" ? (
        <DiscoveryView
          cards={discoveryCards}
          selectedCardKey={focusedDiscoveryCard?.key ?? null}
          sourceRuns={data.sourceRuns}
          jurisdictionId={jurisdictionId}
          onSelect={setFocusedDiscoveryCardId}
        />
      ) : groups.length === 0 ? (
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
                jurisdictionId={jurisdictionId}
                projects={data.projects}
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

      {activeTab === "queue" ? (
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
      ) : null}
    </main>
  );
}

function ReviewTabLink({
  active,
  href,
  label
}: {
  active: boolean;
  href: string;
  label: string;
}) {
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

function ReviewedSelect({
  label,
  value,
  options,
  onChange
}: {
  label: string;
  value: string;
  options: Array<{ value: string; label: string }>;
  onChange: (value: string) => void;
}) {
  return (
    <select
      aria-label={label}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      className="h-10 rounded-md border border-slate-300 bg-white px-3 text-sm text-slate-950 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-100"
    >
      <option value="">All {label.toLowerCase()}</option>
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

function ReviewedDecisionsView({
  rows,
  jurisdictionId
}: {
  rows: ReviewedDecisionRow[];
  jurisdictionId: string | null;
}) {
  if (!rows.length) {
    return (
      <div className="px-5 py-8">
        <div className="rounded-md border border-slate-200 bg-white p-6 text-sm text-slate-600">
          No reviewed decisions match the current filters.
        </div>
      </div>
    );
  }

  return (
    <section className="px-5 py-4">
      <div className="overflow-hidden rounded-md border border-slate-200 bg-white">
        <div className="hidden grid-cols-[9rem_minmax(12rem,1.1fr)_9rem_8rem_minmax(10rem,1fr)_auto] gap-3 border-b border-slate-200 bg-slate-50 px-4 py-2 text-xs font-medium uppercase tracking-normal text-slate-500 lg:grid">
          <span>Committed</span>
          <span>Project</span>
          <span>Field</span>
          <span>Outcome</span>
          <span>Decider</span>
          <span>Links</span>
        </div>
        <div className="divide-y divide-slate-100">
          {rows.map((row) => (
            <ReviewedDecisionListRow row={row} jurisdictionId={jurisdictionId} key={row.item.id} />
          ))}
        </div>
      </div>
    </section>
  );
}

function ReviewedDecisionListRow({
  row,
  jurisdictionId
}: {
  row: ReviewedDecisionRow;
  jurisdictionId: string | null;
}) {
  const project = row.project;
  const detailHref = reviewItemHref(row.item.id, jurisdictionId);
  const changesHref = project
    ? `/pipeline/${project.id}?tab=changes&field=${encodeURIComponent(row.field)}`
    : null;

  return (
    <article className="grid gap-3 px-4 py-3 text-sm lg:grid-cols-[9rem_minmax(12rem,1.1fr)_9rem_8rem_minmax(10rem,1fr)_auto] lg:items-start">
      <span className="text-slate-500">{row.committedAt ? formatDateTime(row.committedAt) : "-"}</span>
      <div className="min-w-0">
        <p className="truncate font-medium text-slate-950">{project?.projectName ?? "Unlinked item"}</p>
        <p className="mt-0.5 truncate text-xs text-slate-500">{project?.canonicalAddress ?? row.item.id}</p>
      </div>
      <span className="font-medium text-slate-800">{row.fieldLabel}</span>
      <span className="w-fit rounded border border-slate-200 bg-slate-50 px-2 py-0.5 text-xs text-slate-700">
        {row.outcomeLabel}
      </span>
      <div className="min-w-0">
        <p className="truncate text-slate-700" title={row.deciderLabel}>
          {row.deciderLabel}
        </p>
        <p className="mt-0.5 truncate text-xs text-slate-500">
          {formatValue(row.currentValue)} to {formatValue(row.proposedValue)}
        </p>
      </div>
      <div className="flex flex-wrap justify-end gap-2">
        <Link
          className="inline-flex h-8 items-center justify-center rounded-md border border-slate-300 bg-white px-2 text-xs font-medium text-slate-800 hover:bg-slate-50"
          href={detailHref}
        >
          Detail
        </Link>
        {changesHref ? (
          <Link
            className="inline-flex h-8 items-center justify-center rounded-md border border-slate-300 bg-white px-2 text-xs font-medium text-slate-800 hover:bg-slate-50"
            href={changesHref}
          >
            ChangeLog
          </Link>
        ) : null}
      </div>
    </article>
  );
}

function DiscoveryView({
  cards,
  selectedCardKey,
  sourceRuns,
  jurisdictionId,
  onSelect
}: {
  cards: DiscoveryCard[];
  selectedCardKey: string | null;
  sourceRuns: Record<string, ReviewSourceRunSummary>;
  jurisdictionId: string | null;
  onSelect: (cardKey: string) => void;
}) {
  const selectedCard = cards.find((card) => card.key === selectedCardKey) ?? cards[0] ?? null;
  if (!cards.length) {
    return (
      <div className="px-5 py-8">
        <div className="rounded-md border border-slate-200 bg-white p-6 text-sm text-slate-600">
          No discovery items match the current filters.
        </div>
      </div>
    );
  }

  return (
    <div className="grid min-h-[calc(100dvh-15rem)] xl:grid-cols-[21rem_minmax(0,1fr)]">
      <aside className="border-b border-slate-200 bg-white xl:border-b-0 xl:border-r">
        <div className="max-h-[calc(100dvh-14rem)] overflow-auto p-3">
          <div className="mb-1 flex items-center justify-between px-1">
            <p className="text-xs font-semibold uppercase tracking-normal text-slate-500">
              Discovery
            </p>
            <p className="text-xs text-slate-400">{cards.length}</p>
          </div>
          <div className="space-y-1">
            {cards.map((card) => (
              <button
                key={card.key}
                type="button"
                onClick={() => onSelect(card.key)}
                className={cn(
                  "w-full rounded-md border px-2 py-2 text-left text-sm transition-colors",
                  selectedCard?.key === card.key
                    ? "border-teal-200 bg-teal-50"
                    : "border-transparent hover:border-slate-200 hover:bg-slate-50"
                )}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate font-medium text-slate-950">{card.title}</p>
                    <p className="truncate text-xs text-slate-500">{card.subtitle}</p>
                  </div>
                  <span className={priorityBadgeClass(card.item.priority)}>
                    {card.potentialMatchCount}
                  </span>
                </div>
                <p className="mt-1 text-[11px] text-slate-500">
                  {humanize(card.item.itemType)} - {newCandidateProbabilityLabel(card)}
                </p>
              </button>
            ))}
          </div>
        </div>
      </aside>
      <section className="min-w-0 px-5 py-4">
        {selectedCard ? (
          <DiscoveryCardShell
            card={selectedCard}
            sourceRun={
              selectedCard.item.sourceRunId ? sourceRuns[selectedCard.item.sourceRunId] : undefined
            }
            jurisdictionId={jurisdictionId}
          />
        ) : null}
      </section>
    </div>
  );
}

function DiscoveryCardShell({
  card,
  sourceRun,
  jurisdictionId
}: {
  card: DiscoveryCard;
  sourceRun: ReviewSourceRunSummary | undefined;
  jurisdictionId: string | null;
}) {
  const subject = card.subject;
  return (
    <article className="rounded-md border border-slate-200 bg-white">
      <div className="border-b border-slate-200 px-4 py-3">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="truncate text-lg font-semibold text-slate-950">{card.title}</h2>
              <span className={priorityBadgeClass(card.item.priority)}>
                {humanize(card.item.priority)}
              </span>
              <span className="rounded border border-slate-200 bg-slate-50 px-2 py-0.5 text-xs text-slate-600">
                {humanize(card.item.itemType)}
              </span>
            </div>
            <p className="mt-1 text-sm text-slate-500">{card.subtitle}</p>
            <div className="mt-3 flex flex-wrap gap-2 text-xs text-slate-500">
              <span className="rounded border border-slate-200 bg-slate-50 px-2 py-1">
                Source: {sourceSummaryForItem(card.item, sourceRun)}
              </span>
              {sourceRun ? (
                <span className="rounded border border-slate-200 px-2 py-1">
                  {sourceRun.sourceName} - {formatDate(sourceRun.finishedAt ?? sourceRun.runTimestamp)}
                </span>
              ) : null}
              {sourceTextForItem(card.item) ? (
                <span className="rounded border border-slate-200 px-2 py-1">
                  {sourceTextForItem(card.item)}
                </span>
              ) : null}
              <NewsContextChips context={newsContextForItem(card.item)} />
            </div>
          </div>
          <div className="grid min-w-44 grid-cols-2 gap-2 text-sm">
            <DiscoveryMetric label="Matches" value={card.potentialMatchCount.toLocaleString()} />
            <DiscoveryMetric
              label="New %"
              value={
                card.newCandidateProbability !== null
                  ? `${Math.round(card.newCandidateProbability * 100)}`
                  : "-"
              }
            />
          </div>
        </div>
      </div>
      <div className="overflow-x-auto px-4 py-3">
        <table className="w-full min-w-[58rem] table-fixed text-left text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-xs font-medium uppercase tracking-normal text-slate-500">
              <th className="w-44 py-2 pr-3">Project</th>
              <th className="w-64 py-2 pr-3">Address</th>
              <th className="w-44 py-2 pr-3">Developer</th>
              <th className="w-28 py-2 pr-3">Units</th>
              <th className="w-32 py-2 pr-3">Product</th>
              <th className="w-32 py-2 pr-3">Status</th>
              <th className="w-24 py-2 pr-3">Stories</th>
            </tr>
          </thead>
          <tbody>
            <tr className="border-b border-slate-100 align-top last:border-b-0">
              <SubjectCell value={subject.projectName} />
              <SubjectCell value={subject.canonicalAddress} />
              <SubjectCell value={subject.developer} />
              <SubjectCell value={subject.totalUnits} />
              <SubjectCell value={subject.productType ?? subject.ageRestriction} />
              <SubjectCell value={subject.pipelineStatus} />
              <SubjectCell value={subject.stories} />
            </tr>
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-200 px-4 py-3 text-sm">
        <div className="text-slate-600">
          Potential matches: {card.potentialMatchCount.toLocaleString()} -{" "}
          {newCandidateProbabilityLabel(card)}
        </div>
        <Link
          className="inline-flex h-8 items-center justify-center gap-2 rounded-md border border-slate-300 bg-white px-2 text-xs font-medium text-slate-800 hover:bg-slate-50"
          href={reviewItemHref(card.item.id, jurisdictionId)}
        >
          <ExternalLink className="size-3.5" aria-hidden="true" />
          Detail
        </Link>
      </div>
    </article>
  );
}

function SubjectCell({ value }: { value: unknown }) {
  return <td className="break-words py-3 pr-3 text-slate-800">{formatValue(value)}</td>;
}

function DiscoveryMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
      <p className="text-xs text-slate-500">{label}</p>
      <p className="text-lg font-semibold text-slate-950">{value}</p>
    </div>
  );
}

function newCandidateProbabilityLabel(card: DiscoveryCard) {
  if (card.newCandidateProbability === null) {
    return "New probability unavailable";
  }
  return `New probability ${Math.round(card.newCandidateProbability * 100)}%`;
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
  jurisdictionId,
  projects,
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
  jurisdictionId: string | null;
  projects: Record<string, ReviewProjectSummary>;
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
            projects={projects}
            sourceRun={item.sourceRunId ? sourceRuns[item.sourceRunId] : undefined}
            currentUserId={currentUserId}
            currentUserEmail={currentUserEmail}
            jurisdictionId={jurisdictionId}
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
  projects,
  sourceRun,
  currentUserId,
  currentUserEmail,
  jurisdictionId,
  focused,
  pending,
  isPending,
  onFocus,
  onStage,
  onUnstage
}: {
  item: ReviewQueueItem;
  index: number;
  projects: Record<string, ReviewProjectSummary>;
  sourceRun: ReviewSourceRunSummary | undefined;
  currentUserId: string;
  currentUserEmail: string | null;
  jurisdictionId: string | null;
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
  const matchCandidates = possibleMatchCandidateProjects(item, projects);
  const warningText = warningForItem(item);
  const supportingEvidence = supportingEvidenceForItem(item);
  const dissentingEvidence = dissentingEvidenceForItem(item);
  const newsContext = newsContextForItem(item);
  const sourceSummary = sourceSummaryForItem(item, sourceRun);
  const valueChange = valueChangeForItem(item);

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

          <p className="mt-2 max-w-4xl text-sm leading-6 text-slate-700">
            {humanSummaryForItem(item)}
          </p>

          {valueChange ? (
            <div className="mt-3">
              <ThreeFieldEditor
                valueChange={valueChange}
                resultValue={formatInputValue(resultDefaultValueForItem(item))}
                editable={false}
                compact
              />
            </div>
          ) : (
            <div className="mt-3 grid gap-2 md:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] md:items-center">
              <ValueBlock label="Current" value={currentValueForItem(item)} />
              <ArrowRight className="hidden size-4 text-slate-300 md:block" aria-hidden="true" />
              <ValueBlock label="Proposed" value={proposedValueForItem(item)} />
            </div>
          )}

          <div className="mt-3 flex flex-wrap gap-2 text-xs text-slate-500">
            <span className="rounded border border-slate-200 bg-slate-50 px-2 py-1">
              Source: {sourceSummary}
            </span>
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
            <NewsContextChips
              context={newsContext}
              emittedValue={proposedValueForItem(item)}
              stopPropagation
            />
          </div>

          {warningText ? (
            <div className="mt-3 flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
              <GitCompareArrows className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              <p>{warningText}</p>
            </div>
          ) : null}

          {supportingEvidence.length || dissentingEvidence.length ? (
            <div className="mt-3 grid gap-2 lg:grid-cols-2">
              <EvidenceSummarySection label="Supporting" rows={supportingEvidence} />
              <EvidenceSummarySection label="Against" rows={dissentingEvidence} />
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
          {matchCandidates.length > 1 ? (
            <div className="mt-3 grid gap-2 md:grid-cols-2">
              {matchCandidates.map((candidate) => (
                <button
                  key={`${item.id}-${candidate.id}`}
                  type="button"
                  className="rounded-md border border-slate-200 bg-white px-3 py-2 text-left text-xs hover:border-teal-300 hover:bg-teal-50 disabled:pointer-events-none disabled:opacity-50"
                  disabled={disabled}
                  onClick={(event) => {
                    event.stopPropagation();
                    onStage(item, "accept_new", { project_id: candidate.id });
                  }}
                >
                  <span className="block font-medium text-slate-950">{candidate.projectName}</span>
                  <span className="mt-0.5 block text-slate-500">{candidate.canonicalAddress}</span>
                  <span className="mt-1 block text-slate-500">
                    {compactStatus(candidate.pipelineStatus)}
                    {candidate.totalUnits !== null ? ` - ${candidate.totalUnits.toLocaleString()} units` : ""}
                  </span>
                </button>
              ))}
            </div>
          ) : null}
        </div>

        <div className="flex shrink-0 flex-wrap gap-2 xl:max-w-72 xl:justify-end">
          {valueChange ? (
            <DecisionButton
              keycap="c"
              label="Confirm"
              disabled={disabled}
              pending={pending}
              onClick={() => onStage(item, "custom", { value: resultDefaultValueForItem(item) })}
            />
          ) : (
            <>
              <DecisionButton
                keycap="a"
                label={item.itemType === "new_candidate" ? "Create project" : "Accept new"}
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
            </>
          )}
          <DecisionButton
            keycap="d"
            label="Defer"
            disabled={disabled}
            pending={pending}
            onClick={() => onStage(item, "defer")}
          />
          {!valueChange ? (
            <DecisionButton
              keycap="f"
              label="Custom"
              disabled={disabled || item.itemType === "new_candidate" || item.itemType === "possible_match"}
              pending={pending}
              onClick={() => onStage(item, "custom")}
            />
          ) : null}
          <Link
            className="inline-flex h-8 items-center justify-center gap-2 rounded-md border border-slate-300 bg-white px-2 text-xs font-medium text-slate-800 hover:bg-slate-50"
            href={reviewItemHref(item.id, jurisdictionId)}
            onClick={(event) => event.stopPropagation()}
          >
            <ExternalLink className="size-3.5" aria-hidden="true" />
            Detail
          </Link>
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

function NewsContextChips({
  context,
  emittedValue,
  stopPropagation = false
}: {
  context: NewsContext | null;
  emittedValue?: unknown;
  stopPropagation?: boolean;
}) {
  if (!context) {
    return null;
  }
  const disagreementText = structuralDisagreementText(context, emittedValue);
  const onClick = stopPropagation
    ? (event: MouseEvent) => event.stopPropagation()
    : undefined;
  return (
    <>
      {context.extractionConfidence ? (
        <span className="inline-flex items-center gap-1 rounded border border-sky-200 bg-sky-50 px-2 py-1 text-xs text-sky-800">
          <Newspaper className="size-3.5" aria-hidden="true" />
          extraction: {humanize(context.extractionConfidence)}
        </span>
      ) : null}
      {context.referenceIndex !== null ? (
        <span className="rounded border border-slate-200 px-2 py-1 text-xs text-slate-600">
          ref {context.referenceIndex + 1}
        </span>
      ) : null}
      {context.structuralDisagreement ? (
        <span
          className="inline-flex items-center gap-1 rounded border border-rose-200 bg-rose-50 px-2 py-1 text-xs text-rose-800"
          title={disagreementText ?? undefined}
        >
          <AlertTriangle className="size-3.5" aria-hidden="true" />
          Structural disagreement
        </span>
      ) : null}
      {context.url ? (
        <a
          className="inline-flex items-center gap-1 rounded border border-slate-200 px-2 py-1 text-xs text-slate-600 hover:border-teal-300 hover:text-teal-800"
          href={context.url}
          target="_blank"
          rel="noopener noreferrer"
          onClick={onClick}
        >
          <ExternalLink className="size-3.5" aria-hidden="true" />
          Article
        </a>
      ) : null}
    </>
  );
}

function EvidenceSummarySection({
  label,
  rows,
  defaultOpen = false
}: {
  label: string;
  rows: ReviewEvidenceSummary[];
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const visibleRows = rows.slice(0, 5);

  return (
    <div className="rounded-md border border-slate-200 bg-slate-50">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-xs font-semibold text-slate-700"
        onClick={(event) => {
          event.stopPropagation();
          setOpen((value) => !value);
        }}
      >
        <span>
          {label} ({rows.length})
        </span>
        <ChevronDown
          className={cn("size-3.5 transition-transform", open && "rotate-180")}
          aria-hidden="true"
        />
      </button>
      {open ? (
        <div className="border-t border-slate-200 bg-white">
          {visibleRows.length ? (
            visibleRows.map((row) => <EvidenceSummaryRow key={row.evidenceId} row={row} />)
          ) : (
            <p className="px-3 py-2 text-xs text-slate-500">No evidence in this bucket.</p>
          )}
          {rows.length > visibleRows.length ? (
            <p className="border-t border-slate-100 px-3 py-2 text-xs text-slate-500">
              {rows.length - visibleRows.length} more in detail view.
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function EvidenceSummaryRow({ row }: { row: ReviewEvidenceSummary }) {
  return (
    <div className="border-b border-slate-100 px-3 py-2 text-xs last:border-b-0">
      <div className="flex items-start gap-2">
        {row.isWinning ? (
          <Star className="mt-0.5 size-3.5 shrink-0 fill-amber-400 text-amber-500" aria-hidden="true" />
        ) : (
          <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-slate-300" />
        )}
        <div className="min-w-0">
          <p className="break-words font-medium text-slate-800">{row.summary}</p>
          {row.highlights[0]?.passage ? (
            <p className="mt-1 break-words text-slate-600">
              {formatValue(row.highlights[0].passage)}
            </p>
          ) : null}
          <SourceFieldsInline fields={row.sourceFields} />
          <p className="mt-0.5 text-slate-500">
            {humanize(row.sourceType)}
            {row.evidenceDate ? ` - ${formatDate(row.evidenceDate)}` : ""}
            {row.isWinning ? " - winning" : ""}
          </p>
        </div>
      </div>
    </div>
  );
}

/** Renders the source-type-specific structured fields populated by
 * SnippetPayload.source_fields. Useful for permit + CoStar regression cards
 * where reviewers want to see permit_number / permit_type / status_desc /
 * costar_property_id / upload_date at a glance without parsing the prose
 * summary. Skips rendering for empty or null field maps. */
function SourceFieldsInline({ fields }: { fields: Record<string, unknown> }) {
  const entries = Object.entries(fields).filter(
    ([, value]) => value !== null && value !== undefined && value !== ""
  );
  if (entries.length === 0) {
    return null;
  }
  return (
    <p className="mt-1 break-words text-slate-600">
      {entries.map(([key, value], index) => (
        <span key={key}>
          {index > 0 ? " · " : ""}
          <span className="text-slate-400">{humanize(key)}:</span>{" "}
          <span className="font-medium text-slate-700">{String(value)}</span>
        </span>
      ))}
    </p>
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
  priorityFilter,
  sourceFilter
}: {
  data: ReviewQueueData;
  currentUserId: string;
  currentUserEmail: string | null;
  search: string;
  priorityFilter: string;
  sourceFilter: SourceFilter;
}): ReviewSection[] {
  const groupMap = new Map<string, ReviewQueueItem[]>();
  for (const item of data.items) {
    if (!itemMatchesFilters(item, data, search, priorityFilter, sourceFilter)) {
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

function nextFocusAfterItem(groups: ReviewGroup[], itemId: string) {
  const flattened = groups.flatMap((group) =>
    group.items.map((item) => ({ groupKey: group.key, itemId: item.id }))
  );
  const index = flattened.findIndex((item) => item.itemId === itemId);
  if (index < 0) {
    return null;
  }
  return flattened[index + 1] ?? flattened[index - 1] ?? null;
}

function itemMatchesFilters(
  item: ReviewQueueItem,
  data: ReviewQueueData,
  search: string,
  priorityFilter: string,
  sourceFilter: SourceFilter
) {
  if (priorityFilter === "deferred") {
    if (item.activeDecision?.decisionType !== "defer") {
      return false;
    }
  } else if (priorityFilter !== "all" && item.priority.toLowerCase() !== priorityFilter) {
    return false;
  }
  const sourceRun = item.sourceRunId ? data.sourceRuns[item.sourceRunId] : null;
  if (!itemMatchesSourceFilter(item, sourceRun, sourceFilter)) {
    return false;
  }
  if (!search.trim()) {
    return true;
  }
  const project = item.projectId ? data.projects[item.projectId] : null;
  const haystack = [
    project?.projectName,
    project?.canonicalAddress,
    project?.developer,
    sourceRun?.sourceName,
    sourceSummaryForItem(item, sourceRun),
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

function itemMatchesSourceFilter(
  item: ReviewQueueItem,
  sourceRun: ReviewSourceRunSummary | null | undefined,
  sourceFilter: SourceFilter
) {
  if (sourceFilter === "all") {
    return true;
  }
  const families = sourceFamiliesForItem(item, sourceRun);
  if (sourceFilter === "multiple") {
    return families.size > 1;
  }
  if (sourceFilter === "other") {
    return families.size === 0 || (families.size === 1 && families.has("other"));
  }
  return families.has(sourceFilter);
}

function sourceSummaryForItem(
  item: ReviewQueueItem,
  sourceRun: ReviewSourceRunSummary | null | undefined
) {
  const families = sourceFamiliesForItem(item, sourceRun);
  if (!families.size) {
    return SOURCE_FAMILY_LABELS.other;
  }
  return SOURCE_FAMILY_ORDER.filter((family) => families.has(family))
    .map((family) => SOURCE_FAMILY_LABELS[family])
    .join(" + ");
}

function sourceFamiliesForItem(
  item: ReviewQueueItem,
  sourceRun: ReviewSourceRunSummary | null | undefined
) {
  const families = new Set<SourceFamily>();
  if (newsContextForItem(item)) {
    families.add("news");
  }
  for (const evidence of item.evidenceSummaries) {
    const family = sourceFamilyForText(evidence.sourceType);
    if (family) {
      families.add(family);
    }
  }
  const sourceRunFamily = sourceFamilyForText(sourceRun?.sourceName);
  if (sourceRunFamily) {
    families.add(sourceRunFamily);
  }
  const payloadSourceFamily = sourceFamilyForText(asString(item.payload?.source_type));
  if (payloadSourceFamily) {
    families.add(payloadSourceFamily);
  }
  return families;
}

function sourceFamilyForText(value: string | null | undefined): SourceFamily | null {
  const normalized = value?.toLowerCase();
  if (!normalized) {
    return null;
  }
  if (
    normalized.includes("news") ||
    normalized.includes("article") ||
    normalized.includes("urbanize") ||
    normalized.includes("yimby") ||
    normalized.includes("bisnow") ||
    normalized.includes("bizjournals")
  ) {
    return "news";
  }
  if (normalized.includes("costar")) {
    return "costar";
  }
  if (normalized.includes("pipedream")) {
    return "pipedream";
  }
  if (
    normalized.includes("ladbs") ||
    normalized.includes("lahd") ||
    normalized.includes("permit") ||
    normalized.includes("inspection") ||
    normalized.includes("planning") ||
    normalized.includes("entitlement") ||
    normalized.includes("socrata") ||
    normalized.includes("zimas")
  ) {
    return "permit";
  }
  return "other";
}

function sectionKeyForGroup(group: ReviewGroup) {
  if (group.allDeferred) {
    return "deferred";
  }
  const priority = group.priority.toLowerCase();
  return priority in SECTION_LABELS ? priority : "low";
}

function canAcceptItem(item: ReviewQueueItem) {
  if (item.itemType === "new_candidate") {
    return true;
  }
  if (item.itemType === "possible_match") {
    return candidateProjectIdsForItem(item).length === 1;
  }
  return true;
}

function possibleMatchCandidateProjects(
  item: ReviewQueueItem,
  projects: Record<string, ReviewProjectSummary>
) {
  if (item.itemType !== "possible_match") {
    return [];
  }
  return candidateProjectIdsForItem(item)
    .map((projectId) => projects[projectId])
    .filter((project): project is ReviewProjectSummary => Boolean(project));
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

function reviewItemHref(itemId: string, jurisdictionId: string | null) {
  const params = new URLSearchParams();
  if (jurisdictionId) {
    params.set("jurisdiction_id", jurisdictionId);
  }
  const query = params.toString();
  return query ? `/review/${itemId}?${query}` : `/review/${itemId}`;
}

function reviewPageHref(tab: ReviewTab, jurisdictionId: string | null) {
  const params = new URLSearchParams();
  if (tab !== "queue") {
    params.set("tab", tab);
  }
  if (jurisdictionId) {
    params.set("jurisdiction_id", jurisdictionId);
  }
  const query = params.toString();
  return query ? `/review?${query}` : "/review";
}

function hasActiveSearchOrFilter({
  activeTab,
  search,
  priorityFilter,
  sourceFilter,
  reviewedFieldFilter,
  reviewedOutcomeFilter,
  reviewedDeciderFilter
}: {
  activeTab: ReviewTab;
  search: string;
  priorityFilter: string;
  sourceFilter: SourceFilter;
  reviewedFieldFilter: string;
  reviewedOutcomeFilter: string;
  reviewedDeciderFilter: string;
}) {
  if (search) {
    return true;
  }
  if (activeTab === "reviewed") {
    return Boolean(reviewedFieldFilter || reviewedOutcomeFilter || reviewedDeciderFilter);
  }
  return priorityFilter !== "all" || sourceFilter !== "all";
}

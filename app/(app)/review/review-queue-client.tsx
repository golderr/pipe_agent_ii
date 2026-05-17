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
  Link2,
  ListChecks,
  Newspaper,
  RotateCcw,
  Save,
  Star
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState, useTransition, type MouseEvent } from "react";
import {
  commitReviewDecisionsAction,
  createAndLinkDiscoveryProjectAction,
  createDiscoveryProjectAction,
  fetchDedupCandidatesAction,
  fetchMatchPreviewAction,
  matchDiscoveryCandidateAction,
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
  candidateBandTone,
  candidateFocusByNumber,
  candidateFocusByOffset,
  applyDiscoverySubjectEdits,
  computeCandidateDeltas,
  computeCandidateOverlaps,
  discoverySubjectEditsPayload,
  isDiscoveryItem,
  matchPreviewImpactText,
  projectFieldsFromDiscoverySubject,
  searchedSummary,
  sortCandidates,
  visibleMatchSignals,
  type DiscoveryCandidate,
  type DiscoveryFieldDelta,
  type DiscoveryOverlap,
  type DiscoveryCandidateSort,
  type DiscoveryCandidateSortField,
  type DiscoveryCandidateSearch,
  type DiscoveryCard,
  type DiscoveryMatchPreview,
  type DiscoverySubject,
  type DiscoverySubjectEdits,
  type DiscoverySubjectEditField
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

type CandidateCacheEntry =
  | { status: "loading"; includeLayer3: boolean }
  | { status: "loaded"; data: DiscoveryCandidateSearch; includeLayer3: boolean }
  | { status: "error"; message: string; includeLayer3: boolean };

type MatchPreviewCacheEntry =
  | { status: "loading" }
  | { status: "loaded"; data: DiscoveryMatchPreview }
  | { status: "error"; message: string };

type CreateNewPrompt = {
  card: DiscoveryCard;
  edits: Record<string, unknown>;
  projectFields: Record<string, unknown>;
} | null;

type MatchDeltasPrompt = {
  card: DiscoveryCard;
  candidate: DiscoveryCandidate;
  deltas: DiscoveryFieldDelta[];
  edits: Record<string, unknown>;
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

const MATCH_PREVIEW_DEBOUNCE_MS = 150;
const DISCOVERY_RELATIONSHIP_OPTIONS = [
  { value: "phase", label: "Phase sibling" },
  { value: "master_plan", label: "Master project" },
  { value: "counterpart", label: "Counterpart" },
  { value: "supersedes", label: "Supersedes" }
];
const SUBJECT_PIPELINE_STATUS_OPTIONS = [
  "Conceptual",
  "Proposed",
  "Pending",
  "Approved",
  "Under Construction",
  "Pre-Leasing/Pre-Selling",
  "Complete",
  "Stalled",
  "Inactive"
];
const SUBJECT_PRODUCT_TYPE_OPTIONS = [
  "Apartment",
  "Condo",
  "Single-Family",
  "Townhome",
  "Micro/Co-Living",
  "Other",
  "Unknown"
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
  const [candidateCache, setCandidateCache] = useState<Record<string, CandidateCacheEntry>>({});
  const [matchPreviewCache, setMatchPreviewCache] = useState<Record<string, MatchPreviewCacheEntry>>(
    {}
  );
  const [createNewPrompt, setCreateNewPrompt] = useState<CreateNewPrompt>(null);
  const [matchDeltasPrompt, setMatchDeltasPrompt] = useState<MatchDeltasPrompt>(null);
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

  const requestDiscoveryCandidates = useCallback(
    (
      itemId: string,
      options: { includeLayer3?: boolean; force?: boolean } = {}
    ) => {
      const includeLayer3 = options.includeLayer3 === true;
      setCandidateCache((cache) => {
        const existing = cache[itemId];
        if (existing?.status === "loading") {
          return cache;
        }
        if (
          existing?.status === "loaded" &&
          !options.force &&
          (existing.includeLayer3 || !includeLayer3)
        ) {
          return cache;
        }
        return { ...cache, [itemId]: { status: "loading", includeLayer3 } };
      });

      void fetchDedupCandidatesAction(itemId, { includeLayer3 }).then((result) => {
        setCandidateCache((cache) => ({
          ...cache,
          [itemId]: result.ok
            ? { status: "loaded", data: result.data, includeLayer3 }
            : { status: "error", message: result.message, includeLayer3 }
        }));
      });
    },
    []
  );

  const requestMatchPreview = useCallback((itemId: string, candidateId: string) => {
    const cacheKey = matchPreviewCacheKey(itemId, candidateId);
    setMatchPreviewCache((cache) => {
      const existing = cache[cacheKey];
      if (existing?.status === "loading" || existing?.status === "loaded") {
        return cache;
      }
      return { ...cache, [cacheKey]: { status: "loading" } };
    });

    void fetchMatchPreviewAction(itemId, candidateId).then((result) => {
      setMatchPreviewCache((cache) => ({
        ...cache,
        [cacheKey]: result.ok
          ? { status: "loaded", data: result.data }
          : { status: "error", message: result.message }
      }));
    });
  }, []);

  const completeDiscoveryWrite = useCallback(
    (card: DiscoveryCard, message: string) => {
      const nextCardKey = nextDiscoveryCardKey(discoveryCards, card.key);
      setCreateNewPrompt(null);
      setMatchDeltasPrompt(null);
      setCandidateCache({});
      setMatchPreviewCache({});
      if (nextCardKey) {
        setFocusedDiscoveryCardId(nextCardKey);
      }
      setBanner({ tone: "success", message });
      router.refresh();
    },
    [discoveryCards, router]
  );

  const runMatchDiscoveryCandidate = useCallback(
    (
      card: DiscoveryCard,
      candidate: DiscoveryCandidate,
      input: { edits: Record<string, unknown>; acceptDeltas: string[] }
    ) => {
      setPendingItemId(card.item.id);
      setBanner(null);
      startTransition(async () => {
        const result = await matchDiscoveryCandidateAction({
          reviewItemId: card.item.id,
          matchedProjectId: candidate.projectId,
          edits: input.edits,
          acceptDeltas: input.acceptDeltas
        });
        setPendingItemId(null);
        if (!result.ok) {
          setBanner({ tone: "error", message: result.message });
          return;
        }
        completeDiscoveryWrite(card, result.message);
      });
    },
    [completeDiscoveryWrite]
  );

  const handleMatchDiscoveryCandidate = useCallback(
    (
      card: DiscoveryCard,
      candidate: DiscoveryCandidate,
      subject: DiscoverySubject,
      edits: Record<string, unknown>
    ) => {
      const deltas = computeCandidateDeltas(subject, candidate);
      if (deltas.length > 0) {
        setMatchDeltasPrompt({ card, candidate, deltas, edits });
        return;
      }
      runMatchDiscoveryCandidate(card, candidate, { edits, acceptDeltas: [] });
    },
    [runMatchDiscoveryCandidate]
  );

  const handleCreateDiscoveryProject = useCallback(
    (prompt: Exclude<CreateNewPrompt, null>) => {
      setPendingItemId(prompt.card.item.id);
      setBanner(null);
      startTransition(async () => {
        const result = await createDiscoveryProjectAction({
          reviewItemId: prompt.card.item.id,
          edits: prompt.edits,
          projectFields: prompt.projectFields
        });
        setPendingItemId(null);
        if (!result.ok) {
          setBanner({ tone: "error", message: result.message });
          return;
        }
        completeDiscoveryWrite(prompt.card, result.message);
      });
    },
    [completeDiscoveryWrite]
  );

  const handleCreateAndLinkDiscoveryProject = useCallback(
    (
      card: DiscoveryCard,
      candidate: DiscoveryCandidate,
      relationshipType: string,
      edits: Record<string, unknown>,
      projectFields: Record<string, unknown>
    ) => {
      setPendingItemId(card.item.id);
      setBanner(null);
      startTransition(async () => {
        const result = await createAndLinkDiscoveryProjectAction({
          reviewItemId: card.item.id,
          relatedProjectId: candidate.projectId,
          relationshipType,
          edits,
          projectFields
        });
        setPendingItemId(null);
        if (!result.ok) {
          setBanner({ tone: "error", message: result.message });
          return;
        }
        completeDiscoveryWrite(card, result.message);
      });
    },
    [completeDiscoveryWrite]
  );

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
          candidateCache={candidateCache}
          matchPreviewCache={matchPreviewCache}
          sourceRuns={data.sourceRuns}
          jurisdictionId={jurisdictionId}
          onSelect={setFocusedDiscoveryCardId}
          onRequestCandidates={requestDiscoveryCandidates}
          onRequestMatchPreview={requestMatchPreview}
          onOpenCreateNew={(card, edits, projectFields) =>
            setCreateNewPrompt({ card, edits, projectFields })
          }
          onMatchCandidate={handleMatchDiscoveryCandidate}
          onCreateAndLink={handleCreateAndLinkDiscoveryProject}
          isPending={isPending}
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

      {createNewPrompt ? (
        <CreateNewConfirmModal
          card={createNewPrompt.card}
          onClose={() => setCreateNewPrompt(null)}
          onConfirm={() => handleCreateDiscoveryProject(createNewPrompt)}
          pending={pendingItemId === createNewPrompt.card.item.id || isPending}
        />
      ) : null}

      {matchDeltasPrompt ? (
        <MatchDeltasModal
          prompt={matchDeltasPrompt}
          onClose={() => setMatchDeltasPrompt(null)}
          onConfirm={(acceptDeltas) =>
            runMatchDiscoveryCandidate(matchDeltasPrompt.card, matchDeltasPrompt.candidate, {
              edits: matchDeltasPrompt.edits,
              acceptDeltas
            })
          }
          pending={pendingItemId === matchDeltasPrompt.card.item.id || isPending}
        />
      ) : null}

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

function matchPreviewCacheKey(itemId: string, candidateId: string) {
  return `${itemId}:${candidateId}`;
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
  candidateCache,
  matchPreviewCache,
  sourceRuns,
  jurisdictionId,
  onSelect,
  onRequestCandidates,
  onRequestMatchPreview,
  onOpenCreateNew,
  onMatchCandidate,
  onCreateAndLink,
  isPending
}: {
  cards: DiscoveryCard[];
  selectedCardKey: string | null;
  candidateCache: Record<string, CandidateCacheEntry>;
  matchPreviewCache: Record<string, MatchPreviewCacheEntry>;
  sourceRuns: Record<string, ReviewSourceRunSummary>;
  jurisdictionId: string | null;
  onSelect: (cardKey: string) => void;
  onRequestCandidates: (
    itemId: string,
    options?: { includeLayer3?: boolean; force?: boolean }
  ) => void;
  onRequestMatchPreview: (itemId: string, candidateId: string) => void;
  onOpenCreateNew: (
    card: DiscoveryCard,
    edits: Record<string, unknown>,
    projectFields: Record<string, unknown>
  ) => void;
  onMatchCandidate: (
    card: DiscoveryCard,
    candidate: DiscoveryCandidate,
    subject: DiscoverySubject,
    edits: Record<string, unknown>
  ) => void;
  onCreateAndLink: (
    card: DiscoveryCard,
    candidate: DiscoveryCandidate,
    relationshipType: string,
    edits: Record<string, unknown>,
    projectFields: Record<string, unknown>
  ) => void;
  isPending: boolean;
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
                  {humanize(card.item.itemType)} -{" "}
                  {newCandidateProbabilityLabel(card.newCandidateProbability)}
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
            candidatesEntry={candidateCache[selectedCard.item.id]}
            key={selectedCard.key}
            matchPreviewCache={matchPreviewCache}
            sourceRun={
              selectedCard.item.sourceRunId ? sourceRuns[selectedCard.item.sourceRunId] : undefined
            }
            jurisdictionId={jurisdictionId}
            onRequestCandidates={onRequestCandidates}
            onRequestMatchPreview={onRequestMatchPreview}
            onOpenCreateNew={onOpenCreateNew}
            onMatchCandidate={onMatchCandidate}
            onCreateAndLink={onCreateAndLink}
            isPending={isPending}
          />
        ) : null}
      </section>
    </div>
  );
}

function DiscoveryCardShell({
  card,
  candidatesEntry,
  matchPreviewCache,
  sourceRun,
  jurisdictionId,
  onRequestCandidates,
  onRequestMatchPreview,
  onOpenCreateNew,
  onMatchCandidate,
  onCreateAndLink,
  isPending
}: {
  card: DiscoveryCard;
  candidatesEntry: CandidateCacheEntry | undefined;
  matchPreviewCache: Record<string, MatchPreviewCacheEntry>;
  sourceRun: ReviewSourceRunSummary | undefined;
  jurisdictionId: string | null;
  onRequestCandidates: (
    itemId: string,
    options?: { includeLayer3?: boolean; force?: boolean }
  ) => void;
  onRequestMatchPreview: (itemId: string, candidateId: string) => void;
  onOpenCreateNew: (
    card: DiscoveryCard,
    edits: Record<string, unknown>,
    projectFields: Record<string, unknown>
  ) => void;
  onMatchCandidate: (
    card: DiscoveryCard,
    candidate: DiscoveryCandidate,
    subject: DiscoverySubject,
    edits: Record<string, unknown>
  ) => void;
  onCreateAndLink: (
    card: DiscoveryCard,
    candidate: DiscoveryCandidate,
    relationshipType: string,
    edits: Record<string, unknown>,
    projectFields: Record<string, unknown>
  ) => void;
  isPending: boolean;
}) {
  useEffect(() => {
    if (!candidatesEntry) {
      onRequestCandidates(card.item.id);
    }
  }, [card.item.id, candidatesEntry, onRequestCandidates]);

  const [candidateSort, setCandidateSort] = useState<DiscoveryCandidateSort>({
    field: "matchLikelihood",
    direction: "desc"
  });
  const [focusedCandidateId, setFocusedCandidateId] = useState<string | null>(null);
  const [subjectEdits, setSubjectEdits] = useState<DiscoverySubjectEdits>({});
  const [linkCandidateId, setLinkCandidateId] = useState<string | null>(null);
  const [relationshipType, setRelationshipType] = useState("phase");
  const candidates = candidatesEntry?.status === "loaded" ? candidatesEntry.data : null;
  const sortedCandidates = useMemo(
    () => sortCandidates(candidates?.candidates ?? [], candidateSort),
    [candidateSort, candidates]
  );
  const focusedCandidate =
    sortedCandidates.find((candidate) => candidate.projectId === focusedCandidateId) ??
    sortedCandidates[0] ??
    null;
  const baseSubject = candidates?.subject ?? card.subject;
  const subject = useMemo(
    () => applyDiscoverySubjectEdits(baseSubject, subjectEdits),
    [baseSubject, subjectEdits]
  );
  const discoveryEdits = useMemo(
    () => discoverySubjectEditsPayload(subjectEdits),
    [subjectEdits]
  );
  const discoveryProjectFields = useMemo(
    () => projectFieldsFromDiscoverySubject(subject),
    [subject]
  );
  const potentialMatchCount = candidates?.candidates.length ?? card.potentialMatchCount;
  const newCandidateProbability =
    candidates?.newCandidateProbability ?? card.newCandidateProbability;
  const focusedPreviewEntry = focusedCandidate
    ? matchPreviewCache[matchPreviewCacheKey(card.item.id, focusedCandidate.projectId)]
    : undefined;
  const onSort = (field: DiscoveryCandidateSortField) => {
    setCandidateSort((current) => {
      if (current.field === field) {
        return {
          field,
          direction: current.direction === "asc" ? "desc" : "asc"
        };
      }
      return { field, direction: defaultCandidateSortDirection(field) };
    });
  };

  const openCreateNew = useCallback(() => {
    onOpenCreateNew(card, discoveryEdits, discoveryProjectFields);
  }, [card, discoveryEdits, discoveryProjectFields, onOpenCreateNew]);

  const matchCandidate = useCallback(
    (candidate: DiscoveryCandidate) => {
      onMatchCandidate(card, candidate, subject, discoveryEdits);
    },
    [card, discoveryEdits, onMatchCandidate, subject]
  );

  const createAndLinkCandidate = useCallback(
    (candidate: DiscoveryCandidate) => {
      onCreateAndLink(
        card,
        candidate,
        relationshipType,
        discoveryEdits,
        discoveryProjectFields
      );
    },
    [card, discoveryEdits, discoveryProjectFields, onCreateAndLink, relationshipType]
  );

  useEffect(() => {
    if (!focusedCandidate) {
      return;
    }
    // Row 0 is the highest-ranked match candidate, so loading its preview on
    // card open is intentional; later focus moves reuse the same preview cache.
    const cacheKey = matchPreviewCacheKey(card.item.id, focusedCandidate.projectId);
    if (matchPreviewCache[cacheKey]) {
      return;
    }
    const timeoutId = window.setTimeout(() => {
      onRequestMatchPreview(card.item.id, focusedCandidate.projectId);
    }, MATCH_PREVIEW_DEBOUNCE_MS);
    return () => window.clearTimeout(timeoutId);
  }, [card.item.id, focusedCandidate, matchPreviewCache, onRequestMatchPreview]);

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
      const key = event.key.toLowerCase();
      if (/^[1-9]$/.test(key)) {
        const nextId = candidateFocusByNumber(sortedCandidates, key);
        if (nextId) {
          event.preventDefault();
          setFocusedCandidateId(nextId);
        }
        return;
      }
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setFocusedCandidateId(
          candidateFocusByOffset(sortedCandidates, focusedCandidate?.projectId ?? null, 1)
        );
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setFocusedCandidateId(
          candidateFocusByOffset(sortedCandidates, focusedCandidate?.projectId ?? null, -1)
        );
        return;
      }
      if (key === "n") {
        event.preventDefault();
        openCreateNew();
        return;
      }
      if (key === "m" && focusedCandidate) {
        event.preventDefault();
        matchCandidate(focusedCandidate);
        return;
      }
      if (key === "l" && focusedCandidate) {
        event.preventDefault();
        setLinkCandidateId(focusedCandidate.projectId);
      }
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [focusedCandidate, matchCandidate, openCreateNew, sortedCandidates]);

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
            <DiscoveryMetric label="Matches" value={potentialMatchCount.toLocaleString()} />
            <DiscoveryMetric
              label="New %"
              value={
                newCandidateProbability !== null
                  ? `${Math.round(newCandidateProbability * 100)}`
                  : "-"
              }
            />
            <Button
              type="button"
              variant="outline"
              className="col-span-2 h-8 text-xs"
              onClick={openCreateNew}
              disabled={isPending}
            >
              Create new
            </Button>
          </div>
        </div>
      </div>
      <div className="overflow-x-auto px-4 py-3">
        <table className="w-full min-w-[72rem] table-fixed text-left text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-xs font-medium uppercase tracking-normal text-slate-500">
              <CandidateHeaderCell
                className="w-44"
                field="projectName"
                label="Project"
                sort={candidateSort}
                onSort={onSort}
              />
              <CandidateHeaderCell
                className="w-64"
                field="canonicalAddress"
                label="Address"
                sort={candidateSort}
                onSort={onSort}
              />
              <CandidateHeaderCell
                className="w-44"
                field="developer"
                label="Developer"
                sort={candidateSort}
                onSort={onSort}
              />
              <CandidateHeaderCell
                className="w-28"
                field="totalUnits"
                label="Units"
                sort={candidateSort}
                onSort={onSort}
              />
              <CandidateHeaderCell
                className="w-32"
                field="productType"
                label="Product"
                sort={candidateSort}
                onSort={onSort}
              />
              <CandidateHeaderCell
                className="w-32"
                field="pipelineStatus"
                label="Status"
                sort={candidateSort}
                onSort={onSort}
              />
              <CandidateHeaderCell
                className="w-24"
                field="stories"
                label="Stories"
                sort={candidateSort}
                onSort={onSort}
              />
              <CandidateHeaderCell
                className="w-44"
                field="matchLikelihood"
                label="Match"
                sort={candidateSort}
                onSort={onSort}
              />
            </tr>
          </thead>
          <tbody>
            <tr className="border-b border-slate-100 align-top last:border-b-0">
              <SubjectEditCell
                field="projectName"
                value={subject.projectName}
                onChange={(value) => setSubjectEdits((edits) => ({ ...edits, projectName: value }))}
              />
              <SubjectEditCell
                field="canonicalAddress"
                value={subject.canonicalAddress}
                onChange={(value) =>
                  setSubjectEdits((edits) => ({ ...edits, canonicalAddress: value }))
                }
              />
              <SubjectEditCell
                field="developer"
                value={subject.developer}
                onChange={(value) => setSubjectEdits((edits) => ({ ...edits, developer: value }))}
              />
              <SubjectEditCell
                field="totalUnits"
                value={subject.totalUnits}
                onChange={(value) => setSubjectEdits((edits) => ({ ...edits, totalUnits: value }))}
              />
              <SubjectEditCell
                field="productType"
                value={subject.productType}
                onChange={(value) => setSubjectEdits((edits) => ({ ...edits, productType: value }))}
              />
              <SubjectEditCell
                field="pipelineStatus"
                value={subject.pipelineStatus}
                onChange={(value) =>
                  setSubjectEdits((edits) => ({ ...edits, pipelineStatus: value }))
                }
              />
              <SubjectEditCell
                field="stories"
                value={subject.stories}
                onChange={(value) => setSubjectEdits((edits) => ({ ...edits, stories: value }))}
              />
              <td className="py-3 pr-3 text-slate-500">Subject</td>
            </tr>
            {sortedCandidates.map((candidate, index) => (
              <CandidateRow
                candidate={candidate}
                isFocused={candidate.projectId === focusedCandidate?.projectId}
                index={index}
                isPending={isPending}
                linkOpen={linkCandidateId === candidate.projectId}
                key={candidate.projectId}
                onCreateAndLink={() => createAndLinkCandidate(candidate)}
                onFocus={() => setFocusedCandidateId(candidate.projectId)}
                onMatch={() => matchCandidate(candidate)}
                onToggleLink={() =>
                  setLinkCandidateId((current) =>
                    current === candidate.projectId ? null : candidate.projectId
                  )
                }
                previewEntry={
                  candidate.projectId === focusedCandidate?.projectId
                    ? focusedPreviewEntry
                    : undefined
                }
                relationshipType={relationshipType}
                onRelationshipTypeChange={setRelationshipType}
                subject={subject}
              />
            ))}
          </tbody>
        </table>
        <CandidateTableSection
          entry={candidatesEntry}
          fallbackCard={card}
          onRetry={() =>
            onRequestCandidates(card.item.id, {
              includeLayer3: candidatesEntry?.includeLayer3,
              force: true
            })
          }
          onShowLayer3={() =>
            onRequestCandidates(card.item.id, { includeLayer3: true, force: true })
          }
        />
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-200 px-4 py-3 text-sm">
        <div className="text-slate-600">
          Potential matches: {potentialMatchCount.toLocaleString()} -{" "}
          {newCandidateProbabilityLabel(newCandidateProbability)}
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

function SubjectEditCell({
  field,
  value,
  onChange
}: {
  field: DiscoverySubjectEditField;
  value: unknown;
  onChange: (value: string) => void;
}) {
  const inputValue = formatInputValue(value);
  if (field === "pipelineStatus" || field === "productType") {
    const options = field === "pipelineStatus" ? SUBJECT_PIPELINE_STATUS_OPTIONS : SUBJECT_PRODUCT_TYPE_OPTIONS;
    return (
      <td className="py-3 pr-3">
        <select
          aria-label={`Subject ${humanize(camelToToken(field))}`}
          className="h-8 w-full min-w-0 rounded-md border border-slate-200 bg-white px-2 text-xs text-slate-900 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-100"
          value={inputValue}
          onChange={(event) => onChange(event.target.value)}
        >
          <option value="">-</option>
          {options.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      </td>
    );
  }
  const type = field === "totalUnits" || field === "stories" ? "number" : "text";
  return (
    <td className="py-3 pr-3">
      <Input
        aria-label={`Subject ${humanize(camelToToken(field))}`}
        className="h-8 min-w-0 px-2 text-xs"
        min={type === "number" ? 0 : undefined}
        onChange={(event) => onChange(event.target.value)}
        type={type}
        value={inputValue}
      />
    </td>
  );
}

function CandidateHeaderCell({
  field,
  label,
  sort,
  className,
  onSort
}: {
  field: DiscoveryCandidateSortField;
  label: string;
  sort: DiscoveryCandidateSort;
  className: string;
  onSort: (field: DiscoveryCandidateSortField) => void;
}) {
  const active = sort.field === field;
  return (
    <th
      aria-sort={active ? (sort.direction === "asc" ? "ascending" : "descending") : "none"}
      className={cn("py-2 pr-3", className)}
    >
      <button
        type="button"
        onClick={() => onSort(field)}
        className="inline-flex items-center gap-1 rounded px-1 py-0.5 text-left hover:bg-slate-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700"
      >
        {label}
        {active ? (
          <span aria-hidden="true" className="text-[10px] font-semibold text-teal-700">
            {sort.direction === "asc" ? "↑" : "↓"}
          </span>
        ) : null}
      </button>
    </th>
  );
}

function CandidateRow({
  candidate,
  isFocused,
  index,
  isPending,
  linkOpen,
  onCreateAndLink,
  onFocus,
  onMatch,
  onRelationshipTypeChange,
  onToggleLink,
  previewEntry,
  relationshipType,
  subject
}: {
  candidate: DiscoveryCandidate;
  isFocused: boolean;
  index: number;
  isPending: boolean;
  linkOpen: boolean;
  onCreateAndLink: () => void;
  onFocus: () => void;
  onMatch: () => void;
  onRelationshipTypeChange: (value: string) => void;
  onToggleLink: () => void;
  previewEntry: MatchPreviewCacheEntry | undefined;
  relationshipType: string;
  subject: DiscoverySubject;
}) {
  const overlaps = computeCandidateOverlaps(subject, candidate);
  return (
    <tr
      aria-selected={isFocused}
      className={cn(
        "border-b border-l-4 border-b-slate-100 align-top outline-none last:border-b-0",
        candidateBandClass(candidate),
        isFocused && "ring-2 ring-inset ring-teal-500"
      )}
      onClick={onFocus}
      onFocus={onFocus}
      tabIndex={0}
    >
      <CandidateValueCell
        overlap={overlaps.projectName}
        prefix={`${index + 1}. `}
        value={candidate.projectName ?? "Unnamed project"}
      />
      <CandidateValueCell overlap={overlaps.canonicalAddress} value={candidate.canonicalAddress} />
      <CandidateValueCell overlap={overlaps.developer} value={candidate.developer} />
      <CandidateValueCell overlap={overlaps.totalUnits} value={candidate.totalUnits} />
      <CandidateValueCell
        overlap={overlaps.productType}
        value={candidate.productType ?? candidate.ageRestriction}
      />
      <CandidateValueCell overlap={overlaps.pipelineStatus} value={candidate.pipelineStatus} />
      <CandidateValueCell overlap={overlaps.stories} value={candidate.stories} />
      <td className="py-3 pr-3 text-slate-800">
        <span className="font-medium">{Math.round(candidate.matchLikelihood * 100)}%</span>
        <span className="mt-1 block text-xs text-slate-500">Layer {candidate.matchLayer}</span>
        <NearSubjectChip overlap={overlaps.lat} />
        <CandidateSignalChips candidate={candidate} />
        {isFocused ? <MatchImpactPreview entry={previewEntry} /> : null}
        {isFocused ? (
          <CandidateRowActions
            isPending={isPending}
            linkOpen={linkOpen}
            onCreateAndLink={onCreateAndLink}
            onMatch={onMatch}
            onRelationshipTypeChange={onRelationshipTypeChange}
            onToggleLink={onToggleLink}
            relationshipType={relationshipType}
          />
        ) : null}
      </td>
    </tr>
  );
}

function CandidateRowActions({
  isPending,
  linkOpen,
  onCreateAndLink,
  onMatch,
  onRelationshipTypeChange,
  onToggleLink,
  relationshipType
}: {
  isPending: boolean;
  linkOpen: boolean;
  onCreateAndLink: () => void;
  onMatch: () => void;
  onRelationshipTypeChange: (value: string) => void;
  onToggleLink: () => void;
  relationshipType: string;
}) {
  return (
    <div className="mt-2 grid gap-1.5">
      <Button type="button" className="h-7 px-2 text-xs" disabled={isPending} onClick={onMatch}>
        Match
      </Button>
      <Button
        type="button"
        variant="outline"
        className="h-7 px-2 text-xs"
        disabled={isPending}
        onClick={onToggleLink}
      >
        <Link2 className="size-3" aria-hidden="true" />
        Create + link
      </Button>
      {linkOpen ? (
        <div className="grid gap-1 rounded border border-slate-200 bg-white/80 p-1.5">
          <select
            aria-label="Relationship type"
            className="h-7 rounded-md border border-slate-200 bg-white px-1.5 text-xs text-slate-900 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-100"
            value={relationshipType}
            onChange={(event) => onRelationshipTypeChange(event.target.value)}
          >
            {DISCOVERY_RELATIONSHIP_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <Button
            type="button"
            className="h-7 px-2 text-xs"
            disabled={isPending}
            onClick={onCreateAndLink}
          >
            Confirm link
          </Button>
        </div>
      ) : null}
    </div>
  );
}

function CandidateValueCell({
  value,
  overlap,
  prefix = ""
}: {
  value: unknown;
  overlap?: DiscoveryOverlap;
  prefix?: string;
}) {
  const label = `${prefix}${formatValue(value)}`;
  return (
    <td className="break-words py-3 pr-3 text-slate-800">
      {overlap ? (
        <span
          className="rounded bg-yellow-100/90 px-1 py-0.5 text-slate-950 ring-1 ring-yellow-300"
          title={overlapTooltip(overlap)}
        >
          {label}
        </span>
      ) : (
        label
      )}
    </td>
  );
}

function NearSubjectChip({ overlap }: { overlap?: DiscoveryOverlap }) {
  if (!overlap) {
    return null;
  }
  return (
    <div className="mt-2">
      <span
        className="rounded border border-yellow-300 bg-yellow-100/90 px-1.5 py-0.5 text-[11px] font-medium text-yellow-900"
        title={overlapTooltip(overlap)}
      >
        Near subject
      </span>
    </div>
  );
}

function MatchImpactPreview({ entry }: { entry: MatchPreviewCacheEntry | undefined }) {
  if (!entry || entry.status === "loading") {
    return (
      <p className="mt-2 rounded border border-slate-200 bg-white/70 px-2 py-1 text-[11px] text-slate-600">
        Loading impact preview.
      </p>
    );
  }
  if (entry.status === "error") {
    return (
      <p className="mt-2 rounded border border-red-200 bg-red-50 px-2 py-1 text-[11px] text-red-800">
        {entry.message}
      </p>
    );
  }
  return (
    <p className="mt-2 rounded border border-teal-200 bg-teal-50 px-2 py-1 text-[11px] text-teal-900">
      {matchPreviewImpactText(entry.data)}
    </p>
  );
}

function CandidateSignalChips({ candidate }: { candidate: DiscoveryCandidate }) {
  const signals = visibleMatchSignals(candidate);
  if (!signals.length) {
    return null;
  }
  return (
    <div className="mt-2 flex flex-wrap gap-1">
      {signals.map(([key, signal]) => (
        <span
          key={key}
          className={cn(
            "rounded border px-1.5 py-0.5 text-[11px] font-medium",
            signal.contributed
              ? "border-emerald-200 bg-emerald-50 text-emerald-800"
              : "border-slate-200 bg-slate-50 text-slate-600"
          )}
          title={signalTooltip(signal)}
        >
          {signal.label}
        </span>
      ))}
    </div>
  );
}

function signalTooltip(signal: DiscoveryCandidate["matchSignals"][string]) {
  const score = `score ${signal.score.toFixed(2)}`;
  const weight = signal.weight !== null ? `weight ${signal.weight.toFixed(2)}` : null;
  return [score, weight, signal.detail].filter(Boolean).join(" - ");
}

function overlapTooltip(overlap: DiscoveryOverlap) {
  const fieldLabel = humanize(camelToToken(overlap.matchedSubjectField));
  const value = formatValue(overlap.matchedValue);
  if (overlap.kind === "cross-field-unit-match") {
    return `Matches subject ${fieldLabel} (${value})`;
  }
  if (overlap.kind === "stories-proximity") {
    return `Within 2 stories (subject: ${value})`;
  }
  if (overlap.kind === "distance-threshold") {
    return overlap.detail ?? `Near subject ${fieldLabel} (${value})`;
  }
  if (overlap.kind === "text-substring") {
    return `Overlaps subject ${fieldLabel} (${value})`;
  }
  return `Matches subject ${fieldLabel} (${value})`;
}

function camelToToken(value: string) {
  return value.replace(/([a-z0-9])([A-Z])/g, "$1_$2");
}

function candidateBandClass(candidate: DiscoveryCandidate) {
  const tone = candidateBandTone(candidate);
  return cn(
    tone === "hard" && "border-l-emerald-500 bg-emerald-50/40",
    tone === "strong" && "border-l-teal-500 bg-teal-50/30",
    tone === "medium" && "border-l-amber-400 bg-amber-50/30",
    tone === "weak" && "border-l-orange-400 bg-orange-50/25",
    tone === "broad" && "border-l-slate-300 bg-slate-50/80"
  );
}

function defaultCandidateSortDirection(field: DiscoveryCandidateSortField): "asc" | "desc" {
  return field === "projectName" ||
    field === "canonicalAddress" ||
    field === "developer" ||
    field === "productType" ||
    field === "pipelineStatus"
    ? "asc"
    : "desc";
}

function CandidateTableSection({
  entry,
  fallbackCard,
  onRetry,
  onShowLayer3
}: {
  entry: CandidateCacheEntry | undefined;
  fallbackCard: DiscoveryCard;
  onRetry: () => void;
  onShowLayer3: () => void;
}) {
  if (!entry || entry.status === "loading") {
    return (
      <div className="mt-3 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-600">
        Loading candidates for {fallbackCard.title}.
      </div>
    );
  }
  if (entry.status === "error") {
    return (
      <div className="mt-3 flex items-center justify-between gap-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
        <span>{entry.message}</span>
        <Button type="button" variant="outline" className="h-8 px-2 text-xs" onClick={onRetry}>
          Retry
        </Button>
      </div>
    );
  }
  if (entry.data.candidates.length === 0) {
    return (
      <div className="mt-3 rounded-md border border-slate-200 bg-slate-50 px-3 py-3 text-sm text-slate-600">
        <p className="font-medium text-slate-800">No candidate projects found.</p>
        <p className="mt-1">{searchedSummary(entry.data)}</p>
        <Layer3Button entry={entry} onShowLayer3={onShowLayer3} />
      </div>
    );
  }
  return <Layer3Button entry={entry} onShowLayer3={onShowLayer3} />;
}

function Layer3Button({
  entry,
  onShowLayer3
}: {
  entry: Extract<CandidateCacheEntry, { status: "loaded" }>;
  onShowLayer3: () => void;
}) {
  if (!entry.data.layer3Available || entry.includeLayer3) {
    return null;
  }
  return (
    <div className="mt-3 flex justify-end">
      <Button type="button" variant="outline" className="h-8 px-2 text-xs" onClick={onShowLayer3}>
        Show broader sweep
      </Button>
    </div>
  );
}

function DiscoveryMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
      <p className="text-xs text-slate-500">{label}</p>
      <p className="text-lg font-semibold text-slate-950">{value}</p>
    </div>
  );
}

function CreateNewConfirmModal({
  card,
  onClose,
  onConfirm,
  pending
}: {
  card: DiscoveryCard;
  onClose: () => void;
  onConfirm: () => void;
  pending: boolean;
}) {
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-950/30 px-4">
      <section
        aria-labelledby="create-new-title"
        className="w-full max-w-md rounded-md border border-slate-200 bg-white p-4 shadow-xl"
        role="dialog"
      >
        <h2 id="create-new-title" className="text-base font-semibold text-slate-950">
          Create new project
        </h2>
        <p className="mt-2 text-sm text-slate-600">
          No match selected for {card.title}. Continue only when the subject is genuinely new.
        </p>
        <div className="mt-3 rounded border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700">
          <p className="font-medium text-slate-900">{card.title}</p>
          <p className="mt-0.5 text-xs text-slate-500">{card.subtitle}</p>
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose} disabled={pending}>
            Cancel
          </Button>
          <Button type="button" disabled={pending} onClick={onConfirm}>
            {pending ? "Creating..." : "Continue"}
          </Button>
        </div>
      </section>
    </div>
  );
}

function MatchDeltasModal({
  prompt,
  onClose,
  onConfirm,
  pending
}: {
  prompt: Exclude<MatchDeltasPrompt, null>;
  onClose: () => void;
  onConfirm: (acceptDeltas: string[]) => void;
  pending: boolean;
}) {
  const [acceptedFields, setAcceptedFields] = useState<Set<string>>(new Set());

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-950/30 px-4">
      <section
        aria-labelledby="match-deltas-title"
        className="max-h-[90dvh] w-full max-w-4xl overflow-auto rounded-md border border-slate-200 bg-white p-4 shadow-xl"
        role="dialog"
      >
        <h2 id="match-deltas-title" className="text-base font-semibold text-slate-950">
          Match with field differences
        </h2>
        <p className="mt-2 text-sm text-slate-600">
          Match {prompt.card.title} to{" "}
          {prompt.candidate.projectName ?? prompt.candidate.canonicalAddress ?? "this project"}.
          Checked fields update the matched project now; unchecked fields become value-change review
          items.
        </p>
        <div className="mt-4 grid gap-3">
          {prompt.deltas.map((delta) => {
            const checked = acceptedFields.has(delta.fieldName);
            return (
              <label
                key={delta.fieldName}
                className="grid gap-3 rounded-md border border-slate-200 p-3"
              >
                <div className="flex items-center gap-2">
                  <input
                    checked={checked}
                    className="size-4 accent-teal-700"
                    onChange={(event) => {
                      setAcceptedFields((current) => {
                        const next = new Set(current);
                        if (event.target.checked) {
                          next.add(delta.fieldName);
                        } else {
                          next.delete(delta.fieldName);
                        }
                        return next;
                      });
                    }}
                    type="checkbox"
                  />
                  <span className="text-sm font-medium text-slate-950">
                    Accept {delta.valueChange.fieldLabel}
                  </span>
                </div>
                <ThreeFieldEditor
                  compact
                  editable={false}
                  resultValue={formatInputValue(delta.valueChange.defaultResultValue)}
                  valueChange={delta.valueChange}
                />
              </label>
            );
          })}
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose} disabled={pending}>
            Cancel
          </Button>
          <Button
            type="button"
            disabled={pending}
            onClick={() => onConfirm(Array.from(acceptedFields))}
          >
            {pending ? "Matching..." : "Confirm match"}
          </Button>
        </div>
      </section>
    </div>
  );
}

function newCandidateProbabilityLabel(value: number | null) {
  if (value === null) {
    return "New probability unavailable";
  }
  return `New probability ${Math.round(value * 100)}%`;
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

function nextDiscoveryCardKey(cards: DiscoveryCard[], cardKey: string) {
  const index = cards.findIndex((card) => card.key === cardKey);
  if (index < 0) {
    return cards[0]?.key ?? null;
  }
  return cards[index + 1]?.key ?? cards[index - 1]?.key ?? null;
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

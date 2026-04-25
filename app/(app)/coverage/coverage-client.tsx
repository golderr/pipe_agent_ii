"use client";

import {
  ChevronDown,
  ChevronRight,
  Columns3,
  Filter,
  RefreshCw,
  Search,
  Star,
  Upload
} from "lucide-react";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type { CoverageJurisdiction, CoverageSourceSummary } from "@/lib/coverage/types";

type CoverageClientProps = {
  jurisdictions: CoverageJurisdiction[];
};

type QueueFilter = "any" | "pending" | "cleared" | "high";
type FreshnessFilter = "any" | "current" | "stale7" | "stale30";
type OptionalColumn = "gov" | "news" | "costar" | "lastReviewed" | "overrides";

type CoverageFilters = {
  search: string;
  market: string;
  state: string;
  queue: QueueFilter;
  freshness: FreshnessFilter;
  pinnedOnly: boolean;
  optionalColumns: OptionalColumn[];
};

const FILTER_STORAGE_KEY = "coverage:filters";
const PIN_STORAGE_KEY = "coverage:pinnedJurisdictions";

const DEFAULT_FILTERS: CoverageFilters = {
  search: "",
  market: "all",
  state: "all",
  queue: "any",
  freshness: "any",
  pinnedOnly: false,
  optionalColumns: ["gov", "news", "costar"]
};

const OPTIONAL_COLUMNS: Array<{ key: OptionalColumn; label: string }> = [
  { key: "gov", label: "Gov last" },
  { key: "news", label: "News last" },
  { key: "costar", label: "CoStar last" },
  { key: "lastReviewed", label: "Last reviewed" },
  { key: "overrides", label: "Overrides" }
];

const SOURCE_CLASS_STYLES: Record<string, string> = {
  gov: "border-green-200 bg-green-50 text-green-800",
  news: "border-amber-200 bg-amber-50 text-amber-900",
  costar: "border-purple-200 bg-purple-50 text-purple-800",
  web: "border-slate-200 bg-slate-50 text-slate-700",
  pipedream_seed: "border-teal-200 bg-teal-50 text-teal-800"
};

function number(value: number) {
  return new Intl.NumberFormat("en-US").format(value);
}

function titleFromSlug(value: string) {
  return value
    .split("_")
    .map((part) => {
      const upper = part.toUpperCase();
      if (["LA", "LADBS", "LAHD", "ZIMAS", "PDIS", "COFO"].includes(upper)) {
        return upper;
      }
      return part.charAt(0).toUpperCase() + part.slice(1);
    })
    .join(" ");
}

function formatDate(value: string | null) {
  if (!value) {
    return "None";
  }

  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric"
  }).format(new Date(value));
}

function daysSince(value: string | null) {
  if (!value) {
    return Number.POSITIVE_INFINITY;
  }

  const date = new Date(value).getTime();
  return Math.floor((Date.now() - date) / 86_400_000);
}

function freshnessBucket(value: string | null): Exclude<FreshnessFilter, "any"> {
  const age = daysSince(value);
  if (age > 30) {
    return "stale30";
  }
  if (age > 7) {
    return "stale7";
  }
  return "current";
}

function freshnessText(value: string | null) {
  if (!value) {
    return "No run";
  }

  const age = daysSince(value);
  if (age === 0) {
    return "Today";
  }
  if (age === 1) {
    return "1 day ago";
  }
  return `${age} days ago`;
}

function queueRank(jurisdiction: CoverageJurisdiction) {
  if (jurisdiction.queue.high > 0) {
    return 0;
  }
  if (jurisdiction.queue.medium > 0) {
    return 1;
  }
  if (jurisdiction.queue.low > 0) {
    return 2;
  }
  return 3;
}

function newestIngested(jurisdiction: CoverageJurisdiction) {
  const values = [
    jurisdiction.lastIngested.gov,
    jurisdiction.lastIngested.news,
    jurisdiction.lastIngested.costar
  ].filter(Boolean) as string[];

  if (values.length === 0) {
    return null;
  }

  return values.reduce((latest, value) => (new Date(value) > new Date(latest) ? value : latest));
}

function sourceClassLabel(sourceClass: string) {
  if (sourceClass === "pipedream_seed") {
    return "Pipedream";
  }

  return sourceClass.charAt(0).toUpperCase() + sourceClass.slice(1);
}

function SourceBadge({ sourceClass }: { sourceClass: string }) {
  return (
    <span
      className={cn(
        "inline-flex h-5 items-center rounded border px-1.5 text-[11px] font-medium",
        SOURCE_CLASS_STYLES[sourceClass] ?? "border-slate-200 bg-slate-50 text-slate-700"
      )}
    >
      {sourceClassLabel(sourceClass)}
    </span>
  );
}

function QueueBadge({ jurisdiction }: { jurisdiction: CoverageJurisdiction }) {
  const { queue } = jurisdiction;

  if (queue.pending === 0) {
    return <span className="text-sm text-slate-500">cleared</span>;
  }

  const label =
    queue.high > 0 ? `High ${queue.high}` : queue.medium > 0 ? `Med ${queue.medium}` : `Low ${queue.low}`;
  const dotClass =
    queue.high > 0 ? "bg-red-600" : queue.medium > 0 ? "bg-amber-500" : "bg-slate-400";

  return (
    <span className="inline-flex items-center gap-2">
      <span className={cn("size-2 rounded-full", dotClass)} aria-hidden="true" />
      <span className="font-medium text-slate-900">{number(queue.pending)}</span>
      <span className="text-xs text-slate-500">{label}</span>
    </span>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-28 border-l border-slate-200 pl-4 first:border-l-0 first:pl-0">
      <p className="text-xs text-slate-500">{label}</p>
      <p className="text-base font-semibold text-slate-950">{value}</p>
    </div>
  );
}

function SelectControl({
  label,
  value,
  onChange,
  children
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  children: React.ReactNode;
}) {
  return (
    <label className="flex min-w-36 flex-col gap-1 text-xs font-medium text-slate-500">
      {label}
      <select
        className="h-9 rounded-md border border-slate-300 bg-white px-2 text-sm font-normal text-slate-900 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-100"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {children}
      </select>
    </label>
  );
}

function SourceDetail({ source }: { source: CoverageSourceSummary }) {
  const actionLabel = source.sourceClass === "costar" ? "Upload" : "Refresh";
  const ActionIcon = source.sourceClass === "costar" ? Upload : RefreshCw;

  return (
    <div className="grid grid-cols-[auto_minmax(12rem,1fr)_minmax(9rem,auto)_minmax(10rem,auto)_auto] items-center gap-3 border-t border-slate-100 py-2 text-sm first:border-t-0">
      <SourceBadge sourceClass={source.sourceClass} />
      <div>
        <p className="font-medium text-slate-900">{titleFromSlug(source.sourceName)}</p>
        <p className="text-xs text-slate-500">{source.active ? "Active" : "Inactive"}</p>
      </div>
      <div className="text-slate-700">
        <p>{formatDate(source.lastRunAt)}</p>
        <p className="text-xs text-slate-500">{freshnessText(source.lastRunAt)}</p>
      </div>
      <div className="text-xs text-slate-500">
        {source.lastRunScope === "unknown"
          ? "No run recorded"
          : source.lastRunScope === "market_historical"
            ? "Historical market run"
            : "Jurisdiction run"}
        {source.lastRunHadError ? <span className="ml-2 text-red-700">Error logged</span> : null}
      </div>
      <Button disabled title="Available in Phase C" type="button" variant="outline">
        <ActionIcon className="size-4" aria-hidden="true" />
        {actionLabel}
      </Button>
    </div>
  );
}

export function CoverageClient({ jurisdictions }: CoverageClientProps) {
  const [filters, setFilters] = useState<CoverageFilters>(DEFAULT_FILTERS);
  const [pinnedIds, setPinnedIds] = useState<Set<string>>(new Set());
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const loadedStoredState = useRef(false);

  useEffect(() => {
    const timeout = window.setTimeout(() => {
      try {
        const savedFilters = window.sessionStorage.getItem(FILTER_STORAGE_KEY);
        if (savedFilters) {
          setFilters({ ...DEFAULT_FILTERS, ...JSON.parse(savedFilters) });
        }

        const savedPins = window.localStorage.getItem(PIN_STORAGE_KEY);
        if (savedPins) {
          setPinnedIds(new Set(JSON.parse(savedPins) as string[]));
        }
      } catch {
        window.sessionStorage.removeItem(FILTER_STORAGE_KEY);
        window.localStorage.removeItem(PIN_STORAGE_KEY);
      } finally {
        loadedStoredState.current = true;
      }
    }, 0);

    return () => window.clearTimeout(timeout);
  }, []);

  useEffect(() => {
    if (!loadedStoredState.current) {
      return;
    }

    window.sessionStorage.setItem(FILTER_STORAGE_KEY, JSON.stringify(filters));
  }, [filters]);

  useEffect(() => {
    if (!loadedStoredState.current) {
      return;
    }

    window.localStorage.setItem(PIN_STORAGE_KEY, JSON.stringify([...pinnedIds]));
  }, [pinnedIds]);

  const markets = useMemo(
    () =>
      [...new Set(jurisdictions.map((jurisdiction) => jurisdiction.market?.displayName).filter(Boolean))]
        .sort()
        .map((market) => market as string),
    [jurisdictions]
  );
  const states = useMemo(
    () => [...new Set(jurisdictions.map((jurisdiction) => jurisdiction.state))].sort(),
    [jurisdictions]
  );

  const filteredJurisdictions = useMemo(() => {
    const search = filters.search.trim().toLowerCase();

    return jurisdictions
      .filter((jurisdiction) => {
        const marketName = jurisdiction.market?.displayName ?? "None";
        const newest = newestIngested(jurisdiction);
        const matchesSearch =
          !search ||
          jurisdiction.displayName.toLowerCase().includes(search) ||
          jurisdiction.slug.toLowerCase().includes(search) ||
          marketName.toLowerCase().includes(search);
        const matchesMarket = filters.market === "all" || marketName === filters.market;
        const matchesState = filters.state === "all" || jurisdiction.state === filters.state;
        const matchesPinned = !filters.pinnedOnly || pinnedIds.has(jurisdiction.id);
        const matchesQueue =
          filters.queue === "any" ||
          (filters.queue === "pending" && jurisdiction.queue.pending > 0) ||
          (filters.queue === "cleared" && jurisdiction.queue.pending === 0) ||
          (filters.queue === "high" && jurisdiction.queue.high > 0);
        const matchesFreshness =
          filters.freshness === "any" || freshnessBucket(newest) === filters.freshness;

        return (
          matchesSearch &&
          matchesMarket &&
          matchesState &&
          matchesPinned &&
          matchesQueue &&
          matchesFreshness
        );
      })
      .sort((a, b) => {
        const pinDelta = Number(pinnedIds.has(b.id)) - Number(pinnedIds.has(a.id));
        if (pinDelta !== 0) {
          return pinDelta;
        }

        const queueDelta = queueRank(a) - queueRank(b);
        if (queueDelta !== 0) {
          return queueDelta;
        }

        return a.displayName.localeCompare(b.displayName);
      });
  }, [filters, jurisdictions, pinnedIds]);

  const totals = useMemo(
    () => ({
      projects: jurisdictions.reduce((sum, jurisdiction) => sum + jurisdiction.projectCount, 0),
      underConstruction: jurisdictions.reduce(
        (sum, jurisdiction) => sum + jurisdiction.underConstructionCount,
        0
      ),
      pending: jurisdictions.reduce((sum, jurisdiction) => sum + jurisdiction.queue.pending, 0),
      deferred: jurisdictions.reduce((sum, jurisdiction) => sum + jurisdiction.queue.deferred, 0)
    }),
    [jurisdictions]
  );

  const visibleOptionalColumns = filters.optionalColumns;
  const colSpan = 9 + visibleOptionalColumns.length;

  function updateFilter<K extends keyof CoverageFilters>(key: K, value: CoverageFilters[K]) {
    setFilters((current) => ({ ...current, [key]: value }));
  }

  function toggleOptionalColumn(column: OptionalColumn) {
    setFilters((current) => {
      const included = current.optionalColumns.includes(column);
      const optionalColumns = included
        ? current.optionalColumns.filter((item) => item !== column)
        : [...current.optionalColumns, column];

      return { ...current, optionalColumns };
    });
  }

  function togglePin(id: string) {
    setPinnedIds((current) => {
      const next = new Set(current);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }

  function toggleExpanded(id: string) {
    setExpandedIds((current) => {
      const next = new Set(current);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }

  return (
    <main className="px-5 py-5">
      <div className="mb-5 flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-normal text-slate-950">Coverage</h1>
          <div className="mt-3 flex flex-wrap gap-5">
            <Metric label="Jurisdictions" value={number(jurisdictions.length)} />
            <Metric label="Projects" value={number(totals.projects)} />
            <Metric label="Under construction" value={number(totals.underConstruction)} />
            <Metric label="Pending queue" value={number(totals.pending)} />
            <Metric label="Deferred" value={number(totals.deferred)} />
          </div>
        </div>

        <div className="flex flex-wrap items-end gap-2">
          <div className="min-w-64">
            <label className="mb-1 block text-xs font-medium text-slate-500" htmlFor="coverage-search">
              Filter
            </label>
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
              <Input
                className="pl-9"
                id="coverage-search"
                placeholder="Search jurisdictions or markets"
                value={filters.search}
                onChange={(event) => updateFilter("search", event.target.value)}
              />
            </div>
          </div>
          <SelectControl label="Market" value={filters.market} onChange={(value) => updateFilter("market", value)}>
            <option value="all">All markets</option>
            {markets.map((market) => (
              <option key={market} value={market}>
                {market}
              </option>
            ))}
          </SelectControl>
          <SelectControl label="State" value={filters.state} onChange={(value) => updateFilter("state", value)}>
            <option value="all">All states</option>
            {states.map((state) => (
              <option key={state} value={state}>
                {state}
              </option>
            ))}
          </SelectControl>
          <SelectControl
            label="Queue"
            value={filters.queue}
            onChange={(value) => updateFilter("queue", value as QueueFilter)}
          >
            <option value="any">Any status</option>
            <option value="pending">Any pending</option>
            <option value="high">High priority</option>
            <option value="cleared">Cleared</option>
          </SelectControl>
          <SelectControl
            label="Freshness"
            value={filters.freshness}
            onChange={(value) => updateFilter("freshness", value as FreshnessFilter)}
          >
            <option value="any">Any freshness</option>
            <option value="current">Current</option>
            <option value="stale7">Stale &gt;7d</option>
            <option value="stale30">Stale &gt;30d</option>
          </SelectControl>
        </div>
      </div>

      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant={filters.pinnedOnly ? "default" : "outline"}
            onClick={() => updateFilter("pinnedOnly", !filters.pinnedOnly)}
          >
            <Star className="size-4" aria-hidden="true" />
            Pinned only
          </Button>
          <Button type="button" variant="outline" onClick={() => setFilters(DEFAULT_FILTERS)}>
            <Filter className="size-4" aria-hidden="true" />
            Reset filters
          </Button>
        </div>
        <div className="flex flex-wrap items-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-2">
          <Columns3 className="size-4 text-slate-500" aria-hidden="true" />
          {OPTIONAL_COLUMNS.map((column) => (
            <label className="flex items-center gap-1.5 text-xs text-slate-700" key={column.key}>
              <input
                checked={visibleOptionalColumns.includes(column.key)}
                type="checkbox"
                onChange={() => toggleOptionalColumn(column.key)}
              />
              {column.label}
            </label>
          ))}
        </div>
      </div>

      <div className="overflow-x-auto rounded-md border border-slate-200 bg-white">
        <table className="min-w-[1080px] w-full border-collapse text-left text-sm">
          <thead className="bg-slate-100 text-xs uppercase text-slate-500">
            <tr>
              <th className="w-10 px-3 py-2 font-medium">Pin</th>
              <th className="min-w-52 px-3 py-2 font-medium">Jurisdiction</th>
              <th className="px-3 py-2 font-medium">State</th>
              <th className="min-w-44 px-3 py-2 font-medium">Market</th>
              <th className="px-3 py-2 text-right font-medium">Projects</th>
              <th className="px-3 py-2 text-right font-medium">U/C</th>
              <th className="min-w-36 px-3 py-2 font-medium">Queue</th>
              <th className="px-3 py-2 text-right font-medium">Deferred</th>
              {visibleOptionalColumns.includes("gov") ? (
                <th className="px-3 py-2 font-medium">Gov last</th>
              ) : null}
              {visibleOptionalColumns.includes("news") ? (
                <th className="px-3 py-2 font-medium">News last</th>
              ) : null}
              {visibleOptionalColumns.includes("costar") ? (
                <th className="px-3 py-2 font-medium">CoStar last</th>
              ) : null}
              {visibleOptionalColumns.includes("lastReviewed") ? (
                <th className="px-3 py-2 font-medium">Last reviewed</th>
              ) : null}
              {visibleOptionalColumns.includes("overrides") ? (
                <th className="px-3 py-2 text-right font-medium">Overrides</th>
              ) : null}
              <th className="w-12 px-3 py-2 font-medium">Open</th>
            </tr>
          </thead>
          <tbody>
            {filteredJurisdictions.map((jurisdiction) => {
              const isPinned = pinnedIds.has(jurisdiction.id);
              const isExpanded = expandedIds.has(jurisdiction.id);

              return (
                <Fragment key={jurisdiction.id}>
                <tr className="border-t border-slate-100">
                  <td className="px-3 py-2 align-top">
                    <button
                      aria-label={isPinned ? "Unpin jurisdiction" : "Pin jurisdiction"}
                      className={cn(
                        "flex size-7 items-center justify-center rounded-md border border-transparent text-slate-400 hover:border-slate-200 hover:bg-slate-50",
                        isPinned && "text-amber-500"
                      )}
                      type="button"
                      onClick={() => togglePin(jurisdiction.id)}
                    >
                      <Star className={cn("size-4", isPinned && "fill-current")} aria-hidden="true" />
                    </button>
                  </td>
                  <td className="px-3 py-2 align-top">
                    <p className="font-medium text-slate-950">{jurisdiction.displayName}</p>
                    <p className="font-mono text-xs text-slate-500">{jurisdiction.slug}</p>
                  </td>
                  <td className="px-3 py-2 align-top text-slate-700">{jurisdiction.state}</td>
                  <td className="px-3 py-2 align-top text-slate-700">
                    {jurisdiction.market?.displayName ?? "None"}
                  </td>
                  <td className="px-3 py-2 text-right align-top font-medium text-slate-950">
                    {number(jurisdiction.projectCount)}
                  </td>
                  <td className="px-3 py-2 text-right align-top text-slate-700">
                    {number(jurisdiction.underConstructionCount)}
                  </td>
                  <td className="px-3 py-2 align-top">
                    <QueueBadge jurisdiction={jurisdiction} />
                  </td>
                  <td className="px-3 py-2 text-right align-top text-slate-700">
                    {number(jurisdiction.queue.deferred)}
                  </td>
                  {visibleOptionalColumns.includes("gov") ? (
                    <td className="px-3 py-2 align-top text-slate-700">
                      <p>{formatDate(jurisdiction.lastIngested.gov)}</p>
                      <p className="text-xs text-slate-500">{freshnessText(jurisdiction.lastIngested.gov)}</p>
                    </td>
                  ) : null}
                  {visibleOptionalColumns.includes("news") ? (
                    <td className="px-3 py-2 align-top text-slate-700">
                      <p>{formatDate(jurisdiction.lastIngested.news)}</p>
                      <p className="text-xs text-slate-500">{freshnessText(jurisdiction.lastIngested.news)}</p>
                    </td>
                  ) : null}
                  {visibleOptionalColumns.includes("costar") ? (
                    <td className="px-3 py-2 align-top text-slate-700">
                      <p>{formatDate(jurisdiction.lastIngested.costar)}</p>
                      <p className="text-xs text-slate-500">{freshnessText(jurisdiction.lastIngested.costar)}</p>
                    </td>
                  ) : null}
                  {visibleOptionalColumns.includes("lastReviewed") ? (
                    <td className="px-3 py-2 align-top text-slate-700">
                      {formatDate(jurisdiction.lastReviewedAt)}
                    </td>
                  ) : null}
                  {visibleOptionalColumns.includes("overrides") ? (
                    <td className="px-3 py-2 text-right align-top text-slate-700">
                      {number(jurisdiction.openOverrides)}
                    </td>
                  ) : null}
                  <td className="px-3 py-2 align-top">
                    <button
                      aria-label={isExpanded ? "Collapse jurisdiction" : "Expand jurisdiction"}
                      className="flex size-8 items-center justify-center rounded-md border border-slate-200 text-slate-600 hover:bg-slate-50"
                      type="button"
                      onClick={() => toggleExpanded(jurisdiction.id)}
                    >
                      {isExpanded ? (
                        <ChevronDown className="size-4" aria-hidden="true" />
                      ) : (
                        <ChevronRight className="size-4" aria-hidden="true" />
                      )}
                    </button>
                  </td>
                </tr>
                {isExpanded ? (
                <tr className="border-t border-slate-100 bg-slate-50">
                  <td colSpan={colSpan} className="px-4 py-4">
                    <div className="grid gap-4 xl:grid-cols-[minmax(18rem,0.6fr)_minmax(34rem,1.4fr)]">
                      <div className="space-y-3">
                        <div className="grid grid-cols-2 gap-3 text-sm">
                          <Metric label="Projects" value={number(jurisdiction.projectCount)} />
                          <Metric label="U/C" value={number(jurisdiction.underConstructionCount)} />
                          <Metric label="Queue" value={number(jurisdiction.queue.pending)} />
                          <Metric label="Deferred" value={number(jurisdiction.queue.deferred)} />
                        </div>
                        <div className="flex flex-wrap gap-2">
                          <Button disabled title="Available in Phase C" type="button">
                            Enter review session
                          </Button>
                          <Button type="button" variant="outline" onClick={() => togglePin(jurisdiction.id)}>
                            <Star
                              className={cn("size-4", pinnedIds.has(jurisdiction.id) && "fill-current text-amber-500")}
                              aria-hidden="true"
                            />
                            {pinnedIds.has(jurisdiction.id) ? "Pinned" : "Pin"}
                          </Button>
                        </div>
                      </div>
                      <div className="rounded-md border border-slate-200 bg-white px-3">
                        {jurisdiction.sources.length > 0 ? (
                          jurisdiction.sources.map((source) => <SourceDetail key={source.id} source={source} />)
                        ) : (
                          <p className="py-3 text-sm text-slate-500">No registered sources.</p>
                        )}
                      </div>
                    </div>
                  </td>
                </tr>
                ) : null}
                </Fragment>
              );
            })}

            {filteredJurisdictions.length === 0 ? (
              <tr>
                <td colSpan={colSpan} className="px-3 py-8 text-center text-sm text-slate-500">
                  No jurisdictions match the current filters.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </main>
  );
}

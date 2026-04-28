import {
  currentValueForItem,
  displayActor,
  fieldNameForItem,
  formatValue,
  humanize,
  proposedValueForItem,
  sourceTextForItem
} from "./payload";
import type { ReviewProjectSummary, ReviewQueueItem } from "./types";

export type ReviewedDecisionFilters = {
  search: string;
  field: string;
  outcome: string;
  decider: string;
  sort: "date_desc" | "date_asc" | "decider" | "project";
};

export type ReviewedDecisionRow = {
  item: ReviewQueueItem;
  project: ReviewProjectSummary | null;
  field: string;
  fieldLabel: string;
  outcome: string;
  outcomeLabel: string;
  deciderKey: string;
  deciderLabel: string;
  committedAt: string | null;
  currentValue: unknown;
  proposedValue: unknown;
  sourceText: string | null;
};

export type ReviewedFilterOption = {
  value: string;
  label: string;
};

export type ReviewedFilterOptions = {
  fields: ReviewedFilterOption[];
  outcomes: ReviewedFilterOption[];
  deciders: ReviewedFilterOption[];
};

export function buildReviewedRows(
  items: ReviewQueueItem[],
  projects: Record<string, ReviewProjectSummary>
): ReviewedDecisionRow[] {
  return items
    .filter((item) => item.activeDecision?.state === "committed")
    .map((item) => {
      const decision = item.activeDecision!;
      const project = item.projectId ? projects[item.projectId] ?? null : null;
      const field = fieldNameForItem(item);
      const outcome = decision.decisionType ?? "committed";
      const deciderKey = decision.committedByEmail ?? decision.committedBy ?? decision.stagedByEmail ?? decision.stagedBy ?? "unknown";

      return {
        item,
        project,
        field,
        fieldLabel: humanize(field),
        outcome,
        outcomeLabel: humanize(outcome),
        deciderKey,
        deciderLabel: displayActor(decision.committedByEmail ?? decision.stagedByEmail, decision.committedBy ?? decision.stagedBy),
        committedAt: decision.committedAt,
        currentValue: currentValueForItem(item),
        proposedValue: proposedValueForItem(item),
        sourceText: sourceTextForItem(item)
      };
    });
}

export function filterReviewedRows(
  rows: ReviewedDecisionRow[],
  filters: ReviewedDecisionFilters
): ReviewedDecisionRow[] {
  const normalizedSearch = filters.search.trim().toLowerCase();
  return rows
    .filter((row) => {
      if (filters.field && row.field !== filters.field) {
        return false;
      }
      if (filters.outcome && row.outcome !== filters.outcome) {
        return false;
      }
      if (filters.decider && row.deciderKey !== filters.decider) {
        return false;
      }
      if (normalizedSearch && !reviewedSearchText(row).includes(normalizedSearch)) {
        return false;
      }
      return true;
    })
    .sort((a, b) => sortReviewedRows(a, b, filters.sort));
}

export function buildReviewedFilterOptions(rows: ReviewedDecisionRow[]): ReviewedFilterOptions {
  return {
    fields: uniqueOptions(rows.map((row) => ({ value: row.field, label: row.fieldLabel }))),
    outcomes: uniqueOptions(rows.map((row) => ({ value: row.outcome, label: row.outcomeLabel }))),
    deciders: uniqueOptions(rows.map((row) => ({ value: row.deciderKey, label: row.deciderLabel })))
  };
}

function reviewedSearchText(row: ReviewedDecisionRow) {
  return [
    row.project?.projectName,
    row.project?.canonicalAddress,
    row.project?.pipelineStatus,
    row.fieldLabel,
    row.outcomeLabel,
    row.deciderLabel,
    row.item.itemType,
    row.sourceText,
    formatValue(row.currentValue),
    formatValue(row.proposedValue)
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

function sortReviewedRows(
  a: ReviewedDecisionRow,
  b: ReviewedDecisionRow,
  sort: ReviewedDecisionFilters["sort"]
) {
  if (sort === "date_asc") {
    return String(a.committedAt ?? "").localeCompare(String(b.committedAt ?? "")) || projectSort(a, b);
  }
  if (sort === "decider") {
    return a.deciderLabel.localeCompare(b.deciderLabel) || dateDescSort(a, b);
  }
  if (sort === "project") {
    return projectSort(a, b) || dateDescSort(a, b);
  }
  return dateDescSort(a, b) || projectSort(a, b);
}

function dateDescSort(a: ReviewedDecisionRow, b: ReviewedDecisionRow) {
  return String(b.committedAt ?? "").localeCompare(String(a.committedAt ?? ""));
}

function projectSort(a: ReviewedDecisionRow, b: ReviewedDecisionRow) {
  return (a.project?.projectName ?? "Unlinked").localeCompare(b.project?.projectName ?? "Unlinked");
}

function uniqueOptions(options: ReviewedFilterOption[]) {
  const byValue = new Map<string, ReviewedFilterOption>();
  for (const option of options) {
    if (option.value) {
      byValue.set(option.value, option);
    }
  }
  return [...byValue.values()].sort((a, b) => a.label.localeCompare(b.label));
}

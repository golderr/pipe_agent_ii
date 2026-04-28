import { describe, expect, it } from "vitest";
import {
  buildReviewedFilterOptions,
  buildReviewedRows,
  filterReviewedRows,
  type ReviewedDecisionFilters
} from "./reviewed";
import type { ReviewDecisionSummary, ReviewProjectSummary, ReviewQueueItem } from "./types";

const baseFilters: ReviewedDecisionFilters = {
  search: "",
  field: "",
  outcome: "",
  decider: "",
  sort: "date_desc"
};

function decision(overrides: Partial<ReviewDecisionSummary> = {}): ReviewDecisionSummary {
  return {
    decisionId: "decision-1",
    state: "committed",
    decisionType: "accept_new",
    stagedAt: "2026-04-27T10:00:00Z",
    stagedBy: "11111111-2222-3333-4444-555555555555",
    stagedByEmail: "reviewer@example.com",
    committedAt: "2026-04-28T12:00:00Z",
    committedBy: "11111111-2222-3333-4444-555555555555",
    committedByEmail: "reviewer@example.com",
    decisionValue: null,
    decisionNotes: "Looks right.",
    sourceUrl: null,
    ...overrides
  };
}

function reviewItem(overrides: Partial<ReviewQueueItem> = {}): ReviewQueueItem {
  return {
    id: "item-1",
    projectId: "project-1",
    sourceRunId: null,
    itemType: "override_contradiction",
    status: "accepted",
    state: "committed",
    priority: "medium",
    matchConfidence: null,
    fieldName: null,
    winningEvidenceId: null,
    payload: {
      field_name: "total_units",
      current_override: { value: 100 },
      candidate: { value: 120 }
    },
    assignedTo: null,
    createdAt: "2026-04-27T09:00:00Z",
    resolvedAt: "2026-04-28T12:00:00Z",
    resolvedBy: "reviewer@example.com",
    activeDecision: decision(),
    evidenceSummaries: [],
    ...overrides
  };
}

function project(overrides: Partial<ReviewProjectSummary> = {}): ReviewProjectSummary {
  return {
    id: "project-1",
    projectName: "Main Tower",
    canonicalAddress: "100 MAIN ST LOS ANGELES CA 90012",
    city: "Los Angeles",
    state: "CA",
    zip: "90012",
    market: "los_angeles",
    jurisdictionId: "jurisdiction-1",
    pipelineStatus: "Approved",
    developer: "Helio",
    totalUnits: 120,
    dateDelivery: null,
    ...overrides
  };
}

describe("reviewed decision helpers", () => {
  it("builds rows from committed review items only", () => {
    const rows = buildReviewedRows(
      [
        reviewItem(),
        reviewItem({
          id: "item-staged",
          state: "staged",
          activeDecision: { ...decision(), state: "staged", committedAt: null }
        })
      ],
      { "project-1": project() }
    );

    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      field: "total_units",
      outcome: "accept_new",
      deciderKey: "reviewer@example.com",
      deciderLabel: "reviewer@example.com",
      committedAt: "2026-04-28T12:00:00Z"
    });
  });

  it("filters by field, outcome, decider, and search text", () => {
    const rows = buildReviewedRows(
      [
        reviewItem(),
        reviewItem({
          id: "item-2",
          projectId: "project-2",
          payload: {
            field_name: "developer",
            current_override: { value: "Old Dev" },
            candidate: { value: "New Dev" }
          },
          activeDecision: decision({
            decisionId: "decision-2",
            decisionType: "keep_old",
            committedAt: "2026-04-27T12:00:00Z",
            committedByEmail: "other@example.com"
          })
        })
      ],
      {
        "project-1": project(),
        "project-2": project({ id: "project-2", projectName: "Developer Site" })
      }
    );

    expect(
      filterReviewedRows(rows, {
        ...baseFilters,
        search: "main",
        field: "total_units",
        outcome: "accept_new",
        decider: "reviewer@example.com"
      }).map((row) => row.item.id)
    ).toEqual(["item-1"]);
  });

  it("sorts rows by date, decider, or project", () => {
    const rows = buildReviewedRows(
      [
        reviewItem(),
        reviewItem({
          id: "item-2",
          projectId: "project-2",
          activeDecision: decision({
            decisionId: "decision-2",
            committedAt: "2026-04-27T12:00:00Z",
            committedByEmail: "alpha@example.com"
          })
        })
      ],
      {
        "project-1": project({ projectName: "Zulu" }),
        "project-2": project({ id: "project-2", projectName: "Alpha" })
      }
    );

    expect(filterReviewedRows(rows, { ...baseFilters, sort: "date_desc" }).map((row) => row.item.id)).toEqual(["item-1", "item-2"]);
    expect(filterReviewedRows(rows, { ...baseFilters, sort: "date_asc" }).map((row) => row.item.id)).toEqual(["item-2", "item-1"]);
    expect(filterReviewedRows(rows, { ...baseFilters, sort: "decider" }).map((row) => row.item.id)).toEqual(["item-2", "item-1"]);
    expect(filterReviewedRows(rows, { ...baseFilters, sort: "project" }).map((row) => row.item.id)).toEqual(["item-2", "item-1"]);
  });

  it("builds distinct filter options", () => {
    const rows = buildReviewedRows([reviewItem()], { "project-1": project() });
    const options = buildReviewedFilterOptions(rows);

    expect(options.fields).toEqual([{ value: "total_units", label: "Total Units" }]);
    expect(options.outcomes).toEqual([{ value: "accept_new", label: "Accept New" }]);
    expect(options.deciders).toEqual([{ value: "reviewer@example.com", label: "reviewer@example.com" }]);
  });

  it("keeps legacy committed rows with no committed timestamp visible", () => {
    const rows = buildReviewedRows(
      [
        reviewItem({
          activeDecision: decision({ committedAt: null })
        })
      ],
      { "project-1": project() }
    );

    expect(rows).toHaveLength(1);
    expect(rows[0].committedAt).toBeNull();
    expect(filterReviewedRows(rows, { ...baseFilters, sort: "date_desc" }).map((row) => row.item.id)).toEqual(["item-1"]);
  });

  it("keeps committed rows visible when their project is no longer linked", () => {
    const rows = buildReviewedRows([reviewItem({ projectId: null })], {});

    expect(rows).toHaveLength(1);
    expect(rows[0].project).toBeNull();
    expect(filterReviewedRows(rows, { ...baseFilters, search: "override" }).map((row) => row.item.id)).toEqual(["item-1"]);
  });
});

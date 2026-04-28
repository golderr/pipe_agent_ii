import { describe, expect, it } from "vitest";
import {
  acceptDecisionValue,
  candidateProjectIdsForItem,
  candidateValuesForItem,
  currentValueForItem,
  displayActor,
  fieldNameForItem,
  flattenPayload,
  formatDate,
  isStagedByMe,
  newProjectDataForItem,
  proposedValueForItem,
  sourceTextForItem,
  warningForItem
} from "./payload";
import type { ReviewQueueItem } from "./types";

function reviewItem(overrides: Partial<ReviewQueueItem> = {}): ReviewQueueItem {
  return {
    id: "item-1",
    projectId: "project-1",
    sourceRunId: null,
    itemType: "override_contradiction",
    status: "pending",
    state: "open",
    priority: "medium",
    matchConfidence: null,
    fieldName: null,
    winningEvidenceId: null,
    payload: null,
    assignedTo: null,
    createdAt: "2026-04-27T10:00:00Z",
    resolvedAt: null,
    resolvedBy: null,
    activeDecision: null,
    ...overrides
  };
}

describe("review payload helpers", () => {
  it("extracts status suggestion values", () => {
    const item = reviewItem({
      itemType: "status_change",
      payload: {
        status_suggestion: {
          current_status: "Proposed",
          suggested_status: "Under Construction"
        }
      }
    });

    expect(fieldNameForItem(item)).toBe("pipeline_status");
    expect(currentValueForItem(item)).toBe("Proposed");
    expect(proposedValueForItem(item)).toBe("Under Construction");
  });

  it("extracts contradiction field and candidate values", () => {
    const item = reviewItem({
      payload: {
        field_name: "total_units",
        current_override: { value: 120 },
        candidate: { value: 136 },
        review_flags: [{ message: "Unit delta exceeds threshold." }]
      }
    });

    expect(fieldNameForItem(item)).toBe("total_units");
    expect(currentValueForItem(item)).toBe(120);
    expect(proposedValueForItem(item)).toBe(136);
    expect(warningForItem(item)).toBe("Unit delta exceeds threshold.");
  });

  it("falls back to changes array fields", () => {
    const item = reviewItem({
      itemType: "field_change",
      payload: {
        changes: [{ field: "developer", old_value: "Helio Capital", new_value: "Helio Capital LLC" }]
      }
    });

    expect(fieldNameForItem(item)).toBe("developer");
    expect(currentValueForItem(item)).toBe("Helio Capital");
    expect(proposedValueForItem(item)).toBe("Helio Capital LLC");
  });

  it("builds new project accept payloads from mapped fields", () => {
    const item = reviewItem({
      itemType: "new_candidate",
      projectId: null,
      payload: {
        canonical_address: "100 Main St, Los Angeles, CA 90012",
        mapped_fields: {
          project_name: "Main Street Tower",
          city: "Los Angeles",
          state: "CA",
          county: "Los Angeles",
          zip: "90012"
        }
      }
    });

    expect(newProjectDataForItem(item)).toEqual({
      canonical_address: "100 Main St, Los Angeles, CA 90012",
      project_name: "Main Street Tower",
      city: "Los Angeles",
      state: "CA",
      county: "Los Angeles",
      zip: "90012"
    });
    expect(acceptDecisionValue(item)).toEqual({
      create_new: true,
      new_project_data: newProjectDataForItem(item)
    });
  });

  it("extracts possible-match candidate project ids", () => {
    const nested = reviewItem({
      itemType: "possible_match",
      payload: { match: { candidate_project_ids: ["project-a", "project-b"] } }
    });
    const root = reviewItem({
      itemType: "possible_match",
      payload: { candidate_project_ids: ["project-c"] }
    });

    expect(candidateProjectIdsForItem(nested)).toEqual(["project-a", "project-b"]);
    expect(candidateProjectIdsForItem(root)).toEqual(["project-c"]);
    expect(acceptDecisionValue(root)).toEqual({ project_id: "project-c" });
    expect(candidateValuesForItem(nested)).toEqual([]);
  });

  it("unwraps multi-candidate value objects for field items", () => {
    const item = reviewItem({
      payload: {
        candidates: [
          { value: "Approved", evidence_ids: ["e-1"] },
          { value: "Under Construction", evidence_ids: ["e-2"] }
        ]
      }
    });

    expect(candidateValuesForItem(item)).toEqual(["Approved", "Under Construction"]);
  });

  it("extracts source text and default contradiction warnings", () => {
    const item = reviewItem({
      payload: {
        candidate: {
          evidence_date: "2026-04-26",
          evidence_frontier: { source_type: "ladbs_permit" }
        }
      }
    });

    expect(sourceTextForItem(item)).toBe("ladbs_permit - Apr 26, 2026");
    expect(warningForItem(item)).toBe("This item conflicts with a manual override.");
  });

  it("flattens top-level and one-level nested payload fields without truncation", () => {
    const payload: Record<string, unknown> = Object.fromEntries(
      Array.from({ length: 26 }, (_, index) => [`field_${index}`, index])
    );
    payload.match = { candidate_project_ids: ["project-a"], confidence: 0.92 };

    const rows = flattenPayload(payload);

    expect(rows).toHaveLength(28);
    expect(rows).toContainEqual({ key: "field_25", value: "25" });
    expect(rows).toContainEqual({ key: "match.candidate_project_ids", value: "[\"project-a\"]" });
    expect(rows).toContainEqual({ key: "match.confidence", value: "0.92" });
  });

  it("uses staged state for ownership checks and improves actor fallback labels", () => {
    const staged = reviewItem({
      activeDecision: {
        decisionId: "decision-1",
        state: "staged",
        decisionType: "accept_new",
        stagedAt: "2026-04-27T10:00:00Z",
        stagedBy: "11111111-2222-3333-4444-555555555555",
        stagedByEmail: null,
        committedAt: null,
        committedBy: null,
        committedByEmail: null,
        decisionValue: null,
        decisionNotes: null,
        sourceUrl: null
      }
    });
    const committed = reviewItem({
      activeDecision: { ...staged.activeDecision!, state: "committed" }
    });

    expect(isStagedByMe(staged, "11111111-2222-3333-4444-555555555555", null)).toBe(true);
    expect(isStagedByMe(committed, "11111111-2222-3333-4444-555555555555", null)).toBe(false);
    expect(displayActor(null, "11111111-2222-3333-4444-555555555555")).toBe("1111...5555");
  });

  it("formats date-only values as their calendar day", () => {
    expect(formatDate("2026-04-26")).toBe("Apr 26, 2026");
  });
});

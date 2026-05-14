import { describe, expect, it } from "vitest";
import { buildDiscoveryCards, isDiscoveryItem, subjectForDiscoveryItem } from "./discovery";
import type { ReviewQueueItem } from "./types";

describe("review discovery helpers", () => {
  it("filters discovery review item types", () => {
    expect(isDiscoveryItem(item({ itemType: "new_candidate" }))).toBe(true);
    expect(isDiscoveryItem(item({ itemType: "possible_match" }))).toBe(true);
    expect(isDiscoveryItem(item({ itemType: "status_change" }))).toBe(false);
  });

  it("normalizes the subject from review-item payload fields", () => {
    const subject = subjectForDiscoveryItem(
      item({
        payload: {
          canonical_address: "100 Fig St",
          mapped_fields: {
            project_name: "Fig Tower",
            developer: "Atlas Development",
            total_units: 140,
            product_type: "Apartment",
            pipeline_status: "Proposed",
            stories: 8
          }
        }
      })
    );

    expect(subject).toMatchObject({
      projectName: "Fig Tower",
      canonicalAddress: "100 Fig St",
      developer: "Atlas Development",
      totalUnits: 140,
      productType: "Apartment",
      pipelineStatus: "Proposed",
      stories: 8
    });
  });

  it("builds one card per discovery review item", () => {
    const cards = buildDiscoveryCards([
      item({
        id: "newer",
        itemType: "possible_match",
        createdAt: "2026-05-14T12:00:00Z",
        matchConfidence: 0.7,
        payload: {
          canonical_address: "100 Fig St",
          mapped_fields: { project_name: "Fig Tower" },
          match: { candidate_project_ids: ["project-1", "project-2"] }
        }
      }),
      item({
        id: "older",
        itemType: "new_candidate",
        createdAt: "2026-05-14T11:00:00Z",
        payload: {
          canonical_address: "200 Pine St"
        }
      }),
      item({ id: "status", itemType: "status_change" })
    ]);

    expect(cards.map((card) => card.key)).toEqual(["newer", "older"]);
    expect(cards[0]).toMatchObject({
      title: "Fig Tower",
      subtitle: "100 Fig St",
      potentialMatchCount: 2
    });
    expect(cards[0].newCandidateProbability).toBeCloseTo(0.3);
    expect(cards[1].newCandidateProbability).toBe(1);
  });
});

function item(overrides: Partial<ReviewQueueItem> = {}): ReviewQueueItem {
  return {
    id: overrides.id ?? "item-1",
    projectId: overrides.projectId ?? null,
    sourceRunId: overrides.sourceRunId ?? null,
    itemType: overrides.itemType ?? "new_candidate",
    status: overrides.status ?? "open",
    state: overrides.state ?? "open",
    priority: overrides.priority ?? "high",
    matchConfidence: overrides.matchConfidence ?? null,
    fieldName: overrides.fieldName ?? null,
    winningEvidenceId: overrides.winningEvidenceId ?? null,
    payload: overrides.payload ?? {},
    assignedTo: overrides.assignedTo ?? null,
    createdAt: overrides.createdAt ?? "2026-05-14T10:00:00Z",
    resolvedAt: overrides.resolvedAt ?? null,
    resolvedBy: overrides.resolvedBy ?? null,
    activeDecision: overrides.activeDecision ?? null,
    valueChange: overrides.valueChange ?? null,
    evidenceSummaries: overrides.evidenceSummaries ?? []
  };
}

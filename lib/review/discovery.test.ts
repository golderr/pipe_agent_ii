import { describe, expect, it } from "vitest";
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
  mapDedupCandidatesResponse,
  mapMatchPreviewResponse,
  matchPreviewImpactText,
  projectFieldsFromDiscoverySubject,
  searchedSummary,
  sortCandidates,
  subjectForDiscoveryItem,
  visibleMatchSignals
} from "./discovery";
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

  it("maps dedup candidate API payloads into frontend discovery models", () => {
    const result = mapDedupCandidatesResponse({
      subject: {
        project_name: "Fig Tower",
        canonical_address: "100 Fig St",
        units_total: 140,
        building_height_stories: 8
      },
      candidates: [
        {
          project_id: "project-1",
          project_name: "Fig Tower",
          canonical_address: "100 Fig St",
          units_total: 100,
          match_likelihood: 0.82,
          match_layer: 1,
          distance_meters: 12.5,
          open_review_item_count: 2,
          match_signals: {
            address: {
              score: 1,
              contributed: true,
              searched: true,
              label: "Address",
              detail: "exact address",
              weight: 0.25
            }
          }
        }
      ],
      layer_3_available: true,
      new_candidate_probability: 0.18,
      searched: {
        layer_1: [{ signal: "address", searched: true, criteria: "exact canonical_address" }],
        layer_2: { searched: true, trigram_min_score: 0.12 }
      }
    });

    expect(result.subject).toMatchObject({
      projectName: "Fig Tower",
      canonicalAddress: "100 Fig St",
      totalUnits: 140,
      stories: 8
    });
    expect(result.candidates[0]).toMatchObject({
      projectId: "project-1",
      matchLayer: 1,
      matchLikelihood: 0.82,
      openReviewItemCount: 2
    });
    expect(result.candidates[0].matchSignals.address.contributed).toBe(true);
    expect(result.layer3Available).toBe(true);
    expect(result.newCandidateProbability).toBe(0.18);
    expect(searchedSummary(result)).toContain("threshold 0.12");
  });

  it("maps match-preview API payloads into frontend discovery models", () => {
    expect(
      mapMatchPreviewResponse({
        review_items_to_close: 3,
        evidence_rows_to_reattach: 4,
        value_change_items_that_would_be_queued: ["developer", null, "stories"]
      })
    ).toEqual({
      reviewItemsToClose: 3,
      evidenceRowsToReattach: 4,
      valueChangeItemsThatWouldBeQueued: ["developer", "stories"]
    });
  });

  it("sorts candidates with match layer as the primary key", () => {
    const result = mapDedupCandidatesResponse({
      candidates: [
        candidatePayload("layer-2-high", {
          match_layer: 2,
          match_likelihood: 0.9,
          project_name: "Beta"
        }),
        candidatePayload("layer-1-low", {
          match_layer: 1,
          match_likelihood: 0,
          project_name: "Zulu"
        }),
        candidatePayload("layer-1-alpha", {
          match_layer: 1,
          match_likelihood: 0.6,
          project_name: "Alpha"
        })
      ]
    });

    expect(sortCandidates(result.candidates).map((candidate) => candidate.projectId)).toEqual([
      "layer-1-alpha",
      "layer-1-low",
      "layer-2-high"
    ]);
    expect(
      sortCandidates(result.candidates, {
        field: "projectName",
        direction: "asc"
      }).map((candidate) => candidate.projectId)
    ).toEqual(["layer-1-alpha", "layer-1-low", "layer-2-high"]);
  });

  it("moves candidate focus by keyboard number and offset", () => {
    const result = mapDedupCandidatesResponse({
      candidates: [
        candidatePayload("candidate-1"),
        candidatePayload("candidate-2"),
        candidatePayload("candidate-3")
      ]
    });

    expect(candidateFocusByNumber(result.candidates, "2")).toBe("candidate-2");
    expect(candidateFocusByNumber(result.candidates, "9")).toBeNull();
    expect(candidateFocusByOffset(result.candidates, "candidate-2", 1)).toBe("candidate-3");
    expect(candidateFocusByOffset(result.candidates, "candidate-2", -1)).toBe("candidate-1");
    expect(candidateFocusByOffset(result.candidates, "candidate-3", 1)).toBe("candidate-3");
    expect(candidateFocusByOffset(result.candidates, null, 1)).toBe("candidate-2");
  });

  it("keeps hidden signals in the data model and bands rows by layer/likelihood", () => {
    const result = mapDedupCandidatesResponse({
      candidates: [
        candidatePayload("candidate-1", {
          match_layer: 2,
          match_likelihood: 0.55,
          match_signals: {
            address: {
              score: 0,
              contributed: false,
              searched: true,
              label: "Address",
              detail: null,
              weight: 0.25
            },
            name: {
              score: 0,
              contributed: false,
              searched: false,
              label: "Name",
              detail: null,
              weight: 0.1
            },
            developer: {
              score: 1,
              contributed: true,
              searched: true,
              label: "Developer",
              detail: "exact",
              weight: 0.2
            }
          }
        }),
        candidatePayload("candidate-2", { match_layer: 1, match_likelihood: 0 }),
        candidatePayload("candidate-3", { match_layer: 3, match_likelihood: 0.8 })
      ]
    });

    expect(result.candidates[0].matchSignals.name.searched).toBe(false);
    expect(visibleMatchSignals(result.candidates[0]).map(([key]) => key)).toEqual([
      "address",
      "developer"
    ]);
    expect(candidateBandTone(result.candidates[0])).toBe("medium");
    expect(candidateBandTone(result.candidates[1])).toBe("hard");
    expect(candidateBandTone(result.candidates[2])).toBe("broad");
  });

  it("computes candidate cell overlaps for text, units, product, status, and stories", () => {
    const result = mapDedupCandidatesResponse({
      subject: {
        project_name: "Fig Tower",
        canonical_address: "100 Fig St",
        developer: "Atlas Development",
        units_affordable: 312,
        product_type: "Apartment",
        pipeline_status: "Under Construction",
        building_height_stories: 10
      },
      candidates: [
        candidatePayload("candidate-1", {
          project_name: "Fig Tower Phase 2",
          canonical_address: "100 Fig St, Los Angeles",
          developer: "Atlas Development LLC",
          units_total: 312,
          product_type: "Apartment",
          pipeline_status: "under construction",
          building_height_stories: 12
        })
      ]
    });

    const overlaps = computeCandidateOverlaps(result.subject, result.candidates[0]);

    expect(overlaps.projectName).toMatchObject({
      kind: "text-substring",
      matchedSubjectField: "projectName",
      matchedValue: "Fig Tower"
    });
    expect(overlaps.canonicalAddress).toMatchObject({
      kind: "text-substring",
      matchedSubjectField: "canonicalAddress",
      matchedValue: "100 Fig St"
    });
    expect(overlaps.developer).toMatchObject({
      kind: "text-substring",
      matchedSubjectField: "developer",
      matchedValue: "Atlas Development"
    });
    expect(overlaps.totalUnits).toMatchObject({
      kind: "cross-field-unit-match",
      matchedSubjectField: "affordableUnits",
      matchedValue: 312
    });
    expect(overlaps.productType).toMatchObject({
      kind: "exact-match",
      matchedSubjectField: "productType",
      matchedValue: "Apartment"
    });
    expect(overlaps.pipelineStatus).toMatchObject({
      kind: "exact-match",
      matchedSubjectField: "pipelineStatus",
      matchedValue: "Under Construction"
    });
    expect(overlaps.stories).toMatchObject({
      kind: "stories-proximity",
      matchedSubjectField: "stories",
      matchedValue: 10
    });
  });

  it("computes subject/candidate deltas for match-with-deltas", () => {
    const result = mapDedupCandidatesResponse({
      subject: {
        project_name: "Fig Tower",
        canonical_address: "100 Fig St",
        developer: "Atlas Development",
        units_total: 312,
        building_height_stories: 10,
        pipeline_status: "Under Construction"
      },
      candidates: [
        candidatePayload("candidate-1", {
          project_name: "Fig Tower",
          canonical_address: "100 Fig St",
          developer: "Old Developer",
          units_total: 300,
          building_height_stories: 10,
          pipeline_status: "Approved"
        })
      ]
    });

    const deltas = computeCandidateDeltas(result.subject, result.candidates[0]);

    expect(deltas.map((delta) => delta.fieldName)).toEqual([
      "developer",
      "total_units",
      "pipeline_status"
    ]);
    expect(deltas[0].valueChange).toMatchObject({
      fieldLabel: "Developer",
      currentValue: "Old Developer",
      evidenceValue: "Atlas Development",
      defaultResultValue: "Atlas Development"
    });
    expect(deltas[1].valueChange).toMatchObject({
      fieldType: "integer",
      currentValue: 300,
      evidenceValue: 312
    });
  });

  it("applies and serializes local subject edits for atomic write payloads", () => {
    const subject = mapDedupCandidatesResponse({
      subject: {
        project_name: "Fig Tower",
        canonical_address: "100 Fig St",
        units_total: 140,
        building_height_stories: 8
      }
    }).subject;

    const editedSubject = applyDiscoverySubjectEdits(subject, {
      projectName: "Fig Tower Revised",
      totalUnits: "1,400",
      stories: ""
    });

    expect(editedSubject).toMatchObject({
      projectName: "Fig Tower Revised",
      totalUnits: 1400,
      stories: null
    });
    expect(
      discoverySubjectEditsPayload({
        projectName: "Fig Tower Revised",
        totalUnits: "1,400",
        stories: ""
      })
    ).toEqual({
      project_name: "Fig Tower Revised",
      total_units: 1400,
      stories: null
    });
    expect(projectFieldsFromDiscoverySubject(editedSubject)).toMatchObject({
      project_name: "Fig Tower Revised",
      canonical_address: "100 Fig St",
      total_units: 1400
    });
  });

  it("computes coordinate overlap without highlighting unrelated short text", () => {
    const result = mapDedupCandidatesResponse({
      subject: {
        developer: "LLC",
        units_total: 100,
        building_height_stories: 10,
        lat: 34.05,
        lng: -118.25
      },
      candidates: [
        candidatePayload("candidate-1", {
          developer: "LLC Holdings",
          units_total: 125,
          building_height_stories: 14,
          lat: 34.051,
          lng: -118.251,
          distance_meters: 140
        })
      ]
    });

    const overlaps = computeCandidateOverlaps(result.subject, result.candidates[0]);

    expect(overlaps.developer).toBeUndefined();
    expect(overlaps.totalUnits).toBeUndefined();
    expect(overlaps.stories).toBeUndefined();
    expect(overlaps.lat).toMatchObject({
      kind: "distance-threshold",
      matchedSubjectField: "lat",
      matchedValue: 34.05
    });
    expect(overlaps.lng).toMatchObject({
      kind: "distance-threshold",
      matchedSubjectField: "lng",
      matchedValue: -118.25
    });
  });

  it("formats match-preview impact text", () => {
    expect(
      matchPreviewImpactText({
        reviewItemsToClose: 2,
        evidenceRowsToReattach: 1,
        valueChangeItemsThatWouldBeQueued: ["developer", "stories"]
      })
    ).toBe("Would close 2 review items, reattach 1 evidence row, and queue 2 value-change reviews.");
  });
});

function candidatePayload(id: string, overrides: Record<string, unknown> = {}) {
  return {
    project_id: id,
    project_name: "Candidate",
    canonical_address: "100 Fig St",
    match_likelihood: 0.5,
    match_layer: 2,
    open_review_item_count: 0,
    match_signals: {},
    ...overrides
  };
}

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

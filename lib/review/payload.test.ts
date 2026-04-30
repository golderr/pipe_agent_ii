import { describe, expect, it } from "vitest";
import {
  acceptDecisionValue,
  candidateProjectIdsForItem,
  candidateValuesForItem,
  currentValueForItem,
  dissentingEvidenceForItem,
  displayActor,
  fieldNameForItem,
  flattenPayload,
  formatDate,
  isStagedByMe,
  newProjectDataForItem,
  newsContextForItem,
  proposedValueForItem,
  sourceTextForItem,
  structuralDisagreementText,
  supportingEvidenceForItem,
  warningForItem,
  winningEvidenceForItem
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
    evidenceSummaries: [],
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

  it("extracts news context and uses article source text", () => {
    const item = reviewItem({
      payload: {
        news_context: {
          article_id: "article-1",
          extraction_id: "extraction-1",
          reference_id: "reference-1",
          reference_index: 2,
          extraction_confidence: "medium",
          structural_disagreement: {
            extractor: "unit_count",
            raw_match: "120 units",
            canonical: 120
          },
          extraction_version: 3,
          prompt_id: "extract_v1",
          prompt_version: "v1",
          evidence_id: "evidence-1",
          article_title: "Urbanize reports new tower",
          published_at: "2026-04-29T12:00:00Z",
          url: "https://example.com/news"
        }
      }
    });

    expect(newsContextForItem(item)).toEqual({
      articleId: "article-1",
      extractionId: "extraction-1",
      referenceId: "reference-1",
      referenceIndex: 2,
      extractionConfidence: "medium",
      structuralDisagreement: {
        extractor: "unit_count",
        raw_match: "120 units",
        canonical: 120
      },
      extractionVersion: 3,
      promptId: "extract_v1",
      promptVersion: "v1",
      evidenceId: "evidence-1",
      articleTitle: "Urbanize reports new tower",
      publishedAt: "2026-04-29T12:00:00Z",
      url: "https://example.com/news"
    });
    expect(sourceTextForItem(item)).toBe("Urbanize reports new tower - Apr 29, 2026");
    expect(structuralDisagreementText(newsContextForItem(item), 140)).toBe(
      'Pass 1 unit_count matched "120 units" (canonical: 120) - Pass 2 emitted 140'
    );
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

  it("groups evidence summaries by stance", () => {
    const item = reviewItem({
      evidenceSummaries: [
        {
          evidenceId: "evidence-1",
          stance: "supporting",
          isWinning: true,
          sourceType: "ladbs_permit",
          sourceTier: 1,
          sourceRecordId: "permit-1",
          evidenceDate: "2026-04-01",
          collectedAt: "2026-04-02T00:00:00Z",
          summary: "Permit evidence",
          detail: "Permit detail",
          externalLink: null,
          highlights: [],
          extractedValue: "Approved"
        },
        {
          evidenceId: "evidence-2",
          stance: "against",
          isWinning: false,
          sourceType: "costar",
          sourceTier: 3,
          sourceRecordId: "costar-1",
          evidenceDate: "2026-03-01",
          collectedAt: "2026-03-02T00:00:00Z",
          summary: "CoStar evidence",
          detail: "CoStar detail",
          externalLink: null,
          highlights: [],
          extractedValue: "Pending"
        }
      ]
    });

    expect(supportingEvidenceForItem(item).map((evidence) => evidence.evidenceId)).toEqual([
      "evidence-1"
    ]);
    expect(dissentingEvidenceForItem(item).map((evidence) => evidence.evidenceId)).toEqual([
      "evidence-2"
    ]);
    expect(winningEvidenceForItem(item)?.evidenceId).toBe("evidence-1");
  });
});

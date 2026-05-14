import {
  asNumber,
  asRecord,
  asString,
  candidateProjectIdsForItem,
  humanize
} from "./payload";
import type { ReviewQueueItem } from "./types";

export type DiscoverySubject = {
  projectName: string | null;
  canonicalAddress: string | null;
  developer: string | null;
  totalUnits: number | null;
  marketRateUnits: number | null;
  affordableUnits: number | null;
  workforceUnits: number | null;
  productType: string | null;
  ageRestriction: string | null;
  pipelineStatus: string | null;
  stories: number | null;
  lat: number | null;
  lng: number | null;
};

export type DiscoveryCard = {
  key: string;
  item: ReviewQueueItem;
  title: string;
  subtitle: string;
  subject: DiscoverySubject;
  potentialMatchCount: number;
  newCandidateProbability: number | null;
};

export const DISCOVERY_ITEM_TYPES = new Set(["new_candidate", "possible_match"]);

export function isDiscoveryItem(item: ReviewQueueItem) {
  return DISCOVERY_ITEM_TYPES.has(item.itemType);
}

export function buildDiscoveryCards(items: ReviewQueueItem[]): DiscoveryCard[] {
  return items
    .filter(isDiscoveryItem)
    .map((item) => {
      const subject = subjectForDiscoveryItem(item);
      return {
        key: item.id,
        item,
        title:
          subject.projectName ??
          subject.canonicalAddress ??
          humanize(item.itemType),
        subtitle:
          subject.canonicalAddress ??
          asString(item.payload?.source_record_id) ??
          "No address",
        subject,
        potentialMatchCount: candidateProjectIdsForItem(item).length,
        newCandidateProbability: newCandidateProbabilityForItem(item)
      };
    })
    // Placeholder ordering until 5D wires /candidates and can sort by Layer 1
    // presence, then Layer 2 likelihood, then likely-new cards.
    .sort((a, b) => b.item.createdAt.localeCompare(a.item.createdAt));
}

export function subjectForDiscoveryItem(item: ReviewQueueItem): DiscoverySubject {
  const payload = item.payload;
  const mappedFields = asRecord(payload?.mapped_fields);
  const rawPayload = asRecord(payload?.raw_payload);
  return {
    projectName: firstText(
      mappedFields?.project_name,
      mappedFields?.name,
      payload?.project_name,
      rawPayload?.project_name
    ),
    canonicalAddress: firstText(
      payload?.canonical_address,
      mappedFields?.canonical_address,
      mappedFields?.address,
      rawPayload?.canonical_address,
      rawPayload?.address
    ),
    developer: firstText(mappedFields?.developer, payload?.developer, rawPayload?.developer),
    totalUnits: firstNumber(mappedFields?.total_units, mappedFields?.units_total),
    marketRateUnits: firstNumber(mappedFields?.market_rate_units),
    affordableUnits: firstNumber(mappedFields?.affordable_units),
    workforceUnits: firstNumber(mappedFields?.workforce_units),
    productType: firstText(mappedFields?.product_type),
    ageRestriction: firstText(mappedFields?.age_restriction),
    pipelineStatus: firstText(mappedFields?.pipeline_status, mappedFields?.status),
    stories: firstNumber(mappedFields?.stories, mappedFields?.building_height_stories),
    lat: firstNumber(mappedFields?.lat, payload?.lat),
    lng: firstNumber(mappedFields?.lng, payload?.lng)
  };
}

function newCandidateProbabilityForItem(item: ReviewQueueItem) {
  // Shell-only placeholder until 5D consumes /candidates, whose response
  // carries the authoritative 1 - max(candidate_match_likelihood) value.
  if (typeof item.matchConfidence === "number") {
    return Math.max(0, Math.min(1, 1 - item.matchConfidence));
  }
  return item.itemType === "new_candidate" ? 1 : null;
}

function firstText(...values: unknown[]) {
  for (const value of values) {
    const text = asString(value);
    if (text) {
      return text;
    }
  }
  return null;
}

function firstNumber(...values: unknown[]) {
  for (const value of values) {
    const number = asNumber(value);
    if (number !== null) {
      return number;
    }
  }
  return null;
}

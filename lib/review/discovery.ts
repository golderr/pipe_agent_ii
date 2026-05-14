import {
  asNumber,
  asRecord,
  asRecordArray,
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

export type DiscoveryMatchSignal = {
  score: number;
  contributed: boolean;
  searched: boolean;
  label: string;
  detail: string | null;
  weight: number | null;
};

export type DiscoveryCandidate = {
  projectId: string;
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
  matchLikelihood: number;
  matchSignals: Record<string, DiscoveryMatchSignal>;
  matchLayer: number;
  distanceMeters: number | null;
  openReviewItemCount: number;
};

export type DiscoveryCandidateSearch = {
  subject: DiscoverySubject;
  candidates: DiscoveryCandidate[];
  layer3Available: boolean;
  newCandidateProbability: number;
  searched: Record<string, unknown>;
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

export function mapDedupCandidatesResponse(payload: unknown): DiscoveryCandidateSearch {
  const row = asRecord(payload) ?? {};
  return {
    subject: subjectFromApi(row.subject),
    candidates: asRecordArray(row.candidates).map(candidateFromApi),
    layer3Available: Boolean(row.layer_3_available),
    newCandidateProbability: asNumber(row.new_candidate_probability) ?? 1,
    searched: asRecord(row.searched) ?? {}
  };
}

export function searchedSummary(search: DiscoveryCandidateSearch) {
  const layer1 = Array.isArray(search.searched.layer_1)
    ? search.searched.layer_1
        .map((entry) => asRecord(entry))
        .filter((entry): entry is Record<string, unknown> => Boolean(entry))
    : [];
  const searchedLayer1 = layer1
    .filter((entry) => entry.searched === true)
    .map((entry) => asString(entry.signal) ?? asString(entry.criteria))
    .filter((value): value is string => Boolean(value));
  const layer2 = asRecord(search.searched.layer_2);
  const layer2Threshold = asNumber(layer2?.trigram_min_score);
  const probes = [
    ...searchedLayer1,
    layer2?.searched === true
      ? `name/address trigrams${layer2Threshold !== null ? ` (threshold ${layer2Threshold})` : ""}`
      : null
  ].filter((value): value is string => Boolean(value));
  return probes.length
    ? `Searched ${probes.join(", ")}. No candidate passed the current thresholds.`
    : "No search signals were available for this subject.";
}

function subjectFromApi(payload: unknown): DiscoverySubject {
  const row = asRecord(payload) ?? {};
  return {
    projectName: asString(row.project_name),
    canonicalAddress: asString(row.canonical_address),
    developer: asString(row.developer),
    totalUnits: asNumber(row.units_total),
    marketRateUnits: asNumber(row.units_market),
    affordableUnits: asNumber(row.units_affordable),
    workforceUnits: asNumber(row.units_workforce),
    productType: asString(row.product_type),
    ageRestriction: asString(row.age_restriction),
    pipelineStatus: asString(row.pipeline_status),
    stories: asNumber(row.building_height_stories),
    lat: asNumber(row.lat),
    lng: asNumber(row.lng)
  };
}

function candidateFromApi(row: Record<string, unknown>): DiscoveryCandidate {
  return {
    projectId: asString(row.project_id) ?? "",
    projectName: asString(row.project_name),
    canonicalAddress: asString(row.canonical_address),
    developer: asString(row.developer),
    totalUnits: asNumber(row.units_total),
    marketRateUnits: asNumber(row.units_market),
    affordableUnits: asNumber(row.units_affordable),
    workforceUnits: asNumber(row.units_workforce),
    productType: asString(row.product_type),
    ageRestriction: asString(row.age_restriction),
    pipelineStatus: asString(row.pipeline_status),
    stories: asNumber(row.building_height_stories),
    lat: asNumber(row.lat),
    lng: asNumber(row.lng),
    matchLikelihood: asNumber(row.match_likelihood) ?? 0,
    matchSignals: matchSignalsFromApi(row.match_signals),
    matchLayer: asNumber(row.match_layer) ?? 0,
    distanceMeters: asNumber(row.distance_meters),
    openReviewItemCount: asNumber(row.open_review_item_count) ?? 0
  };
}

function matchSignalsFromApi(payload: unknown): Record<string, DiscoveryMatchSignal> {
  const row = asRecord(payload) ?? {};
  return Object.fromEntries(
    Object.entries(row)
      .map(([key, value]) => {
        const signal = asRecord(value);
        if (!signal) {
          return null;
        }
        return [
          key,
          {
            score: asNumber(signal.score) ?? 0,
            contributed: signal.contributed === true,
            searched: signal.searched === true,
            label: asString(signal.label) ?? humanize(key),
            detail: asString(signal.detail),
            weight: asNumber(signal.weight)
          }
        ] as const;
      })
      .filter((entry): entry is readonly [string, DiscoveryMatchSignal] => Boolean(entry))
  );
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

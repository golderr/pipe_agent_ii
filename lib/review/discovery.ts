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

export type DiscoveryMatchPreview = {
  reviewItemsToClose: number;
  evidenceRowsToReattach: number;
  valueChangeItemsThatWouldBeQueued: string[];
};

export type DiscoveryCandidateSortField =
  | "matchLikelihood"
  | "projectName"
  | "canonicalAddress"
  | "developer"
  | "totalUnits"
  | "productType"
  | "pipelineStatus"
  | "stories";

export type DiscoveryCandidateSort = {
  field: DiscoveryCandidateSortField;
  direction: "asc" | "desc";
};

export type DiscoveryOverlapField =
  | "projectName"
  | "canonicalAddress"
  | "developer"
  | "totalUnits"
  | "marketRateUnits"
  | "affordableUnits"
  | "workforceUnits"
  | "productType"
  | "ageRestriction"
  | "pipelineStatus"
  | "stories"
  | "lat"
  | "lng";

export type DiscoveryOverlapKind =
  | "text-substring"
  | "exact-match"
  | "cross-field-unit-match"
  | "stories-proximity"
  | "distance-threshold";

export type DiscoveryOverlap = {
  kind: DiscoveryOverlapKind;
  matchedSubjectField: DiscoveryOverlapField;
  matchedValue: string | number;
  detail?: string;
};

export type DiscoveryOverlapMap = Partial<Record<DiscoveryOverlapField, DiscoveryOverlap>>;

const SIGNAL_DISPLAY_ORDER = [
  "identifier",
  "geographic",
  "address",
  "name",
  "developer",
  "units",
  "product_type"
];

const STRONG_LIKELIHOOD_THRESHOLD = 0.7;
const MEDIUM_LIKELIHOOD_THRESHOLD = 0.4;
const MIN_TEXT_OVERLAP_LENGTH = 4;
const STORIES_PROXIMITY_THRESHOLD = 2;
const COORDINATE_OVERLAP_DISTANCE_METERS = 250;
const UNIT_FIELDS = [
  "totalUnits",
  "marketRateUnits",
  "affordableUnits",
  "workforceUnits"
] as const;
type DiscoveryUnitField = (typeof UNIT_FIELDS)[number];

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

export function mapMatchPreviewResponse(payload: unknown): DiscoveryMatchPreview {
  const row = asRecord(payload) ?? {};
  return {
    reviewItemsToClose: asNumber(row.review_items_to_close) ?? 0,
    evidenceRowsToReattach: asNumber(row.evidence_rows_to_reattach) ?? 0,
    valueChangeItemsThatWouldBeQueued: Array.isArray(
      row.value_change_items_that_would_be_queued
    )
      ? row.value_change_items_that_would_be_queued
          .map((value) => asString(value))
          .filter((value): value is string => Boolean(value))
      : []
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

export function sortCandidates(
  candidates: DiscoveryCandidate[],
  sort: DiscoveryCandidateSort = { field: "matchLikelihood", direction: "desc" }
) {
  return [...candidates].sort((left, right) => {
    const layerDelta = left.matchLayer - right.matchLayer;
    if (layerDelta !== 0) {
      return layerDelta;
    }
    const fieldDelta = compareSortValues(
      candidateSortValue(left, sort.field),
      candidateSortValue(right, sort.field),
      sort.direction
    );
    if (fieldDelta !== 0) {
      return fieldDelta;
    }
    const likelihoodDelta = right.matchLikelihood - left.matchLikelihood;
    if (likelihoodDelta !== 0) {
      return likelihoodDelta;
    }
    return left.projectId.localeCompare(right.projectId);
  });
}

export function visibleMatchSignals(candidate: DiscoveryCandidate) {
  return Object.entries(candidate.matchSignals)
    .filter(([, signal]) => signal.searched)
    .sort(([left], [right]) => {
      const leftIndex = SIGNAL_DISPLAY_ORDER.indexOf(left);
      const rightIndex = SIGNAL_DISPLAY_ORDER.indexOf(right);
      return (
        (leftIndex === -1 ? Number.MAX_SAFE_INTEGER : leftIndex) -
          (rightIndex === -1 ? Number.MAX_SAFE_INTEGER : rightIndex) ||
        left.localeCompare(right)
      );
    });
}

export function candidateBandTone(candidate: DiscoveryCandidate) {
  if (candidate.matchLayer === 1) {
    return "hard";
  }
  if (candidate.matchLayer === 3) {
    return "broad";
  }
  if (candidate.matchLikelihood >= STRONG_LIKELIHOOD_THRESHOLD) {
    return "strong";
  }
  if (candidate.matchLikelihood >= MEDIUM_LIKELIHOOD_THRESHOLD) {
    return "medium";
  }
  return "weak";
}

export function computeCandidateOverlaps(
  subject: DiscoverySubject,
  candidate: DiscoveryCandidate
): DiscoveryOverlapMap {
  const overlaps: DiscoveryOverlapMap = {};
  addTextOverlap(overlaps, "projectName", subject.projectName, candidate.projectName);
  addTextOverlap(
    overlaps,
    "canonicalAddress",
    subject.canonicalAddress,
    candidate.canonicalAddress
  );
  addTextOverlap(overlaps, "developer", subject.developer, candidate.developer);
  addUnitOverlaps(overlaps, subject, candidate);
  addProductOverlap(overlaps, subject, candidate);
  addExactTextOverlap(
    overlaps,
    "pipelineStatus",
    "pipelineStatus",
    subject.pipelineStatus,
    candidate.pipelineStatus
  );
  addStoriesOverlap(overlaps, subject, candidate);
  addCoordinateOverlap(overlaps, subject, candidate);
  return overlaps;
}

export function candidateFocusByOffset(
  candidates: DiscoveryCandidate[],
  currentProjectId: string | null,
  offset: number
) {
  if (!candidates.length) {
    return null;
  }
  const currentIndex = candidates.findIndex((candidate) => candidate.projectId === currentProjectId);
  const baseIndex = currentIndex >= 0 ? currentIndex : 0;
  const nextIndex = Math.min(candidates.length - 1, Math.max(0, baseIndex + offset));
  return candidates[nextIndex]?.projectId ?? null;
}

export function candidateFocusByNumber(candidates: DiscoveryCandidate[], numberKey: string) {
  const index = Number.parseInt(numberKey, 10) - 1;
  if (!Number.isInteger(index) || index < 0 || index >= candidates.length) {
    return null;
  }
  return candidates[index]?.projectId ?? null;
}

export function matchPreviewImpactText(preview: DiscoveryMatchPreview) {
  const closeText = `${preview.reviewItemsToClose.toLocaleString()} review item${
    preview.reviewItemsToClose === 1 ? "" : "s"
  }`;
  const evidenceText = `${preview.evidenceRowsToReattach.toLocaleString()} evidence row${
    preview.evidenceRowsToReattach === 1 ? "" : "s"
  }`;
  const deltaCount = preview.valueChangeItemsThatWouldBeQueued.length;
  const deltaText =
    deltaCount > 0
      ? `${deltaCount.toLocaleString()} value-change review${deltaCount === 1 ? "" : "s"}`
      : "no value-change reviews";
  return `Would close ${closeText}, reattach ${evidenceText}, and queue ${deltaText}.`;
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

function candidateSortValue(candidate: DiscoveryCandidate, field: DiscoveryCandidateSortField) {
  switch (field) {
    case "projectName":
      return candidate.projectName;
    case "canonicalAddress":
      return candidate.canonicalAddress;
    case "developer":
      return candidate.developer;
    case "totalUnits":
      return candidate.totalUnits;
    case "productType":
      return candidate.productType ?? candidate.ageRestriction;
    case "pipelineStatus":
      return candidate.pipelineStatus;
    case "stories":
      return candidate.stories;
    case "matchLikelihood":
      return candidate.matchLikelihood;
  }
}

function compareSortValues(
  left: string | number | null,
  right: string | number | null,
  direction: "asc" | "desc"
) {
  if (left === null && right === null) {
    return 0;
  }
  if (left === null) {
    return 1;
  }
  if (right === null) {
    return -1;
  }
  const multiplier = direction === "asc" ? 1 : -1;
  if (typeof left === "number" && typeof right === "number") {
    return (left - right) * multiplier;
  }
  return String(left).localeCompare(String(right)) * multiplier;
}

function addTextOverlap(
  overlaps: DiscoveryOverlapMap,
  candidateField: DiscoveryOverlapField,
  subjectValue: string | null,
  candidateValue: string | null
) {
  const subjectText = comparableText(subjectValue);
  const candidateText = comparableText(candidateValue);
  if (!subjectText || !candidateText) {
    return;
  }
  if (
    subjectText.length < MIN_TEXT_OVERLAP_LENGTH ||
    candidateText.length < MIN_TEXT_OVERLAP_LENGTH
  ) {
    return;
  }
  if (!subjectText.includes(candidateText) && !candidateText.includes(subjectText)) {
    return;
  }
  overlaps[candidateField] = {
    kind: "text-substring",
    matchedSubjectField: candidateField,
    matchedValue: subjectValue ?? ""
  };
}

function addExactTextOverlap(
  overlaps: DiscoveryOverlapMap,
  candidateField: DiscoveryOverlapField,
  subjectField: DiscoveryOverlapField,
  subjectValue: string | null,
  candidateValue: string | null
) {
  const subjectText = comparableText(subjectValue);
  const candidateText = comparableText(candidateValue);
  if (!subjectText || !candidateText || subjectText !== candidateText) {
    return;
  }
  overlaps[candidateField] = {
    kind: "exact-match",
    matchedSubjectField: subjectField,
    matchedValue: subjectValue ?? ""
  };
}

function addProductOverlap(
  overlaps: DiscoveryOverlapMap,
  subject: DiscoverySubject,
  candidate: DiscoveryCandidate
) {
  const subjectField = subject.productType ? "productType" : "ageRestriction";
  const candidateValue = candidate.productType ?? candidate.ageRestriction;
  const subjectValue = subject.productType ?? subject.ageRestriction;
  addExactTextOverlap(overlaps, "productType", subjectField, subjectValue, candidateValue);
}

function addUnitOverlaps(
  overlaps: DiscoveryOverlapMap,
  subject: DiscoverySubject,
  candidate: DiscoveryCandidate
) {
  for (const candidateField of UNIT_FIELDS) {
    const candidateValue = candidate[candidateField];
    if (candidateValue === null) {
      continue;
    }
    const match = matchingSubjectUnit(subject, candidateField, candidateValue);
    if (!match) {
      continue;
    }
    overlaps[candidateField] = {
      kind: match.field === candidateField ? "exact-match" : "cross-field-unit-match",
      matchedSubjectField: match.field,
      matchedValue: match.value
    };
  }
}

function matchingSubjectUnit(
  subject: DiscoverySubject,
  preferredField: DiscoveryUnitField,
  candidateValue: number
) {
  const orderedFields = [
    preferredField,
    ...UNIT_FIELDS.filter((field) => field !== preferredField)
  ];
  for (const field of orderedFields) {
    const subjectValue = subject[field];
    if (subjectValue !== null && subjectValue === candidateValue) {
      return { field, value: subjectValue };
    }
  }
  return null;
}

function addStoriesOverlap(
  overlaps: DiscoveryOverlapMap,
  subject: DiscoverySubject,
  candidate: DiscoveryCandidate
) {
  if (subject.stories === null || candidate.stories === null) {
    return;
  }
  if (Math.abs(subject.stories - candidate.stories) > STORIES_PROXIMITY_THRESHOLD) {
    return;
  }
  overlaps.stories = {
    kind: "stories-proximity",
    matchedSubjectField: "stories",
    matchedValue: subject.stories,
    detail: `within ${STORIES_PROXIMITY_THRESHOLD} stories`
  };
}

function addCoordinateOverlap(
  overlaps: DiscoveryOverlapMap,
  subject: DiscoverySubject,
  candidate: DiscoveryCandidate
) {
  // distanceMeters is API-computed against the loaded subject. Once subject-row
  // edits ship, recompute this client-side for edited coordinates before render.
  if (
    subject.lat === null ||
    subject.lng === null ||
    candidate.lat === null ||
    candidate.lng === null ||
    candidate.distanceMeters === null ||
    candidate.distanceMeters > COORDINATE_OVERLAP_DISTANCE_METERS
  ) {
    return;
  }
  const detail = `${Math.round(candidate.distanceMeters)}m from subject coordinates`;
  overlaps.lat = {
    kind: "distance-threshold",
    matchedSubjectField: "lat",
    matchedValue: subject.lat,
    detail
  };
  overlaps.lng = {
    kind: "distance-threshold",
    matchedSubjectField: "lng",
    matchedValue: subject.lng,
    detail
  };
}

function comparableText(value: string | null) {
  return value
    ?.trim()
    .toLocaleLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
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

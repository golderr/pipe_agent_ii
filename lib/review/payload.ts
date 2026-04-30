import type { ReviewQueueItem } from "./types";

export type PayloadRow = {
  key: string;
  value: string;
};

export type NewsContext = {
  articleId: string | null;
  extractionId: string | null;
  referenceId: string | null;
  referenceIndex: number | null;
  extractionConfidence: string | null;
  structuralDisagreement: Record<string, unknown> | null;
  extractionVersion: number | null;
  promptId: string | null;
  promptVersion: string | null;
  evidenceId: string | null;
  articleTitle: string | null;
  publishedAt: string | null;
  url: string | null;
};

export function isStagedByMe(
  item: ReviewQueueItem,
  currentUserId: string,
  currentUserEmail: string | null
) {
  const decision = item.activeDecision;
  if (!decision || decision.state !== "staged") {
    return false;
  }
  return decision.stagedBy === currentUserId || Boolean(currentUserEmail && decision.stagedByEmail === currentUserEmail);
}

export function isStagedByOther(
  item: ReviewQueueItem,
  currentUserId: string,
  currentUserEmail: string | null
) {
  return Boolean(item.activeDecision?.state === "staged") && !isStagedByMe(item, currentUserId, currentUserEmail);
}

export function acceptDecisionValue(item: ReviewQueueItem) {
  if (item.itemType === "new_candidate") {
    return { create_new: true, new_project_data: newProjectDataForItem(item) };
  }
  const targetProjectId = candidateProjectIdsForItem(item)[0];
  return targetProjectId ? { project_id: targetProjectId } : undefined;
}

export function candidateProjectIdsForItem(item: ReviewQueueItem) {
  const payload = asRecord(item.payload);
  const match = asRecord(payload?.match);
  return asStringArray(match?.candidate_project_ids ?? payload?.candidate_project_ids);
}

export function candidateValuesForItem(item: ReviewQueueItem) {
  if (item.itemType === "new_candidate" || item.itemType === "possible_match") {
    return [];
  }
  const candidates = asRecordArray(item.payload?.candidates);
  return candidates.map((candidate) => ("value" in candidate ? candidate.value : candidate));
}

export function newProjectDataForItem(item: ReviewQueueItem) {
  const mappedFields = asRecord(item.payload?.mapped_fields);
  return {
    canonical_address: asString(item.payload?.canonical_address) ?? undefined,
    project_name: asString(mappedFields?.project_name) ?? undefined,
    city: asString(mappedFields?.city) ?? undefined,
    state: asString(mappedFields?.state) ?? undefined,
    county: asString(mappedFields?.county) ?? undefined,
    zip: asString(mappedFields?.zip) ?? undefined
  };
}

export function fieldNameForItem(item: ReviewQueueItem) {
  const payload = item.payload;
  const change = firstChange(item);
  const statusSuggestion = asRecord(payload?.status_suggestion);
  return (
    item.fieldName ??
    asString(payload?.field_name) ??
    (statusSuggestion ? "pipeline_status" : null) ??
    asString(change?.field) ??
    asString(change?.field_name) ??
    item.itemType
  );
}

export function currentValueForItem(item: ReviewQueueItem) {
  const payload = item.payload;
  const currentOverride = asRecord(payload?.current_override);
  const statusSuggestion = asRecord(payload?.status_suggestion);
  const change = firstChange(item);
  if (currentOverride && "value" in currentOverride) {
    return currentOverride.value;
  }
  if (payload && "current_value" in payload) {
    return payload.current_value;
  }
  if (statusSuggestion && "current_status" in statusSuggestion) {
    return statusSuggestion.current_status;
  }
  if (change && "old_value" in change) {
    return change.old_value;
  }
  return null;
}

export function proposedValueForItem(item: ReviewQueueItem) {
  const payload = item.payload;
  const candidate = asRecord(payload?.candidate);
  const statusSuggestion = asRecord(payload?.status_suggestion);
  const change = firstChange(item);
  if (payload && "proposed_value" in payload) {
    return payload.proposed_value;
  }
  if (candidate && "value" in candidate) {
    return candidate.value;
  }
  if (statusSuggestion && "suggested_status" in statusSuggestion) {
    return statusSuggestion.suggested_status;
  }
  if (change && "new_value" in change) {
    return change.new_value;
  }
  if (payload && "canonical_address" in payload) {
    return payload.canonical_address;
  }
  return null;
}

export function sourceTextForItem(item: ReviewQueueItem) {
  const payload = item.payload;
  const newsContext = newsContextForItem(item);
  if (newsContext) {
    const source = newsContext.articleTitle ?? "News article";
    return newsContext.publishedAt ? `${source} - ${formatDate(newsContext.publishedAt)}` : source;
  }
  const candidate = asRecord(payload?.candidate);
  const frontier = asRecord(candidate?.evidence_frontier);
  const source = asString(frontier?.source_type) ?? asString(payload?.source_record_id);
  const date = asString(candidate?.evidence_date);
  if (source && date) {
    return `${source} - ${formatDate(date)}`;
  }
  return source;
}

export function newsContextForItem(item: ReviewQueueItem): NewsContext | null {
  const context = asRecord(item.payload?.news_context);
  if (!context) {
    return null;
  }
  const structuralDisagreement = asRecord(context.structural_disagreement);
  const articleId = asString(context.article_id);
  const extractionId = asString(context.extraction_id);
  const url = asString(context.url);
  const articleTitle = asString(context.article_title);
  if (!articleId && !extractionId && !url && !articleTitle) {
    return null;
  }
  return {
    articleId,
    extractionId,
    referenceId: asString(context.reference_id),
    referenceIndex: asNumber(context.reference_index),
    extractionConfidence: asString(context.extraction_confidence),
    structuralDisagreement,
    extractionVersion: asNumber(context.extraction_version),
    promptId: asString(context.prompt_id),
    promptVersion: asString(context.prompt_version),
    evidenceId: asString(context.evidence_id),
    articleTitle,
    publishedAt: asString(context.published_at),
    url
  };
}

export function structuralDisagreementText(
  context: NewsContext | null,
  emittedValue?: unknown
) {
  const disagreement = context?.structuralDisagreement;
  if (!disagreement) {
    return null;
  }
  const extractor = asString(disagreement.extractor) ?? "extractor";
  const rawMatch = asString(disagreement.raw_match);
  const hasCanonical = Object.prototype.hasOwnProperty.call(disagreement, "canonical");
  const canonical = hasCanonical ? disagreement.canonical : null;
  let text = `Pass 1 ${extractor}`;
  text += rawMatch ? ` matched "${rawMatch}"` : " flagged a structural signal";
  if (canonical !== null && canonical !== undefined && canonical !== "") {
    text += ` (canonical: ${formatValue(canonical)})`;
  }
  if (emittedValue !== null && emittedValue !== undefined && emittedValue !== "") {
    text += ` - Pass 2 emitted ${formatValue(emittedValue)}`;
  }
  return text;
}

export function supportingEvidenceForItem(item: ReviewQueueItem) {
  return item.evidenceSummaries.filter((evidence) => evidence.stance === "supporting");
}

export function dissentingEvidenceForItem(item: ReviewQueueItem) {
  return item.evidenceSummaries.filter((evidence) => evidence.stance === "against");
}

export function winningEvidenceForItem(item: ReviewQueueItem) {
  return item.evidenceSummaries.find((evidence) => evidence.isWinning) ?? null;
}

export function warningForItem(item: ReviewQueueItem) {
  const payload = item.payload;
  const flags = asRecordArray(payload?.review_flags);
  const firstFlag = flags[0];
  return (
    asString(payload?.message) ??
    asString(firstFlag?.message) ??
    (item.itemType.includes("contradiction") ? "This item conflicts with a manual override." : null)
  );
}

export function firstChange(item: ReviewQueueItem) {
  return asRecordArray(item.payload?.changes)[0] ?? null;
}

export function flattenPayload(payload: Record<string, unknown> | null): PayloadRow[] {
  if (!payload) {
    return [];
  }
  const rows: PayloadRow[] = [];
  for (const [key, value] of Object.entries(payload)) {
    appendPayloadRows(rows, key, value, 0);
  }
  return rows;
}

function appendPayloadRows(rows: PayloadRow[], key: string, value: unknown, depth: number) {
  if (value === null || value === undefined || value === "") {
    return;
  }
  const record = asRecord(value);
  if (record && depth < 1) {
    for (const [childKey, childValue] of Object.entries(record)) {
      appendPayloadRows(rows, `${key}.${childKey}`, childValue, depth + 1);
    }
    return;
  }
  rows.push({ key, value: formatValue(value) });
}

export function formatInputValue(value: unknown) {
  if (value === null || value === undefined) {
    return "";
  }
  if (typeof value === "string") {
    return value;
  }
  return formatValue(value);
}

export function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  if (typeof value === "number") {
    return value.toLocaleString();
  }
  if (typeof value === "boolean") {
    return value ? "Yes" : "No";
  }
  if (typeof value === "string") {
    return value;
  }
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

export function displayActor(email: string | null | undefined, fallback: string | null | undefined) {
  if (email) {
    return email;
  }
  if (fallback && fallback.length > 8) {
    return `${fallback.slice(0, 4)}...${fallback.slice(-4)}`;
  }
  return fallback ?? "unknown";
}

export function humanize(value: string) {
  return value
    .split(/[_.-]/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

export function formatDate(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  const date = /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T12:00:00`) : new Date(value);
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric"
  }).format(date);
}

export function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit"
  }).format(new Date(value));
}

export function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

export function asRecordArray(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.map(asRecord).filter((row): row is Record<string, unknown> => Boolean(row)) : [];
}

export function asString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

export function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && Boolean(item)) : [];
}

export function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

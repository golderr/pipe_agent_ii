import "server-only";

import { requireApiBaseUrl } from "@/lib/env";
import { candidateProjectIdsForItem, fieldNameForItem } from "@/lib/review/payload";
import { accessTokenForApi, responseErrorMessage } from "@/lib/server-actions";
import { createSupabaseServerClient } from "@/lib/supabase/server";
import type {
  ReviewDecisionSummary,
  ReviewItemDetailData,
  ReviewItemNavigation,
  ReviewProcessedChange,
  ReviewProjectSummary,
  ReviewQueueData,
  ReviewQueueItem,
  ReviewValueChangePayload,
  ReviewSourceRunSummary
} from "@/lib/review/types";

type ReviewQueueDataResult =
  | { data: ReviewQueueData; error: null }
  | { data: null; error: string };

type ReviewItemDetailDataResult =
  | { data: ReviewItemDetailData; error: null; notFound?: false }
  | { data: null; error: string; notFound?: false }
  | { data: null; error: null; notFound: true };

type ReviewDecisionApi = {
  decision_id: string;
  state: string;
  decision_type: string | null;
  staged_at: string | null;
  staged_by: string | null;
  staged_by_email: string | null;
  committed_at: string | null;
  committed_by: string | null;
  committed_by_email: string | null;
  decision_value: unknown;
  decision_notes: string | null;
  source_url: string | null;
};

type ReviewQueueItemApi = {
  id: string;
  project_id: string | null;
  source_run_id: string | null;
  item_type: string;
  status: string;
  state: string;
  priority: string;
  match_confidence: number | null;
  field_name: string | null;
  winning_evidence_id: string | null;
  payload: Record<string, unknown> | null;
  assigned_to: string | null;
  created_at: string;
  resolved_at: string | null;
  resolved_by: string | null;
  active_decision: ReviewDecisionApi | null;
  value_change?: ReviewValueChangeApi | null;
  evidence_summaries?: ReviewEvidenceSummaryApi[];
};

type ReviewValueChangeApi = {
  field_name: string;
  field_label: string;
  field_type: string;
  current_value: unknown;
  evidence_value: unknown;
  agent_recommended_value: unknown;
  default_result_value: unknown;
  constraints?: {
    enum_values?: string[];
    min?: number;
    max?: number;
  } | null;
  supporting_evidence_ids?: string[];
  dissenting_evidence_ids?: string[];
  human_summary?: string | null;
};

type ReviewEvidenceSummaryApi = {
  evidence_id: string;
  stance: "supporting" | "against" | "silent";
  is_winning: boolean;
  source_type: string;
  source_tier: number;
  source_record_id: string | null;
  evidence_date: string | null;
  collected_at: string;
  summary: string;
  detail: string;
  source_fields?: Record<string, unknown> | null;
  external_link: string | null;
  highlights: Array<Record<string, unknown>>;
  extracted_value: unknown;
};

type RawProject = {
  id: string;
  project_name: string | null;
  canonical_address: string;
  city: string | null;
  state: string | null;
  zip: string | null;
  market: string;
  jurisdiction_id: string | null;
  pipeline_status: string;
  developer: string | null;
  total_units: number | null;
  date_delivery: string | null;
};

type RawSourceRun = {
  id: string;
  source_name: string;
  run_timestamp: string;
  finished_at: string | null;
};

type RawChangeLog = {
  id: string;
  timestamp: string;
  source: string;
  field: string;
  old_value: unknown;
  new_value: unknown;
  change_type: string;
  priority: string;
  reviewed_by: string | null;
  reviewed_by_user_id: string | null;
  reviewed_by_email: string | null;
  review_item_id: string | null;
};

export async function getReviewQueueData(options: {
  jurisdictionId?: string | null;
} = {}): Promise<ReviewQueueDataResult> {
  try {
    const apiBaseUrl = requireApiBaseUrl();
    const accessToken = await accessTokenForApi();
    const [queue, reviewed] = await Promise.all([
      fetchReviewItemsFromApi(apiBaseUrl, accessToken, {
        jurisdictionId: options.jurisdictionId
      }),
      fetchReviewItemsFromApi(apiBaseUrl, accessToken, {
        jurisdictionId: options.jurisdictionId,
        state: "committed",
        limit: 500
      })
    ]);
    if (queue.error) {
      return { data: null, error: queue.error };
    }
    if (reviewed.error) {
      return { data: null, error: reviewed.error };
    }
    const items = queue.items;
    const reviewedItems = reviewed.items;
    const hydratedItems = [...items, ...reviewedItems];
    const [projects, sourceRuns] = await Promise.all([
      fetchProjects(hydratedItems),
      fetchSourceRuns(hydratedItems)
    ]);
    const error = projects.error ?? sourceRuns.error;
    if (error) {
      return { data: null, error };
    }

    return {
      data: {
        items,
        reviewedItems,
        projects: Object.fromEntries(projects.rows.map((project) => [project.id, project])),
        sourceRuns: Object.fromEntries(sourceRuns.rows.map((sourceRun) => [sourceRun.id, sourceRun])),
        generatedAt: new Date().toISOString()
      },
      error: null
    };
  } catch (error) {
    return {
      data: null,
      error: error instanceof Error ? error.message : "Review queue request failed."
    };
  }
}

export async function getReviewItemDetailData(
  itemId: string,
  options: { jurisdictionId?: string | null } = {}
): Promise<ReviewItemDetailDataResult> {
  try {
    const apiBaseUrl = requireApiBaseUrl();
    const accessToken = await accessTokenForApi();
    const response = await fetch(`${apiBaseUrl}/review/queue/${itemId}`, {
      headers: {
        Authorization: `Bearer ${accessToken}`
      },
      cache: "no-store"
    });

    if (response.status === 404) {
      return { data: null, error: null, notFound: true };
    }
    if (!response.ok) {
      return {
        data: null,
        error: await responseErrorMessage(response, "Review item request failed.")
      };
    }

    const item = mapReviewItem((await response.json()) as ReviewQueueItemApi);
    const [queue, projects, sourceRuns, processedChanges] = await Promise.all([
      fetchReviewItemsFromApi(apiBaseUrl, accessToken, {
        jurisdictionId: options.jurisdictionId
      }),
      fetchProjects([item]),
      fetchSourceRuns([item]),
      fetchProcessedChanges(item)
    ]);
    const error = queue.error ?? projects.error ?? sourceRuns.error ?? processedChanges.error;
    if (error) {
      return { data: null, error };
    }

    const projectById = new Map(projects.rows.map((project) => [project.id, project]));
    const candidateProjects = candidateProjectIdsForItem(item)
      .map((projectId) => projectById.get(projectId))
      .filter((project): project is ReviewProjectSummary => Boolean(project));

    return {
      data: {
        item,
        project: item.projectId ? projectById.get(item.projectId) ?? null : null,
        candidateProjects,
        sourceRun: sourceRuns.rows[0] ?? null,
        navigation: buildNavigation(queue.items, item.id, options.jurisdictionId ?? null),
        processedChanges: processedChanges.rows,
        generatedAt: new Date().toISOString()
      },
      error: null
    };
  } catch (error) {
    return {
      data: null,
      error: error instanceof Error ? error.message : "Review item request failed."
    };
  }
}

async function fetchReviewItemsFromApi(
  apiBaseUrl: string,
  accessToken: string,
  options: { jurisdictionId?: string | null; state?: string | null; limit?: number } = {}
): Promise<{ items: ReviewQueueItem[]; error: string | null }> {
  const params = new URLSearchParams({ limit: String(options.limit ?? 500) });
  if (options.jurisdictionId) {
    params.set("jurisdiction_id", options.jurisdictionId);
  }
  if (options.state) {
    params.set("state", options.state);
  }
  const response = await fetch(`${apiBaseUrl}/review/queue?${params.toString()}`, {
    headers: {
      Authorization: `Bearer ${accessToken}`
    },
    cache: "no-store"
  });

  if (!response.ok) {
    return {
      items: [],
      error: await responseErrorMessage(response, "Review queue request failed.")
    };
  }

  return {
    items: ((await response.json()) as ReviewQueueItemApi[]).map(mapReviewItem),
    error: null
  };
}

function buildNavigation(
  items: ReviewQueueItem[],
  itemId: string,
  jurisdictionId: string | null
): ReviewItemNavigation {
  const itemIds = items.map((item) => item.id);
  const index = itemIds.indexOf(itemId);
  return {
    previousItemId: index > 0 ? itemIds[index - 1] : null,
    nextItemId: index >= 0 && index < itemIds.length - 1 ? itemIds[index + 1] : null,
    position: index >= 0 ? index + 1 : null,
    total: itemIds.length,
    jurisdictionId
  };
}

async function fetchProjects(
  items: ReviewQueueItem[]
): Promise<{ rows: ReviewProjectSummary[]; error: string | null }> {
  const projectIds = uniqueStrings([
    ...items.map((item) => item.projectId),
    ...items.flatMap(candidateProjectIdsForItem)
  ]);
  if (projectIds.length === 0) {
    return { rows: [], error: null };
  }

  const supabase = await createSupabaseServerClient();
  const { data, error } = await supabase
    .from("projects")
    .select(
      [
        "id",
        "project_name",
        "canonical_address",
        "city",
        "state",
        "zip",
        "market",
        "jurisdiction_id",
        "pipeline_status",
        "developer",
        "total_units",
        "date_delivery"
      ].join(", ")
    )
    .in("id", projectIds);

  if (error) {
    return { rows: [], error: error.message };
  }

  return {
    rows: ((data ?? []) as unknown as RawProject[]).map((project) => ({
      id: project.id,
      projectName: project.project_name ?? project.canonical_address,
      canonicalAddress: project.canonical_address,
      city: project.city,
      state: project.state,
      zip: project.zip,
      market: project.market,
      jurisdictionId: project.jurisdiction_id,
      pipelineStatus: project.pipeline_status,
      developer: project.developer,
      totalUnits: project.total_units,
      dateDelivery: project.date_delivery
    })),
    error: null
  };
}

async function fetchSourceRuns(
  items: ReviewQueueItem[]
): Promise<{ rows: ReviewSourceRunSummary[]; error: string | null }> {
  const sourceRunIds = uniqueStrings(items.map((item) => item.sourceRunId));
  if (sourceRunIds.length === 0) {
    return { rows: [], error: null };
  }

  const supabase = await createSupabaseServerClient();
  const { data, error } = await supabase
    .from("source_runs")
    .select("id, source_name, run_timestamp, finished_at")
    .in("id", sourceRunIds);

  if (error) {
    return { rows: [], error: error.message };
  }

  return {
    rows: ((data ?? []) as unknown as RawSourceRun[]).map((sourceRun) => ({
      id: sourceRun.id,
      sourceName: sourceRun.source_name,
      runTimestamp: sourceRun.run_timestamp,
      finishedAt: sourceRun.finished_at
    })),
    error: null
  };
}

async function fetchProcessedChanges(
  item: ReviewQueueItem
): Promise<{ rows: ReviewProcessedChange[]; error: string | null }> {
  if (!item.projectId) {
    return { rows: [], error: null };
  }
  const supabase = await createSupabaseServerClient();
  const { data, error } = await supabase
    .from("change_log")
    .select(
      "id, timestamp, source, field, old_value, new_value, change_type, priority, reviewed_by, reviewed_by_user_id, reviewed_by_email, review_item_id"
    )
    .eq("project_id", item.projectId)
    .eq("field", fieldNameForItem(item))
    .order("timestamp", { ascending: false })
    .limit(8);

  if (error) {
    return { rows: [], error: error.message };
  }

  return {
    rows: ((data ?? []) as unknown as RawChangeLog[]).map((change) => ({
      id: change.id,
      timestamp: change.timestamp,
      source: change.source,
      field: change.field,
      oldValue: change.old_value,
      newValue: change.new_value,
      changeType: change.change_type,
      priority: change.priority,
      reviewedBy: change.reviewed_by,
      reviewedByUserId: change.reviewed_by_user_id,
      reviewedByEmail: change.reviewed_by_email,
      reviewItemId: change.review_item_id
    })),
    error: null
  };
}

function mapReviewItem(item: ReviewQueueItemApi): ReviewQueueItem {
  return {
    id: item.id,
    projectId: item.project_id,
    sourceRunId: item.source_run_id,
    itemType: item.item_type,
    status: item.status,
    state: item.state,
    priority: item.priority,
    matchConfidence: item.match_confidence,
    fieldName: item.field_name,
    winningEvidenceId: item.winning_evidence_id,
    payload: item.payload,
    assignedTo: item.assigned_to,
    createdAt: item.created_at,
    resolvedAt: item.resolved_at,
    resolvedBy: item.resolved_by,
    activeDecision: item.active_decision ? mapDecision(item.active_decision) : null,
    valueChange: item.value_change ? mapValueChange(item.value_change) : null,
    evidenceSummaries: (item.evidence_summaries ?? []).map((evidence) => ({
      evidenceId: evidence.evidence_id,
      stance: evidence.stance,
      isWinning: evidence.is_winning,
      sourceType: evidence.source_type,
      sourceTier: evidence.source_tier,
      sourceRecordId: evidence.source_record_id,
      evidenceDate: evidence.evidence_date,
      collectedAt: evidence.collected_at,
      summary: evidence.summary,
      detail: evidence.detail,
      sourceFields: evidence.source_fields ?? {},
      externalLink: evidence.external_link,
      highlights: evidence.highlights ?? [],
      extractedValue: evidence.extracted_value
    }))
  };
}

function mapValueChange(payload: ReviewValueChangeApi): ReviewValueChangePayload {
  return {
    fieldName: payload.field_name,
    fieldLabel: payload.field_label,
    fieldType: payload.field_type,
    currentValue: payload.current_value,
    evidenceValue: payload.evidence_value,
    agentRecommendedValue: payload.agent_recommended_value,
    defaultResultValue: payload.default_result_value,
    constraints: {
      enumValues: payload.constraints?.enum_values,
      min: payload.constraints?.min,
      max: payload.constraints?.max
    },
    supportingEvidenceIds: payload.supporting_evidence_ids ?? [],
    dissentingEvidenceIds: payload.dissenting_evidence_ids ?? [],
    humanSummary: payload.human_summary ?? null
  };
}

function mapDecision(decision: ReviewDecisionApi): ReviewDecisionSummary {
  return {
    decisionId: decision.decision_id,
    state: decision.state,
    decisionType: decision.decision_type,
    stagedAt: decision.staged_at,
    stagedBy: decision.staged_by,
    stagedByEmail: decision.staged_by_email,
    committedAt: decision.committed_at,
    committedBy: decision.committed_by,
    committedByEmail: decision.committed_by_email,
    decisionValue: decision.decision_value,
    decisionNotes: decision.decision_notes,
    sourceUrl: decision.source_url
  };
}

function uniqueStrings(values: Array<string | null | undefined>) {
  return [...new Set(values.filter((value): value is string => Boolean(value)))];
}

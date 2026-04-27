import "server-only";

import { requireApiBaseUrl } from "@/lib/env";
import { accessTokenForApi, responseErrorMessage } from "@/lib/server-actions";
import { createSupabaseServerClient } from "@/lib/supabase/server";
import type {
  ReviewDecisionSummary,
  ReviewProjectSummary,
  ReviewQueueData,
  ReviewQueueItem,
  ReviewSourceRunSummary
} from "@/lib/review/types";

type ReviewQueueDataResult =
  | { data: ReviewQueueData; error: null }
  | { data: null; error: string };

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
  payload: Record<string, unknown> | null;
  assigned_to: string | null;
  created_at: string;
  resolved_at: string | null;
  resolved_by: string | null;
  active_decision: ReviewDecisionApi | null;
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

export async function getReviewQueueData(options: {
  jurisdictionId?: string | null;
} = {}): Promise<ReviewQueueDataResult> {
  try {
    const apiBaseUrl = requireApiBaseUrl();
    const accessToken = await accessTokenForApi();
    const params = new URLSearchParams({ limit: "500" });
    if (options.jurisdictionId) {
      params.set("jurisdiction_id", options.jurisdictionId);
    }
    const response = await fetch(`${apiBaseUrl}/review/queue?${params.toString()}`, {
      headers: {
        Authorization: `Bearer ${accessToken}`
      },
      cache: "no-store"
    });

    if (!response.ok) {
      return {
        data: null,
        error: await responseErrorMessage(response, "Review queue request failed.")
      };
    }

    const apiItems = (await response.json()) as ReviewQueueItemApi[];
    const items = apiItems.map(mapReviewItem);
    const [projects, sourceRuns] = await Promise.all([
      fetchProjects(items),
      fetchSourceRuns(items)
    ]);
    const error = projects.error ?? sourceRuns.error;
    if (error) {
      return { data: null, error };
    }

    return {
      data: {
        items,
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

async function fetchProjects(
  items: ReviewQueueItem[]
): Promise<{ rows: ReviewProjectSummary[]; error: string | null }> {
  const projectIds = uniqueStrings(items.map((item) => item.projectId));
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
    payload: item.payload,
    assignedTo: item.assigned_to,
    createdAt: item.created_at,
    resolvedAt: item.resolved_at,
    resolvedBy: item.resolved_by,
    activeDecision: item.active_decision ? mapDecision(item.active_decision) : null
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

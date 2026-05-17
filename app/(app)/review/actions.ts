"use server";

import { revalidatePath } from "next/cache";
import { requireApiBaseUrl } from "@/lib/env";
import {
  mapDedupCandidatesResponse,
  mapMatchPreviewResponse,
  type DiscoveryCandidateSearch,
  type DiscoveryMatchPreview
} from "@/lib/review/discovery";
import {
  accessTokenForApi,
  apiBaseUrlForWrite,
  jsonHeadersForApi,
  responseErrorMessage
} from "@/lib/server-actions";

export type StageReviewDecisionInput = {
  reviewItemId: string;
  decisionType: string;
  decisionValue?: unknown;
  notes?: string | null;
  sourceUrl?: string | null;
  revise?: boolean;
};

export type ReviewMutationResult = {
  ok: boolean;
  message: string;
  status?: number;
};

export type DedupCandidatesActionResult =
  | { ok: true; data: DiscoveryCandidateSearch }
  | { ok: false; message: string; status?: number };

export type MatchPreviewActionResult =
  | { ok: true; data: DiscoveryMatchPreview }
  | { ok: false; message: string; status?: number };

export type DedupWriteActionResult =
  | {
      ok: true;
      data: {
        reviewItemId: string;
        projectId: string;
        referenceId: string | null;
        closedReviewItems: number;
        evidenceRowsReattached: number;
        valueChangeItemsQueued: string[];
        changeLogEntriesCreated: number;
        relationshipId: string | null;
      };
      message: string;
    }
  | { ok: false; message: string; status?: number };

export async function fetchDedupCandidatesAction(
  reviewItemId: string,
  options: { includeLayer3?: boolean } = {}
): Promise<DedupCandidatesActionResult> {
  if (!reviewItemId) {
    return { ok: false, message: "Missing review item." };
  }

  try {
    const apiBaseUrl = requireApiBaseUrl();
    const accessToken = await accessTokenForApi();
    const url = new URL(`${apiBaseUrl}/review/queue/${reviewItemId}/candidates`);
    if (options.includeLayer3) {
      url.searchParams.set("include_layer3", "true");
    }
    const response = await fetch(url.toString(), {
      headers: {
        Authorization: `Bearer ${accessToken}`
      },
      cache: "no-store"
    });

    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        message: await responseErrorMessage(response, "Dedup candidates could not be loaded.")
      };
    }

    return {
      ok: true,
      data: mapDedupCandidatesResponse(await response.json())
    };
  } catch (error) {
    return {
      ok: false,
      message: error instanceof Error ? error.message : "Dedup candidates could not be loaded."
    };
  }
}

export async function fetchMatchPreviewAction(
  reviewItemId: string,
  candidateId: string
): Promise<MatchPreviewActionResult> {
  if (!reviewItemId || !candidateId) {
    return { ok: false, message: "Missing review item or candidate." };
  }

  try {
    const apiBaseUrl = requireApiBaseUrl();
    const accessToken = await accessTokenForApi();
    const url = new URL(`${apiBaseUrl}/review/items/${reviewItemId}/match-preview`);
    url.searchParams.set("candidate_id", candidateId);
    const response = await fetch(url.toString(), {
      headers: {
        Authorization: `Bearer ${accessToken}`
      },
      cache: "no-store"
    });

    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        message: await responseErrorMessage(response, "Match preview could not be loaded.")
      };
    }

    return {
      ok: true,
      data: mapMatchPreviewResponse(await response.json())
    };
  } catch (error) {
    return {
      ok: false,
      message: error instanceof Error ? error.message : "Match preview could not be loaded."
    };
  }
}

export async function matchDiscoveryCandidateAction(input: {
  reviewItemId: string;
  matchedProjectId: string;
  edits?: Record<string, unknown>;
  acceptDeltas?: string[];
}): Promise<DedupWriteActionResult> {
  if (!input.reviewItemId || !input.matchedProjectId) {
    return { ok: false, message: "Missing review item or matched project." };
  }
  return postDedupWrite(
    `/review/items/${input.reviewItemId}/match`,
    {
      matched_project_id: input.matchedProjectId,
      edits: input.edits ?? {},
      accept_deltas: input.acceptDeltas ?? []
    },
    "Matched project."
  );
}

export async function createDiscoveryProjectAction(input: {
  reviewItemId: string;
  projectFields?: Record<string, unknown>;
  edits?: Record<string, unknown>;
}): Promise<DedupWriteActionResult> {
  if (!input.reviewItemId) {
    return { ok: false, message: "Missing review item." };
  }
  return postDedupWrite(
    `/review/items/${input.reviewItemId}/create`,
    {
      project_fields: input.projectFields ?? {},
      edits: input.edits ?? {}
    },
    "Created project."
  );
}

export async function createAndLinkDiscoveryProjectAction(input: {
  reviewItemId: string;
  relatedProjectId: string;
  relationshipType: string;
  projectFields?: Record<string, unknown>;
  edits?: Record<string, unknown>;
}): Promise<DedupWriteActionResult> {
  if (!input.reviewItemId || !input.relatedProjectId || !input.relationshipType) {
    return { ok: false, message: "Missing review item, related project, or relationship type." };
  }
  return postDedupWrite(
    `/review/items/${input.reviewItemId}/create-and-link`,
    {
      relationship_type: input.relationshipType,
      related_project_id: input.relatedProjectId,
      project_fields: input.projectFields ?? {},
      edits: input.edits ?? {}
    },
    "Created and linked project."
  );
}

export async function stageReviewDecisionAction(
  input: StageReviewDecisionInput
): Promise<ReviewMutationResult> {
  if (!input.reviewItemId || !input.decisionType) {
    return { ok: false, message: "Missing review item or decision." };
  }

  try {
    const apiBaseUrl = await apiBaseUrlForWrite();
    const endpoint = input.revise ? "revise" : "decide";
    const response = await fetch(`${apiBaseUrl}/review/${input.reviewItemId}/${endpoint}`, {
      method: "POST",
      headers: await jsonHeadersForApi(),
      body: JSON.stringify({
        decision_type: input.decisionType,
        decision_value: input.decisionValue ?? null,
        notes: input.notes ?? null,
        source_url: input.sourceUrl ?? null
      })
    });

    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        message: await responseErrorMessage(response, "Decision could not be staged.")
      };
    }

    revalidateReviewSurfaces();
    revalidatePath(`/review/${input.reviewItemId}`);
    return {
      ok: true,
      message: input.revise ? "Decision revised." : "Decision staged."
    };
  } catch (error) {
    return {
      ok: false,
      message: error instanceof Error ? error.message : "Decision could not be staged."
    };
  }
}

export async function unstageReviewDecisionAction(
  reviewItemId: string
): Promise<ReviewMutationResult> {
  if (!reviewItemId) {
    return { ok: false, message: "Missing review item." };
  }

  try {
    const apiBaseUrl = await apiBaseUrlForWrite();
    const response = await fetch(`${apiBaseUrl}/review/${reviewItemId}/unstage`, {
      method: "POST",
      headers: await jsonHeadersForApi()
    });

    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        message: await responseErrorMessage(response, "Decision could not be unstaged.")
      };
    }

    revalidateReviewSurfaces();
    revalidatePath(`/review/${reviewItemId}`);
    return { ok: true, message: "Decision unstaged." };
  } catch (error) {
    return {
      ok: false,
      message: error instanceof Error ? error.message : "Decision could not be unstaged."
    };
  }
}

export async function commitReviewDecisionsAction(options: {
  jurisdictionId?: string | null;
} = {}): Promise<ReviewMutationResult> {
  try {
    const apiBaseUrl = await apiBaseUrlForWrite();
    const response = await fetch(`${apiBaseUrl}/review/commit`, {
      method: "POST",
      headers: await jsonHeadersForApi(),
      body: JSON.stringify({
        dry_run: false,
        jurisdiction_id: options.jurisdictionId ?? null
      })
    });

    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        message: await responseErrorMessage(response, "Commit failed.")
      };
    }

    const body = (await response.json()) as {
      committed_decisions: number;
      review_items_remaining: number;
      deferred_items: number;
      queue_cleared: boolean;
    };
    revalidateReviewSurfaces();
    revalidatePath("/pipeline");
    return {
      ok: true,
      message: body.queue_cleared
        ? `Committed ${body.committed_decisions} decisions. Queue cleared.`
        : `Committed ${body.committed_decisions} decisions. ${body.review_items_remaining} open and ${body.deferred_items} deferred remain.`
    };
  } catch (error) {
    return {
      ok: false,
      message: error instanceof Error ? error.message : "Commit failed."
    };
  }
}

async function postDedupWrite(
  path: string,
  payload: Record<string, unknown>,
  successMessage: string
): Promise<DedupWriteActionResult> {
  try {
    const apiBaseUrl = await apiBaseUrlForWrite();
    const response = await fetch(`${apiBaseUrl}${path}`, {
      method: "POST",
      headers: await jsonHeadersForApi(),
      body: JSON.stringify(payload)
    });

    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        message: await responseErrorMessage(response, "Discovery action failed.")
      };
    }

    const body = (await response.json()) as Record<string, unknown>;
    revalidateReviewSurfaces();
    return {
      ok: true,
      data: {
        reviewItemId: String(body.review_item_id ?? ""),
        projectId: String(body.project_id ?? ""),
        referenceId: body.reference_id ? String(body.reference_id) : null,
        closedReviewItems: Number(body.closed_review_items ?? 0),
        evidenceRowsReattached: Number(body.evidence_rows_reattached ?? 0),
        valueChangeItemsQueued: Array.isArray(body.value_change_items_queued)
          ? body.value_change_items_queued.map(String)
          : [],
        changeLogEntriesCreated: Number(body.change_log_entries_created ?? 0),
        relationshipId: body.relationship_id ? String(body.relationship_id) : null
      },
      message: successMessage
    };
  } catch (error) {
    return {
      ok: false,
      message: error instanceof Error ? error.message : "Discovery action failed."
    };
  }
}

function revalidateReviewSurfaces() {
  revalidatePath("/review");
  revalidatePath("/coverage");
  revalidatePath("/dashboard");
}

"use server";

import { revalidatePath } from "next/cache";
import {
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

function revalidateReviewSurfaces() {
  revalidatePath("/review");
  revalidatePath("/coverage");
  revalidatePath("/dashboard");
}

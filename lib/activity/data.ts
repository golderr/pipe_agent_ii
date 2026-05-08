import "server-only";

import { requireApiBaseUrl } from "@/lib/env";
import { accessTokenForApi, responseErrorMessage } from "@/lib/server-actions";
import type { ActivityDataResult, ActivityQuery } from "@/lib/activity/types";

export async function getActivityData(query: ActivityQuery): Promise<ActivityDataResult> {
  try {
    const apiBaseUrl = requireApiBaseUrl();
    const accessToken = await accessTokenForApi();
    const params = new URLSearchParams();
    appendParam(params, "view", query.view);
    appendParam(params, "event_type", query.eventType);
    appendParam(params, "source", query.source);
    appendParam(params, "field", query.field);
    appendParam(params, "actor", query.actor);
    appendParam(params, "project_id", query.projectId);
    appendParam(params, "from_date", query.from);
    appendParam(params, "to_date", query.to);
    params.set("limit", "300");

    const response = await fetch(`${apiBaseUrl}/activity/events?${params.toString()}`, {
      headers: {
        Authorization: `Bearer ${accessToken}`
      },
      cache: "no-store"
    });

    if (!response.ok) {
      return { data: null, error: await responseErrorMessage(response) };
    }

    return { data: await response.json(), error: null };
  } catch (error) {
    return {
      data: null,
      error: error instanceof Error ? error.message : "Activity request failed."
    };
  }
}

function appendParam(params: URLSearchParams, key: string, value: string | null) {
  if (value) {
    params.set(key, value);
  }
}

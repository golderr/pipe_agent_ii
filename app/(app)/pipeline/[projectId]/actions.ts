"use server";

import { revalidatePath } from "next/cache";
import { isEmailAllowed } from "@/lib/auth";
import { previewWritesEnabled, requireApiBaseUrl } from "@/lib/env";
import { createSupabaseServerClient } from "@/lib/supabase/server";

export type OverrideActionState = {
  ok: boolean;
  message: string | null;
};

const initialErrorState: OverrideActionState = {
  ok: false,
  message: null
};

export async function setProjectOverrideAction(
  _previousState: OverrideActionState,
  formData: FormData
): Promise<OverrideActionState> {
  return mutateProjectOverride(formData, "POST");
}

export async function clearProjectOverrideAction(
  _previousState: OverrideActionState,
  formData: FormData
): Promise<OverrideActionState> {
  return mutateProjectOverride(formData, "DELETE");
}

async function mutateProjectOverride(
  formData: FormData,
  method: "POST" | "DELETE"
): Promise<OverrideActionState> {
  const projectId = textFormValue(formData, "projectId");
  const fieldName = textFormValue(formData, "fieldName");
  if (!projectId || !fieldName) {
    return { ok: false, message: "Missing project or field." };
  }

  try {
    assertPreviewWritesAllowed();
    const accessToken = await accessTokenForApi();
    const apiBaseUrl = requireApiBaseUrl();
    const endpoint =
      method === "POST"
        ? `${apiBaseUrl}/projects/${projectId}/override`
        : `${apiBaseUrl}/projects/${projectId}/override/${encodeURIComponent(fieldName)}`;
    const response = await fetch(endpoint, {
      method,
      headers: {
        Authorization: `Bearer ${accessToken}`,
        ...(method === "POST" ? { "Content-Type": "application/json" } : {})
      },
      body:
        method === "POST"
          ? JSON.stringify({
              field_name: fieldName,
              value: textFormValue(formData, "value"),
              note: textFormValue(formData, "note"),
              source_url: textFormValue(formData, "sourceUrl")
            })
          : undefined
    });

    if (!response.ok) {
      return {
        ok: false,
        message: await responseErrorMessage(response)
      };
    }
  } catch (error) {
    return {
      ...initialErrorState,
      message: error instanceof Error ? error.message : "Override update failed."
    };
  }

  revalidatePath(`/pipeline/${projectId}`);
  return { ok: true, message: method === "POST" ? "Saved." : "Cleared." };
}

function assertPreviewWritesAllowed() {
  if (process.env.VERCEL_ENV === "preview" && !previewWritesEnabled()) {
    throw new Error("Preview writes are disabled.");
  }
}

async function accessTokenForApi() {
  const supabase = await createSupabaseServerClient();
  const {
    data: { user }
  } = await supabase.auth.getUser();
  if (!user || !isEmailAllowed(user.email)) {
    throw new Error("Not authorized.");
  }

  const {
    data: { session }
  } = await supabase.auth.getSession();
  if (!session?.access_token) {
    throw new Error("No Supabase access token available.");
  }

  return session.access_token;
}

async function responseErrorMessage(response: Response) {
  try {
    const payload = await response.json();
    const detail = payload?.detail;
    if (typeof detail === "string") {
      return detail;
    }
    if (typeof detail?.message === "string") {
      return detail.message;
    }
  } catch {
    // Fall through to generic status text.
  }

  return response.statusText || "Override update failed.";
}

function textFormValue(formData: FormData, key: string) {
  const value = formData.get(key);
  if (typeof value !== "string") {
    return null;
  }
  const trimmed = value.trim();
  return trimmed || null;
}

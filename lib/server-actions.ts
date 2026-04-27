import "server-only";

import { isEmailAllowed } from "@/lib/auth";
import { previewWritesEnabled, requireApiBaseUrl } from "@/lib/env";
import { createSupabaseServerClient } from "@/lib/supabase/server";

export async function apiBaseUrlForWrite() {
  assertWriteFlowAllowed();
  return requireApiBaseUrl();
}

export async function jsonHeadersForApi() {
  const accessToken = await accessTokenForApi();
  return {
    Authorization: `Bearer ${accessToken}`,
    "Content-Type": "application/json"
  };
}

export function assertWriteFlowAllowed() {
  if (process.env.VERCEL_ENV === "preview" && !previewWritesEnabled()) {
    throw new Error("Preview writes are disabled.");
  }
}

export async function accessTokenForApi() {
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

export async function responseErrorMessage(response: Response, fallback = "Request failed.") {
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

  return response.statusText || fallback;
}

export function textFormValue(formData: FormData, key: string) {
  const value = formData.get(key);
  if (typeof value !== "string") {
    return null;
  }
  const trimmed = value.trim();
  return trimmed || null;
}

export function escapeIlikeTerm(value: string) {
  return value.replace(/[\\%_]/g, "\\$&");
}

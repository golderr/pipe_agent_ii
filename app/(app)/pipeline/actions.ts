"use server";

import { revalidatePath } from "next/cache";
import { isEmailAllowed } from "@/lib/auth";
import { previewWritesEnabled, requireApiBaseUrl } from "@/lib/env";
import { createSupabaseServerClient } from "@/lib/supabase/server";

export type ProjectCreateCandidate = {
  projectId: string;
  projectName: string;
  canonicalAddress: string;
  pipelineStatus: string;
  matchType: string;
  confidence: number | null;
};

export type ProjectCreateFormValues = {
  canonicalAddress: string;
  marketId: string;
  jurisdictionId: string;
  projectName: string;
  zip: string;
};

export type ProjectCreateActionState = {
  ok: boolean;
  message: string | null;
  created: boolean;
  projectId: string | null;
  canonicalAddress: string;
  duplicateCandidates: ProjectCreateCandidate[];
  form: ProjectCreateFormValues;
};

type ProjectCreateApiResponse = {
  created: boolean;
  project_id: string | null;
  canonical_address: string;
  duplicate_candidates: Array<{
    project_id: string;
    project_name: string;
    canonical_address: string;
    pipeline_status: string;
    match_type: string;
    confidence: number | null;
  }>;
};

export const initialProjectCreateState: ProjectCreateActionState = {
  ok: false,
  message: null,
  created: false,
  projectId: null,
  canonicalAddress: "",
  duplicateCandidates: [],
  form: {
    canonicalAddress: "",
    marketId: "",
    jurisdictionId: "",
    projectName: "",
    zip: ""
  }
};

export async function createProjectAction(
  _previousState: ProjectCreateActionState,
  formData: FormData
): Promise<ProjectCreateActionState> {
  const form = projectCreateFormValues(formData);
  if (!form.canonicalAddress || !form.marketId || !form.jurisdictionId) {
    return {
      ...initialProjectCreateState,
      ok: false,
      message: "Address, market, and jurisdiction are required.",
      form
    };
  }

  try {
    const apiBaseUrl = await apiBaseUrlForWrite();
    const response = await fetch(`${apiBaseUrl}/projects`, {
      method: "POST",
      headers: await jsonHeadersForApi(),
      body: JSON.stringify({
        canonical_address: form.canonicalAddress,
        market_id: form.marketId,
        jurisdiction_id: form.jurisdictionId,
        project_name: form.projectName || null,
        zip: form.zip || null,
        force_create: formData.get("forceCreate") === "true"
      })
    });

    if (!response.ok) {
      return {
        ...initialProjectCreateState,
        ok: false,
        message: await responseErrorMessage(response),
        form
      };
    }

    const body = (await response.json()) as ProjectCreateApiResponse;
    const duplicateCandidates = body.duplicate_candidates.map((candidate) => ({
      projectId: candidate.project_id,
      projectName: candidate.project_name,
      canonicalAddress: candidate.canonical_address,
      pipelineStatus: candidate.pipeline_status,
      matchType: candidate.match_type,
      confidence: candidate.confidence
    }));
    if (!body.created) {
      return {
        ok: true,
        message: "Possible duplicate found.",
        created: false,
        projectId: null,
        canonicalAddress: body.canonical_address,
        duplicateCandidates,
        form
      };
    }

    revalidatePath("/pipeline");
    return {
      ok: true,
      message: "Project created.",
      created: true,
      projectId: body.project_id,
      canonicalAddress: body.canonical_address,
      duplicateCandidates,
      form
    };
  } catch (error) {
    return {
      ...initialProjectCreateState,
      ok: false,
      message: error instanceof Error ? error.message : "Project creation failed.",
      form
    };
  }
}

async function apiBaseUrlForWrite() {
  assertWriteFlowAllowed();
  return requireApiBaseUrl();
}

async function jsonHeadersForApi() {
  const accessToken = await accessTokenForApi();
  return {
    Authorization: `Bearer ${accessToken}`,
    "Content-Type": "application/json"
  };
}

function assertWriteFlowAllowed() {
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

  return response.statusText || "Project creation failed.";
}

function projectCreateFormValues(formData: FormData): ProjectCreateFormValues {
  return {
    canonicalAddress: textFormValue(formData, "canonicalAddress") ?? "",
    marketId: textFormValue(formData, "marketId") ?? "",
    jurisdictionId: textFormValue(formData, "jurisdictionId") ?? "",
    projectName: textFormValue(formData, "projectName") ?? "",
    zip: textFormValue(formData, "zip") ?? ""
  };
}

function textFormValue(formData: FormData, key: string) {
  const value = formData.get(key);
  if (typeof value !== "string") {
    return null;
  }
  const trimmed = value.trim();
  return trimmed || null;
}

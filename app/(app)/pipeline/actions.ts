"use server";

import { revalidatePath } from "next/cache";
import {
  apiBaseUrlForWrite,
  jsonHeadersForApi,
  responseErrorMessage,
  textFormValue
} from "@/lib/server-actions";

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
  city: string;
  county: string;
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
  geocoding?: {
    status: string;
    provider: string | null;
    confidence: string;
    message: string | null;
  } | null;
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
    city: "",
    county: "",
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
        city: form.city || null,
        county: form.county || null,
        zip: form.zip || null,
        force_create: formData.get("forceCreate") === "true"
      })
    });

    if (!response.ok) {
      return {
        ...initialProjectCreateState,
        ok: false,
        message: await responseErrorMessage(response, "Project creation failed."),
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
      message: projectCreatedMessage(body.geocoding),
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

function projectCreatedMessage(geocoding: ProjectCreateApiResponse["geocoding"]) {
  if (!geocoding || geocoding.status === "accepted") {
    return "Project created.";
  }
  if (geocoding.status === "skipped") {
    return "Project created. Geocoding is not configured for this environment.";
  }
  return "Project created. Geocoding did not return reliable coordinates.";
}

function projectCreateFormValues(formData: FormData): ProjectCreateFormValues {
  return {
    canonicalAddress: textFormValue(formData, "canonicalAddress") ?? "",
    marketId: textFormValue(formData, "marketId") ?? "",
    jurisdictionId: textFormValue(formData, "jurisdictionId") ?? "",
    projectName: textFormValue(formData, "projectName") ?? "",
    city: textFormValue(formData, "city") ?? "",
    county: textFormValue(formData, "county") ?? "",
    zip: textFormValue(formData, "zip") ?? ""
  };
}

"use server";

import { revalidatePath } from "next/cache";
import {
  accessTokenForApi,
  apiBaseUrlForWrite,
  assertWriteFlowAllowed,
  escapeIlikeTerm,
  jsonHeadersForApi,
  responseErrorMessage,
  textFormValue
} from "@/lib/server-actions";
import { createSupabaseServerClient } from "@/lib/supabase/server";

export type ProjectMutationActionState = {
  ok: boolean;
  message: string | null;
  changed?: boolean;
};

export type RelationshipSearchCandidate = {
  id: string;
  name: string;
  canonicalAddress: string;
  location: string;
  status: string;
};

export type RelationshipSearchActionState = ProjectMutationActionState & {
  query: string;
  relationshipType: string;
  candidates: RelationshipSearchCandidate[];
};

type ProjectRelationshipMutationApiResponse = {
  created?: boolean;
  updated?: boolean;
};

const initialErrorState: ProjectMutationActionState = {
  ok: false,
  message: null
};

export async function setProjectOverrideAction(
  _previousState: ProjectMutationActionState,
  formData: FormData
): Promise<ProjectMutationActionState> {
  return mutateProjectOverride(formData, "POST");
}

export async function clearProjectOverrideAction(
  _previousState: ProjectMutationActionState,
  formData: FormData
): Promise<ProjectMutationActionState> {
  return mutateProjectOverride(formData, "DELETE");
}

export async function setProjectFieldAction(
  _previousState: ProjectMutationActionState,
  formData: FormData
): Promise<ProjectMutationActionState> {
  return mutateProjectField(formData);
}

export async function addProjectNoteAction(
  _previousState: ProjectMutationActionState,
  formData: FormData
): Promise<ProjectMutationActionState> {
  return mutateProjectNote(formData);
}

export async function searchRelationshipCandidatesAction(
  _previousState: RelationshipSearchActionState,
  formData: FormData
): Promise<RelationshipSearchActionState> {
  const projectId = textFormValue(formData, "projectId");
  const query = textFormValue(formData, "query") ?? "";
  const relationshipType = textFormValue(formData, "relationshipType") ?? "phase";
  if (!projectId) {
    return relationshipSearchState({
      ok: false,
      message: "Missing project.",
      query,
      relationshipType
    });
  }
  if (query.length < 2) {
    return relationshipSearchState({
      ok: false,
      message: "Enter at least 2 characters.",
      query,
      relationshipType
    });
  }

  try {
    // Search is read-only, but it feeds the relationship write flow.
    assertWriteFlowAllowed();
    await accessTokenForApi();
    const supabase = await createSupabaseServerClient();
    const select = "id, project_name, canonical_address, city, state, zip, pipeline_status";
    const escapedQuery = escapeIlikeTerm(query);
    const addressQuery = supabase
      .from("projects")
      .select(select)
      .neq("id", projectId)
      .ilike("canonical_address", `%${escapedQuery}%`)
      .limit(8);
    const nameQuery = supabase
      .from("projects")
      .select(select)
      .neq("id", projectId)
      .ilike("project_name", `%${escapedQuery}%`)
      .limit(8);
    const [addressResults, nameResults] = await Promise.all([addressQuery, nameQuery]);
    const error = addressResults.error ?? nameResults.error;
    if (error) {
      return relationshipSearchState({
        ok: false,
        message: error.message,
        query,
        relationshipType
      });
    }
    const byId = new Map<string, RelationshipSearchCandidate>();
    for (const row of [...(addressResults.data ?? []), ...(nameResults.data ?? [])]) {
      byId.set(row.id, {
        id: row.id,
        name: row.project_name ?? row.canonical_address,
        canonicalAddress: row.canonical_address,
        location: [row.city, row.state, row.zip].filter(Boolean).join(", "),
        status: row.pipeline_status
      });
    }
    return relationshipSearchState({
      ok: true,
      message: byId.size ? null : "No matching projects.",
      query,
      relationshipType,
      candidates: [...byId.values()].slice(0, 8)
    });
  } catch (error) {
    return relationshipSearchState({
      ok: false,
      message: error instanceof Error ? error.message : "Project search failed.",
      query,
      relationshipType
    });
  }
}

export async function addProjectRelationshipAction(
  _previousState: ProjectMutationActionState,
  formData: FormData
): Promise<ProjectMutationActionState> {
  const projectId = textFormValue(formData, "projectId");
  const relatedProjectId = textFormValue(formData, "relatedProjectId");
  const relationshipType = textFormValue(formData, "relationshipType");
  if (!projectId || !relatedProjectId || !relationshipType) {
    return { ok: false, message: "Missing relationship target." };
  }

  try {
    const apiBaseUrl = await apiBaseUrlForWrite();
    const response = await fetch(`${apiBaseUrl}/projects/${projectId}/relationship`, {
      method: "POST",
      headers: await jsonHeadersForApi(),
      body: JSON.stringify({
        relationship_type: relationshipType,
        related_project_id: relatedProjectId,
        notes: textFormValue(formData, "notes")
      })
    });

    if (!response.ok) {
      return { ok: false, message: await responseErrorMessage(response) };
    }

    const body = (await response.json().catch(() => null)) as
      | ProjectRelationshipMutationApiResponse
      | null;
    const changed = !body || body.created === true || body.updated === true;
    if (changed) {
      revalidatePath(`/pipeline/${projectId}`);
    }
    if (body?.updated) {
      return { ok: true, message: "Relationship note updated.", changed: true };
    }
    if (body?.created === false) {
      return { ok: true, message: "Already linked.", changed: false };
    }
    return { ok: true, message: "Linked.", changed: true };
  } catch (error) {
    return {
      ...initialErrorState,
      message: error instanceof Error ? error.message : "Relationship link failed."
    };
  }
}

async function mutateProjectOverride(
  formData: FormData,
  method: "POST" | "DELETE"
): Promise<ProjectMutationActionState> {
  const projectId = textFormValue(formData, "projectId");
  const fieldName = textFormValue(formData, "fieldName");
  if (!projectId || !fieldName) {
    return { ok: false, message: "Missing project or field." };
  }

  try {
    const apiBaseUrl = await apiBaseUrlForWrite();
    const accessToken = await accessTokenForApi();
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
        message: await responseErrorMessage(response, "Override update failed.")
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

async function mutateProjectField(formData: FormData): Promise<ProjectMutationActionState> {
  const projectId = textFormValue(formData, "projectId");
  const fieldName = textFormValue(formData, "fieldName");
  const value = textFormValue(formData, "value") ?? "";
  if (!projectId || !fieldName) {
    return { ok: false, message: "Missing project or field." };
  }

  try {
    const apiBaseUrl = await apiBaseUrlForWrite();
    const response = await fetch(`${apiBaseUrl}/projects/${projectId}/field`, {
      method: "POST",
      headers: await jsonHeadersForApi(),
      body: JSON.stringify({
        field_name: fieldName,
        value
      })
    });

    if (!response.ok) {
      return { ok: false, message: await responseErrorMessage(response, "Field update failed.") };
    }
  } catch (error) {
    return {
      ...initialErrorState,
      message: error instanceof Error ? error.message : "Field update failed."
    };
  }

  revalidatePath(`/pipeline/${projectId}`);
  return { ok: true, message: "Saved." };
}

async function mutateProjectNote(formData: FormData): Promise<ProjectMutationActionState> {
  const projectId = textFormValue(formData, "projectId");
  const fieldName = textFormValue(formData, "fieldName");
  const value = textFormValue(formData, "value");
  if (!projectId || !fieldName || !value) {
    return { ok: false, message: "Missing project, note type, or note text." };
  }

  try {
    const apiBaseUrl = await apiBaseUrlForWrite();
    const response = await fetch(`${apiBaseUrl}/projects/${projectId}/note`, {
      method: "POST",
      headers: await jsonHeadersForApi(),
      body: JSON.stringify({
        note_type: fieldName,
        body: value
      })
    });

    if (!response.ok) {
      return { ok: false, message: await responseErrorMessage(response, "Note append failed.") };
    }
  } catch (error) {
    return {
      ...initialErrorState,
      message: error instanceof Error ? error.message : "Note append failed."
    };
  }

  revalidatePath(`/pipeline/${projectId}`);
  return { ok: true, message: "Added." };
}

function relationshipSearchState(
  overrides: Partial<RelationshipSearchActionState> = {}
): RelationshipSearchActionState {
  return {
    ok: false,
    message: null,
    query: "",
    relationshipType: "phase",
    candidates: [],
    ...overrides
  };
}

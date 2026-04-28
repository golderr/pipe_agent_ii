"use client";

import { useActionState, useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Check, Link2, Save, Search, Trash2 } from "lucide-react";
import {
  addProjectRelationshipAction,
  deleteProjectRelationshipAction,
  searchRelationshipCandidatesAction,
  updateProjectRelationshipAction,
  type ProjectMutationActionState,
  type RelationshipSearchActionState
} from "./actions";
import type { ProjectRelationshipRow } from "@/lib/project-detail/types";
import { cn } from "@/lib/utils";

const initialSearchState: RelationshipSearchActionState = {
  ok: false,
  message: null,
  query: "",
  relationshipType: "phase",
  candidates: []
};

const initialMutationState: ProjectMutationActionState = {
  ok: false,
  message: null
};

const RELATIONSHIP_OPTIONS = [
  { value: "phase", label: "Phase sibling" },
  { value: "master_plan", label: "Master project" },
  { value: "counterpart", label: "Counterpart" },
  { value: "duplicate", label: "Duplicate" },
  { value: "supersedes", label: "Supersedes" }
];

export function RelationshipPicker({
  projectId,
  relationships
}: {
  projectId: string;
  relationships: ProjectRelationshipRow[];
}) {
  const router = useRouter();
  const [searchState, searchAction, searchPending] = useActionState(
    searchRelationshipCandidatesAction,
    initialSearchState
  );
  const [linkState, linkAction, linkPending] = useActionState(
    addProjectRelationshipAction,
    initialMutationState
  );
  const linkedCandidateIds = new Set(
    relationships
      .filter(
        (relationship) =>
          relationship.direction === "outgoing" &&
          relationship.relationshipType === searchState.relationshipType
      )
      .map((relationship) => relationship.relatedProjectId)
  );

  useEffect(() => {
    if (linkState.ok && linkState.changed !== false) {
      router.refresh();
    }
  }, [linkState.changed, linkState.ok, router]);

  return (
    <div className="space-y-3 border-t border-slate-100 px-4 py-3">
      <div>
        <p className="text-xs font-semibold uppercase tracking-normal text-slate-500">
          Linked projects
        </p>
        {relationships.length ? (
          <div className="mt-2 grid gap-2">
            {relationships.map((relationship) => (
              <RelationshipRow
                projectId={projectId}
                relationship={relationship}
                key={relationship.id}
              />
            ))}
          </div>
        ) : (
          <p className="mt-2 text-sm text-slate-500">No project relationships linked yet.</p>
        )}
      </div>

      <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
        <form action={searchAction} className="grid gap-2 md:grid-cols-[10rem_minmax(0,1fr)_auto]">
          <input name="projectId" type="hidden" value={projectId} />
          <select
            className="h-9 rounded-md border border-slate-200 bg-white px-2 text-sm text-slate-900 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-100"
            defaultValue={searchState.relationshipType}
            name="relationshipType"
          >
            {RELATIONSHIP_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <input
            className="h-9 rounded-md border border-slate-200 bg-white px-2 text-sm text-slate-900 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-100"
            defaultValue={searchState.query}
            name="query"
            placeholder="Search name or address"
            type="search"
          />
          <button
            className="inline-flex h-9 items-center justify-center gap-1.5 rounded-md bg-teal-700 px-3 text-sm font-medium text-white hover:bg-teal-800 disabled:opacity-60"
            disabled={searchPending}
            type="submit"
          >
            <Search className="size-3.5" aria-hidden="true" />
            Search
          </button>
        </form>

        <ActionMessage state={searchState} />
        <ActionMessage state={linkState} />

        {searchState.candidates.length ? (
          <div className="mt-3 grid gap-2">
            {searchState.candidates.map((candidate) => {
              const isLinked = linkedCandidateIds.has(candidate.id);
              return (
                <form
                  action={linkAction}
                  className="grid gap-2 rounded-md border border-slate-200 bg-white p-2 md:grid-cols-[minmax(0,1fr)_12rem_auto]"
                  key={candidate.id}
                >
                  <input name="projectId" type="hidden" value={projectId} />
                  <input name="relatedProjectId" type="hidden" value={candidate.id} />
                  <input
                    name="relationshipType"
                    type="hidden"
                    value={searchState.relationshipType}
                  />
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <p className="truncate text-sm font-medium text-slate-950">
                        {candidate.name}
                      </p>
                      {isLinked ? (
                        <span className="shrink-0 rounded border border-green-200 bg-green-50 px-1.5 py-0.5 text-[11px] text-green-800">
                          Already linked
                        </span>
                      ) : null}
                    </div>
                    <p className="truncate text-xs text-slate-500">
                      {candidate.canonicalAddress}
                    </p>
                    <p className="text-xs text-slate-500">
                      {[candidate.location, candidate.status].filter(Boolean).join(" | ")}
                    </p>
                  </div>
                  <input
                    className="h-8 rounded-md border border-slate-200 px-2 text-xs text-slate-900 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-100"
                    name="notes"
                    placeholder={isLinked ? "Update note" : "Optional note"}
                  />
                  <button
                    className="inline-flex h-8 items-center justify-center gap-1 rounded-md border border-slate-200 px-2 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-60"
                    disabled={linkPending}
                    type="submit"
                  >
                    <Check className="size-3.5" aria-hidden="true" />
                    {isLinked ? "Update note" : "Link"}
                  </button>
                </form>
              );
            })}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function RelationshipRow({
  projectId,
  relationship
}: {
  projectId: string;
  relationship: ProjectRelationshipRow;
}) {
  const router = useRouter();
  const [updateState, updateAction, updatePending] = useActionState(
    updateProjectRelationshipAction,
    initialMutationState
  );
  const [deleteState, deleteAction, deletePending] = useActionState(
    deleteProjectRelationshipAction,
    initialMutationState
  );
  const canEdit = relationship.direction === "outgoing";

  useEffect(() => {
    if (
      (updateState.ok && updateState.changed !== false) ||
      (deleteState.ok && deleteState.changed !== false)
    ) {
      router.refresh();
    }
  }, [deleteState.changed, deleteState.ok, router, updateState.changed, updateState.ok]);

  return (
    <div className="grid gap-2 rounded-md border border-slate-200 bg-white p-2 text-sm md:grid-cols-[8rem_minmax(0,1fr)_auto]">
      <div className="flex flex-wrap items-start gap-1.5 md:block">
        <span className="w-fit rounded border border-indigo-200 bg-indigo-50 px-1.5 py-0.5 text-[11px] text-indigo-800">
          {relationshipLabel(relationship.relationshipType)}
        </span>
        <span
          className={cn(
            "h-fit rounded border px-1.5 py-0.5 text-[11px] md:mt-1 md:block md:w-fit",
            relationship.direction === "outgoing"
              ? "border-slate-200 bg-slate-50 text-slate-700"
              : "border-blue-200 bg-blue-50 text-blue-800"
          )}
        >
          {relationship.direction}
        </span>
      </div>
      <div className="min-w-0">
        <Link
          className="inline-flex max-w-full items-center gap-1 font-medium text-slate-950 hover:text-teal-800"
          href={`/pipeline/${relationship.relatedProjectId}`}
        >
          <Link2 className="size-3.5 shrink-0" aria-hidden="true" />
          <span className="truncate">{relationship.relatedProjectName}</span>
        </Link>
        <p className="truncate text-xs text-slate-500">{relationship.relatedProjectAddress}</p>
        <p className="text-xs text-slate-500">
          {[relationship.relatedProjectLocation, relationship.relatedProjectStatus]
            .filter(Boolean)
            .join(" | ")}
        </p>
        {relationship.notes ? (
          <p className="mt-1 text-xs text-slate-600">{relationship.notes}</p>
        ) : null}
      </div>
      {canEdit ? (
        <div className="grid gap-2 md:col-span-3">
          <form
            action={updateAction}
            className="grid gap-2 rounded-md bg-slate-50 p-2 md:grid-cols-[10rem_minmax(0,1fr)_auto]"
          >
            <input name="projectId" type="hidden" value={projectId} />
            <input name="relationshipId" type="hidden" value={relationship.id} />
            <select
              className="h-8 rounded-md border border-slate-200 bg-white px-2 text-xs text-slate-900 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-100"
              defaultValue={relationship.relationshipType}
              name="relationshipType"
            >
              {RELATIONSHIP_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <input
              className="h-8 rounded-md border border-slate-200 bg-white px-2 text-xs text-slate-900 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-100"
              defaultValue={relationship.notes ?? ""}
              name="notes"
              placeholder="Optional note"
            />
            <button
              className="inline-flex h-8 items-center justify-center gap-1 rounded-md border border-slate-200 bg-white px-2 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-60"
              disabled={updatePending}
              type="submit"
            >
              <Save className="size-3.5" aria-hidden="true" />
              Save
            </button>
          </form>
          <div className="flex flex-wrap items-center gap-2">
            {relationship.notes ? (
              <form action={updateAction}>
                <input name="projectId" type="hidden" value={projectId} />
                <input name="relationshipId" type="hidden" value={relationship.id} />
                <input name="notes" type="hidden" value="" />
                <button
                  className="inline-flex h-8 items-center justify-center rounded-md border border-slate-200 px-2 text-xs font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-60"
                  disabled={updatePending}
                  type="submit"
                >
                  Clear note
                </button>
              </form>
            ) : null}
            <form action={deleteAction}>
              <input name="projectId" type="hidden" value={projectId} />
              <input name="relationshipId" type="hidden" value={relationship.id} />
              <button
                className="inline-flex h-8 items-center justify-center gap-1 rounded-md border border-red-200 px-2 text-xs font-medium text-red-700 hover:bg-red-50 disabled:opacity-60"
                disabled={deletePending}
                onClick={(event) => {
                  if (!window.confirm("Unlink this project relationship?")) {
                    event.preventDefault();
                  }
                }}
                type="submit"
              >
                <Trash2 className="size-3.5" aria-hidden="true" />
                Unlink
              </button>
            </form>
          </div>
          <ActionMessage state={updateState} />
          <ActionMessage state={deleteState} />
        </div>
      ) : null}
    </div>
  );
}

function ActionMessage({ state }: { state: ProjectMutationActionState }) {
  if (!state.message) {
    return null;
  }

  return (
    <p
      className={cn(
        "mt-2 rounded border px-2 py-1 text-[11px]",
        state.ok
          ? "border-green-200 bg-green-50 text-green-800"
          : "border-red-200 bg-red-50 text-red-800"
      )}
    >
      {state.message}
    </p>
  );
}

function relationshipLabel(value: string) {
  return (
    RELATIONSHIP_OPTIONS.find((option) => option.value === value)?.label ??
    value.replace(/_/g, " ")
  );
}

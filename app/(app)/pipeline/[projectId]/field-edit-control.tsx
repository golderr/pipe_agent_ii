"use client";

import { useActionState, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Check, Info, Pencil, RotateCcw, X } from "lucide-react";
import {
  addProjectNoteAction,
  clearProjectOverrideAction,
  setProjectFieldAction,
  setProjectOverrideAction,
  type ProjectMutationActionState
} from "./actions";
import type { FieldEditConfig, ProjectField } from "@/lib/project-detail/types";
import { cn } from "@/lib/utils";

const initialState: ProjectMutationActionState = {
  ok: false,
  message: null
};

export function FieldEditControl({
  field,
  projectId
}: {
  field: ProjectField;
  projectId: string;
}) {
  const edit = field.edit;
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [setState, setAction, setPending] = useActionState(
    setProjectOverrideAction,
    initialState
  );
  const [clearState, clearAction, clearPending] = useActionState(
    clearProjectOverrideAction,
    initialState
  );
  const [fieldState, fieldAction, fieldPending] = useActionState(
    setProjectFieldAction,
    initialState
  );
  const [noteState, noteAction, notePending] = useActionState(
    addProjectNoteAction,
    initialState
  );
  const saveAction =
    edit?.mutation === "field"
      ? fieldAction
      : edit?.mutation === "note"
        ? noteAction
        : setAction;
  const pending = setPending || clearPending || fieldPending || notePending;

  useEffect(() => {
    if (setState.ok || clearState.ok || fieldState.ok || noteState.ok) {
      router.refresh();
    }
  }, [clearState.ok, fieldState.ok, noteState.ok, router, setState.ok]);

  if (!edit?.enabled) {
    return null;
  }

  return (
    <div className="relative">
      <button
        aria-expanded={open}
        aria-label={`Edit ${field.label}`}
        className="inline-flex size-7 items-center justify-center rounded-md border border-slate-200 bg-white text-slate-500 hover:border-slate-300 hover:bg-slate-50 hover:text-slate-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700"
        onClick={() => setOpen((value) => !value)}
        title={`Edit ${field.label}`}
        type="button"
      >
        <Pencil className="size-3.5" aria-hidden="true" />
      </button>

      {open ? (
        <div className="absolute right-0 top-9 z-40 w-[min(24rem,calc(100vw-2rem))] rounded-md border border-slate-200 bg-white p-3 text-xs shadow-xl">
          <div className="mb-3 flex items-start justify-between gap-3">
            <div>
              <p className="font-semibold text-slate-950">{field.label}</p>
              <p className="mt-0.5 text-slate-500">Current: {field.value}</p>
            </div>
            <button
              aria-label="Close editor"
              className="inline-flex size-6 items-center justify-center rounded text-slate-500 hover:bg-slate-100 hover:text-slate-900"
              onClick={() => setOpen(false)}
              type="button"
            >
              <X className="size-3.5" aria-hidden="true" />
            </button>
          </div>

          <form action={saveAction} className="space-y-2">
            <HiddenFields fieldName={field.key} projectId={projectId} />
            <EditValueInput field={field} />
            {edit.mutation === "override" ? <OverrideMetadataFields /> : null}
            <div className="flex items-center justify-between gap-2 pt-1">
              <span
                className="inline-flex items-center gap-1 text-[11px] text-slate-500"
                title={edit.info}
              >
                <Info className="size-3" aria-hidden="true" />
                {mutationLabel(edit.mutation)}
              </span>
              <div className="flex items-center gap-1.5">
                {edit.mutation === "override" && edit.isOverridden ? (
                  <button
                    formAction={clearAction}
                    className="inline-flex h-8 items-center gap-1 rounded-md border border-slate-200 px-2 text-xs font-medium text-slate-600 hover:bg-slate-50"
                    disabled={pending}
                    type="submit"
                  >
                    <RotateCcw className="size-3.5" aria-hidden="true" />
                    Clear
                  </button>
                ) : null}
                <button
                  className="inline-flex h-8 items-center gap-1 rounded-md bg-teal-700 px-2 text-xs font-medium text-white hover:bg-teal-800 disabled:opacity-60"
                  disabled={pending}
                  type="submit"
                >
                  <Check className="size-3.5" aria-hidden="true" />
                  Save
                </button>
              </div>
            </div>
          </form>

          <ActionMessage state={setState} />
          <ActionMessage state={clearState} />
          <ActionMessage state={fieldState} />
          <ActionMessage state={noteState} />
        </div>
      ) : null}
    </div>
  );
}

function HiddenFields({
  fieldName,
  projectId
}: {
  fieldName: string;
  projectId: string;
}) {
  return (
    <>
      <input name="projectId" type="hidden" value={projectId} />
      <input name="fieldName" type="hidden" value={fieldName} />
    </>
  );
}

function EditValueInput({ field }: { field: ProjectField }) {
  const edit = field.edit;
  if (!edit) {
    return null;
  }
  const label = edit.mutation === "note" ? "New note" : "Value";

  if (edit.kind === "select") {
    return (
      <label className="block">
        <span className="font-medium text-slate-600">{label}</span>
        <select
          className="mt-1 h-9 w-full rounded-md border border-slate-200 bg-white px-2 text-sm text-slate-900 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-100"
          defaultValue={edit.value ?? ""}
          name="value"
          required
        >
          {edit.value ? null : (
            <option disabled value="">
              Select value
            </option>
          )}
          {(edit.options ?? []).map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      </label>
    );
  }

  if (edit.kind === "textarea") {
    return (
      <label className="block">
        <span className="font-medium text-slate-600">{label}</span>
        <textarea
          className="mt-1 min-h-24 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm text-slate-900 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-100"
          defaultValue={edit.mutation === "note" ? "" : edit.value ?? ""}
          maxLength={edit.mutation === "note" ? 10000 : undefined}
          name="value"
          required={edit.mutation === "note"}
        />
      </label>
    );
  }

  return (
    <label className="block">
      <span className="font-medium text-slate-600">{label}</span>
      <input
        className="mt-1 h-9 w-full rounded-md border border-slate-200 px-2 text-sm text-slate-900 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-100"
        defaultValue={edit.value ?? ""}
        min={edit.kind === "number" ? 0 : undefined}
        name="value"
        required={edit.mutation === "override"}
        step={edit.kind === "number" ? 1 : undefined}
        type={edit.kind}
      />
    </label>
  );
}

function OverrideMetadataFields() {
  return (
    <>
      <label className="block">
        <span className="font-medium text-slate-600">Note</span>
        <textarea
          className="mt-1 min-h-16 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm text-slate-900 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-100"
          defaultValue=""
          name="note"
        />
      </label>
      <label className="block">
        <span className="font-medium text-slate-600">Source URL</span>
        <input
          className="mt-1 h-9 w-full rounded-md border border-slate-200 px-2 text-sm text-slate-900 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-100"
          name="sourceUrl"
          type="url"
        />
      </label>
    </>
  );
}

function mutationLabel(mutation: FieldEditConfig["mutation"]) {
  if (mutation === "override") {
    return "Override";
  }
  if (mutation === "note") {
    return "Append note";
  }
  return "Direct edit";
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

"use client";

import type { ReactNode } from "react";
import { useActionState, useEffect } from "react";
import { Check, FileBarChart2, GalleryHorizontalEnd } from "lucide-react";
import { useRouter } from "next/navigation";
import { setProjectFieldAction, type ProjectMutationActionState } from "./actions";
import type { ProjectDetailData } from "@/lib/project-detail/types";
import { cn } from "@/lib/utils";

type InclusionFlags = ProjectDetailData["project"]["inclusion"];

const initialState: ProjectMutationActionState = {
  ok: false,
  message: null
};

export function InclusionFlagsControl({
  inclusion,
  projectId
}: {
  inclusion: InclusionFlags;
  projectId: string;
}) {
  const router = useRouter();
  const [state, action, pending] = useActionState(setProjectFieldAction, initialState);

  useEffect(() => {
    if (state.ok) {
      router.refresh();
    }
  }, [router, state.ok]);

  return (
    <section className="rounded-md border border-slate-200 bg-white p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-950">Inclusion</h2>
          <p className="mt-0.5 text-xs text-slate-500">Analysis and exhibit scope</p>
        </div>
        <span className="rounded border border-blue-200 bg-blue-50 px-1.5 py-0.5 text-[11px] font-medium text-blue-800">
          TCG
        </span>
      </div>

      <div className="mt-4 grid gap-2">
        <ToggleForm
          action={action}
          enabled={inclusion.inAnalysis}
          fieldName="inclusion_in_analysis"
          icon={<FileBarChart2 className="size-4" aria-hidden="true" />}
          label="Analysis"
          pending={pending}
          projectId={projectId}
        />
        <ToggleForm
          action={action}
          enabled={inclusion.inExhibit}
          fieldName="inclusion_in_exhibit"
          icon={<GalleryHorizontalEnd className="size-4" aria-hidden="true" />}
          label="Exhibit"
          pending={pending}
          projectId={projectId}
        />
      </div>

      <form action={action} className="mt-4 space-y-2">
        <input name="projectId" type="hidden" value={projectId} />
        <input name="fieldName" type="hidden" value="inclusion_note" />
        <label className="block">
          <span className="text-xs font-medium text-slate-600">Note</span>
          <textarea
            className="mt-1 min-h-24 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm text-slate-900 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-100"
            defaultValue={inclusion.note ?? ""}
            key={inclusion.note ?? "empty"}
            maxLength={255}
            name="value"
          />
        </label>
        <div className="flex items-center justify-between gap-2">
          <span className="text-[11px] text-slate-500">255 characters max</span>
          <button
            className="inline-flex h-8 items-center gap-1 rounded-md bg-teal-700 px-2 text-xs font-medium text-white hover:bg-teal-800 disabled:opacity-60"
            disabled={pending}
            type="submit"
          >
            <Check className="size-3.5" aria-hidden="true" />
            Save note
          </button>
        </div>
      </form>

      {state.message ? (
        <p
          className={cn(
            "mt-3 rounded border px-2 py-1 text-[11px]",
            state.ok
              ? "border-green-200 bg-green-50 text-green-800"
              : "border-red-200 bg-red-50 text-red-800"
          )}
        >
          {state.message}
        </p>
      ) : null}
    </section>
  );
}

function ToggleForm({
  action,
  enabled,
  fieldName,
  icon,
  label,
  pending,
  projectId
}: {
  action: (formData: FormData) => void;
  enabled: boolean;
  fieldName: "inclusion_in_analysis" | "inclusion_in_exhibit";
  icon: ReactNode;
  label: string;
  pending: boolean;
  projectId: string;
}) {
  return (
    <form action={action}>
      <input name="projectId" type="hidden" value={projectId} />
      <input name="fieldName" type="hidden" value={fieldName} />
      <input name="value" type="hidden" value={String(!enabled)} />
      <button
        aria-checked={enabled}
        className="flex w-full items-center justify-between gap-3 rounded-md border border-slate-200 px-3 py-2 text-left text-sm hover:border-slate-300 hover:bg-slate-50 disabled:opacity-60"
        disabled={pending}
        role="switch"
        type="submit"
      >
        <span className="flex min-w-0 items-center gap-2">
          <span className={cn("text-slate-500", enabled && "text-teal-700")}>{icon}</span>
          <span className="font-medium text-slate-800">{label}</span>
        </span>
        <span className="flex items-center gap-2">
          <span className="text-xs text-slate-500">{enabled ? "Included" : "Excluded"}</span>
          <span
            aria-hidden="true"
            className={cn(
              "relative h-5 w-9 rounded-full border transition-colors",
              enabled ? "border-teal-700 bg-teal-700" : "border-slate-300 bg-slate-200"
            )}
          >
            <span
              className={cn(
                "absolute top-0.5 size-4 rounded-full bg-white transition-transform",
                enabled ? "translate-x-4" : "translate-x-0.5"
              )}
            />
          </span>
        </span>
      </button>
    </form>
  );
}

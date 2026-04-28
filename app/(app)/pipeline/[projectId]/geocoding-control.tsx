"use client";

import { useActionState, useEffect } from "react";
import { MapPin, RefreshCw } from "lucide-react";
import { useRouter } from "next/navigation";
import { reGeocodeProjectAction, type ProjectMutationActionState } from "./actions";
import type { ProjectDetailData } from "@/lib/project-detail/types";
import { cn } from "@/lib/utils";

type Coordinates = ProjectDetailData["project"]["coordinates"];

const initialState: ProjectMutationActionState = {
  ok: false,
  message: null
};

export function GeocodingControl({
  coordinates,
  projectId
}: {
  coordinates: Coordinates;
  projectId: string;
}) {
  const router = useRouter();
  const [state, action, pending] = useActionState(reGeocodeProjectAction, initialState);
  const hasCoordinates = coordinates.lat !== null && coordinates.lng !== null;
  const needsRemediation = !hasCoordinates || coordinates.confidence !== "high";

  useEffect(() => {
    if (state.ok) {
      router.refresh();
    }
  }, [router, state.ok]);

  return (
    <section className="rounded-md border border-slate-200 bg-white p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-950">Map Location</h2>
          <p className="mt-0.5 text-xs text-slate-500">
            {hasCoordinates ? `${coordinates.lat}, ${coordinates.lng}` : "No coordinates"}
          </p>
        </div>
        <span
          className={cn(
            "rounded border px-1.5 py-0.5 text-[11px] font-medium",
            coordinates.confidence === "high"
              ? "border-green-200 bg-green-50 text-green-800"
              : coordinates.confidence === "medium"
                ? "border-amber-200 bg-amber-50 text-amber-900"
                : "border-slate-200 bg-slate-50 text-slate-700"
          )}
        >
          {coordinates.confidence ?? "none"}
        </span>
      </div>

      {needsRemediation ? (
        <form action={action} className="mt-4">
          <input name="projectId" type="hidden" value={projectId} />
          <button
            className="inline-flex h-8 w-full items-center justify-center gap-1.5 rounded-md bg-teal-700 px-2 text-xs font-medium text-white hover:bg-teal-800 disabled:opacity-60"
            disabled={pending}
            type="submit"
          >
            {pending ? (
              <RefreshCw className="size-3.5 animate-spin" aria-hidden="true" />
            ) : (
              <MapPin className="size-3.5" aria-hidden="true" />
            )}
            Re-geocode
          </button>
        </form>
      ) : null}

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

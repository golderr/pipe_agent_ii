"use client";

import {
  ArrowDown,
  ArrowUp,
  ChevronLeft,
  ChevronRight,
  Command as CommandIcon,
  ExternalLink,
  List,
  Map as MapIcon,
  Plus,
  Save,
  Search,
  SlidersHorizontal,
  Trash2
} from "lucide-react";
import type { Feature, FeatureCollection, Point } from "geojson";
import type { StyleSpecification } from "maplibre-gl";
import type { LayerProps, MapGeoJSONFeature, MapMouseEvent, MapRef } from "react-map-gl/maplibre";
import MapLibreMap, { Layer, NavigationControl, Popup, Source } from "react-map-gl/maplibre";
import { Command } from "cmdk";
import { useRouter } from "next/navigation";
import { useActionState, useEffect, useMemo, useRef, useState } from "react";
import { createProjectAction } from "./actions";
import {
  initialProjectCreateState,
  type ProjectCreateActionState,
  type ProjectCreateFormValues
} from "./state";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { compactStatus, statusStyle, STATUS_STYLES } from "@/lib/status";
import { cn } from "@/lib/utils";
import type { PipelineData, PipelineProject } from "@/lib/pipeline/types";

type PipelineClientProps = {
  data: PipelineData;
  initialFilters?: Partial<PipelineFilters>;
};

type ViewMode = "table" | "map";
type SortKey = "projectName" | "pipelineStatus" | "developer" | "totalUnits" | "dateDelivery" | "confidence";
type SortDirection = "asc" | "desc";

type PipelineFilters = {
  search: string;
  statuses: string[];
  market: string;
  jurisdiction: string;
  developer: string;
  submarket: string;
  minUnits: string;
  maxUnits: string;
  confidence: string;
  geocodedOnly: boolean;
};

type SavedView = {
  id: string;
  name: string;
  filters: PipelineFilters;
};

type ProjectFeatureProperties = {
  projectId: string;
  projectName: string;
  address: string;
  status: string;
  units: number | null;
};

const FILTER_STORAGE_KEY = "pipeline:filters";
const VIEW_MODE_STORAGE_KEY = "pipeline:viewMode";
const SAVED_VIEWS_STORAGE_KEY = "pipeline:savedViews";
const MAP_TILE_URL = process.env.NEXT_PUBLIC_MAP_TILE_URL?.trim() || "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const MAP_TILE_ATTRIBUTION = process.env.NEXT_PUBLIC_MAP_TILE_ATTRIBUTION?.trim() || "(C) OpenStreetMap contributors";

const DEFAULT_FILTERS: PipelineFilters = {
  search: "",
  statuses: [],
  market: "all",
  jurisdiction: "all",
  developer: "all",
  submarket: "all",
  minUnits: "",
  maxUnits: "",
  confidence: "all",
  geocodedOnly: false
};

function normalizeInitialFilters(initialFilters: Partial<PipelineFilters> | undefined): PipelineFilters {
  return {
    ...DEFAULT_FILTERS,
    ...initialFilters,
    statuses: initialFilters?.statuses ?? DEFAULT_FILTERS.statuses
  };
}

function hasInitialFilterValues(initialFilters: Partial<PipelineFilters> | undefined) {
  if (!initialFilters) {
    return false;
  }

  return Object.entries(initialFilters).some(([, value]) =>
    Array.isArray(value) ? value.length > 0 : value !== undefined && value !== "" && value !== "all" && value !== false
  );
}

const MAP_STYLE: StyleSpecification = {
  version: 8,
  sources: {
    basemap: {
      type: "raster",
      tiles: [MAP_TILE_URL],
      tileSize: 256,
      attribution: MAP_TILE_ATTRIBUTION
    }
  },
  layers: [
    {
      id: "basemap",
      type: "raster",
      source: "basemap"
    }
  ]
};

const clusterLayer: LayerProps = {
  id: "clusters",
  type: "circle",
  source: "projects",
  filter: ["has", "point_count"],
  paint: {
    "circle-color": ["step", ["get", "point_count"], "#0f766e", 25, "#d97706", 100, "#dc2626"],
    "circle-radius": ["step", ["get", "point_count"], 16, 25, 22, 100, 30],
    "circle-stroke-width": 2,
    "circle-stroke-color": "#ffffff"
  }
};

const clusterCountLayer: LayerProps = {
  id: "cluster-count",
  type: "symbol",
  source: "projects",
  filter: ["has", "point_count"],
  layout: {
    "text-field": "{point_count_abbreviated}",
    "text-font": ["Arial Unicode MS Bold"],
    "text-size": 12
  },
  paint: {
    "text-color": "#ffffff"
  }
};

const projectLayer: LayerProps = {
  id: "project-points",
  type: "circle",
  source: "projects",
  filter: ["!", ["has", "point_count"]],
  paint: {
    "circle-color": [
      "match",
      ["get", "status"],
      "Under Construction",
      STATUS_STYLES["Under Construction"].color,
      "Approved",
      STATUS_STYLES.Approved.color,
      "Pending",
      STATUS_STYLES.Pending.color,
      "Proposed",
      STATUS_STYLES.Proposed.color,
      "Conceptual",
      STATUS_STYLES.Conceptual.color,
      "Complete",
      STATUS_STYLES.Complete.color,
      "Stalled",
      STATUS_STYLES.Stalled.color,
      "Inactive",
      STATUS_STYLES.Inactive.color,
      "#0f766e"
    ],
    "circle-radius": 6,
    "circle-stroke-width": 1.5,
    "circle-stroke-color": "#ffffff"
  }
};

function number(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "-";
  }

  return new Intl.NumberFormat("en-US").format(value);
}

function formatDate(value: string | null) {
  if (!value) {
    return "-";
  }

  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    year: "numeric"
  }).format(new Date(value));
}

function projectMatchesSearch(project: PipelineProject, search: string) {
  if (!search) {
    return true;
  }

  const haystack = [
    project.projectName,
    project.canonicalAddress,
    project.developer,
    project.pipelineStatus,
    project.jurisdiction?.displayName,
    project.costarSubmarket,
    ...project.apns
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();

  return haystack.includes(search);
}

function compareNullable<T>(a: T | null | undefined, b: T | null | undefined, direction: SortDirection) {
  if (a === b) {
    return 0;
  }
  if (a === null || a === undefined) {
    return 1;
  }
  if (b === null || b === undefined) {
    return -1;
  }

  const result = typeof a === "number" && typeof b === "number" ? a - b : String(a).localeCompare(String(b));
  return direction === "asc" ? result : -result;
}

function fieldForSort(project: PipelineProject, sortKey: SortKey) {
  if (sortKey === "confidence") {
    return project.confidence ?? project.statusConfidence;
  }

  return project[sortKey];
}

function jurisdictionLabel(project: PipelineProject) {
  return project.jurisdiction?.displayName ?? "Unassigned";
}

function ProjectStatusBadge({ status }: { status: string }) {
  return (
    <span className={cn("inline-flex rounded border px-1.5 py-0.5 text-[11px] font-medium", statusStyle(status).className)}>
      {compactStatus(status)}
    </span>
  );
}

function ConfidenceBadge({ value }: { value: string | null }) {
  if (!value) {
    return <span className="text-slate-400">-</span>;
  }

  const className =
    value === "high"
      ? "border-green-200 bg-green-50 text-green-800"
      : value === "medium"
        ? "border-amber-200 bg-amber-50 text-amber-900"
        : "border-slate-200 bg-slate-50 text-slate-700";

  return <span className={cn("inline-flex rounded border px-1.5 py-0.5 text-[11px] font-medium", className)}>{value}</span>;
}

function ProjectPreview({ project }: { project: PipelineProject }) {
  return (
    <div className="w-80 rounded-md border border-slate-200 bg-white p-3 text-sm shadow-lg">
      <p className="font-semibold text-slate-950">{project.projectName}</p>
      <p className="mt-1 text-xs text-slate-500">{project.canonicalAddress}</p>
      <div className="mt-3 grid grid-cols-2 gap-2">
        <div>
          <p className="text-xs text-slate-500">Status</p>
          <ProjectStatusBadge status={project.pipelineStatus} />
        </div>
        <div>
          <p className="text-xs text-slate-500">Units</p>
          <p className="font-medium">{number(project.totalUnits)}</p>
        </div>
        <div>
          <p className="text-xs text-slate-500">Delivery</p>
          <p className="font-medium">{formatDate(project.dateDelivery)}</p>
        </div>
        <div>
          <p className="text-xs text-slate-500">Confidence</p>
          <ConfidenceBadge value={project.confidence ?? project.statusConfidence} />
        </div>
      </div>
      <div className="mt-3 border-t border-slate-100 pt-2">
        <p className="text-xs text-slate-500">Latest evidence</p>
        <p className="mt-1 line-clamp-2 text-slate-700">
          {project.lastEvidence?.teaser ?? project.lastEvidence?.sourceType ?? "No evidence summary"}
        </p>
        {project.lastEvidence?.fields.length ? (
          <p className="mt-1 text-xs text-slate-500">Fields: {project.lastEvidence.fields.slice(0, 3).join(", ")}</p>
        ) : null}
      </div>
    </div>
  );
}

function SortButton({
  label,
  sortKey,
  activeSort,
  onSort
}: {
  label: string;
  sortKey: SortKey;
  activeSort: { key: SortKey; direction: SortDirection };
  onSort: (sortKey: SortKey) => void;
}) {
  const active = activeSort.key === sortKey;

  return (
    <button className="inline-flex items-center gap-1 text-left" type="button" onClick={() => onSort(sortKey)}>
      {label}
      {active ? (
        activeSort.direction === "asc" ? (
          <ArrowUp className="size-3" aria-hidden="true" />
        ) : (
          <ArrowDown className="size-3" aria-hidden="true" />
        )
      ) : null}
    </button>
  );
}

function FilterSelect({
  label,
  value,
  onChange,
  children
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1 text-xs font-medium text-slate-500">
      {label}
      <select
        className="h-9 rounded-md border border-slate-300 bg-white px-2 text-sm font-normal text-slate-900 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-100"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {children}
      </select>
    </label>
  );
}

type ClusterSource = {
  getClusterExpansionZoom: (clusterId: number) => Promise<number>;
};

function ProjectMap({
  projects,
  onOpenProject
}: {
  projects: PipelineProject[];
  onOpenProject: (project: PipelineProject) => void;
}) {
  const mapRef = useRef<MapRef | null>(null);
  const [popupProject, setPopupProject] = useState<PipelineProject | null>(null);
  const projectById = useMemo(() => new Map(projects.map((project) => [project.id, project])), [projects]);

  const geojson = useMemo<FeatureCollection<Point, ProjectFeatureProperties>>(
    () => ({
      type: "FeatureCollection",
      features: projects
        .filter((project) => project.lat !== null && project.lng !== null)
        .map(
          (project): Feature<Point, ProjectFeatureProperties> => ({
            type: "Feature",
            geometry: {
              type: "Point",
              coordinates: [project.lng as number, project.lat as number]
            },
            properties: {
              projectId: project.id,
              projectName: project.projectName,
              address: project.canonicalAddress,
              status: project.pipelineStatus,
              units: project.totalUnits
            }
          })
        )
    }),
    [projects]
  );

  function handleMapClick(event: MapMouseEvent) {
    const feature = event.features?.[0] as MapGeoJSONFeature | undefined;
    if (!feature) {
      return;
    }

    if (feature.layer?.id === "clusters") {
      const source = mapRef.current?.getSource("projects") as ClusterSource | undefined;
      const clusterId = feature.properties?.cluster_id as number | undefined;
      if (source && clusterId !== undefined) {
        source
          .getClusterExpansionZoom(clusterId)
          .then((zoom) => {
            const coordinates = (feature.geometry as Point).coordinates;
            mapRef.current?.easeTo({ center: coordinates as [number, number], zoom, duration: 400 });
          })
          .catch(() => undefined);
      }
      return;
    }

    const projectId = feature.properties?.projectId as string | undefined;
    const project = projectId ? projectById.get(projectId) : undefined;
    if (project) {
      setPopupProject(project);
    }
  }

  return (
    <div className="h-[calc(100vh-13rem)] min-h-[520px] overflow-hidden rounded-md border border-slate-200 bg-white">
      <MapLibreMap
        ref={mapRef}
        initialViewState={{ latitude: 34.0522, longitude: -118.2437, zoom: 9.5 }}
        mapStyle={MAP_STYLE}
        interactiveLayerIds={["clusters", "project-points"]}
        onClick={handleMapClick}
      >
        <NavigationControl position="top-right" />
        <Source id="projects" type="geojson" data={geojson} cluster clusterMaxZoom={14} clusterRadius={46}>
          <Layer {...clusterLayer} />
          <Layer {...clusterCountLayer} />
          <Layer {...projectLayer} />
        </Source>
        {popupProject && popupProject.lat !== null && popupProject.lng !== null ? (
          <Popup
            latitude={popupProject.lat}
            longitude={popupProject.lng}
            closeButton={false}
            maxWidth="320px"
            onClose={() => setPopupProject(null)}
          >
            <div className="space-y-2 text-sm">
              <p className="font-semibold text-slate-950">{popupProject.projectName}</p>
              <p className="text-xs text-slate-500">{popupProject.canonicalAddress}</p>
              <div className="flex items-center gap-2">
                <ProjectStatusBadge status={popupProject.pipelineStatus} />
                <span>{number(popupProject.totalUnits)} units</span>
              </div>
              <Button type="button" variant="outline" onClick={() => onOpenProject(popupProject)}>
                <ExternalLink className="size-4" aria-hidden="true" />
                Open detail
              </Button>
            </div>
          </Popup>
        ) : null}
      </MapLibreMap>
    </div>
  );
}

function CommandSearch({
  open,
  projects,
  onOpenChange,
  onSelectProject
}: {
  open: boolean;
  projects: PipelineProject[];
  onOpenChange: (open: boolean) => void;
  onSelectProject: (project: PipelineProject) => void;
}) {
  return (
    <div className={cn("fixed inset-0 z-50 bg-slate-950/20", !open && "hidden")} onClick={() => onOpenChange(false)}>
      <div
        className="mx-auto mt-24 max-w-xl overflow-hidden rounded-md border border-slate-200 bg-white shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <Command label="Project search">
          <div className="flex items-center border-b border-slate-200 px-3">
            <Search className="mr-2 size-4 text-slate-400" aria-hidden="true" />
            <Command.Input
              className="h-11 w-full outline-none placeholder:text-slate-400"
              placeholder="Search project, address, developer, APN"
            />
          </div>
          <Command.List className="max-h-96 overflow-y-auto p-2">
            <Command.Empty className="px-3 py-6 text-center text-sm text-slate-500">No projects found.</Command.Empty>
            {projects.map((project) => (
              <Command.Item
                className="cursor-pointer rounded-md px-3 py-2 text-sm aria-selected:bg-slate-100"
                key={project.id}
                value={`${project.projectName} ${project.canonicalAddress} ${project.developer ?? ""} ${project.apns.join(" ")}`}
                onSelect={() => {
                  onSelectProject(project);
                  onOpenChange(false);
                }}
              >
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <p className="font-medium text-slate-950">{project.projectName}</p>
                    <p className="text-xs text-slate-500">{project.canonicalAddress}</p>
                  </div>
                  <ProjectStatusBadge status={project.pipelineStatus} />
                </div>
              </Command.Item>
            ))}
          </Command.List>
        </Command>
      </div>
    </div>
  );
}

function NewProjectDialog({
  data,
  onCreated,
  onOpenChange,
  open
}: {
  data: PipelineData;
  onCreated: (projectId: string) => void;
  onOpenChange: (open: boolean) => void;
  open: boolean;
}) {
  const [state, action, pending] = useActionState(
    createProjectAction,
    initialProjectCreateState
  );
  const [formValues, setFormValues] = useState<ProjectCreateFormValues>(() =>
    initialNewProjectFormValues(data)
  );
  const jurisdictionOptions = data.facets.jurisdictionOptions.filter(
    (jurisdiction) => jurisdiction.marketId === formValues.marketId
  );
  const effectiveJurisdictionId = jurisdictionOptions.some(
    (jurisdiction) => jurisdiction.id === formValues.jurisdictionId
  )
    ? formValues.jurisdictionId
    : (jurisdictionOptions[0]?.id ?? "");
  const duplicateFormUnchanged =
    state.duplicateCandidates.length > 0 &&
    projectCreateFormsEqual(formValues, state.form);
  const duplicateCandidates = duplicateFormUnchanged ? state.duplicateCandidates : [];

  function updateFormValue(field: keyof ProjectCreateFormValues, value: string) {
    setFormValues((current) => {
      if (field !== "marketId") {
        return { ...current, [field]: value };
      }
      return {
        ...current,
        marketId: value,
        jurisdictionId:
          data.facets.jurisdictionOptions.find(
            (jurisdiction) => jurisdiction.marketId === value
          )?.id ?? ""
      };
    });
  }

  useEffect(() => {
    if (state.created && state.projectId) {
      onOpenChange(false);
      onCreated(state.projectId);
    }
  }, [onCreated, onOpenChange, state.created, state.projectId]);

  if (!open) {
    return null;
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-slate-950/30 px-4 py-12"
      onClick={() => onOpenChange(false)}
    >
      <div
        className="w-full max-w-2xl rounded-md border border-slate-200 bg-white shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4 border-b border-slate-200 px-4 py-3">
          <div>
            <h2 className="text-base font-semibold text-slate-950">New project</h2>
          </div>
          <button
            className="rounded px-2 py-1 text-sm text-slate-500 hover:bg-slate-100 hover:text-slate-900"
            type="button"
            onClick={() => onOpenChange(false)}
          >
            Close
          </button>
        </div>

        <form action={action} className="grid gap-3 px-4 py-4 md:grid-cols-2">
          <label className="md:col-span-2">
            <span className="text-xs font-medium text-slate-600">Canonical address</span>
            <input
              className="mt-1 h-9 w-full rounded-md border border-slate-200 px-2 text-sm text-slate-900 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-100"
              maxLength={255}
              name="canonicalAddress"
              required
              value={formValues.canonicalAddress}
              onChange={(event) => updateFormValue("canonicalAddress", event.target.value)}
            />
          </label>

          <label>
            <span className="text-xs font-medium text-slate-600">Market</span>
            <select
              className="mt-1 h-9 w-full rounded-md border border-slate-200 bg-white px-2 text-sm text-slate-900 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-100"
              name="marketId"
              required
              value={formValues.marketId}
              onChange={(event) => updateFormValue("marketId", event.target.value)}
            >
              {data.facets.marketOptions.map((market) => (
                <option key={market.id} value={market.id}>
                  {market.displayName}
                </option>
              ))}
            </select>
          </label>

          <label>
            <span className="text-xs font-medium text-slate-600">Jurisdiction</span>
            <select
              className="mt-1 h-9 w-full rounded-md border border-slate-200 bg-white px-2 text-sm text-slate-900 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-100"
              name="jurisdictionId"
              required
              value={effectiveJurisdictionId}
              onChange={(event) => updateFormValue("jurisdictionId", event.target.value)}
            >
              {jurisdictionOptions.map((jurisdiction) => (
                <option key={jurisdiction.id} value={jurisdiction.id}>
                  {jurisdiction.displayName}
                </option>
              ))}
            </select>
          </label>

          <label>
            <span className="text-xs font-medium text-slate-600">Project name</span>
            <input
              className="mt-1 h-9 w-full rounded-md border border-slate-200 px-2 text-sm text-slate-900 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-100"
              maxLength={255}
              name="projectName"
              value={formValues.projectName}
              onChange={(event) => updateFormValue("projectName", event.target.value)}
            />
          </label>

          <label>
            <span className="text-xs font-medium text-slate-600">City</span>
            <input
              className="mt-1 h-9 w-full rounded-md border border-slate-200 px-2 text-sm text-slate-900 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-100"
              maxLength={120}
              name="city"
              placeholder="Defaults from jurisdiction"
              value={formValues.city}
              onChange={(event) => updateFormValue("city", event.target.value)}
            />
          </label>

          <label>
            <span className="text-xs font-medium text-slate-600">County</span>
            <input
              className="mt-1 h-9 w-full rounded-md border border-slate-200 px-2 text-sm text-slate-900 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-100"
              maxLength={120}
              name="county"
              placeholder="Defaults from market"
              value={formValues.county}
              onChange={(event) => updateFormValue("county", event.target.value)}
            />
          </label>

          <label>
            <span className="text-xs font-medium text-slate-600">ZIP</span>
            <input
              className="mt-1 h-9 w-full rounded-md border border-slate-200 px-2 text-sm text-slate-900 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-100"
              maxLength={10}
              name="zip"
              value={formValues.zip}
              onChange={(event) => updateFormValue("zip", event.target.value)}
            />
          </label>

          <div className="md:col-span-2 flex items-center justify-between gap-3 border-t border-slate-100 pt-3">
            <ActionMessage state={state} />
            <Button disabled={pending} type="submit">
              <Plus className="size-4" aria-hidden="true" />
              Check and create
            </Button>
          </div>
        </form>

        {state.duplicateCandidates.length && !duplicateFormUnchanged ? (
          <p className="border-t border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-900">
            Project details changed. Check again before creating.
          </p>
        ) : null}

        {duplicateCandidates.length ? (
          <div className="border-t border-amber-200 bg-amber-50 px-4 py-3">
            <p className="text-sm font-semibold text-amber-950">Possible duplicate</p>
            <div className="mt-2 grid gap-2">
              {duplicateCandidates.map((candidate) => (
                <div
                  className="flex flex-col gap-2 rounded-md border border-amber-200 bg-white p-2 text-sm md:flex-row md:items-center md:justify-between"
                  key={candidate.projectId}
                >
                  <div>
                    <p className="font-medium text-slate-950">{candidate.projectName}</p>
                    <p className="text-xs text-slate-500">{candidate.canonicalAddress}</p>
                    <p className="mt-1 text-xs text-slate-500">
                      {candidate.pipelineStatus} | {candidate.matchType}
                    </p>
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => onCreated(candidate.projectId)}
                  >
                    Open existing
                  </Button>
                </div>
              ))}
            </div>
            <form action={action} className="mt-3 flex justify-end">
              <input name="canonicalAddress" type="hidden" value={state.form.canonicalAddress} />
              <input name="marketId" type="hidden" value={state.form.marketId} />
              <input name="jurisdictionId" type="hidden" value={state.form.jurisdictionId} />
              <input name="projectName" type="hidden" value={state.form.projectName} />
              <input name="city" type="hidden" value={state.form.city} />
              <input name="county" type="hidden" value={state.form.county} />
              <input name="zip" type="hidden" value={state.form.zip} />
              <input name="forceCreate" type="hidden" value="true" />
              <Button disabled={pending} type="submit" variant="outline">
                Create anyway
              </Button>
            </form>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function ActionMessage({ state }: { state: ProjectCreateActionState }) {
  if (!state.message) {
    return <span />;
  }

  return (
    <p className={cn("text-xs", state.ok ? "text-slate-600" : "text-red-700")}>
      {state.message}
    </p>
  );
}

function initialNewProjectFormValues(data: PipelineData): ProjectCreateFormValues {
  const marketId = data.facets.marketOptions[0]?.id ?? "";
  const jurisdictionId =
    data.facets.jurisdictionOptions.find(
      (jurisdiction) => jurisdiction.marketId === marketId
    )?.id ?? "";
  return {
    canonicalAddress: "",
    marketId,
    jurisdictionId,
    projectName: "",
    city: "",
    county: "",
    zip: ""
  };
}

function projectCreateFormsEqual(
  current: ProjectCreateFormValues,
  submitted: ProjectCreateFormValues
) {
  return (
    normalizeFormValue(current.canonicalAddress) ===
      normalizeFormValue(submitted.canonicalAddress) &&
    normalizeFormValue(current.marketId) === normalizeFormValue(submitted.marketId) &&
    normalizeFormValue(current.jurisdictionId) ===
      normalizeFormValue(submitted.jurisdictionId) &&
    normalizeFormValue(current.projectName) === normalizeFormValue(submitted.projectName) &&
    normalizeFormValue(current.city) === normalizeFormValue(submitted.city) &&
    normalizeFormValue(current.county) === normalizeFormValue(submitted.county) &&
    normalizeFormValue(current.zip) === normalizeFormValue(submitted.zip)
  );
}

function normalizeFormValue(value: string) {
  return value.trim();
}

export function PipelineClient({ data, initialFilters }: PipelineClientProps) {
  const router = useRouter();
  const searchRef = useRef<HTMLInputElement | null>(null);
  const rowRefs = useRef<Map<string, HTMLTableRowElement>>(new Map());
  const loadedStoredState = useRef(false);
  const initialFilterState = useMemo(() => normalizeInitialFilters(initialFilters), [initialFilters]);
  const hasUrlFilters = hasInitialFilterValues(initialFilters);
  const [filters, setFilters] = useState<PipelineFilters>(() => normalizeInitialFilters(initialFilters));
  const [viewMode, setViewMode] = useState<ViewMode>("table");
  const [sort, setSort] = useState<{ key: SortKey; direction: SortDirection }>({
    key: "totalUnits",
    direction: "desc"
  });
  const [filtersOpen, setFiltersOpen] = useState(true);
  const [hoveredProject, setHoveredProject] = useState<PipelineProject | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const [commandOpen, setCommandOpen] = useState(false);
  const [newProjectOpen, setNewProjectOpen] = useState(false);
  const [savedViews, setSavedViews] = useState<SavedView[]>([]);
  const [savedViewName, setSavedViewName] = useState("");

  useEffect(() => {
    const timeout = window.setTimeout(() => {
      try {
        const savedFilters = window.sessionStorage.getItem(FILTER_STORAGE_KEY);
        if (savedFilters && !hasUrlFilters) {
          setFilters({ ...DEFAULT_FILTERS, ...JSON.parse(savedFilters) });
        } else if (hasUrlFilters) {
          setFilters(initialFilterState);
        }

        const savedViewMode = window.sessionStorage.getItem(VIEW_MODE_STORAGE_KEY);
        if (savedViewMode === "table" || savedViewMode === "map") {
          setViewMode(savedViewMode);
        }

        const saved = window.localStorage.getItem(SAVED_VIEWS_STORAGE_KEY);
        if (saved) {
          setSavedViews(JSON.parse(saved) as SavedView[]);
        }
      } catch {
        window.sessionStorage.removeItem(FILTER_STORAGE_KEY);
        window.sessionStorage.removeItem(VIEW_MODE_STORAGE_KEY);
        window.localStorage.removeItem(SAVED_VIEWS_STORAGE_KEY);
      } finally {
        loadedStoredState.current = true;
      }
    }, 0);

    return () => window.clearTimeout(timeout);
  }, [hasUrlFilters, initialFilterState]);

  useEffect(() => {
    if (!loadedStoredState.current) {
      return;
    }
    window.sessionStorage.setItem(FILTER_STORAGE_KEY, JSON.stringify(filters));
  }, [filters]);

  useEffect(() => {
    if (!loadedStoredState.current) {
      return;
    }
    window.sessionStorage.setItem(VIEW_MODE_STORAGE_KEY, viewMode);
  }, [viewMode]);

  useEffect(() => {
    if (!loadedStoredState.current) {
      return;
    }
    window.localStorage.setItem(SAVED_VIEWS_STORAGE_KEY, JSON.stringify(savedViews));
  }, [savedViews]);

  const filteredProjects = useMemo(() => {
    const search = filters.search.trim().toLowerCase();
    const minUnits = filters.minUnits ? Number(filters.minUnits) : null;
    const maxUnits = filters.maxUnits ? Number(filters.maxUnits) : null;

    return data.projects
      .filter((project) => {
        const units = project.totalUnits ?? 0;
        const confidence = project.confidence ?? project.statusConfidence ?? "";
        const jurisdiction = project.jurisdiction?.displayName;

        return (
          projectMatchesSearch(project, search) &&
          (filters.statuses.length === 0 || filters.statuses.includes(project.pipelineStatus)) &&
          (filters.market === "all" || project.market === filters.market) &&
          (filters.jurisdiction === "all" || jurisdiction === filters.jurisdiction) &&
          (filters.developer === "all" || project.developer === filters.developer) &&
          (filters.submarket === "all" || project.costarSubmarket === filters.submarket) &&
          (filters.confidence === "all" || confidence === filters.confidence) &&
          (!filters.geocodedOnly || (project.lat !== null && project.lng !== null)) &&
          (minUnits === null || units >= minUnits) &&
          (maxUnits === null || units <= maxUnits)
        );
      })
      .sort((a, b) => {
        const result = compareNullable(fieldForSort(a, sort.key), fieldForSort(b, sort.key), sort.direction);
        return result !== 0 ? result : a.projectName.localeCompare(b.projectName);
      });
  }, [data.projects, filters, sort]);

  const totals = useMemo(
    () => ({
      projects: filteredProjects.length,
      units: filteredProjects.reduce((sum, project) => sum + (project.totalUnits ?? 0), 0),
      geocoded: filteredProjects.filter((project) => project.lat !== null && project.lng !== null).length,
      underConstruction: filteredProjects.filter((project) => project.pipelineStatus === "Under Construction").length
    }),
    [filteredProjects]
  );

  const boundedActiveIndex = Math.min(activeIndex, Math.max(0, filteredProjects.length - 1));
  const activeProjectId = filteredProjects[boundedActiveIndex]?.id ?? null;

  useEffect(() => {
    if (viewMode !== "table" || !activeProjectId) {
      return;
    }

    rowRefs.current.get(activeProjectId)?.scrollIntoView({ block: "nearest" });
  }, [activeProjectId, viewMode]);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      const target = event.target as HTMLElement;
      const inEditable = ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);

      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setCommandOpen(true);
        return;
      }

      if (event.key === "Escape") {
        if (commandOpen) {
          event.preventDefault();
          setCommandOpen(false);
          return;
        }

      }

      if (event.key === "/" && !inEditable) {
        event.preventDefault();
        searchRef.current?.focus();
        return;
      }

      if (inEditable || commandOpen) {
        return;
      }

      if (viewMode !== "table") {
        return;
      }

      if (event.key.toLowerCase() === "j") {
        event.preventDefault();
        setActiveIndex((current) => Math.min(current + 1, Math.max(0, filteredProjects.length - 1)));
      }

      if (event.key.toLowerCase() === "k") {
        event.preventDefault();
        setActiveIndex((current) => Math.max(current - 1, 0));
      }

      if (event.key === "Enter" && filteredProjects[boundedActiveIndex]) {
        event.preventDefault();
        router.push(`/pipeline/${filteredProjects[boundedActiveIndex].id}`);
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [boundedActiveIndex, commandOpen, filteredProjects, router, viewMode]);

  function updateFilter<K extends keyof PipelineFilters>(key: K, value: PipelineFilters[K]) {
    setFilters((current) => ({ ...current, [key]: value }));
  }

  function toggleStatus(status: string) {
    setFilters((current) => ({
      ...current,
      statuses: current.statuses.includes(status)
        ? current.statuses.filter((item) => item !== status)
        : [...current.statuses, status]
    }));
  }

  function handleSort(sortKey: SortKey) {
    setSort((current) =>
      current.key === sortKey
        ? { key: sortKey, direction: current.direction === "asc" ? "desc" : "asc" }
        : { key: sortKey, direction: "asc" }
    );
  }

  function saveView() {
    const name = savedViewName.trim();
    if (!name) {
      return;
    }

    const view: SavedView = {
      id: crypto.randomUUID(),
      name,
      filters
    };
    setSavedViews((current) => [...current.filter((item) => item.name !== name), view]);
    setSavedViewName("");
  }

  function removeSavedView(id: string) {
    setSavedViews((current) => current.filter((view) => view.id !== id));
  }

  return (
    <main className="px-5 py-5">
      <div className="mb-4 flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-normal text-slate-950">Pipeline</h1>
          <div className="mt-3 flex flex-wrap gap-5">
            <Metric label="Projects" value={number(totals.projects)} />
            <Metric label="Units" value={number(totals.units)} />
            <Metric label="U/C" value={number(totals.underConstruction)} />
            <Metric label="Mapped" value={number(totals.geocoded)} />
          </div>
        </div>

        <div className="flex flex-wrap items-end gap-2">
          <div className="min-w-72">
            <label className="mb-1 block text-xs font-medium text-slate-500" htmlFor="pipeline-search">
              Search
            </label>
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
              <Input
                ref={searchRef}
                className="pl-9"
                id="pipeline-search"
                placeholder="Project, address, developer, APN"
                value={filters.search}
                onChange={(event) => updateFilter("search", event.target.value)}
              />
            </div>
          </div>
          <Button type="button" variant="outline" onClick={() => setCommandOpen(true)}>
            <CommandIcon className="size-4" aria-hidden="true" />
            Command K
          </Button>
          <div className="flex rounded-md border border-slate-200 bg-white p-1">
            <Button type="button" variant={viewMode === "table" ? "default" : "ghost"} onClick={() => setViewMode("table")}>
              <List className="size-4" aria-hidden="true" />
              Table
            </Button>
            <Button type="button" variant={viewMode === "map" ? "default" : "ghost"} onClick={() => setViewMode("map")}>
              <MapIcon className="size-4" aria-hidden="true" />
              Map
            </Button>
          </div>
          <Button type="button" variant="outline" onClick={() => setNewProjectOpen(true)}>
            <Plus className="size-4" aria-hidden="true" />
            New project
          </Button>
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[18rem_minmax(0,1fr)]">
        <aside className={cn("rounded-md border border-slate-200 bg-white p-3", !filtersOpen && "xl:hidden")}>
          <div className="mb-3 flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <SlidersHorizontal className="size-4 text-slate-500" aria-hidden="true" />
              <p className="text-sm font-semibold text-slate-950">Filters</p>
            </div>
            <Button type="button" variant="ghost" onClick={() => setFilters(DEFAULT_FILTERS)}>
              Reset
            </Button>
          </div>

          <div className="space-y-4">
            <div>
              <p className="mb-2 text-xs font-medium text-slate-500">Status</p>
              <div className="flex flex-wrap gap-2">
                {data.facets.statuses.map((status) => (
                  <button
                    className={cn(
                      "rounded border px-2 py-1 text-xs",
                      filters.statuses.includes(status)
                        ? statusStyle(status).className
                        : "border-slate-200 bg-white text-slate-600"
                    )}
                    key={status}
                    type="button"
                    onClick={() => toggleStatus(status)}
                  >
                    {compactStatus(status)}
                  </button>
                ))}
              </div>
            </div>

            <FilterSelect label="Market" value={filters.market} onChange={(value) => updateFilter("market", value)}>
              <option value="all">All markets</option>
              {data.facets.markets.map((market) => (
                <option key={market} value={market}>
                  {market}
                </option>
              ))}
            </FilterSelect>

            <FilterSelect
              label="Jurisdiction"
              value={filters.jurisdiction}
              onChange={(value) => updateFilter("jurisdiction", value)}
            >
              <option value="all">All jurisdictions</option>
              {data.facets.jurisdictions.map((jurisdiction) => (
                <option key={jurisdiction} value={jurisdiction}>
                  {jurisdiction}
                </option>
              ))}
            </FilterSelect>

            <FilterSelect label="Developer" value={filters.developer} onChange={(value) => updateFilter("developer", value)}>
              <option value="all">All developers</option>
              {data.facets.developers.map((developer) => (
                <option key={developer} value={developer}>
                  {developer}
                </option>
              ))}
            </FilterSelect>

            <FilterSelect label="Submarket" value={filters.submarket} onChange={(value) => updateFilter("submarket", value)}>
              <option value="all">All submarkets</option>
              {data.facets.submarkets.map((submarket) => (
                <option key={submarket} value={submarket}>
                  {submarket}
                </option>
              ))}
            </FilterSelect>

            <FilterSelect
              label="Confidence"
              value={filters.confidence}
              onChange={(value) => updateFilter("confidence", value)}
            >
              <option value="all">All confidence</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </FilterSelect>

            <div className="grid grid-cols-2 gap-2">
              <label className="flex flex-col gap-1 text-xs font-medium text-slate-500">
                Min units
                <Input
                  min={0}
                  type="number"
                  value={filters.minUnits}
                  onChange={(event) => updateFilter("minUnits", event.target.value)}
                />
              </label>
              <label className="flex flex-col gap-1 text-xs font-medium text-slate-500">
                Max units
                <Input
                  min={0}
                  type="number"
                  value={filters.maxUnits}
                  onChange={(event) => updateFilter("maxUnits", event.target.value)}
                />
              </label>
            </div>

            <label className="flex items-center gap-2 text-sm text-slate-700">
              <input
                checked={filters.geocodedOnly}
                type="checkbox"
                onChange={(event) => updateFilter("geocodedOnly", event.target.checked)}
              />
              Mapped projects only
            </label>

            <div className="border-t border-slate-200 pt-3">
              <p className="mb-2 text-xs font-medium text-slate-500">Saved views</p>
              <div className="flex gap-2">
                <Input
                  placeholder="View name"
                  value={savedViewName}
                  onChange={(event) => setSavedViewName(event.target.value)}
                />
                <Button type="button" variant="outline" onClick={saveView}>
                  <Save className="size-4" aria-hidden="true" />
                </Button>
              </div>
              <div className="mt-2 space-y-1">
                {savedViews.map((view) => (
                  <div className="flex items-center gap-1" key={view.id}>
                    <button
                      className="min-w-0 flex-1 truncate rounded-md px-2 py-1 text-left text-sm text-slate-700 hover:bg-slate-100"
                      type="button"
                      onClick={() => setFilters(view.filters)}
                    >
                      {view.name}
                    </button>
                    <button
                      aria-label={`Delete ${view.name}`}
                      className="flex size-7 items-center justify-center rounded-md text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                      type="button"
                      onClick={() => removeSavedView(view.id)}
                    >
                      <Trash2 className="size-4" aria-hidden="true" />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </aside>

        <section className="min-w-0">
          <div className="mb-3 flex items-center justify-between">
            <Button type="button" variant="outline" onClick={() => setFiltersOpen((open) => !open)}>
              {filtersOpen ? <ChevronLeft className="size-4" aria-hidden="true" /> : <ChevronRight className="size-4" aria-hidden="true" />}
              {filtersOpen ? "Hide filters" : "Show filters"}
            </Button>
            <p className="text-sm text-slate-500">J/K selects rows. Enter opens preview. Slash focuses search.</p>
          </div>

          {viewMode === "table" ? (
            <div className="relative">
              <div className="overflow-x-auto rounded-md border border-slate-200 bg-white">
                <table className="min-w-[1120px] w-full border-collapse text-left text-sm">
                  <thead className="bg-slate-100 text-xs uppercase text-slate-500">
                    <tr>
                      <th className="w-10 px-3 py-2 font-medium">#</th>
                      <th className="min-w-56 px-3 py-2 font-medium">
                        <SortButton label="Project" sortKey="projectName" activeSort={sort} onSort={handleSort} />
                      </th>
                      <th className="min-w-56 px-3 py-2 font-medium">Address</th>
                      <th className="px-3 py-2 font-medium">
                        <SortButton label="Status" sortKey="pipelineStatus" activeSort={sort} onSort={handleSort} />
                      </th>
                      <th className="min-w-44 px-3 py-2 font-medium">
                        <SortButton label="Developer" sortKey="developer" activeSort={sort} onSort={handleSort} />
                      </th>
                      <th className="px-3 py-2 text-right font-medium">
                        <SortButton label="Units" sortKey="totalUnits" activeSort={sort} onSort={handleSort} />
                      </th>
                      <th className="px-3 py-2 font-medium">
                        <SortButton label="Delivery" sortKey="dateDelivery" activeSort={sort} onSort={handleSort} />
                      </th>
                      <th className="px-3 py-2 font-medium">
                        <SortButton label="Conf" sortKey="confidence" activeSort={sort} onSort={handleSort} />
                      </th>
                      <th className="min-w-36 px-3 py-2 font-medium">Evidence</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredProjects.map((project, index) => {
                      const active = index === boundedActiveIndex;

                      return (
                        <tr
                          className={cn(
                            "cursor-pointer border-t border-slate-100 hover:bg-slate-50",
                            active && "bg-teal-50/70"
                          )}
                          key={project.id}
                          ref={(node) => {
                            if (node) {
                              rowRefs.current.set(project.id, node);
                            } else {
                              rowRefs.current.delete(project.id);
                            }
                          }}
                          onClick={() => router.push(`/pipeline/${project.id}`)}
                          onMouseEnter={() => {
                            setHoveredProject(project);
                            setActiveIndex(index);
                          }}
                          onMouseLeave={() => setHoveredProject(null)}
                        >
                          <td className="px-3 py-2 text-xs text-slate-400">{index + 1}</td>
                          <td className="px-3 py-2">
                            <p className="font-medium text-slate-950">{project.projectName}</p>
                            <p className="text-xs text-slate-500">{jurisdictionLabel(project)}</p>
                          </td>
                          <td className="px-3 py-2 text-slate-700">{project.canonicalAddress}</td>
                          <td className="px-3 py-2">
                            <ProjectStatusBadge status={project.pipelineStatus} />
                          </td>
                          <td className="px-3 py-2 text-slate-700">{project.developer ?? "-"}</td>
                          <td className="px-3 py-2 text-right font-medium text-slate-950">{number(project.totalUnits)}</td>
                          <td className="px-3 py-2 text-slate-700">{formatDate(project.dateDelivery)}</td>
                          <td className="px-3 py-2">
                            <ConfidenceBadge value={project.confidence ?? project.statusConfidence} />
                          </td>
                          <td className="px-3 py-2 text-xs text-slate-500">
                            {project.lastEvidence?.sourceType ?? "-"}
                          </td>
                        </tr>
                      );
                    })}
                    {filteredProjects.length === 0 ? (
                      <tr>
                        <td className="px-3 py-8 text-center text-sm text-slate-500" colSpan={9}>
                          No projects match the current filters.
                        </td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
              {hoveredProject ? (
                <div className="pointer-events-none fixed right-5 top-28 z-30 hidden xl:block">
                  <ProjectPreview project={hoveredProject} />
                </div>
              ) : null}
            </div>
          ) : (
            <ProjectMap projects={filteredProjects} onOpenProject={(project) => router.push(`/pipeline/${project.id}`)} />
          )}
        </section>
      </div>

      <CommandSearch
        open={commandOpen}
        projects={data.projects}
        onOpenChange={setCommandOpen}
        onSelectProject={(project) => router.push(`/pipeline/${project.id}`)}
      />
      <NewProjectDialog
        data={data}
        open={newProjectOpen}
        onOpenChange={setNewProjectOpen}
        onCreated={(projectId) => router.push(`/pipeline/${projectId}`)}
      />
    </main>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-24 border-l border-slate-200 pl-4 first:border-l-0 first:pl-0">
      <p className="text-xs text-slate-500">{label}</p>
      <p className="text-base font-semibold text-slate-950">{value}</p>
    </div>
  );
}

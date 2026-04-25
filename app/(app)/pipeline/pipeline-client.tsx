"use client";

import {
  ArrowDown,
  ArrowUp,
  ChevronLeft,
  ChevronRight,
  Command as CommandIcon,
  Eye,
  List,
  Map as MapIcon,
  Plus,
  Save,
  Search,
  SlidersHorizontal,
  Trash2,
  X
} from "lucide-react";
import type { Feature, FeatureCollection, Point } from "geojson";
import type { StyleSpecification } from "maplibre-gl";
import type { LayerProps, MapGeoJSONFeature, MapMouseEvent, MapRef } from "react-map-gl/maplibre";
import MapLibreMap, { Layer, NavigationControl, Popup, Source } from "react-map-gl/maplibre";
import { Command } from "cmdk";
import { useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type { PipelineData, PipelineProject } from "@/lib/pipeline/types";

type PipelineClientProps = {
  data: PipelineData;
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
const MAP_TILE_URL = process.env.NEXT_PUBLIC_MAP_TILE_URL ?? "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const MAP_TILE_ATTRIBUTION = process.env.NEXT_PUBLIC_MAP_TILE_ATTRIBUTION ?? "(C) OpenStreetMap contributors";

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

const STATUS_STYLES: Record<string, { className: string; color: string }> = {
  "Under Construction": { className: "border-red-200 bg-red-50 text-red-800", color: "#dc2626" },
  Approved: { className: "border-green-200 bg-green-50 text-green-800", color: "#16a34a" },
  Pending: { className: "border-amber-200 bg-amber-50 text-amber-900", color: "#d97706" },
  Proposed: { className: "border-blue-200 bg-blue-50 text-blue-800", color: "#2563eb" },
  Conceptual: { className: "border-violet-200 bg-violet-50 text-violet-800", color: "#7c3aed" },
  Complete: { className: "border-slate-200 bg-slate-50 text-slate-700", color: "#64748b" },
  Stalled: { className: "border-orange-200 bg-orange-50 text-orange-900", color: "#ea580c" },
  Inactive: { className: "border-zinc-200 bg-zinc-50 text-zinc-600", color: "#71717a" }
};

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

function statusStyle(status: string) {
  return STATUS_STYLES[status] ?? { className: "border-slate-200 bg-white text-slate-700", color: "#0f766e" };
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

function compactStatus(status: string) {
  if (status === "Under Construction") {
    return "U/C";
  }
  return status;
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

function DetailDrawer({ project, onClose }: { project: PipelineProject | null; onClose: () => void }) {
  if (!project) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-40 bg-slate-950/20" onClick={onClose}>
      <aside
        className="absolute inset-y-0 right-0 w-full max-w-xl overflow-y-auto border-l border-slate-200 bg-white p-5 shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="mb-5 flex items-start justify-between gap-4">
          <div>
            <p className="text-xs font-medium uppercase text-slate-500">Project Preview</p>
            <h2 className="mt-1 text-lg font-semibold text-slate-950">{project.projectName}</h2>
            <p className="text-sm text-slate-500">{project.canonicalAddress}</p>
          </div>
          <button
            aria-label="Close project preview"
            className="flex size-8 items-center justify-center rounded-md border border-slate-200 text-slate-600 hover:bg-slate-50"
            type="button"
            onClick={onClose}
          >
            <X className="size-4" aria-hidden="true" />
          </button>
        </div>

        <div className="grid grid-cols-2 gap-3 text-sm">
          <PreviewField label="Status" value={<ProjectStatusBadge status={project.pipelineStatus} />} />
          <PreviewField label="Units" value={number(project.totalUnits)} />
          <PreviewField label="Developer" value={project.developer ?? "-"} />
          <PreviewField label="Delivery" value={formatDate(project.dateDelivery)} />
          <PreviewField label="Jurisdiction" value={jurisdictionLabel(project)} />
          <PreviewField label="Submarket" value={project.costarSubmarket ?? "-"} />
          <PreviewField label="Product" value={project.productType ?? "-"} />
          <PreviewField label="Rent / Sale" value={project.rentOrSale ?? "-"} />
        </div>

        <div className="mt-5 rounded-md border border-slate-200 p-3">
          <p className="text-xs font-medium uppercase text-slate-500">Latest evidence</p>
          <p className="mt-2 text-sm text-slate-900">{project.lastEvidence?.teaser ?? "No evidence summary available."}</p>
          <p className="mt-2 text-xs text-slate-500">
            {project.lastEvidence?.sourceType ?? "No source"} |{" "}
            {project.lastEvidence?.evidenceDate ?? project.lastEvidence?.collectedAt ?? "No date"}
          </p>
          {project.lastEvidence?.fields.length ? (
            <p className="mt-2 text-xs text-slate-500">Fields: {project.lastEvidence.fields.join(", ")}</p>
          ) : null}
        </div>

        <p className="mt-4 rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-600">
          Full Project Detail tabs are scheduled for B.4-B.6. This drawer keeps B.3 navigation behavior in place without adding write controls.
        </p>
      </aside>
    </div>
  );
}

function PreviewField({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded-md border border-slate-200 p-3">
      <p className="text-xs text-slate-500">{label}</p>
      <div className="mt-1 font-medium text-slate-950">{value}</div>
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
                <Eye className="size-4" aria-hidden="true" />
                Open preview
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

export function PipelineClient({ data }: PipelineClientProps) {
  const searchRef = useRef<HTMLInputElement | null>(null);
  const rowRefs = useRef<Map<string, HTMLTableRowElement>>(new Map());
  const loadedStoredState = useRef(false);
  const [filters, setFilters] = useState<PipelineFilters>(DEFAULT_FILTERS);
  const [viewMode, setViewMode] = useState<ViewMode>("table");
  const [sort, setSort] = useState<{ key: SortKey; direction: SortDirection }>({
    key: "totalUnits",
    direction: "desc"
  });
  const [filtersOpen, setFiltersOpen] = useState(true);
  const [hoveredProject, setHoveredProject] = useState<PipelineProject | null>(null);
  const [selectedProject, setSelectedProject] = useState<PipelineProject | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const [commandOpen, setCommandOpen] = useState(false);
  const [savedViews, setSavedViews] = useState<SavedView[]>([]);
  const [savedViewName, setSavedViewName] = useState("");

  useEffect(() => {
    const timeout = window.setTimeout(() => {
      try {
        const savedFilters = window.sessionStorage.getItem(FILTER_STORAGE_KEY);
        if (savedFilters) {
          setFilters({ ...DEFAULT_FILTERS, ...JSON.parse(savedFilters) });
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
  }, []);

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

        if (selectedProject) {
          event.preventDefault();
          setSelectedProject(null);
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
        setSelectedProject(filteredProjects[boundedActiveIndex]);
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [boundedActiveIndex, commandOpen, filteredProjects, selectedProject, viewMode]);

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
          <Button disabled title="Available in Phase C" type="button" variant="outline">
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
                          onClick={() => setSelectedProject(project)}
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
            <ProjectMap projects={filteredProjects} onOpenProject={setSelectedProject} />
          )}
        </section>
      </div>

      <CommandSearch
        open={commandOpen}
        projects={data.projects}
        onOpenChange={setCommandOpen}
        onSelectProject={setSelectedProject}
      />
      <DetailDrawer project={selectedProject} onClose={() => setSelectedProject(null)} />
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

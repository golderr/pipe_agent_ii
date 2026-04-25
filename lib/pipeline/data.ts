import { createSupabaseServerClient } from "@/lib/supabase/server";
import type { PipelineData, PipelineProject } from "@/lib/pipeline/types";

const PAGE_SIZE = 1000;

type SupabaseServerClient = Awaited<ReturnType<typeof createSupabaseServerClient>>;

type RawProject = {
  id: string;
  canonical_address: string;
  city: string;
  state: string;
  county: string;
  market: string;
  jurisdiction_id: string | null;
  project_name: string | null;
  developer: string | null;
  total_units: number | null;
  date_delivery: string | null;
  pipeline_status: string;
  confidence: string | null;
  status_confidence: string | null;
  product_type: string | null;
  rent_or_sale: string | null;
  costar_submarket: string | null;
  lat: number | null;
  lng: number | null;
};

type RawJurisdiction = {
  id: string;
  slug: string;
  name: string;
  display_name: string | null;
};

type RawIdentifier = {
  project_id: string;
  identifier_type: string;
  value: string;
};

type RawLatestEvidence = {
  project_id: string;
  source_type: string;
  collected_at: string;
  evidence_date: string | null;
  extracted_fields: Record<string, unknown> | null;
  notes: string | null;
};

type PipelineDataResult = { data: PipelineData; error: null } | { data: PipelineData; error: string };

async function fetchAllRows<T>(
  supabase: SupabaseServerClient,
  table: string,
  select: string
): Promise<{ rows: T[]; error: string | null }> {
  const rows: T[] = [];

  for (let from = 0; ; from += PAGE_SIZE) {
    const to = from + PAGE_SIZE - 1;
    const { data, error } = await supabase.from(table).select(select).range(from, to);

    if (error) {
      return { rows, error: error.message };
    }

    const page = (data ?? []) as T[];
    rows.push(...page);

    if (page.length < PAGE_SIZE) {
      return { rows, error: null };
    }
  }
}

function evidenceTeaser(evidence: RawLatestEvidence) {
  if (evidence.notes) {
    return evidence.notes;
  }

  const fields = Object.entries(evidence.extracted_fields ?? {})
    .filter(([, value]) => value !== null && value !== undefined && value !== "")
    .slice(0, 3)
    .map(([key, value]) => `${key}: ${String(value)}`);

  return fields.length > 0 ? fields.join(" | ") : null;
}

function uniqueSorted(values: Array<string | null | undefined>) {
  return [...new Set(values.filter(Boolean) as string[])].sort((a, b) => a.localeCompare(b));
}

export async function getPipelineData(): Promise<PipelineDataResult> {
  const supabase = await createSupabaseServerClient();

  const [projects, jurisdictions, identifiers, evidenceRows] = await Promise.all([
    fetchAllRows<RawProject>(
      supabase,
      "projects",
      [
        "id",
        "canonical_address",
        "city",
        "state",
        "county",
        "market",
        "jurisdiction_id",
        "project_name",
        "developer",
        "total_units",
        "date_delivery",
        "pipeline_status",
        "confidence",
        "status_confidence",
        "product_type",
        "rent_or_sale",
        "costar_submarket",
        "lat",
        "lng"
      ].join(", ")
    ),
    fetchAllRows<RawJurisdiction>(supabase, "jurisdictions", "id, slug, name, display_name"),
    fetchAllRows<RawIdentifier>(supabase, "project_identifiers", "project_id, identifier_type, value"),
    fetchAllRows<RawLatestEvidence>(
      supabase,
      "project_latest_evidence",
      "project_id, source_type, collected_at, evidence_date, extracted_fields, notes"
    )
  ]);

  const error = projects.error ?? jurisdictions.error ?? identifiers.error ?? evidenceRows.error;
  if (error) {
    return {
      data: {
        projects: [],
        facets: {
          statuses: [],
          markets: [],
          jurisdictions: [],
          developers: [],
          submarkets: [],
          maxUnits: 0
        }
      },
      error
    };
  }

  const jurisdictionById = new Map(jurisdictions.rows.map((jurisdiction) => [jurisdiction.id, jurisdiction]));
  const apnsByProject = new Map<string, string[]>();
  const latestEvidenceByProject = new Map<string, RawLatestEvidence>();

  for (const identifier of identifiers.rows) {
    if (identifier.identifier_type !== "apn") {
      continue;
    }

    const apns = apnsByProject.get(identifier.project_id) ?? [];
    apns.push(identifier.value);
    apnsByProject.set(identifier.project_id, apns);
  }

  for (const evidence of evidenceRows.rows) {
    latestEvidenceByProject.set(evidence.project_id, evidence);
  }

  const pipelineProjects: PipelineProject[] = projects.rows.map((project) => {
    const jurisdiction = project.jurisdiction_id ? jurisdictionById.get(project.jurisdiction_id) : null;
    const latestEvidence = latestEvidenceByProject.get(project.id);

    return {
      id: project.id,
      projectName: project.project_name ?? project.canonical_address,
      canonicalAddress: project.canonical_address,
      city: project.city,
      state: project.state,
      county: project.county,
      market: project.market,
      jurisdiction: jurisdiction
        ? {
            id: jurisdiction.id,
            slug: jurisdiction.slug,
            name: jurisdiction.name,
            displayName: jurisdiction.display_name ?? jurisdiction.name
          }
        : null,
      pipelineStatus: project.pipeline_status,
      developer: project.developer,
      totalUnits: project.total_units,
      dateDelivery: project.date_delivery,
      confidence: project.confidence,
      statusConfidence: project.status_confidence,
      productType: project.product_type,
      rentOrSale: project.rent_or_sale,
      costarSubmarket: project.costar_submarket,
      lat: project.lat,
      lng: project.lng,
      apns: apnsByProject.get(project.id)?.sort() ?? [],
      lastEvidence: latestEvidence
        ? {
            sourceType: latestEvidence.source_type,
            evidenceDate: latestEvidence.evidence_date,
            collectedAt: latestEvidence.collected_at,
            fields: Object.keys(latestEvidence.extracted_fields ?? {}),
            teaser: evidenceTeaser(latestEvidence)
          }
        : null
    };
  });

  return {
    data: {
      projects: pipelineProjects,
      facets: {
        statuses: uniqueSorted(pipelineProjects.map((project) => project.pipelineStatus)),
        markets: uniqueSorted(pipelineProjects.map((project) => project.market)),
        jurisdictions: uniqueSorted(pipelineProjects.map((project) => project.jurisdiction?.displayName)),
        developers: uniqueSorted(pipelineProjects.map((project) => project.developer)).slice(0, 500),
        submarkets: uniqueSorted(pipelineProjects.map((project) => project.costarSubmarket)),
        maxUnits: Math.max(0, ...pipelineProjects.map((project) => project.totalUnits ?? 0))
      }
    },
    error: null
  };
}

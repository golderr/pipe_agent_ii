export type PipelineProject = {
  id: string;
  projectName: string;
  canonicalAddress: string;
  city: string;
  state: string;
  county: string;
  market: string;
  jurisdiction: {
    id: string;
    slug: string;
    name: string;
    displayName: string;
  } | null;
  pipelineStatus: string;
  developer: string | null;
  totalUnits: number | null;
  dateDelivery: string | null;
  confidence: string | null;
  statusConfidence: string | null;
  productType: string | null;
  rentOrSale: string | null;
  costarSubmarket: string | null;
  lat: number | null;
  lng: number | null;
  apns: string[];
  lastEvidence: {
    sourceType: string | null;
    evidenceDate: string | null;
    collectedAt: string | null;
    fields: string[];
    teaser: string | null;
  } | null;
};

export type PipelineData = {
  projects: PipelineProject[];
  facets: {
    statuses: string[];
    markets: string[];
    jurisdictions: string[];
    developers: string[];
    submarkets: string[];
    maxUnits: number;
  };
};

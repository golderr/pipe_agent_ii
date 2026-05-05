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

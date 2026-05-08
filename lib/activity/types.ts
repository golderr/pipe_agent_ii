export type ActivityProjectSummary = {
  id: string;
  project_name: string | null;
  canonical_address: string;
  city: string | null;
  state: string | null;
  zip: string | null;
  pipeline_status: string;
};

export type ActivityArticleSummary = {
  id: string;
  title: string | null;
  url: string;
  source_slug: string | null;
  source_name: string | null;
  fetched_at: string | null;
  published_at: string | null;
};

export type ActivityPermitSummary = {
  permit_number: string | null;
  permit_type: string | null;
  issue_date: string | null;
  address: string | null;
};

export type ActivityIntakeSummary = {
  kind: string;
  label: string | null;
  article: ActivityArticleSummary | null;
  permit: ActivityPermitSummary | null;
};

export type ActivityEvent = {
  id: string;
  event_type: "change" | "resolution" | "agent" | "semantic";
  occurred_at: string;
  project: ActivityProjectSummary | null;
  source: string;
  source_label: string;
  field: string | null;
  field_label: string | null;
  actor_label: string | null;
  title: string;
  summary: string;
  old_value: unknown;
  new_value: unknown;
  change_type: string | null;
  priority: string | null;
  review_item_id: string | null;
  review_item_ids: string[];
  article: ActivityArticleSummary | null;
  intake_summary: ActivityIntakeSummary | null;
  article_fetched_at: string | null;
  agent_created_at: string | null;
  agent_outcome: string | null;
  agent_triggers: string[];
  agent_reasoning_trace: string | null;
  cost_usd: number | null;
  detail: Record<string, unknown>;
};

export type ActivityFeedData = {
  generated_at: string;
  events: ActivityEvent[];
};

export type ActivitySemanticMetric = {
  market: string | null;
  source_slug: string | null;
  source_name: string | null;
  field_name: string;
  field_label: string;
  reason_code: string;
  total_count: number;
  glossary_gap_count: number;
  unmappable_count: number;
  glossary_gap_rate: number;
  unmappable_rate: number;
  reviewer_decision_count: number;
  reviewer_rejection_count: number;
  reviewer_rejection_rate: number | null;
};

export type ActivitySemanticMetricsData = {
  generated_at: string;
  thresholds: Record<string, number>;
  metrics: ActivitySemanticMetric[];
};

export type ActivityQuery = {
  view: string | null;
  eventType: string | null;
  source: string | null;
  field: string | null;
  actor: string | null;
  projectId: string | null;
  market: string | null;
  jurisdiction: string | null;
  from: string | null;
  to: string | null;
};

export type ActivityDataResult =
  | { data: ActivityFeedData; error: null }
  | { data: null; error: string };

export type ActivitySemanticMetricsResult =
  | { data: ActivitySemanticMetricsData; error: null }
  | { data: null; error: string };

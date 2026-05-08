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

export type ActivityEvent = {
  id: string;
  event_type: "change" | "resolution" | "agent";
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

export type ActivityQuery = {
  view: string | null;
  eventType: string | null;
  source: string | null;
  field: string | null;
  actor: string | null;
  projectId: string | null;
  from: string | null;
  to: string | null;
};

export type ActivityDataResult =
  | { data: ActivityFeedData; error: null }
  | { data: null; error: string };

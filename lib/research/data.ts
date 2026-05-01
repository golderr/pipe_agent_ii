import "server-only";

import { requireApiBaseUrl } from "@/lib/env";
import { accessTokenForApi, responseErrorMessage } from "@/lib/server-actions";
import type {
  ResearchArticle,
  ResearchArticleDetailData,
  ResearchExtraction,
  ResearchReference,
  ResearchScrapeJob
} from "@/lib/research/types";

type ResearchArticleDetailResult =
  | { data: ResearchArticleDetailData; error: null; notFound?: false }
  | { data: null; error: string; notFound?: false }
  | { data: null; error: null; notFound: true };

type ResearchArticleApi = {
  id: string;
  news_source_id: string;
  source_name: string;
  url_canonical: string;
  url_original: string;
  fetch_status: string;
  fetch_attempts: number;
  fetched_at: string | null;
  fetch_error_text: string | null;
  http_status: number | null;
  title: string | null;
  byline_author: string | null;
  published_at: string | null;
  publication_section: string | null;
  tags: string[] | null;
  external_article_id: string | null;
  language: string;
  paywall_state: string | null;
  body_text: string | null;
  body_text_hash: string | null;
  raw_html_hash: string | null;
  structural_signals_at: string | null;
  triage_status: string | null;
  triage_at: string | null;
  triage_extraction_id: string | null;
  current_extraction_id: string | null;
  current_extraction_version: number;
  ingest_method: string;
  ingested_by_user_id: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
};

type ResearchScrapeJobApi = {
  id: string;
  jurisdiction_id: string | null;
  kind: string;
  source_name: string;
  target_payload: unknown;
  trigger_type: string;
  initiated_by_user_id: string | null;
  initiated_by_email: string | null;
  status: string;
  queued_at: string;
  started_at: string | null;
  completed_at: string | null;
  source_run_id: string | null;
  error_text: string | null;
  progress: unknown;
};

type ResearchExtractionApi = {
  id: string;
  pass_name: string;
  triggered_by: string;
  prompt_id: string;
  prompt_version: string;
  model: string;
  parse_status: string;
  created_at: string;
};

type ResearchReferenceApi = {
  id: string;
  extraction_id: string;
  reference_index: number;
  candidate_name: string | null;
  candidate_address: string | null;
  candidate_developer: string | null;
  match_status: string;
  matched_project_id: string | null;
};

type ResearchArticleDetailApi = {
  article: ResearchArticleApi;
  scrape_jobs: ResearchScrapeJobApi[];
  extractions: ResearchExtractionApi[];
  references: ResearchReferenceApi[];
};

export async function getResearchArticleData(
  articleId: string
): Promise<ResearchArticleDetailResult> {
  try {
    const apiBaseUrl = requireApiBaseUrl();
    const accessToken = await accessTokenForApi();
    const response = await fetch(`${apiBaseUrl}/research/articles/${articleId}`, {
      headers: { Authorization: `Bearer ${accessToken}` },
      cache: "no-store"
    });

    if (response.status === 404) {
      return { data: null, error: null, notFound: true };
    }
    if (!response.ok) {
      return {
        data: null,
        error: await responseErrorMessage(response, "Research article request failed.")
      };
    }

    const body = (await response.json()) as ResearchArticleDetailApi;
    return {
      data: {
        article: mapArticle(body.article),
        scrapeJobs: body.scrape_jobs.map(mapScrapeJob),
        extractions: body.extractions.map(mapExtraction),
        references: body.references.map(mapReference)
      },
      error: null
    };
  } catch (error) {
    return {
      data: null,
      error: error instanceof Error ? error.message : "Research article request failed."
    };
  }
}

function mapArticle(article: ResearchArticleApi): ResearchArticle {
  return {
    id: article.id,
    newsSourceId: article.news_source_id,
    sourceName: article.source_name,
    urlCanonical: article.url_canonical,
    urlOriginal: article.url_original,
    fetchStatus: article.fetch_status,
    fetchAttempts: article.fetch_attempts,
    fetchedAt: article.fetched_at,
    fetchErrorText: article.fetch_error_text,
    httpStatus: article.http_status,
    title: article.title,
    bylineAuthor: article.byline_author,
    publishedAt: article.published_at,
    publicationSection: article.publication_section,
    tags: article.tags,
    externalArticleId: article.external_article_id,
    language: article.language,
    paywallState: article.paywall_state,
    bodyText: article.body_text,
    bodyTextHash: article.body_text_hash,
    rawHtmlHash: article.raw_html_hash,
    structuralSignalsAt: article.structural_signals_at,
    triageStatus: article.triage_status,
    triageAt: article.triage_at,
    triageExtractionId: article.triage_extraction_id,
    currentExtractionId: article.current_extraction_id,
    currentExtractionVersion: article.current_extraction_version,
    ingestMethod: article.ingest_method,
    ingestedByUserId: article.ingested_by_user_id,
    notes: article.notes,
    createdAt: article.created_at,
    updatedAt: article.updated_at
  };
}

function mapScrapeJob(job: ResearchScrapeJobApi): ResearchScrapeJob {
  return {
    id: job.id,
    jurisdictionId: job.jurisdiction_id,
    kind: job.kind,
    sourceName: job.source_name,
    targetPayload: job.target_payload,
    triggerType: job.trigger_type,
    initiatedByUserId: job.initiated_by_user_id,
    initiatedByEmail: job.initiated_by_email,
    status: job.status,
    queuedAt: job.queued_at,
    startedAt: job.started_at,
    completedAt: job.completed_at,
    sourceRunId: job.source_run_id,
    errorText: job.error_text,
    progress: job.progress
  };
}

function mapExtraction(extraction: ResearchExtractionApi): ResearchExtraction {
  return {
    id: extraction.id,
    passName: extraction.pass_name,
    triggeredBy: extraction.triggered_by,
    promptId: extraction.prompt_id,
    promptVersion: extraction.prompt_version,
    model: extraction.model,
    parseStatus: extraction.parse_status,
    createdAt: extraction.created_at
  };
}

function mapReference(reference: ResearchReferenceApi): ResearchReference {
  return {
    id: reference.id,
    extractionId: reference.extraction_id,
    referenceIndex: reference.reference_index,
    candidateName: reference.candidate_name,
    candidateAddress: reference.candidate_address,
    candidateDeveloper: reference.candidate_developer,
    matchStatus: reference.match_status,
    matchedProjectId: reference.matched_project_id
  };
}

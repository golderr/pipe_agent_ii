"use server";

import { revalidatePath } from "next/cache";
import {
  apiBaseUrlForWrite,
  jsonHeadersForApi,
  responseErrorMessage,
  textFormValue
} from "@/lib/server-actions";
import {
  initialResearchArticleCreateState,
  type ResearchArticleCreateActionState,
  type ResearchArticleCreateFormValues
} from "./state";

type ResearchArticleCreateApiResponse = {
  article_id: string;
  scrape_job_id: string | null;
  status: string;
  existing_article: boolean;
};

type ResearchArticleRetryApiResponse = {
  article_id: string;
  scrape_job_id: string;
  status: string;
  existing_active_job: boolean;
};

export async function createResearchArticleAction(
  _previousState: ResearchArticleCreateActionState,
  formData: FormData
): Promise<ResearchArticleCreateActionState> {
  const form = researchArticleCreateFormValues(formData);
  if (!form.url) {
    return {
      ...initialResearchArticleCreateState,
      message: "Article URL is required.",
      form
    };
  }

  try {
    const apiBaseUrl = await apiBaseUrlForWrite();
    const response = await fetch(`${apiBaseUrl}/research/articles`, {
      method: "POST",
      headers: await jsonHeadersForApi(),
      body: JSON.stringify({
        url: form.url,
        force_project_id: form.forceProjectId || null,
        note: form.note || null
      })
    });

    if (!response.ok) {
      return {
        ...initialResearchArticleCreateState,
        message: await responseErrorMessage(response, "Article ingest failed."),
        form
      };
    }

    const body = (await response.json()) as ResearchArticleCreateApiResponse;
    revalidatePath("/research");
    revalidatePath(`/research/articles/${body.article_id}`);
    return {
      ok: true,
      message: body.existing_article ? "Article already exists." : "Article queued.",
      articleId: body.article_id,
      scrapeJobId: body.scrape_job_id,
      existingArticle: body.existing_article,
      form
    };
  } catch (error) {
    return {
      ...initialResearchArticleCreateState,
      message: error instanceof Error ? error.message : "Article ingest failed.",
      form
    };
  }
}

export async function retryResearchArticleFetchAction(
  articleId: string
): Promise<{ ok: boolean; message: string; scrapeJobId: string | null }> {
  try {
    const apiBaseUrl = await apiBaseUrlForWrite();
    const response = await fetch(`${apiBaseUrl}/research/articles/${articleId}/refetch`, {
      method: "POST",
      headers: await jsonHeadersForApi()
    });

    if (!response.ok) {
      return {
        ok: false,
        message: await responseErrorMessage(response, "Article retry failed."),
        scrapeJobId: null
      };
    }

    const body = (await response.json()) as ResearchArticleRetryApiResponse;
    revalidatePath(`/research/articles/${articleId}`);
    return {
      ok: true,
      message: body.existing_active_job ? "Existing job is still active." : "Article refetch queued.",
      scrapeJobId: body.scrape_job_id
    };
  } catch (error) {
    return {
      ok: false,
      message: error instanceof Error ? error.message : "Article retry failed.",
      scrapeJobId: null
    };
  }
}

function researchArticleCreateFormValues(formData: FormData): ResearchArticleCreateFormValues {
  return {
    url: textFormValue(formData, "url") ?? "",
    forceProjectId: textFormValue(formData, "forceProjectId") ?? "",
    note: textFormValue(formData, "note") ?? ""
  };
}

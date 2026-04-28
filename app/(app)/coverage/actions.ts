"use server";

import { revalidatePath } from "next/cache";
import {
  accessTokenForApi,
  apiBaseUrlForWrite,
  responseErrorMessage
} from "@/lib/server-actions";

const MAX_COSTAR_UPLOAD_BYTES = 50 * 1024 * 1024;

export type ScrapeJobStatus = {
  id: string;
  jurisdictionId: string;
  sourceName: string;
  status: string;
  queuedAt: string;
  startedAt: string | null;
  completedAt: string | null;
  errorText: string | null;
  progress: unknown;
};

export type CoverageActionResult = {
  ok: boolean;
  message: string;
  job?: ScrapeJobStatus;
  upload?: {
    id: string;
    status: string;
    fileName: string;
    rowCount: number | null;
    errorText: string | null;
  };
};

type ScrapeJobApiResponse = {
  id: string;
  jurisdiction_id: string;
  source_name: string;
  status: string;
  queued_at: string;
  started_at: string | null;
  completed_at: string | null;
  error_text: string | null;
  progress: unknown;
};

type CoStarUploadApiResponse = {
  id: string;
  status: string;
  file_name: string;
  row_count: number | null;
  error_text: string | null;
};

export async function enqueueScrapeAction(
  jurisdictionId: string,
  sourceName: string
): Promise<CoverageActionResult> {
  if (!jurisdictionId || !sourceName) {
    return { ok: false, message: "Missing jurisdiction or source." };
  }

  try {
    const apiBaseUrl = await apiBaseUrlForWrite();
    const accessToken = await accessTokenForApi();
    const response = await fetch(`${apiBaseUrl}/coverage/${jurisdictionId}/scrape`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ source_name: sourceName })
    });
    if (!response.ok) {
      return {
        ok: false,
        message: await responseErrorMessage(response, "Could not queue scrape.")
      };
    }

    const job = mapScrapeJob((await response.json()) as ScrapeJobApiResponse);
    revalidatePath("/coverage");
    return { ok: true, message: `${sourceName} scrape queued.`, job };
  } catch (error) {
    return {
      ok: false,
      message: error instanceof Error ? error.message : "Could not queue scrape."
    };
  }
}

export async function getScrapeJobAction(
  jobId: string,
  jurisdictionId: string
): Promise<CoverageActionResult> {
  if (!jobId || !jurisdictionId) {
    return { ok: false, message: "Missing scrape job." };
  }

  try {
    const apiBaseUrl = await apiBaseUrlForWrite();
    const accessToken = await accessTokenForApi();
    const params = new URLSearchParams({ jurisdiction_id: jurisdictionId });
    const response = await fetch(`${apiBaseUrl}/scrape_jobs/${jobId}?${params}`, {
      headers: { Authorization: `Bearer ${accessToken}` },
      cache: "no-store"
    });
    if (!response.ok) {
      return {
        ok: false,
        message: await responseErrorMessage(response, "Could not load scrape job.")
      };
    }
    const job = mapScrapeJob((await response.json()) as ScrapeJobApiResponse);
    if (job.status === "completed" || job.status === "failed" || job.status === "cancelled") {
      revalidatePath("/coverage");
    }
    return { ok: true, message: `Scrape ${job.status}.`, job };
  } catch (error) {
    return {
      ok: false,
      message: error instanceof Error ? error.message : "Could not load scrape job."
    };
  }
}

export async function uploadCostarAction(formData: FormData): Promise<CoverageActionResult> {
  const jurisdictionId = formData.get("jurisdictionId");
  const file = formData.get("file");
  if (typeof jurisdictionId !== "string" || !jurisdictionId || !(file instanceof File)) {
    return { ok: false, message: "Choose a CoStar workbook to upload." };
  }
  if (file.size > MAX_COSTAR_UPLOAD_BYTES) {
    return { ok: false, message: "CoStar uploads must be 50 MB or smaller." };
  }

  try {
    const apiBaseUrl = await apiBaseUrlForWrite();
    const accessToken = await accessTokenForApi();
    const uploadForm = new FormData();
    uploadForm.set("file", file);
    const response = await fetch(`${apiBaseUrl}/coverage/${jurisdictionId}/costar-upload`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${accessToken}`
      },
      body: uploadForm
    });
    if (!response.ok) {
      return {
        ok: false,
        message: await responseErrorMessage(response, "CoStar upload failed.")
      };
    }

    const body = (await response.json()) as CoStarUploadApiResponse;
    const ok = body.status === "completed";
    revalidatePath("/coverage");
    revalidatePath("/pipeline");
    revalidatePath("/dashboard");
    return {
      ok,
      message: ok
        ? `Imported ${body.row_count ?? 0} CoStar rows.`
        : body.error_text || "CoStar upload failed.",
      upload: {
        id: body.id,
        status: body.status,
        fileName: body.file_name,
        rowCount: body.row_count,
        errorText: body.error_text
      }
    };
  } catch (error) {
    return {
      ok: false,
      message: error instanceof Error ? error.message : "CoStar upload failed."
    };
  }
}

function mapScrapeJob(job: ScrapeJobApiResponse): ScrapeJobStatus {
  return {
    id: job.id,
    jurisdictionId: job.jurisdiction_id,
    sourceName: job.source_name,
    status: job.status,
    queuedAt: job.queued_at,
    startedAt: job.started_at,
    completedAt: job.completed_at,
    errorText: job.error_text,
    progress: job.progress
  };
}

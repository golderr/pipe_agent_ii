"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import {
  ArrowLeft,
  ExternalLink,
  FileText,
  RefreshCw,
  RotateCw,
  Rows3,
  Workflow
} from "lucide-react";
import { retryResearchArticleFetchAction } from "../../actions";
import { Button } from "@/components/ui/button";
import type {
  ResearchArticleDetailData,
  ResearchExtraction,
  ResearchReference,
  ResearchScrapeJob
} from "@/lib/research/types";
import { cn } from "@/lib/utils";

type ResearchArticleDetailClientProps = {
  data: ResearchArticleDetailData;
};

const RETRYABLE_FETCH_STATUSES = new Set(["fetch_failed", "parse_failed", "paywalled", "dead_link"]);
const ACTIVE_JOB_STATUSES = new Set(["queued", "running"]);

export function ResearchArticleDetailClient({ data }: ResearchArticleDetailClientProps) {
  const router = useRouter();
  const [message, setMessage] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();
  const { article } = data;
  const latestJob = data.scrapeJobs[0] ?? null;
  const hasActiveJob = data.scrapeJobs.some((job) => ACTIVE_JOB_STATUSES.has(job.status));
  const canRetry = RETRYABLE_FETCH_STATUSES.has(article.fetchStatus);

  function retryFetch() {
    startTransition(async () => {
      const result = await retryResearchArticleFetchAction(article.id);
      setMessage(result.message);
      if (result.ok) {
        router.refresh();
      }
    });
  }

  return (
    <main className="min-h-dvh px-5 py-6">
      <div className="mx-auto max-w-7xl">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <Link
              className="mb-3 inline-flex items-center gap-1 text-sm text-slate-600 hover:text-slate-950"
              href="/research"
            >
              <ArrowLeft className="size-4" aria-hidden="true" />
              Research
            </Link>
            <h1 className="truncate text-xl font-semibold text-slate-950">
              {article.title || article.urlCanonical}
            </h1>
            <div className="mt-2 flex flex-wrap items-center gap-2 text-sm text-slate-500">
              <StatusBadge status={article.fetchStatus} />
              <span>{article.sourceName}</span>
              {article.publishedAt ? <span>{formatDateTime(article.publishedAt)}</span> : null}
              <a
                className="inline-flex items-center gap-1 text-teal-700 hover:text-teal-900"
                href={article.urlCanonical}
                rel="noopener noreferrer"
                target="_blank"
              >
                Source
                <ExternalLink className="size-3.5" aria-hidden="true" />
              </a>
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="outline" onClick={() => router.refresh()}>
              <RefreshCw className="size-4" aria-hidden="true" />
              Refresh
            </Button>
            {canRetry ? (
              <Button disabled={isPending || hasActiveJob} type="button" onClick={retryFetch}>
                <RotateCw className="size-4" aria-hidden="true" />
                {hasActiveJob ? "Job active" : isPending ? "Queueing" : "Retry fetch"}
              </Button>
            ) : null}
          </div>
        </div>

        {message ? (
          <div className="mt-4 rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700">
            {message}
          </div>
        ) : null}

        <section className="mt-5 grid gap-3 md:grid-cols-2 lg:grid-cols-4">
          <Metric label="Fetch" value={label(article.fetchStatus)} />
          <Metric label="Triage" value={label(article.triageStatus)} />
          <Metric label="Attempts" value={String(article.fetchAttempts)} />
          <Metric label="HTTP" value={article.httpStatus ? String(article.httpStatus) : "-"} />
        </section>

        {article.fetchErrorText ? (
          <section className="mt-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">
            <p className="font-medium">Fetch error</p>
            <p className="mt-1 break-words">{article.fetchErrorText}</p>
          </section>
        ) : null}

        <div className="mt-5 grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
          <div className="grid gap-5">
            <Panel icon={FileText} title="Article">
              <dl className="grid gap-3 text-sm md:grid-cols-2">
                <Detail label="Byline" value={article.bylineAuthor} />
                <Detail label="Section" value={article.publicationSection} />
                <Detail label="Fetched" value={formatDateTime(article.fetchedAt)} />
                <Detail label="Paywall" value={label(article.paywallState)} />
                <Detail label="Current extraction" value={shortId(article.currentExtractionId)} />
                <Detail label="Extraction version" value={String(article.currentExtractionVersion)} />
              </dl>
              {article.tags?.length ? (
                <div className="mt-4 flex flex-wrap gap-2">
                  {article.tags.map((tag) => (
                    <span
                      className="rounded border border-slate-200 bg-slate-50 px-2 py-1 text-xs text-slate-600"
                      key={tag}
                    >
                      {tag}
                    </span>
                  ))}
                </div>
              ) : null}
              {article.notes ? (
                <div className="mt-4 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm text-slate-700">
                  {article.notes}
                </div>
              ) : null}
              {article.bodyText ? (
                <p className="mt-4 max-h-[28rem] overflow-auto whitespace-pre-wrap rounded-md border border-slate-200 bg-slate-50 p-3 text-sm leading-6 text-slate-700">
                  {article.bodyText}
                </p>
              ) : (
                <EmptyState label="No article body captured." />
              )}
            </Panel>

            <Panel icon={Rows3} title="Project References">
              {data.references.length ? (
                <div className="overflow-x-auto">
                  <table className="min-w-full text-left text-sm">
                    <thead className="border-b border-slate-200 text-xs uppercase text-slate-500">
                      <tr>
                        <th className="py-2 pr-3 font-medium">Index</th>
                        <th className="px-3 py-2 font-medium">Candidate</th>
                        <th className="px-3 py-2 font-medium">Address</th>
                        <th className="px-3 py-2 font-medium">Developer</th>
                        <th className="px-3 py-2 font-medium">Match</th>
                        <th className="py-2 pl-3 font-medium">Project</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {data.references.map((reference) => (
                        <ReferenceRow key={reference.id} reference={reference} />
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <EmptyState label="No project references extracted." />
              )}
            </Panel>
          </div>

          <aside className="grid content-start gap-5">
            <Panel icon={Workflow} title="Job History">
              {data.scrapeJobs.length ? (
                <div className="grid gap-3">
                  {data.scrapeJobs.map((job) => (
                    <JobCard key={job.id} job={job} latest={job.id === latestJob?.id} />
                  ))}
                </div>
              ) : (
                <EmptyState label="No scrape jobs recorded." />
              )}
            </Panel>

            <Panel icon={FileText} title="Extractions">
              {data.extractions.length ? (
                <div className="grid gap-3">
                  {data.extractions.map((extraction) => (
                    <ExtractionCard extraction={extraction} key={extraction.id} />
                  ))}
                </div>
              ) : (
                <EmptyState label="No extraction passes recorded." />
              )}
            </Panel>
          </aside>
        </div>
      </div>
    </main>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-slate-200 bg-white p-3">
      <p className="text-xs font-medium text-slate-500">{label}</p>
      <p className="mt-1 truncate text-sm font-semibold text-slate-950">{value}</p>
    </div>
  );
}

function Panel({
  children,
  icon: Icon,
  title
}: {
  children: React.ReactNode;
  icon: typeof FileText;
  title: string;
}) {
  return (
    <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
      <div className="mb-4 flex items-center gap-2">
        <Icon className="size-4 text-slate-500" aria-hidden="true" />
        <h2 className="text-sm font-semibold text-slate-950">{title}</h2>
      </div>
      {children}
    </section>
  );
}

function Detail({ label, value }: { label: string; value: string | null }) {
  return (
    <div>
      <dt className="text-xs font-medium text-slate-500">{label}</dt>
      <dd className="mt-1 break-words text-slate-800">{value || "-"}</dd>
    </div>
  );
}

function ReferenceRow({ reference }: { reference: ResearchReference }) {
  return (
    <tr className="align-top">
      <td className="py-2 pr-3 text-slate-600">{reference.referenceIndex}</td>
      <td className="px-3 py-2 font-medium text-slate-900">{reference.candidateName || "-"}</td>
      <td className="px-3 py-2 text-slate-600">{reference.candidateAddress || "-"}</td>
      <td className="px-3 py-2 text-slate-600">{reference.candidateDeveloper || "-"}</td>
      <td className="px-3 py-2">
        <StatusBadge status={reference.matchStatus} />
      </td>
      <td className="py-2 pl-3 text-slate-600">{shortId(reference.matchedProjectId)}</td>
    </tr>
  );
}

function JobCard({ job, latest }: { job: ResearchScrapeJob; latest: boolean }) {
  return (
    <div className="rounded-md border border-slate-200 p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-slate-950">{label(job.kind)}</p>
          <p className="text-xs text-slate-500">{formatDateTime(job.queuedAt)}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {latest ? <span className="text-xs text-slate-500">latest</span> : null}
          <StatusBadge status={job.status} />
        </div>
      </div>
      {job.errorText ? <p className="mt-2 text-sm text-red-700">{job.errorText}</p> : null}
      <ProgressList progress={job.progress} />
    </div>
  );
}

function ProgressList({ progress }: { progress: unknown }) {
  const entries = progressEntries(progress);
  if (!entries.length) {
    return null;
  }
  return (
    <dl className="mt-3 grid gap-2 text-xs">
      {entries.map(([key, value]) => (
        <div className="grid grid-cols-[120px_minmax(0,1fr)] gap-2" key={key}>
          <dt className="font-medium text-slate-500">{label(key)}</dt>
          <dd className="break-words text-slate-700">{formatUnknown(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function ExtractionCard({ extraction }: { extraction: ResearchExtraction }) {
  return (
    <div className="rounded-md border border-slate-200 p-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-slate-950">{label(extraction.passName)}</p>
          <p className="text-xs text-slate-500">{formatDateTime(extraction.createdAt)}</p>
        </div>
        <StatusBadge status={extraction.parseStatus} />
      </div>
      <dl className="mt-3 grid gap-2 text-xs">
        <div className="grid grid-cols-[96px_minmax(0,1fr)] gap-2">
          <dt className="font-medium text-slate-500">Trigger</dt>
          <dd className="break-words text-slate-700">{label(extraction.triggeredBy)}</dd>
        </div>
        <div className="grid grid-cols-[96px_minmax(0,1fr)] gap-2">
          <dt className="font-medium text-slate-500">Prompt</dt>
          <dd className="break-words text-slate-700">
            {extraction.promptId} {extraction.promptVersion}
          </dd>
        </div>
        <div className="grid grid-cols-[96px_minmax(0,1fr)] gap-2">
          <dt className="font-medium text-slate-500">Model</dt>
          <dd className="break-words text-slate-700">{extraction.model}</dd>
        </div>
      </dl>
    </div>
  );
}

function EmptyState({ label }: { label: string }) {
  return <p className="rounded-md border border-dashed border-slate-200 p-3 text-sm text-slate-500">{label}</p>;
}

function StatusBadge({ status }: { status: string | null }) {
  if (!status) {
    return <span className="rounded border border-slate-200 bg-slate-50 px-2 py-0.5 text-xs text-slate-500">-</span>;
  }
  return (
    <span className={cn("rounded border px-2 py-0.5 text-xs font-medium", statusTone(status))}>
      {label(status)}
    </span>
  );
}

function statusTone(status: string) {
  if (["fetched", "completed", "ok", "relevant", "confirmed"].includes(status)) {
    return "border-teal-200 bg-teal-50 text-teal-800";
  }
  if (["queued", "running", "pending"].includes(status)) {
    return "border-blue-200 bg-blue-50 text-blue-800";
  }
  if (["paywalled", "possible_match", "superseded_by_reextraction"].includes(status)) {
    return "border-amber-200 bg-amber-50 text-amber-900";
  }
  if (status.includes("failed") || status === "dead_link" || status === "error") {
    return "border-red-200 bg-red-50 text-red-800";
  }
  return "border-slate-200 bg-slate-50 text-slate-700";
}

function progressEntries(progress: unknown): Array<[string, unknown]> {
  if (!progress || typeof progress !== "object" || Array.isArray(progress)) {
    return [];
  }
  return Object.entries(progress as Record<string, unknown>);
}

function formatUnknown(value: unknown) {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}

function label(value: string | null) {
  if (!value) {
    return "-";
  }
  return value
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function formatDateTime(value: string | null) {
  if (!value) {
    return "-";
  }
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit"
  }).format(new Date(value));
}

function shortId(value: string | null) {
  if (!value) {
    return "-";
  }
  return value.slice(0, 8);
}

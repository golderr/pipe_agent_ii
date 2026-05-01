import { notFound } from "next/navigation";
import { AlertCircle } from "lucide-react";
import { ResearchArticleDetailClient } from "./research-article-detail-client";
import { getResearchArticleData } from "@/lib/research/data";

export const dynamic = "force-dynamic";

type ResearchArticlePageProps = {
  params: Promise<{ articleId: string }>;
};

export default async function ResearchArticlePage({ params }: ResearchArticlePageProps) {
  const { articleId } = await params;
  const result = await getResearchArticleData(articleId);

  if (result.notFound) {
    notFound();
  }

  if (result.error || !result.data) {
    return (
      <main className="px-5 py-6">
        <div className="flex max-w-2xl items-start gap-3 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <div>
            <p className="font-medium">Could not load Research Article.</p>
            <p>{result.error ?? "Research article data was not returned."}</p>
          </div>
        </div>
      </main>
    );
  }

  return <ResearchArticleDetailClient data={result.data} />;
}

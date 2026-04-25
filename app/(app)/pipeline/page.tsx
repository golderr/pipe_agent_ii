import { AlertCircle } from "lucide-react";
import { PipelineClient } from "@/app/(app)/pipeline/pipeline-client";
import { getPipelineData } from "@/lib/pipeline/data";

export const dynamic = "force-dynamic";

export default async function PipelinePage() {
  const { data, error } = await getPipelineData();

  if (error) {
    return (
      <main className="px-5 py-6">
        <div className="flex max-w-2xl items-start gap-3 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <div>
            <p className="font-medium">Could not load Pipeline.</p>
            <p>{error}</p>
          </div>
        </div>
      </main>
    );
  }

  return <PipelineClient data={data} />;
}

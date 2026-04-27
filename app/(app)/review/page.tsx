import { AlertCircle } from "lucide-react";
import { ReviewQueueClient } from "./review-queue-client";
import { getReviewQueueData } from "@/lib/review/data";
import { createSupabaseServerClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

type ReviewPageProps = {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

function firstQueryValue(value: string | string[] | undefined) {
  return Array.isArray(value) ? value[0] : value;
}

export default async function ReviewPage({ searchParams }: ReviewPageProps) {
  const query = searchParams ? await searchParams : {};
  const jurisdictionId = firstQueryValue(query.jurisdiction_id);
  const supabase = await createSupabaseServerClient();
  const {
    data: { user }
  } = await supabase.auth.getUser();
  const result = await getReviewQueueData({ jurisdictionId });

  if (result.error || !result.data || !user) {
    return (
      <main className="px-5 py-6">
        <div className="flex max-w-2xl items-start gap-3 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <div>
            <p className="font-medium">Could not load Review Queue.</p>
            <p>{result.error ?? "Review queue data was not returned."}</p>
          </div>
        </div>
      </main>
    );
  }

  return (
    <ReviewQueueClient
      data={result.data}
      jurisdictionId={jurisdictionId ?? null}
      currentUserId={user.id}
      currentUserEmail={user.email ?? null}
    />
  );
}

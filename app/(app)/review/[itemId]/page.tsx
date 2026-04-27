import { notFound } from "next/navigation";
import { AlertCircle } from "lucide-react";
import { ReviewItemDetailClient } from "./review-item-detail-client";
import { getReviewItemDetailData } from "@/lib/review/data";
import { createSupabaseServerClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

type ReviewItemPageProps = {
  params: Promise<{ itemId: string }>;
};

export default async function ReviewItemPage({ params }: ReviewItemPageProps) {
  const { itemId } = await params;
  const supabase = await createSupabaseServerClient();
  const {
    data: { user }
  } = await supabase.auth.getUser();
  const result = await getReviewItemDetailData(itemId);

  if (result.notFound) {
    notFound();
  }

  if (result.error || !result.data || !user) {
    return (
      <main className="px-5 py-6">
        <div className="flex max-w-2xl items-start gap-3 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <div>
            <p className="font-medium">Could not load Review Item.</p>
            <p>{result.error ?? "Review item data was not returned."}</p>
          </div>
        </div>
      </main>
    );
  }

  return (
    <ReviewItemDetailClient
      data={result.data}
      currentUserId={user.id}
      currentUserEmail={user.email ?? null}
    />
  );
}

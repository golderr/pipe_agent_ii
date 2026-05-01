"use client";

import { useRouter } from "next/navigation";
import { useActionState, useEffect } from "react";
import { Link as LinkIcon, Newspaper, Send } from "lucide-react";
import {
  createResearchArticleAction,
  initialResearchArticleCreateState
} from "./actions";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export function ResearchClient() {
  const router = useRouter();
  const [state, action, pending] = useActionState(
    createResearchArticleAction,
    initialResearchArticleCreateState
  );

  useEffect(() => {
    if (state.ok && state.articleId) {
      router.push(`/research/articles/${state.articleId}`);
    }
  }, [router, state.articleId, state.ok]);

  return (
    <main className="min-h-dvh px-5 py-6">
      <div className="mx-auto max-w-5xl">
        <div className="flex items-center gap-3">
          <div className="flex size-10 items-center justify-center rounded-md border border-teal-200 bg-teal-50 text-teal-800">
            <Newspaper className="size-5" aria-hidden="true" />
          </div>
          <div>
            <h1 className="text-xl font-semibold text-slate-950">News Research</h1>
            <p className="text-sm text-slate-500">Paste-a-link ingest</p>
          </div>
        </div>

        <form action={action} className="mt-5 rounded-md border border-slate-200 bg-white p-4 shadow-sm">
          <div className="grid gap-4">
            <label>
              <span className="text-xs font-medium text-slate-600">Article URL</span>
              <div className="mt-1 flex gap-2">
                <div className="relative min-w-0 flex-1">
                  <LinkIcon
                    className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400"
                    aria-hidden="true"
                  />
                  <Input
                    className="pl-9"
                    defaultValue={state.form.url}
                    maxLength={4000}
                    name="url"
                    placeholder="https://..."
                    required
                    type="url"
                  />
                </div>
                <Button className="shrink-0" disabled={pending} type="submit">
                  <Send className="size-4" aria-hidden="true" />
                  {pending ? "Queueing" : "Queue"}
                </Button>
              </div>
            </label>

            <div className="grid gap-4 md:grid-cols-2">
              <label>
                <span className="text-xs font-medium text-slate-600">Force project ID</span>
                <Input
                  defaultValue={state.form.forceProjectId}
                  name="forceProjectId"
                  placeholder="Optional UUID"
                />
              </label>
              <label>
                <span className="text-xs font-medium text-slate-600">Note</span>
                <Input defaultValue={state.form.note} maxLength={2000} name="note" />
              </label>
            </div>
          </div>

          {state.message ? (
            <div
              className={
                state.ok
                  ? "mt-4 rounded-md border border-teal-200 bg-teal-50 px-3 py-2 text-sm text-teal-800"
                  : "mt-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800"
              }
              role="status"
            >
              {state.message}
            </div>
          ) : null}
        </form>
      </div>
    </main>
  );
}

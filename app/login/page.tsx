import { Building2 } from "lucide-react";
import { signInWithEmail } from "@/app/login/actions";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { safeRedirectPath } from "@/lib/paths";

type LoginPageProps = {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

function firstValue(value: string | string[] | undefined) {
  return Array.isArray(value) ? value[0] : value;
}

function messageForError(error: string | undefined) {
  if (!error) {
    return null;
  }

  if (error === "not_allowed") {
    return "That email is not on the approved access list.";
  }

  if (error === "email_required") {
    return "Enter an email address.";
  }

  return error;
}

export default async function LoginPage({ searchParams }: LoginPageProps) {
  const params = await searchParams;
  const next = safeRedirectPath(firstValue(params.next));
  const sent = firstValue(params.sent) === "1";
  const error = messageForError(firstValue(params.error));

  return (
    <main className="flex min-h-dvh items-center justify-center px-6 py-10">
      <section className="w-full max-w-sm rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
        <div className="mb-6 flex items-center gap-3">
          <div className="flex size-9 items-center justify-center rounded-md bg-teal-700 text-white">
            <Building2 className="size-5" aria-hidden="true" />
          </div>
          <div>
            <h1 className="text-base font-semibold text-slate-950">TCG Pipeline Tracker</h1>
            <p className="text-sm text-slate-500">Research access</p>
          </div>
        </div>

        <form action={signInWithEmail} className="space-y-4">
          <input type="hidden" name="next" value={next} />
          <div className="space-y-2">
            <label className="text-sm font-medium text-slate-700" htmlFor="email">
              Email
            </label>
            <Input id="email" name="email" type="email" autoComplete="email" required />
          </div>
          <Button type="submit" className="w-full">
            Send magic link
          </Button>
        </form>

        {sent ? (
          <p className="mt-4 rounded-md bg-teal-50 px-3 py-2 text-sm text-teal-900">
            Check your email for a sign-in link.
          </p>
        ) : null}
        {error ? (
          <p className="mt-4 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
        ) : null}
      </section>
    </main>
  );
}

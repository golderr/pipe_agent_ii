"use server";

import { redirect } from "next/navigation";
import { isEmailAllowed } from "@/lib/auth";
import { getSiteUrl } from "@/lib/env";
import { safeRedirectPath } from "@/lib/paths";
import { createSupabaseServerClient } from "@/lib/supabase/server";

export async function signInWithEmail(formData: FormData) {
  const email = String(formData.get("email") ?? "")
    .trim()
    .toLowerCase();
  const next = safeRedirectPath(String(formData.get("next") ?? "/coverage"));

  if (!email) {
    redirect("/login?error=email_required");
  }

  if (!isEmailAllowed(email)) {
    redirect("/login?error=not_allowed");
  }

  const supabase = await createSupabaseServerClient();
  const { error } = await supabase.auth.signInWithOtp({
    email,
    options: {
      emailRedirectTo: `${getSiteUrl()}/auth/callback?next=${encodeURIComponent(next)}`
    }
  });

  if (error) {
    redirect(`/login?error=${encodeURIComponent(error.message)}`);
  }

  redirect("/login?sent=1");
}

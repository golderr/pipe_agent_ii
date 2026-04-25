import { redirect } from "next/navigation";
import { AppShell } from "@/components/app-shell";
import { isEmailAllowed } from "@/lib/auth";
import { createSupabaseServerClient } from "@/lib/supabase/server";

export default async function ProtectedLayout({ children }: { children: React.ReactNode }) {
  const supabase = await createSupabaseServerClient();
  const {
    data: { user }
  } = await supabase.auth.getUser();

  if (!user) {
    redirect("/login");
  }

  if (!isEmailAllowed(user.email)) {
    redirect("/login?error=not_allowed");
  }

  return <AppShell>{children}</AppShell>;
}

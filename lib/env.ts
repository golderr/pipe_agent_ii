export function getSupabaseUrl() {
  return process.env.NEXT_PUBLIC_SUPABASE_URL ?? process.env.SUPABASE_URL ?? "";
}

export function getSupabaseAnonKey() {
  return process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? process.env.SUPABASE_ANON_KEY ?? "";
}

export function getSiteUrl() {
  const configuredUrl =
    process.env.NEXT_PUBLIC_SITE_URL ?? process.env.VERCEL_PROJECT_PRODUCTION_URL;

  if (!configuredUrl) {
    if (process.env.NODE_ENV === "production") {
      throw new Error(
        "Site URL missing. Set NEXT_PUBLIC_SITE_URL or VERCEL_PROJECT_PRODUCTION_URL before sending magic links."
      );
    }

    return "http://localhost:3000";
  }

  const rawUrl = configuredUrl.replace(/\/$/, "");

  if (rawUrl.startsWith("http://") || rawUrl.startsWith("https://")) {
    return rawUrl;
  }

  return `https://${rawUrl}`;
}

export function requireSupabaseConfig() {
  const url = getSupabaseUrl();
  const anonKey = getSupabaseAnonKey();

  if (!url || !anonKey) {
    throw new Error(
      "Supabase config missing. Set SUPABASE_URL and SUPABASE_ANON_KEY, or the NEXT_PUBLIC_* equivalents."
    );
  }

  return { url, anonKey };
}

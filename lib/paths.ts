export function safeRedirectPath(value: string | null | undefined, fallback = "/coverage") {
  if (!value || !value.startsWith("/") || value.startsWith("//")) {
    return fallback;
  }

  return value;
}

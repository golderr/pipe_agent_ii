export function safeRedirectPath(value: string | null | undefined, fallback = "/coverage") {
  let decoded: string | null = null;
  try {
    decoded = value ? decodeURIComponent(value) : null;
  } catch {
    return fallback;
  }

  if (!decoded || !decoded.startsWith("/") || decoded.startsWith("//") || decoded.startsWith("/\\")) {
    return fallback;
  }

  return decoded;
}

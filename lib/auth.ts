export function allowedEmails() {
  return (process.env.ALLOWED_EMAILS ?? "")
    .split(",")
    .map((email) => email.trim().toLowerCase())
    .filter(Boolean);
}

export function isEmailAllowed(email: string | null | undefined) {
  if (!email) {
    return false;
  }

  const allowlist = allowedEmails();
  if (allowlist.length === 0) {
    return process.env.NODE_ENV !== "production";
  }

  return allowlist.includes(email.toLowerCase());
}

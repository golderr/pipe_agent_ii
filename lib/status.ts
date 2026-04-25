export const STATUS_STYLES: Record<string, { className: string; color: string }> = {
  "Under Construction": { className: "border-red-200 bg-red-50 text-red-800", color: "#dc2626" },
  Approved: { className: "border-green-200 bg-green-50 text-green-800", color: "#16a34a" },
  Pending: { className: "border-amber-200 bg-amber-50 text-amber-900", color: "#d97706" },
  Proposed: { className: "border-blue-200 bg-blue-50 text-blue-800", color: "#2563eb" },
  Conceptual: { className: "border-violet-200 bg-violet-50 text-violet-800", color: "#7c3aed" },
  Complete: { className: "border-slate-200 bg-slate-50 text-slate-700", color: "#64748b" },
  Stalled: { className: "border-orange-200 bg-orange-50 text-orange-900", color: "#ea580c" },
  Inactive: { className: "border-zinc-200 bg-zinc-50 text-zinc-600", color: "#71717a" }
};

export function statusStyle(status: string) {
  return STATUS_STYLES[status] ?? { className: "border-slate-200 bg-white text-slate-700", color: "#0f766e" };
}

export function compactStatus(status: string) {
  return status === "Under Construction" ? "U/C" : status;
}

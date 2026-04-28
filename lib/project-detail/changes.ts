import type {
  ProjectChangeLogRow,
  ProjectEvidenceFilterOption,
  ProjectNoteHistoryRow,
  ProjectStatusHistoryRow
} from "./types";

export type ProjectChangeQuery = {
  field: string | null;
  source: string | null;
  actor: string | null;
  type: string | null;
  from: string | null;
  to: string | null;
};

export type ProjectChangeFilterOptions = {
  fields: ProjectEvidenceFilterOption[];
  sources: ProjectEvidenceFilterOption[];
  actors: ProjectEvidenceFilterOption[];
  types: ProjectEvidenceFilterOption[];
};

export function hasActiveChangeFilter(query: ProjectChangeQuery) {
  return Boolean(query.field || query.source || query.actor || query.type || query.from || query.to);
}

export function filterChangeRows(rows: ProjectChangeLogRow[], query: ProjectChangeQuery) {
  return rows.filter((row) => {
    if (query.field && row.field !== query.field) {
      return false;
    }
    if (query.source && row.source !== query.source) {
      return false;
    }
    if (query.actor && actorKey(row) !== query.actor) {
      return false;
    }
    if (query.type && row.changeType !== query.type) {
      return false;
    }
    return dateMatches(row.timestamp, query);
  });
}

export function filterNoteRows(rows: ProjectNoteHistoryRow[], query: ProjectChangeQuery) {
  return rows.filter((row) => {
    if (query.field && row.noteType !== query.field) {
      return false;
    }
    if (query.source && row.source !== query.source) {
      return false;
    }
    if (query.actor && actorKey(row) !== query.actor) {
      return false;
    }
    if (query.type && row.source !== query.type) {
      return false;
    }
    return dateMatches(row.createdAt, query);
  });
}

export function filterStatusRows(rows: ProjectStatusHistoryRow[], query: ProjectChangeQuery) {
  return rows.filter((row) => {
    if (query.field && query.field !== "pipeline_status") {
      return false;
    }
    if (query.source && row.source !== query.source) {
      return false;
    }
    if (query.actor) {
      return false;
    }
    if (query.type && query.type !== "status_history") {
      return false;
    }
    return dateMatches(row.statusDate, query);
  });
}

export function buildChangeFilterOptions(
  changeRows: ProjectChangeLogRow[],
  noteRows: ProjectNoteHistoryRow[],
  statusRows: ProjectStatusHistoryRow[]
): ProjectChangeFilterOptions {
  return {
    fields: uniqueOptions([
      ...changeRows.map((row) => ({ value: row.field, label: row.fieldLabel })),
      ...noteRows.map((row) => ({ value: row.noteType, label: row.noteTypeLabel })),
      ...(statusRows.length ? [{ value: "pipeline_status", label: "Status" }] : [])
    ]),
    sources: uniqueOptions([
      ...changeRows.map((row) => ({ value: row.source, label: row.sourceLabel })),
      ...noteRows.map((row) => ({ value: row.source, label: row.sourceLabel })),
      ...statusRows.map((row) => ({ value: row.source, label: row.sourceLabel }))
    ]),
    actors: uniqueOptions([
      ...changeRows.flatMap((row) => actorOption(row)),
      ...noteRows.flatMap((row) => actorOption(row))
    ]),
    types: uniqueOptions([
      ...changeRows.map((row) => ({ value: row.changeType, label: row.changeTypeLabel })),
      ...(noteRows.length ? [{ value: "project_note", label: "Project note" }] : []),
      ...(statusRows.length ? [{ value: "status_history", label: "Status history" }] : [])
    ])
  };
}

function actorOption(row: ProjectChangeLogRow | ProjectNoteHistoryRow) {
  const value = actorKey(row);
  return value ? [{ value, label: row.actorLabel }] : [];
}

function actorKey(row: ProjectChangeLogRow | ProjectNoteHistoryRow) {
  if ("reviewedByEmail" in row) {
    return row.reviewedByEmail ?? row.reviewedBy ?? row.reviewedByUserId ?? row.actorLabel;
  }
  return row.createdByLabel ?? row.createdByUserId ?? row.actorLabel;
}

function dateMatches(value: string | null, query: ProjectChangeQuery) {
  const date = dateKey(value);
  if (query.from && (!date || date < query.from)) {
    return false;
  }
  if (query.to && (!date || date > query.to)) {
    return false;
  }
  return true;
}

function dateKey(value: string | null) {
  return value ? String(value).slice(0, 10) : null;
}

function uniqueOptions(options: ProjectEvidenceFilterOption[]) {
  const byValue = new Map<string, ProjectEvidenceFilterOption>();
  for (const option of options) {
    if (option.value) {
      byValue.set(option.value, option);
    }
  }

  return [...byValue.values()].sort((a, b) => a.label.localeCompare(b.label));
}

import { describe, expect, it } from "vitest";
import {
  buildChangeFilterOptions,
  filterChangeRows,
  filterNoteRows,
  filterStatusRows,
  hasActiveChangeFilter,
  type ProjectChangeQuery
} from "./changes";
import type {
  ProjectChangeLogRow,
  ProjectNoteHistoryRow,
  ProjectStatusHistoryRow
} from "./types";

const emptyQuery: ProjectChangeQuery = {
  field: null,
  source: null,
  actor: null,
  type: null,
  from: null,
  to: null
};

function changeRow(overrides: Partial<ProjectChangeLogRow> = {}): ProjectChangeLogRow {
  return {
    id: "change-1",
    timestamp: "2026-04-27T12:00:00Z",
    source: "inline_override",
    sourceLabel: "Inline override",
    field: "total_units",
    fieldLabel: "Total units",
    oldValue: "100",
    newValue: "120",
    changeType: "researcher_override",
    changeTypeLabel: "Researcher Override",
    priority: "medium",
    reviewedBy: "legacy@example.com",
    reviewedByUserId: "11111111-2222-3333-4444-555555555555",
    reviewedByEmail: "reviewer@example.com",
    actorLabel: "reviewer@example.com",
    reviewItemId: null,
    ...overrides
  };
}

function noteRow(overrides: Partial<ProjectNoteHistoryRow> = {}): ProjectNoteHistoryRow {
  return {
    id: "note-1",
    noteType: "researcher_notes",
    noteTypeLabel: "Researcher notes",
    body: "Called planning desk.",
    createdByUserId: "99999999-2222-3333-4444-555555555555",
    createdByLabel: "notes@example.com",
    actorLabel: "notes@example.com",
    createdAt: "2026-04-28T09:00:00Z",
    source: "project_note",
    sourceLabel: "Project note",
    ...overrides
  };
}

function statusRow(overrides: Partial<ProjectStatusHistoryRow> = {}): ProjectStatusHistoryRow {
  return {
    status: "Approved",
    statusDate: "2026-04-26",
    source: "ladbs_permits",
    sourceLabel: "LADBS permit",
    notes: "Permit issued.",
    ...overrides
  };
}

describe("project detail change helpers", () => {
  it("filters change rows by actor, field, source, type, and date", () => {
    const rows = [
      changeRow(),
      changeRow({
        id: "change-2",
        timestamp: "2026-04-20T12:00:00Z",
        source: "inline_field",
        field: "project_name",
        changeType: "researcher_confirmed",
        reviewedByEmail: "other@example.com",
        actorLabel: "other@example.com"
      })
    ];

    expect(
      filterChangeRows(rows, {
        ...emptyQuery,
        field: "total_units",
        source: "inline_override",
        actor: "reviewer@example.com",
        type: "researcher_override",
        from: "2026-04-27",
        to: "2026-04-28"
      }).map((row) => row.id)
    ).toEqual(["change-1"]);
  });

  it("filters project note history through the same audit query", () => {
    const rows = [
      noteRow(),
      noteRow({
        id: "note-2",
        noteType: "change_notes",
        createdByUserId: "88888888-2222-3333-4444-555555555555",
        createdByLabel: "other@example.com",
        actorLabel: "other@example.com",
        createdAt: "2026-04-20T09:00:00Z"
      })
    ];

    expect(
      filterNoteRows(rows, {
        ...emptyQuery,
        field: "researcher_notes",
        actor: "notes@example.com",
        type: "project_note",
        from: "2026-04-28",
        to: "2026-04-28"
      }).map((row) => row.id)
    ).toEqual(["note-1"]);
  });

  it("keeps status history out of actor-filtered results", () => {
    expect(filterStatusRows([statusRow()], { ...emptyQuery, actor: "reviewer@example.com" })).toEqual([]);
    expect(filterStatusRows([statusRow()], { ...emptyQuery, field: "pipeline_status", type: "status_history" })).toHaveLength(1);
  });

  it("builds filter options from change, note, and status rows", () => {
    const options = buildChangeFilterOptions([changeRow()], [noteRow()], [statusRow()]);

    expect(options.fields).toEqual([
      { value: "researcher_notes", label: "Researcher notes" },
      { value: "pipeline_status", label: "Status" },
      { value: "total_units", label: "Total units" }
    ]);
    expect(options.actors).toContainEqual({ value: "reviewer@example.com", label: "reviewer@example.com" });
    expect(options.actors).toContainEqual({ value: "notes@example.com", label: "notes@example.com" });
    expect(options.types).toContainEqual({ value: "project_note", label: "Project note" });
    expect(hasActiveChangeFilter(emptyQuery)).toBe(false);
    expect(hasActiveChangeFilter({ ...emptyQuery, source: "inline_override" })).toBe(true);
  });
});

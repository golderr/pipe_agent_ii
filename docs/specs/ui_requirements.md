# UI Requirements

Updated: 2026-04-24

This document is the primary design reference for the Phase B and Phase C frontend work. It specifies how the tool looks, feels, and behaves from the researcher's perspective.

Read alongside:

- `ROADMAP.md` — phase plan, milestones, priorities.
- `docs/specs/EVIDENCE_LAYER_DECISIONS.md` — resolution engine and override semantics.
- `docs/specs/data_model_changes.md` — schema changes required to support this UI.
- `docs/specs/field_inventory.md` — per-field classification and MVP editability. Prerequisite for Phase B.
- `docs/specs/review_workflow.md` — backend state machine for staged/committed review decisions.

---

## 1. Principles

Five principles shape every interaction decision in the system. When a design choice is ambiguous, these are the tiebreakers.

1. **Evidence-first.** Every value a researcher sees is backed by a chain of evidence that is at most one gesture away — hover, click, or keyboard shortcut. The differentiator between this tool and any generic CRM is continuous, visible provenance.
2. **Researcher-assistive, not researcher-replacing.** The system surfaces its reasoning, flags its own uncertainty, and asks for decisions where it isn't sure. It never silently overwrites human judgment.
3. **Read-first, write-second.** Most interactions are scans, lookups, and verifications. Write paths are confident but deliberate — staged before committed, reversible within a window.
4. **Keyboard-native.** Researchers come from Excel and live in hotkeys. The keyboard is the primary input; the mouse is a fallback.
5. **Density over whitespace.** Researchers need to see many projects, many evidence rows, many deltas quickly. Aesthetic reference is Linear / Airtable / Retool, not Notion or Figma.

---

## 2. Information Architecture

Four top-level surfaces. Each has a distinct role; they are not filters of the same list.

```
TCG Pipeline Tracker
├─ Coverage        (g j)   Jurisdictions, freshness, kick off scrapes, pick what to review
├─ Review Queue    (g r)   Process pending changes, make decisions, commit
├─ Pipeline        (g p)   Browse all projects, filter, search, look things up
└─ Dashboard       (g d)   Quick glance: counts, anomalies, new activity
```

- **Coverage** — entry point to a work session. "Which jurisdictions am I working today?"
- **Review Queue** — the primary workspace. Batched decisions against staged proposed changes. Commit-to-apply.
- **Pipeline** — exploration / lookup. Not for decisions; for answering "do we have X?" and "what's in Y?"
- **Dashboard** — 30-second situational awareness. Not a reporting tool.

Export, consultant-specific views, and admin surfaces are **out of scope** for Phase B/C and will be specified separately.

---

## 3. Coverage

The entry point for any work session. "Where should I work today?"

### 3.1 Concept

Jurisdictions are real governmental entities (City of Los Angeles, Santa Monica, Beverly Hills). They roll up into markets (Los Angeles County, LA Metro). One market contains many jurisdictions.

Coverage is a filterable table with one row per jurisdiction. Researchers use it to:
- See the freshness of each source per jurisdiction.
- Kick off scrapes (for automated sources) or trigger CoStar uploads (for manual sources).
- See how much is pending review.
- Jump into a review session scoped to one or more jurisdictions.

### 3.2 Layout

```
╭──────────────────────────────────────────────────────────────────────────────╮
│ Coverage                                        [filter…] [pinned only] [+] │
├──────────────────────────────────────────────────────────────────────────────┤
│ 📌 Jurisdiction    State  Market         Projects  U/C  Queue  Deferred  …  │
│ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│ ★ Los Angeles       CA    LA County      1,362    184   ●47      12    >>> │
│ ★ Santa Monica      CA    LA County         97      8   cleared    0    >>> │
│   West Hollywood    CA    LA County         41      3   ●4         1    >>> │
│   Beverly Hills     CA    LA County         22      1   cleared    0    >>> │
│ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│ Last ingested · Gov sources · News sources · CoStar · Last reviewed         │
╰──────────────────────────────────────────────────────────────────────────────╯
```

Each row expands (click or `l`) to show source-level detail and action buttons:

```
╭── ★ Los Angeles — CA — LA County ────────────────────────────────────────── ╮
│  Projects: 1,362   U/C: 184   Queue: 47 pending · 12 deferred              │
│  Last reviewed by you: 2026-04-18 (5 days ago)                             │
│                                                                              │
│  Sources:                                                                    │
│   Gov     LADBS permits     4 sources    last: 2026-04-18 08:00   [Refresh]│
│   Gov     LADBS inspections              last: 2026-04-18 08:00           │
│   Gov     LADBS CofO                     last: 2026-04-18 08:00           │
│   Gov     ZIMAS                          last: 2026-03-30          [Refresh]│
│   News    BizJournals LA                 last: 2026-04-17 23:00   [Refresh]│
│   CoStar  CoStar MF export               last: 2026-04-16 by NG   [Upload] │
│                                                                              │
│  [Enter review session ▸]    [Pin as favorite]                              │
╰──────────────────────────────────────────────────────────────────────────────╯
```

### 3.3 Columns

Default columns in the collapsed table:

- **Pin indicator** (`★`) — user-pinned jurisdictions float to the top by default.
- **Jurisdiction** — display name.
- **State** — two-letter.
- **Market** — the parent market for this jurisdiction.
- **Projects** — total project count.
- **U/C** — count of projects currently in Under Construction status. Chosen because this is the highest-value-per-project state to track.
- **Queue** — count of undecided review items. Rendered with priority color: ●red if any HIGH pending, ◆amber if any MEDIUM but no HIGH, ○gray if only LOW, `cleared` if empty.
- **Deferred** — count of deferred items. Rendered muted. Always non-blocking; shown so deferrals are visible, not hidden.

Optional columns (toggle via a column-picker menu):

- **Last ingested (Gov / News / CoStar)** — timestamp of the most recent ingestion per source class.
- **Last reviewed by you** — last time the current user committed decisions for this jurisdiction.
- **Last reviewed by anyone** — same but across all TCG users.
- **Open overrides** — number of active researcher overrides.

### 3.4 Filters

Filter bar above the table:

- Market (multi-select)
- State
- Queue status (any pending / cleared / high priority only)
- Last-ingested freshness (stale >7d, stale >30d, current)
- Pinned only

Filter state is **session-sticky** — navigating away and back preserves the filter.

### 3.5 Actions per jurisdiction

Phase B renders Coverage as a read-only surface. Refresh, Upload, and Enter review session are part of Phase C and should be hidden or disabled with a tooltip until their backend paths exist. Pinning may be local-only in Phase B; persistent pins arrive with the `user_jurisdiction_pins` table.

- **Refresh (Gov / News sources)** — kicks off scraper jobs for the automated sources registered against this jurisdiction. Shows a spinner; completes asynchronously with a toast notification. Backend enqueues a job; UI polls. See `docs/specs/review_workflow.md` for the backend mechanics.
- **Upload (CoStar only)** — opens a file picker. CoStar is a manual export, not an API pull, so it's a distinct action type. Audit: who uploaded, when, row counts. Displayed in the source row's "last" timestamp.
- **Enter review session** — opens the Review Queue filtered to this jurisdiction. If multiple jurisdictions are selected via checkbox, enters review scoped to all.
- **Pin as favorite** — toggles `★`. Pinned jurisdictions sort above unpinned by default.

### 3.6 Jurisdiction claim indicator (informational)

If another TCG user is actively reviewing a jurisdiction, a small indicator shows:

```
★ Los Angeles       CA    LA County      1,362    184   ●47   NG reviewing…
```

This is advisory only — it does not block another user from also entering review. See §12 Concurrency.

---

## 4. Review Queue

The primary workspace during a review session. Researchers process pending changes (one per project/field), decide each, and commit in batch.

### 4.1 Concept

A change row represents a **field-level conclusion** from the resolution engine that differs from the project's current stored value. One row = one proposed change to one field on one project.

Rows are grouped by project, sorted within a project chronologically by default (optional: group by source). Projects are sorted by priority: those with any HIGH priority items first, then MEDIUM, then LOW.

The queue is transactional: decisions are **staged** as researchers work through rows, then **committed** together. Commit is atomic; uncommitted decisions persist across sessions.

### 4.2 Layout

```
╭──────────────────────────────────────────────────────────────────────────────╮
│ Review — Los Angeles                        47 pending · 12 deferred · 3h   │
├──────────────────────────────────────────────────────────────────────────────┤
│ Filters: ▪Status ▪Units ▪Delivery ▪Dev   min units 50–∞   status: any ▼   │
│ View: ○ flat by time  ○ group by source                                     │
├──────────────────────────────┬───────────────────────────────────────────────┤
│                              │                                               │
│ ▼ HIGH (10 projects)         │  8th & Mariposa            [Open detail ↗] │
│ ● 8th & Mariposa      +3   ← │  3216 W 8th St · 90005 · 🗺                  │
│   [in review]                │  Current: Pending · 150u · Helio/UCLA · …   │
│ ● Melrose Crossing    +2     │                                               │
│ ● Howard Hughes…      +1     │  📝 "Helio pulling out? 3/22 call" — SL 2w  │
│ ● Grayson             +4     │                                               │
│                              │  ─ 3 proposed changes ─────────────────────  │
│ ▼ MEDIUM (22 projects)       │                                               │
│   The Spaulding       +1     │  ① pipeline_status                         │
│   Miles at Highland   +2     │    Pending  →  Approved                    │
│   …                          │    CoStar · 4/16                            │
│                              │    ⚠ single source, Tier 3                  │
│ ▼ LOW (15 projects)          │    [a] Accept new  [s] Keep old             │
│                              │    [d] Defer       [f] Custom               │
│ ▼ DEFERRED (12)              │                                               │
│   830 Wilshire        +1     │  ② date_delivery                            │
│   Alverado Temple     +2     │    2027-01-01  →  2028-07-01                │
│                              │    CoStar · 4/16                            │
│                              │    [a] Accept  [s] Keep  [d] Defer  [f]    │
│                              │                                               │
│                              │  ③ developer                                │
│                              │    Helio/UCLA  →  Beach City Capital        │
│                              │                   [200u] 2 srcs • suggested │
│                              │                   [212u] 1 src              │
│                              │    CoStar · 4/16                            │
│                              │    [a] [s] [d] [f]                          │
│                              │                                               │
│                              │  ─ project-level actions ────────────────   │
│                              │  [Accept all] [Keep all current] [Open ↗]  │
│                              │                                               │
│ j/k nav · a/s/d/f decide · ⏎ open detail · / search · ⌘⏎ commit             │
│                              Commit 0 decisions · queue has 47 undecided   │
╰──────────────────────────────────────────────────────────────────────────────╯
```

### 4.3 Grouping

- **Default grouping: by project.** Each group header shows project name, pending change count, decision state indicator (if any changes are staged), most-recent researcher note inline.
- **Within a project, default: chronological by evidence date.** Most recent first. Puts causally-linked changes near each other (e.g., an LADBS permit on 4/10 immediately followed by a CoStar status refresh on 4/16).
- **Optional "group by source" toggle.** When CoStar refreshes many fields on one project at once, source-grouping makes bulk-accept-by-source faster.

### 4.4 Sorting

- **Projects** sort by their highest-priority pending item:
  - Any HIGH pending → project is HIGH, surfaced to top.
  - Any MEDIUM but no HIGH → project is MEDIUM.
  - Only LOW → project is LOW.
  - All items deferred → project drops to DEFERRED section at bottom.
- **Within a priority tier**, projects sort by count of pending changes (more first), then alphabetically.
- **Within a project**, rows are chronological or source-grouped per the view toggle.
- **Deferred projects** are always at the bottom of the list, with an expandable `▼ DEFERRED` section.

### 4.5 Row content

Each proposed-change row shows:

- **Field name.**
- **Old value → new value.** Clearly diffed. Arrow visible.
- **Source badge of the winning evidence** (and its evidence date).
- **Inline warnings** where the system has flagged its own uncertainty:
  - `⚠ single source, Tier 3` — a low-tier source is the only evidence for a field change.
  - `⚠ canonical target polluted` — developer canonicalization hit a registry row with grab-bag aliases.
  - `⚠ contradicts your override 2026-04-15` — new evidence contradicts an existing researcher override (see §8 review-protected overrides).
  - `⚠ contradicting sources` — multi-candidate scenario.
- **Decision buttons**: `[a] Accept new`  `[s] Keep old`  `[d] Defer`  `[f] Custom`.
- **Decision state badge** (if staged): see §11.

For multi-candidate rows, the Accept button is replaced by value buttons:

```
  total_units
    Current: 150                                         (Pipedream · 2024)
    →  [200] 2 srcs · 1 permit + 1 news · suggested
       [212] 1 src  · 1 news article
       [150] Keep old
       [f]   Custom
```

### 4.6 Project header

Each project group shows:

```
● 8th & Mariposa    +3    [in review]
  📝 latest researcher note, truncated — Sarah · 2w ago
```

- **Priority dot** (color + shape).
- **Project name** (or address if no name).
- **Pending change count** (`+3`).
- **Decision state summary** (if any changes staged):
  - `[in review]` — some staged, some undecided.
  - `[all staged]` — every change has a decision, ready to commit.
  - `[all deferred]` — every change deferred (project drops to DEFERRED section).
- **Latest researcher note preview** — truncated, with author and age. Hover expands to full note history.

### 4.7 Decision buttons

Buttons always appear in the same order with the same labels:

| Key | Label | Action |
|-----|-------|--------|
| `a` | Accept new | Accept the proposed new value. Stages the decision. |
| `s` | Keep old | Reject the change. Writes a researcher "keep current" decision that becomes an override on commit. |
| `d` | Defer | Skip for now. Row moves to the Deferred section at the bottom of the queue. Never auto-expires. |
| `f` | Custom | Open custom-value editor. Researcher enters their own value with optional justification note and source URL. |

Keycaps (`[a]`, `[s]`, etc.) appear on the buttons themselves, showing the shortcut. Label initials do not have to match the key — learnability comes from consistent placement, not mnemonic.

For multi-candidate rows, value buttons replace the Accept button. Additional keys `1`, `2`, `3`… select the nth candidate.

### 4.8 Bulk actions

- **Multi-select:** hold `space` on rows to select; `shift+j`/`shift+k` to extend selection.
- **Bulk accept:** `shift+a` — accepts all selected rows.
- **Bulk keep:** `shift+s` — keeps current values for all selected rows.
- **Bulk defer:** `shift+d`.
- **Project-level bulk accept:** `[Accept all]` button in the project right-pane. Accepts all undecided changes for the focused project in one action.
- **Project-level bulk keep:** `[Keep all current]` button. Same, but rejects every proposed change.
- Bulk custom is intentionally **not offered** — custom values must be per-row, per-field.

### 4.9 Filters

Available filters in the filter bar:

- **Change type** (status / units / delivery / developer / product_type / age_restriction). Multi-select.
- **Minimum units / maximum units.**
- **Current project status** (multi-select).
- **Priority tier** (HIGH / MEDIUM / LOW).
- **Has researcher override** (yes / no / any).

Filters stack. A "clear all filters" control is always visible when any are active.

### 4.10 Commit

- **Commit button** lives in the bottom bar, persistent across scroll.
- Label is dynamic: `Commit 32 decisions · 15 undecided` or `Commit 47 decisions · queue will clear`.
- Commit is keyboard-accessible via `⌘⏎`, with a confirmation modal.
- On successful commit:
  - Staged decisions apply as writes to the project table (values updated or overrides written, per field class).
  - The resolution engine re-runs for affected projects.
  - Committed rows disappear from the active queue.
  - Queue displays a success banner summarizing the commit (e.g., "Committed 32 decisions. 15 items remain in queue.").
- Commit is **atomic** — if any row fails to apply, the entire commit rolls back and staged decisions remain as staged.

### 4.11 Undecided rows at commit

The commit button is **always enabled** regardless of how many rows are staged vs. undecided. Committing when some rows are undecided:

- Commits only the staged rows.
- Undecided rows stay in the queue.
- The queue is not "cleared" until it is empty.
- A "Queue cleared" banner appears in Coverage only when all rows (including deferred ones) have been decided and committed.

### 4.12 Researcher notes on review rows

Each row supports per-row notes visible as a truncated preview with a hover expansion to full history:

```
  ① pipeline_status   Pending → Approved     CoStar · 4/16
     📝 "waiting on planning dept confirmation" — SL · 1h ago
     [a] [s] [d] [f]
```

- Notes are append-only. A new note does not replace the prior one; both are kept.
- Each note has author + timestamp.
- Previous notes accessible via hover on the 📝 icon.
- Notes persist across decisions (a deferred row retains its notes).
- Notes on committed rows remain visible in the ChangeLog / Project Detail for audit.

### 4.13 Reviewed tab

A secondary tab on Review Queue shows **previously committed decisions**:

- Sortable by decision date, decider, project.
- Filterable by field, outcome, decider.
- Each row links to the project's ChangeLog.
- Purpose: audit, "what did I decide last week," spot patterns of mistakes.

---

## 5. Review Item Detail

A full-screen view for a single review item. Used when one row needs deeper examination than the queue's inline hover can provide.

### 5.1 When it opens

- `⏎` or click on a row → opens detail.
- `esc` closes and returns to the queue at the same position.
- `[` and `]` navigate to the previous/next review item without returning to the queue.

### 5.2 Standard layout (single-candidate)

```
╭── Review: 8th & Mariposa · pipeline_status ───────────────────── [esc] ──╮
│                                                                            │
│  Current value                       │  Proposed value                    │
│  ══════════════                      │  ═════════════════                 │
│                                       │                                    │
│  Pending                             │  Approved                          │
│                                       │                                    │
│  Set: 2024-11-05                    │  Rule: highest_status_wins         │
│  Source: Pipedream                   │  Confidence: medium                │
│  Set by: Sarah Lee                   │                                    │
│                                       │                                    │
│  Supporting evidence (2):            │  Supporting evidence (2):          │
│  ─────────────────                   │  ─────────────────                 │
│                                       │                                    │
│  ▸ Pipedream seed  2024-11-05       │  ▸ CoStar  2026-04-16              │
│    Notes: "CPC hearing scheduled"    │    "pipeline_status: Approved"    │
│    [expand raw]                      │    [expand raw] [source URL]      │
│                                       │                                    │
│                                       │  ▸ LADBS permit  2026-04-10       │
│                                       │    PCIS 11010-10000-02451          │
│                                       │    [expand raw] [source URL]      │
│                                       │                                    │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│  Decision:                                                                 │
│    [a] Accept Approved   [s] Keep Pending   [d] Defer   [f] Custom…       │
│                                                                            │
│  Notes:  _                                                                 │
│                                                                            │
│  Previous notes:                                                           │
│    "Helio pulling out? heard 3/22 call" — Sarah Lee · 2w ago              │
│                                                                            │
╰───────────────────────────────────────────────────────────────────────────╯
```

### 5.3 Multi-conclusion layout

When sources disagree on the new value (e.g., one article says 212 units, another says 200), the proposed side splits into panes, one per candidate:

```
╭── Review: Spaulding · total_units ──────────────────────────── [esc] ─╮
│                                                                        │
│  Current value                                                         │
│  ══════════════                                                        │
│  150                                                                   │
│  Source: Pipedream · set 2024-11-05 by Sarah Lee                      │
│                                                                        │
├────────────────────────────────────────────────────────────────────────┤
│  Proposed values (2 candidates)                                        │
│  ══════════════                                                        │
│                                                                        │
│  ┌─ 200  ★ suggested ───────┐  ┌─ 212 ─────────────────────────┐    │
│  │ 2 sources                 │  │ 1 source                        │    │
│  │                           │  │                                  │    │
│  │ ▸ LADBS permit 4/10      │  │ ▸ BizJournals 4/8               │    │
│  │   "200 units total"       │  │   "…the 212-unit project…"     │    │
│  │   [expand raw]            │  │   article passage highlighted   │    │
│  │                           │  │   [view article] [source URL]  │    │
│  │ ▸ CoStar 4/16             │  │                                 │    │
│  │   total_units: 200        │  │                                 │    │
│  │   [expand raw]            │  │                                 │    │
│  │                           │  │                                 │    │
│  │ [1] Accept 200            │  │ [2] Accept 212                  │    │
│  └───────────────────────────┘  └─────────────────────────────────┘    │
│                                                                        │
│  [s] Keep 150    [d] Defer    [f] Custom…                             │
│                                                                        │
╰────────────────────────────────────────────────────────────────────────╯
```

- Each candidate gets its own pane with its supporting evidence.
- The system's suggested candidate is marked with ★ (highlight color + label).
- Number keys (`1`, `2`, `3`) accept the corresponding candidate.
- `s` always means keep current.
- `d`, `f` as usual.

### 5.4 Processed items section

Below the active decision area, a section shows items already decided in this session (staged but not yet committed). As each item is decided, it animates to this section and gray-grays out.

```
─── Decided in this session ─────────────────────────────────────
 ✓ status      Pending → Approved         [Accept]    2m ago   [Revise]
 ✓ units       150 → 200                  [Accept]    1m ago   [Revise]
 ⊘ developer   Helio/UCLA → Beach City…   [Keep old]  30s ago  [Revise]
──────────────────────────────────────────────────────────────────
```

Deferred items have a separate sub-section labelled "Deferred" below the processed section.

Each staged decision has a `[Revise]` action to re-open it. Reviseing a committed decision requires undoing the commit (separate flow).

### 5.5 Project-view link

A button or keyboard shortcut (`p`) from the Review Item view opens the full Project Detail in a right-hand drawer without leaving the review. Useful when a decision depends on broader project context.

---

## 6. Project Detail

The single source of truth for everything the system knows about one project. Accessible from Pipeline (click a row), Review Queue (open detail), Review Item (shortcut `p`), or direct URL.

### 6.1 Concept

Project Detail has four visually distinct sections reflecting the field-class split (see §14):

1. **Core** — evidence-derived fields (status, units, developer, etc.). Edits become researcher overrides.
2. **Identity** — human-authored project identity (project name, previous names, source URLs). Edits write directly.
3. **Notes** — researcher-only text fields (researcher notes, personal notes, change log entries). Direct writes, append-only where applicable.
4. **Relationships** — parent-child project relationships, phase siblings, related-by-address. Direct writes to relationship tables.

### 6.2 Layout

```
╭── 8th & Mariposa ───────────────────────────────── [×] ──╮
│ 3216 W 8th St · 90005 · 🗺                                │
│ ● Low confidence · 12 evidence rows · last 4/16          │
│                                                           │
│ [Snapshot] Evidence · Resolution · Changes · Overrides   │
├───────────────────────────────────────────────────────────┤
│                                                           │
│  ╭ Core ─────────────────────────────────────────────╮  │
│  │ Status           Pending    ↑ [Approved pending]  │  │
│  │                                   CoStar · 4/16   │  │
│  │ Total units      150                   CoStar · ●  │  │
│  │ Affordable       —                              —  │  │
│  │ Market-rate      150                   CoStar · ●  │  │
│  │ Developer        Helio/UCLA          Pipedream · ●│  │
│  │ Product type     Apartment              CoStar · ●│  │
│  │ Delivery         2027-01-01  ↑ [2028-07 pending] │  │
│  │ Age restriction  —                              —  │  │
│  ╰──────────────────────────────────────────────────╯  │
│                                                           │
│  ╭ Identity ────────────────────────────────────────╮  │
│  │ Project name    8th & Mariposa            [edit]  │  │
│  │ Previous names  —                         [add]   │  │
│  │ Source URLs     (0)                       [+]     │  │
│  ╰──────────────────────────────────────────────────╯  │
│                                                           │
│  ╭ Notes ───────────────────────────────────────────╮  │
│  │ Researcher   "Helio pulling out? 3/22 call"       │  │
│  │              — Sarah Lee · 2026-04-01             │  │
│  │              [+ add]                              │  │
│  │ Personal     _                           [add]    │  │
│  │ Change log   "affordable count unclear"           │  │
│  │              — Sarah Lee · 2026-02-12             │  │
│  │              [+ add]                              │  │
│  ╰──────────────────────────────────────────────────╯  │
│                                                           │
│  ╭ Relationships ───────────────────────────────────╮  │
│  │ Master project   —                      [link]    │  │
│  │ Phase siblings   Phase 2 of →           [link]    │  │
│  │ Related nearby   2 projects within 500ft  [view]  │  │
│  ╰──────────────────────────────────────────────────╯  │
╰───────────────────────────────────────────────────────────╯
```

Source-populated direct fields can appear in a compact **Source Facts** section between Core and Identity when there are enough of them to warrant separation. On sparse records, they can stay in Core with a disabled edit affordance. Either way, they are visually distinct from resolver-owned Core fields.

### 6.3 Field rendering

Each field row shows:

- **Field label** (left column).
- **Current value** (middle column). If a staged change exists for this field in the open Review Queue, a compact inline indicator `↑ [new value pending]` appears.
- **Source badge** (right column) — small, subtle, showing origin. Click badge to open the Evidence tab filtered to that field.
- **Hover anywhere on the row** shows a popover:
  - Current value + full source provenance.
  - Any active researcher overrides (mode, set by, set at, note).
  - Evidence rows that support the current value.

### 6.4 Field highlighting — three states

Fields in Project Detail have three visible states, distinguished by background color:

- **Unchanged (default)** — no highlight.
- **In current review batch** — soft yellow/amber background. Indicates this field has a pending review item in the queue.
- **Staged (not yet committed)** — soft green (for staged Accept) or soft blue (for staged Keep/Custom) background. Indicates the researcher has decided but not committed.

This gives an at-a-glance view: when a researcher opens Project Detail in the middle of a review session, they see which fields are in play and which they've already handled.

### 6.5 Inline editing

Inline editing is Phase C behavior. Phase B Project Detail is read-only, but it should render the same field affordance classes so researchers can understand what will become editable later.

In Phase C, Evidence-derived Core fields and Researcher-authored Identity/Notes fields are inline-editable:

- Click a field value → becomes an input.
- Enter saves; Esc cancels.
- Arrow keys navigate between fields.
- Tab moves to the next field.

Edits on **Core** fields are researcher overrides (see §8 Override semantics). The UI surfaces a small ⓘ tooltip on hover: "Your edit holds the value; new evidence that contradicts will trigger a review item."

Edits on **Identity** and **Notes** fields write directly to the project table. The UI surfaces a small ⓘ: "Your edit is the source of truth for this field."

**Source-populated direct** fields are not inline-editable in MVP. They display source badges and hover provenance, but the edit control is disabled with the tooltip: "Managed by source updates in MVP." This prevents researcher edits from being overwritten by the next CoStar or collector refresh.

**Relationships** fields open a separate link/search picker rather than inline text editing.

### 6.6 Tabs

- **Snapshot** (default) — the Core / Source Facts / Identity / Notes / Relationships layout shown above.
- **Evidence** — chronological timeline of every evidence row for this project, grouped by month. Each row is one-line collapsed; expand for full raw data. Filter by field, source, date range.
- **Resolution** — the engine's reasoning per field: rule applied, confidence, evidence ids considered, alternatives considered. For power users and debugging.
- **Changes** — ChangeLog entries for this project (human + system changes).
- **Overrides** — active researcher overrides, mode, set-by, set-at, notes. Superseded overrides shown with a strikethrough and "superseded by…" link.

### 6.7 Evidence tab

```
│  Evidence — 8th & Mariposa    12 rows    [filter ▼]                    │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  April 2026                                                              │
│                                                                          │
│  ▸ 4/16  CoStar   status · units · developer · delivery                  │
│          "Approved · 200u · Beach City Capital · 2028-07"               │
│          [raw] [mark suspect]                                            │
│                                                                          │
│  ▸ 4/10  LADBS inspection   status                                       │
│          Initial Grading · result: Approved · permit: Issued            │
│          [raw] [source URL]                                              │
│                                                                          │
│  ▸ 4/09  LADBS permit   status · units                                   │
│          PCIS 11010-10000-02451 · 200u                                   │
│          [raw] [source URL]                                              │
│                                                                          │
│  March 2026                                                              │
│  ▸ 3/22  News (BizJournals)   developer                                  │
│          "Helio considering pulling out of 8th & Mariposa"              │
│          [full article] [source URL]                                     │
│                                                                          │
│  November 2024                                                           │
│  ▸ 11/5  Pipedream (seed)   all fields (38)                              │
│          [expand]                                                        │
```

- Month dividers for scannability.
- Each row collapses to one line summarizing which fields were updated and a teaser.
- Expand for full raw data.
- `[mark suspect]` flags an evidence row as bad (without affecting the resolution), useful when CoStar clearly has the wrong project.

---

## 7. Pipeline

Exploration / lookup surface. Not for decisions — for answering questions.

### 7.1 Layout

Table or map view, toggled at the top right:

```
Pipeline — Los Angeles        [Table | Map]  [⌘K search]  [+ New project]

Filters: ●UC ●Approved ●Pending   Developer: _   Units: 50–∞   Submarket: any
──────────────────────────────────────────────────────────────────────────
Project           Address          Stat  Dev          Units  Del     Conf
──────────────────────────────────────────────────────────────────────────
Miles at Highland 1410 N Highland  UC    Helio/UCLA    250  2028-03   ●Med
...
```

### 7.2 Table view

- Dense, spreadsheet-feel. Small text, tight rows.
- Sortable columns (click header, shift-click to sort by multiple).
- Filter sidebar (collapsible) with current selections.
- Row hover shows a preview card with key stats and most-recent-evidence teaser.
- Row click opens Project Detail in a drawer.
- `j`/`k` navigate rows, `⏎` opens detail.
- Sticky filter context across navigation.

### 7.3 Map view

- Same row set, same filter state, rendered as pins.
- Pins color-coded by status.
- Cluster at zoom-out.
- Click pin → preview card → click into Project Detail.
- Filter-aware: changing filters re-renders the map.
- Provider: Mapbox GL via react-map-gl.

### 7.4 Search

- `⌘K` opens command palette with fuzzy project search by name, address, or APN.
- `/` focuses the inline search field on the Pipeline surface.

### 7.5 New project creation

- `[+ New project]` opens a creation form.
- Required fields: canonical address, market, jurisdiction.
- On submit: matcher runs. If possible duplicates exist, shows them and asks "create anyway?" or "merge with existing?"
- Successful creation writes a project row with `created_by = 'current_user'` and opens Project Detail for the new project.

### 7.6 Side-by-side project comparison (deferred)

Out of scope for MVP but spec'd here to preserve the requirement. Select 2-5 projects → "Compare" action → opens a side-by-side view of core fields for visual comparison. Useful for "are these phases of the same project?" judgments.

### 7.7 Saved views

- User can save a filter configuration as a named view.
- MVP: local storage only (per-browser).
- Future: persist per-user in the database.

---

## 8. Override Semantics (Review-Protected, Not Sticky)

> This section summarizes how researcher overrides behave. Full detail in `docs/specs/EVIDENCE_LAYER_DECISIONS.md`.

### 8.1 The rule

Researcher inputs are **review-protected, not sticky**.

- A researcher override (inline edit on Core field, Keep-old decision, or Custom decision in Review Queue) writes a value that becomes the project's current value.
- The override **does not** auto-expire.
- The override **does not** silently block newer evidence.
- When newly arriving evidence contradicts the override, a **review item is generated** at minimum MEDIUM priority.
- Until reviewed, the override's value remains displayed as current.

### 8.2 Priority escalation

The priority of the contradiction review item depends on the strength of the new evidence:

- **Strong Tier 1 or multi-source agreement** → HIGH.
- **Single source, any tier, contradicting an existing override** → MEDIUM (minimum).
- Weak sources or small deltas below thresholds → MEDIUM (not LOW — contradicting a human decision is never low-priority).

### 8.3 What "contradiction" means per field

- `pipeline_status`: any evidence implying a different status.
- `total_units`: any evidence with a different value (threshold applies per the `≤5` policy; deltas within threshold do not contradict).
- `developer`: any evidence with a different string (after canonicalization; identical canonicals do not contradict).
- `date_delivery`: any explicit date more than 30 days different, or any article within 6 months suggesting a different delivery (per the 2026-04-23 decision on article priority for delivery).
- Other fields: explicit disagreement.

### 8.4 Display of overrides

Fields with active overrides are shown normally (no special badge in the main flow), but:

- A small `You`/`NG`/`SL` badge on the field source column indicates the override.
- Hovering the field reveals the override: mode (review-protected), set by, set at, note.
- If a contradiction review item is pending, the field gets the standard "in current review batch" highlight (see §6.4).

### 8.5 Clearing an override

- From Project Detail → Overrides tab → `[Clear]` button.
- From the field row → hover → `[Clear override]` in the hover popover.
- Clearing triggers a resolution re-run for the project.
- The cleared override is logged in ChangeLog.

---

## 9. Badge & Color System

Consistent across every surface. Small, subtle, accessible.

### 9.1 Source type badges

Eight categories, each with a color and icon. Badges are short text labels (≤8 chars), ~14px tall, rounded corners, low-saturation fill.

| Badge | Color | Meaning |
|---|---|---|
| `Gov` | **green** | Government / public records: LADBS, ZIMAS, LAHD, LA Case Reports, SM Active Permits, city planning departments, any publicly-scraped governmental source. |
| `News` | **amber** | News articles: BizJournals LA, The Architect, Urbanize LA, Curbed, LA Times, any news source (current or future). |
| `CoStar` | **purple** | CoStar (per TCG internal convention). |
| `Pipedream` | **teal** | Legacy Pipedream spreadsheet entries, pre-tool. Distinct from live TCG researcher input. |
| `You` | **blue + accent dot** | Current user's live-in-tool input. |
| `NG` (initials) | **blue** | Another TCG user's live-in-tool input. Initials are the user's first-name + last-name initials. Hover for full name. |
| `Web` | **slate** | Developer websites. Reserved for future broader "scraped general web" sources; currently only `developer_website` maps here. |
| `—` | **gray** | Computed / derived values (no human or feed source). Examples: `confidence`, `likelihood`, `delivery_year_provenance`. |

### 9.2 Priority indicators

Priority is shown as color + shape + icon, never color alone (for accessibility):

| Priority | Shape | Color | Icon |
|---|---|---|---|
| HIGH | ● filled circle | red | optional `!` |
| MEDIUM | ◆ filled diamond | amber | — |
| LOW | ○ hollow circle | gray | — |
| DEFERRED | ◌ dotted circle | muted gray | clock |

### 9.3 Decision state badges

See §11 for complete decision state vocabulary. Visual:

| State | Visual | Background |
|---|---|---|
| Unreviewed | no badge | default row |
| Staged: Accept new | ✓ green | soft green row tint |
| Staged: Keep old | ✓ neutral | soft blue row tint |
| Staged: Custom | ✎ pencil | soft blue row tint |
| Deferred | ◌ clock | muted, moved to Deferred section |
| Committed | — | removed from active queue; visible in Reviewed tab |

### 9.4 Accessibility

- Every color-coded element also has a shape or text cue.
- WCAG AA contrast minimum on all text-on-color.
- Focus rings on all interactive elements.
- Keyboard path exists for every mouse action.
- Screen reader labels on all icons and badges.

---

## 10. Hover Evidence Patterns

Hovering over any value or source badge opens a popover showing the supporting evidence. Popovers close on mouse-out.

### 10.1 General pattern

```
┌─ Evidence ─────────────────────────────────────┐
│ Field: pipeline_status                         │
│ Value: Approved                                │
│ Supporting: 2 evidence rows                    │
│                                                 │
│ ▸ CoStar · 4/16                                │
│   "pipeline_status: Approved"                  │
│                                                 │
│ ▸ LADBS permit · 4/10                          │
│   PCIS 11010-... · building_permit_issued      │
│                                                 │
│ [view all] [open in detail ↗]                 │
└────────────────────────────────────────────────┘
```

### 10.2 Per source-type snippet rendering

Each source_type has a dedicated snippet renderer that extracts the decision-relevant content from the raw evidence row.

- **Gov (LADBS permit)**: `PCIS {id} · {evidence_type} · permit status: {status}`.
- **Gov (LADBS inspection)**: `{inspection_name} · result: {result} · permit: {permit_status}`.
- **Gov (LADBS CofO)**: `CofO · issued: {cofo_issue_date} · status: {cofo_status}`.
- **News**: the specific article passage that drove the extraction, with the key phrase highlighted. Additional metadata: source name, author, publication date, link to full article. Example:
  > BizJournals · 2026-04-08 · Jane Reporter
  > "…construction on the **310-unit** Helio project is expected to start Q3 2026…"
  > [view article] [view source URL]
- **CoStar**: raw field value as-is. `pipeline_status: "Approved"` or `total_units: 200`. No transformation.
- **Pipedream**: the field value + last-edit metadata if available. `"Approved" · last edited by Sarah Lee · 2024-11-05`.
- **You / TCG user**: the override value + mode + set-by + set-at + note. `"212 units" · set by NG · 2026-04-15 · "confirmed with developer call"`.
- **Web (developer_website)**: the field value + scrape URL + scrape timestamp.
- **Computed**: the rule applied and input values. `Rule: highest_status_wins · inputs: CoStar(Approved), LADBS(permit_issued→Approved)`.

Snippet renderers are implemented as one function per source_type in the backend, called when the UI requests a hover-level view of an evidence row.

### 10.3 Depth of disclosure

Three depths, progressively:

1. **Inline badge** — always visible. Source + date.
2. **Hover popover** — instant, no click. Snippet-level content (per §10.2).
3. **Click / open drawer** — full raw evidence. Includes raw JSON, source URL (if external), source_record_id, collected_at.

Depth 3 never appears on the primary scanning surface. Always a click away, never blocking.

### 10.4 Dismissal

- Mouse-out from the hovered element → popover closes.
- Escape key → popover closes.
- Click outside the popover → popover closes.
- Hovering the popover itself keeps it open (for copying text, clicking links).

---

## 11. Decision State Vocabulary

Every review row has exactly one state at any time. Researchers see a visible indicator of the state so the batch-commit model is legible without explanation.

### 11.1 The five states

1. **Unreviewed** — default. No staged decision. Row shown with no special badge.
2. **Staged: Accept new** — researcher decided to accept the proposed change. Row tinted green; badge `✓ Accept`.
3. **Staged: Keep old** — researcher decided to keep the current value. Row tinted blue; badge `✓ Keep`.
4. **Staged: Custom** — researcher entered a custom value. Row tinted blue; badge `✎ Custom`.
5. **Deferred** — researcher postponed. Row moved to Deferred section at bottom of queue; badge `◌ Deferred`.

After commit, decisions transition to **Committed** — removed from the active queue, visible only in the Reviewed tab and the ChangeLog.

### 11.2 Transitions

- Any state → any other state, until commit. Researchers can change their minds freely pre-commit.
- A staged decision can be revised from the queue (click the row, change decision) or from the Review Item detail view.
- Commit is one-way (from staged → committed). To "undo" a committed decision, use the ChangeLog undo flow (limited time window) or create a new override manually.

### 11.3 Visual summary at project-group level

Project headers in the queue show aggregate state:

- All unreviewed → no badge.
- Some staged, some unreviewed → `[in review]`.
- All staged → `[all staged]` (strong candidate for committing this project).
- All deferred → project drops to the Deferred section.
- Mixed staged + deferred → `[in review · partial]`.

---

## 12. Concurrency Model

### 12.1 Rule

Optimistic last-click-wins. No row-level locking.

### 12.2 What researchers see

- All active users see the same pending-review queue.
- When User A takes a decision on a row:
  - Decision is saved immediately as `staged` in the backend.
  - The row appears as `Staged by A` in everyone's queue.
  - Only User A can revise the decision before commit.
- If User B tried to decide the same row within a narrow window (e.g., 2 seconds):
  - B's decision posts, the backend detects A's prior decision, rejects B's with a 409-equivalent response.
  - B sees a brief banner: "Just decided by A — Accept new. [View decision]"
  - B can dismiss the banner or click to view A's decision. If B disagrees, B can contact A (or, future: add a comment thread — out of scope).

### 12.3 Jurisdiction-claim indicator (informational only)

In Coverage, an indicator shows if another user is actively reviewing a jurisdiction: `NG reviewing…`. This is advisory, not enforced. Useful for 1-3 users to avoid bumping into each other.

### 12.4 Future scaling

If researcher count grows and concurrency becomes painful, options: add row-level soft locking, add hard jurisdiction claims, or add multi-user commit-conflict resolution. None required for MVP.

---

## 13. Author Attribution

### 13.1 In badges

Every user-sourced value is tagged with the author's initials on the badge (e.g., `NG`). The current user's own edits additionally carry a small accent dot (e.g., `NG•` when NG is logged in). Hovering reveals full name + timestamp.

### 13.2 In hover popovers

Every evidence / override / note hover surface shows:

- Author name (full) + title/role if available.
- Timestamp (absolute + relative, e.g., "2026-04-15 · 2 weeks ago").

### 13.3 In ChangeLog entries

Every change (system or human) has an attributed author:

- System changes: attributed as `System · resolution_engine`.
- Scrape-driven changes: `System · scheduled scrape` + `source_name`.
- CoStar uploads: attributed to the uploader.
- Manual overrides / edits: attributed to the editing user.

---

## 14. Field-Class Split

Every field in the data model belongs to one of five classes. The class determines the write path and visual treatment.

### 14.1 The five classes

| Class | Examples | Write path | Visual cue on edit |
|---|---|---|---|
| **Evidence-derived** | `pipeline_status`, `total_units`, `affordable_units`, `workforce_units`, `market_rate_units`, `developer`, `product_type`, `age_restriction`, `date_delivery` | Edit becomes a `researcher_override` via the FastAPI. Resolution engine re-runs. | ⓘ "Your edit holds the value. New contradicting evidence will create a review item." |
| **Source-populated direct** | `rent_or_sale`, `acres`, `retail_sf`, `office_sf`, `hotel_keys`, `stories`, bed-mix percentages, `costar_submarket`, `owner`, `zoning` | Read-only for MVP. Future choice: promote to Evidence-derived or teach source ingesters to respect field overrides. | Disabled edit control: "Managed by source updates in MVP." |
| **Researcher-authored** | `project_name`, `previous_names`, `source_urls`, planner contacts | Direct write to project table via the FastAPI. ChangeLog entry created. | ⓘ "Your edit is the source of truth for this field." |
| **Project notes** | `researcher_notes`, `personal_notes`, `change_notes` | Append-only write to `project_notes` via the FastAPI. ChangeLog entry created. | ⓘ "Notes are append-only." |
| **Relationships** | `master_project`, `phase_siblings`, `related_projects` | Write to `project_relationships` / `project_identifiers` tables. Uses a link picker, not inline text. | ⓘ "Relationship — uses a picker, not inline edit." |
| **Computed** | `canonical_address`, `id`, `created_at`, `confidence`, `likelihood`, `delivery_year_provenance` | Not editable. Displayed with `—` source badge. | Non-interactive. |

The complete 81-field classification is in `docs/specs/field_inventory.md` (prerequisite for Phase B).

### 14.2 Visual treatment

The Project Detail Core / Source Facts / Identity / Notes / Relationships sections visually partition the field classes, so the researcher intuitively understands which edits are overrides (Core), which fields are source-managed and read-only for MVP (Source Facts), which are direct writes (Identity / Notes), and which use relationship operations.

---

## 15. Multi-Candidate Values

### 15.1 When they appear

When the resolution engine sees multiple evidence observations pointing at different values for the same field, the review item presents each as a candidate. Example: article A says 212 units, article B says 200 units.

### 15.2 Presentation

In the Review Queue inline row:

```
total_units
  Current: 150                                       (Pipedream · 2024)
  →  [200] 2 sources • suggested
     [212] 1 source
     [s] Keep 150
     [f] Custom
```

In the Review Item detail: side-by-side panes, one per candidate, each with its own supporting evidence (see §5.3).

### 15.3 System suggestion

The engine computes a single "suggested" candidate based on source count, tier, and recency. The suggested candidate is visually highlighted but **not** auto-selected. The researcher must pick explicitly.

### 15.4 Evidence aggregation

When two or more evidence rows agree on the same value, they are aggregated into one candidate with a count (`2 sources`). Hover the count to see each supporting evidence row.

### 15.5 Keyboard

- `1`, `2`, `3`, … select the nth candidate.
- `s` always keeps current.
- `d` defers.
- `f` opens custom editor.

---

## 16. Notes

### 16.1 Where notes can be attached

- Per review item (note on a specific decision).
- Per project field (note on an override or current value).
- Project-level notes: `researcher_notes`, `personal_notes`, `change_notes` stored as append-only `project_notes` rows.

### 16.2 Append-only semantics

All note fields are append-only. A "new" note does not replace the existing note; both are preserved with author + timestamp.

### 16.3 Inline preview + hover history

Wherever notes are associated with a row or field, the **latest** note is shown inline (truncated to one line), with author and relative age:

```
📝 "Helio pulling out? heard 3/22 call" — SL · 2w ago
```

Hover expands to full note history:

```
┌─ Notes ─────────────────────────────────────────────────┐
│ ▸ "Helio pulling out? heard 3/22 call"                 │
│   Sarah Lee · 2026-04-01 14:32                          │
│                                                          │
│ ▸ "CPC hearing scheduled next week"                     │
│   Sarah Lee · 2026-03-18 09:15                          │
│                                                          │
│ ▸ "Initial Pipedream seed entry"                        │
│   System · 2024-11-05 (Pipedream seed)                  │
└─────────────────────────────────────────────────────────┘
```

### 16.4 Adding a note

- `[+ add]` button at the bottom of the notes section.
- Free text. Markdown not supported in MVP.
- On save: auto-stamped with author + current timestamp.

### 16.5 Edit / delete

- Notes are not editable or deletable in MVP.
- If a researcher wants to correct a prior note, they add a new one ("Correction: earlier note was wrong — actually 212 units per call 3/28").
- Future: consider soft-delete with audit trail.

---

## 17. Keyboard Shortcuts (Reference)

### 17.1 Per-row decisions (Review Queue, Review Item)

| Key | Action |
|---|---|
| `a` | Accept new value |
| `s` | Keep old (current) value |
| `d` | Defer |
| `f` | Custom value — opens editor |
| `1` – `9` | Pick the nth candidate (multi-candidate rows) |

### 17.2 Navigation

| Key | Action |
|---|---|
| `j` / `k` | Next / previous row |
| `h` / `l` | Collapse / expand focused group |
| `space` | Multi-select toggle |
| `shift+j` / `shift+k` | Extend selection up/down |
| `⏎` | Open Review Item detail |
| `esc` | Close drawer / detail view; return to parent |
| `[` / `]` | Previous / next review item (in detail view) |

### 17.3 Bulk actions

| Key | Action |
|---|---|
| `shift+a` | Bulk accept selected |
| `shift+s` | Bulk keep old selected |
| `shift+d` | Bulk defer selected |

### 17.4 Global

| Key | Action |
|---|---|
| `g j` | Go to Coverage |
| `g r` | Go to Review Queue |
| `g p` | Go to Pipeline |
| `g d` | Go to Dashboard |
| `/` | Focus inline search |
| `⌘K` | Command palette |
| `⌘⏎` | Commit all staged decisions (with confirmation) |
| `?` | Show keyboard shortcut reference |

### 17.5 In Project Detail

| Key | Action |
|---|---|
| `e` | Focus the first editable field |
| `tab` | Next field |
| `shift+tab` | Previous field |
| Arrow keys | Navigate fields (when focused but not editing) |
| `⏎` | Begin edit on focused field |
| `esc` | Cancel edit |

### 17.6 Discovery

`?` always opens the keyboard-shortcut reference overlay, showing shortcuts contextual to the current surface.

---

## 18. Dashboard

The smallest surface. Five tiles. Answers "is anything on fire?" in 30 seconds.

```
╭─ Dashboard — Los Angeles ──────────────────────────────────╮
│                                                              │
│ ╭ Needs attention ──────╮  ╭ Pipeline by status ────────╮  │
│ │   47                  │  │ ■■■■■■■ 184 UC             │  │
│ │   review items pending│  │ ■■■■ 96 Approved           │  │
│ │   [go to review]      │  │ ■■■■■ 137 Pending          │  │
│ ╰───────────────────────╯  │ ■■ 62 Complete             │  │
│                             ╰────────────────────────────╯  │
│ ╭ Stalled candidates ───╮                                   │
│ │   12                  │  ╭ Recent activity ──────────╮  │
│ │   no evidence 12+ mo  │  │ 127 evidence rows          │  │
│ │   [review list]       │  │    ingested last 7 days    │  │
│ ╰───────────────────────╯  │ 3 news articles            │  │
│                             │ 2 CoStar refreshes          │  │
│ ╭ Contradictions ────────╮ │ [activity feed]             │  │
│ │   4                    │ ╰─────────────────────────────╯  │
│ │   override vs evidence │                                   │
│ │   [review]             │                                   │
│ ╰───────────────────────╯                                   │
│                                                              │
╰──────────────────────────────────────────────────────────────╯
```

Each tile is a single number + a mini-viz + a click-through. Not a reporting tool; a situational-awareness glance.

Dashboard is out of critical-path for MVP. It ships last in Phase B.

---

## 19. Phase-A UI Requirements (Inherited)

The requirements documented during Phase A validation are incorporated into the sections above. This list is the audit trail mapping each Phase A requirement to its home in this document.

| Phase A requirement (original title) | Where it lives now |
|---|---|
| 1. Status-Change Display (clear SOLE reason, flag for single-source) | §4.5 (review row inline warnings), §10.2 (hover per source), §6.7 (Evidence tab) |
| 2. Delivery Estimation Controls (global + per-export tuning) | Section below (§20) + Phase G export spec (deferred) |
| 3. Units-Change Review UI (side-by-side previous vs. current + evidence) | §5.2 (Review Item standard layout), §5.3 (multi-conclusion) |
| 4. Same-Project Verification UI (mini-map, phase markers, match confidence) | §7.3 (map view), §5.5 (Project-view link from review), and the §5.2 detail layout |
| 5. Evidence-Focused Developer Review (raw + canonical, architecture-firm exceptions) | §4.5 (inline warnings: `⚠ canonical target polluted`), §15 (multi-candidate), §8 (override semantics) |

## 20. Delivery Estimation Controls

Carried forward from Phase A. Needs a UI in the system settings / export configuration flow.

### 20.1 Requirements

- **Global controls:**
  - Operators can adjust default year offsets by status (e.g., Proposed adds 5 years by default).
  - Operators can adjust size-based modifiers.
- **Per-export controls:**
  - An export workflow (Phase G) can choose a more conservative or aggressive assumption set for a specific client engagement, without changing the global default.
- The UI must always distinguish, for any displayed delivery date:
  - Explicit source-provided date
  - Estimated date (from formula)
  - Researcher override date

### 20.2 Implementation note

Externalize delivery-estimation coefficients alongside the planned likelihood YAML work in Phase E.3. UI controls expose those YAML values. Per-export overrides in Phase G write to export-scoped configuration, not global.

---

## 21. Open Items / Deferred

Tracked here so they are not forgotten but are not in MVP scope.

- **Consultant-specific views** — the consultant-facing read-only view of a market for client engagements. Separate spec when we get there.
- **Export views / formats** — per Phase G. Templates must be collected and mapped to system fields before building. The data model must stay export-ready (don't drop fields from the UI just because the engine doesn't resolve them).
- **Saved views per user (persisted)** — MVP uses local storage. Persisted saved views come later.
- **Side-by-side project comparison** — Pipeline action to compare 2-5 projects visually. Not in MVP.
- **Map with evidence pins** — pins showing evidence locations (e.g., multiple permit addresses for one project). Enhancement to map view.
- **Change-density indicator on project rows** — color band on each Pipeline row showing "had N changes this sweep" / "no evidence in 12 months." Enhancement.
- **Activity feed** — a stream of recent changes across projects. Out of scope; dashboard tile is enough for MVP.
- **Comments / @-mentions between users** — not required at 1-3 users. Revisit at scale.
- **Hard jurisdiction locking** — only if concurrent-user pain emerges.
- **Soft-delete for notes** — not required; notes are append-only.
- **Bulk import path** — researcher pastes a list of projects from a meeting. Possible future Phase C extension.
- **Article-aware delivery-date override** — article evidence within 6 months prioritized over CoStar on `date_delivery`. Decision recorded 2026-04-23; implementation lands with Phase D (news).

---

## 22. Accessibility Baseline

- WCAG 2.1 AA contrast ratios on all text.
- Every color-coded element paired with a shape, icon, or text cue.
- Full keyboard navigation: no functionality is mouse-only.
- Focus rings visible on all interactive elements.
- Semantic HTML (nav, main, article, button — not clickable divs).
- Screen reader labels on all icons, badges, and graphics.
- Forms: associated labels, error messages, inline validation.
- No auto-playing content.
- Respect `prefers-reduced-motion` for any animations.
- Tab order matches visual order.

Accessibility is not a Phase B polish pass — it is a constraint on the initial implementation. Each component ships accessible.

---

## 23. Tech Stack (Frontend)

Locked in for Phase B:

- **Framework**: Next.js (app router, React Server Components by default).
- **Language**: TypeScript.
- **Styling**: Tailwind CSS.
- **Components**: shadcn/ui (Tailwind-native, owned-in-repo).
- **Tables**: TanStack Table (headless).
- **Charts**: Tremor (built on Recharts).
- **Maps**: Mapbox GL JS via `react-map-gl`.
- **Command palette**: cmdk.
- **Client state**: Zustand for cross-route state; React Server Components + React state otherwise.
- **Data fetching**: TanStack Query on the client, Server Components for reads, Supabase PostgREST direct for reads, FastAPI for writes.
- **Forms**: react-hook-form + zod.
- **Icons**: Lucide.
- **Auth**: Supabase Auth (magic-link email).
- **Deployment**: Vercel.

Backend for writes: FastAPI on Render. See `ROADMAP.md` Phase C.a and the 2026-04-23 decision log entry.

### 23.1 B.1 integration checks

B.1 must prove one real read path before the rest of Phase B builds on it:

- `/coverage` is protected by Supabase Auth.
- Unauthenticated users are redirected to `/login`.
- Login uses Supabase magic-link email.
- Access is limited to an approved email allowlist.
- Session persistence uses Supabase's default browser persistence; no separate "remember me" control for MVP.
- An authenticated user loads jurisdiction rows through Supabase PostgREST with RLS enforced and sees the rendered Coverage table.

---

## 24. Cross-References

- `ROADMAP.md` — Phase B, Phase C, Decision Log.
- `ARCHITECTURE.md` §3d (Pipedream field inventory), §3e (master project record), §4b (source-by-source analysis), §6 (matching strategy).
- `docs/specs/EVIDENCE_LAYER_INTEGRATION_GUIDE.md` — resolution rules and schema.
- `docs/specs/EVIDENCE_LAYER_DECISIONS.md` — override semantics (review-protected) and all edge-case decisions.
- `docs/specs/data_model_changes.md` — schema migrations this UI requires.
- `docs/specs/field_inventory.md` — 81-field classification; Phase B prerequisite.
- `docs/specs/review_workflow.md` — backend workflow state machine.

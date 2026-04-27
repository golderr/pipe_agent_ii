# Data Model Changes — Phase B/C Prerequisites

Updated: 2026-04-25

This document specifies the schema changes required to support the Phase B and Phase C frontend. It is a prerequisite for UI implementation — none of the UI surfaces in `docs/specs/ui_requirements.md` can ship without these tables and columns in place.

Read alongside:

- `docs/specs/ui_requirements.md` — what the UI needs; this doc explains how the data model supports it.
- `docs/specs/EVIDENCE_LAYER_DECISIONS.md` §22 — review-protected override semantics.
- `docs/specs/review_workflow.md` — backend workflow state machine.

---

## 1. Summary of Changes

Nine changes, in order of priority:

1. **Pre-migration DB snapshot** — take a Supabase point-in-time backup or `pg_dump` before B.0b/B.0c migrations.
2. **Markets as a first-class table** — currently a string slug on `Project`. Jurisdictions roll up into markets.
3. **Jurisdictions as a first-class table** — currently a string field on `Project`. Needed for Coverage and jurisdiction-scoped review sessions.
4. **SourceRun schema expansion** — scope runs per-(jurisdiction, source) for Coverage freshness display.
5. **Read-model views for Phase B** — expose UI-friendly slices such as latest evidence per project without client-side full-table scans.
6. **ReviewItem / ReviewDecision — staged/committed state machine** — the batch-commit UI requires explicit staging.
7. **Contradiction detection as a first-class concern** — new ReviewItemType and supporting columns for override-contradiction review items.
8. **Per-user review state** — tracking last-reviewed-at per (user, jurisdiction) for Coverage's "last reviewed by you" column.
9. **Scrape jobs table** — UI-initiated scrapes need status tracking and auditability.

Each change below includes: the motivation, the schema additions, the migration path from current state, and any backfill considerations.

---

## 2. Jurisdictions Table

### 2.1 Motivation

The Coverage view treats jurisdictions as primary. Current `Project.jurisdiction` is a denormalized string column; distinct jurisdictions cannot be inspected, filtered against, or attached to ancillary data (scrape schedules, review state, source registrations).

### 2.2 Schema

```sql
CREATE TABLE jurisdictions (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug              TEXT NOT NULL,              -- "city_of_los_angeles", "santa_monica"
  name              TEXT NOT NULL,              -- "City of Los Angeles", "Santa Monica"
  display_name      TEXT,                       -- optional override for UI display
  state             CHAR(2) NOT NULL,           -- "CA"
  market_id         UUID NOT NULL REFERENCES markets(id),
  entity_type       TEXT,                       -- "city", "county", "unincorporated_area"
  geom              geography(MULTIPOLYGON),    -- boundary polygon (optional, for map/spatial queries)
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  UNIQUE (state, slug)
);

CREATE INDEX ix_jurisdictions_market_id ON jurisdictions(market_id);
CREATE INDEX ix_jurisdictions_state ON jurisdictions(state);
CREATE INDEX ix_jurisdictions_slug ON jurisdictions(slug);
```

### 2.3 Project relationship

Add a foreign key column on `projects`:

```sql
ALTER TABLE projects ADD COLUMN jurisdiction_id UUID REFERENCES jurisdictions(id);
CREATE INDEX ix_projects_jurisdiction_id ON projects(jurisdiction_id);
```

The existing string column `projects.jurisdiction` is retained during the migration window as a read-only reference. Do not drop it until all ingest, matcher, CLI, and UI reads have migrated to `jurisdiction_id`.

### 2.4 Backfill strategy

1. Create `jurisdictions` table.
2. Seed known jurisdictions from market config slugs. For the current LA dataset, `config/markets/los_angeles.yaml` declares `city_of_los_angeles`; create a jurisdiction row for that slug under the `los_angeles` market row.
3. Backfill existing LA projects where `projects.market = 'los_angeles'` and `projects.city = 'Los Angeles'` to `jurisdiction_id = city_of_los_angeles`.
4. For any non-null `projects.jurisdiction` values, map by normalized slug/name + state and update `jurisdiction_id`.
5. Verify every current `projects.market = 'los_angeles'` / `city = 'Los Angeles'` row has `jurisdiction_id = city_of_los_angeles`.
6. Leave `projects.jurisdiction` in place for compatibility until the migration window closes.

### 2.5 Seeding LA

Initial seed for the LA market. Add to a seed migration or an initial setup script. The first B.0 migration only needs `city_of_los_angeles` to map current production rows; the remaining LA County jurisdictions can be seeded as inactive/no-project coverage rows or added as their collectors come online.

```
Los Angeles County (market; compatibility slug `los_angeles`)
  ├─ City of Los Angeles (`city_of_los_angeles`)
  ├─ Santa Monica
  ├─ West Hollywood
  ├─ Beverly Hills
  ├─ Culver City
  ├─ Long Beach
  └─ Unincorporated LA County
```

Other jurisdictions can be added as coverage expands.

---

## 3. Markets Table

### 3.1 Motivation

Markets are the aggregation unit for jurisdictions. One market contains many jurisdictions. Currently `Project.market` is a string column.

### 3.2 Schema

```sql
CREATE TABLE markets (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug              TEXT NOT NULL UNIQUE,       -- compatibility slug, e.g. "los_angeles"
  name              TEXT NOT NULL,              -- "Los Angeles County", "LA Metro"
  display_name      TEXT,                       -- UI display override
  state             CHAR(2) NOT NULL,
  market_type       TEXT,                       -- "county", "metro", "custom_aggregate"
  parent_market_id  UUID REFERENCES markets(id), -- optional hierarchy (e.g., LA Metro includes LA County)
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_markets_state ON markets(state);
CREATE INDEX ix_markets_parent_market_id ON markets(parent_market_id);
CREATE INDEX ix_markets_slug ON markets(slug);
```

### 3.3 Project relationship

```sql
ALTER TABLE projects ADD COLUMN market_id UUID REFERENCES markets(id);
CREATE INDEX ix_projects_market_id ON projects(market_id);
```

The existing string column `projects.market` is retained during the migration window. Do not drop it until all CLI commands, collectors, matcher queries, and scripts stop filtering on the legacy string slug.

### 3.4 Backfill strategy

1. `INSERT INTO markets (slug, name, display_name, state, market_type) VALUES ('los_angeles', 'Los Angeles County', 'Los Angeles County', 'CA', 'county'), ...`
2. `UPDATE projects SET market_id = markets.id FROM markets WHERE projects.market = markets.slug;`
3. Verify every non-null `projects.market` has a matching `market_id`.
4. Keep the legacy `projects.market = 'los_angeles'` slug stable until the Python pipeline is migrated.

### 3.5 Overlapping markets

The roadmap's Open Question on `ProjectMarketMembership` for overlapping markets remains open. If it is resolved before this migration, implement as a junction table `project_market_memberships` instead of a direct FK. For Phase B, single-market-per-project is sufficient.

---

## 4. SourceRun Schema Expansion

### 4.1 Motivation

Coverage displays per-(jurisdiction, source) freshness: "LADBS permits for LA — last refreshed 4/18 08:00." The current `SourceRun` schema scopes to `(market, source_name)` which is too coarse.

### 4.2 Schema changes

```sql
ALTER TABLE source_runs ADD COLUMN jurisdiction_id UUID REFERENCES jurisdictions(id);
ALTER TABLE source_runs ADD COLUMN trigger_type TEXT NOT NULL DEFAULT 'scheduled';
  -- 'scheduled' | 'user_initiated' | 'backfill'
ALTER TABLE source_runs ADD COLUMN initiated_by_user_id UUID REFERENCES auth.users(id);
  -- non-null only when trigger_type = 'user_initiated'
ALTER TABLE source_runs ADD COLUMN finished_at TIMESTAMPTZ;
ALTER TABLE source_runs ADD COLUMN rows_inserted INTEGER;
ALTER TABLE source_runs ADD COLUMN rows_updated INTEGER;
ALTER TABLE source_runs ADD COLUMN rows_unchanged INTEGER;
ALTER TABLE source_runs ADD COLUMN error_text TEXT;

CREATE INDEX ix_source_runs_jurisdiction_id_source_name ON source_runs(jurisdiction_id, source_name);
CREATE INDEX ix_source_runs_finished_at ON source_runs(finished_at);
```

### 4.3 Backfill

Existing rows get `jurisdiction_id = NULL` and `trigger_type = 'scheduled'`. Coverage must gracefully handle missing jurisdiction scoping on historical rows (show "(historical data — jurisdiction unknown)").

### 4.4 Source registration

A sibling concept: which source_adapters run against which jurisdictions. Currently in the market-config YAML. Formalize as a table:

```sql
CREATE TABLE source_registrations (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  jurisdiction_id   UUID NOT NULL REFERENCES jurisdictions(id),
  source_name       TEXT NOT NULL,
  source_class      TEXT NOT NULL,              -- 'gov' | 'news' | 'costar' | 'web' | 'pipedream_seed'
  active            BOOLEAN NOT NULL DEFAULT TRUE,
  schedule_cron     TEXT,                       -- optional cron expression for scheduled runs
  config            JSONB,                      -- source-specific config
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  UNIQUE (jurisdiction_id, source_name)
);

CREATE INDEX ix_source_registrations_jurisdiction_id ON source_registrations(jurisdiction_id);
```

Coverage reads `source_registrations` to list sources per jurisdiction, and joins against `source_runs` for freshness timestamps.

### 4.5 Seeding for LA

Populate `source_registrations` from the existing `config/markets/los_angeles.yaml` source definitions as part of the migration. Use the YAML `name` values as `source_name` (`ladbs_permits`, `ladbs_inspections`, `lahd_affordable`, `la_case_reports`, `zimas_pdis`, etc.) and map the YAML market/jurisdiction slug to the seeded `jurisdictions.slug`.

---

## 4a. Phase B Read-Model Views

### 4a.1 Motivation

Phase B pages read directly from Supabase PostgREST under RLS. Those reads should be shaped in Postgres when the operation is naturally relational. Pipeline needs the most recent evidence row per project for table previews and drawers; fetching the full `evidence` table and reducing it in the Next.js server component does not scale once news and additional collectors multiply the row count.

### 4a.2 `project_latest_evidence`

```sql
CREATE INDEX ix_evidence_project_latest
ON evidence (
  project_id,
  evidence_date DESC NULLS LAST,
  collected_at DESC,
  id DESC
)
WHERE project_id IS NOT NULL;

CREATE VIEW project_latest_evidence
WITH (security_invoker = true) AS
SELECT DISTINCT ON (project_id)
  project_id,
  id AS evidence_id,
  source_type,
  collected_at,
  evidence_date,
  extracted_fields,
  notes
FROM evidence
WHERE project_id IS NOT NULL
ORDER BY project_id, evidence_date DESC NULLS LAST, collected_at DESC, id DESC;
```

Grant `SELECT` to `authenticated`. The view is read-only and uses `security_invoker` so it respects the same RLS posture as the underlying `evidence` table. B.4 needs a richer per-field provenance read model keyed from `resolution_log`; this latest-evidence view is only the B.3/B.4 preview path, not the field-level provenance source of truth.

### 4a.3 `project_field_resolution`

```sql
CREATE INDEX ix_resolution_log_project_field_latest
ON resolution_log (
  project_id,
  field,
  created_at DESC,
  id DESC
);

CREATE VIEW project_field_resolution
WITH (security_invoker = true) AS
SELECT DISTINCT ON (project_id, field)
  project_id,
  field,
  current_value,
  resolved_value,
  evidence_ids,
  rule_applied,
  confidence,
  created_at
FROM resolution_log
ORDER BY project_id, field, created_at DESC, id DESC;
```

Grant `SELECT` to `authenticated`. Project Detail Snapshot uses this as the field-level read model for source badges, hover provenance, rule labels, and confidence. The view intentionally remains narrow: full evidence timelines and raw evidence expansion are part of B.5.

Snapshot badges should not guess provenance by matching canonical UI field names against source-native `evidence.extracted_fields` keys. If a displayed field has no `project_field_resolution.evidence_ids`, render it as unlinked/system-sourced until the resolver logs field-level evidence or a source-specific provenance mapper is added.

---

## 5. ReviewItem / ReviewDecision — Staged/Committed State

### 5.1 Motivation

The Review Queue uses a batch-commit model. Researchers decide rows, decisions stage, commit applies them atomically. The existing `ReviewItem` and `ReviewDecision` models need explicit state and commit tracking.

### 5.2 ReviewItem — add state column

```sql
ALTER TABLE review_items ADD COLUMN state TEXT NOT NULL DEFAULT 'open';
  -- 'open' | 'staged' | 'committed' | 'invalidated'
```

State transitions:
- `open` → `staged` (when a user records a decision)
- `staged` → `open` (when a user revises a staged decision back to undecided)
- `staged` → `committed` (when the user commits)
- `staged` or `open` → `invalidated` (when new evidence supersedes or another user's commit resolves the field)

### 5.3 ReviewDecision — add fields

```sql
ALTER TABLE review_decisions ADD COLUMN state TEXT NOT NULL DEFAULT 'staged';
  -- 'staged' | 'committed'
ALTER TABLE review_decisions ADD COLUMN decision_type TEXT;
  -- 'accept_new' | 'keep_old' | 'custom' | 'defer' | 'candidate_n'
ALTER TABLE review_decisions ADD COLUMN staged_at TIMESTAMPTZ;
ALTER TABLE review_decisions ADD COLUMN staged_by UUID REFERENCES auth.users(id);
ALTER TABLE review_decisions ADD COLUMN committed_at TIMESTAMPTZ;
ALTER TABLE review_decisions ADD COLUMN committed_by UUID REFERENCES auth.users(id);
ALTER TABLE review_decisions ADD COLUMN decision_value JSONB;
  -- for Custom decisions, stores the user-entered value; also used for multi-candidate selection to record which candidate was chosen
ALTER TABLE review_decisions ADD COLUMN decision_notes TEXT;
ALTER TABLE review_decisions ADD COLUMN source_url TEXT;
  -- optional justification link for Custom decisions

CREATE INDEX ix_review_decisions_state_staged_by ON review_decisions(state, staged_by) WHERE state = 'staged';
```

`review_items.status` remains the legacy item lifecycle (`open`, `accepted`, `rejected`, `deferred`, `auto_accepted`). `review_items.state` is the new queue/staging lifecycle (`open`, `staged`, `committed`, `invalidated`). During migration, both columns may exist; API reads should prefer `state` for Phase C queue behavior.

### 5.4 Decision types

`review_decisions.decision_type` takes these values. The existing `review_decisions.action` enum remains during the migration window for legacy immediate-apply workflows.

- `accept_new` — accept the proposed change.
- `keep_old` — reject; keep current value (writes a researcher override on commit).
- `custom` — user-entered value (writes a researcher override on commit).
- `defer` — postpone. Still creates a decision row but with `state = 'staged'`; the UI renders deferred items in a separate section.
- `candidate_{n}` — for multi-candidate scenarios, the specific candidate index chosen.

### 5.5 Defer semantics

Per `docs/specs/ui_requirements.md` §11, deferred items remain in the queue at the bottom. Implementation:

- A deferred decision has `decision_type = 'defer'` and `state = 'staged'`.
- Coverage queries deferred-count by `WHERE state = 'staged' AND decision_type = 'defer'`.
- When committing, deferred decisions are **not** included in the commit — they stay in the queue.
- Only explicit Accept / Keep / Custom / candidate_n decisions are included in a commit action.
- To un-defer: user revises the decision to Accept / Keep / Custom.

### 5.6 Commit mechanics

A commit action:

1. Selects all `review_decisions` where `state = 'staged'`, `decision_type != 'defer'`, `staged_by = current_user`.
2. Applies each decision in a single transaction:
   - Writes overrides for `keep_old` / `custom`.
   - Updates project fields for `accept_new` (via normal resolution re-run since the accepted value is now the evidence winner).
   - Marks decisions `state = 'committed'`, sets `committed_at` and `committed_by`.
   - Marks corresponding `review_items.state = 'committed'`.
3. Runs `resolve_project(apply=True)` for each affected project.
4. Writes ChangeLog entries.
5. On any failure: rollback. Staged state preserved.

### 5.7 Per-user queue semantics

- Staged decisions are scoped to `staged_by`. User A's staged decisions are visible only to A.
- ReviewItem `state = 'staged'` is set when any user stages a decision (any user's staging makes the item appear as "in review" to everyone), but the decision itself is per-user.
- Edge case: User A stages, User B also tries to stage on the same ReviewItem. B's attempt returns 409. UI shows "Just decided by A."

---

## 6. Contradiction Flags and Review Item Types

### 6.1 Motivation

Per `EVIDENCE_LAYER_DECISIONS.md` §22, overrides are review-protected. New contradicting evidence generates review items. The current `ReviewItem` schema can accommodate this with a new type, but the contradiction state needs supporting columns.

### 6.2 ReviewItemType extension

Add new enum value `override_contradiction` (Python enum name can be `OVERRIDE_CONTRADICTION`). Keep database enum values lowercase to match existing `ReviewItemType` values such as `new_candidate`, `status_change`, and `possible_match`.

### 6.3 ReviewItem schema additions

```sql
ALTER TABLE review_items ADD COLUMN contradicted_override_id UUID REFERENCES researcher_overrides(id);
  -- only non-null for item_type = 'override_contradiction'
  -- Requires the researcher_overrides table migration. If contradiction detection is built
  -- before that migration, store override metadata in payload only and add this FK later.

ALTER TABLE review_items ADD COLUMN contradiction_priority TEXT;
  -- 'high' | 'medium' | (no low; see §22.3)
```

### 6.4 Researcher overrides — promote to table (recommended)

Currently `project.researcher_override` is a JSONB column. For review-protected semantics, it is easier to FK to an explicit table and track per-override history (set-at, set-by, cleared-at, re-affirmed-at, notes per override).

```sql
CREATE TABLE researcher_overrides (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id           UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  field_name           TEXT NOT NULL,
  value                JSONB NOT NULL,
  set_by_user_id       UUID,                    -- Supabase auth.users id; no cross-schema FK in app migration
  set_by_label         TEXT,                    -- legacy initials / system actor when no auth user exists
  set_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  reaffirmed_at        TIMESTAMPTZ,
  cleared_at           TIMESTAMPTZ,
  cleared_by_user_id   UUID,                    -- Supabase auth.users id; no cross-schema FK in app migration
  note                 TEXT,
  source_url           TEXT,
  mode                 TEXT,                    -- still honored until §22 contradiction handling replaces supersession
  baseline             JSONB,                   -- still honored with mode until §22 contradiction handling lands
  created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_researcher_overrides_project_id_active ON researcher_overrides(project_id) WHERE cleared_at IS NULL;
CREATE UNIQUE INDEX uq_researcher_overrides_active_field ON researcher_overrides(project_id, field_name) WHERE cleared_at IS NULL;
```

Migration: extract current JSONB overrides into rows in this table. During C.c, keep the legacy `projects.researcher_override` JSONB column synchronized for existing read-only UI surfaces and verification. Drop the JSONB column only after the table-backed write/read paths are verified. Resolution engine reads active rows from the new table first, with a legacy JSONB fallback during the transition.

### 6.5 Contradiction detection

The detection service runs in two triggers:

1. **After evidence ingest** (collector finishes writing rows): for every project with an active override, check whether any newly-ingested evidence contradicts per §22.2 thresholds. If yes, create `override_contradiction` review items.
2. **After resolution re-run** (any path that calls `resolve_project`): confirm overrides are still consistent with newest evidence; create review items for new contradictions not already tracked.

The service produces at most one open `override_contradiction` review item per `(project_id, field_name)`. If one already exists and is `open` or `staged`, don't duplicate.

### 6.6 Contradiction review item lifecycle

- User accepts new → override cleared + committed; review item committed.
- User keeps old → override reaffirmed (timestamp bumps); review item committed.
- User defers → review item stays in the queue at the bottom.
- User custom → new override value written; review item committed.

---

## 7. Per-User Review State

### 7.1 Motivation

Coverage shows "Last reviewed by you: 5 days ago" per jurisdiction. Requires tracking last-commit-per-(user, jurisdiction).

### 7.2 Schema

```sql
CREATE TABLE user_jurisdiction_reviews (
  user_id                UUID NOT NULL REFERENCES auth.users(id),
  jurisdiction_id        UUID NOT NULL REFERENCES jurisdictions(id),
  last_committed_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  commit_count           INTEGER NOT NULL DEFAULT 0,
  decisions_committed    INTEGER NOT NULL DEFAULT 0,

  PRIMARY KEY (user_id, jurisdiction_id)
);

CREATE INDEX ix_user_jurisdiction_reviews_jurisdiction_id ON user_jurisdiction_reviews(jurisdiction_id);
```

### 7.3 Update triggers

After a commit:

```sql
INSERT INTO user_jurisdiction_reviews (user_id, jurisdiction_id, commit_count, decisions_committed)
VALUES (:user_id, :jurisdiction_id, 1, :n_decisions)
ON CONFLICT (user_id, jurisdiction_id) DO UPDATE SET
  last_committed_at = NOW(),
  commit_count = user_jurisdiction_reviews.commit_count + 1,
  decisions_committed = user_jurisdiction_reviews.decisions_committed + EXCLUDED.decisions_committed;
```

### 7.4 Pinning jurisdictions

Separate small table for pinned favorites:

```sql
CREATE TABLE user_jurisdiction_pins (
  user_id          UUID NOT NULL REFERENCES auth.users(id),
  jurisdiction_id  UUID NOT NULL REFERENCES jurisdictions(id),
  pinned_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  PRIMARY KEY (user_id, jurisdiction_id)
);
```

---

## 8. Scrape Jobs Table

### 8.1 Motivation

Coverage's `[Refresh]` button kicks off scrapes. These are asynchronous — the UI needs to poll for completion and surface success/failure. The CoStar `[Upload]` button is different (immediate server-side ingestion, not a queued job), but audit tracking is similar.

### 8.2 Schema

```sql
CREATE TABLE scrape_jobs (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  jurisdiction_id       UUID NOT NULL REFERENCES jurisdictions(id),
  source_name           TEXT NOT NULL,
  trigger_type          TEXT NOT NULL,      -- 'user_initiated' | 'scheduled'
  initiated_by_user_id  UUID REFERENCES auth.users(id),
  status                TEXT NOT NULL DEFAULT 'queued',
                                             -- 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
  queued_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  started_at            TIMESTAMPTZ,
  completed_at          TIMESTAMPTZ,
  source_run_id         UUID REFERENCES source_runs(id),
  error_text            TEXT,
  progress              JSONB,              -- optional progress tracking: {"rows_processed": 1234, "total": 5000}

  CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled'))
);

CREATE INDEX ix_scrape_jobs_jurisdiction_id_status ON scrape_jobs(jurisdiction_id, status);
CREATE INDEX ix_scrape_jobs_status_queued_at ON scrape_jobs(status, queued_at) WHERE status IN ('queued', 'running');
```

### 8.3 CoStar upload tracking

```sql
CREATE TABLE costar_uploads (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  jurisdiction_id       UUID NOT NULL REFERENCES jurisdictions(id),
  uploaded_by_user_id   UUID NOT NULL REFERENCES auth.users(id),
  uploaded_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  file_name             TEXT NOT NULL,
  file_size_bytes       BIGINT,
  row_count             INTEGER,
  source_run_id         UUID REFERENCES source_runs(id),
  status                TEXT NOT NULL DEFAULT 'processing',
                                             -- 'processing' | 'completed' | 'failed'
  error_text            TEXT
);

CREATE INDEX ix_costar_uploads_jurisdiction_id ON costar_uploads(jurisdiction_id);
```

### 8.4 Job queue implementation

Backend options:
- **Lightweight**: RQ (Redis Queue) with a background worker on Render.
- **Simpler still**: Supabase pg_cron + a poller Python script for Phase B. Acceptable when job frequency is low.
- **Heavier**: Celery + Redis — not needed at current scale.

Recommend RQ for Phase B. One worker process running on Render, consuming `scrape_jobs` queued jobs. UI polls `GET /scrape_jobs/{id}` every 2-5 seconds while status is `queued` or `running`.

---

## 9. Review Decision Notes (Append-Only)

### 9.1 Motivation

Per `ui_requirements.md` §16, review-row notes are append-only. Each note has author + timestamp. Preserve all notes across the decision's lifecycle.

### 9.2 Schema

```sql
CREATE TABLE review_decision_notes (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  review_item_id      UUID NOT NULL REFERENCES review_items(id) ON DELETE CASCADE,
  author_user_id      UUID NOT NULL REFERENCES auth.users(id),
  note_text           TEXT NOT NULL,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_review_decision_notes_review_item_id_created_at ON review_decision_notes(review_item_id, created_at DESC);
```

Notes are never updated or deleted; corrections are new rows. The UI shows the most recent note inline, with hover expansion to full history.

---

## 10. Project-Level Notes (Extensions to Existing Fields)

### 10.1 Current state

`projects` already has:
- `researcher_notes` TEXT
- `personal_notes` TEXT
- `change_notes` TEXT

These are single-string fields. Under the new append-only note model (§16 of ui_requirements), they need to support multiple notes with authors + timestamps.

### 10.2 Option A: Convert to child tables

```sql
CREATE TABLE project_notes (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id        UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  note_type         TEXT NOT NULL,      -- 'researcher' | 'personal' | 'change_log'
  author_user_id    UUID NOT NULL REFERENCES auth.users(id),
  note_text         TEXT NOT NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_project_notes_project_id_type_created_at ON project_notes(project_id, note_type, created_at DESC);
```

Migrate existing `researcher_notes`, `personal_notes`, `change_notes` strings into `project_notes` rows with a synthetic author (e.g., a system user ID or NULL) and `created_at = projects.created_at`. Drop the original columns.

### 10.3 Option B: Keep existing columns + add history

Keep `projects.researcher_notes` etc. as the "latest note" display value, and add `project_note_history` for the append-only audit. More redundant but backward-compatible.

**Recommend Option A** — cleaner, matches the append-only model in the UI, no double-storage.

### 10.4 Field-level notes

Per `ui_requirements.md` §16, notes can also be attached per project field. These go in the researcher override's `note` column (single string; if we want multiple notes per override, add a `researcher_override_notes` child table analogous to `review_decision_notes`).

For MVP, single note per override is acceptable. Promote to a child table if usage demands.

---

## 11. Source Snippet Renderer Support

### 11.1 Motivation

Per `ui_requirements.md` §10.2, each source_type has a dedicated snippet renderer producing human-readable content from the raw evidence row. This is backend code, not a schema change, but may benefit from caching.

### 11.2 Optional caching schema

If rendering snippets on every hover is too slow (news articles with passage extraction, for example), cache the rendered snippets:

```sql
ALTER TABLE evidence ADD COLUMN snippet_cached JSONB;
  -- {field_name: rendered_snippet, ...}
ALTER TABLE evidence ADD COLUMN snippet_cached_at TIMESTAMPTZ;
```

Populated by a background job after each evidence insert, or lazily on first hover. Not required for MVP — can render on-the-fly initially.

---

## 12. Migration Ordering

Proposed migration order (one Alembic migration per section where feasible):

Use timestamped, ordered Alembic revision names so the Phase B prerequisite stack stays reviewable, e.g. `2026_04_24_0001_create_markets`, `2026_04_24_0002_create_jurisdictions`, etc. Keep each revision focused on one numbered step below unless two steps must share a transaction.

0. Take a DB snapshot / backup immediately before running the first migration.
1. Create `markets` table + backfill from `projects.market` using `markets.slug`.
2. Create `jurisdictions` table + backfill current LA projects to `city_of_los_angeles`; add FKs on `projects`.
3. Create `source_registrations` + seed from `config/markets/los_angeles.yaml` using each source's jurisdiction slug.
4. Extend `source_runs` with jurisdiction_id and new columns.
5. Create Phase B read-model views, beginning with `project_latest_evidence`.
6. Promote `researcher_override` JSONB to `researcher_overrides` table + backfill.
7. Extend `review_items` and `review_decisions` with state/staging columns.
8. Create `review_decision_notes`.
9. Create `user_jurisdiction_reviews` and `user_jurisdiction_pins`.
10. Create `scrape_jobs` and `costar_uploads`.
11. Convert project notes to `project_notes` table.
12. (Optional) Add snippet caching columns on `evidence`.

Each migration is backward-compatible individually. Existing string columns such as `projects.market` and `projects.jurisdiction` stay in place until code paths have migrated; a later cleanup migration may drop deprecated columns only after that verification.

---

## 13. Cross-References

- `docs/specs/ui_requirements.md` — UI spec that depends on these changes.
- `docs/specs/EVIDENCE_LAYER_DECISIONS.md` §22 — review-protected override model requiring §6.
- `docs/specs/review_workflow.md` — backend workflow using the staged/committed state machine in §5.
- `docs/specs/field_inventory.md` — field classifications that determine write paths.
- `ROADMAP.md` — phase scheduling for these migrations.

# Phase D — News Scraping & Deep Research Design

> **Status:** Design — implementation contract for ROADMAP.md Phase D items D.1–D.B. Additional sources, paid-source capability, advanced fetch, auto-apply, and cross-source corroboration ("deep research") are explicitly deferred to D-late or later phases.
>
> **Audience:** Engineers implementing Phase D. Researchers evaluating the system's behavior. A future contributor joining the team six months from now and asking "what is Phase D and why is it shaped this way?"
>
> **Last updated:** 2026-05-01 (D.2b advanced-fetch guardrail implementation — see Revision History)
> **Owner:** Nate Goldstein (researcher), pipeline maintainers (engineering)
>
> **Read alongside:**
> - `ROADMAP.md` Phase D
> - `ARCHITECTURE.md` §5 (collection workflow), §6 (matching strategy)
> - `docs/specs/data_model_changes.md` (schema/migration patterns, RLS conventions, Phase B/C tables)
> - `docs/specs/review_workflow.md` (review queue state machine — news evidence flows through this)
> - `docs/specs/EVIDENCE_LAYER_DECISIONS.md` (especially §6 extracted_fields shape, §11 unmatched evidence, §22 review-protected overrides, §21f recent-article delivery-date priority)
> - `docs/specs/ui_requirements.md` §10.2 (per-source snippet renderers)
> - `docs/ops/backend_api.md` (FastAPI write-path conventions)
> - `config/source_tiers.yaml` (Tier 2 = `news_article`)

---

## 0. Revision History

### D.2-docs Urbanize pivot revision - 2026-05-01

This revision makes the active Phase D scheduled-source design Urbanize-first
instead of BizJournals-first.

Active Phase D source posture:

- `urbanize_la` is the only live scheduled-source pilot for D.2a/D.6.
- `urbanize_la` is seeded as market-unscoped (`market_id = NULL`,
  `jurisdiction_id = NULL`). The matcher decides whether each reference belongs
  to an active market or should remain discarded/new-candidate signal.
- The unscoped branch in `news_matcher` project scoping is load-bearing. Do not
  replace it with source-specific `if market_slug == ...` maps.
- `bizjournals_la` remains mapped to `news_article`, but is inactive and
  unscheduled until D.late.C ships paid-source capability.
- D.B prices the full Urbanize 12-month URL set observed in D.2v (988 URLs with
  `lastmod >= 2025-05-01`), not an LA-filtered subset.

D.2a/D.6 gates added by this revision:

- D.2a added the seeded `urbanize_la` source and host-routing path. D.2v used
  `news_paste_a_link`, so D.6 must rerun those five URLs in staging through
  `urbanize_la` before enabling cron.
- Before D.6 enables the cron, rerun those five URLs in staging with an Anthropic
  key and verify Haiku triage plus Opus extraction/integration output.
- Pass 1 tuning for comma-formatted unit counts, title/headline-only addresses,
  and completion-date phrases is promoted to explicit D.2a-prep work before
  high-volume backfill.
- The initial cron candidate is `30 7 * * *` in `America/Los_Angeles`, roughly
  75-90 minutes after the observed Urbanize RSS publication window
  (`06:00`-`06:15` PT). D.6 may add jitter around that configured time.

### D.2v source validation addendum - 2026-04-30

Urbanize LA source validation confirmed the Phase D scheduled-source pivot. Detailed
runbook notes are in `docs/sources/news/urbanize_la.md`.

Findings:

- `https://la.urbanize.city/robots.txt` returned HTTP 200. Article paths,
  `/rss.xml`, `/sitemap.xml`, and neighborhood pages were not disallowed for a
  normal project-identifying user agent. `/search/` is disallowed and should not
  be crawled.
- `https://la.urbanize.city/rss.xml` returned HTTP 200 with 10 current items.
  The validation snapshot spanned 2026-04-27 through 2026-04-30, with multiple
  morning posts per weekday.
- `https://la.urbanize.city/sitemap.xml` returned a two-page sitemap index with
  12,111 post URLs total. 988 URLs had `lastmod >= 2025-05-01`, matching the
  approximate 12-month backfill horizon at validation time.
- Five Urbanize article URLs were submitted through `POST /research/articles` in
  the development DB and run through Pass 0 and Pass 1. All five returned
  `fetch_status='fetched'`, HTTP 200, `paywall_state='open'`, and body text
  between 1,442 and 2,298 characters. Triage/extraction were intentionally
  disabled in that environment because no Anthropic key was configured.
- Pass 1 quality was good enough for source validation but exposed tuning needs:
  comma-formatted unit counts like `2,250 residential units` were not captured,
  some title-only addresses were missed, and completion-date phrases should be
  rechecked before high-volume backfill.
- Light reconnaissance confirmed LA YIMBY (`https://layimby.com`) has
  WordPress-style robots/feed/sitemap endpoints suitable for a later fixture and
  D.late.E1 source. The Real Deal LA exposes `/la/feed/` but has heavier
  frontend/paywall/commercial-news noise and remains a D.late.E2 candidate.

D.2a should use RSS for incremental discovery, sitemap pages for dry-run/backfill
discovery, a conservative per-host limiter, 24-hour robots cache, and source
routing loaded from `news_sources.config` or source YAML rather than hardcoded
publisher branches.

### v3 — 2026-04-28 (post second senior review)

The v2 doc fixed the major architectural issues but introduced or left several lower-level mismatches against current code. Senior review caught all of them; v3 corrects them before implementation. Cumulative summary:

| Change | Sections affected | What was wrong | What is fixed |
|---|---|---|---|
| Source-name → logical-type mapping for orphan accept | §5.1 (new), §10.3, §10.7 | v2 had `source_runs.source_name = news_sources.slug` (e.g., `bizjournals_la`) but `evidence.source_type = 'news_article'`. `_link_orphan_evidence` (`review_workflow.py:1447`) looks up orphans via `get_logical_source_type(source_run.source_name)`. `bizjournals_la` is not in `LOGICAL_SOURCE_TYPE_BY_SOURCE_NAME` (`source_tiers.py:14`), so the function returns `bizjournals_la` unchanged and orphan accept queries `evidence.source_type = 'bizjournals_la'` — finding nothing. | The migration adds entries to `LOGICAL_SOURCE_TYPE_BY_SOURCE_NAME`: `bizjournals_la`, `news_paste_a_link`, `news_backfill`, `news_reextraction` all → `news_article`. Future publishers (Architect, Urbanize) each get one entry. This mirrors the existing LADBS pattern (`ladbs_permits` → `ladbs_permit`, etc.). |
| Review payload shape uses `changes`, not `field_changes` | §10.7, §11.2, §11.4 | v2 said `payload.field_changes`; the actual key written by `collect.py:211` and read by `payload.ts:142`/`firstChange` is `payload.changes`. | All references corrected to `payload.changes`. |
| One STATUS_CHANGE per changed field for news | §10.7, §11.4 | The frontend's `firstChange(item)` returns `payload.changes[0]` only; multi-change items mean Keep-old/Custom only apply to the first listed field. v2 packed multiple field changes into one item like the LADBS path does, which would silently lose decisions on fields 2+. | News integrator emits **one `STATUS_CHANGE` review item per changed field**. Each item's `payload.changes` is a single-element list containing that one field's change. This matches the user's option-B choice from the design Q&A and avoids the existing multi-change limitation. |
| Job-kind names made consistent | §6.4 | §6.4 used `reextract_article` and `fetch_and_extract`; §12 used `news_reextract`, `news_paste_a_link`. | §6.4 updated to use the §12 names. |
| Scheduler `last_scrape_started_at` is derived, not stored | §12.5 | v2 referenced `source.last_scrape_started_at` but `news_sources` had no such column. | Computed at scheduler-tick time from `MAX(source_runs.run_timestamp) WHERE jurisdiction_id = ? AND source_name = <news source slug> AND trigger_type = 'scheduled'`. No schema change. |
| RLS view mechanics use `security_definer`, not `security_invoker` | §4.14 | v2 declared the summary views with `security_invoker = true` while revoking authenticated SELECT on the underlying tables. Invoker-security views require the invoker to hold privileges on the underlying tables, so authenticated users couldn't read through the views either. | Views use `security_definer` (Postgres `SECURITY DEFINER` view semantics — the view runs with the privileges of its owner, not its caller). A dedicated role `news_summary_reader` owns the views and has narrow SELECT on the underlying tables. Authenticated users get SELECT on the views only. RLS on the underlying tables remains denying-authenticated. |
| Heartbeat is a real background thread | §12.6 | v2 said both "every 30s" and "after every task". Long backfill jobs would yield stale heartbeats since RQ workers are one-job-at-a-time. | A daemon `threading.Thread` started at worker process startup writes `worker_heartbeats` every 30s independent of task lifecycle. Per-task writes remain as supplementary signal. |
| Cost cap race protection via Postgres advisory lock | §13.3 | v2 said the cap "can't race past" but two concurrent workers checking the rollup could each fire a call. | LLM-call wrapper acquires `pg_advisory_xact_lock(NEWS_COST_CAP_LOCK_KEY)` before the cap check + cost-pre-reservation. Lock released after the row is inserted. Documented overshoot tolerance for in-flight calls already past the lock. |
| Auth validation uses GET, not HEAD | §16.2 | A HEAD response carries no body; the doc described detection via body markers, which can't fire on HEAD. | Validation issues a GET against a stored subscriber-only article URL (`news_sources.config.validation_url`) and inspects the body. |

### v2 — 2026-04-28 (post first senior review)

The v1 draft of this design contained eight contract mismatches against the current code, plus several smaller issues. Senior review caught all of them; this revision corrects the design before any implementation begins. The structural shape (article → extraction → reference → evidence quartet, immutable extraction history, passage offsets, paste-a-link first, no automated login) is unchanged.

| Change | Sections affected | What was wrong | What is fixed |
|---|---|---|---|
| Worker reuse | §3.3, §12, §23 | v1 introduced a new Postgres-as-queue worker. C.tail.1 already shipped a Redis/RQ worker (`src/tcg_pipeline/workers/scrape_jobs.py:26`). | Phase D extends the existing RQ worker by registering new job kinds. No second queue loop. |
| Apply-time semantics | §10, §11.2 | v1 said "no auto-apply" but the integration path called `resolve_project(apply=True)` on confirmed matches, which mutates project fields synchronously (`engine.py:225`). | Phase D adopts **post-apply review semantics**, identical to existing collectors. Article evidence on a confirmed match updates project fields immediately; the review queue surfaces those changes as `STATUS_CHANGE` / `OVERRIDE_CONTRADICTION` items, and the reviewer reverts via Keep-old (writes a `researcher_override` of the prior value). For `new_candidate` / `possible_match`, evidence stays orphan until accept, identical to today. |
| ReviewItemType enum | §10, §11.2 | v1 used `field_change` as a review-item type. The actual enum (`models.py:106`) has no such value. | All references rewritten to use the existing `STATUS_CHANGE` umbrella, `OVERRIDE_CONTRADICTION`, `POSSIBLE_MATCH`, or `NEW_CANDIDATE`. A future split of the `STATUS_CHANGE` umbrella into per-field types is out of scope for Phase D. |
| Source-run requirement | §10, §11.2, §12 | Discovery accept requires a `source_run` on the review item (`review_workflow.py:1349`). v1 didn't define how news creates one. | Each scheduled scrape and each paste-a-link batch creates a `SourceRun` row scoped to the news source's jurisdiction (or a sentinel jurisdiction when paste-a-link target isn't LA). The source_run id is set on every review item Phase D produces. |
| `scrape_jobs` schema | §4.15, §12.2, §23 | `scrape_jobs.jurisdiction_id` is non-null and the active-job unique index is `(jurisdiction_id, source_name)`. Paste-a-link of arbitrary URLs has no jurisdiction; concurrent paste-a-link/backfill jobs would collide on the unique index. | Migration: add `kind` and `target_payload` columns to `scrape_jobs`, make `jurisdiction_id` nullable, narrow the active-job unique index to `WHERE kind = 'collector_run' AND status IN ('queued','running')`. News job kinds use a new uniqueness story (§12.2). |
| Re-extraction tie-break | §10.3, §15.5 | v1 used `collected_at = article.fetched_at`. Re-extraction of the same article produces evidence rows tied on both `evidence_date` and `collected_at`; the resolver's "most recent wins" can't disambiguate. | `collected_at = NOW()` at evidence-write time (i.e., extraction-integration time), preserving `evidence_date = article.published_at`. Add `evidence.superseded_at` column; on materially-changed re-extraction, the prior reference's evidence row is marked superseded and excluded from resolver reads. |
| Operational tables | §4 (new tables), §13 | v1 referenced `system_alerts`, worker heartbeats, and credential validation timestamps in monitoring/recovery, but defined none. | Added §4 entries for `system_alerts`, `worker_heartbeats`, and `service_credential_validations`. CLI audit rows go in the existing `change_log` for project-touching actions and a new `news_admin_actions` table for non-project-touching ones. |
| `output_json` nullability | §4.3 | `news_extractions.output_json NOT NULL` couldn't represent parse-error / refusal cases. | Made nullable; added a sibling `raw_response_text` column for the unparsed model output and a `parse_error_text` column for the structured-output failure detail. |
| Pass 3 ordering | §8.7, §23 | Pass 3 triggered by `new_candidate` from the matcher (D.4), but Pass 3 was scheduled in build step D.3d before the matcher exists. | Pass 3 is split: extraction-triggered Pass 3 (low-confidence load-bearing field, structural disagreement, refusal) ships in D.3d. Match-triggered Pass 3 (`new_candidate` matches) ships in D.4 alongside the matcher itself. |
| `is_new_candidate` parens | §9.4 | `A and B or C` parses as `(A and B) or C`; any reference with `unit_total >= 10` could become a new candidate. | Explicit grouping: `has_strong_signals AND confidence_ok AND unit_count_ok`. |
| Backfill volume | §12.7, §17.3 | §12.7 estimated ~200 URLs for an 8-week backfill; §17.3 priced 2,800. | Reconciled to ~200 URLs (LABJ real-estate cadence). Backfill cost ~$10–$15, not $115. Headroom remains for adding sources without revisiting cost caps. |
| RLS exposure | §4.14, §18.8, §22 | v1 granted authenticated SELECT on `news_articles` and `news_extractions`. That exposes raw HTML, full body text, and model payloads through PostgREST. | Authenticated PostgREST gets SELECT only on narrow summary views (`news_articles_summary`, `news_extractions_summary`) that exclude `raw_html`, `body_text`, `output_json`, `raw_response_text`. Full rows are admin-only via FastAPI `/research/articles/{id}` after JWT + allowlist. |
| Downstream-handling overstatement | §5, §25 | v1 implied the resolver and contradiction service already implement §21f's "recent article wins for delivery dates" rule. Code review shows §21f is forward-looking only. | §5 corrected to list precisely what is implemented today (developer source priority, units split allowlist, news provenance string, contradiction service's recent-article-relaxed delivery threshold, snippet renderer) vs. what Phase D must implement to honor §21f. The §21f delivery-date priority rule is added as a Phase-D implementation item, not assumed present. |
| Dependencies | §27 (new) | v1 didn't enumerate the pyproject.toml additions. | New section lists `anthropic`, `playwright`, `trafilatura`, `dateparser`, `croniter`, `pyahocorasick`, with version-pin guidance and Render Docker base-image notes for Playwright. |

What was kept from v1: paste-a-link first as the vertical slice; the article/extraction/reference/evidence separation; immutable extraction history; passage offsets stored on extracted_fields; cookie-injection auth (no automated login); deferred trust-based auto-apply; the structural-vs-LLM dual-pass extraction approach; the conditional Pass 3 mechanism; the cost-cap design with bump-by-admin; the discarded-articles graveyard.

---

## 1. Overview

Phase D extends the TCG Pipeline Tracker with automated ingestion of news articles as Tier 2 evidence about real-estate development projects. The goals:

1. **Discovery** — articles surface project information (developer announcements, status updates, opposition, financing) that government sources and CoStar miss.
2. **Currency** — articles are often the freshest signal for delivery dates, financing milestones, and project changes.
3. **Auditability** — every value the system asserts based on a news article must trace back to a specific article passage, with character offsets for highlighting.

The design treats news ingestion as a producer-side pipeline that writes Tier 2 evidence into the existing evidence layer. The downstream consumers — resolution engine, contradiction detection, snippet renderer, review queue — already speak `news_article`. Phase D's job is to populate that channel correctly and reliably.

The downstream consumer side — `developer.py`, `units.py`, `delivery_year.py`, `contradictions.py`, `snippets.py` — is already news-aware. Phase D must respect those existing contracts.

---

## 2. Goals and Non-Goals

### 2.1 Goals

- Reliably collect news articles from configured polite sources, with Urbanize LA
  as the first live scheduled pilot, plus accept any URL pasted by a researcher.
- Extract structured project references, status signals, developer names, unit counts, delivery dates, and operational signal flags from article text.
- Match extracted references against existing projects, propose new projects when no match is plausible, or discard when no project is meaningfully described.
- Surface every match decision and every evidence row to the researcher through the existing Review Queue. Nothing in Phase D auto-applies to projects.
- Persist enough state that any value derived from an article can be traced to the article passage that supported it, and any extraction can be re-run against improved prompts in the future.
- Keep cost predictable, alerted, and capped.

### 2.2 Non-Goals (Phase D)

- **Cross-source LLM corroboration** ("deep research" — given an article mentions Project X, agentically search permits, CoStar, and other articles for confirmation). The roadmap names "Deep Research" but does not define items. Corroboration is the resolution engine's job; new evidence already gets resolved against all existing evidence on the project. Adding agentic LLM corroboration would explode cost, latency, and failure modes. **Routed to Phase E** as a resolution-engine refinement; see §25.2.
- **Auto-apply for high-confidence article matches.** All article-derived changes go through the Review Queue in Phase D. Auto-apply is a future refinement once researchers have built confidence in extraction quality. See §25 for the deferred follow-up.
- **Additional live sources beyond Urbanize** (D.late.E1/E2/C). LA YIMBY, The
  Real Deal LA, BizJournals LA, and other publishers are deferred until the
  Urbanize path proves the generic collector. The schema and pipeline are
  designed for multi-source from day one.
- **Sentiment scoring** as a numeric field. Sentiment is captured as discrete signal flags (`community_opposition`, `lawsuit_filed`, etc.), not a `-1.0..1.0` score.
- **Article body redistribution.** Articles are stored in the database for internal use only. They never leave the tool.

---

## 3. System Architecture

### 3.1 Component diagram

```
              ┌───────────────────────────────────────────────────────┐
              │                Render Worker (new)                     │
              │                                                        │
   schedule ─►│  scheduler ──► polite_news_collector ─┐                │
              │                                       │                │
   FastAPI ──►│  paste_a_link  ──────────────────────┼─► ingest_pipeline
   /research/ │                                       │     ├─ Pass 0  │
   articles   │  scrape_jobs queue ──────────────────┘     │ ingest    │
              │  (SELECT FOR UPDATE                         ├─ Pass 1  │
              │   SKIP LOCKED)                              │ regex/HTML│
              │                                             ├─ Pass 2a │
              │                                             │ Haiku    │
              │                                             ├─ Pass 2b │
              │                                             │ Opus     │
              │                                             ├─ Pass 3  │
              │                                             │ Opus     │
              │                                             │ (cond.)  │
              │                                             ▼          │
              │                                  article_matcher       │
              │                                             │          │
              │                                             ▼          │
              │                                  evidence_integrator   │
              │                                             │          │
              │              ┌──────────────────────────────┘          │
              │              ▼                                          │
              │   news_articles, news_extractions, news_matches         │
              │   evidence (Tier 2), review_items                      │
              │                                                         │
              │   cost_tracker, alert_dispatcher, audit_logger         │
              └───────────────────────────────────────────────────────┘
                                  │           │
                                  ▼           ▼
                         Postgres (Supabase)  Email / SMTP
                                  ▲
                                  │
              ┌───────────────────┴────────────────────┐
              │   FastAPI (existing service, extended) │
              │                                        │
              │   /research/articles      (paste-a-link)│
              │   /research/articles/{id} (read)       │
              │   /research/extractions/* (audit)      │
              │   /research/graveyard     (discarded)  │
              │   /research/cost          (admin)      │
              │   /research/admin + source health       │
              │                                        │
              └────────────────────────────────────────┘
                                  ▲
                                  │
              ┌───────────────────┴────────────────────┐
              │   Vercel Next.js (existing)             │
              │                                        │
              │   /research/paste                       │
              │   /research/graveyard                   │
              │   /coverage  (news ops banner)          │
              │   /review    (news-derived items)       │
              │                                        │
              └────────────────────────────────────────┘
```

### 3.2 Process boundaries

- **Render Worker (new).** Long-running Python process. Roles:
  - Scheduled news scraping (Urbanize LA pilot, configurable per source).
  - Consumer of the existing C.tail.1 RQ/Redis scrape_jobs queue (`src/tcg_pipeline/workers/scrape_jobs.py:26`); Phase D registers new job kinds — see §12.
  - Article ingest pipeline orchestrator (Pass 0–3).
  - Article matcher.
  - Evidence integrator.
  - Cost tracking + alert dispatch.
  - **Also retroactively unblocks C.l** for non-LADBS Socrata sources by consuming their queued `scrape_jobs` rows.
- **FastAPI (existing service, extended).** Adds a small `/research/*` surface for
  paste-a-link, ingest status reads, audit reads, graveyard browse, cost admin,
  source health/admin actions, and later paid-source auth rotation. Mutating
  endpoints stage `scrape_jobs` rows that the Worker consumes; FastAPI itself
  does not fetch articles or call LLMs.
- **Next.js (existing).** Adds the paste-a-link UI, graveyard browser, news ops admin tile in Coverage, and renders news-derived review items through the existing Review Queue surfaces (already news-aware via `render_news_article_snippet`).
- **Postgres (Supabase).** All durable state. New tables described in §4. RLS conventions match `data_model_changes.md`: authenticated SELECT only for read-only client surfaces; mutations service-role-only via FastAPI / Worker.

### 3.3 Worker reuse — extend the existing C.tail.1 RQ/Redis worker

C.tail.1 (2026-04-28) shipped a durable Render worker that consumes `scrape_jobs` rows via Redis/RQ. The dispatcher and worker entrypoints live in `src/tcg_pipeline/workers/scrape_jobs.py` (`enqueue_scrape_job_execution`, `run_scrape_job_task`, `run_worker`). The Coverage Refresh path already enqueues into RQ when `REDIS_URL` is configured.

**Phase D extends this worker; it does not introduce a parallel queue.** Concretely:

- New job kinds (`news_paste_a_link`, `news_scrape`, `news_reextract`, `news_backfill_chunk`) are new RQ task functions registered in `src/tcg_pipeline/workers/news_jobs.py`. Each is enqueued by the same `enqueue_scrape_job_execution` helper (or a thin sibling that targets a `news_jobs` task function) writing through the existing `scrape_jobs` table.
- The same RQ worker process consumes both Phase C scrape jobs and Phase D news jobs. We do not run two worker fleets.
- Scheduling (the cron-style `news_sources.schedule_cron`) runs as a separate RQ-Scheduler-style ticker registered alongside the worker, OR as a tiny in-process loop that runs inside one worker instance flagged as the "scheduler" (env `NEWS_SCHEDULER_LEADER=true`). We pick the simpler in-process loop for v1 — single instance, no leader-election complexity.
- Concurrency: RQ workers process one job at a time per worker process. To run multiple article extractions in parallel, scale the Render worker instance count. This matches existing C.tail.1 behavior. We avoid asyncio fan-out within a single RQ job because errors and timeouts get harder to attribute.

`scrape_jobs` schema is extended in §12.2 to support news-shaped jobs (nullable jurisdiction, generic `kind` + `target_payload`, narrowed unique index). This is the only schema change needed to absorb Phase D into the existing worker.

If RQ proves insufficient for news-volume bursts (it should not at our volumes), the migration to a more capable queue is a separate task and not in Phase D scope.

---

## 4. Data Model

All tables follow `data_model_changes.md` conventions: UUID PKs with `gen_random_uuid()`, `TIMESTAMPTZ NOT NULL DEFAULT NOW()` for timestamps, RLS on, named indexes via SQLAlchemy naming convention, Alembic migration timestamps `2026_05_NN_NNNN_*`.

### 4.1 `news_sources`

A registry of news publications we ingest from. Lets us add new sources without code changes (only collector implementations are code).

```sql
CREATE TABLE news_sources (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug              TEXT NOT NULL UNIQUE,            -- 'urbanize_la'
  name              TEXT NOT NULL,                   -- 'Urbanize LA'
  base_url          TEXT NOT NULL,
  collector_class   TEXT NOT NULL,                   -- 'PoliteNewsCollector'
  active            BOOLEAN NOT NULL DEFAULT TRUE,
  schedule_cron     TEXT,                            -- '30 7 * * *' (daily 07:30 PT)
  schedule_timezone TEXT,                            -- IANA TZ for schedule_cron, e.g. 'America/Los_Angeles'
  config            JSONB,                           -- collector-specific config
  market_id         UUID REFERENCES markets(id),     -- null = unscoped; matcher decides relevance
  jurisdiction_id   UUID REFERENCES jurisdictions(id),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_news_sources_active ON news_sources(active);
```

Active scheduled seed row for D.2a:

```sql
INSERT INTO news_sources (
  slug, name, base_url, collector_class, active,
  schedule_cron, schedule_timezone, config, market_id, jurisdiction_id
)
VALUES (
  'urbanize_la',
  'Urbanize LA',
  'https://la.urbanize.city',
  'PoliteNewsCollector',
  TRUE,
  '30 7 * * *',
  'America/Los_Angeles',
  '{
    "fetch_path": "polite",
    "hosts": ["la.urbanize.city"],
    "rss_urls": ["https://la.urbanize.city/rss.xml"],
    "sitemap_urls": ["https://la.urbanize.city/sitemap.xml"],
    "robots_url": "https://la.urbanize.city/robots.txt",
    "robots_cache_ttl_seconds": 86400,
    "rate_limit_seconds": 2,
    "source_strategy_doc": "docs/sources/news/urbanize_la.md",
    "user_agent": "Mozilla/5.0 (compatible; TCGPipelineTracker/0.1; +https://tcg-pipeline.vercel.app)"
  }'::jsonb,
  NULL,
  NULL
);
```

`schedule_cron` is informational; the actual cadence is enforced by the Worker's scheduler reading these rows. `bizjournals_la` may exist for historical migrations and orphan-link compatibility, but it must be `active = FALSE` with `schedule_cron = NULL` until D.late.C ships paid-source capability.

### 4.2 `news_articles`

One row per article we have ever attempted to ingest. Append-only after fetch; updated only when re-fetched (rare).

```sql
CREATE TABLE news_articles (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  news_source_id          UUID NOT NULL REFERENCES news_sources(id),
  url_canonical           TEXT NOT NULL,             -- after stripping utm/ref params
  url_original            TEXT NOT NULL,             -- as provided / discovered
  url_hash                TEXT NOT NULL,             -- sha256(url_canonical), dedup key
  fetch_status            TEXT NOT NULL,             -- 'pending', 'fetched', 'fetch_failed', 'parse_failed', 'paywalled', 'dead_link'
  fetch_attempts          INTEGER NOT NULL DEFAULT 0,
  first_attempted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_attempted_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  fetched_at              TIMESTAMPTZ,
  fetch_error_text        TEXT,

  -- Pass 0 outputs (HTML metadata + body)
  http_status             INTEGER,
  raw_html                TEXT,                      -- gzip'd via column compression (toast)
  raw_html_hash           TEXT,                      -- sha256, body-level dedup
  body_text               TEXT,                      -- readability-extracted plaintext
  body_text_hash          TEXT,                      -- sha256(body_text)
  title                   TEXT,
  byline_author           TEXT,
  published_at            TIMESTAMPTZ,
  publication_section     TEXT,                      -- 'Real Estate', 'Commercial Real Estate'
  tags                    TEXT[],                    -- from JSON-LD / OG metadata
  external_article_id     TEXT,                      -- canonical-URL tail or JSON-LD identifier
  language                TEXT NOT NULL DEFAULT 'en',
  paywall_state           TEXT,                      -- 'open', 'metered', 'subscriber_only'

  -- Pass 1 outputs (structural signals)
  structural_signals      JSONB,                     -- see §7.3 for shape
  structural_signals_at   TIMESTAMPTZ,

  -- Triage / extraction state
  triage_status           TEXT,                      -- 'pending', 'relevant', 'not_relevant', 'error'
  triage_at               TIMESTAMPTZ,
  triage_extraction_id    UUID,                      -- FK set after triage row written; see news_extractions
  current_extraction_id   UUID,                      -- pointer to active news_extractions row; set after Pass 2
  current_extraction_version INTEGER NOT NULL DEFAULT 0, -- bumps on each re-extraction

  -- Audit
  ingest_method           TEXT NOT NULL,             -- 'scheduled', 'paste_a_link', 'backfill', 'reextraction'
  ingested_by_user_id     UUID,                      -- non-null for paste_a_link
  notes                   TEXT,

  created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  UNIQUE (url_hash)
);

CREATE INDEX ix_news_articles_news_source_id ON news_articles(news_source_id);
CREATE INDEX ix_news_articles_published_at ON news_articles(published_at DESC NULLS LAST);
CREATE INDEX ix_news_articles_fetch_status ON news_articles(fetch_status);
CREATE INDEX ix_news_articles_triage_status ON news_articles(triage_status);
CREATE INDEX ix_news_articles_body_text_hash ON news_articles(body_text_hash);
```

Notes on shape:

- **`url_canonical`** is the dedup key. Normalization rules in §6.3.
- **`raw_html`** is stored as a TEXT column; Postgres TOAST compresses it transparently. Median article HTML is ~150KB raw, ~30KB compressed. We store HTML so that improvements to our HTML→text pipeline in the future do not require re-fetching.
- **`body_text`** is the LLM input. Stable plaintext with stable character offsets — these offsets are referenced by `passage_excerpts.offset_start/end` in extractions and snippets.
- **`fetch_status`** values cover every terminal outcome. No article fetched is ever silently dropped — even `fetch_failed` and `paywalled` rows persist with `fetch_error_text`.
- **`current_extraction_id`** is updated in lockstep with re-extraction; `current_extraction_version` is the auditable version counter. Old extractions remain queryable by `news_extractions.article_id`.
- **`paywall_state`** reflects the response we actually got. Urbanize validation
  articles all returned `open`; no speculative Urbanize paywall behavior should
  be added until a real gated article is observed. Paid-source session/paywall
  semantics are deferred to D.late.C (see §16).

### 4.3 `news_extractions`

Append-only. One row per (article × prompt-version × triggering reason). Old rows are kept as audit history.

```sql
CREATE TABLE news_extractions (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  article_id               UUID NOT NULL REFERENCES news_articles(id) ON DELETE CASCADE,
  pass                     TEXT NOT NULL,             -- 'triage', 'extraction', 'reextraction'
  triggered_by             TEXT NOT NULL,             -- 'initial', 'prompt_version_change', 'pass1_pass2_conflict', 'pass2_low_confidence', 'pass2_parse_error', 'pass2_new_candidate', 'user_reextract'
  supersedes_extraction_id UUID REFERENCES news_extractions(id),
  prompt_id                TEXT NOT NULL,             -- 'triage_v3', 'extract_v7', 'reextract_v2'
  prompt_version           TEXT NOT NULL,             -- 'v7' (matches prompt_id suffix)
  prompt_hash              TEXT NOT NULL,             -- sha256 of the rendered system + user prompt template
  model                    TEXT NOT NULL,             -- 'claude-haiku-4-5-20251001', 'claude-opus-4-7'
  model_provider           TEXT NOT NULL DEFAULT 'anthropic',

  -- LLM input/output
  input_tokens_uncached    INTEGER,
  input_tokens_cache_creation INTEGER,                 -- prompt-cache write tokens
  input_tokens_cached      INTEGER,                   -- prompt-cache read tokens
  output_tokens            INTEGER,
  cost_usd                 NUMERIC(10, 6),            -- per-extraction cost
  latency_ms               INTEGER,

  -- Output
  output_json              JSONB,                      -- structured response when parse_status='ok'; nullable for failures
  raw_response_text        TEXT,                       -- unparsed model output, populated for parse_error/schema_invalid/refused
  parse_status             TEXT NOT NULL,              -- 'ok', 'parse_error', 'schema_invalid', 'refused', 'truncated'
  parse_error_text         TEXT,                       -- structured-output failure detail when parse_status != 'ok'

  -- Per-extraction diagnostics
  diagnostic               JSONB,                     -- {regex_disagreements: [...], unresolved_refs: [...], ...}

  -- Identity / audit
  triggered_by_user_id     UUID,                      -- non-null when user-triggered
  created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_news_extractions_article_id_created_at ON news_extractions(article_id, created_at DESC);
CREATE INDEX ix_news_extractions_prompt_id_version ON news_extractions(prompt_id, prompt_version);
CREATE INDEX ix_news_extractions_pass_triggered_by ON news_extractions(pass, triggered_by);
```

Notes:

- **`pass`** distinguishes Haiku triage from Opus extraction from Pass-3 re-extraction. All three are persisted; auditing the cost or output of any one of them must be a single SELECT.
- **`prompt_hash`** is the SHA-256 of the *rendered* system + user prompt (not just the template name). Two articles using the same template but with different glossary content (registry has new entries) get different hashes — this lets us reproduce exactly what the model saw.
- **`output_json`** is the full structured response; the canonical schema is in §8.5.
- **`supersedes_extraction_id`** lets us walk extraction history per article for audit. The `news_articles.current_extraction_id` always points at the live row.
- **`triggered_by`** distinguishes initial extraction from re-extraction reasons. `pass2_new_candidate` triggers Pass 3 because creating a project is high-impact.

### 4.4 `news_project_references`

The structured project references emitted by an extraction, before matching. One row per `(extraction, reference_index)` tuple.

```sql
CREATE TABLE news_project_references (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  extraction_id       UUID NOT NULL REFERENCES news_extractions(id) ON DELETE CASCADE,
  article_id          UUID NOT NULL REFERENCES news_articles(id) ON DELETE CASCADE,
  reference_index     INTEGER NOT NULL,           -- 0-based position within extraction.output_json.project_references

  -- LLM-extracted signals about the referenced project
  candidate_name      TEXT,
  candidate_address   TEXT,
  candidate_developer TEXT,
  candidate_unit_total INTEGER,
  candidate_unit_affordable INTEGER,
  candidate_unit_market_rate INTEGER,
  candidate_product_type TEXT,
  candidate_age_restriction TEXT,
  candidate_status_signal TEXT,                   -- normalized to PipelineStatus value, or signal_flag key
  candidate_delivery_year_text TEXT,              -- raw "Q3 2026" / "late 2027" string from LLM
  candidate_delivery_year_normalized DATE,        -- best-effort parse to YYYY-07-01 etc.
  candidate_signal_flags JSONB,                   -- {flag_key: true, ...}
  candidate_identifiers JSONB,                    -- {case_number: [...], permit_number: [...], apn: [...]}
  candidate_neighborhood TEXT,                    -- "Hollywood", "DTLA"
  candidate_lat        FLOAT,
  candidate_lng        FLOAT,
  candidate_confidence TEXT NOT NULL,             -- 'high' | 'medium' | 'low' (from LLM)
  passage_excerpts    JSONB,                      -- list of {field, value, offset_start, offset_end, passage}

  -- Match outcome (populated by article_matcher)
  match_status        TEXT,                       -- 'pending' | 'confirmed' | 'possible' | 'new_candidate' | 'discarded' | 'manual_relink'
  matched_project_id  UUID REFERENCES projects(id) ON DELETE SET NULL,
  match_confidence    FLOAT,                      -- composite score from matcher
  match_reason        TEXT,                       -- 'identifier:case_number' | 'address_exact' | 'developer+neighborhood+units' | etc.
  match_candidates    JSONB,                      -- ordered list of {project_id, score, reason}
  match_decision_at   TIMESTAMPTZ,
  matched_evidence_id UUID REFERENCES evidence(id) ON DELETE SET NULL,
  review_item_id      UUID REFERENCES review_items(id) ON DELETE SET NULL,
  manual_relink_by_user_id UUID,
  manual_relink_at    TIMESTAMPTZ,
  manual_relink_note  TEXT,

  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  UNIQUE (extraction_id, reference_index)
);

CREATE INDEX ix_news_project_references_article_id ON news_project_references(article_id);
CREATE INDEX ix_news_project_references_matched_project_id ON news_project_references(matched_project_id);
CREATE INDEX ix_news_project_references_match_status ON news_project_references(match_status);
```

Notes:

- One extraction can produce N references (an article that mentions 5 projects). Each reference is matched independently.
- **`match_status = 'discarded'`** is the graveyard state: extraction succeeded but no project was meaningfully matched and the signals weren't strong enough to propose a new one. The article + extraction + reference rows persist; only the evidence row is absent. Researchers can manually relink from the graveyard browser (§15).
- **`matched_evidence_id`** points back at the `evidence` row this reference produced — that evidence row is what flows through the normal resolution + review queue.
- **`manual_relink_*`** columns audit graveyard reconciliation actions.

### 4.5 `news_extraction_costs`

Per-day cost rollup, populated by the Worker after every LLM call. Used by the cost-cap enforcer (§17).

```sql
CREATE TABLE news_extraction_costs (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  cost_date           DATE NOT NULL,                  -- the local PT date the call happened on
  pass                TEXT NOT NULL,                  -- 'triage', 'extraction', 'reextraction'
  model               TEXT NOT NULL,
  call_count          INTEGER NOT NULL DEFAULT 0,
  input_tokens_uncached BIGINT NOT NULL DEFAULT 0,
  input_tokens_cache_creation BIGINT NOT NULL DEFAULT 0,
  input_tokens_cached BIGINT NOT NULL DEFAULT 0,
  output_tokens       BIGINT NOT NULL DEFAULT 0,
  cost_usd            NUMERIC(12, 6) NOT NULL DEFAULT 0,

  UNIQUE (cost_date, pass, model)
);

CREATE INDEX ix_news_extraction_costs_cost_date ON news_extraction_costs(cost_date DESC);
```

The `cost_usd` column is updated transactionally with each `news_extractions` insert via a database trigger or worker-side aggregation.

### 4.6 `news_cost_caps`

A small admin table holding the live cap and any overrides.

```sql
CREATE TABLE news_cost_caps (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  effective_date      DATE NOT NULL,
  daily_warn_usd      NUMERIC(8, 2) NOT NULL,         -- $25 default
  daily_hard_usd      NUMERIC(8, 2) NOT NULL,         -- $35 default
  override_until      TIMESTAMPTZ,                    -- when an admin bumps the cap, this is the bump expiry
  override_hard_usd   NUMERIC(8, 2),
  override_set_by_user_id UUID,
  override_note       TEXT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  UNIQUE (effective_date)
);
```

The Worker checks the active cap before every LLM call and pauses (refuses to start a new call) when the day's cumulative cost would exceed the live hard cap.

### 4.7 `news_signal_flag_registry`

Config-driven so adding new signal flags is a row insert + prompt update, not a schema migration.

```sql
CREATE TABLE news_signal_flag_registry (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  flag_key          TEXT NOT NULL UNIQUE,             -- 'community_opposition'
  display_label     TEXT NOT NULL,                    -- 'Community opposition'
  category          TEXT NOT NULL,                    -- 'milestone' | 'risk' | 'change' | 'meta'
  description       TEXT NOT NULL,                    -- prose definition the LLM uses
  example_phrases   TEXT[],                           -- few-shot examples
  active            BOOLEAN NOT NULL DEFAULT TRUE,
  added_by_user_id  UUID,
  added_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  retired_at        TIMESTAMPTZ
);

CREATE INDEX ix_news_signal_flag_registry_active ON news_signal_flag_registry(active);
```

Initial seed (full list in §8.6). Adding flags is reviewer-driven; a CLI `tcg-pipeline news propose-flags --since <date>` runs an Opus pass over recent articles' free text and proposes new flags, which the researcher accepts or rejects.

### 4.8 `service_credentials`

Secure storage for third-party credentials (D.late.C paid-source cookie state, future news-source cookies, etc.).

```sql
CREATE TABLE service_credentials (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug              TEXT NOT NULL UNIQUE,             -- 'bizjournals_session'
  description       TEXT,
  payload_encrypted BYTEA NOT NULL,                   -- pgcrypto-encrypted JSON blob
  payload_kid       TEXT NOT NULL,                    -- key id used (pgcrypto / kms)
  set_by_user_id    UUID,
  set_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  rotated_at        TIMESTAMPTZ,
  expires_at        TIMESTAMPTZ,                      -- known expiry if available
  notes             TEXT
);

-- RLS: nobody but the service role can SELECT or write.
ALTER TABLE service_credentials ENABLE ROW LEVEL SECURITY;
-- (No policies created → all authenticated reads denied; service role bypasses RLS.)
```

The encryption uses pgcrypto's `pgp_sym_encrypt` against a key held in Render env (`SERVICE_CREDS_KEY`). Loss of the key invalidates the row; re-uploading rotates `payload_kid`. Authentication rotation flow is in §16.

### 4.9 `system_alerts`

A live alert table backing the Coverage banner and email-dispatch dedup. One row per active alert; rows close (set `cleared_at`) when the underlying condition resolves. v1 referenced this table in §13 without defining it; this fills the gap.

```sql
CREATE TABLE system_alerts (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  alert_key           TEXT NOT NULL,                  -- 'source_auth_invalid' | 'cost_warn_cap' | 'cost_hard_cap' | 'worker_stale' | 'extraction_error_rate' | 'fetch_failure_rate' | 'source_dark'
  severity            TEXT NOT NULL,                  -- 'info' | 'medium' | 'high'
  scope               JSONB,                          -- e.g., {news_source_id: '<uuid>'} or {jurisdiction_id: '<uuid>'}
  message             TEXT NOT NULL,
  detail              JSONB,                          -- structured context for UI rendering
  raised_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  email_sent_at       TIMESTAMPTZ,                    -- when the email notification fired (cooldown driver)
  cleared_at          TIMESTAMPTZ,
  cleared_by_user_id  UUID,
  cleared_reason      TEXT
);

CREATE UNIQUE INDEX uq_system_alerts_one_active_per_key_scope
  ON system_alerts(alert_key, COALESCE(scope::text, '{}'))
  WHERE cleared_at IS NULL;
```

The Worker raises and clears alerts; the email dispatcher reads from this table; the Next.js Coverage banner reads via PostgREST under RLS (authenticated SELECT only; no writes). The unique partial index prevents duplicate active alerts for the same `(key, scope)`.

### 4.10 `worker_heartbeats`

Liveness for the RQ worker(s). Render health checks read this. v1 referenced it in §12 without defining the table.

```sql
CREATE TABLE worker_heartbeats (
  worker_name         TEXT PRIMARY KEY,                -- 'news_worker_1', 'scheduler', etc.
  last_heartbeat_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  process_started_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  active_job_id       UUID,
  active_job_started_at TIMESTAMPTZ,
  metadata            JSONB                            -- {'rq_worker_pid': ..., 'queue': '...', 'host': '...'}
);
```

Each worker writes its own row every 30s. The health endpoint compares `NOW() - last_heartbeat_at` against a 5-minute threshold. The scheduler worker writes a row keyed `scheduler`.

### 4.11 `service_credential_validations`

Audit + last-known-state for credential-payload validity. Drives D.late.C paid-source cookie status surfaces.

```sql
CREATE TABLE service_credential_validations (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  credential_slug          TEXT NOT NULL,               -- 'bizjournals_session'
  validated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  outcome                  TEXT NOT NULL,               -- 'valid' | 'invalid' | 'error'
  outcome_reason           TEXT,                        -- 'login_redirect' | 'metered_paywall' | 'http_error' | etc.
  validated_by_user_id     UUID,                        -- non-null when CLI-triggered or admin-triggered
  validated_by_process     TEXT                         -- 'auth_cli' | 'pre_fetch_check' | 'scheduled_check'
);

CREATE INDEX ix_service_credential_validations_credential ON service_credential_validations(credential_slug, validated_at DESC);
```

Latest-row-per-slug answers D.late.C FastAPI auth-status queries such as `/research/auth/bizjournals/status`.

### 4.12 `news_admin_actions`

CLI / admin audit rows for actions that don't touch a project (so don't fit `change_log`): paid-source auth runs, prompt-version bumps, cost-cap bumps, source pause/resume, signal-flag registry edits.

```sql
CREATE TABLE news_admin_actions (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  action_kind         TEXT NOT NULL,                   -- 'auth_rotate' | 'prompt_bump' | 'cost_cap_bump' | 'source_pause' | 'source_resume' | 'flag_added' | 'flag_retired' | 'reextract_initiated' | 'manual_relink'
  performed_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  performed_by_user_id UUID,
  performed_by_label  TEXT,
  payload             JSONB,                           -- action-specific context
  notes               TEXT
);

CREATE INDEX ix_news_admin_actions_kind_performed_at ON news_admin_actions(action_kind, performed_at DESC);
```

These rows are never touched after insert. Manual relinks from the graveyard write here in addition to a `change_log` row (so we have both project-side and news-side audit views of the same action).

### 4.13 Indexes / unique constraints summary

- `news_articles.url_hash` UNIQUE — URL-based dedup.
- `news_articles.body_text_hash` indexed — secondary dedup if a publisher rotates URLs but reuses body.
- `news_extractions(article_id, created_at DESC)` — fastest "show me the history of this article."
- `news_project_references(extraction_id, reference_index)` UNIQUE — stable referencing back into LLM output.
- `news_extraction_costs(cost_date, pass, model)` UNIQUE — daily rollup keys.
- `news_cost_caps(effective_date)` UNIQUE — one cap per day.
- `system_alerts(alert_key, COALESCE(scope::text,'{}'))` UNIQUE WHERE cleared_at IS NULL — one active alert per key/scope.
- `evidence(superseded_at)` partial index WHERE superseded_at IS NULL — resolver reads only-active evidence (added in §15.5).

### 4.14 RLS posture (corrected from v1)

Phase D tables hold raw third-party article content (`news_articles.raw_html`, `news_articles.body_text`) and full LLM payloads (`news_extractions.output_json`, `raw_response_text`). Granting authenticated `SELECT` directly on those tables exposes them through PostgREST to any authenticated user — too wide for an internal tool with multiple researchers.

The corrected posture:

| Table | Authenticated PostgREST access | FastAPI admin access |
|---|---|---|
| `news_sources` | SELECT (full row) | full |
| `news_articles` | **No direct SELECT.** Authenticated reads go through view `news_articles_summary` which excludes `raw_html` and `body_text`. | full row via `/research/articles/{id}` |
| `news_extractions` | **No direct SELECT.** Authenticated reads go through view `news_extractions_summary` which excludes `output_json` and `raw_response_text` and exposes only `pass`, `prompt_id`, `prompt_version`, `model`, `cost_usd`, `parse_status`, timestamps. | full row via `/research/extractions/{id}/raw` |
| `news_project_references` | **No direct SELECT.** Authenticated reads go through `news_project_references_summary`, which excludes `passage_excerpts` because those carry article text snippets. | full row via FastAPI when review rendering needs excerpts |
| `news_extraction_costs` | SELECT (full row) | full |
| `news_cost_caps` | SELECT (full row) | full |
| `news_signal_flag_registry` | SELECT (full row) | full |
| `system_alerts` | SELECT (full row) — drives the Coverage banner | mutation via FastAPI / Worker only |
| `worker_heartbeats` | SELECT (full row) — drives the Coverage worker-status indicator | only the Worker writes |
| `service_credential_validations` | **No SELECT.** Status comes through D.late.C FastAPI auth-status routes. | full |
| `service_credentials` | **No SELECT, no anything.** Service role only. | service role only |
| `news_admin_actions` | SELECT (full row) — read-only audit view | mutation via Worker / FastAPI only |

The summary views are defined as:

v2 declared these views with `security_invoker = true`. That was wrong: under invoker security, Postgres requires the *invoker* (the authenticated user) to hold privileges on the *underlying* tables — but we're explicitly revoking authenticated SELECT on `news_articles` and `news_extractions`. Authenticated reads through the views would fail with "permission denied for table news_articles".

The correct mechanic uses **definer-security summary views**:

```sql
-- 1. A dedicated no-login marker role. Hosted Supabase pooler connections
--    cannot reliably transfer view ownership to this role because ALTER OWNER
--    requires SET ROLE privileges, so the migration leaves views owned by the
--    migration owner. The security boundary is the projection plus grants below.
CREATE ROLE news_summary_reader NOLOGIN;

-- 2. Revoke authenticated SELECT on the underlying tables.
REVOKE SELECT ON news_articles, news_extractions, news_project_references
FROM authenticated;

-- 3. Create the summary views. Postgres views default to running with the
--    privileges of their owner (security_definer is the default;
--    security_invoker = false). Setting the option explicitly documents
--    the intent.
CREATE VIEW news_articles_summary
WITH (security_invoker = false) AS
SELECT id, news_source_id, url_canonical, fetch_status, fetched_at, http_status, title,
       byline_author, published_at, publication_section, tags, language, paywall_state,
       triage_status, triage_at, current_extraction_id, current_extraction_version,
       ingest_method, ingested_by_user_id, created_at, updated_at
FROM news_articles;

CREATE VIEW news_extractions_summary
WITH (security_invoker = false) AS
SELECT id, article_id, pass, triggered_by, supersedes_extraction_id, prompt_id, prompt_version,
       model, model_provider, input_tokens_uncached, input_tokens_cached, output_tokens,
       cost_usd, latency_ms, parse_status, parse_error_text, triggered_by_user_id, created_at
FROM news_extractions;

CREATE VIEW news_project_references_summary
WITH (security_invoker = false) AS
SELECT id, extraction_id, article_id, reference_index, candidate_name, candidate_address,
       candidate_developer, candidate_unit_total, candidate_unit_affordable,
       candidate_unit_market_rate, candidate_product_type, candidate_age_restriction,
       candidate_status_signal, candidate_delivery_year_text,
       candidate_delivery_year_normalized, candidate_signal_flags, candidate_identifiers,
       candidate_neighborhood, candidate_lat, candidate_lng, candidate_confidence,
       match_status, matched_project_id, match_confidence, match_reason, match_candidates,
       match_decision_at, matched_evidence_id, review_item_id, manual_relink_by_user_id,
       manual_relink_at, manual_relink_note, created_at, updated_at
FROM news_project_references;

-- 4. Grant authenticated SELECT on the views (the safe projection).
GRANT SELECT ON news_articles_summary, news_extractions_summary,
  news_project_references_summary TO authenticated;
```

How this resolves:

- An authenticated PostgREST query against `news_articles_summary` runs through the view; the view executes with the privileges of the migration owner; the projection returns only the safe columns.
- The same authenticated user querying `news_articles` directly is denied (no SELECT grant; RLS on the table also denies for completeness — see below).
- Service-role connections (FastAPI's privileged session) bypass these grants entirely and read full rows.

**Underlying-table RLS posture**: keep RLS enabled on `news_articles`, `news_extractions`, and `news_project_references` with no policies for `authenticated` (deny-by-default). The combination of "RLS on, no authenticated policies, no authenticated SELECT grant" is belt-and-suspenders against accidental future grants.

**Why not a row-level security policy on the view itself?** Postgres doesn't apply RLS to views directly; RLS attaches to base tables. The definer-with-narrow-projection pattern above is idiomatic and is what we want for "public-projection of a private table" in PostgREST + Supabase setups.

**Caveats to verify in implementation:**

- Confirm `news_summary_reader` cannot be used as a login role at any point. `NOLOGIN` plus no password makes this a configuration mistake to acquire.
- After future ALTER TABLE on `news_articles` / `news_extractions` that adds columns, the views are NOT auto-refreshed; the migration that adds the column must also `CREATE OR REPLACE VIEW` if the column should be exposed.
- Supabase's PostgREST role chain (anon → authenticated → service_role) interacts with the definer pattern fine, but a smoke test through PostgREST after migration is required (one of the D.1 verification steps).

All mutations route through FastAPI / Worker as before.

### 4.15 Migration order

Two Alembic migrations:

**`2026_05_NN_create_news_research_phase_d_tables`** — the news content tables:

0. Create `news_summary_reader` role (`NOLOGIN`) as a marker role for the summary-view boundary.
1. `news_sources` + seed `news_paste_a_link`; D.2a later seeds active unscoped `urbanize_la` and disables/unschedules historical `bizjournals_la`.
2. `news_articles` + `news_articles_summary` view per §4.14.
3. `news_extractions` + `news_extractions_summary` view per §4.14.
4. `news_project_references` + `news_project_references_summary` view excluding `passage_excerpts`.
5. `news_extraction_costs`.
6. `news_cost_caps` + seed today's row.
7. `news_signal_flag_registry` + seed initial flag set (§8.6).
8. `service_credentials`.
9. `system_alerts`, `worker_heartbeats`, `service_credential_validations`, `news_admin_actions`.

**`2026_05_NN_extend_scrape_jobs_for_phase_d`** — the existing-table extensions:

10. `scrape_jobs`: add `kind TEXT NOT NULL DEFAULT 'collector_run'`, `target_payload JSONB`. Make `jurisdiction_id` nullable. Drop the existing partial unique index on `(jurisdiction_id, source_name) WHERE status IN ('queued','running')`. Replace with `(jurisdiction_id, source_name, kind) WHERE kind = 'collector_run' AND status IN ('queued','running')` so collector-style scheduled jobs remain de-duplicated while news jobs do not collide. (See §12.2 for the news-job de-dup story.)
11. `evidence`: add `superseded_at TIMESTAMPTZ NULL` and a partial index `WHERE superseded_at IS NULL` for resolver reads. (See §15.5.)
12. `LOGICAL_SOURCE_TYPE_BY_SOURCE_NAME`: add `bizjournals_la`, `urbanize_la`, `news_paste_a_link`, `news_backfill`, `news_reextraction` → `news_article` per §5.1. This is a code change shipped alongside the migration (the dict lives in `src/tcg_pipeline/source_tiers.py`, not the database) but must land in the same release so orphan-evidence accept works.

A third follow-on migration adds the trigger or worker-side aggregation that updates `news_extraction_costs` from `news_extractions` inserts.

---

## 5. Source Tier Integration

`config/source_tiers.yaml` already declares `news_article` as Tier 2. Phase D writes evidence rows with:

- `source_type = 'news_article'`
- `source_tier = 2`
- `ingest_method ∈ {'news_scheduled', 'news_paste_a_link', 'news_backfill', 'news_reextraction'}`
- `source_record_id =` the `news_project_references.id` UUID (NOT the article URL — references are the unit of evidence, articles are not).

**What is implemented in code today** (Phase D must remain compatible with these contracts):

- **`developer.py:18`** — `DEVELOPER_SOURCE_PRIORITY` puts `news_article` at priority 1, just below `pipedream` (priority 0). Used as a tiebreak when evidence dates collide; "most recent wins" is still the primary ordering. So a fresh news article does NOT automatically outrank older Pipedream evidence — recency is checked first.
- **`units.py:15-19`** — `SPLIT_SOURCE_ALLOWLIST` includes `news_article`; news evidence may write affordable/market_rate splits.
- **`delivery_year.py`** — `_provenance_for_source_type('news_article')` returns `'explicit_news'`. D.4-resolver implements the §21f rule: when CoStar would otherwise win delivery-date resolution, recent `news_article` evidence within 180 days can outrank it while preserving higher-priority TCG/government winners. See §25.7.
- **`contradictions.py:30, 341-357`** — `NEWS_SOURCE_TYPES = {'news_article', 'news', 'article', 'bizjournals'}`. The contradiction service relaxes the delivery-date contradiction threshold for news evidence within the last 180 days (`_candidate_is_recent_article`). Phase D standardizes on `'news_article'` as the canonical source_type; the other strings remain in the set for compat.
- **`snippets.py:145, 240`** — `'news_article': render_news_article_snippet`. The renderer reads `evidence.raw_data.{publication, published_at, author}` for the detail line and `extracted_fields[field].highlights` (or top-level `extracted_fields.highlights`) for passage offsets. Phase D writes highlights into `extracted_fields[field].highlights`.

**What is intentionally unchanged or not implemented** (Phase D must keep these constraints unless explicitly scoped):

- **Status promotion from news alone.** The status resolver is conservative — single Tier 2 evidence will not promote a project to `Under Construction` without Tier 1 corroboration (per existing decisions in `ARCHITECTURE.md` §8). This is a feature, not a bug: an article alone is not construction-start proof. Phase D should not weaken this. News evidence may suggest status changes that show up as `STATUS_CHANGE` review items but do not auto-promote.
- **Field-level review item types other than `STATUS_CHANGE`.** The `ReviewItemType` enum has no `field_change`. All field-level diffs from collectors today get packed into `STATUS_CHANGE` items by the differ. Phase D follows that convention; splitting `STATUS_CHANGE` into per-field types is out of scope.

**Required `extracted_fields` shape from Phase D evidence rows** (per §6 of EVIDENCE_LAYER_DECISIONS, contract for the resolver):

```json
{
  "developer": {
    "value": "Helio Capital",
    "confidence": "high",
    "highlights": [
      {
        "field": "developer",
        "value": "Helio Capital",
        "passage": "...under developer Helio Capital...",
        "offset_start": 142,
        "offset_end": 158
      }
    ]
  },
  "total_units": {
    "value": 310,
    "confidence": "high",
    "highlights": [
      {"field": "total_units", "value": 310, "passage": "the 310-unit Helio project", "offset_start": 87, "offset_end": 108}
    ]
  },
  "pipeline_status": {
    "value": "Approved",
    "confidence": "medium",
    "highlights": [...]
  },
  "date_delivery": {
    "value": "2027-09-01",
    "confidence": "medium",
    "raw_text": "expected delivery in late 2027",
    "highlights": [...]
  }
}
```

`signal_flags` (separate evidence column, not under `extracted_fields`):

```json
{
  "groundbreaking_announced": true,
  "construction_financing_announced": true
}
```

`raw_data` (the article-side context, kept on the evidence row only because the existing snippet renderer reads `raw_data.publication`, `raw_data.author`, `raw_data.published_at` for snippet metadata — we duplicate these from `news_articles` for renderer convenience):

```json
{
  "article_id": "uuid...",
  "extraction_id": "uuid...",
  "reference_id": "uuid...",
  "publication": "Urbanize LA",
  "publisher": "urbanize_la",
  "source_name": "Urbanize LA",
  "published_at": "2026-04-08",
  "author": "Jane Reporter",
  "url": "https://la.urbanize.city/post/...",
  "title": "Helio breaks ground on 310-unit project",
  "body_excerpt": "First 600 chars of the article body for the snippet renderer detail line"
}
```

This is a denormalization on purpose. The evidence row carries everything the renderer needs without joining `news_articles` at hover time.

### 5.1 `source_runs.source_name` ↔ `evidence.source_type` mapping

Phase D follows the existing convention in `src/tcg_pipeline/source_tiers.py`:

- `source_runs.source_name` carries the **runtime source slug** (the publisher / job-kind identifier — e.g., `urbanize_la`, `news_paste_a_link`).
- `evidence.source_type` carries the **logical type** (`news_article` for all news, mirroring how `ladbs_permits` and `ladbs_permit_activity` both map to `ladbs_permit`).
- `get_logical_source_type(source_name)` is the dict lookup that converts.

The orphan-evidence accept path in `_link_orphan_evidence` (`review_workflow.py:1447`) calls `get_logical_source_type(source_run.source_name)` to find orphan evidence by `source_type`. **If a news source slug is missing from the dict, the function returns the slug unchanged and the orphan accept finds no evidence.** This is the most common failure mode for new ingest paths and v1 missed it.

The Phase D migration adds these entries to `LOGICAL_SOURCE_TYPE_BY_SOURCE_NAME`:

```python
LOGICAL_SOURCE_TYPE_BY_SOURCE_NAME = {
    # ... existing LADBS / Pipedream / CoStar entries ...
    "bizjournals_la":     "news_article",
    "urbanize_la":        "news_article",
    "news_paste_a_link":  "news_article",
    "news_backfill":      "news_article",
    "news_reextraction":  "news_article",
}
```

When a future news source is added (D.late.E1/E2/C), the source_runs slug for that publisher gets one entry mapped to `news_article` and is referenced in `news_sources.slug`. The convention is "one slug per publisher OR per ingest pathway, all mapping to the same logical type."

How each Phase D ingest path uses these slugs:

| Ingest path | `source_runs.source_name` | `evidence.source_type` |
|---|---|---|
| Scheduled Urbanize scrape | `urbanize_la` | `news_article` |
| Paste-a-link, Urbanize URL after D.2a host routing | `urbanize_la` (publisher detected from URL host) | `news_article` |
| Paste-a-link, unknown publisher | `news_paste_a_link` | `news_article` |
| 12-month Urbanize backfill | `news_backfill` or `urbanize_la` run context, with article source `urbanize_la` | `news_article` |
| Re-extraction (prompt-version sweep, conflict-triggered) | `news_reextraction` | `news_article` |

The result: orphan evidence written by any news ingest path can be linked on accept of any news-source-run review item, because all of them resolve to the same logical type. The publisher-specific attribution survives in `news_articles.news_source_id` and the source_run's runtime slug.

---

## 6. Component 1 — Article Fetcher

### 6.1 Generic polite NewsCollector

D.2a implements a config-driven polite collector, not a publisher-specific
Urbanize scraper and not the deferred BizJournals paid-source path. It reads
`news_sources.config` (or the source YAML/doc equivalent) for:

- `fetch_path`: `polite` or `advanced`; `advanced` hard-fails with a system
  alert until D.late.ADV implements it.
- `hosts`: hostnames routed to this source, used by paste-a-link and scheduled
  discovery.
- `rss_urls`: feeds used for incremental discovery.
- `sitemap_urls` / archive URLs: used for dry-run and backfill discovery.
- `robots_url`, robots cache TTL, crawl-delay handling, per-host rate limit,
  retry/backoff policy, `Retry-After` handling, conditional GET settings, and
  source strategy doc path.

```python
class PoliteNewsCollector:
    fetch_path = "polite"

    async def discover_urls(self, since: datetime) -> list[str]:
        """Return canonical article URLs discovered from configured feeds/sitemaps."""
        # 1. Load the source config and source strategy doc pointer.
        # 2. Fetch robots.txt with a 24h cache; refuse disallowed paths.
        # 3. Fetch configured RSS URLs for incremental runs.
        # 4. Fetch configured sitemap/archive URLs for backfill dry-runs.
        # 5. Canonicalize, dedupe, and return URLs published/modified since `since`.

    async def fetch_article(self, url: str) -> ArticleFetchResult:
        """Fetch one article through the polite path."""
        # Use httpx + trafilatura/readability with an identifying User-Agent.
        # Respect robots, per-host limits, Retry-After, and conditional GET.
        # Persist structured failures; do not silently drop a URL.
```

`ArticleFetchResult` shape:

```python
@dataclass
class ArticleFetchResult:
    url_canonical: str
    url_original: str
    http_status: int
    raw_html: str | None
    body_text: str | None
    title: str | None
    byline_author: str | None
    published_at: datetime | None
    publication_section: str | None
    tags: list[str]
    external_article_id: str | None
    paywall_state: Literal['open', 'metered', 'subscriber_only', 'unknown']
    fetch_status: Literal['fetched', 'fetch_failed', 'parse_failed', 'paywalled', 'dead_link']
    fetch_error_text: str | None
```

### 6.2 Urbanize LA source configuration

Urbanize LA is the only live Phase D scheduled source:

- Host: `la.urbanize.city`
- Incremental discovery: `https://la.urbanize.city/rss.xml`
- Backfill discovery: `https://la.urbanize.city/sitemap.xml`
- Robots: `https://la.urbanize.city/robots.txt`
- Fetch path: `polite`
- Scope: market-unscoped (`market_id = NULL`, `jurisdiction_id = NULL`)
- Initial cron candidate: `30 7 * * *` in `America/Los_Angeles`

The source is intentionally unscoped. The matcher decides relevance across live
markets; Orange County/Santa Monica/non-modeled articles may become discarded or
new-candidate signal instead of being filtered at collection time.

D.2v validated the five representative URLs through `news_paste_a_link`, not
through `urbanize_la`. D.2a added the source row and host-routing path; before
D.6 turns on the cron, rerun the same URLs in staging with an Anthropic key so
Haiku triage and Opus extraction/integration are smoke-tested against real
Urbanize text through the source-specific path.

### 6.2.1 Paid-source auth is deferred

Cookie/session auth for BizJournals and other paid sources is D.late.C. The
original session-state injection design remains valid for that later work, but
it is not part of the active D.2a/D.6 Urbanize path. `bizjournals_la` should stay
inactive and unscheduled until the paid-source capability ships.

### 6.3 URL canonicalization

```python
def canonicalize_url(url: str) -> str:
    """
    1. Lowercase scheme + host.
    2. Strip query parameters: utm_*, ref, fbclid, gclid, mc_cid, mc_eid, _hsenc, _hsmi, source, share.
    3. Strip trailing slash from path.
    4. Strip URL fragment.
    5. Preserve all other query params that may be source-significant.
    """
```

`url_hash = sha256(url_canonical)` is the dedup key. A second-line dedup is `body_text_hash` — if a publisher rotates URLs but reuses body verbatim, we collapse. The matcher and review-queue UI deduplicate by article-id; secondary dedup is just to avoid double-counting cost and review items.

### 6.4 Paste-a-link

The simplest entrypoint, and the first one we ship. Routed through FastAPI `POST /research/articles`:

```http
POST /research/articles
Authorization: Bearer <supabase token>
Content-Type: application/json

{
  "url": "https://la.urbanize.city/post/example-project-update",
  "force_reextract": false,
  "force_project_id": null,
  "note": "optional researcher note"
}
```

Response:

```json
{
  "article_id": "uuid...",
  "scrape_job_id": "uuid...",
  "status": "queued",
  "existing_article": false
}
```

Behavior:

1. Canonicalize the URL.
2. Lookup existing `news_articles.url_hash` row.
   - If exists and `force_reextract = false`: return the existing row immediately. Frontend renders "we already have this — view extraction" with a `[Reextract]` button.
   - If exists and `force_reextract = true`: enqueue a `scrape_jobs` row with `kind = 'news_reextract'` and `target_payload = {"article_id": "<existing>", "reason": "user_paste_force"}`.
   - If not exists: insert pending `news_articles` row + enqueue `scrape_jobs` row with `kind = 'news_paste_a_link'` and `target_payload = {"url": "...", "force_project_id": null}`.
3. The Worker picks up the job, runs the full pipeline (fetch → Pass 0/1/2/3 → match → integrate).
4. UI polls the article status (or subscribes via WebSocket — out of scope; HTTP polling is fine for MVP).

For paste-a-link, host routing checks configured news sources first. A routed
Urbanize URL uses the `urbanize_la` source context; unknown hosts fall back to
`news_paste_a_link`. D.2a/D.2b use the polite fetch path only. If a source config
requests `fetch_path = advanced` before D.late.ADV exists, the worker records a
hard failure and `news_advanced_fetch_deferred` system alert rather than trying a
browser/proxy fallback. Paste-a-link job progress records the selected
`fetch_path` so the admin article view can explain which path ran or failed.

---

## 7. Component 2 — Pass 0 / Pass 1 (Deterministic Ingest)

### 7.1 Pass 0 — Article ingest

Inputs: an `ArticleFetchResult` from the fetcher.
Outputs: a populated `news_articles` row with `fetch_status`, `body_text`, metadata.

Steps:

1. Persist `raw_html` (TOAST-compressed automatically). Compute `raw_html_hash`.
2. Run readability-lxml (or trafilatura) to extract `body_text`. Trafilatura's `extract()` with `output_format='txt'` is preferred for the polite path because it handled Urbanize validation pages well and produces stable plaintext.
3. Compute `body_text_hash`. If a previously-ingested article has the same body hash, log a `notes` entry and treat as the same article (skip extraction; Pass-2 already done).
4. Parse JSON-LD from `<script type="application/ld+json">` for `headline`, `author.name`, `datePublished`, `articleSection`, `keywords`, `identifier`. Fall back to OpenGraph (`og:title`, `og:author`, `article:published_time`, `article:section`, `article:tag`).
5. Detect `paywall_state` — if the body is < 200 chars and contains "subscribe" / "log in to read", mark `metered`. Source-specific paid-wall markers are D.late.C unless observed on an active Phase D source.
6. Update `news_articles` row: `fetch_status='fetched'`, `fetched_at=NOW()`, all metadata fields populated.

**Pass 0 has no LLM cost.** It's pure text manipulation. It always runs. Re-extraction never re-runs Pass 0 (the article body is durable).

### 7.2 Pass 1 — Structural signal extraction

Inputs: `news_articles.body_text` plus title/headline metadata where noted.
Outputs: `news_articles.structural_signals` JSONB blob, `structural_signals_at = NOW()`.

The structural pass is a battery of regex / NER / dictionary lookups. It produces *candidates* with character offsets — it does not interpret which project they belong to. Its purpose is to give the LLM concrete anchors and to act as a sanity check on LLM output.

The full battery (extensible — adding a new extractor is a code change but not a schema change):

#### 7.3 Structural extractor inventory

```python
@dataclass
class StructuralSignal:
    extractor: str               # 'unit_count', 'address', 'case_number', 'permit_number',
                                 # 'apn', 'date', 'developer_dict', 'project_dict',
                                 # 'status_phrase', 'delivery_phrase', 'product_type_phrase',
                                 # 'age_restriction_phrase', 'affordable_split_phrase',
                                 # 'opposition_phrase', 'lawsuit_phrase', 'financing_phrase',
                                 # 'milestone_phrase'
    raw_match: str               # the literal text matched
    offset_start: int            # character offset into body_text
    offset_end: int
    canonical: Any               # parsed/canonicalized value (e.g., 310 for "310-unit")
    confidence: float            # 0.0–1.0; structural extractors set this conservatively
    metadata: dict[str, Any]     # extractor-specific: {separator: '-'} for unit, etc.
```

Extractors:

1. **`unit_count`** — `\b(\d{1,3}(?:,\d{3})+|\d{2,5})[-\s]?(?:unit|units|apartment|apartments|residences|residential\s+units|condos|condominium|condominiums|keys|rooms)\b` (case-insensitive). Canonical = the integer after stripping commas. Excludes obvious non-counts ("$310M unit").
2. **`address`** — `usaddress.tag()` over the body and title/headline, plus a regex pre-pass for street-numbered patterns. We bias toward LA address patterns (cardinal + numbered street + suffix). Canonical = the parsed `(street_number, street_name, suffix, city, zip)` tuple normalized through the existing `tcg_pipeline.matching.normalizer.normalize_address`.
3. **`case_number`** — `\b(CPC|VTT|TT|ENV|DIR|ZA|APCC|APCSV|APCNV|APCS|APCH|APCE|APCW)-\d{4}-\d+(-[A-Z0-9-]+)?\b`. Canonical = uppercased exact match.
4. **`permit_number`** — PCIS pattern `\b\d{2}[A-Z]?\d{3}-?\d{5}-?\d{5}\b`.
5. **`apn`** — LA APN patterns. `\b\d{4}-\d{3}-\d{3}\b` and variants.
6. **`date`** — `dateparser.parse_dates_from_text` and `dateutil` over body. Canonical = `(parsed_date, surrounding_text)`. Useful for status-history dating.
7. **`developer_dict`** — Aho-Corasick scan over `developer_registry.canonical_name + developer_aliases`. Canonical = canonical developer ID. This is the second use of the registry (the first is canonicalization in `developer.py`).
8. **`project_dict`** — Aho-Corasick scan over `projects.project_name + projects.previous_names` for the relevant market(s). Canonical = `project_id`. Heuristic only — names collide.
9. **`status_phrase`** — dictionary of status-language phrases mapped to PipelineStatus or signal-flag values:
   - `"under construction"`, `"construction is underway"`, `"vertical construction"` → `pipeline_status: Under Construction`.
   - `"broke ground"`, `"groundbreaking"`, `"construction began"` → `signal_flag: groundbreaking_announced`.
   - `"topped out"`, `"reached the top floor"`, `"structurally complete"` → `signal_flag: topped_out`.
   - `"opened"`, `"delivered"`, `"now open"`, `"residents are moving in"`, `"first occupancy"` → `pipeline_status: Complete`.
   - `"approved by city council"`, `"won approval"`, `"received approval"`, `"city approved"`, `"ENV cleared"` → `pipeline_status: Approved`.
   - `"filed plans"`, `"submitted application"`, `"filed for entitlement"`, `"applied to"` → `pipeline_status: Pending`.
   - `"proposed"`, `"plans"`, `"plans for"`, `"is planning"` → `pipeline_status: Proposed`.
   - `"shelved"`, `"on hold"`, `"paused"`, `"delayed indefinitely"`, `"stalled"` → `signal_flag: stalled_indicator`.
   - `"lawsuit"`, `"sued"`, `"plaintiff"`, `"complaint filed"` → `signal_flag: lawsuit_filed`.
   - `"appeal filed"`, `"under appeal"`, `"appealed the decision"` → `signal_flag: appeal_filed`.
   - `"opposition"`, `"opposed by"`, `"residents protested"`, `"community pushback"`, `"NIMBY"` → `signal_flag: community_opposition`.
   - `"construction loan"`, `"financing closed"`, `"secured financing"`, `"refinanced"` → `signal_flag: construction_financing_announced`.
   - `"leasing center open"`, `"sales office open"`, `"now leasing"`, `"now selling"`, `"pre-leasing"` → `signal_flag: sales_or_leasing_center_open`.
10. **`delivery_phrase`** — `(expected to|scheduled to|will|set to|aiming to|projected to)\s+(deliver|open|complete|finish)\s+(in|by)?\s+(.+?\b(?:Q[1-4] \d{4}|early|mid|late) \d{4}|spring|summer|fall|winter)` plus noun-first variants like `completion is expected in Fall 2027` and `expected completion in late 2027`. Canonical = best-effort parsed date.
11. **`product_type_phrase`** — `\b(apartment(s)?|condo(s)?|condominium(s)?|townhom(e|es)|build-to-rent|BTR|single-family|micro[-\s]unit|co-living)\b`.
12. **`age_restriction_phrase`** — `\b(senior\s+(housing|living|apartment(s)?)|55\+|62\+|student\s+housing|university\s+housing)\b`.
13. **`affordable_split_phrase`** — `\b(\d{1,4})\s+(?:affordable|low-income|workforce|moderate-income|market-rate|market\s+rate)\b`. Also `\b(\d{1,3})%\s+(affordable|inclusionary)\b`.

The output, stored on `news_articles.structural_signals`:

```json
{
  "extractor_version": "v1",
  "ran_at": "2026-04-28T13:00:00Z",
  "signals": [
    {"extractor": "unit_count", "raw_match": "310-unit", "offset_start": 87, "offset_end": 95, "canonical": 310, "confidence": 0.95, "metadata": {}},
    {"extractor": "developer_dict", "raw_match": "Helio Capital", "offset_start": 145, "offset_end": 158, "canonical": "<developer_uuid>", "confidence": 0.99, "metadata": {"matched_alias": "Helio Capital"}},
    {"extractor": "status_phrase", "raw_match": "broke ground", "offset_start": 220, "offset_end": 232, "canonical": "groundbreaking_announced", "confidence": 0.9, "metadata": {"signal_kind": "flag"}},
    ...
  ]
}
```

### 7.4 Why structural and LLM both, not one or the other

The user's instinct here is right and worth recording:

- **Structural extraction is exact, free, and reproducible.** It anchors LLM output to real character offsets. When the LLM says "the article says 310 units", we know exactly where in the body that came from because the structural pass already pinpointed it.
- **The LLM owns interpretation.** Multi-project articles, cross-references ("the project," "it"), partial mentions, ambiguous developer names — the LLM resolves these. The structural pass cannot.
- **Disagreement is signal.** When structural finds "310 units" and the LLM extracts 250, that's a real disagreement worth surfacing. Sometimes the LLM is right (the 310 was a quote from a 2023 article cited in the body), sometimes the structural pass is. Either way, the researcher needs to know.

Disagreements are stored in `news_extractions.diagnostic.structural_disagreements` and surfaced in the Review Item detail UI as a small chip ("Automated parsing read 310 units; the model read 250. View passages."). They do **not** create separate review items — that would be queue noise. They influence Pass 3 triggering (§8.3).

### 7.5 The signal-flag registry self-update (slow burn)

CLI: `tcg-pipeline news propose-flags --since <date>`.

Reads N recent articles. Sends them to Opus 4.7 with a meta-prompt: "Here are recent articles. Look for recurring patterns in the project-related language that aren't covered by the existing flag list [registry]. Propose new flags with key, description, examples, category. Be conservative — only propose flags you saw in at least 3 articles."

Output is written to `data/output/news/proposed_flags_<timestamp>.json` for researcher review. Researcher accepts → row inserted into `news_signal_flag_registry`. Then `tcg-pipeline news bump-prompt-version` regenerates the extraction prompt with the new flags and the next extraction round picks them up.

This is opt-in and slow-burn. We do not expand the registry automatically.

---

## 8. Component 3 — Pass 2 / Pass 3 (LLM Extraction)

### 8.1 Models and providers

- **Provider:** Anthropic API directly via the `anthropic` Python SDK. We use `prompt_caching` for the static parts of the prompt (system prompt, glossary, project list).
- **Pass 2a triage:** `claude-haiku-4-5-20251001` (current Haiku 4.5). Cheap, fast.
- **Pass 2b extraction:** `claude-opus-4-7` (current Opus 4.7). The user's stated preference: highest-quality model for anything difficult, cost is not the primary constraint. We will allow downgrade to `claude-sonnet-4-6` via configuration if cost becomes a problem at scale.
- **Pass 3 re-extraction:** `claude-opus-4-7`.

The model id for each pass is stored in `news_extractions.model` so historical extractions can be reproduced exactly.

We do not default to Vercel AI Gateway. Reasons:
- Direct SDK is cheaper per token (no gateway markup).
- Prompt caching is straightforward via SDK headers; gateway support varies.
- Cost cap enforcement is simpler when we're the sole client of the provider.
- We can add Gateway later if multi-provider A/B becomes a need.

### 8.2 Prompt structure

Every LLM call uses a three-block message structure with prompt caching applied to the static blocks:

```python
messages = [
    {
        "role": "system",
        "content": [
            {
                "type": "text",
                "text": SYSTEM_PROMPT_TEMPLATE,        # ~2KB, static, cached
                "cache_control": {"type": "ephemeral"},
            },
            {
                "type": "text",
                "text": render_glossary(),             # ~5KB: developer registry + canonical project names
                "cache_control": {"type": "ephemeral"},
            },
            {
                "type": "text",
                "text": render_signal_flag_registry(), # ~3KB: flag definitions + examples
                "cache_control": {"type": "ephemeral"},
            },
        ],
    },
    {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": render_article(article, structural_signals),  # per-article, not cached
            },
        ],
    },
]
```

Cache hit rate after the first article of the day is ~95%+ on the system blocks. We track `input_tokens_cached` separately on `news_extractions` and roll it up into `news_extraction_costs` for cost-tracking transparency.

The full system prompt template, glossary template, and extraction-output JSON schema are versioned in `src/tcg_pipeline/news/prompts/`. Each prompt gets a stable id like `extract_v7` and is referenced by `news_extractions.prompt_id`.

### 8.3 Pass 2a — Triage prompt (Haiku 4.5)

The triage prompt, intentionally broad:

```
You are filtering a stream of news articles. Decide if the article is about real estate,
a real estate development project, or anything related to development of residential or
commercial real estate. Cast a wide net. When in doubt, say yes.

Examples of "yes":
- A specific apartment, condo, or mixed-use project being planned, approved, built, financed,
  delivered, or stalled.
- An interview with a developer about their pipeline.
- Community opposition or litigation about a specific project.
- A market report mentioning specific projects.
- An investor announcement about a real estate transaction tied to a development.
- A municipal action affecting a specific project.

Examples of "no":
- Pure macroeconomic real-estate trend pieces with no specific project named.
- Articles purely about residential sales markets (existing single-family resale) with
  no development project mentioned.
- Articles about commercial leasing of existing buildings.
- Articles unrelated to real estate.

Respond in this exact JSON form:
{"relevant": true | false, "reason": "<one short sentence>"}
```

Output is parsed; `news_extractions(pass='triage')` row is written. If `relevant=true`, we proceed to Pass 2b.

**Triage error direction: always inclusion.** Per the design decisions (user note), if Haiku reports `relevant=false` but its `reason` contains uncertainty markers ("might be," "possibly," "unclear"), we override to `relevant=true`. Cost of false positive is one Pass 2b call (~$0.05); cost of false negative is permanent loss of intelligence.

### 8.4 Pass 2b — Extraction prompt (Opus 4.7)

The extraction prompt makes the structural signals explicit input:

```
You are extracting structured project data from a news article for the TCG real estate
pipeline tracker. Your output must be valid JSON matching the schema below.

You will be given:
- The article body, with character offsets numbered every 100 chars in the margin.
- Article metadata (publication, date, author, title).
- Structural signals already extracted by automated parsing — these are concrete tokens
  with character offsets that you should treat as ground truth unless the surrounding
  context contradicts them.
- A glossary of known developer names and project names (with IDs).
- A registry of signal flags you may emit, each with a definition and examples.

Your job:
1. Identify each distinct development project the article is about. One article can
   reference multiple projects; emit one project_reference per project.
2. For each project_reference, extract:
   - candidate_name (project name, if stated)
   - candidate_address (street address, if stated)
   - candidate_developer (developer/sponsor entity, if stated)
   - candidate_unit_total, candidate_unit_affordable, candidate_unit_market_rate
   - candidate_product_type (apartment | condo | townhome | single_family | micro_co_living | other)
   - candidate_age_restriction (non_age_restricted | senior | student | unknown)
   - candidate_status_signal (Conceptual | Proposed | Pending | Approved | Under Construction
     | Pre-Leasing/Pre-Selling | Complete | Stalled | Inactive — pick at most one,
     or null if the article doesn't establish current status)
   - candidate_delivery_year_text (raw "Q3 2026" / "late 2027")
   - candidate_signal_flags (any flags from the registry that the article supports)
   - candidate_identifiers (case_number[], permit_number[], apn[])
   - candidate_neighborhood (e.g., "Hollywood", "DTLA")
   - candidate_lat, candidate_lng (only if explicitly stated, e.g., from an embedded map)
   - candidate_confidence ("high" | "medium" | "low") — your overall confidence in this
     reference
   - passage_excerpts: a list of {field, value, passage, offset_start, offset_end}.
     Every value you extracted must have at least one passage_excerpt anchoring it.
3. Be conservative. If the article doesn't say a value, leave it null. Do not infer.

Special rules:
- If the article quotes an old date or refers to a historical state, do NOT emit it as
  current status. Status signals must reflect the current state as the article describes it.
- If multiple projects are mentioned but only one in detail, only emit a project_reference
  for the detailed one. Listed-but-not-discussed projects should not generate references.
- Use offsets from the structural_signals when reporting passage_excerpts for the same
  values structural extraction already found.

Schema: <see §8.5>
```

### 8.5 Output schema

```json
{
  "relevance": "confirmed" | "rejected" | "unclear",
  "rejected_reason": "string | null",
  "project_references": [
    {
      "candidate_name": "string | null",
      "candidate_address": "string | null",
      "candidate_developer": "string | null",
      "candidate_unit_total": "integer | null",
      "candidate_unit_affordable": "integer | null",
      "candidate_unit_market_rate": "integer | null",
      "candidate_product_type": "apartment | condo | townhome | single_family | micro_co_living | other | null",
      "candidate_age_restriction": "non_age_restricted | senior | student | unknown | null",
      "candidate_status_signal": "PipelineStatus enum value | null",
      "candidate_delivery_year_text": "string | null",
      "candidate_delivery_year_normalized": "YYYY-MM-DD | null",
      "candidate_signal_flags": {"flag_key": true, ...},
      "candidate_identifiers": {"case_number": [], "permit_number": [], "apn": []},
      "candidate_neighborhood": "string | null",
      "candidate_lat": "number | null",
      "candidate_lng": "number | null",
      "candidate_confidence": "high | medium | low",
      "passage_excerpts": [
        {
          "field": "string",
          "value": "any",
          "passage": "string",
          "offset_start": "integer",
          "offset_end": "integer"
        }
      ],
      "registry_developer_id": "uuid | null",
      "registry_project_id": "uuid | null"
    }
  ],
  "diagnostic": {
    "structural_disagreements": [...],
    "uncertain_offsets": [...],
    "model_notes": "string | null"
  }
}
```

The schema is enforced via `anthropic` SDK's structured output (tool-use with JSON schema). Schema violations are caught and a `parse_error` row is recorded; we do not silently consume malformed output.

### 8.6 Initial signal-flag registry seed

```yaml
# Milestones
- groundbreaking_announced
- topped_out
- delivered
- pre_leasing_open
- sales_or_leasing_center_open
- construction_financing_announced
- equity_financing_announced

# Risks
- community_opposition
- lawsuit_filed
- lawsuit_resolved
- appeal_filed
- appeal_resolved
- stalled_indicator
- developer_change

# Project changes
- naming_change
- unit_count_change
- product_type_change
- delivery_date_changed
- affordable_inclusionary_component
- entitlement_change

# Meta
- mention_only           # article only mentions the project; no new info
- speculative            # article uses "rumored," "in talks," "considering"
- correction_or_retraction
- prior_phase_delivered_same_site
- land_assembly_incomplete
```

Each row gets `display_label`, `description`, and ~3 example phrases. The full seed is in `migrations/2026_05_NN_*.py` and mirrored in `config/news_signal_flags.yaml` for reference.

### 8.7 Pass 3 — Conditional re-extraction (split between extraction time and match time)

Pass 3 has two distinct trigger families. They fire at different points in the pipeline and ship in different build steps. v1 conflated them; v2 splits them.

**Pass 3a — extraction-time triggers** (fire inside the extraction pipeline, before the matcher runs; ship in build step **D.3d**):

1. **Pass 1 / Pass 2 conflict** on a load-bearing field. Load-bearing = `pipeline_status`, `total_units`, `affordable_units`, `market_rate_units`, `developer`, `date_delivery`, `candidate_address`. Conflict = differing canonical values where both sides are non-null.
2. **Pass 2 low confidence** on any load-bearing field.
3. **Pass 2 returned a parse-error / schema-invalid / refused result** that the structured-output retry didn't fix.

**Pass 3b — match-time trigger** (fires after the matcher runs; ships in build step **D.4** alongside the matcher itself):

4. **Matcher returned `new_candidate`** for any reference (creating a new project is high-impact, worth a second look).

The order of operations in the pipeline:

```
Pass 0 (ingest) → Pass 1 (regex) → Pass 2a (triage) → Pass 2b (extract)
   → if any of (1)(2)(3) → Pass 3a (re-extract) [ships in D.3d]
   → matcher runs on the active extraction's references [D.4]
   → if matcher emits new_candidate → Pass 3b (re-extract focused on the new candidate) [ships in D.4]
   → matcher re-runs on the latest extraction
   → evidence integrator
```

Pass 3a and Pass 3b both write a new `news_extractions` row with `pass='reextraction'`, `triggered_by` set to the actual trigger reason (`pass1_pass2_conflict`, `pass2_low_confidence`, `pass2_parse_error`, `pass2_new_candidate`), `supersedes_extraction_id` pointing at the prior row. `news_articles.current_extraction_id` is updated to point at the latest re-extraction.

The Pass 3 prompt includes the conflict context:

```
Re-examine this article. The previous extraction and automated parsing disagreed on
the following:

  - total_units: automated parsing found 310 (offsets 87-95: "310-unit"), the previous
    extraction emitted 250.

Re-read the article body and emit a corrected project_references list. Explain in
diagnostic.model_notes why the previous extraction differed.
```

Pass 3a/3b both write a new `news_extractions` row per the supersession rules in §8.7. `news_articles.current_extraction_id` is updated to point at the latest extraction.

Old extractions are **never deleted**. Researcher can browse extraction history per article in the UI.

Cost bound: Pass 3a fires for ~10–25% of relevant articles; Pass 3b fires for the much smaller fraction that produced `new_candidate` matches. Combined daily impact at 30 relevant articles/day: ~$0.20–$0.50.

### 8.8 Cost accounting per call

Every LLM call's response includes token-usage metadata. We compute cost using the model's published rates and persist:

- `input_tokens_uncached` — first-time tokens in this call.
- `input_tokens_cache_creation` — prompt-cache write tokens billed at the provider's cache-creation rate.
- `input_tokens_cached` — tokens served from prompt cache (cheaper).
- `output_tokens` — generation.
- `cost_usd` — computed in code from a static rate table (currently in `src/tcg_pipeline/news/llm.py`) so we can correct historical cost if the rate table changes.

The trigger or worker-side aggregation updates `news_extraction_costs` daily-rollup keyed by `(cost_date, pass, model)`.

---

## 9. Component 4 — Article-to-Project Matcher

### 9.1 Why a separate matcher (not `match_raw_record`)

The existing `match_raw_record` (`src/tcg_pipeline/matching/matcher.py`) is built for `RawRecord`s — one record, one canonical address, one project. News is fundamentally different:

- One article → many projects. Each `news_project_references` row is its own match attempt.
- News references often lack a single canonical address (street + cross-street, neighborhood-only).
- News brings additional fields the existing matcher doesn't use (unit count, developer, neighborhood, lat/lng).
- The existing matcher assumes structured government IDs; news provides them only sometimes.

We introduce `tcg_pipeline.matching.news_matcher` as a sibling module. It reuses the existing primitives where possible (address normalization, identifier lookup) but adds article-specific tiers.

### 9.2 Match input

```python
@dataclass
class ArticleReferenceMatchInput:
    market_id: UUID | None  # None means source is unscoped; query all active markets
    candidate_name: str | None
    candidate_address: str | None
    candidate_developer: str | None
    candidate_unit_total: int | None
    candidate_unit_affordable: int | None
    candidate_unit_market_rate: int | None
    candidate_neighborhood: str | None
    candidate_lat: float | None
    candidate_lng: float | None
    candidate_identifiers: dict[str, list[str]]  # {case_number: [...], permit_number: [...], apn: [...]}
    candidate_confidence: str  # 'high' | 'medium' | 'low'
    structural_signals: list[StructuralSignal]
```

### 9.3 Match tiers

**Tier 1 — Identifier match.** Reuse `_match_identifiers` from `matcher.py`. Article identifiers are rare but possible (case numbers cited in articles). One identifier match → confidence 0.97 → `confirmed`.

**Tier 2 — Address + project name + developer composite.**
- Normalize `candidate_address` through `tcg_pipeline.matching.normalizer`.
- Look up exact and zip-tolerant address matches via `_load_address_matches`.
- For each candidate project, score:
  - Address exact: +0.50
  - Address zip-tolerant: +0.40
  - Project name fuzzy match (rapidfuzz token_set_ratio ≥ 85): +0.15
  - Developer match after canonicalization: +0.20
  - Unit count within ±25% of project's `total_units`: +0.05
- Score ≥ 0.85 → `confirmed`. 0.65–0.84 → `possible`. < 0.65 → fall through.

**Tier 3 — Developer + neighborhood + unit fingerprint** (used when address is absent).
- Query `projects` filtered by `market_id` when present; for unscoped sources,
  query all active markets and let scoring decide relevance. Then score:
  - `developer` ILIKE candidate_developer (canonicalized)
  - OR `costar_submarket` matches candidate_neighborhood
- For each candidate, score:
  - Developer canonical match: +0.40
  - Neighborhood match: +0.20
  - Unit count within ±25%: +0.20
  - Product type match: +0.05
  - Stories within ±1: +0.05
  - Coordinates within 75m (if provided): +0.20
- Score ≥ 0.80 → `possible`. < 0.65 → fall through.

**Tier 4 — Project name fuzzy alone.** Last-ditch lookup of `project_name` and `previous_names` against extracted candidates. Score ≥ 0.85 → `possible`. Otherwise → `discard` (or `new_candidate`, see §9.4).

### 9.4 New candidate vs. discard

When no tier produces a match, the matcher chooses between `new_candidate` and `discard` based on signal strength:

```python
def is_new_candidate(input: ArticleReferenceMatchInput) -> bool:
    has_strong_signals = (
        bool(input.candidate_address)
        or bool(input.candidate_identifiers.get("case_number"))
        or bool(input.candidate_identifiers.get("permit_number"))
        or bool(input.candidate_identifiers.get("apn"))
        or (bool(input.candidate_developer) and bool(input.candidate_unit_total))
    )
    confidence_ok = input.candidate_confidence in {"high", "medium"}
    # If a unit count is asserted, require it to be at least 10 to filter out
    # marginal mentions ("two-unit ADU"). If no unit count is asserted, do not
    # use unit count as a gate.
    unit_count_ok = (
        input.candidate_unit_total is None
        or input.candidate_unit_total >= 10
    )
    return has_strong_signals and confidence_ok and unit_count_ok
```

(v1 had `A and B and C is None or C >= 10`, which Python parses as `(A and B and C is None) or (C >= 10)` — any reference with `unit_total >= 10` would qualify even without strong signals or sufficient confidence. Fixed by introducing explicit named subexpressions.)

`new_candidate` → write a `new_candidate` review item (already supported by C.j review queue UI). On accept, the C.g project-creation flow is invoked.

`discard` → no review item, no evidence row. The reference row is persisted with `match_status = 'discarded'`. The article is browseable in the graveyard (§15) where researchers can manually relink.

### 9.5 Match output

```python
@dataclass
class ArticleReferenceMatchResult:
    match_status: Literal['confirmed', 'possible', 'new_candidate', 'discarded']
    matched_project_id: UUID | None
    match_confidence: float
    match_reason: str               # human-readable
    candidate_project_ids: list[UUID]  # for 'possible' status
    candidate_scores: list[dict]    # [{'project_id': ..., 'score': ..., 'reason': ...}, ...]
```

The matcher writes its result back onto the `news_project_references` row (`match_status`, `matched_project_id`, `match_confidence`, `match_reason`, `match_candidates`, `match_decision_at`).

### 9.6 Auto-apply policy (Phase D vs. future)

**In Phase D, no article-derived match is auto-applied to a project, even at 0.95 confidence.** Every reference produces a review item. This is intentional — articles are the noisiest source we have, and confidence in extraction needs to be earned by months of reviewer feedback.

A future phase (call it D-late or E follow-up) introduces an auto-apply path for `confirmed` matches with `candidate_confidence = 'high'` and a clean Pass 1/Pass 2 agreement. That's a separate item; see §25.1.

---

## 10. Component 5 — Evidence Integrator

### 10.1 The applied-vs-staged decision (corrected from v1)

The v1 draft claimed Phase D enforces "no auto-apply" for article evidence — but the integration path called `resolve_project(apply=True)` on confirmed matches, which mutates project fields synchronously per `engine.py:225`. That's a contract contradiction.

**The correct framing for Phase D: post-apply review semantics, identical to existing collectors.**

This is the same pattern the LADBS / Pipedream / CoStar collectors use today:

1. Evidence is written (with `project_id` set when the matcher confirmed the project; `project_id=NULL` otherwise).
2. `resolve_project(apply=True)` is called, which mutates project fields and emits `STATUS_CHANGE` / `OVERRIDE_CONTRADICTION` review items via the existing differ + contradiction service.
3. The reviewer sees the changes in the queue. Two outcomes:
   - **Accept new** → no-op (the field already has the new value); review item committed; ChangeLog row written.
   - **Keep old** → writes a `researcher_override` of the prior value, which the resolver respects on the next run; future contradicting evidence will reopen the loop via `OVERRIDE_CONTRADICTION`.

The "review queue gates the change" semantic is **post-hoc**: the field has already changed when the queue item appears, but the review provides the opportunity to revert (via override) and creates the audit trail.

This is consistent with how government collectors behave today. Reviewers already understand it. Phase D should not invent a different model.

The cases where evidence is NOT immediately project-linked + applied:

- `match_status = 'possible'` → evidence written orphan (`project_id=NULL`); a `POSSIBLE_MATCH` review item references the candidate project ids and the orphan evidence id. Accepting links the evidence and re-resolves; this is the existing `_link_orphan_evidence` flow from `review_workflow.py`.
- `match_status = 'new_candidate'` → evidence written orphan; a `NEW_CANDIDATE` review item is generated with the news payload + source_run context. Accept invokes the C.g project-creation flow, links the evidence to the new project, and re-resolves.
- `match_status = 'discarded'` → no evidence row at all; `news_project_references.match_status='discarded'` is the only record. Article shows in the graveyard for manual relink.

The cases where evidence IS immediately project-linked + applied (post-apply review):

- `match_status = 'confirmed'` → evidence written with `project_id=matched_project_id`; `resolve_project(apply=True)` runs; the resulting `STATUS_CHANGE` items + any `OVERRIDE_CONTRADICTION` items represent the changes the reviewer can revert.

### 10.2 Why this is acceptable (and what protects against bad evidence)

The senior reviewer's concern: if confirmed-match news evidence auto-applies, a bad LLM extraction can mutate a project field before any human sees it. Mitigations already in place or added by Phase D:

1. **Status promotion is conservative.** The status resolver requires Tier 1 corroboration before promoting to `Under Construction`. A single news article alone won't auto-promote; it'll show up as a `STATUS_CHANGE` item suggesting promotion, which the reviewer accepts/rejects.
2. **Researcher overrides are review-protected.** Any field with an active override will trigger `OVERRIDE_CONTRADICTION` rather than silently changing — the reviewer is notified.
3. **Confidence chips on the queue.** Low-confidence article-derived changes get a visible LOW priority chip, sorting to the bottom.
4. **Pass 3 catches load-bearing low-confidence + structural disagreements** before evidence is even written (§8.7).
5. **Article-derived `STATUS_CHANGE` items have a distinctive `news_context` payload** so reviewers see at a glance "this came from Urbanize LA 2026-04-08, here's the passage" and can revert in one keystroke.

If confidence in extraction quality grows over time and reviewers consistently accept-new on confirmed-match news evidence, we eventually graduate to true auto-apply (no review item raised) per §25.1. Phase D ships with the conservative "post-apply review" model.

### 10.3 Source-run context for article-derived review items

Discovery accept (`new_candidate` / `possible_match`) requires a `source_run` on the review item per `review_workflow.py:1349`. Phase D creates source_runs as follows:

- **Scheduled scrape:** one `source_runs` row per scrape kickoff, scoped to the
  news source's configured market/jurisdiction when present or `market='unscoped'`
  when the source is market-unscoped. `source_name=news_sources.slug`,
  `trigger_type='scheduled'`. All review items produced from articles in that
  scrape reference this source_run.
- **Paste-a-link:** one `source_runs` row per paste, scoped to the article's inferred jurisdiction (or the news_source's default jurisdiction; or a sentinel "unknown jurisdiction" row when paste-a-link points at a non-LA URL — see §12.3 for the sentinel jurisdiction handling), `trigger_type='user_initiated'`, `initiated_by_user_id` populated.
- **Backfill:** one `source_runs` row per backfill batch (per source). Review items reference the batch source_run.
- **Re-extraction:** if the re-extraction generates new review items (the materially-changed-output gating in §15.3), they reference a fresh source_run with `trigger_type='backfill'` and a note pointing at the prompt-version bump.

`source_runs` is denormalized from the news context but lets the existing review_workflow primitives work without modification.

### 10.4 Evidence row shape

```python
Evidence(
    project_id=reference.matched_project_id if match_status == 'confirmed' else None,
    source_type="news_article",
    source_tier=2,
    ingest_method="news_scheduled" | "news_paste_a_link" | "news_backfill" | "news_reextraction",
    source_record_id=str(reference.id),
    collected_at=now_utc(),                     # extraction-integration time, NOT article fetched_at
                                                 # (see §15.5 for re-extraction tie-break)
    evidence_date=article.published_at.date() if article.published_at else article.fetched_at.date(),
    raw_data={  # denormalized for snippet renderer
        "article_id": str(article.id),
        "extraction_id": str(extraction.id),
        "reference_id": str(reference.id),
        "publication": news_source.name,
        "publisher": news_source.slug,
        "source_name": news_source.name,
        "published_at": article.published_at.isoformat() if article.published_at else None,
        "author": article.byline_author,
        "url": article.url_canonical,
        "title": article.title,
        "body_excerpt": article.body_text[:600],
    },
    raw_data_hash=compute_hash(...),
    extracted_fields={
        # one entry per field the LLM extracted, with confidence + highlights
    },
    signal_flags=reference.candidate_signal_flags,
    notes=None,
    superseded_at=None,                          # set when a re-extraction supersedes this row
)
```

### 10.5 Hash-based dedup and re-extraction

The standard partial unique index on `(source_type, source_record_id, raw_data_hash)` gives us idempotency for repeated extraction of the same article without re-extraction. Re-extraction produces a new `news_project_references.id` (hence a new `source_record_id`), so the new evidence row is a separate row. The prior evidence row's `superseded_at` is set to the integration time of the new row, and a partial index excludes superseded rows from resolver reads. See §15.5 for the full mechanic.

### 10.6 Project resolution after evidence write

After the integrator inserts evidence and (for confirmed matches) sets `project_id`, it calls `resolve_project(project_id, session, apply=True)` once per affected project. The existing resolver:

1. Re-reads all non-superseded evidence for the project.
2. Re-runs each field resolver.
3. Mutates project fields where resolved values differ from current.
4. Calls `detect_project_contradictions` against active researcher overrides.
5. Writes a `resolution_log` row.
6. Status changes append to `status_history`.

The differ logic that runs in `persist_collected_records` for collectors generates `STATUS_CHANGE` review items packed with field-level changes. The integrator must invoke the equivalent path so news-driven changes produce the same kind of review items reviewers see today.

For `possible` and `new_candidate` matches (orphan evidence), `resolve_project` is NOT called at write time — there's no project to resolve. Resolution runs on review-accept inside `_link_orphan_evidence`, which already handles this case.

### 10.7 Review item type for article-derived changes

The `ReviewItemType` enum (`models.py:106`) values that Phase D uses:

| Article situation | ReviewItemType | Source of review item |
|---|---|---|
| Confirmed match, fields changed | `STATUS_CHANGE` (one item per changed field; see §10.7.1) | News integrator iterates the differ's `field_changes` and creates one review item per `(project_id, field_name)`, with `payload.changes` as a single-element list. |
| Confirmed match, contradicts active override | `OVERRIDE_CONTRADICTION` | Existing contradiction service in `contradictions.py` |
| Possible match | `POSSIBLE_MATCH` | Existing matcher path, with news-source-run context |
| New project candidate | `NEW_CANDIDATE` | Existing matcher path, with news-source-run context |
| Discarded | None (graveyard only) | — |

We do not introduce a `field_change` enum value. The existing `STATUS_CHANGE` umbrella is what carries field diffs today. A future split is out of scope for Phase D.

### 10.7.1 Per-field item granularity (deviation from existing collector pattern)

The existing collector path in `collect.py:211` packs **all** field changes from one diff into a single `STATUS_CHANGE` item with `payload.changes = [change_1, change_2, ...]`. The frontend's `firstChange()` helper in `lib/review/payload.ts:142` returns only `payload.changes[0]`; Keep-old / Custom decisions therefore only resolve against the first changed field. For multi-change LADBS items, this is a known limitation reviewers tolerate today.

For news evidence, this limitation matters more — articles often imply 3-7 field changes at once, and we don't want any of them to silently lose Keep-old/Custom decisions. **The Phase D news integrator emits one `STATUS_CHANGE` review item per changed field.** Each item's `payload.changes` is a single-element list. Multiple articles affecting the same project produce multiple items, all grouped under the project header by the existing queue UI.

The integrator pseudo-code:

```python
def integrate_confirmed_news_match(reference, project, diff_result, source_run):
    for change in diff_result.field_changes:
        item = ReviewItem(
            project_id=project.id,
            source_run_id=source_run.id,
            item_type=ReviewItemType.STATUS_CHANGE,
            status=ReviewItemStatus.OPEN,
            state="open",
            priority=compute_priority(change, news_evidence_confidence),
            payload={
                "field_name": change.field_name,
                "current_value": change.old_value,
                "proposed_value": change.new_value,
                "changes": [serialize_change(change)],     # single-element list per §10.7.1
                "review_flags": [],
                "news_context": {                          # see §11.2
                    "article_id": str(reference.article_id),
                    "extraction_id": str(reference.extraction_id),
                    "reference_id": str(reference.id),
                    "extraction_confidence": reference.candidate_confidence,
                    "structural_disagreement": diff_result.structural_disagreement_for(change.field_name),
                    "extraction_version": current_article_extraction_version,
                    "prompt_id": current_extraction.prompt_id,
                },
            },
        )
        session.add(item)
```

Existing review-item dedup (`(project_id, field_name, item_type)` per `review_workflow.md` §2.4) automatically merges/refreshes per-field items as new evidence arrives. This is exactly the right behavior — a second article confirming a unit count refreshes the existing `STATUS_CHANGE(project_id=X, field=total_units)` item rather than creating a duplicate.

`OVERRIDE_CONTRADICTION` items remain one-per-field already (per `contradictions.py:101`), so no change there.

### 10.8 Confidence visibility in review items

LLM extraction confidence flows through:

- `evidence.extracted_fields[field].confidence` ∈ {high, medium, low}.
- The resolver's `infer_confidence` already reads this.
- The Review Queue UI shows the LLM confidence as a small chip on each news-derived row (`high`, `medium`, `low`). Researchers learn to weight accordingly.

Per `review_workflow.md` §3.1, single-source Tier 2 maps to MEDIUM at most for `STATUS_CHANGE` items. Phase D adds: **if extraction confidence is `low` on the load-bearing field, priority floors at LOW** for the resulting `STATUS_CHANGE` items (not for `OVERRIDE_CONTRADICTION`, which stays at MEDIUM minimum per §22.3).

---

## 11. Component 6 — Review Queue Surfacing

### 11.1 No new schema work

The existing `review_items` schema, `review_workflow.py` orchestration, `/review` UI surfaces, and `render_news_article_snippet` are sufficient. Phase D does not add tables or UI screens for review.

### 11.2 What changes in payload rendering

The article-derived review item's `payload` is the standard shape from `review_workflow.md` §2.3, with these additions when the source is news:

```json
{
  "field_name": "total_units",
  "current_value": 280,
  "proposed_value": 310,
  "winning_evidence_id": "uuid...",
  "supporting_evidence_ids": ["uuid..."],
  "rule_applied": "most_recent_wins",
  "resolution_confidence": "high",
  "candidates": [...],
  "changes": [
    {
      "field_name": "total_units",
      "old_value": 280,
      "new_value": 310,
      "source": "news_article",
      "evidence_id": "uuid..."
    }
  ],
  "flags": [],
  "news_context": {
    "article_id": "uuid...",
    "extraction_id": "uuid...",
    "reference_id": "uuid...",
    "extraction_confidence": "high",
    "structural_disagreement": null,
    "extraction_version": 1,
    "prompt_id": "extract_v7"
  }
}
```

`payload.changes` matches the existing key written by `collect.py:211` and read by `lib/review/payload.ts:142` — Phase D does not introduce a new shape. Per §10.7.1, news items always carry a single-element `changes` list so `firstChange()` works correctly for Keep-old / Custom decisions.

The frontend reads `news_context` to render:
- The LLM confidence chip.
- A `[View extraction]` link to a per-article audit screen (admin-only — out of scope for D.1, possibly D-late).
- A small chip when `structural_disagreement` is non-null ("Automated parsing read this differently — view passages").

### 11.3 Snippet rendering (already implemented)

`render_news_article_snippet` in `snippets.py:145` already reads:

- `evidence.raw_data.publication`, `published_at`, `author` for the detail line.
- `evidence.extracted_fields[field].highlights` for passage excerpts with offsets.
- The external link via `_external_link`.

Phase D writes this contract correctly. No renderer changes.

### 11.4 Review-queue grouping (existing)

The `/review` UI groups by project. Per §10.7.1, news evidence produces one `STATUS_CHANGE` item per changed field, with a single-element `payload.changes` list. An article that changes 5 fields on one project therefore appears as 5 rows under that project's header in the queue. Reviewers stage A/S/D/F per row; the commit-bar applies them atomically. The existing `(project_id, field_name, item_type)` dedup (`review_workflow.md` §2.4) collapses re-extractions and follow-up articles into the same row.

### 11.5 Staged/committed lifecycle for news items

The C.h staged → committed lifecycle (`review_workflow.md` §4) applies unchanged to news-derived items:

- News-derived `STATUS_CHANGE` items can be staged Accept-new (no-op since the project is already mutated post-apply), Keep-old (writes `researcher_override` of the prior value, which the resolver respects on the next run), Custom (writes a researcher override of the user's value), or Defer.
- News-derived `OVERRIDE_CONTRADICTION` items inherit the existing contradiction-item lifecycle.
- News-derived `NEW_CANDIDATE` accepts route through the existing C.g project-creation flow and `_link_orphan_evidence` linking; the source_run on the review item satisfies the requirement at `review_workflow.py:1349`.
- News-derived `POSSIBLE_MATCH` accepts use the existing matcher's link path; orphan evidence linking and re-resolution work as today.

### 11.6 Deferred news items

The existing defer mechanic (decision_type=defer, item moves to bottom Deferred section in queue) works for news. Article-derived items can be deferred without special-casing.

---

## 12. Component 7 — Worker & Scheduling (extending the existing C.tail.1 RQ worker)

### 12.1 Process model

Phase D runs inside the existing C.tail.1 Render worker (`src/tcg_pipeline/workers/scrape_jobs.py`). That worker is an RQ consumer started via `tcg-pipeline worker` (or `python -m tcg_pipeline.workers.scrape_jobs`). It connects to Redis using `REDIS_URL` and runs `Worker([Queue('scrape_jobs', ...)]).work()`.

Phase D additions:

1. **New RQ task functions** in `src/tcg_pipeline/workers/news_jobs.py`:
   - `run_news_paste_a_link_task(scrape_job_id)` — fetch one article + run the ingest pipeline.
   - `run_news_scrape_task(scrape_job_id)` — discover + enqueue child jobs for a scheduled scrape.
   - `run_news_reextract_task(scrape_job_id)` — re-extract a known article.
   - `run_news_backfill_chunk_task(scrape_job_id)` — process a chunk of URLs in a backfill.
2. **Enqueue helpers** that mirror `enqueue_scrape_job_execution` for these new task functions, writing through `scrape_jobs` rows so the existing audit / status-polling story still works.
3. **Scheduler responsibility** is added to one designated worker process. The simplest approach: a small in-process loop in the worker that runs every 60 seconds, reads `news_sources.schedule_cron`, and enqueues a `news_scrape` job when a tick fires. The designated worker is identified by env (`NEWS_SCHEDULER_LEADER=true`); only one Render instance has it set. If we need leader election later (multi-worker), we add a Postgres advisory lock; for v1, env-flag is sufficient.

The scheduler's tick loop:

```python
def scheduler_tick():
    sources = active_news_sources_with_schedule()
    for source in sources:
        last_run = derive_last_scheduled_scrape(source)   # see below
        if cron_should_fire_now(source.schedule_cron, last_run=last_run):
            enqueue_news_scrape(source_id=source.id, trigger='scheduled')
    write_worker_heartbeat('scheduler')


def derive_last_scheduled_scrape(source: NewsSource) -> datetime | None:
    """Most-recent SCHEDULED scrape start for this news source. Derived; not stored.

    Reads from source_runs rather than a column on news_sources so we don't
    have a second source of truth that could drift.
    """
    return session.execute(
        select(func.max(SourceRun.run_timestamp)).where(
            SourceRun.source_name == source.slug,
            SourceRun.trigger_type == 'scheduled',
        )
    ).scalar()
```

The data-handling tick (running inside RQ's `Worker.work()`):

- RQ pulls one job at a time from Redis, calls the registered task function.
- The task function reads the `scrape_jobs` row by id, runs the matching pipeline, updates the `scrape_jobs` row's status and audit columns.
- On exception, RQ records the failure; the task function should also write `scrape_jobs.error_text` and emit a `system_alerts` row if the error is operationally meaningful (auth-invalid, persistent fetch failure).
- A daemon thread (started at worker boot — see §12.6) writes a `worker_heartbeats` row every 30s independent of task lifecycle. Per-task entry/exit writes are supplementary.

### 12.2 `scrape_jobs` schema extension

Migration adds `kind` and `target_payload` columns and reshapes the active-job unique index. v1 was wrong about the unique index continuing to enforce non-concurrency for news jobs.

```sql
ALTER TABLE scrape_jobs ADD COLUMN kind TEXT NOT NULL DEFAULT 'collector_run';
ALTER TABLE scrape_jobs ADD COLUMN target_payload JSONB;
ALTER TABLE scrape_jobs ALTER COLUMN jurisdiction_id DROP NOT NULL;

DROP INDEX IF EXISTS uq_scrape_jobs_one_active_per_source;

-- Collector-style scheduled jobs remain de-duplicated per (jurisdiction, source).
CREATE UNIQUE INDEX uq_scrape_jobs_one_active_collector
  ON scrape_jobs(jurisdiction_id, source_name)
  WHERE kind = 'collector_run' AND status IN ('queued', 'running');

-- News scheduled scrapes: at most one active scheduled scrape per news source.
CREATE UNIQUE INDEX uq_scrape_jobs_one_active_news_scrape
  ON scrape_jobs(source_name)
  WHERE kind = 'news_scrape' AND status IN ('queued', 'running');

-- Paste-a-link, reextract, backfill chunks: no uniqueness — fan out freely.
```

`kind` values:

- `collector_run` — pre-existing collector-style scrape (LADBS Socrata sources). Default value preserves existing rows.
- `news_scrape` — discover + enqueue child jobs for one news source.
- `news_paste_a_link` — single-article ingest from paste.
- `news_reextract` — re-extract a specified article id.
- `news_backfill_chunk` — batched backfill chunk (up to 25 URLs per chunk to keep job duration bounded).

`target_payload` examples:

- `news_scrape`: `{"news_source_id": "<uuid>", "since": "2026-04-01"}`.
- `news_paste_a_link`: `{"url": "...", "force_reextract": false, "force_project_id": null}`.
- `news_reextract`: `{"article_id": "<uuid>", "prompt_version": "v8", "reason": "..."}`.
- `news_backfill_chunk`: `{"news_source_id": "<uuid>", "urls": [...], "chunk_index": 3, "total_chunks": 12}`.

### 12.3 Sentinel jurisdiction for non-LA paste-a-link

`scrape_jobs.jurisdiction_id` is now nullable, but we'd rather not orphan paste-a-link rows from any jurisdiction context. A migration seeds a sentinel `Unknown / Unscoped` jurisdiction under a sentinel `Unscoped` market that paste-a-link uses when the URL doesn't map to a known LA jurisdiction. Source_runs created from paste-a-link reference the sentinel jurisdiction for the same reason. This keeps Coverage and review queries simple — they can always filter by jurisdiction without special-casing nulls.

### 12.4 Concurrency

RQ workers process one job at a time per worker process. To run multiple article extractions in parallel, scale Render worker instance count. We avoid asyncio fan-out within a single RQ job to keep error attribution clean.

For the LLM extraction calls themselves, asyncio is fine inside one job — sequential within a single article (Pass 0 → 1 → 2a → 2b → 3a/b). Articles are processed serially on one worker; concurrency comes from instance count.

Render starting plan: 1 worker instance, 1 scheduler-flagged. Scale to 2-3 workers during backfill if needed.

### 12.5 Scheduling

`news_sources.schedule_cron` and `news_sources.schedule_timezone` are read every 60 seconds by the scheduler tick. Cron expressions are parsed via `croniter` in the source's IANA timezone (for `urbanize_la`, `America/Los_Angeles`) so the intended local fire time survives DST changes. When a scheduled tick fires, the scheduler inserts a `scrape_jobs` row of kind `news_scrape` with `trigger_type='scheduled'`, then enqueues the matching RQ task. The initial Urbanize candidate is `30 7 * * *`, roughly 75-90 minutes after the observed RSS publication window; D.6 may apply jitter around that configured time.

Process restarts (Render redeploys, OOM kills) are tolerated: the scheduler re-evaluates cron on startup against `derive_last_scheduled_scrape(source)` (computed from `source_runs` per the helper above — no column on `news_sources`) to decide whether a missed tick should fire. Missed daily ticks fire at most once per day.

### 12.6 Worker observability

- **Heartbeat**: a dedicated daemon `threading.Thread` started at worker-process startup writes its `worker_heartbeats` row every 30s **independent of task lifecycle**. This is critical because RQ workers are single-threaded job consumers; long-running jobs (extraction of a complex multi-project article, backfill chunks) would otherwise produce stale heartbeats and trigger false-positive Render restarts.

    ```python
    # src/tcg_pipeline/workers/heartbeat.py
    import threading, time
    from datetime import datetime, UTC

    def start_heartbeat_thread(worker_name: str, session_factory) -> threading.Thread:
        def loop():
            while True:
                try:
                    with session_factory() as session:
                        upsert_heartbeat(session, worker_name, datetime.now(UTC))
                        session.commit()
                except Exception:
                    log.exception("heartbeat write failed")
                time.sleep(30)

        thread = threading.Thread(target=loop, name=f"heartbeat-{worker_name}", daemon=True)
        thread.start()
        return thread
    ```

    The daemon flag ensures it doesn't block process shutdown. Each task may also write a heartbeat on entry/exit as a supplementary signal, but the 30s loop is the primary liveness signal.

- **Health endpoint**: the Worker runs a tiny aiohttp/Starlette health server on a side port (Render expects HTTP). `GET /healthz` returns 200 iff this worker's `worker_heartbeats.last_heartbeat_at` is < 5 minutes old, else 503. Render restarts the dyno on 503.

- **Per-job logging**: each task emits a structured log line at start and end (job_id, kind, duration, outcome, articles_processed, cost_spent).

- **Per-cycle alerts**: after every job, the worker dispatches alerts based on rolling counters (extraction error rate, fetch failure rate, etc.) per §13.

### 12.7 Backfill mode

`tcg-pipeline news backfill --source urbanize_la --since <12mo>` runs a 12-month Urbanize backfill. Backfill:

1. Calls `PoliteNewsCollector.discover_urls(since=...)` and gets the URL list from the configured sitemap/archive path. D.2v observed 988 Urbanize URLs with `lastmod >= 2025-05-01`; because `urbanize_la` is market-unscoped, D.B prices the full count, not an LA-filtered subset.
2. Chunks the URL list into groups of 25 and inserts one `news_backfill_chunk` `scrape_jobs` row per chunk.
3. The worker drains them through the normal pipeline.
4. Cost-capped — if we hit the cap, queued chunks stay queued until the cap resets at midnight PT or the researcher bumps it.

The backfill spans multiple worker hours, not one shot. That's fine — the cost cap and visible queue depth keep the operation observable.

---

## 13. Component 8 — Monitoring, Alerting, Cost Control

### 13.1 Alert channels

All alerts go to two destinations:

1. **Email to `ng@theconcordgroup.com`** via SMTP (Mailgun / SendGrid; pick whichever is cheapest with $5/mo free tier). Plain text.
2. **In-app banner on `/coverage`** rendered from a `system_alerts` table (small, one row per active alert, dismissable per-user).

### 13.2 Alert conditions

| Condition | Severity | Detection | Cooldown |
|---|---|---|---|
| Source block/auth signal on active source | High | Repeated `401/403/429/503`, login redirect, or metered/subscriber-only marker where source config expects open access | Email immediately; banner persists until source is resumed; Worker pauses that source when policy requires it |
| Worker heartbeat stale > 5 min | High | Health endpoint reports stale | Email; Render restarts dyno automatically; banner persists until heartbeat fresh |
| Daily cost ≥ warn cap ($25) | Medium | Cost rollup check on each LLM call | Email once per day; banner persists until next midnight PT |
| Daily cost ≥ hard cap ($35) | High | Same | Email; Worker pauses LLM calls (in-flight calls allowed to finish); banner persists until cap bumped or cap resets |
| Pass-2 schema-invalid rate > 5% over last 100 calls | Medium | Rolling counter in Worker | Email once per hour while elevated; banner persists |
| Pass-2 LLM API error rate > 10% over last 50 calls | Medium | Same | Email once per hour while elevated; banner |
| Article fetch failure rate > 20% over last 50 fetches | Medium | Rolling counter | Email once per hour while elevated; banner |
| Hard fetch error (HTTP 5xx repeated) on active source for > 1 hour | Medium | Rolling counter | Email once per hour |

### 13.3 Cost cap enforcement (race-protected)

With multiple RQ worker instances, two workers can each read the cost rollup and each see "we have $1 of headroom" simultaneously, then both fire calls and overshoot. Phase D protects against this with a Postgres advisory lock around the cap check + cost reservation.

```python
NEWS_COST_CAP_LOCK_KEY = 0x4E_45_57_53_43_41_50  # 'NEWSCAP' as int64

def can_make_llm_call(session, estimated_cost: float) -> bool:
    """Acquire advisory lock; check cap; reserve estimated cost; release lock.

    The lock is held only for the cap-check transaction, NOT for the duration
    of the LLM call. Multiple workers serialize at the cap check but proceed
    in parallel for the LLM call itself.
    """
    today = pt_today()
    with session.begin():  # transaction lock auto-released on commit/rollback
        # pg_advisory_xact_lock blocks until the lock is acquired; cheap when uncontended.
        session.execute(text("SELECT pg_advisory_xact_lock(:k)"),
                        {"k": NEWS_COST_CAP_LOCK_KEY})
        spent_today = current_day_spend(session, today)
        cap = active_cost_cap(session, today)
        projected = spent_today + estimated_cost
        if projected > cap.daily_hard_usd:
            emit_alert(session, "daily_cost_hard_cap_reached", spent_today, cap.daily_hard_usd)
            return False
        if projected > cap.daily_warn_usd:
            emit_alert_once(session, "daily_cost_warn_cap_reached", spent_today, cap.daily_warn_usd)
        # Pre-reserve via UPSERT on news_extraction_costs row (cost_date, pass='reserved', model='_reservation_').
        # When the actual call returns, write the real news_extractions row and decrement the reservation.
        reserve_cost(session, today, estimated_cost)
        return True
```

`estimated_cost` is computed from a rolling-average per-pass cost (Haiku ~$0.003, Opus ~$0.05) plus a 25% buffer.

**Overshoot tolerance under concurrency.** Even with the advisory lock, in-flight calls past the lock can still complete after the cap is reached — the actual cost lands in the rollup when the LLM returns. With 2-3 workers and per-call cost ≤ $0.10, max overshoot is bounded at `(workers - 1) × max_call_cost ≈ $0.20`. Acceptable. If we scale to many workers, the reservation pattern (pre-charge before call, true-up after) absorbs this exactly.

**When the cap is reached**, the Worker continues to drain non-LLM jobs (article fetches still happen; their results queue for extraction), but Pass 2/3 are paused.

Researcher bumps the cap via `/coverage` admin panel:

```http
POST /research/cost/bump
{"cap_usd": 50, "expires_in_hours": 8, "note": "backfilling Q1 articles"}
```

This writes `news_cost_caps.override_until = NOW() + 8h, override_hard_usd = 50`. The Worker re-checks the active cap on the next call and resumes.

### 13.4 In-app news ops banner

`/coverage` gets a "News Operations" tile (rendered when any of these are true):

- Active source pause/block/auth alert.
- Active cost cap warning or hard pause.
- Active extraction error rate alert.
- Worker heartbeat stale.

Each row in the tile has the situation, last-detected timestamp, and an action button (refresh auth / bump cap / view logs). Buttons hit `/research/*` admin routes. Allowlisted users only (`ALLOWED_EMAILS`).

### 13.5 Audit transparency surfaces

For any article, a researcher can:

1. **From a review item:** click `[View article]` → opens the article in a new tab via `external_link`.
2. **From a review item:** see passage excerpts inline, with offsets; click `[View full article + highlights]` → opens an internal page showing the article body with all extracted highlights overlaid.
3. **From an article admin view (`/research/articles/{id}`):** see all extractions (history), all references, all match decisions, all cost data. Allowlisted only.

For the system overall:

- `/research/cost?since=YYYY-MM-DD` returns the cost rollup as JSON.
- `/research/extractions?since=...&model=...&parse_status=...` returns the extraction audit log as JSON.
- `/research/extractions/{id}/raw` returns the full LLM input + output as JSON.

These are powering admin-mode debugging surfaces, not researcher-facing primary UI. They exist because we need to be able to ask "why did the system extract X from this article" months after the fact.

---

## 14. Component 9 — Discarded Articles Graveyard

### 14.1 Goal

Researchers should be able to browse articles the matcher discarded and manually relink any of them to a project. This catches matcher false negatives (article was about a real project we couldn't connect to).

### 14.2 UI surface

`/research/graveyard` (Next.js page):

- List of `news_articles` where ALL `news_project_references.match_status = 'discarded'` and `news_articles.triage_status = 'relevant'`. (Articles triaged not_relevant don't appear; they were correctly skipped.)
- Filterable by date range, news source, candidate developer, candidate name.
- Each row shows: title, publication date, top extracted fields, "discard reason" (the highest-scoring near-match's reason and score, or "no signals strong enough" if none).
- Click into an article: full body view with highlights, all extracted references, candidate match scores, "Relink to project" picker.

### 14.3 Manual relink action

```http
POST /research/articles/{article_id}/references/{reference_id}/relink
{"target_project_id": "uuid", "note": "this is the ABC project that we created last week"}
```

Behavior:

- Updates `news_project_references` row: `match_status='manual_relink'`, `matched_project_id=target`, `manual_relink_by_user_id=user`, `manual_relink_at=NOW()`, `manual_relink_note=note`.
- Inserts an `evidence` row (same shape as confirmed match), with `notes='manual_relink: <note>'`.
- Calls `resolve_project(target, apply=True)` to re-resolve.
- Writes a `change_log` row with `change_type=researcher_confirmed`.

Manual relinks are an explicit reviewer override of the matcher's decision; they're audited and reversible (a future relink to a different project just creates another evidence row; old one is left intact).

---

## 15. Reprocessing & Re-extraction Semantics

### 15.1 Why reprocessing matters

Prompts evolve. Models improve. The signal-flag registry expands. The structural-extractor regex set is updated. An article ingested in May 2026 should still produce useful evidence when extracted against a January 2027 prompt. Without re-extraction support, every Phase D evidence row is "whatever the prompt happened to be that week."

### 15.2 What is re-extractable

| Layer | Re-extract? | Why |
|---|---|---|
| Pass 0 (article ingest) | No | Article body and metadata are durable. Re-fetch only on explicit request. |
| Pass 1 (structural) | Yes, automatically | When the structural extractor version changes, the Worker re-runs Pass 1 against affected articles. Free. |
| Pass 2 (LLM extraction) | Yes, on demand | Triggered by prompt-version bump or user request. Costs LLM tokens. |
| Pass 3 (re-extraction) | Yes, on demand | Same as Pass 2. |

### 15.3 Prompt version bumps

`tcg-pipeline news bump-prompt-version --prompt extract --new-version v8`:

1. Validates the new prompt template exists in `src/tcg_pipeline/news/prompts/extract_v8/`.
2. Updates a config row pointing the Worker at the new version.
3. Future extractions use v8.
4. Existing articles are unaffected.

`tcg-pipeline news reextract-articles --prompt-version v8 --since 2026-04-01 --dry-run`:

1. Selects articles where `current_extraction.prompt_version != v8` and the article is in scope.
2. For each, enqueues a `news_reextract` job.
3. Each job runs Pass 2 (and Pass 3 if conflict triggers fire) against the new prompt.
4. **Materially-changed-output gating**: after the new extraction lands, compare it to the previous extraction's emitted fields. If the changes pass the existing field-change thresholds (unit delta > 5, status changed, developer changed, etc.), the new evidence row is inserted normally and a review item is generated. If the diff is below threshold, the new extraction is recorded but no new evidence row or review item is created — we don't pollute the queue with cosmetic re-extraction noise.
5. `--dry-run` mode reports what would change without writing.

### 15.4 Re-extraction cost containment

Bulk re-extraction can be expensive. The CLI prompts for confirmation when the projected cost exceeds 50% of the daily cap, and re-extraction respects the same cost cap as live ingest. A backfill runs over multiple days if needed.

### 15.5 Re-extraction supersession (corrected from v1)

When re-extraction produces materially-changed output for a reference, two evidence rows now exist for the same article+reference position. v1 said "most recent wins" handles this — but evidence rows from re-extraction of the same article share `evidence_date` (article's published_at) and would have shared `collected_at` (article's fetched_at), tying every term in the resolver's sort order. The resolver could not deterministically choose the new extraction.

The corrected design uses two mechanisms:

**1. `collected_at` is the integration time, not the article fetch time.** §10.4 already specifies `collected_at = now_utc()` at evidence-write time. Re-extracted evidence rows therefore have a strictly later `collected_at` than the original, and the resolver's sort tiebreak (`evidence_date DESC, collected_at DESC, source_tier ASC`) selects the new row.

**2. Explicit supersession flag on evidence.** The migration adds `evidence.superseded_at TIMESTAMPTZ` (nullable). When the integrator writes a re-extracted evidence row, it also sets `superseded_at = NOW()` on the prior evidence row(s) for the same `(article_id, reference_index, field-set)`. A partial index `WHERE superseded_at IS NULL` scopes resolver reads to active rows only; superseded rows remain queryable for audit but never participate in resolution.

This belt-and-suspenders avoids both:
- A resolver bug where stale evidence wins on a tied ordering tuple.
- A future requirement that prior extractions be temporarily revived (just clear `superseded_at` to bring the prior row back).

The integrator must:

```python
def integrate_reextracted_reference(reference: NewsProjectReference, prior_evidence: Evidence | None):
    new_evidence = build_evidence_row(reference, collected_at=now_utc())
    session.add(new_evidence)
    if prior_evidence is not None:
        prior_evidence.superseded_at = now_utc()
    if reference.match_status == 'confirmed':
        new_evidence.project_id = reference.matched_project_id
        resolve_project(reference.matched_project_id, session, apply=True)
```

The resolver's evidence-loading queries get a small change: add `WHERE superseded_at IS NULL` to all `evidence` SELECTs. This is a single change in `tcg_pipeline.resolution.engine.load_project_evidence` and the equivalent contradiction service queries.

### 15.6 Old extraction retention

`news_extractions` rows are never deleted. The `current_extraction_id` pointer moves but old rows remain queryable. This is the audit trail.

A `tcg-pipeline news prune-extractions --before <date>` exists for genuine cleanup if storage becomes an issue (unlikely — extraction rows are <10KB each), but it's gated behind `--apply` and audits what it deletes.

---

## 16. Auth / Credentials Management (D.late.C)

Active D.2a/D.6 Urbanize collection does not require credentials. This section
is retained as the paid-source design for D.late.C, with BizJournals as the first
known consumer once that capability is approved and scheduled.

### 16.1 BizJournals cookie rotation

`tcg-pipeline news auth-bizjournals`:

```python
def auth_bizjournals():
    # 1. Ask researcher to login via a real Chromium window.
    print("A browser window will open. Log into bizjournals.com, then press Enter.")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://www.bizjournals.com/losangeles/login")
        input("Press Enter after you have completed login...")

        storage_state = context.storage_state()  # {cookies, origins}
        browser.close()

    # 2. Validate by fetching a known subscriber-only article.
    if not validate_session(storage_state):
        print("Validation failed — session does not look authenticated. Aborting.")
        return

    # 3. Encrypt and persist.
    session = SessionLocal()
    payload = json.dumps(storage_state).encode("utf-8")
    encrypted = pgp_sym_encrypt(payload, key=os.environ["SERVICE_CREDS_KEY"])
    upsert_service_credential(
        session,
        slug="bizjournals_session",
        payload_encrypted=encrypted,
        expires_at=now() + timedelta(days=30),  # conservative TTL
    )
    print("Cookie state saved. Worker will resume on next tick.")
```

Validation step is critical — we never persist a non-authenticated state. The validation issues a `GET` (not HEAD — HEAD returns no body to inspect) against a known subscriber-only article URL stored in `news_sources.config.validation_url`. A 200 response whose body contains the subscriber-only article content (and lacks the metered-paywall stub class / "log in to read" markers) = authenticated. The validator inspects:

- HTTP status (must be 200).
- Body length (subscriber-only articles return ≥ ~5 KB of body; metered stubs are < 1 KB).
- Body content: presence of expected article structural markers (`<article>` with full text), absence of paywall login prompt classes.
- JSON-LD `articleBody` length (subscriber-only sites embed full text in JSON-LD when authenticated).

A failed validation aborts the cookie-rotation flow; the prior cookie remains active.

### 16.2 Session-invalid detection during scraping

Every fetch in the Worker checks for invalidation markers:

- HTTP 302 to `/login`.
- Response body contains "log in to read" / "subscriber-only" markers when we expect full body.
- JSON-LD shows truncated content fields.

On detection, the Worker:

1. Logs the invalidation reason.
2. Pauses BizJournals scraping (`news_sources.config.paused_until = NOW() + 24h` or until rotation).
3. Emits the alert (email + banner).
4. The next tick detects the new auth.json (uploaded via `auth-bizjournals` CLI) and clears the pause.

### 16.3 Encryption key management

`SERVICE_CREDS_KEY` is a 32-byte random key stored in Render env. Key rotation is a future operational concern — for MVP, one key is fine. If the key is lost, the cookie is unrecoverable; researcher reruns `auth-bizjournals` and we move on.

### 16.4 Why not just store cookies in Render env

Render env variables are visible to anyone with project access in the Render dashboard. The DB-backed approach gives us:

- RLS isolation (only service role can read).
- Audit (`set_at`, `rotated_at`, `set_by_user_id`).
- Programmatic rotation (the CLI does it, no human touches the secrets dashboard).
- Encryption at rest in Postgres.

---

## 17. Cost Model

### 17.1 Per-article cost expectations

Using current Anthropic pricing (April 2026 rates encoded in code):

| Pass | Model | Input | Cache creation | Cache read | Output | Per-article cost |
|---|---|---|---|---|---|---|
| 2a triage | Haiku 4.5 | ~3K tokens | first-call static prompt writes only | near-zero | ~100 tokens | ~$0.005 |
| 2b extract | Opus 4.7 | ~5K article tokens | glossary/system cache writes on miss | ~30K+ glossary tokens on hit | ~1K tokens | ~$0.20 cached / ~$0.75 cache miss |
| 3 reextract | Opus 4.7 | ~5K article tokens | glossary/system cache writes on miss | ~30K+ glossary tokens on hit | ~1K tokens | ~$0.20 cached / ~$0.75 cache miss |

Cost accounting tracks regular input, cache-creation input, cache-read input,
and output separately because Anthropic bills cache writes above the base input
rate and cache reads below it.

### 17.2 Daily budget

Assume:
- 10-20 Urbanize URLs discovered on a typical weekday RSS run.
- 50-60% triaged relevant (broad-net): 5-12 articles to Pass 2.
- 20% trigger Pass 3: 1-3 articles to Pass 3.

Daily cost depends on cache-hit behavior. For a tight scrape batch, a working
estimate is `20 × $0.005 (triage) + 12 × $0.20 (extract) + 3 × $0.20
(reextract) = $0.10 + $2.40 + $0.60 = ~$3.10/day`. Sporadic paste-a-link calls
are reserved at `$0.75` each to cover cache misses against the current LA
glossary size.

This is well under the $25 warn cap. The cap exists to catch:

- Runaway loops (a bug that retries extraction).
- Backfill spikes (12-month Urbanize backfill starts from the 988 URLs observed in D.2v).
- Future expansion to multiple sources.

### 17.3 Backfill cost (Urbanize D.B)

D.2v observed 988 Urbanize URLs with `lastmod >= 2025-05-01`. Because
`urbanize_la` is market-unscoped, the D.B dry-run prices the full URL set rather
than filtering to LA-labeled articles before fetch/triage.

For 988 URLs: triage all is roughly ~$5. If 50-60% pass triage, Pass 2 touches
~500-590 articles; at cached-batch rates that is roughly ~$100-$120, with Pass 3
adding another ~$20-$25 if 20% of extracted articles re-extract. The dry-run
must report ranges for relevance, projected LLM cost, cache-hit assumptions, and
runtime instead of a single optimistic estimate.

This may need a temporary cap bump above the default $35 hard cap or a multi-day
backfill window. Researcher kicks off the backfill in the morning; it completes
over a few hours when cap headroom allows.

The older 2,800-article figure remains useful as a multi-source expansion
capacity sanity check, not as the Urbanize D.B estimate.

### 17.4 Caching effect

The "static" portion of the prompt — system prompt + glossary + signal-flag registry + active project list — is ~12KB / ~10K tokens. With `cache_control` ephemeral, this is served from cache for ~5 minutes after first use. With ≥1 article every 5 minutes during scrape windows, cache hit rate is >95% on the static content. This drives the cost from ~$0.15/article to ~$0.05/article.

### 17.5 Pricing changes

Pricing rates live in `src/tcg_pipeline/news/llm.py` as a versioned dict. When Anthropic changes prices, we add a new dict version and `cost_usd` for new extractions uses the new rates. Old extractions retain their original cost figures.

---

## 18. API Surface (FastAPI)

All routes auth-gated by Supabase JWT + `ALLOWED_EMAILS`. Mutations stage `scrape_jobs` rows or update admin tables; the Worker does the actual work.

### 18.1 Article ingest

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/research/articles` | Paste-a-link entry. Body `{url, force_reextract?, force_project_id?, note?}`. Returns `{article_id, scrape_job_id, existing_article}`. |
| `GET` | `/research/articles/{id}` | Article detail (admin view). Returns article + all extractions + references + match decisions. |
| `GET` | `/research/articles/{id}/extractions` | Extraction history for the article. |
| `POST` | `/research/articles/{id}/reextract` | Force re-extract. Body `{prompt_version?, reason?}`. Enqueues a `news_reextract` job. |
| `POST` | `/research/articles/{id}/refetch` | Force re-fetch. Rare. Body `{reason}`. |
| `GET` | `/research/articles/{id}/body` | Article body text + highlights overlay (renderable in the audit UI). |

### 18.2 Reference relinking

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/research/articles/{article_id}/references/{reference_id}/relink` | Manual graveyard relink. Body `{target_project_id, note}`. |
| `POST` | `/research/articles/{article_id}/references/{reference_id}/discard` | Manually mark a reference as discarded (rare — when a confirmed/possible match was wrong). |

### 18.3 Graveyard

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/research/graveyard` | Paginated discarded-article list. Query params: `since`, `news_source_id`, `developer`, `q` (free-text search), `limit`, `offset`. |

### 18.4 Cost / admin

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/research/cost` | Cost rollup. Query `?since=YYYY-MM-DD`. Returns daily rows. |
| `GET` | `/research/cost/today` | Today's spend, current cap, time until reset. |
| `POST` | `/research/cost/bump` | Bump today's hard cap. Body `{cap_usd, expires_in_hours, note}`. |

### 18.5 Paid-source auth (deferred to D.late.C)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/research/auth/bizjournals/status` | D.late.C. Returns `{valid, expires_at, last_validated_at, last_invalidated_at, last_invalidated_reason}`. |
| `POST` | `/research/auth/bizjournals` | D.late.C. Receives an auth.json blob from the CLI. Server-side encryption + persist. |
| `POST` | `/research/auth/bizjournals/validate` | D.late.C. Force a validation check. |

### 18.6 Source admin

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/research/sources` | List news sources with status. |
| `POST` | `/research/sources/{id}/pause` | Pause a source. Body `{until?}`. |
| `POST` | `/research/sources/{id}/resume` | Resume a paused source. |

### 18.7 Extraction admin

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/research/extractions` | Audit log. Filters: `since`, `model`, `pass`, `parse_status`, `prompt_id`, `triggered_by`. |
| `GET` | `/research/extractions/{id}/raw` | Full input + output JSON. |

### 18.8 RLS posture

All `/research/*` routes use the privileged FastAPI DB session, validated by Supabase JWT + allowlist. No direct PostgREST writes. Reads (e.g., review items including news context) continue to flow through PostgREST against the read-only views and the existing `review_items` + `evidence` tables.

---

## 19. CLI Surface

`src/tcg_pipeline/cli.py` extensions (`tcg-pipeline news ...`):

```
tcg-pipeline news scrape --source urbanize_la [--since YYYY-MM-DD] [--limit N]
    Run a scrape now (manually triggered). Writes scrape_jobs rows; Worker drains.

tcg-pipeline news backfill --source urbanize_la --since YYYY-MM-DD
    Bulk backfill helper. D.B uses a 12-month Urbanize horizon.

tcg-pipeline news ingest-url <url> [--reextract] [--force-project <uuid>]
    Same as POST /research/articles, but from the CLI.

tcg-pipeline news reextract-articles --prompt-version v8 [--since YYYY-MM-DD] [--dry-run]
    Bulk re-extract on prompt version bump.

tcg-pipeline news bump-prompt-version --prompt extract --new-version v8
    Promote a prompt version to active.

tcg-pipeline news propose-flags [--since YYYY-MM-DD] [--limit N]
    LLM-driven novel-flag mining. Output to a file for researcher review.

tcg-pipeline news cost [--since YYYY-MM-DD]
    Print cost rollup table.

tcg-pipeline news doctor
    Health check: source routing, robots/fetch config, prompt configuration, model availability,
    cost rollup integrity, Worker heartbeat. Prints a diagnostic.

tcg-pipeline news auth-bizjournals
    D.late.C only. Refresh the BizJournals cookie. Opens a real browser;
    researcher logs in; cookie is stored encrypted.
```

Each command writes audit rows so its execution is reproducible.

---

## 20. Reliability — Failure Modes & Recovery

### 20.1 Failure mode catalog

| Failure | Detection | Recovery | Impact |
|---|---|---|---|
| Active source blocked/paused | Repeated `401/403/429/503`, robots disallow, or configured block marker | Email + banner; source pauses per policy until researcher resumes or config changes | New scrapes for that source pause; other sources continue |
| Paid-source cookie expired (D.late.C) | Auth-invalid response markers; next fetch fails | Email + banner; researcher runs source auth CLI; Worker resumes on next tick | Paid-source scrapes paused for hours, not days |
| Article fetch fails (transient network) | HTTP 5xx from origin | Retry with exponential backoff (3 attempts); persist `fetch_failed` after | Article retried automatically next tick |
| Article body unreadable | Body < 200 chars or extractor returns no article-like text | Persist `parse_failed`; alert if rate > 20%. Advanced/browser fallback is D.late.ADV only. | Per-article; rare unless source escalates |
| Anthropic API outage | Connection error / 5xx from anthropic | Retry with exponential backoff (3 attempts); enqueue extraction for later | Extractions resume when API recovers |
| Anthropic rate limit | 429 from anthropic | Backoff per retry-after header; lower concurrency | Slows ingest, no data loss |
| Anthropic schema-invalid response | Parse error on output_json | Persist row with parse_status='schema_invalid'; alert if rate > 5% | Article waits for prompt fix |
| Anthropic content refusal | `stop_reason=refusal` | Mark `parse_status='refused'`; flag for researcher manual extraction | Rare |
| Prompt-template bug (pre-deploy) | Eval set fails | Pre-deploy gate (§21) blocks the bad prompt | Prevented |
| Cost cap exceeded | Pre-call check | Worker pauses LLM; researcher bumps cap | No data loss |
| Worker process crash / OOM | Render dyno restart on 503 / heartbeat stale | Render auto-restart | Few minutes downtime |
| Database deadlock during integrate | SQLAlchemy DeadlockError | Retry the transaction; if persistent, abort the job | Per-job; rare |
| `news_articles` URL collision (race) | UNIQUE violation on `url_hash` | Deterministic — second worker reads existing row | Idempotent |
| Re-extraction floods queue with cosmetic changes | Materially-changed gating in §15.3 | Filter at integrator | No queue noise |
| Schema drift (column added but worker not updated) | Worker startup sanity check | Worker refuses to start; fix and redeploy | Prevents corruption |
| Dependency upgrade breaks fetch/extraction library | CI fixture test catches | Pin major versions; explicit upgrade testing | Caught pre-deploy |
| Paid-source `auth.json` payload corrupted (D.late.C) | Validation step fails post-decrypt | Researcher reruns source auth CLI | Same as cookie expiry |
| LLM emits a fake offset (offset doesn't exist in body) | Validate offsets server-side; mark passage with `valid_offset: false` | The passage still renders but without highlight; reviewer sees flag | Per-passage; soft |
| Article published_at missing | Falls back to fetched_at | Evidence date is approximate; flag in evidence notes | Soft |
| Two extractions in flight on the same article (race) | Single-flight guard in worker (`SELECT FOR UPDATE` on `news_articles.id`) | One waits, the other proceeds | Idempotent |
| Prompt cache eviction during burst | Higher uncached cost for one call | Self-recovers | Cost spike <$0.10 |
| Render Redis (if added later) outage | N/A — using Postgres queue | — | — |

### 20.2 What detection cannot miss

These are the failure modes that must produce alerts within hours, not days:

1. **Active source blocked or paused.** Email + banner quickly. Worker visibly paused for that source.
2. **Worker not running.** Heartbeat staleness > 5 min triggers Render restart; > 30 min triggers email.
3. **Cost cap reached.** Email + banner; Worker visibly paused on LLM calls.
4. **Source went dark.** If `urbanize_la` returns zero new URLs for > 36 hours during expected coverage window, alert.

### 20.3 Replay / re-process semantics

Because `news_extractions` is append-only and `news_project_references` carries everything needed to rebuild evidence, a corruption-recovery is straightforward:

- "Reset article X's evidence" → mark all `news_project_references.match_status` for the article's current extraction as `reset`, delete the corresponding evidence rows, re-run the matcher and integrator.
- "Reset all article evidence since date Y" → bulk variant of the above.
- These are exposed as CLI commands, not API routes.

---

## 21. Accuracy — Prompt Versioning, Eval Set

### 21.1 Prompt versioning

All prompts live in `src/tcg_pipeline/news/prompts/`:

```
prompts/
  triage_v1/
    system.md
    user.md
    schema.json
  extract_v1/
    system.md
    user.md
    schema.json
  reextract_v1/
    system.md
    user.md
    schema.json
```

Each version is immutable once promoted. New versions go in new directories.

`config/news_prompts.yaml` declares which prompt version is active for each pass:

```yaml
active:
  triage: triage_v1
  extract: extract_v1
  reextract: reextract_v1
```

Bumping versions is a config + Worker restart. No DB migration. The Worker logs the active versions on startup.

### 21.2 Eval set (deferred but scoped)

A versioned ground-truth set under `tests/data/news_eval/`:

```
news_eval/
  cases/
    case_01_helio_groundbreaking.json
    case_02_multi_project_summary.json
    case_03_opposition_lawsuit.json
    case_04_no_match_relevant.json
    case_05_paywalled_partial_body.json
    ...
  cases.yaml      # metadata: id, description, expected_outcome, tags
```

Each case is one article (HTML + body_text snapshot) with hand-labeled expected outputs:

```json
{
  "case_id": "01_helio_groundbreaking",
  "expected_relevance": "confirmed",
  "expected_project_references": [
    {
      "candidate_name": "Helio",
      "candidate_developer": "Helio Capital",
      "candidate_unit_total": 310,
      "candidate_signal_flags": {"groundbreaking_announced": true},
      "candidate_status_signal": "Under Construction",
      "match_target_project_id": "<uuid>",
      "expected_match_status": "confirmed"
    }
  ]
}
```

`tcg-pipeline news eval --prompt extract_v8` runs the eval set against a candidate prompt and reports precision / recall / per-field accuracy.

**Bootstrap of the eval set is deferred but scoped explicitly:**

1. We don't hand-label 50 articles upfront — the labor is too high before we have signal.
2. After the first 4 weeks of D shipping, we pick the 30–50 most-recently-reviewed news articles where reviewer decisions were definitive (accept-new or keep-old, not deferred). The reviewer's decision becomes the ground truth.
3. CLI tool `tcg-pipeline news bootstrap-eval --since 2026-05-01 --limit 50` produces draft labels from review decisions; researcher confirms / corrects in a 1–2 hour pass.
4. After eval set exists, prompt-version bumps are gated on `--eval-pass-rate >= 0.90`.

**This is captured as a roadmap item D-late** (see §25.4), not part of the initial Phase D ship.

### 21.3 Production accuracy monitoring

Even before an eval set exists, we collect production accuracy signals:

- Acceptance rate: (review items accepted) / (review items raised) per source per week.
- Reject-with-correction rate: when reviewers correct a value (custom decision) vs. accept-new vs. keep-old.
- Manual relink rate from graveyard: indicates matcher false negatives.
- Override-contradiction rate from news evidence: how often does news contradict reviewer overrides?

These are surfaced in a small `/research/quality` admin page after MVP. Acceptance rate < 60% sustained = prompt regression; investigate.

---

## 22. Audit & Transparency

### 22.1 Per-evidence-row audit chain

Given any evidence row written by Phase D, a reviewer can trace:

1. `evidence.source_record_id` → `news_project_references.id`.
2. → `news_project_references.extraction_id` → `news_extractions` row (with prompt, model, cost).
3. → `news_extractions.article_id` → `news_articles` row (with full body, raw HTML, fetched_at, fetch_status).
4. The extraction's `output_json` contains the full LLM input + output.
5. Each `extracted_fields[field].highlights` references character offsets into `news_articles.body_text`.

That's a complete chain from "the project's developer is Helio Capital because…" to "…the LLM extracted that from the Urbanize article fetched 2026-04-08, at offset 145–158, which we can show you with the surrounding sentence."

### 22.2 Re-runnability

Any extraction can be re-run to produce a comparable artifact:

- The original `news_extractions.prompt_hash` lets us reproduce the exact prompt string.
- The article body is preserved in `news_articles.body_text`.
- The signal-flag registry at extraction time can be reconstructed from `news_signal_flag_registry.added_at` / `retired_at`.
- The model version is stored.

### 22.3 Decision audit chain

The existing `change_log` table captures every researcher decision on a news-derived field change. The chain:

`change_log.review_item_id` → `review_items.payload.news_context.article_id` → article + extraction context.

This means every decision is auditable end-to-end: "On 2026-04-30, NG accepted that the project's status changed from Approved to Under Construction. The proposed value came from Urbanize article X published 2026-04-28, extracted with prompt extract_v7 by Opus 4.7 at cost $0.04, anchored to passage 'Helio broke ground last week' at offset 220–252."

### 22.4 What is intentionally not audited

- The Render Worker's internal scheduling decisions (which job ran first). Logs are sufficient.
- Per-tick microbursts of cache hit/miss. Aggregated at the rollup level.
- Individual researcher session navigation. Out of scope.

---

## 23. Sequencing — Build Plan

### 23.1 Recommended order (corrected from v1)

v1 listed D.W as "Render Worker scaffold." That's wrong — C.tail.1 already shipped the worker (`src/tcg_pipeline/workers/scrape_jobs.py:26`). v2 reframes D.W as "extend the existing C.tail.1 RQ worker."

| Step | Task | Approx. effort | Depends on |
|---|---|---|---|
| **D.1** | Two Alembic migrations per §4.15: news content tables, then `scrape_jobs` extension + `evidence.superseded_at`. Seed `news_signal_flag_registry`, `news_sources`, `news_cost_caps`, sentinel jurisdiction. RLS + summary views per §4.14. Add Phase D dependencies to `pyproject.toml` per §27. | 2–3 days | C.l-bis, C.tail.1 |
| **D.W** | Extend the existing C.tail.1 RQ worker: register `news_paste_a_link`, `news_scrape`, `news_reextract`, `news_backfill_chunk` task functions; add the in-process scheduler tick gated by `NEWS_SCHEDULER_LEADER`; add worker heartbeat writer; add Worker side `/healthz` server. Add `system_alerts` dispatcher hooks. | 2–3 days | D.1 |
| **D.7a** | Paste-a-link minimal vertical: FastAPI `POST /research/articles` writes a `scrape_jobs(kind='news_paste_a_link')` row and enqueues into RQ; worker fetches via httpx + trafilatura; Pass 0 (ingest) only; admin view shows article + body + metadata. | 2 days | D.W |
| **D.3a** | Pass 1 structural extractor library + tests. Wired automatically after Pass 0. Adds `news_articles.structural_signals`. | 2 days | D.7a |
| **D.3b** | Pass 2a triage (Haiku) — prompt + structured output + cost tracking + `news_extraction_costs` rollup writes. | 1 day | D.3a |
| **D.3c** | Pass 2b extraction (Opus) — prompt + structured output schema + glossary cache + signal-flag registry consumption. Matching, evidence writes, and materially-changed gating remain in D.3d/D.4. | 3 days | D.3b |
| **D.3d** | Extraction-time Pass 3a: Pass 1/2 conflict, low-confidence, parse-error/refusal triggers (NOT match-triggered, which ships in D.4). | 1 day | D.3c |
| **D.4** | Article matcher (`news_matcher`) — Tier 1/2/3/4 from §9. Match-triggered Pass 3b on `new_candidate`. Evidence integrator with post-apply review semantics per §10. SourceRun creation per §10.3. Connect to existing `_link_orphan_evidence` for orphan accept paths. End-to-end: paste-a-link → fetch → extract → match → evidence row → `resolve_project(apply=True)` → STATUS_CHANGE/OVERRIDE_CONTRADICTION review item. | 4 days | D.3d |
| **D.4-resolver** | Implement §21f recent-article delivery-date priority in the resolver (`delivery_year.py` `_select_explicit_delivery_observation`). Add resolver-side filter `WHERE evidence.superseded_at IS NULL` so re-extraction supersession works. | 1 day | D.4 |
| **D.5** | Review-queue rendering of news context. `news_context` payload extension. Confidence chip. Structural-disagreement chip. Verify the existing `render_news_article_snippet` works against the data Phase D writes. | 2 days | D.4 |
| **D.7b** | Paste-a-link UI in Next.js. Article admin view (admin-only via `/research/articles/{id}`). | 2 days | D.5 |
| **D.2-docs** | Revise this design for the Urbanize-first polite collector pivot, 12-month Urbanize backfill, source-doc discipline, deferred advanced fetch, and D-late source expansion. | 0.5 day | D.2v |
| **D.2a-prep** | Pass 1 tightening from D.2v: comma-formatted unit counts, title/headline address scan, completion-date phrase variants. Capture sanitized Urbanize/LA YIMBY fixtures. | 1 day | D.2-docs |
| **D.2a** | Generic polite NewsCollector + Urbanize LA pilot: config-driven RSS/sitemap discovery, robots/rate-limit/Retry-After/conditional GET, host routing, source docs, seed unscoped `urbanize_la`, and disable/unschedule `bizjournals_la`. Repeat five-URL validation through host routing. | 2-3 days | D.2-docs |
| **D.6** | Scheduled Urbanize scrapes via `news_sources.schedule_cron` and the in-process scheduler tick. Before enabling cron, rerun the five D.2v URLs in staging with an Anthropic key and verify Haiku triage plus Opus extraction/integration output. | 1 day | D.2a, D.2b |
| **D.M** | Monitoring: SMTP email dispatcher; `system_alerts` reader for the Coverage banner; news ops admin tile; cost-cap bump UI. | 3 days | D.6 |
| **D.G** | Graveyard UI + manual relink endpoint. `news_admin_actions` writes for audit. | 2 days | D.5 |
| **D.B** | 12-month Urbanize backfill. Dry-run the full 988-URL D.2v-sized set first, report relevance/cost/runtime ranges, get approval, then enqueue under source pause/rate-limit/cost-cap controls. | 1 day live + monitoring | D.6, D.M, D.2a-prep |

Total ballpark: ~4 weeks for a focused engineer. Phase D is bigger than any single Phase C step.

### 23.2 Why this order beats the roadmap order

- **D.7 first as a vertical slice.** Paste-a-link is the same pipeline minus scheduled discovery. Shipping it first proves the schema, the worker extension, the LLM integration, the matcher, and the review surfaces against real articles. We learn what extraction needs to do well on real data before scheduled Urbanize volume starts.
- **D.W (Worker extension) second.** Now that C.tail.1's RQ worker exists, we just register new task functions and add the scheduler tick. Much lower risk than v1's "build a new worker."
- **D.3 (extraction) before D.2a (collector).** Extraction quality is the load-bearing concern. Wiring up scheduled collection before we trust extraction means we'd be debugging both at once. With paste-a-link, we can iterate on prompts against curated articles.
- **D.4 (matcher) after extraction lands.** The matcher's input is the extraction output; we want extractions to be stable before matcher tuning starts. D.4 also folds in the §21f resolver change because the rule is impossible to validate without real article evidence flowing.
- **D.6 (scheduling) near the end.** Scheduling is the simplest part. It's not at the front because there's nothing to schedule until everything else works.
- **Backfill last.** Backfill is a dollars-burning operation. We do it once everything is trustworthy.

### 23.3 Roadmap.md alignment

ROADMAP.md is authoritative for status. The active alignment after D.2-docs:

- D.2-docs records this Urbanize-first design revision.
- D.2a-prep captures the Pass 1 tightening and fixture-capture work surfaced by D.2v.
- D.2a implements the generic polite collector and unscoped `urbanize_la` seed.
- D.2b adds the `fetch_path` routing/interface and hard-failure posture for `advanced`.
- D.6 enables scheduled Urbanize scrapes only after the staging Anthropic-key smoke test.
- D.B runs a 12-month Urbanize backfill dry-run over the full unscoped URL set.
- D.late.E1/E2/C/ADV hold LA YIMBY, The Real Deal, paid-source/BizJournals, and advanced-fetch expansion.

---

## 24. Risks & Mitigations

### 24.1 Extraction quality

- **Risk:** LLM hallucinates a unit count or developer that isn't in the article. Bad evidence → bad project record.
- **Mitigation:** Every value must have a `passage_excerpt` with valid offsets into `body_text`. Server-side validation of offsets (refuse to write evidence whose passage doesn't actually exist in the body). Pass 1 structural disagreement triggers Pass 3. Reviewer always confirms before commit.

### 24.2 Match quality

- **Risk:** Article matched to the wrong project. Now that project's evidence carries a foreign developer/unit count, the resolver promotes it, the review queue may not catch it because field-change items are MEDIUM at most.
- **Mitigation:** Phase D uses post-apply review semantics consistent with existing collectors (§10). Confirmed-match article evidence updates project fields synchronously, but every change generates a `STATUS_CHANGE` or `OVERRIDE_CONTRADICTION` review item the reviewer can revert via Keep-old (writes a `researcher_override` of the prior value). The status resolver is conservative — single Tier 2 evidence won't auto-promote to `Under Construction`. Possible matches get orphan evidence + explicit candidate panes; new candidates go through C.g project creation. True auto-apply (no review item raised) is deferred until production acceptance metrics justify it (§25.1).

### 24.3 Scraper bitrot

- **Risk:** Urbanize changes its RSS/sitemap/body structure. Discovery or body extraction breaks silently.
- **Mitigation:** Daily "did we find any articles?" sanity check; alert on zero articles for > 36 hours. Discovery/body behavior is fixture-tested against archived Urbanize samples.

### 24.4 Auth bitrot

- **Risk:** Future paid sources change auth flow. Our cookie-injection no longer produces a valid session.
- **Mitigation:** Paid-source auth is D.late.C. The `auth-bizjournals` CLI design stays manual-session based; if login flow changes, researcher reruns it from the new login URL. We never automate login, so there is no login automation to fix.

### 24.5 Cost runaway

- **Risk:** A bug retries extraction in a tight loop; we burn $1000 in an hour.
- **Mitigation:** Hard cost cap with cooldown. The cap check is the first thing the LLM call wrapper does; can't bypass it. Cap rollup updates transactionally; can't race past the cap.

### 24.6 Prompt regression

- **Risk:** A prompt change reduces extraction quality across the board; bad evidence floods in for a week before anyone notices.
- **Mitigation:** Eval set (when bootstrapped). Production acceptance-rate monitoring. Prompt versions immutable; instant rollback by config flip.

### 24.7 Privacy / leakage

- **Risk:** Article body is sent to Anthropic. Even though articles are public, this is a data movement concern.
- **Mitigation:** Articles are public-press. Anthropic's zero-data-retention is configurable. Documented in `docs/ops/data_movement.md` (to be created as part of D.1).

### 24.8 LLM provider concentration

- **Risk:** Anthropic outage or pricing change.
- **Mitigation:** All extractions are re-runnable. We can swap providers (or models) by introducing a new prompt version and re-extracting. No data is locked into a model.

### 24.9 Articles-as-evidence bias

- **Risk:** Reviewers come to over-trust news evidence because it's the freshest signal. They start auto-accepting article-derived changes without scrutinizing.
- **Mitigation:** Confidence chips on every row. Structural-disagreement chips. The fact that the resolver still treats news as Tier 2 (lower than Tier 1 government), not Tier 0 (researcher), is preserved.

### 24.10 Single-source dependency

- **Risk:** Urbanize blocks polite access, changes ownership, or becomes noisy for target markets.
- **Mitigation:** Schema and pipeline are multi-source from day one. Adding LA YIMBY, The Real Deal, or BizJournals is a source-row/config/doc/migration exercise with any advanced/paid fetch handled by D.late.ADV/D.late.C.

---

## 25. Roadmap Follow-ups (Items Routed Out of Phase D)

Phase D explicitly punts the following to other phases. Each carries an explicit pointer back to this document.

### 25.1 Auto-apply for high-confidence article matches

- **Routed to:** A new ROADMAP item D.late.A (or absorbed into Phase E).
- **Trigger condition:** Phase D has been live for ≥ 3 months; production acceptance rate for `news_article` evidence ≥ 80%; no major prompt regressions.
- **Scope when activated:** `confirmed` matches with `candidate_confidence='high'` and clean Pass 1 / Pass 2 agreement skip the Review Queue and apply directly to the project as auto-accepted ChangeLog rows. Researcher gets a daily email summarizing auto-applied changes.
- **Why deferred:** Auto-apply is a trust contract. We earn it after months of reviewer feedback, not at MVP.

### 25.2 Cross-source LLM corroboration ("deep research")

- **Routed to:** Phase E (resolution-engine refinement).
- **Why there:** Corroboration is fundamentally a resolution concern — given evidence from multiple sources, how does the resolver weight them? Today, the resolver does this by source-priority and most-recent-wins. A future enhancement could ask "given this article mentions Project X with unit count Y, search recent permits and CoStar for corroborating signals before promoting Y."
- **Why not in Phase D:** It's not a producer-side problem (which is what Phase D is). Building it on top of Phase D would make the producer-side pipeline harder to test and reason about.
- **Sketch when implemented:** Resolver gains a `corroboration_pass` step that, for high-impact field changes, queries other recent evidence on the project and emits a `corroboration_score` into the resolution log. Used to escalate or de-escalate review item priority.

### 25.3 Additional news sources (D.late.E1/E2/C)

- D.8 is superseded by explicit D-late source rows.
- The schema and pipeline accept new polite sources by adding a source row/config,
  `LOGICAL_SOURCE_TYPE_BY_SOURCE_NAME` mapping, source strategy doc, migration,
  host routing, and fixtures. No design changes if D.2a holds.
- Candidates: LA YIMBY (D.late.E1), The Real Deal LA (D.late.E2), and
  BizJournals LA through paid-source capability (D.late.C).

### 25.4 Eval set bootstrapping

- Routed to Phase D-late.
- See §21.2.
- Do not hand-label upfront; bootstrap from real reviewer decisions.

### 25.5 Worker scale-out

- If volume justifies: scale Render worker instance count beyond 1. RQ already handles multi-instance concurrency cleanly. The scheduler tick remains on the single instance flagged `NEWS_SCHEDULER_LEADER=true`. If we ever need leader election, swap the env flag for a Postgres advisory lock — small change.

### 25.6 LADBS Worker migration

- The existing LADBS scrape behavior already runs through C.tail.1's RQ worker as of 2026-04-28; Phase D does not need to migrate it. v1 was wrong to claim this work was Phase-D-pending.
- D.W simply registers news task functions alongside the existing collector-run tasks.

### 25.7 §21f recent-article delivery-date priority

- Routed to D.4-resolver in §23.1.
- Implemented on 2026-04-30 in D.4-resolver.
- `_select_explicit_delivery_observation` now lets recent `news_article` evidence within 180 days outrank CoStar when CoStar would otherwise win by raw recency. Higher-priority TCG/government winners remain untouched. Focused resolver tests cover recent news, stale news, and TCG precedence.

### 25.8 Decision Log entries to be added to ROADMAP.md

When this design is approved:

- 2026-04-28: News-research design v2 committed. See `docs/specs/news_research_design.md`. Phase D items D.1–D.7 reordered for vertical-slice delivery; D.W reframed as RQ-worker-extension; D.M (monitoring), D.G (graveyard), D.B (backfill), D.4-resolver added.
- 2026-04-28: Phase D extends the C.tail.1 RQ/Redis worker rather than introducing a parallel queue. Background-task LADBS migration was C.tail.1, not Phase D.
- 2026-04-28: Article auth via session-state injection (cookie file), not auto-login Playwright. Rationale: eliminates the most fragile component of any scraper.
- 2026-04-28: Articles stored as `(news_articles, news_extractions, news_project_references, evidence)` quartet, not inline-in-evidence. Rationale: re-extractability, multi-project mentions, graveyard tracking.
- 2026-04-28: Phase D adopts post-apply review semantics consistent with existing collectors. Article evidence on a confirmed match updates project fields immediately; the review queue surfaces those changes as `STATUS_CHANGE` / `OVERRIDE_CONTRADICTION` items, with revert via Keep-old override.
- 2026-04-28: `evidence.superseded_at` added so re-extracted evidence rows deterministically supersede prior rows.
- 2026-04-28: §21f recent-article delivery-date priority moves from "documented intent" to "implemented in D.4-resolver."
- 2026-04-28: Auto-apply for high-confidence article matches deferred until Phase D shows ≥ 80% acceptance over 3 months.
- 2026-04-28: Cross-source LLM corroboration deferred to Phase E (resolver refinement).
- 2026-04-28: PostgREST RLS posture for Phase D excludes raw HTML, body text, and full LLM payloads from authenticated reads; admin views via FastAPI with JWT + allowlist.

---

## 26. Cross-References

- `ROADMAP.md` Phase D — gets reordered per §23.
- `ARCHITECTURE.md` §5 (collection workflow) — Phase D fits the existing "discovery + status updates" framing; news evidence flows into the existing pipeline.
- `ARCHITECTURE.md` §6 (matching strategy) — extended with a sibling `news_matcher` per §9.
- `docs/specs/data_model_changes.md` — Phase D follows the same migration ordering, RLS, and naming conventions.
- `docs/specs/review_workflow.md` — news-derived items use the existing review state machine; payload extension via `news_context` per §11.2.
- `docs/specs/EVIDENCE_LAYER_DECISIONS.md` §6, §11, §21f, §22 — directly load-bearing for the evidence shape and resolver behavior.
- `docs/specs/ui_requirements.md` §10.2 — news article snippet renderer is already specified; Phase D writes the contract.
- `docs/ops/backend_api.md` — extended with `/research/*` endpoints per §18.
- `config/source_tiers.yaml` — `news_article` is already Tier 2; no changes.
- `src/tcg_pipeline/review/snippets.py:145` — `render_news_article_snippet` is the existing renderer; Phase D writes data it can render.
- `src/tcg_pipeline/review/contradictions.py:30` — `NEWS_SOURCE_TYPES` already covers `news_article`; Phase D writes evidence Phase C contradiction detection consumes.
- `src/tcg_pipeline/resolution/fields/{developer,units,delivery_year}.py` — already news-aware; Phase D writes data they consume.
- `src/tcg_pipeline/matching/matcher.py` — sibling `news_matcher` reuses primitives.

---

## 27. Dependencies (pyproject.toml additions)

Phase D introduces the following Python dependencies. All are added under the existing `[project] dependencies` array unless noted; Playwright also requires a Render Docker base-image change.

| Package | Min version | Purpose | Notes |
|---|---|---|---|
| `anthropic` | ≥ 0.40.0 | Anthropic SDK (LLM extraction calls) | Pin major version. Track Anthropic's release notes for API stability. |
| `playwright` | ≥ 1.45 | Headless browser for SPA-hydrated article fetches | Requires `playwright install chromium` at deploy. Render base image must include the system libs Chromium needs (`libnss3`, `libatk1.0-0`, `libxss1`, etc.). Add a Render `build.sh` step or a custom Dockerfile. |
| `trafilatura` | ≥ 1.10 | HTML → plaintext body extraction | Preferred over `readability-lxml` for Urbanize validation pages and polite-source body extraction. |
| `dateparser` | ≥ 1.2 | Natural-language date parsing for delivery_year_text → date | Used in Pass 1 structural extraction. |
| `croniter` | ≥ 2.0 | Cron-expression evaluation for the scheduler tick | Tiny library, well-tested. |
| `pyahocorasick` | ≥ 2.0 | Aho-Corasick automaton for fast multi-string matching (developer / project name dictionary scans) | Used in Pass 1 to scan article bodies against the developer registry and project list. |
| `usaddress` | already installed | Address parsing | No change. |
| `rapidfuzz` | already installed | Fuzzy string matching | No change. |

Optional / dev-only:

| Package | Min version | Purpose |
|---|---|---|
| `pytest-vcr` | ≥ 1.0 | Record/replay source fetches in tests without hitting the network |
| `respx` | ≥ 0.21 | Mock httpx for unit tests of the fetcher |
| `pytest-asyncio` | already installed | Test async paths in worker tasks |

Render deployment additions:

- The worker service needs `REDIS_URL`, `ANTHROPIC_API_KEY`, `SERVICE_CREDS_KEY`, `NEWS_SCHEDULER_LEADER` (true on exactly one instance), `SMTP_*` (for alert email), and the existing `DATABASE_URL` + Supabase env.
- `playwright install chromium` runs as part of the worker's Render build command.
- Base image upgrade may be required; document in `docs/ops/render_worker_deployment.md` (to be created in D.W).

CLI additions are wired through `src/tcg_pipeline/cli.py` per §19; no new top-level package needed.

---

## 28. Open Questions (for tracking; not blocking implementation)

- **Provider markup vs Vercel AI Gateway.** Default is direct Anthropic SDK. Revisit if multi-provider A/B becomes a need.
- **Article body PII / sensitive-quote handling.** Articles are public. Quotes from named individuals are public. No special handling planned. Revisit if a researcher flags an article.
- **Paid-source credential key rotation cadence.** D.late.C decision. Default to indefinite; rotate when researcher notices anomaly. Formalize a quarterly rotation if security review demands.
- **Multi-jurisdiction news sources.** `urbanize_la` is intentionally market-unscoped. The matcher queries all active markets and decides relevance; non-modeled geography may land as discarded/new-candidate signal. Revisit only if volume makes unscoped matching too expensive.
- **Articles in non-English languages.** `news_articles.language='en'` default. If we ingest Spanish-language sources later, the LLM handles it natively but the structural extractor regex set is English-only. Address when the source list expands.
- **Article archival.** Body text + raw HTML grow over time. At ~30KB/article × 50/day × 365 = ~550MB/year. Sustainable indefinitely on Supabase. Revisit at 5GB.
- **WebSocket-based article-status push.** UI currently HTTP-polls. Push from Worker would be nice. Out of scope for D.

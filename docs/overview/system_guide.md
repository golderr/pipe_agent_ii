# TCG Pipeline Tracker — System Guide

> **Living document.** This guide explains how the app operates and how pipeline decisions get made. It is written for two audiences: a senior-leadership reader who needs the 5-minute version, and a researcher, engineer, or stakeholder who wants the drill-down. Read Parts 1–2 for the overview; continue into Parts 3–6 for the detail.
>
> **Maintenance:** Update this file whenever the agent roster changes, a new source goes live, a resolution rule changes, a kill switch is added, or the AGENT sprint advances a step. See the maintenance note at the bottom.
>
> **Last updated:** 2026-05-12

---

## Part 1 — The 5-Minute Overview

### What the system is

The TCG Pipeline Tracker is the automated replacement for the Pipedream researcher workbook. It builds and maintains comprehensive real estate development pipeline data — every project, its status, its developer, its unit counts, its expected delivery — across US markets. Los Angeles is live; Santa Monica is next; the design target is 25 markets.

The old workflow was a single researcher manually reconciling CoStar exports, government permit feeds, news scans, and tribal knowledge into one workbook. The new system continuously ingests those same sources, reconciles them automatically, and surfaces only the ambiguous cases to a researcher in a structured review queue. **The researcher is still the final authority on every project — the system's job is to do the reading, watching, and bookkeeping so the researcher can focus on judgment.**

### The team of agents

Rather than one monolithic pipeline, the system is built as a **team of ~25 specialized agents** working together. Some agents are LLM tool-using loops that reason over evidence; some are focused single-shot LLM calls; some are deterministic logic. Each agent has one job and an audit trail.

The team breaks into five working groups:

- **Intake agents** politely pull data from sources — government permit feeds, news publishers, CoStar uploads, Pipedream workbooks. One agent per source family.
- **Article processing agents** (the news team) read every news article: a triage agent filters for relevance, an extraction agent pulls structured facts, an interpretation agent translates journalist language into TCG's canonical pipeline vocabulary, a retry agent recovers from output failures, and a research agent escalates ambiguous attribution by reading past articles and consulting permits.
- **Project identification agents** decide which project an observation belongs to — normalizing addresses, geocoding, matching against existing records, canonicalizing developer names.
- **Decision-making agents** read all evidence for a project and compute the canonical state — resolving each field, enforcing status progression rules, scoring confidence and likelihood, flagging contradictions, and detecting regressions.
- **Permit and audit agents** reason over LADBS permit ambiguity and continuously measure the automated pipeline against the gold-standard Pipedream workbook.

A few more agents are designed and queued: **Pre-Leasing Detection**, **Active Research**, **CoStar Reasoning**, **Pipedream Reasoning**, **Google Alerts Discovery**. The architecture is source-agnostic, so each new agent is a configuration profile plus a prompt — not a new codebase.

### The trust model

Three principles, all visible in the audit trail:

1. **Every datum has provenance.** Every value on every project record traces back to one or more immutable evidence rows, each tagged with source, date collected, source tier, and (for LLM-derived facts) the exact passage of text it came from.
2. **Nothing is silently overwritten.** When new evidence disagrees with a researcher's manual override, the system raises a contradiction in the review queue rather than overwriting. When new evidence is silenced by an older value, the system logs that fact so a researcher can audit it later.
3. **Auto-apply is bounded and explicit.** The only places automated decisions land directly on a project record without human review are the ones we've explicitly approved (e.g., high-confidence agent-confirmed news regressions on early-stage projects). Each auto-apply path has a kill switch.

### Where we are today

- **Phases A–C** (validated data, read-only frontend, write path, review queue) — **shipped**
- **Phase D** (scheduled news ingestion) — Urbanize LA running in production behind kill switches; first-cron observation in progress
- **AGENT sprint** — News Research Agent live on production traffic; Permit Research Agent staged behind kill switches; semantic interpretation layer cut over; ~12 of 15 sub-steps done
- **What's next** — finish status-regression handling, cut over the Permit Research Agent, then `AGENT.reset` (the controlled production database rebuild that establishes the post-stabilization baseline), then Santa Monica

LA has roughly 1,360 active projects in the database, ingestion across six LADBS Socrata feeds plus Urbanize LA news, the full review/audit workflow live, and a researcher reading the queue every day.

---

## Part 2 — How the App Operates

### The continuous loop

```
Sources  →  Intake Agents  →  Evidence Store  →  Resolution Agents  →  Project Record
                                    ↓                    ↓
                       Article Processing Agents     Review Queue  ←  Researcher
                                    ↓                    ↑
                              Research Agents (news + permit, on ambiguity)
```

Evidence is **append-only** — every observation from every source becomes one immutable row. The project record is never written to directly by an intake agent; it is **computed** by the Resolution Agent reading all evidence for that project. When the computed state disagrees with what's currently displayed, the Review Queue surfaces it.

### Researcher-facing surfaces

The web app (Next.js on Vercel) has six primary surfaces:

- **Pipeline** — sortable list and map of every project, with filters, saved views, and a command-search bar
- **Project Detail** — a single project's snapshot plus tabs for Evidence (every row of source data we've ever pulled), Resolution (which rule chose which value), Changes (audit trail), and Overrides
- **Review Queue** — the day-to-day workspace. Items are grouped by project and triaged with one keystroke (`A` accept, `S` keep, `D` defer, `F` flag custom). Batch commits apply decisions in groups
- **Coverage** — per-jurisdiction view of source freshness, queue depth, scrape job history, and news source health; manual Refresh and CoStar upload land here
- **Research** — paste a news article URL, watch it flow through the news team in real time, see the result
- **Activity** — chronological audit feed across every project, every agent decision, every override, every resolved field change, filterable by source/market/field/actor/date

A **Dashboard** ties it together with five tiles: Needs Attention, Stalled Candidates, Contradictions, Pipeline by Status, and Recent Activity.

### Roles

- **Researcher** — reads the Review Queue, makes accept/reject/custom decisions, sets manual overrides where automated values are wrong, creates new projects when needed
- **Admin** — manages source schedules, cost caps, kill switches, user permissions (admin console is queued for Phase J; today this is done via deploys and Render config)
- **Agents** — see the roster in Part 4
- **System actors** — automated services that aren't really "agents" but show up in the audit trail with identifiers like `agent.news_v1`, `agent.status_regression_candidate`, `manual_geocode`. Every automated change is attributed to one of these

### Cadence

- **LADBS Socrata feeds** pull weekly and on-demand via Coverage
- **Urbanize LA** runs on a daily cron (`30 7 * * *` Pacific) plus paste-a-link on demand
- **CoStar** is uploaded by a researcher when a fresh export is available (monthly-ish)
- **Pipedream** is imported when the researcher publishes a new workbook (~quarterly, typically around June)
- **Resolution and review** are continuous — every evidence write triggers a re-resolve for the affected project

---

## Part 3 — How Pipeline Decisions Are Made

### The evidence-first principle

Every value on every project record is the output of a deterministic rule reading all the evidence we have. Three implications:

- We can rerun resolution at any time and get the same answer.
- We can tune rules and replay them against historical evidence without losing data.
- Every researcher question of the form "why does this project say X?" has a concrete answer: which evidence rows, which rule, which winner.

### Source tiers

Authority is hierarchical:

| Tier | Sources | Role |
|------|---------|------|
| **Tier 0** | Researcher overrides | Highest authority — but review-protected, not silently sticky. New contradicting evidence raises a review item rather than silently replacing or yielding |
| **Tier 1** | LADBS permits/inspections/CofO, LAHD, ZIMAS, SM permits, Pipedream | Government procedural records plus researcher-verified TCG data |
| **Tier 2** | News articles (Urbanize LA today) | Timely intelligence; moderate confidence; the semantic interpreter is responsible for translating prose into canonical values |
| **Tier 3** | CoStar, developer websites | Broad coverage, sometimes stale |
| **Tier 4** | Social media, forums | Weak signal; corroboration required |

Per-field rules can override the default hierarchy where the source's role isn't its tier. For example, **news outranks government for developer identity** because government permit filings name the attorney of record rather than the actual builder.

### Per-field resolution rules

| Field | Rule |
|-------|------|
| **pipeline_status** | Highest status wins, **forward-only**. CofO with a real completion date → Complete. Substantive recent inspection activity → Under Construction. Permit-issued alone → Approved (but requires review before promoting further). News-only Under Construction claims need corroboration |
| **total_units** | Most recent evidence wins regardless of tier. Tier breaks ties. Changes of more than 5 units create a review item |
| **affordable / market-rate / workforce units** | Most recent evidence from an *allowlisted* source (Pipedream, LAHD, SM Dev Tracking, explicit news) wins. The split is never inferred from the total; if total changes but no new split is available, the prior split is preserved and a review item is raised if it stops summing |
| **product_type** | Most recent explicit value wins. Unknown → known is a free gap-fill; known → different known creates a review item |
| **age_restriction** | Most recent explicit mention wins. Critical: silence is not evidence. An article that doesn't mention age restriction provides nothing for this field |
| **delivery_year** | Explicit source date wins over estimation. 6-month freshness threshold applies (UC projects exempt). Recent news (≤180 days) can outrank CoStar. Provenance tag tracks where the date came from |
| **developer** | Most recent wins, then per-field source priority (Pipedream > news > developer website > CoStar > LADBS) as tiebreak. Canonicalized against the developer registry; fuzzy matches in the 75–89 range are auto-applied but flagged |

### Status progression — the forward-only rule and its exceptions

Status moves forward through Conceptual → Proposed → Pending → Approved → Under Construction → Pre-Leasing/Pre-Selling → Complete. It does not move backwards automatically. Two carve-outs:

- **Stalled / Inactive** are never auto-assigned. A project with 12+ months of evidence silence may be *flagged* as a stall candidate, but the transition is researcher-only.
- **Status regression** (new evidence implies a project moved backwards — e.g., construction paused, ground hasn't actually broken) used to be silently dropped. As of May 2026, the system runs a new **Regression Detection Agent** that emits a candidate, routes it through the News Research Agent (for news-origin) or Permit Research Agent (for LADBS-origin), and either auto-applies a high-confidence regression on an early-stage project or surfaces a `status_regression_review` card for the researcher.

**Complete is terminal.** A Complete project stays Complete even if newer evidence implies otherwise. Completion is a delivery fact; reopening it is a deliberate researcher action.

### Researcher overrides — review-protected, not sticky

When a researcher manually sets a value, that value holds. But it is **review-protected, not silently sticky**:

- It does not silently lose to newer automated evidence.
- It does not silently win against newer corroborating evidence forever.
- Whenever newer evidence disagrees with the override, the Contradiction Detection Agent raises a review item explicitly: *"You set developer to X on 2026-03-15; LADBS now says Y. Affirm, accept new, or enter custom."*

This eliminates the two failure modes of older systems: silent rot (an old 2023 override stays authoritative forever) and silent overwrite (a researcher's judgment is erased without their knowledge).

### Confidence and likelihood

Two distinct scores travel on every project record:

- **Confidence** (HIGH / MEDIUM / LOW) — how reliable is the data? Roll-up of field-level confidence. Government + multiple agreeing sources + recent = HIGH. CoStar only + old = LOW.
- **Likelihood** (0–1 float) — how likely is this project to actually deliver? Status-driven base rate (UC = 1.00; Approved = 0.55; Proposed = 0.15; Conceptual = 0.08) plus signal adjustments for construction financing, sales/leasing center open, top-tier developer, public opposition, no activity in 12+ months, etc.

Both scores are explainable — the project record carries the full breakdown.

### When an LLM agent gets invoked

Most evidence flows through the deterministic path. An LLM **research agent** is invoked only on ambiguous cases, with a narrow trigger list, a tight cost cap, and a bounded toolset. See Part 4 for the full mechanics; the conceptual frame is: **deterministic resolution by default; agents on hard cases; deterministic fallback if the agent fails or is killed.**

---

## Part 4 — Drill-Down

### 4.1 The agent roster

#### Intake agents (one per source family)

| Agent | What it does | Status |
|-------|--------------|--------|
| **Government Data Agent** | Polite paginated Socrata pulls for LADBS permits, permit activity, inspections, certificates of occupancy. Six adapters today (LA City) | Live |
| **News Discovery Agent** | Robots-aware RSS + sitemap crawler with per-host rate limiting, conditional GET, retry/backoff, and auto-pause on block-like responses | Live (Urbanize LA) |
| **Paste-a-Link Agent** | Operator-initiated single-article ingestion through the same pipeline as scheduled scrapes | Live |
| **CoStar Import Agent** | Parses .xlsx exports; merges by CoStar Property ID, APN, address; writes evidence rows | Live |
| **Pipedream Import Agent** | Parses the .xlsm workbook DataStorage tab (81 fields); preserves source provenance and handles dedup/duplicate-flag rows | Live |

#### Article processing agents (the news team)

| Agent | What it does | Status |
|-------|--------------|--------|
| **News Triage Agent** | Haiku 4.5 single-shot call. "Is this article about a development project we care about?" Broad-net — when in doubt, includes | Live |
| **News Extraction Agent** | Opus 4.7 single-shot call. Pulls structured facts: name, developer, address, units, status, delivery, signal flags. Anchors every value to a character offset in the article body | Live |
| **News Interpretation Agent (Pass 2c)** | Opus 4.7 single-shot call. Translates journalist phrases into canonical TCG status / product type / age restriction / delivery date / unit buckets. Uses per-market glossary addenda. Emits reason codes like `news_status_uncorroborated_high_quality_permit_jurisdiction` | Live |
| **News Retry Agent (`extract_retry_v1`)** | Recovers from parse errors, schema-invalid output, refusals, and truncation. Up to 2 retries with a tighter prompt. Cheap path; no tools | Live |
| **News Research Agent (`news_v1`)** | Opus 4.7 **tool-using loop** for ambiguous cases. Reads past articles via semantic search, fetches article bodies, inspects project state, decides whether to promote, confirm, regress, or escalate | Live (production traffic; bounded triggers) |

#### Project identification agents

| Agent | What it does | Status |
|-------|--------------|--------|
| **Address Normalization Agent** | `usaddress` parsing plus directional/suffix/ordinal canonicalization; LA-specific city alias handling | Live |
| **Geocoding Agent** | Geocodio first; Esri fallback when Geocodio confidence is below threshold. Writes `geocode_confidence` + provider audit | Live |
| **Matching Agent** | Three-tier match: source-record exact → identifier (permit number, APN, CoStar ID, etc.) → normalized address. Emits `confirmed` / `possible` / `new_candidate` / `discarded` | Live |
| **Developer Canonicalization Agent** | `rapidfuzz` fuzzy match against the developer registry. ≥90 auto-resolves; 75–89 auto-resolves but flags; <75 raises a new-developer review | Live |

#### Decision-making agents

| Agent | What it does | Status |
|-------|--------------|--------|
| **Resolution Agent** | Orchestrates the six field resolvers (status, units, delivery, developer, product type, age restriction), recomputes likelihood and confidence, writes a `resolution_log` row, updates the project record | Live |
| **Status Progression Agent** | Inside the Resolution Agent — enforces forward-only progression, terminal Complete, Stalled/Inactive carve-out, and (new) regression candidate emission | Live |
| **Likelihood Scoring Agent** | Status-driven base rate plus signal adjustments; clamps to [0.02, 0.98] | Live |
| **Confidence Rollup Agent** | Field-level confidence → project-level confidence with reasoning JSON | Live |
| **Contradiction Detection Agent** | When newer evidence disagrees with an active researcher override, raises an `override_contradiction` review card with proposed alternatives | Live |
| **Regression Detection Agent** | New. Emits a `status_regression_candidate` for each lower-ranked status observation. Routes to the News Research Agent (for news evidence) or Permit Research Agent (for LADBS evidence); Pipedream / CoStar regressions create direct review cards until their reasoning agents ship | Live (rolling out — Slice 5 in progress) |

#### Permit reasoning agent

| Agent | What it does | Status |
|-------|--------------|--------|
| **Permit Research Agent (`permit_v1`)** | Opus 4.7 tool-using loop for LADBS ambiguity — unmatched permits, >10% unit-delta contradictions, product-type changes. Cross-references news evidence via the `get_articles_about_parcel_or_address` tool | Code complete; controlled smoke passed; production cutover held until first clean news observation window |

#### Audit and ops agents

| Agent | What it does | Status |
|-------|--------------|--------|
| **Coverage Comparison Agent** | `tcg-pipeline compare-pipedream-coverage` — parses a Pipedream workbook without persisting it, matches rows to current projects, reports disagreements on status, developer, units, location. The "external auditor" agent | Code complete; first real run scheduled after AGENT.reset against the June 2026 Pipedream refresh |
| **Source Health Agent** | Watches HTTP response codes from news fetches; auto-pauses unhealthy sources on repeated 401/403/429/503; raises a system alert; surfaces on the Coverage source health panel | Live |
| **Activity / Audit Log View** | Cross-project chronological feed of every agent decision, resolver-applied change, and override action. Saved presets for non-escalated agent decisions, auto-applied changes, semantic auto-promotions, and per-market reason-code distribution | Live |

#### Future agents (designed, queued)

| Agent | What it will do | Status |
|-------|-----------------|--------|
| **Pre-Leasing Detection Agent (`leasing_v1`)** | Discovers a project's leasing site (Apartments.com / StreetEasy / developer microsite); classifies whether real rents are publicly listed. Strong listings → Pre-Leasing evidence. Sweeps biweekly over UC projects plus event-triggered checks on `topped_out` / `first_move_ins` signal flags | Designed |
| **Active Research Agent** | Operator- or schedule-triggered targeted web search for stale, low-confidence, or contradictory projects; routes promising URLs through the news pipeline | Designed |
| **CoStar Reasoning Agent (`costar_v1`)** | Same agent runner, CoStar source profile. Trigger profile is contradiction-only (>10% unit delta, status regression, developer mismatch); no `new_candidate` triggers because CoStar Property IDs short-circuit identifier matching | Designed |
| **Pipedream Reasoning Agent (`pipedream_v1`)** | Same runner, Pipedream profile. Conservative: contradiction-only against Tier 1 evidence. Pipedream is human-curated Tier 1; the agent escalates, never overrides | Designed |
| **Google Alerts Discovery Agent** | Passive discovery from configured Google Alerts RSS feeds; dedupes URLs and hands new ones to the Paste-a-Link Agent | Designed |

### 4.2 Sources

**Live:**
- Pipedream (.xlsm workbook) — 81 fields, seed + periodic refresh
- CoStar (.xlsx export) — 287 columns, multi-family + non-MF
- LADBS permits + permit activity (Socrata `pi9x-tg5x`)
- LADBS inspections (Socrata `9w5z-rg2h`)
- LADBS CofO (Socrata `3f9m-afei`)
- Two legacy frozen LADBS datasets
- Urbanize LA (news; daily RSS + sitemap)
- Researcher overrides + manual project creation + paste-a-link
- Manual geocoding remediation

**Configured but not yet coded:**
- LAHD affordable housing (Socrata)
- LA Case Reports (biweekly PDF API)
- ZIMAS / PDIS (enrichment lookup, not bulk)
- CEQAnet (state environmental review)

**Planned:**
- LA YIMBY, The Real Deal LA (news)
- BizJournals LA (paid; cookie/session auth via Playwright)
- Santa Monica: Dev Tracking PDF, Ministerial PDF, Active Permits Socrata, optional Accela
- LA County, West Hollywood, Glendale, Burbank, and beyond

### 4.3 The news article processing pipeline in detail

Every article — paste-a-link or scheduled scrape — goes through the same sequence:

1. **Pass 0 — Polite Fetch.** The News Discovery Agent (or Paste-a-Link Agent for ad-hoc) does the actual HTTP fetch via httpx + trafilatura. Robots-aware, conditional GET, per-host rate limit, identifying User-Agent. Body is normalized to plaintext with stable character offsets — critical for later highlight-and-quote display in the review queue.
2. **Pass 1 — Structural Extraction.** Deterministic regex / Aho-Corasick scans for dates, addresses, unit counts, status keywords, developer/project dictionary hits. No LLM. Acts as a check on Pass 2.
3. **Pass 2a — Triage.** News Triage Agent (Haiku) — relevance only. Cost: ~$0.002/article.
4. **Pass 2b — Extraction.** News Extraction Agent (Opus) — structured facts with character-offset anchors. Cost: cache-warm ~$0.05–0.10; cache-cold ~$0.20–0.40.
5. **Pass 2c — Interpretation.** News Interpretation Agent (Opus, single-shot, no tools) — translates prose to canonical pipeline values, emits reason codes. Cost: currently ~$0.20/call; AGENT.7 has a multi-tier optimization plan to bring this down to ~$0.08–0.10.
6. **Matching.** Matching Agent decides which existing project the article references map to.
7. **Conditional agent escalation.** If the deterministic matcher returns `new_candidate`, `possible_multi_candidate`, `low_confidence`, `material_contradiction`, `override_contradiction`, or `pass1_pass2_conflict` — the News Research Agent (Opus tool loop) runs with `get_project_state`, `search_articles_similar` (pgvector semantic search), `get_article_body`, `search_projects`, and (when relevant) `get_permits_for_project`.
8. **Evidence + review.** Evidence rows are written; resolve_project runs; review items are created for whatever the agent (or deterministic path) couldn't auto-resolve.

The output-quality **News Retry Agent** sits parallel to this — if Pass 2b returns parse errors, refusals, schema-invalid, or truncated output, it retries with a tighter prompt before falling through to deterministic failure handling.

**Legacy fallback:** the older Pass 3a/3b deterministic re-extraction code is preserved behind a `news_use_legacy_pass3` kill switch as an emergency rollback path. Default is off; the agent path is the production path.

### 4.4 The semantic interpretation layer

Pass 2b extracts what the article *says*. Pass 2c interprets what the article *means* in TCG's pipeline vocabulary. This separation matters because:

- "Broke ground" can be a ceremonial photo-op or actual construction start. Pass 2c knows the difference by consulting the jurisdiction's permit-data-quality policy.
- "Late 2027" needs to become an unambiguous date. Pass 2c applies a documented convention (e.g., `2027-12-15`).
- Local entitlement language varies by jurisdiction. NYC's "ULURP certification," California's "Tentative Map," Florida's "DRI" all need a per-market glossary addendum.

Each interpretation output carries a **reason code** from a finite vocabulary. Reason codes are auditable (visible in the review card), actionable (drive different downstream behaviors — e.g., `news_status_uncorroborated_high_quality_permit_jurisdiction` blocks auto-promote), and monitorable (per-market reason-code distribution is surfaced in Activity and alerts fire when gap rates exceed thresholds).

For structured sources (LADBS today; CoStar/Pipedream when their agents ship), semantic interpretation is deterministic — the source is already field-typed.

### 4.5 Review item types

- **`status_change`** — proposed status update that needs human review
- **`possible_match`** — matcher returned multiple candidate projects for an observation
- **`new_candidate`** — observation doesn't match any existing project; researcher can accept (creates a new project) or reject
- **`override_contradiction`** — newer evidence disagrees with an active researcher override on a field
- **`status_regression_review`** — newer evidence implies a project moved backwards in the lifecycle (new in May 2026)
- **`material_contradiction`** — newer evidence implies a >10% unit delta, status mismatch, or developer change on a project
- **`unit_split_mismatch`** — total units changed but the preserved affordable/market-rate/workforce split no longer sums correctly
- **Custom** — researcher-entered freeform decision with notes

All review items are decision cards (one card per `(project_id, field_name, item_type)`), with supporting and dissenting evidence sections, agent reasoning where applicable, and one-keystroke actions.

### 4.6 Status regression handling (current redesign)

Until May 2026, regression candidates (lower-status observations on a higher-status project) were silently dropped by the resolver's `forward_only_preserve_current` rule. The redesign in flight:

- **Slice 1–2** (shipped): the Status Progression Agent emits one raw regression candidate per lower-ranked observation, even when older higher-ranked evidence still wins.
- **Slice 3** (shipped): news regression candidates route through the News Research Agent with the new `status_regression_candidate` trigger — separated from the broader `material_contradiction` trigger.
- **Slice 4** (shipped, behind `NEWS_REGRESSION_AUTO_APPLY_ENABLED`): the agent can auto-accept high-confidence (≥0.90) regressions on early-stage projects (current rank ≤4 = Under Construction or earlier). Pre-Leasing/Complete remain review-only at any confidence. Auto-accepts use a system-authored `until_newer_evidence` override that yields cleanly to fresher Tier 1 evidence, unlike researcher-authored overrides which require explicit review.
- **Slice 5** (in progress): structured-source routing — LADBS regressions through the Permit Research Agent, Pipedream/CoStar regressions as direct review cards until their agents ship.

### 4.7 Cost, safety, kill switches

**Cost caps** are scoped per source bucket. Each bucket has a warn threshold, a hard daily limit, and an audited override path:

| Bucket | Daily warn | Daily hard |
|--------|------------|------------|
| `news` (all news LLM calls combined) | $25 | $35 |
| `agent.news_v1` | configured per source | configured per source |
| `agent.permit_v1` | configured per source | configured per source |
| `permits` | $50 | $75 |

**Kill switches** flip at runtime without a deploy:

- `agent_enabled_for_news` — disables the News Research Agent; deterministic path only
- `agent_enabled_for_permits` — disables the Permit Research Agent
- `agent_allow_live_llm` — global guard. Production workers must have this true to make real LLM calls; CI and local default false
- `news_use_legacy_pass3` — emergency revert to deterministic Pass 3a/3b
- `news_use_legacy_semantic` — emergency revert to legacy semantic interpretation (Pass 2c off)
- `NEWS_REGRESSION_AUTO_APPLY_ENABLED` — gates Slice 4 auto-accept of high-confidence regressions
- Per-source `active` and `paused` flags
- Source-level auto-pause on repeated block-like HTTP responses

**Wallclock timeouts** — every agent run has a 300-second cap. Timeout writes `outcome='failed_timeout'`, releases the cost reservation, and preserves the deterministic verdict.

**Deterministic fallback** is the default posture. Agent timeout, budget exhaustion, error, killed-by-switch, or off-list verdict all fall back to the deterministic review item — the researcher is never left with no path forward.

### 4.8 The evidence schema in summary

```
evidence
├── id (uuid)
├── project_id (uuid, nullable for unmatched)
├── source_type (ladbs_permit, news_article, pipedream, costar, …)
├── source_tier (computed from source_tiers.yaml)
├── ingest_method (scheduled_collector, deep_research, manual_entry, seed_import, costar_refresh)
├── source_record_id
├── collected_at  (when our system pulled this)
├── evidence_date (real-world date — permit issued, article published, …)
├── raw_data (jsonb — full original record)
├── extracted_fields (jsonb — normalized values with per-field confidence)
├── signal_flags (jsonb — for likelihood engine)
├── superseded_at (timestamp — set when a fresher row replaces this for resolver purposes)
└── notes
```

Append-only by design. Resolution reads all non-superseded rows for a project, applies the per-field rules, writes the project record + a `resolution_log` audit row, and triggers review item creation for anything ambiguous.

Companion tables: `project_field_resolution`, `resolution_log`, `change_log`, `status_history`, `researcher_overrides`, `agent_runs`, `agent_run_review_items`, `news_extractions`, `news_project_references`, `news_semantic_interpretations`, `news_article_chunks` (with pgvector embeddings), `system_alerts`.

---

## Part 5 — Trust, Audit, Safety

### What the audit trail captures

For any value on any project, a researcher can answer:

- **Where did this come from?** → evidence rows linked via `resolution_log`
- **Why did this rule choose this value?** → `resolution_log.rule_applied` and `resolution_log.metadata`
- **Who set this manually?** → `researcher_overrides` with Supabase user ID and email
- **What did the LLM agent think?** → `agent_runs.reasoning_trace`, `evidence_consulted`, `tool_calls_summary`, `agent_revised_verdict`
- **What did the researcher decide?** → `review_decisions` + `change_log`
- **When did each thing happen?** → every table has timestamps; Activity stitches them into one feed

### How researchers read the audit trail

Most of the time: through **Activity**. It is a chronological, filterable, cross-project feed. Saved presets:

- Non-escalated agent decisions (for spot-checking the agent's judgment)
- Auto-applied changes (every change that bypassed the review queue)
- Semantic auto-promotions (Pass 2c interpretations the resolver accepted)
- Per-market reason-code distribution (with gap-rate, unmappable-rate, reviewer-rejection-rate metrics)

For a single project: the **Resolution** and **Changes** tabs on Project Detail. Resolution shows the current per-field rule + winning evidence; Changes is the project-scoped audit log.

### Eval methodology

Three layers plus a spot-check sampler:

1. **Continuous reviewer acceptance** — the rate at which researchers accept vs. reject each agent decision type, surfaced as a metric. Cheap, continuous, free.
2. **Periodic Pipedream auto-comparison** — when a new Pipedream workbook publishes, the Coverage Comparison Agent diffs the automated pipeline against TCG's gold-standard curation. The most rigorous quality signal.
3. **Targeted hand-grading on disagreement** — when Layer 1 and Layer 2 conflict, a researcher hand-grades the disputed cases.

**Spot-check sampler:** 10 random non-escalated agent decisions per week land in Activity for researcher review. Steady-state cadence tapers as agreement rate stabilizes.

**Prompt-version gate:** any prompt change must clear `--eval-pass-rate >= 0.90` on the accumulated eval set before deploy.

---

## Part 6 — Roadmap Context

### What's built (high confidence)

- The evidence layer end-to-end (schema, backfill, resolution, contradictions, overrides)
- Read-only frontend: Pipeline, Project Detail, Coverage, Dashboard, Activity
- Write path: inline editing, manual project creation, relationships, notes, geocoding remediation
- Review Queue: decision cards, staged + committed state machine, batch commit, reviewed tab
- News pipeline: Pass 0 / Pass 1 / Pass 2a (triage) / Pass 2b (extraction) / Pass 2c (semantic) / paste-a-link / scheduled scrape / article admin / graveyard / retry
- Agent foundation: source-agnostic runner, source profiles, `agent_runs` audit, cost-cap accounting, kill switches, A/B harness, article chunk embeddings
- News Research Agent live for `new_candidate`, `possible_multi_candidate`, `low_confidence`, `pass1_pass2_conflict`, `material_contradiction`, `override_contradiction`, `status_regression_candidate` triggers
- Permit Research Agent code complete, controlled smoke passed
- Status regression handling Slices 1–4 shipped; Slice 5 in progress
- Activity / Audit Log with semantic, agent, resolution, and change sources
- Coverage Comparison Agent code complete
- `reset-user-actions` CLI for stabilization cycles

### What's deliberately not yet built

- **LAHD affordable**, **LA Case Reports PDF**, **ZIMAS scraper**, **CEQAnet** — defined in config, no collector code
- **Excel/exhibit/CMA exports** — Phase G
- **6-month delivery-year freshness threshold**, **auto-stall detection**, **externalized likelihood config** — Phase E refinements
- **Pre-Leasing Detection Agent**, **Active Research Agent**, **CoStar Reasoning Agent**, **Pipedream Reasoning Agent**, **Google Alerts Agent** — designed, queued
- **Admin console** (LLM model registry, scrape ledger, user/role management) — Phase J
- **Santa Monica market** — Phase H, gated on `AGENT.reset` and a market-agnostic hardening audit (H.0)
- **Third market / generalization template** — Phase I

### The immediate sequence

1. Finish status-regression handling Slices 5–6 (structured-source routing + monitoring)
2. Cut over the Permit Research Agent (enable `AGENT_ENABLED_FOR_PERMITS=true` after a clean news observation window)
3. Run `AGENT.reset` — controlled production database rebuild (truncate data tables, preserve config, reseed CoStar + Pipedream, replay collectors, re-resolve, re-enable news cron, run a bounded Urbanize backfill, rerun the Pipedream coverage compare as the inaugural eval baseline). Expected to run iteratively across multiple stabilization cycles
4. Cross-cutting **UI.QA** pass before Santa Monica goes live — UI patterns lock in once a second market is live
5. Phase H — Santa Monica market

After Phase H: Phase I (generalization template + third market), Phase J (admin console), Phase G (exports), Phase F (additional LA collectors), AGENT.4 / AGENT.5 / AGENT.6 (CoStar / Pipedream / Pre-Leasing agents), AGENT.7 (Pass 2c cost optimization).

---

## Maintenance Note

This document is a **living overview**, not a contract. The authoritative specifications live elsewhere:

- **Build status and sequencing:** `ROADMAP.md`
- **Original design:** `ARCHITECTURE.md`
- **Evidence layer rules:** `docs/specs/EVIDENCE_LAYER_INTEGRATION_GUIDE.md` and `EVIDENCE_LAYER_DECISIONS.md`
- **News pipeline contract:** `docs/specs/news_research_design.md`
- **Agent layer contract:** `docs/specs/agentic_escalation_design.md`
- **Semantic interpretation:** `docs/specs/semantic_interpretation_layer_design.md`
- **Status regression redesign:** `docs/specs/regression_handling_plan.md`
- **Per-source runbooks:** `docs/sources/`
- **Operational change-impact framework:** `docs/ops/change_impact_classification.md`

**Update this guide when:**
- A new agent is added to the roster, retired, or significantly changes scope
- A new source family goes live or is retired
- A resolution rule changes (e.g., a threshold moves, a tier reassignment)
- A new kill switch or cost-cap bucket is added
- A review item type is added or retired
- An auto-apply path is added or removed
- A phase milestone closes (Phase D/E/F/G/H/I/J or an AGENT sprint sub-step)
- The trust model or eval methodology changes
- An `AGENT.reset` cycle completes and changes the production baseline

When updating, keep both audiences in mind: the leadership section (Parts 1–2) should stay tight enough to read in five minutes; the drill-down (Parts 3–6) should stay accurate without trying to be exhaustive.

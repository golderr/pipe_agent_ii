# Agent Layer Design — Working Notes

> **Status:** Draft / discussion notes. **Not yet wired into `ROADMAP.md`.** Captures the design conversation between Nate and Claude on 2026-04-25 about adding an agentic "first pass" layer over the evidence and review systems. Decisions recorded here are tentative until ratified into the roadmap.
>
> **Last updated:** 2026-04-25
> **Maintained by:** Nate Goldstein + Claude
>
> **Reads alongside:**
> - `ROADMAP.md` — for current build plan and phase ordering
> - `ARCHITECTURE.md` — sections 2 (system architecture), 3 (data model), 5 (collection workflow), 6 (matching strategy), 8 (decision log)
> - `docs/specs/EVIDENCE_LAYER_DECISIONS.md` — resolution rules, override semantics, source tier hierarchy
> - `docs/specs/EVIDENCE_LAYER_INTEGRATION_GUIDE.md` — schema and resolution engine reference
> - `docs/specs/review_workflow.md` — review queue state machine
> - `docs/specs/ui_requirements.md` — researcher UX context

---

## 1. Purpose

This document specifies an **agentic assistance layer** over the existing evidence pipeline. The layer's job is to do a "first pass" on data quality and review-queue items: identifying problems, drafting recommendations, surfacing additional sources, and preparing the queue so researcher decisions become fast.

The layer is explicitly **researcher-assistive, not researcher-replacing.** Nothing here changes the principle that humans decide and tier-0 (researcher overrides) stays sacred. What changes is that researchers walk into a queue where most items already have a draft answer with citations attached, instead of a queue where every item starts blank.

The agent layer is built on top of three things you already have:
- The evidence model (append-only `evidence` table, hash-based dedup, per-field resolution)
- The review queue (`ReviewItem`, `ReviewDecision`, `ChangeLog`)
- The status rules (`STATUS_FROM_EVIDENCE_TYPE`)

It adds:
- A new `agent_assessment` table linked to `ReviewItem` and `Project`
- A few new status evidence types (`groundbreaking_announced`, `pre_leasing_active`, `temporary_co_issued`, `grand_opening_announced`, `construction_topped_out`, `construction_loan_closed`)
- A small set of additive columns on `evidence` and `review_decision` to support a learning loop
- A custom MCP server (`tcg-pipeline-mcp`) exposing read/write capabilities to agents
- A library of skills (each agent task documented as a SKILL.md) shared across surfaces

---

## 2. Core principles

These are the invariants the layer is designed around. Every other choice is downstream.

1. **Agent never writes overrides.** Tier 0 stays human-only. Agents write evidence rows and draft assessments; researchers commit decisions.
2. **Citation-or-fail.** Every agent claim about a specific project must cite a retrieved source URL. The MCP write layer rejects un-cited evidence. No "general knowledge" assertions about real projects.
3. **Tier reflects source authority, not collection method.** Agent-written evidence carries the tier of its underlying source (a news_article remains Tier 2 whether a collector or an agent fetched it). A separate `verification_status` field captures the agent/human review dimension orthogonally.
4. **Always-approve to start.** Every agent recommendation initially requires explicit researcher approval. There is no auto-accept tier on day one. Skills graduate to auto-accept (or auto-accept-with-revoke-window) only after the system has measured ≥95% accept rate over a meaningful sample. See §10.
5. **Skills are the substrate, not the surface.** Each agent task lives as a versioned skill (a `SKILL.md` plus its prompt). The same skill runs in the backend worker (Agent SDK), the future Cowork plugin, and any embedded chat. Skill iteration is the unit of improvement.
6. **Pre-filter aggressively.** Most agent invocations should be cheap deterministic gates ("does this project have an APN?", "is this point inside the polygon?", "has the underlying evidence changed since last assessment?"). Only ambiguous cases get model calls. Cooldowns prevent re-running on unchanged input.
7. **Failure modes must be loud, not silent.** When the agent can't reach a source, can't find corroboration, or hits its budget, it returns `needs_human` with the reason recorded — never a hallucinated answer.
8. **Dedup proposals never auto-accept.** Merging two project IDs is treated as effectively irreversible. Always human-reviewed regardless of agent confidence.

---

## 3. The two work types

The agent does two genuinely distinct kinds of work, and they flow through different paths.

### A. Writing new evidence

This is the agent finding *information* the system did not have. Examples: pre-leasing detection, source-hunting on sparse projects, groundbreaking discovery, CofO/grand-opening detection, sparse-evidence enrichment.

The agent writes evidence rows (or queues a forward-collector run; see §9). The resolution engine recomputes. If a status would change as a result, the existing review-item flow kicks in just like for any collector — a `STATUS_CHANGE` review item is created, with the agent's assessment attached.

**The agent does not "decide" anything in this path.** It brings sources to the table; the rules and the researcher do the rest.

### B. Drafting decisions on contested review items

This is the agent reading an *existing* review item, doing research, and proposing a resolution. Examples: unit reconciliation (CoStar says 80, project says 220 — phase split or rescope?), developer name classification, status-promotion validation when there's contradictory evidence, proximity-dedup investigation.

The review item already exists. The agent attaches an `agent_assessment` to it with `recommendation`, `reasoning`, `sources_cited[]`, `agent_confidence`, and a `pattern_class` describing what kind of case this is. The item still goes into the normal queue with status `OPEN`.

The researcher's UX is: same review item they would have seen, but with an "Agent draft" panel pre-filled. One-key accept means accept-the-agent's-recommendation; one-key reject means reject-and-fall-back-to-system-default-or-pick-something-else.

---

## 4. Agent task taxonomy

Five families, organized by what the agent is actually doing. Not all are equally high-priority — see §13 for phasing.

### 4.1 Identity & location verification

| Task | Trigger | What the agent does |
|---|---|---|
| Out-of-jurisdiction coordinate sweep | Daily/weekly: PostGIS `ST_Within(location, jurisdiction.boundary) IS FALSE` | Re-geocode from canonical_address; propose corrected lat/lng or flag the address as bad |
| Low geocode confidence | `geocode_confidence` IN (NONE, LOW) | Re-geocode, surface the most likely correct address |
| Address-vs-coordinate disagreement | Reverse-geocode of stored lat/lng disagrees with stored canonical_address | Investigate, propose correction |
| Range-address resolution | Address contains `NNN-MMM` pattern | Verify single parcel vs. multiple |
| Missing APN | `ProjectIdentifier` row of type APN absent | County-assessor lookup from address |
| Missing project name | `project_name IS NULL` | Extract from listings/news/permits |

### 4.2 Duplicate detection

This is the matcher's biggest blind spot today. Current matcher does only exact canonical-address, identifier, and source-record match. There is **no fuzzy address match, no proximity match, and no APN-pair check**. Candidate-pair sweeps fill those gaps.

| Task | Trigger | What the agent does |
|---|---|---|
| Same-parcel dedup | Two projects share APN | Deterministic flag → POSSIBLE_MATCH review item; agent investigates only ambiguous cases |
| Proximity dedup sweep | Project pairs within ~30m | Pre-filter: at least one secondary signal must align (same developer, similar unit count, similar name, same status). Then agent investigates whether they are the same project, phase siblings, or unrelated |
| Phase-vs-duplicate disambiguation | Found by proximity sweep | If phase siblings, propose `ProjectRelationship` rows (phase_sibling). If duplicate, propose merge (always human-reviewed) |
| Address-format duplicates | Sweep detects addresses that normalize to the same string | Mostly deterministic; agent handles ambiguous ones |

### 4.3 Field reconciliation on contradictory evidence

This is the agent drafting decisions on existing review items.

| Task | Trigger | What the agent does |
|---|---|---|
| Unit reconciliation | `units_review` items with absolute delta > 5 | Web search for phasing signals, retail-vs-residential breakdown, project rescope news. Hypothesis with 2-3 cited sources. Proposes value or `needs_human` |
| Status promotion validation | STATUS_CHANGE items where new value is UC or COMPLETE, OR permit-only Approved with `requires_review` | Cross-check news for confirmation; check developer site; trigger forward-LADBS query if signals found |
| Delivery date reconciliation | Date contradiction or year_built < 2000 with future date_delivery | Decide: real timeline change, renovation, stale data |
| Developer name classification | New name surfaced from a non-Pipedream source, OR fuzzy-review (75-89 similarity) canonicalization candidate | Classify: real_dev_firm / parent_company / shell_LLC / owner / architect / unknown. Token-overlap guard against the `Helio / UCLA` and `Category` failure modes |
| Affordable + market_rate ≠ total | Sanity sweep | Investigate unit-split provenance, propose correction |
| Property-type vs. unit-count mismatch | `Single Family` with 200 units etc. | Investigate; usually a property-type misclassification |

### 4.4 Sparse-evidence enrichment

| Task | Trigger | What the agent does |
|---|---|---|
| Source hunter | `evidence_count < 3` OR (`confidence == LOW` AND `last_evidence_date > 90 days ago`) | Search news, developer websites, environmental review filings; write evidence rows with `ingest_method='deep_research'` |
| Pipedream-only corroboration | Project with `created_by='pipedream_import'` and no public-source evidence | Find current public records (LADBS permits, ZIMAS case) |
| CoStar-only verification | Project with only CoStar evidence and no government records | Look for permits / case reports; if none, flag as possible vaporware |
| Pipedream Site URL chaining | Pipedream `Site1-4` URLs present and pointing at PDIS | Extract case number; trigger ZIMAS-style enrichment (when Phase F.3 ships) |

### 4.5 Stall and lifecycle investigation

| Task | Trigger | What the agent does |
|---|---|---|
| Stall investigator | Status Proposed/Approved with no evidence in 12+ months | Search "[name] [address] stalled withdrawn paused sold". Propose STALLED or INACTIVE with citations |
| Status-downgrade validation | Status moved Approved → Pending in a feed | Real change or feed glitch? |
| CofO without lifecycle | Complete status but no permit/inspection history | Probably wrong building at the address; investigate |
| Researcher-override-vs-evidence contradiction | C.i contradiction detection, expanded | Provide draft assessment for the human |

---

## 5. High-priority capabilities — deep specs

These are the capabilities discussed in detail; the rest of §4 is summary.

### 5.1 Pre-leasing / pre-selling detection

Your schema already has `PRE_LEASING_PRE_SELLING` in the status progression (between UC and COMPLETE), but no source feeds it today. This is fundamentally a web-only signal.

**Trigger.** Project status is UC, OR Approved older than 12 months. Cooldown: do not re-run on the same project within 14-30 days. (Tunable.)

**Search signals**, in rough order of strength:
- Building marketing website (`live[name].com`, `[name]apartments.com`, `the[name].com`) — strongest
- Apartments.com / Zillow / Redfin / RentCafe listings with floor plans and rents
- Social media with leasing CTA (Instagram bio link, "Now Leasing" hashtags)
- Press releases announcing leasing office opening or sales gallery
- For condos: MLS listings, "now selling" announcements

**Output.** Evidence row with `source_type=developer_website` (or `news_article`), evidence type `pre_leasing_active`. The status rule promotes to PRE_LEASING_PRE_SELLING. Citation must include URL plus a fetched_at timestamp; ideally a screenshot reference.

**Gotchas.**
- Listings can predate construction completion by 6+ months. Don't conflate with COMPLETE.
- Some buildings list units before construction even begins (deeply pre-construction marketing). Differentiate by checking for unit availability dates.
- Watch for stale listings on still-vacant or never-completed buildings.

### 5.2 Certificate of Occupancy / Complete detection

Existing `ladbs_cofo` adapter (Socrata `3f9m-afei`) handles the canonical case: only final CofO with real `cofo_issue_date` emits Complete evidence. The agent fills in three gaps.

**TCO (Temporary Certificate of Occupancy).** A building can be occupied 6-12 months under TCO before final CofO. From a pipeline-tracking standpoint that's effectively complete. New evidence type: `temporary_co_issued` → COMPLETE. Worth adding to status rules whether or not the agent is involved, since LADBS data sometimes shows TCO directly.

**Grand opening / first residents.** When LADBS lags, news outlets and developers announce "first residents," "ribbon cutting," "now open." Trigger: UC project with confirmed pre-leasing evidence already present. Search: `"[name]" "now open"`, `"[name]" "first residents"`, `"[name]" "grand opening"`, `"[name]" "leased up"`. New evidence type: `grand_opening_announced` → COMPLETE, **flagged `requires_review` unless an LADBS CofO is also found.** This corroboration requirement prevents news-only false positives.

**Forward-search to LADBS.** When the agent finds a grand-opening signal, it should immediately call `trigger_collector(project_id, 'ladbs_cofo')` (see §9). Often the CofO data is in LADBS but hasn't synced through the scheduled collector yet. This is the "agent triggers a targeted collector run" pattern.

### 5.3 "Started construction" verification — the most important sweep

This is the highest-stakes sweep because UC count is probably your most-watched metric. False negatives (UC projects sitting in Approved) directly understate the pipeline.

The current promotion rule (`building_inspection_recorded` → UC, requires recent + substantive + active permit) is conservative on purpose, but has known blind spots:
- LADBS inspection feed lags ~weeks
- Phase 2 of a phased project doesn't auto-promote even when its own permits and visible work exist
- Some inspection types may not pass the "substantive" filter
- Pre-construction site work (demolition, abatement, grading) may not register as a building inspection
- Projects between permit issuance and first inspection are stuck in Approved

**Trigger.** Weekly sweep on the Approved cohort with `status_date` older than 6 months. Skip projects with no permits in evidence (too early) or with status changes in the last 30 days.

**Search signals**, in rough order of strength:
1. `"[address]" "broke ground"` / `"groundbreaking"` — strongest, news-confirmed
2. `"[address] OR [name]" "topped out"` — late-stage, very strong
3. `"[address]" "construction loan" closed` OR `"construction loan" closed for [name]` — financing confirms imminent or active construction
4. `"[name]" "under construction" site:[developer-domain]` — developer's own website
5. `"[address] OR [name]" "construction begins"` / "construction starts"
6. Local construction press: BizJournals LA, Urbanize LA, The Architect's Newspaper, Curbed LA

**Forward-query LADBS.** When the agent has any construction signal, immediately call `trigger_collector(project_id, 'ladbs_inspection')`. There may be inspections that didn't pass the existing "substantive" filter but, combined with the news signal, justify promotion.

**Output.** Evidence row with `source_type=news_article`, evidence type `groundbreaking_announced` or `construction_topped_out`, plus extracted_fields including the date construction started per the article. Status rule promotes → UC. Agent assessment attached with all citations. STATUS_CHANGE review item with the agent's draft.

This sweep alone is likely worth its build cost — even a handful of false-negative UC projects per quarter caught early is meaningful for reporting accuracy.

### 5.4 Out-of-bounds geocode sweep

User flagged: "I notice a few projects with coordinates outside their jurisdiction."

**Trigger.** Daily or weekly:
```sql
SELECT p.id FROM projects p
JOIN jurisdictions j ON p.jurisdiction_id = j.id
WHERE NOT ST_Within(p.location::geometry, j.boundary)
   OR p.location IS NULL AND p.geocode_confidence != 'none';
```

(Requires `jurisdictions.boundary` column; if not yet present, this sweep depends on adding boundary polygons — note this as a prerequisite.)

**What the agent does.** Re-geocode `canonical_address`, compare to stored `lat/lng`, compute distance. If the geocoded point is inside the jurisdiction and the stored point is outside: propose correction. If both are outside: the address itself may be wrong — investigate and propose a corrected canonical_address (with the original raw addresses preserved).

**Output.** Field-correction review items, not status changes. Agent draft proposes the new lat/lng (or new canonical_address) with citation to the geocoding service result.

### 5.5 Proximity dedup sweep

The matcher's biggest blind spot. Current code does only exact canonical-address; the architecture doc claimed fuzzy + proximity matching, but those layers were never built.

**Trigger.** Weekly job. For every project pair where `ST_DWithin(p1.location, p2.location, 30)` (~30 meters):

**Pre-filter** (deterministic, before any agent call). At least one of:
- Same APN
- Same building name (after normalization)
- Same developer (canonicalized) AND similar unit count (within 25%)
- Same status AND similar unit count AND similar age

If the pre-filter passes, queue an agent investigation. Otherwise log and skip.

**What the agent does.** Read both projects' evidence. Decide one of:
- **Same project** (duplicate): propose merge — always requires human review, never auto-accept
- **Phase siblings** (same master, different phases): propose `ProjectRelationship` rows of type `phase_sibling`
- **Unrelated** (different buildings on adjacent parcels): close the candidate pair, log so future sweeps don't re-investigate

**Output.** A POSSIBLE_MATCH review item with the agent's classification and citations. For phase-sibling proposals, the relationship rows are staged but not committed until accepted.

### 5.6 Unit reconciliation

The single highest-volume case: 28 rows in the Phase A `units_review.csv` already, with no automated triage today.

Pattern classes the agent should distinguish:
- `phase_split`: CoStar reports Phase 1, project record reflects total — find phase configuration
- `rescope`: project genuinely re-sized; news typically corroborates
- `mixed_use_accounting`: 293 → 121 might be retail-vs-residential separation
- `unit_type_breakdown`: market-rate-only vs. total
- `data_error`: source had a typo or mismapping
- `unknown`: insufficient signal — `needs_human`

Each pattern_class has its own confidence threshold for the agent to recommend acceptance vs. flag for human review.

### 5.7 Developer name classification

Volume: Phase A `developer_review.csv` has 162 rows, plus 85 in `developer_category_cleanup.csv` and the Helio/UCLA cluster. Real failure modes:
- CoStar listing a holding company / shell LLC instead of the actual developer
- Architecture firms accidentally listed as developer (e.g., `MVE + Partners`, `Three 6Ixty`)
- Owners listed as developers
- Generic/polluting canonical names absorbing unrelated firms (the `Category`, `Capital`, `Investment`, `Development`, `Helio / UCLA` failures)

Classification output: `real_dev_firm` / `parent_company` / `shell_LLC` / `owner` / `architect_firm` / `unknown`. For new registry entries, the agent must verify via web search before the registry row is created. Token-overlap guard runs at insertion time, not just at canonicalize-apply.

---

## 6. New status evidence types

Proposed additions to `STATUS_FROM_EVIDENCE_TYPE`:

```python
STATUS_FROM_EVIDENCE_TYPE = {
    "building_permit_issued": APPROVED,
    "construction_loan_closed": UNDER_CONSTRUCTION,    # new — supporting signal, requires corroboration
    "groundbreaking_announced": UNDER_CONSTRUCTION,    # new — strong signal from news
    "building_inspection_recorded": UNDER_CONSTRUCTION,
    "construction_topped_out": UNDER_CONSTRUCTION,     # new — late-stage signal
    "pre_leasing_active": PRE_LEASING_PRE_SELLING,     # new
    "temporary_co_issued": COMPLETE,                   # new — TCO
    "grand_opening_announced": COMPLETE,               # new — requires_review unless corroborated
    "certificate_of_occupancy_issued": COMPLETE,
}
```

`requires_review` should be set when the only evidence is news-only with no government corroboration. That keeps news signals useful without making them sufficient on their own. Specifically:
- `construction_loan_closed` alone: requires_review (financing closed but project may not have started)
- `groundbreaking_announced` alone: not requires_review — news of groundbreaking is reliable
- `pre_leasing_active` alone: not requires_review — listings are reliable
- `grand_opening_announced` without LADBS CofO: requires_review

---

## 7. Schema additions

### 7.1 `agent_assessment` table

```python
class AgentAssessment(Base, TimestampMixin):
    __tablename__ = "agent_assessment"

    id: UUID PK
    review_item_id: UUID FK ReviewItem (nullable)
    project_id: UUID FK Project (nullable)
    skill_name: str                    # 'reconcile-units', 'verify-construction-started', etc.
    pattern_class: str | None          # skill-specific subclassification
    model_version: str                 # 'claude-sonnet-4-6'
    prompt_version: str                # 'reconcile-units@v3'
    recommendation: str                # accept | reject | defer | needs_human
    recommendation_value: dict | None  # the proposed value if applicable (jsonb)
    reasoning_text: str
    sources_cited: list[dict]          # jsonb: [{url, title, fetched_at, claim_supported}]
    agent_confidence: enum             # low | medium | high
    cost_usd: float
    latency_ms: int
    created_at: timestamptz
    superseded_at: timestamptz | None  # set if re-run or invalidated
```

Indices on `(review_item_id, created_at)`, `(project_id, skill_name, created_at)`, `(skill_name, prompt_version)`.

### 7.2 `evidence` additions

```python
class Evidence(Base):
    # existing columns...
    verification_status: str = "unverified"
    # one of: unverified | agent_assessed | human_confirmed | human_rejected
    # independent of source_tier — captures the review dimension orthogonally

    signal_only: bool = False
    # True for non-status-changing findings the agent wants to log but not act on
    # signal_only=True rows do not feed the resolution engine but are visible in the project's evidence tab
```

Open question: do `signal_only=True` rows count for `evidence_count` in confidence rollup? Default proposal is **no** — they're for audit, not resolution. (Listed in §14.)

### 7.3 `review_decision` additions

```python
class ReviewDecision(Base):
    # existing columns...
    agent_assessment_id: UUID FK AgentAssessment (nullable)
    agreement: str                     # 'accepted_agent' | 'rejected_agent' | 'modified_agent' | 'no_agent_draft'
    disagreement_reason: str | None    # skill-specific enum, populated on reject/modify
    reviewer_note: str | None          # free text
```

The `agreement` field plus `disagreement_reason` is what powers the learning loop in §10.

### 7.4 Indexes for sweep performance

Out-of-bounds geocode sweep needs `jurisdictions.boundary` (geometry) plus a spatial index. Proximity sweep needs an additional `ix_projects_location_gist`. Same-parcel sweep needs the existing `ix_project_identifiers_value` to be efficient on APN lookups.

---

## 8. The `tcg-pipeline-mcp` server

Custom MCP server, Python, deploys alongside the Render workers. Talks to the same Supabase database. The agents (whether running as backend workers or as Cowork plugins) use it as their interface to the system.

### 8.1 Tools exposed

**Read tools** (cheap, no permission gates):
- `read_project(project_id) -> ProjectSnapshot` — full project row plus latest resolved values
- `read_evidence(project_id) -> list[EvidenceRow]` — all evidence for a project, ordered by evidence_date
- `read_review_items(project_id?, status?) -> list[ReviewItem]` — for queue inspection
- `read_neighbors_within_meters(project_id, meters) -> list[ProjectSnapshot]` — for proximity sweeps
- `read_developer_registry(name?, fuzzy?) -> list[DeveloperRow]`
- `read_source_runs(project_id?, source_type?) -> list[SourceRun]`

**Write tools** (each enforces invariants at the MCP boundary):
- `write_evidence_with_citation(project_id, source_type, source_record_id, evidence_date, raw_data, extracted_fields, sources_cited[], signal_only=False)` — rejects with error if any extracted_fields claim has no corresponding source URL
- `write_agent_assessment(review_item_id?, project_id?, skill_name, pattern_class, recommendation, ...)` — rejects without sources_cited[]
- `propose_review_item(project_id, item_type, priority, ...)` — for sweep agents creating net-new items
- `trigger_collector(project_id, source_type, reason, filters?)` — see §9
- `propose_relationship(project_id_a, project_id_b, relationship_type)` — for proximity / phase-sibling proposals (always staged, never committed by agent)

**Search/external tools** (delegated to Anthropic's hosted web search; not implemented by this MCP):
- The agent calls Anthropic's built-in web search via the SDK, not via this MCP. That keeps the MCP surface small and lets web search benefit from the standard fetch/render pipeline.

### 8.2 Invariants enforced at the MCP

1. Citation-or-fail on every write
2. Cooldown: reject `write_agent_assessment` if same `(review_item_id, skill_name)` already has an assessment within 7 days (configurable per skill)
3. Budget per skill: track cost_usd accumulator; reject calls when daily budget exhausted
4. No project mutation. Agent cannot write to `Project` columns directly. All mutation is mediated through evidence + resolution.
5. No researcher_override writes. Tier 0 stays human-only.

---

## 9. Generic `trigger_collector` design

Decided: build it generic, scoped to Socrata sources at first, expanded deliberately.

### 9.1 Signature

```python
trigger_collector(
    project_id: UUID,
    source_type: str,           # ladbs_cofo, ladbs_inspection, lahd_affordable, ...
    reason: str,                # "agent forward query: grand_opening signal found"
    filters: dict | None = None # narrowing hints (e.g., APN-only)
) -> {
    trigger_id: UUID,
    status: 'queued' | 'running' | 'complete' | 'skipped' | 'rejected',
    source_run_id: UUID | None,
    retry_after: timestamp | None
}
```

### 9.2 Behavior

- **Async by default.** Returns immediately with `status=queued` and a `trigger_id`. The actual collection runs in the existing worker pool. When it completes, evidence is written, resolution re-runs, and the resulting review item (if any) supersedes the agent's note.
- **Rate budgets per source.** Each `SourceRegistration` carries `max_forward_queries_per_day`. When exceeded, return `status=skipped` with `retry_after` hint.
- **Cross-agent dedup.** If two agents independently trigger the same `(project_id, source_type)` within an hour, the second returns `status=running, source_run_id=<original>` and waits on the first.
- **`trigger_type='agent_forward_query'`** in `SourceRun.trigger_type` (already supported by B.0c). Coverage view can show agent-triggered runs distinctly from scheduled and manual.
- **Source allowlist.** Initially Socrata-only: `ladbs_permits`, `ladbs_inspections`, `ladbs_cofo`, `ladbs_permit_activity`, `lahd_affordable`. PDF sources don't fit (re-fetching a biweekly PDF for one project is wasteful) and are excluded until a "lookup-against-cached-archive" pattern is built.

### 9.3 Escape hatch for high-cost sources

Different MCP tool for sources where forward-query is expensive or rate-limited:

```python
propose_forward_query(project_id, source_type, reason, justification)
  -> queue row for human approval
```

Same agent, same skill, two paths depending on source cost.

---

## 10. The learning loop

**Decision:** start with all agent recommendations requiring researcher approval. Track acceptance per (skill, pattern_class, prompt_version). Graduate to auto-accept only when accuracy is empirically demonstrated.

Three loops, in order of cost-to-build.

### 10.1 Loop 1 — aggregate metrics → graduation decisions

Scheduled job, daily. Per `(skill_name, pattern_class, prompt_version, time_window)`:

```
accept_rate, reject_rate, modify_rate
cost_per_decision, mean_latency_ms
mean_agent_confidence, calibration_gap
  (calibration_gap = |stated_confidence - empirical_accept_rate|)
```

Display on Dashboard. Initial graduation criteria for a `(skill, pattern_class)`:
- ≥100 reviewed assessments
- ≥95% accept rate over the trailing 60 days
- No 7-day window in that period below 90%
- Calibration gap within ±10%

When all four are met, the (skill, pattern_class) graduates. Graduation can mean either "auto-accept" or "auto-accept with a revoke window" — a UI filter that keeps recently auto-accepted items visible in a tab for N days for spot-checking. Window length scales with stakes:

| Decision type | Suggested revoke window after graduation |
|---|---|
| Unit reconciliation | 7 days |
| Developer name / canonicalization | 7 days |
| Delivery date | 14 days |
| Pre-leasing detection | 14 days |
| Status promotion (Approved → UC, UC → Complete) | 30 days |
| Dedup / merge proposals | **Never auto-accept** regardless of graduation |

### 10.2 Loop 2 — few-shot exemplars in prompts

Each prompt version of a skill carries a slot for "exemplars" — 5-15 past cases with the researcher's decision and reasoning. Periodically (monthly), regenerate exemplar sets:
- Diverse high-confidence accepts (with confirming reviewer notes if any)
- Diverse high-confidence rejects (with `disagreement_reason` populated)
- Bump `prompt_version`, run eval harness, ship if it passes

This is the lowest-cost, highest-impact learning mechanism. The agent calibrates to *researchers' actual judgment* without any model training.

### 10.3 Loop 3 — eval harness against historical decisions

CLI command: `tcg-pipeline agent-eval --skill reconcile-units --prompt-version v4 --since 2026-01-01`. Runs the new prompt against held-out historical review items where the researcher's decision is known. Outputs:
- Agreement rate vs. previous prompt
- Regression cases (cases the old prompt got right that the new one gets wrong)
- Cost / latency comparison

This gates prompt changes before they ship to production. Without it, "minor" prompt edits can silently regress on edge cases.

### 10.4 Risks to design around

**Researcher drift.** Standards change over time. A 60-day rolling window bounds how far back the system reaches. Mitigation: tag `ReviewDecision` with the user; when researchers diverge on the same item type, surface those items as eval candidates.

**Overfitting to easy cases.** If `pattern_class` skews easy (phase_split is obvious), aggregate accept rate may hide a hard-pattern problem. Per-pattern graduation handles this directly.

**Adversarial drift in agent behavior.** If the agent learns "researchers tend to accept anything with citations," it might cite weak sources. Mitigation: include citation quality in the eval harness — periodically sample and verify cited URLs actually support the agent's claim.

**Reviewer agreement loss.** When two researchers disagree on similar items, the agent will be calibrated to whoever does more reviews. Mitigation: tag decisions with user_id; flag systematic disagreements.

---

## 11. UX integration with the existing review queue

### 11.1 Always-approve mode (initial)

- Every agent-touched review item lives in the normal queue with status `OPEN`
- A small badge on the row indicates "has agent draft"
- A queue filter "Only items with agent draft" lets researchers prioritize fast wins
- Keyboard binding stays the same: `a` = accept (the agent's recommendation if drafted; otherwise the system default), `s` = keep old, `d` = defer, `f` = custom
- When `a` is pressed on an agent-drafted item, the `ReviewDecision` is created with `agreement='accepted_agent'`
- When `f` is pressed on an agent-drafted item, researcher's custom value is captured and `agreement='modified_agent'`
- When `s` is pressed, `agreement='rejected_agent'` and a small dialog asks for `disagreement_reason` (enum dropdown) and optional note. This is the learning data.

### 11.2 After graduation (per-pattern)

- Graduated `(skill, pattern_class)` items are auto-accepted on creation: `ReviewItem.status='AUTO_ACCEPTED'`
- A new "Agent-resolved" tab on the Review Queue shows recently auto-accepted items within their revoke window
- One-key revoke flips the status back to OPEN with the agent draft cleared
- After the window, items fall into the existing Reviewed tab — still reversible, just no longer visible in daily glance

### 11.3 Visibility into agent reasoning

Every agent-drafted item shows:
- Recommendation (the proposed value)
- Confidence (low/medium/high)
- Reasoning text (1-2 short paragraphs)
- Cited sources (URLs with title and fetched_at)
- Pattern class (e.g., "phase_split")
- Agent skill name + prompt version (for traceability)
- Optionally: the search queries the agent ran (open question — see §14)

---

## 12. Surface choices: backend, Cowork, embedded chat

The agent layer can be invoked from three places, each playing a different role.

### 12.1 Backend agent worker (Phase 1)

Always built first. No UI. Runs as a Render worker, scheduled and event-driven. Consumes from a queue of (skill_name, project_id, review_item_id) tuples produced by:
- Resolution engine creating new review items (event-driven)
- Sweep schedulers (proximity, out-of-bounds, sparse-evidence, etc.)
- Researcher-triggered re-runs

Writes assessments and evidence through the MCP server. Researchers see results in the existing review queue.

### 12.2 Cowork plugin (Phase 2)

Package the same skills as a Cowork plugin. Researcher gets agent-assisted ad-hoc work for free, using Anthropic's existing Cowork UX. No new UI to maintain.

Use cases best served by Cowork:
- Onboarding a new market (cross-tool work spanning local files, news, maps, notes)
- Generating a CMA block or exhibit appendix
- Investigating a developer's portfolio across markets
- Writing a project report combining pipeline data + meeting notes + emails

Researcher invokes skills like `/investigate-project [id]`, `/find-additional-sources [id]`, `/dedup-check [id_a] [id_b]`, `/verify-address [id]`. Same skills the backend worker uses, just driven interactively.

### 12.3 Embedded chat panel (Phase 3 — gated on demand)

A chat panel inside the Next.js app, using the same MCP under the hood. Auto-loads project context. Best for:
- Project-level work while looking at a specific project
- Bulk reconciliation in the review queue ("help me work these 5 items")
- Quick lookups in-context

**Built only after watching researchers use Cowork for a month.** The decision to invest in this panel depends on observed friction with context-switching to Cowork.

### 12.4 Decision: phase the surfaces

1. Phase 1 (foundational): backend agents only. Most value, lowest UI cost.
2. Phase 2: install MCP in Cowork. Add agent-assisted ad-hoc capability.
3. Phase 3 (conditional): embedded chat panel in Next.js if real usage warrants.

---

## 13. Suggested phasing — "Phase J" (proposed roadmap addition)

Not yet wired into ROADMAP.md. Approximately 10-14 weeks total, first value visible in 3 weeks.

### Phase J0 — foundation (1.5 weeks)

- `agent_assessment` table + migration
- `evidence.verification_status` and `evidence.signal_only` columns
- `review_decision.agent_assessment_id`, `agreement`, `disagreement_reason`, `reviewer_note`
- `tcg-pipeline-mcp` server skeleton with read tools and `write_evidence_with_citation`
- `agent_skills/` directory convention with one stub SKILL.md
- Bare-bones metrics view (per-skill accept rate aggregation)

### Phase J1 — first triage agents (3-4 weeks)

Three skills, one worker that dispatches by `ReviewItem.item_type`:
- `reconcile-units` — runs on units-change items with |Δ| > 5
- `validate-status-promotion` — runs on STATUS_CHANGE items where new value is UC or COMPLETE, OR permit-only Approved with `requires_review`
- `classify-developer` — runs on canonicalize candidates that would create new registry entries from Tier 3 sources, or merge at fuzzy-review threshold

Each writes an `agent_assessment` linked to the review item. UI shows it as an "Agent draft" panel. Researcher accepts with one keystroke.

**Pre-J1 prerequisite:** new evidence types added to `STATUS_FROM_EVIDENCE_TYPE` per §6, even before the agent ships, since `temporary_co_issued` and `pre_leasing_active` may also come from collector data once they exist.

**Critical first deliverable to prove value:** running `reconcile-units` on the existing 28-row Phase A units backlog. Tractable evaluation set, immediate researcher-time savings, agent calibration.

### Phase J2 — sweep agents (3 weeks)

- `out-of-bounds-geocode-sweep` — daily/weekly
- `same-parcel-dedup-sweep` — deterministic, no agent for clear cases
- `proximity-dedup-sweep` — weekly, with pre-filter

Output: net-new POSSIBLE_MATCH or `bad_geocode` review items, each with agent assessment attached.

**Prerequisite:** `jurisdictions.boundary` polygon column populated for City of LA. May require new migration.

### Phase J3 — sparse-evidence enrichment (2 weeks)

- `source-hunter` scheduled sweep on low-evidence projects

### Phase J4 — pre-leasing + CofO + construction-started detection (3 weeks)

This is where the user's specific high-priority capabilities ship.

- `detect-pre-leasing` — weekly sweep on UC + late-Approved
- `detect-cofo-and-grand-opening` — weekly sweep on UC projects, especially those with confirmed pre-leasing
- `verify-construction-started` — weekly sweep on Approved cohort with status_date older than 6 months

Heaviest-leverage of any phase for reporting accuracy; recommended priority above J3 if time-constrained.

### Phase J5 — stall investigator + Cowork plugin (2 weeks)

- `stall-investigator` for 12+ month no-evidence cohort
- Cowork plugin packaging the same skills for ad-hoc researcher use

### Cross-cutting concerns

- **Cost monitoring** runs from J0 — log every agent invocation's cost; aggregate per skill per day
- **Prompt versioning** runs from J1 — every skill has a version; eval harness can compare versions
- **Dashboard cells** for agent-acceptance metrics arrive when Dashboard ships (currently B.7 in ROADMAP)

### Where Phase J slots into the existing roadmap

- **Depends on:** Phase A (validated evidence layer), Phase B.0c (source_runs.trigger_type)
- **Independent of:** Phase B (read-only frontend) — agent layer can run before researchers see any UI
- **Potentially supersedes parts of Phase D:** an on-demand source-hunter agent gets ~70% of Phase D's value (BizJournals scraping) at maybe 20% of the build cost, and avoids the BizJournals TOS exposure
- **Potentially supersedes parts of Phase F:** agent forward-querying a source on demand may be a more cost-effective alternative to building scheduled scrapers for low-volume sources (e.g., CEQAnet)

To be decided: whether Phase J replaces, complements, or sequences with Phases D and F.

---

## 14. Outstanding questions

Captured here for resolution before Phase J is wired into ROADMAP.md.

### 14.1 Evidence and resolution semantics

1. **Does `signal_only=true` evidence count for `evidence_count` in confidence rollup?** Default proposal: no. Open: should it count for `last_evidence_date`?
2. **Should `signal_only=true` evidence appear in the project detail Evidence tab?** Default: yes (it's audit-relevant), but visually distinguished from resolution-feeding evidence.
3. **When the agent finds CofO/grand-opening news, should it ever assert COMPLETE without LADBS corroboration?** Current proposal: `requires_review=true` always. Should there be a strong-signal exception (e.g., 3+ corroborating news sources within 30 days)?
4. **Should rejected agent recommendations create a "negative evidence" row** so future runs avoid the same proposal? Default proposal: no — rejection log only. Negative evidence is a slippery slope.
5. **When agent has very high confidence, should the review item's `priority` be lowered** so it falls to the bottom of the queue (less urgent because likely fine)? Suggested: yes for graduated-pattern items, no for pre-graduation items.

### 14.2 Schema and tooling

6. **Where exactly do `verification_status` and `signal_only` live** — directly on `evidence` columns, or in a separate `evidence_metadata` table? Default: on `evidence` directly.
7. **How is `pattern_class` enumerated per skill** — a Python enum, a YAML config, an open string? Default: Python enum per skill, evolving alongside the skill.
8. **Cooldown window per skill** — 7 days uniform, or skill-specific? Some skills (sparse-evidence enrichment) probably want longer cooldowns (30+ days) than others (status validation).
9. **Worker pool sizing** — how many concurrent agent invocations? Depends on cost ceiling and how aggressive sweeps are.

### 14.3 Cost and budgets

10. **Cost modeling not done.** Need back-of-envelope: across 1,362 LA projects, what does a full run of each skill cost? Helps prioritize which sweeps are affordable on what cadence.
11. **Per-skill daily budget defaults** — set hard ceilings or soft budgets with overage warnings?
12. **Token budgets per agent invocation** — single-turn or multi-turn allowed? Multi-turn enables iteration but costs more.

### 14.4 Forward-query and source allowlist

13. **What forward-query sources beyond Socrata** should we open up over time? PDF sources, ArcGIS, Accela?
14. **Cooldown on forward-queries** — how often can the agent re-query the same source for the same project? Default: same as agent assessment cooldown.

### 14.5 Eval and learning loop

15. **Eval harness scope** — historical-only against past reviewer decisions, or live shadow mode where new agent versions run alongside production and disagreements are surfaced for review?
16. **Researcher disagreement handling** — when User A and User B disagree on similar items, how is that surfaced? Separate "researcher-disagreement" tab?
17. **Calibration recheck cadence** — once a (skill, pattern_class) graduates, how often do we sample auto-accepted items for human spot-check to detect drift? Suggested: continuous 5% sample.

### 14.6 UX and visibility

18. **Should researchers see the search queries the agent ran** as part of the agent draft? Helps trust and debugging but adds visual noise.
19. **Bulk-accept on agent-drafted items** — can researcher select 10 items and accept-all-agent-drafts? Probably yes for graduated patterns; risky pre-graduation.
20. **Cowork vs embedded chat** — explicitly deferred until usage data. Revisit after Phase J5.

### 14.7 Roadmap integration

21. **Where does Phase J slot relative to Phase C and Phase D?** Options:
    - In parallel with C-late (review queue UX assumes agent drafts exist)
    - Before Phase D (replaces parts of news scraping)
    - After Phase C ships (don't gate review-queue UX on agent availability)
22. **Does proximity dedup matching algorithm warrant a separate spec doc** like `docs/specs/proximity_dedup_algorithm.md`?
23. **First-skill spec** — should we draft a real `reconcile-units` SKILL.md (with prompt structure, search queries, pattern_class options, citation requirements) before implementation begins, or defer to implementation-time iteration?

### 14.8 Open principles questions

24. **Is the agent ever allowed to write to the `Project` table directly, even via mediated paths?** Current principle says no — only via evidence + resolution. But what about geocode corrections (where the agent's output is a corrected `lat/lng`)? Either route through evidence (with a new evidence type `geocode_correction`) or accept a narrow exception for corrections.
25. **Tier 0 protection vs. agent findings** — if a researcher override exists and the agent finds strong contradicting evidence, the existing review-protected override semantics apply. But should the agent's contradiction signal create a different review item type (e.g., `agent_override_challenge`) so it's visually distinct in the queue?
26. **Trust calibration over time** — should the system track per-source acceptance separately from per-skill (e.g., "developer_website signals are accepted 92% of the time, news_article 78%")? This crosses into the source-tier discussion and may warrant its own design pass.

---

## 15. Decision log

Append-only. Decisions made during 2026-04-25 discussion that, while not yet in ROADMAP.md, are recorded here as the working baseline.

| Date | Decision | Rationale |
|---|---|---|
| 2026-04-25 | Agent layer is researcher-assistive, never autonomous; Tier 0 stays human-only | Aligns with existing principle "automate collection, not judgment" |
| 2026-04-25 | All agent recommendations require explicit researcher approval initially | Build trust before graduating to auto-accept; requires evidence base for graduation |
| 2026-04-25 | Tier reflects source authority, not collection method; verification_status is orthogonal | Keeps source-tier semantics clean; agent review becomes a separate quality dimension |
| 2026-04-25 | Always-write evidence with `signal_only` flag for non-actionable findings | Preserves audit completeness; prevents noise from polluting resolution |
| 2026-04-25 | `trigger_collector` MCP tool is generic; scoped to Socrata sources at first | Reusable long-term; bounded blast radius initially |
| 2026-04-25 | Citation-or-fail enforced at MCP write boundary | Prevents agent hallucinations from entering the evidence corpus |
| 2026-04-25 | Dedup proposals always require human review regardless of agent confidence | Merges feel irreversible; one-bad-merge can corrupt downstream IDs |
| 2026-04-25 | Per-pattern_class graduation, not whole-skill | Avoids high-volume easy patterns hiding hard-pattern problems |
| 2026-04-25 | Eval harness against historical decisions gates prompt-version changes | Prevents silent regressions on edge cases when iterating prompts |
| 2026-04-25 | Status-promotion auto-accept (post-graduation) gets 30-day revoke window; unit reconciliations 7 days | Risk asymmetry — UC promotion flows into pipeline counts and reports |
| 2026-04-25 | `temporary_co_issued`, `grand_opening_announced`, `groundbreaking_announced`, `construction_topped_out`, `construction_loan_closed`, `pre_leasing_active` proposed as new status evidence types | Closes status-coverage gaps the existing source set doesn't reach |
| 2026-04-25 | Backend agent worker built first; Cowork plugin second; embedded Next.js chat panel only if usage data warrants | Defer UI investment until value is proven |

---

## 16. Glossary

- **Agent assessment** — a record of the agent's analysis of a specific review item or project; contains recommendation, reasoning, citations, confidence
- **Pattern class** — skill-specific subclassification of a case (e.g., for unit reconciliation: `phase_split`, `rescope`, `mixed_use_accounting`, `data_error`)
- **Pre-filter** — a deterministic check the agent layer runs before invoking a model, to avoid wasting cost on cases that are obviously clear-cut
- **Revoke window** — a UI filter that keeps recently auto-accepted items visible in a tab for N days, allowing one-click reversal during the spot-check period
- **Skill** — a versioned, documented agent task; lives as a `SKILL.md` in `agent_skills/` and is loadable across surfaces (backend worker, Cowork, embedded chat)
- **Sweep** — a scheduled job that scans the dataset for candidates and queues agent investigations
- **Triage** — agent work on existing review items (reading them and drafting recommendations), as distinct from sweep work (finding new items)
- **Verification status** — orthogonal-to-tier dimension on evidence rows: `unverified | agent_assessed | human_confirmed | human_rejected`
- **Forward-query** — agent calling `trigger_collector` to fetch fresh data from a specific source for a specific project, rather than waiting for the scheduled run

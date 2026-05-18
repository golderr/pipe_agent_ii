# News Extraction Expansion — Overview

> **Status:** Plan locked 2026-05-18. Forward-compatible foundation (D.EXP.0/1/2/10) cleared to land in parallel with `AGENT.reset` cycle 1. Full A/B + downstream wiring (D.EXP.5E and Chunks 3R–8) gated on post-cycle-1 first-settle period.
>
> **Roadmap home:** `ROADMAP.md` `Cross-cutting: News extraction expansion (D.EXP.*)` and the four `D.EXP.late.*` deferred rows.
> **Sister docs:** `docs/operations/reviewer_usefulness_review.md` (post-deployment automated review runbook), `docs/specs/cycle1_prep_plan.md` Deferred follow-ons section (back-pointer).
> **Maintained by:** Nate Goldstein + Claude Code.

---

## 1. TL;DR

Expand the news article → project pipeline to capture 15 additional candidate fields (rent/sale tenure, bedroom counts, status milestone date, construction start, mixed-use SF components, parking, acres, architect, affordable program, project description, building style, previous names, relationship signals) and introduce a three-stage extraction pipeline — **Primary → Verifier → Arbiter on disagreement** — governed by a **closed audit on 14 high-stakes targets**. Other new fields are captured opportunistically (citation required, no audit pressure). Pass-2c (semantic policy translator) stays in its current lane and does NOT receive raw extraction expansion. Two adjacent capabilities — split-project workflow, news-driven relationship signal routing — are explicitly deferred to follow-up cycles with Phase-1 capture/instrumentation in this cycle.

All changes are forward-compatible. Foundation chunks land in parallel with `AGENT.reset` cycle 1; A/B-gated work runs post-cycle-1 using organic reviewer corrections as the fixture seed.

---

## 2. Motivation

The current news extractor (Pass-1 with the `extract_v2` prompt) produces a fixed candidate set covering name, address, developer, unit totals, the affordable/market-rate/workforce composition, stories (newly wired), product type, age restriction, status, delivery date, identifiers, and a small set of registry-gated signal flags.

Several common analytical fields are not in the schema — bedroom mix, parking, mixed-use SF components, total_sf, acres, architect, affordable program, project description, building style, status milestone date, construction start date — so articles stating those facts produce no Evidence rows. Additionally, the prompt provides no explicit recall pressure on the high-value fields, no second-pass verification, and no anti-gloss-over discipline beyond the existing per-value passage-excerpt requirement.

The originating concern was bedroom mix specifically ("an article says 120 1BRs and 80 2BRs and the pipeline ignores it"). After senior-developer review, the design rationale was **reframed**: the closed audit lives where high-frequency-contradiction and high-analytical-stakes fields warrant it, which is the 14 targets below. Bedroom mix is captured **opportunistically** with a soft-emphasis prompt line — sufficient for recall, without consuming an audit slot that would dilute attention on the 14 high-stakes fields. See Decision Log 2026-05-18.

---

## 3. Architecture

Three-stage extraction pipeline replaces today's single-pass extract:

**Primary extractor** — rewritten prompt in 6 sections:
1. Project boundary identification (multi-project articles get per-reference field isolation; no cross-project copying)
2. Closed-audit "actively inspect" framing on the 14 targets
3. Opportunistic field guidance (citation required; no audit)
4. Citation rule — every non-null value needs a `passage_excerpt`
5. Null vs. enum-Unknown discipline — prefer null when the schema allows
6. Anti-retreat re-read trigger — if the article is primarily about the project and nearly all audit fields are `not_stated`, re-read

Emits a 14-key `field_audit` block (state per target: `stated` / `not_stated` / `ambiguous` / `inferred`), an `uncertain_observations[]` array for unassigned signals, and `candidate_rent_or_sale_inferred_from` when rent_or_sale is `inferred`. Bedroom counts get a soft-emphasis line in the opportunistic section. 7 explicit accuracy prohibitions:

- No totals from partial counts
- No market-rate-by-subtraction arithmetic
- No publication-date-as-status_date
- No stories from height/podium-levels/zoning/renderings
- No rent_or_sale inference outside the allowlist
- No relationship signals from proximity or shared developer alone
- No normalizing vague timing into exact dates

**Verifier** — new second model call. Audits the 14 high-value targets only. **Blind to primary's `candidate_confidence` score** (prevents confirmation bias). Per-target output: `supported` / `unsupported` / `missing_but_stated` / `ambiguous` / `contradicted` with passage citation and brief rationale. Does not audit opportunistic fields.

**Arbiter** — new third model call, **conditional** on any non-`supported` verifier state. **Batched per project_reference** (one call resolves multiple disputed fields coherently, with cross-field context). Inputs: article text, disputed fields only, primary value + citation, verifier state + rationale, allowed enums and normalization rules. Outputs per disputed field: final value or null, final state, rationale, supporting passage, **primary trustworthiness signal** (telemetry over time). Per-article budget cap.

**Pass-2c stays in its current policy-translation lane** — status reason codes, tenure corroboration rules, multi-tenure routing, project-cancellation routing, status-regression routing. The latent declaration of `rent_or_sale` in `SEMANTIC_CANONICAL_FIELDS` means Pass-2c **consumes** Pass-1's value to produce reason codes; it does NOT re-extract. Any future PR adding fields to `pass2c.py` raw extraction should be rejected and routed back to the three-stage pipeline.

**Evidence-layer contract**:
- Closed-audit fields write Evidence using the arbiter-final value when arbiter ran, primary value when verifier said `supported`
- Opportunistic fields write Evidence directly from primary as today
- `Evidence.source_name` stays `"news_article"`; verification provenance lives in `Evidence.metadata` JSONB (`verification_state`, `arbiter_invoked`, `primary_value`, `final_value`, `audit_chain`)
- Existing per-field resolvers and `resolution/engine.py` dispatch don't change shape

---

## 4. Closed audit — 14 targets

Composite definitions in parens.

| # | Target | Covers |
|---|---|---|
| 1 | **location** | `candidate_address` + `candidate_city` + `candidate_lat` + `candidate_lng` + `candidate_neighborhood` |
| 2 | **project_name** | `candidate_name` |
| 3 | **developer** | `candidate_developer` |
| 4 | **identifiers** | `candidate_identifiers.{case_number, permit_number, apn}` |
| 5 | **total_units** | `candidate_unit_total` |
| 6 | **unit_composition** | `candidate_unit_affordable` + `candidate_unit_market_rate` + `candidate_unit_workforce` |
| 7 | **rent_or_sale** | `candidate_rent_or_sale` |
| 8 | **product_type** | `candidate_product_type` |
| 9 | **age_restriction** | `candidate_age_restriction` |
| 10 | **pipeline_status** | `candidate_status_signal` |
| 11 | **status_date** | `candidate_status_date_text` + `candidate_status_date_normalized` |
| 12 | **date_delivery** | `candidate_delivery_year_text` + `candidate_delivery_year_normalized` |
| 13 | **stories** | `candidate_stories` |
| 14 | **relationship_signal** | `candidate_relationship_signal` *(Phase-1 capture only — no routing this cycle, see `D.EXP.late.RELSIG`)* |

### Opportunistic fields (capture if stated + cited; no audit pressure)

- Bedroom counts: `candidate_unit_studio` / `_1bed` / `_2bed` / `_3plus_bed` *(soft-emphasis line in prompt)*
- `candidate_date_construction_start_text` + `_normalized`
- `candidate_total_sf`, `candidate_retail_sf`, `candidate_office_sf`, `candidate_hotel_keys`, `candidate_parking_spaces`
- `candidate_acres`
- `candidate_previous_names`
- `candidate_architect`
- `candidate_affordable_type`
- `candidate_description`
- `candidate_style` (controlled enum: wrap, podium, garden, walk-up, mid-rise, high-rise, single-family, townhome, adaptive-reuse, other)
- `candidate_signal_flags` (already registry-gated and opportunistic by today's design)

### Explicitly omitted from the schema entirely

`owner`, `true_owner`, `applicant`, `zoning`, `entitlement_type`, `appeal_status`, `ceqa_status`, `planner_1/2_*`, `property_type`, style/architecture beyond the controlled enum. Reasoning: news-rare (planners, applicant), better-sourced elsewhere (CoStar/permits for `true_owner`, zoning), or out-of-scope analytical fields. Listed here so future proposals can be evaluated against this decision rather than treating them as oversights. See `D.EXP.late.OMITTED`.

---

## 5. rent_or_sale inference allowlist

Allowlist has two tiers; everything else requires explicit tenure language.

**Tier 1 — Bare-noun lexical map (low-risk, deterministic)**:
- "condo" / "condominium" → For-Sale
- "apartment" / "apartments" → Rental
- All other product nouns (townhome, single-family, etc.) → no inference

**Tier 2 — Contextual sales/leasing language (subject-of-verb rule)**:
- Allowed: subject is a **housing unit** ("units are for sale", "homes are being sold", "now leasing apartments", "pre-sales have begun for residences", "buyers can purchase a 2BR")
- Disallowed: subject is **land / parcel / property / site** ("the parcel sold", "the site was acquired", "land transactions", "ground lease executed")
- Disallowed: subject is the **developer or owner** ("developer sold its stake", "ownership changed hands")
- Disallowed: subject is the **project itself as a transaction** ("the project sold for $X")

Conflict between tiers (e.g., article says "condo" but also "units now leasing") → emit both signals into `inferred_from`, route to `MULTI_TENURE_REVIEW` (existing ReviewItemType — reused).

Explicit tenure descriptors ("a 200-unit rental project") → `field_audit = stated`, not `inferred`.

---

## 6. Schema impact

### `Project` — 7 new columns (Alembic migration #0041)

All nullable, no backfill. Every other field added to the news schema (parking, acres, retail_sf, office_sf, hotel_keys, total_sf, previous_names, architect, affordable_type, description, style, rent_or_sale, status_date, date_construction_start, zip/state/county/city) **already exists** on `Project` — verified directly against `src/tcg_pipeline/db/models.py:412-557`.

- `unit_studio`, `unit_1bed`, `unit_2bed`, `unit_3plus_bed` — `Integer`
- `census_tract`, `census_block`, `census_block_group` — `String(20)`

**Bedroom mix design (Option B)**: raw count columns added alongside existing `pct_studio`/`pct_1bed`/`pct_2bed`/`pct_other_bed`. Counts are intrinsically more reliable from prose; percentages are a downstream computation. Resolver computes percentages when `total_units` is also known.

**`RelationshipType.SPLIT`** enum value is NOT added this cycle — defers with the split workflow (`D.EXP.late.SPLIT`).

### `NewsProjectReference` — 19 new columns (Alembic migration #0042)

All nullable, no backfill of existing rows. Follows the existing `candidate_*` pattern at `models.py:1427-1451`.

**16 candidate value columns**:
- `candidate_rent_or_sale` — Text
- `candidate_unit_studio` / `_1bed` / `_2bed` / `_3plus_bed` — Integer
- `candidate_total_sf` / `_retail_sf` / `_office_sf` / `_hotel_keys` / `_parking_spaces` — Integer
- `candidate_acres` — Float
- `candidate_status_date_text` — Text; `candidate_status_date_normalized` — Date
- `candidate_date_construction_start_text` — Text; `candidate_date_construction_start_normalized` — Date
- `candidate_previous_names` — `ARRAY(String)`
- `candidate_architect`, `candidate_affordable_type`, `candidate_description`, `candidate_style` — Text
- `candidate_relationship_signal` — JSONB (Phase-1 capture only)

**3 audit/verification JSONB columns**:
- `candidate_field_audit` — closed 14-key audit block from Primary
- `candidate_uncertain_observations` — array of `{note, passage, offset_start, offset_end}`
- `candidate_rent_or_sale_inferred_from` — `{tier, trigger_phrase, passage, offset_start, offset_end}` or null

**Verifier and arbiter output** persist to additional columns on `NewsProjectReference` (`verifier_audit` JSONB, `arbiter_audit` JSONB) — exact placement TBD in `D.EXP.5V` (could move to a separate `news_reference_verifications` table if the row gets too wide).

---

## 7. Implementation chunks

| Chunk | Description | Blast radius | Reversibility |
|---|---|---|---|
| **D.EXP.0** | Doc updates: `cycle1_prep_plan.md` deferred-follow-ons, this overview, runbook, ROADMAP rows | None | Trivial |
| **D.EXP.1** | `Project` columns + migration #0041 | DB only; all nullable | Drop columns |
| **D.EXP.2** | `NewsProjectReference` columns + migration #0042 | DB only; all nullable | Drop columns |
| **D.EXP.10** | Census enrichment workstream (Census Geocoder + city/state/county/zip backfill) | New service + worker; isolated writes | Disable worker; columns stay null |
| **D.EXP.3R** | JSON Schema additions to `extract_v2/schema.json` | Schema only; no behavioral change until 5R | Revert file |
| **D.EXP.4R** | Primary extractor prompt rewrite (`extract_v2/system.md`) — 6 sections | Prompt change → measurable extraction shifts | Revert file |
| **D.EXP.5R** | `extraction.py` Pydantic + validators (audit consistency, null discipline, no-cross-project-copy) | Affects all new extractions | Revert; existing extractions unaffected |
| **D.EXP.5V** | Verifier module (new) | Adds a model call per article | Disable; primary extractions unchanged |
| **D.EXP.5A** | Arbiter module (new) | Adds conditional model call | Disable; verifier results stand |
| **D.EXP.5E** | A/B evaluation gate (three arms, cost metrics included) | Eval only; no production impact | N/A |
| **D.EXP.6** | `integration.py` wiring (routing, Evidence metadata provenance, material-contradiction extensions, silent counters) | Affects how new extractions write to Project | Revert wiring; candidates stay in NewsProjectReference but don't reach Project |
| **D.EXP.7** | Per-field resolvers (15 fields) | Affects resolution outputs for new fields | Revert; Project falls back to prior source priorities |
| **D.EXP.8** | Differ + review UI surfaces | UI only | Revert |
| **D.EXP.12** | Tests + regression suite | Test-only | N/A |
| **D.EXP.review** | Reviewer usefulness self-arming review schedule | Scheduled remote agent + runbook | Disable schedule |

### Dependency graph

```
D.EXP.0 (docs)
   ├─> D.EXP.1 (Project schema)
   │      └─> D.EXP.2 (NewsRef schema)
   │             └─> D.EXP.3R (JSON schema)
   │                    └─> D.EXP.4R (prompt)
   │                           └─> D.EXP.5R (Pydantic)
   │                                  ├─> D.EXP.5V (Verifier)
   │                                  │      └─> D.EXP.5A (Arbiter)
   │                                  │             └─> D.EXP.5E (A/B gate)
   │                                  │                    └─> D.EXP.6 (integration wiring)
   │                                  │                           ├─> D.EXP.7 (resolvers)
   │                                  │                           └─> D.EXP.8 (differ + UI)
   └─> D.EXP.10 (census)   [parallel, independent of all news work]

D.EXP.12 (tests) runs alongside every chunk that has testable behavior.
D.EXP.review schedule arms after the cycle-1 git tag `cycle1-news-extraction-v1` is pushed.
```

---

## 8. Timing — parallel with `AGENT.reset` cycle 1

**Decision (2026-05-18, senior-dev confirmed):** Option 3 — foundation lands in parallel with cycle 1, A/B + downstream runs post-cycle-1. **"Foundation before AGENT.reset" means lands without blocking cycle 1 kickoff, not a serial gate ahead of it.**

Practical sequencing:

1. **This week**: deploy `5H` source-run-market hardening, spot-check production. *(Out of scope of this doc — sister workstream.)*
2. **Next 1–2 weeks**: `AGENT.reset` cycle 1 kickoff. **In parallel: D.EXP.0 / 1 / 2 / 10.** These are nullable column additions and an independent enrichment service; they survive cycle 1's truncate-and-reseed (config tables are preserved; new nullable columns just stay null until later chunks populate them).
3. **Cycle 1 first-settle period (~2–4 weeks)**: no new extractor changes. Let organic data accumulate. **Reviewers correcting extractions naturally produce labeled examples for the eventual A/B fixture set.**
4. **Post-cycle-1**: D.EXP.5E A/B eval setup + Chunks 3R–8, gated on the A/B winner.

Cycle 1's data lifecycle: each reset cycle wipes data tables and re-runs the bootstrap. The forward-compatible foundation columns are config-table-adjacent (additive, nullable, non-breaking) so they're safe under `AGENT.reset`'s constraint of "do not break config tables; everything else is fair game to mutate freely until R fires."

---

## 9. Eval gates

### Gate 1 — D.EXP.5E A/B evaluation (gates downstream wiring)

Three arms on a labeled fixture set (50–100 hand-graded articles, sourced from cycle-1 first-settle reviewer corrections):

- **Arm A** — full closed audit on all extractable fields
- **Arm B** — 14-target closed audit only, no verifier
- **Arm C** — 14-target audit + verifier + arbiter-on-disagreement

**Metrics — quality**:
- Recall on the 14 audit targets (per-field and aggregate)
- False-positive rate (verifier `unsupported` calls confirmed by human review)
- `not_stated` overuse (retreat detection — anti-recall regression signal)
- Schema-validation-failure rate (Pydantic / JSON Schema rejection rate of primary output)
- Reviewer usefulness — % of arbiter-resolved fields the human accepts without override

**Metrics — performance**:
- p50 / p95 latency end-to-end per article

**Metrics — cost (first-class, per senior-dev direction)**:
1. **Cost per arm at current article volume** — LLM tokens × per-token pricing, summed per article, averaged across the fixture set
2. **Projected cost at projected steady-state volume** — current LA Urbanize cadence + Phase F additional collectors (LA YIMBY, The Real Deal LA, BizJournals LA) + Phase H Santa Monica volume. Project rate × per-article cost × Verifier multiplier × Arbiter trigger rate
3. **Cost-per-validated-extraction** — total LLM cost ÷ number of fields that passed the closed audit (i.e., reached `supported` or `arbiter-final`). The real efficiency signal: Arm B may be cheaper per article but less efficient per validated field if it leaks errors that cost reviewer time downstream

Winner gates D.EXP.6 / 7 / 8. If Arm C (verifier + arbiter) wins on quality but loses on cost-per-validated-extraction at projected volume, revisit the arbiter trigger threshold or batching strategy before final commit.

### Gate 2 — Reviewer usefulness review (post-deployment, automated)

Self-arming scheduled remote agent (weekly for first 4 fires, monthly thereafter) reads `docs/operations/reviewer_usefulness_review.md` as source of truth for queries and thresholds. Skips silently before the git tag `cycle1-news-extraction-v1` is pushed at the moment Chunks 6/7/8 actually go live.

Primary thresholds (full definitions in the runbook):
- Arbiter-final acceptance rate ≥ 80%
- Verifier precision ≥ 90%
- Verifier recall on `missing_but_stated` ≥ 70%

On threshold failure → writes report to `docs/review_logs/`, opens GitHub issue, emails `ng@theconcordgroup.com` with the failure and next steps from the runbook escalation rules.

---

## 10. Explicitly deferred (Phase-1 instrumentation only this cycle)

| Item | Phase-1 (this cycle) | Phase-2 (deferred row) |
|---|---|---|
| **Split-project workflow** | Silent `tenure_split_signal_count` counter on Project; trigger fires no review item, no destructive action | `D.EXP.late.SPLIT` — full agent-investigated trigger / review / atomic split / undo. Prereqs: reliable tenure extraction in production, Evidence allocation model (Evidence rows need shared/component tags — non-trivial schema change), volume justifies build |
| **Relationship signal routing** | `candidate_relationship_signal` captured to `NewsProjectReference` for observation; no matching, no `ProjectRelationship` inserts | `D.EXP.late.RELSIG` — matcher resolving `related_project_text` → existing project_id, low-confidence ProjectRelationship insertion, review UI, calibrated confidence thresholds. Re-evaluate ~1 month after D.EXP.6 ships |
| **Pass-2c expansion** | Pass-2c stays untouched and continues consuming Pass-1's values for policy translation | `D.EXP.late.passes` — architectural-decision row prevents future PRs from violating this boundary |
| **Omitted fields** | Not in the schema; not extracted | `D.EXP.late.OMITTED` — architectural-decision row prevents re-litigation |

---

## 11. Risks (carried forward)

1. **Closed audit could induce retreat into `not_stated`.** D.EXP.12 anti-retreat fixtures are the primary guardrail. Failure mode: roll back to a narrower audit list before falling back to opportunistic-only.
2. **Arbiter cost spike on pathologically ambiguous articles.** Per-article budget cap + verifier-blind-to-confidence rule should bound this. Measured in D.EXP.5E.
3. **`previous_names` array-append semantics** are unique among resolvers — spec dedupe, case-normalization, and "previous vs typo" rules in D.EXP.7.
4. **`status_date` vs `last_evidence_date` confusion** — status_date = milestone date per article; last_evidence_date = pipeline-wide latest evidence. Resolver must not use the latter as a fallback for the former.
5. **StatusHistory population integrity** — longitudinal analyses (time-in-stage, completion rates) depend on `StatusHistory` being written on every status transition. New verifier/arbiter pipeline writes to `pipeline_status` and must route through the StatusHistory-emitting path. Regression test in D.EXP.12 walks every Project and fails CI if current `pipeline_status` differs from latest `StatusHistory.status`. A one-time backfill from `ChangeLog` seeds missing rows before the test starts enforcing.
6. **Census Geocoder rate limits** undocumented; conservative request rate + retry-with-backoff + circuit breaker for the backfill job. **Before committing the full enrichment worker**, run the NULL audit (see §12) — if reverse-geocoding is already solid, the worker becomes lower-priority and only the column additions are needed.
7. **Relationship_signal capture without routing creates an observation backlog.** Re-evaluate cadence for Phase-2 build after ~1 month of capture data.

---

## 12. Pre-commit audit (Chunk 10 sizing question)

Before committing the full Census enrichment worker, run the NULL-rate audit on news-attached projects. If reverse-geocoding is already solid (low NULL rates on city/state/county/zip), Chunk 10 collapses to "just add the three census_* columns" without the enrichment worker. If NULL rates are meaningful, the worker is justified.

```sql
-- NULL-rate audit for geographic fields on news-attached projects
SELECT
  COUNT(*) AS total_news_attached_projects,
  COUNT(*) FILTER (WHERE p.city IS NULL OR p.city = '') AS null_city,
  COUNT(*) FILTER (WHERE p.state IS NULL OR p.state = '') AS null_state,
  COUNT(*) FILTER (WHERE p.county IS NULL OR p.county = '') AS null_county,
  COUNT(*) FILTER (WHERE p.zip IS NULL OR p.zip = '') AS null_zip,
  ROUND(100.0 * COUNT(*) FILTER (WHERE p.city IS NULL OR p.city = '') / NULLIF(COUNT(*), 0), 2) AS pct_null_city,
  ROUND(100.0 * COUNT(*) FILTER (WHERE p.state IS NULL OR p.state = '') / NULLIF(COUNT(*), 0), 2) AS pct_null_state,
  ROUND(100.0 * COUNT(*) FILTER (WHERE p.county IS NULL OR p.county = '') / NULLIF(COUNT(*), 0), 2) AS pct_null_county,
  ROUND(100.0 * COUNT(*) FILTER (WHERE p.zip IS NULL OR p.zip = '') / NULLIF(COUNT(*), 0), 2) AS pct_null_zip
FROM projects p
WHERE EXISTS (
  SELECT 1 FROM news_project_references npr
  WHERE npr.matched_project_id = p.id
);
```

Decision rule: if any `pct_null_*` for city/state/county exceeds ~2%, the enrichment worker is justified (cycle 1's news ingestion will widen the gap, not close it). If all three are at or near 0%, ship the columns without the worker and revisit if Phase H Santa Monica or new sources surface gaps.

`pct_null_zip` is OK to be higher (zip is harder to reverse-geocode reliably from rural / boundary coordinates) and is **not** a worker trigger by itself.

---

## 13. Cross-references

- **ROADMAP**: `ROADMAP.md` `Cross-cutting: News extraction expansion (D.EXP.*)` section and the four `D.EXP.late.*` deferred rows
- **Cycle 1 prep plan**: `docs/specs/cycle1_prep_plan.md` Deferred follow-ons section (back-pointer)
- **Reviewer usefulness runbook**: `docs/operations/reviewer_usefulness_review.md`
- **Existing news design contract**: `docs/specs/news_research_design.md` (Phase D foundational design — this work extends it)
- **Semantic interpretation boundary**: `docs/specs/semantic_interpretation_layer_design.md` (Pass-2c's protected scope)
- **Change-impact framework**: `docs/ops/change_impact_classification.md` (tiered reset rules — Chunks 0/1/2/10 are Tier 0; Chunks 3R–8 are Tier 2 or higher depending on phase)

---

## 14. Decision Log (anchored entries)

These decisions are also recorded in `ROADMAP.md` §8 Decision Log so they survive even if this doc is decommissioned. Listed here for at-a-glance reference.

- **2026-05-18** — News extraction expanded to 15 new fields with closed-audit philosophy
- **2026-05-18** — Pass-2c stays in policy-translation lane; raw extraction expansion goes to Primary+Verifier+Arbiter
- **2026-05-18** — `rent_or_sale` inference allowlist: bare-noun (Tier 1) + sales/leasing context with subject-of-verb rule (Tier 2)
- **2026-05-18** — Closed audit = 14 targets (composite where noted), not 30+
- **2026-05-18** — Split-project workflow + relationship-signal routing both deferred; Phase-1 instrumentation lands in this cycle
- **2026-05-18** — Reviewer usefulness review is automated via self-arming scheduled agent, not memory-dependent
- **2026-05-18** — Bedroom-mix reframed: originating concern downgraded after senior review; full expansion justified on the other 14 fields, not on bed-mix recall. Bedroom counts stay opportunistic with a soft-emphasis prompt line

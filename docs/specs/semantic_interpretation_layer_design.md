# Semantic Interpretation Layer Design

> **Status:** Design — not yet implemented.
> **Implementation owner:** AGENT.2 sub-sequence step 7.
> **Implementation contract for:** the shared semantic field interpretation layer specified in `agentic_escalation_design.md` §5.1.1.
> **Last updated:** 2026-05-07 (revision 5 — Pass 2c runway cleanup + audit persistence)

---

## 1. Overview

The semantic interpretation layer converts observed source-specific facts and language into canonical TCG evidence-field values. It sits between the source-specific extractors (news Pass 0/1/2, LADBS adapters, CoStar/Pipedream ingesters, future permit adapters) and the evidence write layer.

Today, most semantic mapping lives implicitly inside source-specific code: the news extractor emits `candidate_status_signal`, the LADBS adapter emits `Approved` evidence on permit issuance, the resolver decides on the resulting evidence rows. This works for narrow sources but does not scale: TCG `pipeline_status` / `product_type` / `age_restriction` / `date_delivery` / unit-bucket semantics duplicate and drift across each source profile, and the rules cannot be inspected, unit-tested, or A/B-tested independently.

This document specifies a shared interpretation layer with:

- **Per-source-profile interpreters** owned by each source profile (`agentic_escalation_design.md` §5.9).
- **A common output schema** carrying canonical value, confidence, reason code, signal flags, and source anchors.
- **A reason-code registry** so every interpreter output is auditable, A/B-testable, and renderable in the review-queue card.
- **A jurisdiction policy layer** that gates ambiguous status promotion based on per-jurisdiction permit-data quality.
- **A resolver-side corroboration step** that elevates confidence honestly when independent sources agree.

The field domains covered are organized in two scope tiers:

**v1 in-scope (Evidence-derived today):** `pipeline_status`, `product_type`, `age_restriction`, `date_delivery`, and the unit-bucket family (`total_units`, `affordable_units`, `workforce_units`, `market_rate_units`).

**v1 in-scope (signal flags only; no evidence write until researcher sign-off):** `rent_or_sale` (tenure). This is currently Source-populated direct. Step 7 records tenure observations as structured signal flags, but does not promote `rent_or_sale` to Evidence-derived or write canonical tenure evidence without a separate researcher decision — see §5.6.

**v1 in-scope (cross-field interpretation that does not write canonical evidence yet, but emits structured signal flags consumed by the matcher / resolver / future fields):** vague-location language → coordinates (§5.7), project name cleaning + alias detection (§5.8), identifier extraction (§5.9), developer role disambiguation (§5.10), status nuance for delayed / cancelled / abandoned articles (§5.11).

**Forward-compatible (future-scope fields):** the interpreter framework supports `stories`, `retail_sf` / `office_sf` / `hotel_keys` / `total_sf`, `affordable_type`, `entitlement_type` / `appeal_status` / `ceqa_status` once they graduate from Source-populated direct to Evidence-derived. Reason codes are pre-defined now (§5.12) to avoid registry rework later.

**Implementation strategy varies per source profile.** News articles (and other prose-bearing sources like developer websites) are interpreted by a focused LLM call (§3.4 / §4): Pass 2c, single-shot Opus 4.7 with the `interpret_v1` prompt, downstream of the existing Pass 2b extraction call. Structured sources (LADBS permits, CoStar exports, Pipedream workbooks, future structured permit feeds) use deterministic rules because the source is already structured. The framework abstraction is the same in both cases — same `SemanticInterpretation` output schema, same reason-code registry, same source-profile-owned `SemanticInterpreterProfile`.

This document does NOT cover:

- The agent runner architecture (covered in `agentic_escalation_design.md`).
- Pre-Leasing detection via leasing-site discovery (covered in roadmap row AGENT.6). Step 7 emits the trigger signal flags AGENT.6 consumes.

---

## 2. Design principles

1. **LLM where the source is natural prose; deterministic where the source is structured.** This is the primary architectural pivot of revision 4. News articles, developer websites, alert RSS feeds, and any future prose-bearing source are interpreted by a focused LLM call (single-shot Opus, structured output) because phrase tables cannot enumerate the variation in real article language. Structured sources — LADBS permit rows, CoStar columns, Pipedream cells, future structured permit / parcel / planning-system feeds — are interpreted by deterministic rules because the source already provides field-typed values; an LLM adds nothing. Both implementations conform to the same `SemanticInterpreter` Protocol and emit the same `SemanticInterpretation` shape, so resolver / review / audit code is source-agnostic.

2. **Source-profile-owned mapping.** TCG semantics are not buried in any one extractor. Each source profile (`news_v1`, `ladbs_v1`, future `costar_v1`, `pipedream_v1`, `permit_v1`) declares its own `SemanticInterpreterProfile` with the per-field interpreter implementations it uses, and chooses LLM vs deterministic per principle 1. Common deterministic utilities are shared as libraries; profile-specific logic stays in the profile. The reason-code registry is the shared vocabulary across profiles.

3. **Honest output, not silent decisions.** Every interpreter output carries a reason code, a confidence, source anchors, and (where relevant) signal flags. Resolver and review-queue display this context. We never silently collapse an ambiguous signal to a clean status; we record the ambiguity in the reason code and let the resolver corroborate or the researcher decide.

4. **Tense matters.** Forward-looking news language must never promote a status field. Past/concurrent and historical-with-date language can. Tense classification is a required interpretation step before status output; for `news_v1` it happens inside the Pass 2c prompt rubric and is emitted in `metadata.tense`.

5. **Permit data is the only cross-source corroborator for status.** CoStar and Pipedream are evidence sources for physical attributes and seed data, but their status fields cannot corroborate news status signals because both are stale by design. The single cross-source corroboration path is `news_status_signal × permit_signal`. CoStar status agreement is displayed as supporting context but does not gate the algorithmic flow.

6. **Per-jurisdiction policy decides ambiguous cases.** "Broke ground"-class news language is ambiguous (ceremonial vs. actual). Jurisdictions with verified high-quality permit feeds do not promote alone on ambiguous news; they create a review item recommending stay-current. Jurisdictions without strong permit coverage promote with `confidence=medium` and an explicit "no permit corroboration" reason. New jurisdictions default to the latter; the former is earned operationally.

7. **TCG status taxonomy is preserved as-is.** The granular Conceptual / Proposed / Pending / Approved / Under Construction / Pre-Leasing / Complete / Stalled / Inactive taxonomy stays. The interpreter changes how source signals map *into* the taxonomy; it does not collapse or extend the buckets.

8. **Reduce review load without hiding judgment calls.** Strong deterministic signals auto-apply when the rule is reliable. Ambiguous signals produce one deduplicated review item per project/status target/reason, with a clear system recommendation and supporting evidence attached. Additional articles strengthen the same item instead of creating parallel review work.

9. **Researcher decisions become feedback data.** Every semantic-layer review item records the source text, reason code, system recommendation, researcher decision, final value, and reviewer note when present. The first use of this dataset is rule/prompt/eval improvement and spot-check sampling, not direct model fine-tuning.

10. **Glossary is multi-market by design.** The base TCG rubric (status definitions, product-type definitions, age-restriction definitions, unit-bucket definitions) is jurisdiction-agnostic. Locale-specific entitlement vocabulary (NYC's ULURP, Florida's DRI, Texas's MUD, NY/MA "preliminary plat" vs CA's "tentative map," etc.) lives in per-market glossary addenda layered on top of the base rubric at inference time. The interpreter encounters terminology not in the base or market glossary by emitting a `glossary_gap_observed` signal flag with the unfamiliar phrase and a best-fit canonical mapping at `confidence=low`, never refusing to interpret. Recurring gaps are clustered and surfaced as suggested glossary additions; new-market onboarding ships with a glossary addendum built from a sample of real articles.

11. **Pass 2c is a single-shot classifier, not an agent loop.** The LLM-backed news interpreter receives rich pre-computed input (Pass 2b output, article passages, project state, jurisdiction policy, market glossary) and emits structured output in one round-trip. It does NOT have tools. Cross-source reasoning, contradiction handling, and dynamic lookups are the agent layer's job (AGENT.2 escalation), which consumes Pass 2c output as one of its inputs. Keeping the layers separate keeps each prompt focused and each layer A/B-testable.

---

## 3. Architecture

### 3.1 Output schema

The interpreter emits zero or more `SemanticInterpretation` records per source observation:

```python
@dataclass(frozen=True, slots=True)
class SemanticInterpretation:
    field_name: str                          # canonical TCG field, e.g. "pipeline_status"
    canonical_value: Any | None              # canonical TCG value, or None when only signal flags emitted
    confidence: Literal["low", "medium", "high"]
    reason_code: str                         # finite vocabulary, see §3.3
    signal_flags: dict[str, Any]             # per-field signal flag emissions; values may be bool or scalar (e.g. dates)
    source_anchors: list[PassageExcerpt]     # passage text, offset_start, offset_end, field
    requires_corroboration: bool             # True when resolver may gate on cross-source evidence
    metadata: dict[str, Any]                 # interpreter-specific extras (tense classification, structural-signal echoes)
```

A valid output may have `canonical_value=None` with non-empty `signal_flags`: the interpreter saw something worth recording but did not have enough to assign a canonical value.

### 3.2 Interpreter interface

```python
class SemanticInterpreter(Protocol):
    field_name: str
    source_profile: str

    def interpret(
        self,
        observations: SourceObservations,
        context: InterpreterContext,
    ) -> list[SemanticInterpretation]: ...
```

`SourceObservations` is the per-source canonical input: news interpreters receive the article body, structural signals, and the parsed Pass-2 reference; LADBS interpreters receive permit / inspection / CofO rows; etc. Each source profile defines its own concrete `SourceObservations` shape.

`InterpreterContext` is the **input-context contract** for all interpreters. It provides read-only access to project state, jurisdiction config, market glossary, and recent evidence so interpreters can produce context-aware output. Project state is fetched once per integration call and shared across interpreters, not refetched per field. Concretely, `InterpreterContext` carries:

- `project_id` — for audit linkage.
- `project_state` — current resolved values (especially `pipeline_status`, `total_units`, `developer`, `date_delivery`) so the interpreter can describe its output relative to existing state ("article supports a transition from Approved to U/C" vs "article corroborates current Complete status").
- `jurisdiction_slug` and `jurisdiction_policy` — for the jurisdiction-policy gating logic (see §6).
- `market_glossary` — base TCG rubric merged with the per-market addendum loaded from `config/markets/<slug>/semantic_glossary.yaml`. Composed once per article, passed into the LLM call's system prompt for prose-bearing sources (see §5.13).
- `recent_evidence` — bounded summary of recent active evidence (last 3-5 rows for the relevant fields, with source_type and date) so the interpreter can write reason codes that reflect whether this article is novel or corroborating.
- `source_profile` — for downstream prompt-version pinning.

The contract is identical for LLM-backed interpreters (they pass the context into the prompt) and deterministic interpreters (they consume the context as Python data). LLM-backed interpreters do not call tools — all needed input is precomputed.

### 3.4 Interpretation strategy per source profile

Each source profile chooses LLM-backed or deterministic implementation per principle 1:

| Source profile | Implementation strategy | Rationale |
|---|---|---|
| `news_v1` | LLM-backed (Pass 2c, single-shot Opus 4.7, `interpret_v1` prompt) | Article language has too much variation for phrase tables. |
| `ladbs_v1` | Deterministic (Python rules over permit / inspection / CofO rows) | Source is already structured; rules are stable and auditable. |
| `costar_v1` (future) | Deterministic (Python rules over CoStar columns) | Source is already structured. |
| `pipedream_v1` (future) | Deterministic (Python rules over Pipedream cells) | Source is already structured. |
| `developer_website_v1` (future, if added) | LLM-backed (likely Pass 2c-equivalent) | Web pages are prose. |
| `alert_rss_v1` (future, if added) | LLM-backed (consumes news pipeline) | Alert content is article-shaped. |
| `permit_v1` (future, non-LADBS jurisdictions) | Deterministic per-jurisdiction (rules vary by jurisdiction's permit feed shape) | Source is structured, but rules differ across jurisdictions. |

The reason-code registry, output schema, jurisdiction policy, and resolver corroboration all stay shared. Implementation strategy is a per-profile choice, not a framework choice.

### 3.3 Reason-code registry

Reason codes are a finite vocabulary keyed by `(source_profile, field_name)`. The registry lives at `src/tcg_pipeline/semantic/reason_codes.py` and is loaded into both the resolver and the review-queue rendering layer. Each entry carries a stable code, a human-readable label, a short description suitable for display, and metadata used by the resolver and the spot-check sampler.

Sketch:

```python
@dataclass(frozen=True)
class ReasonCode:
    code: str
    label: str
    description: str
    confidence_default: Literal["low", "medium", "high"]
    promotes_status_alone: bool      # for status field; ignored for others
    requires_corroboration: bool
    review_item_template: str | None  # populated for codes that always create a review item

NEWS_STATUS_REASON_CODES: dict[str, ReasonCode] = {
    "news_topped_out": ReasonCode(...),
    "news_groundbreaking_corroborated_by_permit": ReasonCode(...),
    "news_groundbreaking_unverified_low_quality_permit_jurisdiction": ReasonCode(...),
    "news_status_uncorroborated_high_quality_permit_jurisdiction": ReasonCode(...),
    "news_status_forward_looking_signal_flag_only": ReasonCode(...),
    "news_first_move_ins_complete": ReasonCode(...),
    # ...
}
```

The spot-check sampler dashboard (AGENT.2 sub-sequence step 11) uses reason-code distribution as a primary slice. Researcher review of (e.g.) 10 random `news_groundbreaking_unverified_low_quality_permit_jurisdiction` decisions per week is a targeted hand-grading slice in the §6 eval methodology of `agentic_escalation_design.md`.

A test asserts every reason code referenced from production code is present in the registry, and the registry has no dangling entries.

---

## 4. News status interpreter (`news_v1.pipeline_status`) — Pass 2c

The news status interpreter is implemented as **Pass 2c**: a single-shot Opus 4.7 LLM call downstream of Pass 2b extraction, with the `interpret_v1` prompt. This section is the substantive bulk of step 7's first build. The other field interpreters in §5 are emitted by the same Pass 2c call.

### 4.1 Pass 2c call shape

**Inputs:**
- The article body (or the relevant passages from Pass 2b's `passage_excerpts` if the body is long).
- Pass 2b's structured candidate fields and passage anchors (so the interpreter knows what was extracted and where).
- `InterpreterContext` (per §3.2): project state, jurisdiction policy, market glossary (base + addendum), recent evidence summary.

**System prompt content** (cacheable, ~3-5K tokens):
- The TCG status taxonomy and TCG status-definitions rubric (Conceptual / Proposed / Pending / Approved / Under Construction / Pre-Leasing / Complete / Stalled / Inactive). The §4.3 strong-signal vocabulary, §4.4 ambiguous-signal vocabulary, and §4.5 forward-looking vocabulary are described as prompt-content rubrics here, not as Python phrase tables.
- The reason-code registry filtered to `(news_v1, *)` entries (the model picks reason codes from this finite vocabulary).
- The tense-classification rubric (§4.2 — past/concurrent vs historical-dated vs forward-looking).
- The jurisdiction-policy logic (§6 — when to emit `news_status_uncorroborated_*` vs `news_groundbreaking_unverified_*`).
- The glossary-gap discipline (§5.14 — best-fit + `glossary_gap_observed=true` + reduced confidence rather than refusing).
- The base TCG semantic glossary (jurisdiction-agnostic terms).

**Per-article (non-cacheable) prompt content:**
- The article body / passages.
- Pass 2b's structured candidates with anchors.
- The market glossary addendum (loaded from `config/markets/<slug>/semantic_glossary.yaml` for the article's market).
- Matched project context, including the matched project's jurisdiction policy values (`permit_data_quality`, `news_status_promotion_policy`). The project jurisdiction owns status-promotion policy; article-source jurisdiction is only a fallback for unmatched/new-candidate references.
- The current project state and recent-evidence summary.

**Output:** a JSON array of `SemanticInterpretation` records conforming to the schema in §3.1, enforced via structured-output schema. The model returns one or more interpretations per field domain (status, product_type, age_restriction, date_delivery, unit buckets, tenure, address, name, identifiers, developer-roles, status-nuance), with reason codes drawn from the registry.

**Audit persistence:** each Pass 2c call writes one row to
`news_semantic_interpretations`, linked to the source `news_extractions.id`.
The table stores `output_json`, prompt id/version/hash, parse status,
provider/model, usage, cost, latency, raw response text, diagnostic metadata,
and `created_at`. This intentionally stays separate from `news_extractions`
so extraction and interpretation prompt lineages, A/B runs, and cost traces do
not collapse into one audit surface.

**Model:** Opus 4.7. Temperature 0. Prompt versioning lives at `src/tcg_pipeline/news/prompts/interpret_v1/system.md` and `schema.json`, mirroring the existing `extract_v2` layout. Capability key `semantic.news_v1` for J.1 admin console registration and cost accounting under the shared `news` cost bucket.

**Tools:** none. Pass 2c does not call tools. All required context arrives as input.

### 4.2 Tense classification (prompt rubric, not a preprocessing pass)

Tense classification happens *inside* the Pass 2c call, not as a preprocessing step. The system prompt instructs the model to classify each candidate status signal it identifies into one of three tense categories before assigning a reason code:

- **`past_concurrent`** — actually happened, anchored to a recent date (article publication date or a stated date in the article). Examples: *broke ground*, *construction has begun*, *is underway*, *construction is well underway*, *started construction*, *construction commenced*, *began construction*.
- **`historical_dated`** — actually happened, anchored to an explicit historical date in the article. Examples: *broke ground in spring 2025*, *construction started two years ago*, *broke ground last March*.
- **`forward_looking`** — has not happened yet. Examples: *plans to break ground*, *is expected to break ground*, *will begin construction*, *set to break ground*, *scheduled to start*, *groundbreaking is anticipated*, *expects to break ground*.

The model emits the tense classification in `metadata.tense` of each `SemanticInterpretation`, so the audit trail captures the model's reasoning.

**Disambiguation rule (in-prompt):** bare past-tense status verbs without explicit temporal context default to `past_concurrent` when the article is contemporary news (publication date within 60 days of paste/scrape time); otherwise treat as `historical_dated` if the article carries another parseable date that contextualizes it. Bare past-tense verbs in older articles without any date context still default to `past_concurrent` — better to surface a possibly-stale signal as a review-able status promotion than to silently drop it.

### 4.3 Strong physical signals — promote in every jurisdiction

The system prompt instructs the model that the following signals describe demonstrable vertical construction or delivery and are unambiguous. When the model identifies one in `past_concurrent` or `historical_dated` tense, it emits an interpretation with `canonical_value=Under Construction` (or `Complete` for delivery signals), `confidence=high`, `requires_corroboration=false`, and the corresponding reason code. Strong-signal interpretations bypass jurisdiction-policy gating.

**Strong vertical signals** (status → Under Construction; reason codes per the registry):
- "topped out" / "topping out" / "topped off" → `news_topped_out`, signal flag `topped_out=true`.
- "framing complete" / "framing has been completed" / "wood framing topped out" → `news_framing_complete`, signal flag `framing_complete=true`.
- "concrete pour" / "foundation poured" / "foundation has been poured" / "first concrete pour" → `news_concrete_pour`, signal flag `concrete_pour=true`.
- "halfway through construction" / "more than halfway built" → `news_construction_midpoint`.
- "vertical construction underway" / "vertical construction has begun" → `news_vertical_construction`.

**Strong completion signals** (status → Complete):
- "ribbon cutting" / "ribbon-cutting ceremony" → `news_ribbon_cutting`, signal flag `ribbon_cutting=true`.
- "first residents" / "first move-ins" / "residents have moved in" / "tenants have moved in" → `news_first_move_ins`, signal flag `first_move_ins=true`.
- "officially opened" / "now open" / "opens to residents" → `news_officially_opened`, signal flag `now_open=true`.
- "fully delivered" / "construction is complete" → `news_construction_complete`.

Per the TCG status definitions, "first move-ins" and "first sales completed" both map to Complete. The CofO path remains primary for Complete in jurisdictions with high-quality permit data, but news first-move-ins evidence stands on its own. The model is instructed not to overgeneralize ("residents are excited" is not a first-move-ins signal); the strong vocabulary requires the actual physical milestone language.

The signal flags emitted alongside these reason codes are consumed by AGENT.6 leasing-site detection as immediate-check triggers (per the AGENT.6 row in the roadmap).

### 4.4 Ambiguous early-construction signals — jurisdiction-policy gated

The system prompt instructs the model that the following signals are ambiguous (ceremonial groundbreaking vs actual construction start) and require jurisdiction-policy gating:

- "broke ground" / "groundbreaking ceremony" / "broke ground ceremonially"
- "started construction" / "construction has begun"
- "construction is underway" / "construction is now underway"
- "began construction"

The model is instructed to emit different reason codes based on `jurisdiction_policy.permit_data_quality` from `InterpreterContext`:

- **`permit_data_quality: high`:** emit `canonical_value=Under Construction`, `confidence=medium`, `requires_corroboration=true`, `reason_code=news_status_uncorroborated_high_quality_permit_jurisdiction`. The resolver does NOT promote on this output alone — it creates or updates a single open `news_status_uncorroborated` review item per `(project_id, proposed_status, reason_code)` per §7.3. Three or more independent articles strengthen the recommendation but do not auto-promote.
- **`permit_data_quality: low`:** emit `canonical_value=Under Construction`, `confidence=medium`, `requires_corroboration=false`, `reason_code=news_groundbreaking_unverified_low_quality_permit_jurisdiction`. The resolver promotes; the `resolution_log` row carries the explicit "no permit corroboration" reason.

When permit evidence later arrives in either jurisdiction, the resolver re-runs and the row updates per §7.

Tense gates the ambiguous-signal path: only `past_concurrent` and `historical_dated` produce status output here. `forward_looking` produces signal-flag-only output per §4.5.

### 4.5 Forward-looking signals — signal flag only, no status, no review item by default

The system prompt instructs the model that forward-looking status language never writes `pipeline_status` evidence and does not create a review item by default. Instead, the model emits a `SemanticInterpretation` with `canonical_value=None`, signal flag `groundbreaking_expected_at` populated with the projected date when extractable, and reason code `news_status_forward_looking_signal_flag_only`.

Examples (instructed in prompt content):
- "plans to break ground" / "expects to break ground" → signal flag emission with date if stated.
- "will begin construction in <date>" → signal flag with date populated.
- "scheduled to start construction in <date>" → signal flag with date.
- "groundbreaking is anticipated for <date>" → signal flag with date.

The resolver may use `groundbreaking_expected_at` for delivery-year estimation. The project's `pipeline_status` is unchanged. A follow-up review item is created only if later resolver logic detects a direct contradiction with committed project state (e.g., article says "plans to break ground in 2027" but project is currently resolved as Complete).

### 4.6 Other signal-flag-only phrases — no direct status promotion

The model is instructed that the following phrases are **not** Pre-Leasing or Stalled signals on their own:

- "leasing office is now open" / "now accepting applications" / "leasing has begun" / "pre-leasing has begun" → emits signal flag `news_leasing_marketing_observed=true`, reason code `news_leasing_marketing_signal_flag_only`. Does NOT promote to Pre-Leasing. Pre-Leasing is determined by AGENT.6 leasing-site detection (see roadmap row AGENT.6).
- "stalled" / "halted" / "indefinitely delayed" → emits signal flag `stalled_news_observed=true`, reason code `news_status_stalled_signal_flag_only`. Does NOT promote to Stalled. Phase E.2 auto-stall detection (12+ months no evidence) is the actual mechanism for promoting to Stalled.

---

## 5. Other field interpreters (`news_v1`)

All `news_v1` field interpreters share the same Pass 2c call described in §4.1. The system prompt covers all field domains in one rubric; the Pass 2c response includes interpretations for every field the article supports. There is no separate LLM call per field — one call, multiple `SemanticInterpretation` records out, one per field-and-signal observed in the article.

The sections below describe the prompt-content rubric per field. The prompt material itself lives at `src/tcg_pipeline/news/prompts/interpret_v1/system.md`; this doc is the spec that prompt is built against.

### 5.1 `pipeline_status` — see §4

### 5.2 `product_type`

The current TCG `ProductType` enum is residential-centric: `apartment | condo | townhome | single_family | micro_co_living | other`. Step 7 v1 keeps the enum as-is for the live build. Potential expansion remains a future schema decision; v1 preserves the richer signal as structured flags while writing `OTHER` when the current enum has no safe canonical value.

#### 5.2.1 Existing enum mapping

Prompt-rubric mapping from the news extractor's `candidate_product_type` and article language to the existing `ProductType`:

- "apartment", "apartments", "rental apartment", "apartment building" → `APARTMENT`
- "condo", "condominium", "for-sale apartment" → `CONDO`
- "townhome", "townhouse", "rowhome", "rowhouse" → `TOWNHOME`
- "single-family detached", "single-family home", "detached home" → `SINGLE_FAMILY`
- "micro-unit", "co-living", "shared housing" → `MICRO_CO_LIVING`

Note: `ProductType` and `AgeRestriction` are independent enums per the existing schema. A 55+ apartment building is `(APARTMENT, SENIOR)`, not its own product type.

#### 5.2.2 Deferred enum expansion (future researcher decision)

The current enum collapses several operationally-distinct asset classes into `OTHER`. Forecasting depends on per-class absorption curves and unit economics that differ materially. Step 7 does not expand the enum; it records signal flags so later migration has auditable source data. Possible future additions:

- **`HOTEL`** — hospitality, tracked by `hotel_keys`, fundamentally different from residential.
- **`SENIOR_CARE_INDEPENDENT_LIVING`** (IL) — units are real units; market-rate-like absorption.
- **`SENIOR_CARE_ASSISTED_LIVING`** (AL) — units, but tied to care services.
- **`SENIOR_CARE_MEMORY_CARE`** (MC) — specialized AL for cognitive impairment.
- **`SENIOR_CARE_SKILLED_NURSING`** (SNF) — beds, not units; not directly comparable to apartments.
- **`SENIOR_CARE_CCRC`** — campus combining IL + AL + MC + SNF; needs special handling for unit/bed totals.
- **`STUDENT_HOUSING`** — purpose-built; absorption tied to academic calendar; operationally distinct from market apartments even when `age_restriction=student` is set.
- **`MIXED_USE`** — substantial commercial (retail/office/hotel) component alongside residential; needs a definition threshold (e.g., commercial sf ≥ 25% of total sf).

Until the enum expands, these get mapped to `OTHER` with explicit signal flags so the data isn't lost:
- "assisted living" / "memory care" / "skilled nursing" / "CCRC" / "continuing care retirement community" / "independent living" → `OTHER` + `signal_flags={"care_based_senior": true, "care_subtype": "<AL|MC|SNF|CCRC|IL>"}`.
- "hotel", "hotel keys", "hospitality project" → `OTHER` + `signal_flags={"hotel_observed": true}`.
- "student housing", "purpose-built student housing", "university apartments" (as a product description, not an age-restriction qualifier) → `OTHER` + `signal_flags={"student_housing": true}`.
- "mixed-use" with ≥25% commercial component identifiable → `OTHER` + `signal_flags={"mixed_use_observed": true, "commercial_components": ["retail", "office", "hotel"]}`.

When the enum expansion ships, a one-time migration rewrites existing `OTHER` rows that carry these signal flags into the new enum values. Reason codes for the v1 mappings are stable across the enum-expansion event (the codes describe what the article said, not which enum value got written).

Reason codes: `news_product_type_explicit_apartment`, `news_product_type_explicit_condo`, `news_product_type_explicit_townhome`, `news_product_type_explicit_single_family`, `news_product_type_explicit_micro_co_living`, `news_product_type_care_based_senior` with subtype carried in `signal_flags.care_subtype`, `news_product_type_hotel`, `news_product_type_student_housing`, `news_product_type_mixed_use`, `news_product_type_unmappable`.

### 5.3 `age_restriction`

Prompt-rubric mapping:

- "55+", "62+", "active adult", "age-restricted", "senior community" → `AgeRestriction.SENIOR`
- "student housing", "student community", "university apartments" (used as an age-restriction qualifier, e.g., "leases only to UCLA students") → `AgeRestriction.STUDENT`
- Explicit "no age restriction", "open to all ages", "market-rate housing for all residents" → `AgeRestriction.NON_AGE_RESTRICTED`
- Article silent → no output (do not default to `unknown`; null is the correct unknown signal).

Note: "student housing" appears as both a product-type signal (§5.2.2 — purpose-built student housing as an asset class) and an age-restriction signal (here — leases only to students). The interpreter emits the appropriate signal in both interpreters when the article supports both readings.

Reason codes: `news_age_restriction_explicit_senior`, `news_age_restriction_explicit_student`, `news_age_restriction_explicit_unrestricted`.

### 5.4 `date_delivery`

Three input types from the news extractor:

- **Explicit date** in `candidate_delivery_year_normalized` → emit canonical value, `confidence=high`, reason code `news_delivery_date_explicit`.
- **Vague timing language** in `candidate_delivery_year_text` → normalize per documented projection conventions:

  | Phrase | Normalized date | Confidence |
  |---|---|---|
  | "early <year>" | `<year>-03-15` | medium |
  | "spring <year>" | `<year>-04-15` | medium |
  | "mid-<year>" / "summer <year>" | `<year>-07-15` | medium |
  | "fall <year>" / "autumn <year>" | `<year>-10-15` | medium |
  | "late <year>" / "end of <year>" | `<year>-12-15` | medium |
  | Bare year ("delivery 2027") | `<year>-12-15` | medium |
  | Quarter ("Q3 2027") | quarter midpoint (`<year>-08-15` for Q3) | medium |

  Reason codes: `news_delivery_date_projected_season` with the season carried in `signal_flags.projected_season`, `news_delivery_date_projected_quarter`, `news_delivery_date_projected_year_only`.

- **Forward-looking groundbreaking** ("will start construction Q1 2026") → does not write `date_delivery`; writes a `construction_start_expected_at` signal flag instead. The delivery-year estimator may use it.

Raw text is always anchored in `metadata.raw_delivery_phrase` and `source_anchors` so the projection is auditable.

### 5.5 unit-bucket interpreters (`total_units`, `affordable_units`, `workforce_units`, `market_rate_units`)

Per the existing `workforce_units` decision (E.6 / AGENT.2 step 6), the four unit buckets are independent. The interpreter:

- Emits each bucket only when the article directly states that bucket.
- Does NOT compute market-rate by subtracting affordable from total.
- Does NOT collapse workforce into affordable or market-rate.
- Tags reason codes per bucket: `news_units_total_explicit`, `news_units_affordable_explicit`, `news_units_workforce_explicit`, `news_units_market_rate_explicit`.

If the article mentions a unit-mix split that doesn't sum to total within ±2, the interpreter writes each bucket as observed and emits a `unit_split_arithmetic_mismatch` signal flag for the resolver. The existing `unit_split_mismatch` review flag in `engine.py` consumes this signal.

### 5.6 Tenure interpretation (`rent_or_sale`) — CRITICAL safety

> **Project-wide assumption:** rental and for-sale projects are tracked as **separate projects**, even when they share an address or are part of one master plan. The relationships table (`phase`, `master_plan`, `counterpart`) handles linkage. This decision is recorded in the 2026-05-06 Decision Log entry on tenure-as-separate-projects.

#### 5.6.1 Status of `rent_or_sale` field

`rent_or_sale` is currently a Source-populated direct field (read-only for MVP). Step 7 does **not** promote it to Evidence-derived. The news interpreter records tenure observations as signal flags only until a separate researcher decision promotes the field. A future promotion would be a substantive schema/workflow change requiring researcher sign-off:

- Resolver gains a `rent_or_sale` resolver (most recent explicit-source-stated value wins).
- Contradiction detection covers tenure mismatches.
- Override API allows researcher overrides on tenure.
- Matcher gains tenure as a key matching dimension (rental and for-sale projects at the same address are treated as separate projects unless an explicit `counterpart` relationship exists).

Until the promotion ships, the news interpreter emits tenure observations as signal flags, but does not write `rent_or_sale` evidence rows.

#### 5.6.2 CRITICAL safety rule: never default unstated tenure

The single most important rule in tenure interpretation: **if the article does not explicitly state tenure, do NOT default to for-sale.**

The motivating failure mode: a news article saying "this 80-unit townhome project will deliver in 2027" without explicit tenure language. The traditional default in some markets is to assume "townhome" = for-sale, but the SFR/BTR (single-family-rental / build-to-rent / build-for-rent) asset class has grown materially since 2020 and townhome rental communities are common. Silently classifying an SFR townhome project as for-sale would be a real and recurring error that contaminates downstream forecasting.

When tenure is unstated, the interpreter:
- Emits `signal_flags={"tenure_unknown": true}` with reason `news_tenure_unstated_no_default`.
- Does NOT write `rent_or_sale` evidence (regardless of whether `rent_or_sale` is Evidence-derived yet).
- Does NOT preserve any existing tenure assumption from the source profile or jurisdiction config.
- The matcher treats tenure-unstated articles as ambiguous and applies extra scrutiny when matching to existing rental-only or for-sale-only projects (does not auto-merge).

Reason code: `news_tenure_unstated_no_default`.

#### 5.6.3 Strong rental indicators

The interpreter emits `rent_or_sale=rental` (or signal flag `tenure=rental` until field promotion) when ANY of these phrases appear:

- "rental community", "rental townhomes", "rental homes", "rental single-family", "for-rent townhomes", "for-rent homes"
- "build-to-rent" (BTR), "build-for-rent" (BFR), "single-family rental" (SFR)
- "monthly rent of $X", "rents starting at $X", "rents from $X", "rent ranges from $X to $Y"
- "leasing", "lease-up", "leasing office", "rental community managed by X"
- Operator language: "X manages the property as a rental community", "X will operate the project as a rental"

Reason code: `news_tenure_explicit_rental`.

#### 5.6.4 Strong for-sale indicators

The interpreter emits `rent_or_sale=for_sale` (or signal flag `tenure=for_sale` until field promotion) when ANY of these phrases appear:

- "for-sale", "for sale", "homes for sale"
- "homebuyers", "buyers", "homeownership", "first-time buyers"
- "sales office", "sales gallery", "model home tour", "model homes"
- "first sales completed", "closings", "first closings"
- "starting at $<price>" where context indicates home prices, not rents (price magnitude + duration cues — "$850K homes" vs "$3,500/month")

Reason code: `news_tenure_explicit_for_sale`.

#### 5.6.5 SFR / BTR / townhome-rental detection

Compound product-type + tenure cases need explicit handling because they are the highest-risk for silent misclassification. **This section addresses the user-flagged critical failure mode.**

Detection rules:

- "single-family rental community", "SFR community", "BTR community", "build-to-rent community" → `(product_type=SINGLE_FAMILY, rent_or_sale=rental)` + signal flag `sfr_btr=true`. Reason: `news_tenure_sfr_btr_explicit`.
- "rental townhomes", "townhome rental community", "for-rent townhomes", "townhomes for lease" → `(product_type=TOWNHOME, rent_or_sale=rental)` + signal flag `townhome_rental=true`. Reason: `news_tenure_townhome_rental_explicit`.
- "townhome project" / "townhome development" / "townhome community" with NO explicit tenure language → product_type=TOWNHOME emitted, tenure unstated per §5.6.2. The interpreter MUST NOT silently classify as for-sale.
- "single-family project" / "single-family development" with NO explicit tenure language → same: product_type=SINGLE_FAMILY emitted, tenure unstated.

The display layer (Project Detail, Pipeline list, exports) renders these as "Single-family rental" / "Townhome rental" when both fields resolve compatibly. No new ProductType enum is added for the SFR/townhome-rental cases — see open question on `asset_class` field in §12.

Researcher-facing display rule: anywhere `product_type` is shown alongside `rent_or_sale`, the UI uses the compound label ("Single-family rental", "Townhome rental", "Apartment rental") so the rental-vs-for-sale distinction is visually unambiguous.

#### 5.6.6 Mixed-tenure projects

Articles describing mixed-tenure projects ("a 200-unit project with 150 rental apartments and 50 for-sale condos") need to be detected but NOT silently merged into one project:

- The interpreter emits `signal_flags={"tenure_split_observed": true, "tenure_split_breakdown": {"rental_units": 150, "for_sale_units": 50, "rental_product_type": "apartment", "for_sale_product_type": "condo"}}`.
- Does NOT write `total_units` or other unit-bucket evidence for the article; the buckets are ambiguous when split across two projects.
- Matcher behavior:
  - If both linked projects already exist (linked via `counterpart` or `phase` relationship), write the appropriate component evidence to each project.
  - If one component exists and the other is missing, create a `multi_tenure_review` item with a strong suggested action to create the missing counterpart project. Example: if only the rental apartment project exists and the article states "150 rental apartments and 50 for-sale condos," the review item proposes a new for-sale condo project prefilled with the observed unit count, product type, tenure signal, address/name anchors, and relationship back to the rental project.
  - If neither component can be matched confidently, create a `multi_tenure_review` item asking the researcher to split/create the appropriate linked projects from the article.

The `multi_tenure_review` review-item type is new; additive enum migration in step 7.

Reason code: `news_tenure_mixed_split_observed`.

### 5.7 Vague location language → coordinates

News articles routinely describe sites in ways the current Geocodio/Esri pipeline can't parse:

- **Intersection language:** "the corner of Main Street and 5th Avenue", "at the intersection of Pico and Sepulveda", "the southeast corner of X and Y".
- **Landmark-relative language:** "across from Union Station", "next to the LA Convention Center", "the former Sears building", "the parking lot adjacent to the courthouse".
- **Block-level language:** "the 800 block of Wilshire Boulevard", "Wilshire near La Brea".

The interpreter handles these in two tiers.

#### 5.7.1 Intersection language (v1 — synthesizable)

Detect intersection patterns ("corner of A and B", "intersection of A and B", "A and B" when both are street names) and synthesize a geocodable query in the form `intersection of A and B, <city>, <state>`. Emit:

- `candidate_address` populated with the synthesized query string.
- `signal_flags={"candidate_address_imprecise": true, "address_form": "intersection"}`.
- `confidence=medium` on any address-derived match.

The downstream geocoder (Geocodio + Esri fallback, already shipped) typically handles intersection queries with reduced confidence. The matcher consumes `candidate_address_imprecise=true` as a hint to weight name + developer + city more heavily than address proximity for ambiguous cases.

Reason code: `news_address_intersection_synthesized`.

#### 5.7.2 Landmark-relative + block-level language (v2 — deferred)

Landmark-relative resolution requires either a per-market landmark gazetteer (config table) or LLM-assisted resolution against a known-place index. Both are non-trivial. v1 emits `signal_flags={"location_landmark_relative": true, "landmark_text": "<raw phrase>"}` so the data isn't lost, but does not attempt to resolve coordinates. v2 ships once a clear approach is chosen.

Block-level language ("800 block of Wilshire") can be partially resolved by synthesizing a representative address ("850 Wilshire Boulevard, Los Angeles, CA") and tagging `address_form=block_level`. v1 handles this similarly to intersection language with `confidence=medium`.

Reason codes (deferred): `news_address_landmark_relative_unresolved`, `news_address_block_level_synthesized`.

### 5.8 Project name cleaning + alias detection

The Pass-2 extractor emits `candidate_name`, but the interpreter cleans it before evidence writes:

- Strip generic suffixes: "the project", "the development", "the proposed development", "the planned community".
- Normalize capitalization (Title Case for proper nouns; preserve known stylizations like "ARBOR" or "iO" if explicitly stated).
- Strip location parentheticals already covered elsewhere: "Helio (Los Angeles)" → `Helio` (city already in `candidate_address`).

Detect aliases mentioned in the same article:

- "the project, formerly known as Sunset Palladium, is now branded as Helio" → emit primary `candidate_name=Helio`, signal flag `previous_names=["Sunset Palladium"]`.
- "Helio (formerly the Crossroads Hollywood project)" → same shape.
- These flow into `Project.previous_names` so the matcher can use them.

Reason codes: `news_project_name_extracted`, `news_project_name_aliases_detected`.

### 5.9 Identifier extraction

Articles sometimes mention specific identifiers:

- LA case numbers: `ENV-2023-00045-EIR`, `CPC-2024-1234-CU`, `VTT-12345`.
- LADBS permit numbers: `BLD-2026-08332`, `26010-10000-12345`.
- ZIMAS / PDIS numeric IDs.
- Address-as-identifier: "the property at 1234 Sunset Boulevard".

The interpreter applies regex-per-format to extract candidate identifiers and emits them via `signal_flags={"candidate_identifiers": [{"kind": "la_case_number", "value": "ENV-2023-00045-EIR", "anchor": <PassageExcerpt>}, ...]}`. The matcher consumes these as additional matching dimensions; the resolver may write them to `project_identifiers` after researcher review.

Reason code: `news_identifier_extracted`.

### 5.10 Developer role disambiguation

Articles often mention multiple parties with distinct roles:

- "developer", "lead developer".
- "owner", "landowner", "ground lessor".
- "equity partner", "joint venture partner", "investor".
- "lender", "financing provider".
- "general contractor" (GC), "construction manager".
- "architect", "design architect".
- "property manager", "operator".

Today the news interpreter writes whichever entity it found first to `candidate_developer`, sometimes capturing an architect or a JV partner instead of the actual developer. Phase A's developer-canonicalization apply step had to add per-row exceptions for architecture firms incorrectly normalized as developers — this is the upstream cause.

The interpreter:

- Detects role-tagged mentions via phrase patterns ("developed by X", "X is developing", "owned by Y", "X, the project's architect", "designed by Z", "X serves as the GC").
- Emits `candidate_developer` only for the developer-role entity. Falls back to highest-confidence single mention when no role is explicit, but tags with `developer_role_uncertain=true`.
- Emits separate signal flags for non-developer roles: `landowner_observed`, `equity_partner_observed`, `architect_observed`, `gc_observed`, `operator_observed`, with the entity name as the value.
- These signal flags do NOT write evidence today (corresponding fields like `architect`, `owner`, `true_owner` are Source-populated direct), but they preserve the data for future field-class promotion and provide context to the review-queue card.

Reason codes: `news_developer_explicit_role`, `news_developer_inferred_no_explicit_role`, `news_role_landowner`, `news_role_architect`, `news_role_gc`, `news_role_operator`, `news_role_equity_partner`.

### 5.11 Status nuance — delayed / cancelled / abandoned

The TCG taxonomy has `Stalled` and `Inactive` but news uses several phrases that need careful disambiguation:

- **"Delayed by X months"** → does NOT change status. Emits `signal_flags={"delivery_date_slip_observed": true, "slip_months": <int>, "new_projected_delivery": <date|null>}`. Resolver may use the slip signal to update delivery-date estimates; does not promote to Stalled.
- **"Indefinitely delayed" / "halted" / "paused" / "on hold"** → does NOT change status directly. Emits `signal_flags={"stalled_news_observed": true}`. Phase E.2 auto-stall detection (12+ months no evidence) is the actual mechanism for promoting to Stalled. The signal flag is informational and surfaced on the project detail page.
- **"Cancelled" / "scrapped" / "killed by developer" / "dead" / "the project is no longer moving forward"** → emits `signal_flags={"cancelled_news_observed": true}` AND creates a high-priority `project_cancellation_review` review item proposing `pipeline_status=Inactive`. Cancellation is significant and uncommon enough to warrant explicit researcher confirmation. News is frequently the *only* source that captures cancellation; permits won't tell you a project was killed.

Reason codes: `news_status_delivery_slip`, `news_status_stalled_signal_flag_only`, `news_status_cancellation_review_required`.

The `project_cancellation_review` review-item type is new; additive enum migration in step 7 (alongside the `news_status_uncorroborated` and `multi_tenure_review` enum value additions).

### 5.12 Future-scope fields (interpreter is forward-compatible)

The following fields are currently Source-populated direct (read-only for MVP) and not in v1 interpreter scope. The interpreter framework is forward-compatible — when these fields graduate to Evidence-derived, new field interpreters slot in without framework changes. Reason codes are pre-defined now to avoid registry rework later.

- **`stories`** — "32-story tower", "20-story residential building". Reason: `news_stories_explicit`.
- **`retail_sf` / `office_sf` / `hotel_keys` / `total_sf`** — "1.5 million square feet of office", "200 hotel keys", "50,000 square feet of ground-floor retail". Reasons: `news_retail_sf_explicit`, `news_office_sf_explicit`, `news_hotel_keys_explicit`, `news_total_sf_explicit`.
- **`affordable_type`** — "100% affordable LIHTC project", "ED1 streamlined", "TOC bonus", "density bonus project". Currently emitted as signal flags (`lihtc_observed`, `ed1_observed`, `toc_observed`, `density_bonus_observed`) with reasons `news_affordable_type_lihtc_observed`, `news_affordable_type_ed1_observed`, `news_affordable_type_toc_observed`, and `news_affordable_type_density_bonus_observed`; becomes canonical when `affordable_type` graduates.
- **`entitlement_type` / `appeal_status` / `ceqa_status`** — "Draft EIR released", "Final EIR certified", "EIR challenged in court", "appeal denied". Reasons reserved: `news_ceqa_status_draft_eir_released`, `news_ceqa_status_final_eir_certified`, `news_ceqa_status_exemption_observed`, `news_appeal_status_filed`, `news_appeal_status_denied`, and `news_appeal_status_challenge_observed`. CEQA milestone interpretation interacts with `pipeline_status` per the TCG status definitions ("Draft EIR Submitted" → Pending; "Environmental Review Completed (full EIR)" → Approved); a follow-on design covers operationalizing it. Tracked as an open question in §12.

Until graduation, the interpreter emits these as signal flags only; no evidence rows are written.

### 5.13 Multi-market glossary structure

The base TCG semantic glossary (in the Pass 2c system prompt) is **jurisdiction-agnostic**. It carries the universally applicable vocabulary: physical construction milestones, tenure language, age-restriction language, unit-bucket language, delivery-timing conventions. These mean the same thing across markets.

**Locale-specific entitlement vocabulary** lives in **per-market addenda** loaded at inference time. Storage shape:

```
config/markets/<slug>/semantic_glossary.yaml
```

Schema (proposed; refine during implementation):

```yaml
slug: new_york_city
notes: |
  NYC uses ULURP for major land-use approvals. "ULURP certification" is the
  city-planning equivalent of CA's "Tentative Map Approved" stage.

status_phrases:
  - phrase: "ULURP certification"
    tcg_status: "Pending"
    reason_code_extension: "news_status_ulurp_certification"
    confidence_default: "high"
    promotes_status_alone: true
    notes: "NYC public review process certification step."
  - phrase: "City Council approved"
    tcg_status: "Pending"
    reason_code_extension: "news_status_city_council_approved"
    confidence_default: "high"
    promotes_status_alone: true
    notes: "NYC Council action on ULURP applications."
  - phrase: "TCO"
    tcg_status: "Complete"
    reason_code_extension: "news_status_tco_issued"
    confidence_default: "high"
    promotes_status_alone: true
    notes: |
      NYC Temporary Certificate of Occupancy. Issued before final CofO; common
      in NYC because buildings often deliver before all minor punch-list items
      are resolved.

product_type_phrases: []
age_restriction_phrases: []
delivery_timing_phrases: []
unit_bucket_phrases: []
tenure_phrases: []
identifier_patterns: []
```

Per-entry reason-code metadata overrides are optional. When omitted,
`confidence_default` defaults to `medium`, `promotes_status_alone` defaults to
`false`, `requires_corroboration` defaults to `false`, and `signal_only`
defaults from the target field. `promotes_status_alone: true` is valid only for
`pipeline_status` entries. This lets deterministic local-government terms such
as `ULURP certification` carry the same authority as base status reason codes
without requiring a base-registry edit for every market.

**Composition at inference time:**

1. The base TCG rubric is loaded from `src/tcg_pipeline/news/prompts/interpret_v1/system.md` (cacheable, ~3-5K tokens).
2. The per-market glossary YAML for the article's market is loaded from `config/markets/<slug>/semantic_glossary.yaml`.
3. The two are concatenated into the Pass 2c system prompt (base first, addendum after, with a clear "MARKET-SPECIFIC TERMINOLOGY FOLLOWS" delimiter).
4. The reason-code registry is dynamically extended with `reason_code_extension` entries from the addendum, registered against `(news_v1, <field_name>)` so the registry remains complete and validated at startup.

**The base glossary is the default.** A market with no addendum file simply doesn't extend the base — the interpreter still works, the gap-detection signals (§5.14) just fire more often.

**Addendum changes are version-controlled.** Each `semantic_glossary.yaml` is committed to git; updates ship with normal CI/CD. An admin UI for managing addenda live without deploys is future scope (Phase J / J.1).

### 5.14 Glossary gap detection — best-fit + flag, never refuse

The interpreter handles unfamiliar terminology with three complementary mechanisms.

#### 5.14.1 Make-best-fit + `glossary_gap_observed` flag

The Pass 2c system prompt instructs the model:

> When you encounter status / product-type / age-restriction / tenure / delivery-timing terminology not explicitly covered in the base TCG glossary or this market's addendum, attempt the closest semantic mapping based on the surrounding article context (what the article is actually describing). Assign `confidence=low`. Emit the canonical value if you can defend it from context; emit only signal flags if you cannot. Always emit `signal_flags={"glossary_gap_observed": true, "unfamiliar_phrase": "<the literal phrase>", "best_fit_reasoning": "<one sentence on why you mapped it this way>"}`. Never refuse to interpret.

Modern frontier models do this well when given clear instructions and surrounding context. The output is honest: a best-fit canonical value with low confidence, a recorded unfamiliar phrase, and the model's reasoning. Researchers can spot-check or override.

#### 5.14.2 `*_unmappable` reason codes for non-recoverable cases

When the model genuinely cannot map a phrase even with best-effort reasoning, it emits the appropriate `*_unmappable` reason code. The registry includes one per field domain that has interpretation:

- `news_status_unmappable`
- `news_product_type_unmappable` (already shipped in foundation commit)
- `news_age_restriction_unmappable`
- `news_delivery_date_unmappable`
- `news_tenure_unmappable`

`*_unmappable` interpretations write `canonical_value=None` but preserve the phrase + passage anchor in `signal_flags={"unmappable_phrase": "<phrase>", "context_summary": "<one sentence>"}`. The integrator surfaces them to the researcher via a "glossary attention" review-queue tab.

#### 5.14.3 Per-market reason-code distribution monitoring

The spot-check sampler dashboard (AGENT.2 sub-sequence step 11) gets a market-slicing dimension. Per-market metrics:

- `% of interpretations with confidence=low` per field domain.
- `% of interpretations with glossary_gap_observed=true` per field domain.
- `% of interpretations with reason_code ending in _unmappable`.
- Top-N most-frequent unfamiliar phrases (clustered by similarity) per market.
- Reviewer rejection rate per market vs the global baseline.

When any of these meaningfully exceeds the established-markets baseline (thresholds set during implementation; defaults: gap rate > 15% per field, unmappable rate > 5% per field, rejection rate > established-markets baseline + 2σ), an alert fires in the Coverage / admin UI: *"Boston market has 18% unmappable status interpretations across 47 articles — glossary attention needed."* The researcher reviews the clustered unfamiliar phrases, drafts canonical mappings for the market addendum YAML, and ships the update through normal git/deploy flow. The alert clears once metrics return to baseline.

### 5.15 New-market glossary onboarding

Phase I.1 (new-market onboarding) gains a required step: build the per-market semantic glossary addendum before the market goes live for news scraping.

Process:

1. **Collect a representative article sample** for the new market: 50-100 articles spanning multiple publications, status milestones, project sizes, and submarkets. Source candidates are the same outlets the market will use post-launch (e.g., for a new market, the local Urbanize-equivalent + general news sources covering real estate).
2. **Run the sample through the existing `interpret_v1` prompt** with no market addendum. Capture the interpreter outputs.
3. **Review for misses:** filter to interpretations with `glossary_gap_observed=true`, `confidence=low`, or `*_unmappable` reason codes. Cluster recurring unfamiliar phrases.
4. **Draft canonical mappings** for the recurring phrases. For each, record: phrase, proposed TCG canonical mapping, reason code extension, notes explaining the locale-specific meaning.
5. **Add to `config/markets/<slug>/semantic_glossary.yaml`.** Commit + deploy.
6. **Re-run the sample** and confirm gap-rate metrics drop below the alert thresholds (per §5.14.3).
7. **Iterate** if needed: a second pass usually catches edge cases the first pass missed.
8. **Ship the market live** for news scraping with the validated glossary in place.

The onboarding artifact is the per-market YAML file plus an `onboarding_evaluation.md` document under the same market directory recording: sample article URLs, gap rate before/after addendum, top unfamiliar phrases addressed, residual gaps accepted (and why), and researcher sign-off.

Post-launch, the §5.14.3 monitoring catches any gaps the onboarding sample missed, and the per-market YAML accretes addenda over time as new terminology surfaces.

---

## 6. Jurisdiction policy

### 6.1 Schema

Jurisdiction policy lives in YAML config at `config/jurisdictions/<slug>.yaml`. Keeping it in YAML rather than the `jurisdictions` table avoids a config-table schema migration and makes policy changes reviewable in version control.

```yaml
# config/jurisdictions/city_of_los_angeles.yaml
slug: city_of_los_angeles
permit_data_quality: high  # one of {low, high}
news_status_promotion_policy: wait_for_permit_corroboration
permit_data_quality_validated_at: "2026-05-06"
permit_data_quality_notes: |
  LADBS Socrata feeds covering permits / inspections / CofO with daily refresh,
  ≤7-day inspection lag, and stable project IDs. Validated over 2026-04 through 2026-05.
```

```yaml
# config/jurisdictions/city_of_santa_monica.yaml
slug: city_of_santa_monica
permit_data_quality: low
news_status_promotion_policy: auto_promote_unverified
permit_data_quality_notes: |
  SM Active Permits Socrata feed exists but coverage is uneven; PDF-based Active
  Development Tracking is the actual gold standard but is monthly cadence. Treat
  as low until a daily structured feed with reliable inspection data ships.
```

The two policies are:

- **`wait_for_permit_corroboration`** (used when `permit_data_quality: high`). Ambiguous news status signals do NOT promote alone. Resolver creates a `news_status_uncorroborated` review item.
- **`auto_promote_unverified`** (used when `permit_data_quality: low`). Ambiguous news status signals promote with `confidence=medium` and an explicit "no permit corroboration" reason.

### 6.2 Defaults

- New jurisdictions default to `permit_data_quality: low`, `news_status_promotion_policy: auto_promote_unverified` when no explicit policy file exists. The new-market onboarding checklist (Phase I.1) requires explicit acknowledgement of the default and should add a policy file before launch.
- Missing jurisdiction config file → loader returns the low-quality default and marks the prompt payload with `policy_source: default`.

### 6.3 Upgrade criteria (low → high)

A jurisdiction earns `permit_data_quality: high` when ALL of the following are true for at least three consecutive months of operation:

1. Permit / inspection / CofO feeds run at daily-or-better cadence.
2. Observed inspection lag (filing date → feed appearance) is ≤14 days for ≥90% of records.
3. Project / permit identifiers are stable across feed runs.
4. No ongoing source-health alerts on the relevant `source_runs` rows.

The flip is an explicit operator decision (config edit + commit + deploy), not auto-detection. Operators may use the Coverage source-health panel and the Phase D.M / Phase J.2 ledger to validate the criteria.

When a jurisdiction is upgraded, existing `news_groundbreaking_unverified_low_quality_permit_jurisdiction` evidence rows are NOT rewritten — they stay with their original reason code as audit history. New news signals after the flip use the stricter posture.

Downgrade (high → low) is permitted but should be rare and accompanied by an operator note in `permit_data_quality_notes`.

---

## 7. Resolver corroboration

The resolver consumes `SemanticInterpretation` outputs the same way it consumes existing evidence rows, with two behaviors specific to the news status path.

### 7.1 Permit-driven promotion (status)

When a permit-source `pipeline_status` resolution promotes a project to U/C / Complete AND the project has news evidence with the same canonical value within a recent window (180 days), the resolver writes a single `resolution_log` row citing the permit as primary and the news as supporting:

```json
{
  "field": "pipeline_status",
  "value": "Under Construction",
  "confidence": "high",
  "rule_applied": "permit_inspection_vertical_trade",
  "evidence_ids": ["<permit_evidence_id>", "<news_evidence_id>"],
  "metadata": {
    "primary_source_type": "ladbs_inspection",
    "supporting_source_types": ["news_article"]
  }
}
```

The permit alone would have produced the same canonical value; the news anchor improves review-queue rendering and the Resolution tab.

### 7.2 News-driven promotion under low-quality jurisdiction

When the news interpreter produces `requires_corroboration=false, confidence=medium` (i.e., `low` permit-data jurisdiction), the resolver writes the U/C status with `confidence=medium`. If permit evidence later arrives, the resolver re-runs and the row updates honestly:

```json
{
  "field": "pipeline_status",
  "value": "Under Construction",
  "confidence": "high",
  "rule_applied": "permit_inspection_vertical_trade_with_prior_news_promotion",
  "evidence_ids": ["<permit_evidence_id>", "<news_evidence_id>"],
  "metadata": {
    "previously_unverified_news_promotion": true,
    "news_promotion_at": "2026-04-15T...",
    "permit_corroboration_at": "2026-04-22T..."
  }
}
```

The Resolution tab UI displays both sources and the upgrade timeline.

### 7.3 News-driven review item under high-quality jurisdiction

When the news interpreter produces `requires_corroboration=true` (`high` permit-data jurisdiction), the resolver does NOT write a new `pipeline_status` resolution_log row. It creates a review item with payload:

```json
{
  "review_item_type": "news_status_uncorroborated",
  "field_name": "pipeline_status",
  "current_value": "Approved",
  "proposed_value": "Under Construction",
  "recommendation": "keep_current",
  "recommendation_text": "Article reports project is under construction; this jurisdiction has high-quality permit feeds and no permit corroboration was found. Suggest keeping at Approved unless you have outside knowledge.",
  "evidence_ids": ["<news_evidence_id>"],
  "reason_code": "news_status_uncorroborated_high_quality_permit_jurisdiction",
  "system_recommendation_strength": "keep_current",
  "independent_news_article_count": 1,
  "supporting_context": {
    "costar_or_pipedream_status_agrees": false,
    "note": "CoStar/Pipedream status may be displayed for context but does not count as status corroboration."
  }
}
```

The review-queue card uses the existing decision-card UI shape with one specialization: a default-recommendation banner ("System suggests: keep Approved"). Decision keys are unchanged (`a` accept-new, `s` keep-current, `d` defer, `f` custom).

Open-item dedupe rule: only one open `news_status_uncorroborated` item exists per project/proposed status/reason code. Later independent articles with the same signal append their evidence IDs to the item and increment `independent_news_article_count`. When the count reaches three or more, the item remains non-auto-promoting but the system recommendation strength changes to `researcher_review_recommended` and the priority/copy may be upgraded so the researcher sees that multiple sources now support the same uncorroborated status signal.

If permit evidence arrives later that promotes the project to U/C, the resolver invalidates the open `news_status_uncorroborated` item (it is now consistent with the resolved status) and surfaces the news evidence as a supporting row on the new permit-driven resolution. The audit trail records that the review item was superseded by permit corroboration rather than by researcher action.

A new value `news_status_uncorroborated` is added to the `ReviewItemType` enum. Migration is additive (new enum value), conservative per the AGENT.reset config-table preservation rule.

### 7.4 Researcher feedback dataset

Semantic-layer review items are not just operational queue entries; they are labeled feedback data for improving the rules and later agent layers. Every semantic-layer review item must preserve:

- source text / source anchors that triggered the interpretation,
- reason code,
- system recommendation and recommendation strength,
- proposed value and current value,
- researcher decision,
- final value after the decision,
- reviewer note when present.

The first use is not model fine-tuning. The first use is targeted rule updates, prompt improvements, spot-check sampling, and eval slices by reason code. Future agent layers may consume this dataset as training/evaluation material once enough labeled decisions exist.

---

## 8. Source profiles

Each source profile declares its semantic interpreter set in a `SemanticInterpreterProfile`. Step 7 v1 ships:

- **`news_v1`** — interpreters for all five field domains per §4-5. Most behavior described in this doc.
- **`ladbs_v1`** — port of existing LADBS adapter logic into the interpreter framework. No behavior change in v1; the port establishes the framework's per-source extension pattern. Interpreters: `pipeline_status` (existing permit/inspection/CofO rules), `date_delivery` (existing CofO date mapping). No `product_type`, `age_restriction`, or unit-bucket interpretation — LADBS doesn't carry those.

Future profiles (`costar_v1`, `pipedream_v1`, `permit_v1` for non-LADBS jurisdictions) ship with their own AGENT roadmap items (AGENT.4 / AGENT.5 / AGENT.3).

---

## 9. Pre-Leasing detection — out of scope

Per §1, Pre-Leasing detection via leasing-site discovery is roadmap row AGENT.6 and is not part of step 7. Step 7's news interpreter does, however, emit the trigger signal flags AGENT.6 will consume:

- `topped_out=true`
- `framing_complete=true`
- `concrete_pour=true`
- `first_move_ins=true`
- `ribbon_cutting=true`
- `now_open=true`

These flags are written to the news evidence row's `metadata` and are consumable by AGENT.6's leasing-site sweep job's trigger handler. Step 7 does not need to import or know about AGENT.6 internals; the signal-flag contract is the integration point.

---

## 10. Migration / cutover

Step 7 ships in a single coherent build (per the 2026-05-06 decision). Sequence:

1. **Add the `semantic` package skeleton** — `src/tcg_pipeline/semantic/` with output schema, interpreter protocol, reason-code registry. No behavior change; existing code paths unaffected.
2. **Port LADBS first** into the framework. Gold-test against the existing LA dataset to confirm zero behavior change. This validates the framework before adding new logic.
3. **Build the `news_v1` Pass 2c LLM call** per §4-5. Implementation is a single-shot Opus 4.7 call with the `interpret_v1` prompt (system prompt assembled from base TCG rubric + per-market addendum + reason-code registry filtered to `(news_v1, *)`), structured-output schema enforcing the `SemanticInterpretation` array shape, temperature 0, and capability key `semantic.news_v1` tracked separately under the shared `news` cost bucket. Prompt files live at `src/tcg_pipeline/news/prompts/interpret_v1/system.md` and `schema.json`, mirroring the existing `extract_v2` layout. The Pass 2c module lives at `src/tcg_pipeline/semantic/news/pass2c.py`. Each call persists one audit row to `news_semantic_interpretations`, linked to the source extraction, before its outputs are converted into evidence/review flow inputs. Behind a `news_use_legacy_semantic` settings flag (default `false` in code, `true` on Render until controlled smoke). When the flag is `true`, the news integrator uses legacy interpretation paths; when `false`, it routes through Pass 2c. Pass 2c calls do not have tools — all required input arrives in `InterpreterContext`. The existing AGENT.1 multi-provider LLM abstraction is reused so model swap (Opus → Haiku, etc.) is a config change, not a code change.
4. **Wire the resolver corroboration step** per §7. Three additive `ReviewItemType` enum values are added in this step: `news_status_uncorroborated` (§7.3), `multi_tenure_review` (§5.6.6), and `project_cancellation_review` (§5.11). All three are additive migrations; no existing review-item rows are rewritten.
5. **Add jurisdiction config files** for LA City (`high`) and any other current jurisdictions (`low`). Document the operational data that earned LA City `high`.
6. **Run the smoke suite end-to-end:** the D.6 5-article fixture set + paste-link smokes with explicit cases:
   - `topped_out` strong signal → expect U/C, high confidence, regardless of jurisdiction.
   - `broke_ground` ambiguous + LA (`high` jurisdiction) → expect `news_status_uncorroborated` review item, no status promotion.
   - Three independent `broke_ground` ambiguous LA articles → expect the same deduplicated review item with all evidence attached and upgraded recommendation strength, still no auto-promotion.
   - `broke_ground` ambiguous + a hypothetical Santa Monica `low` jurisdiction → expect U/C promotion with `confidence=medium`, no review item.
   - Forward-looking "plans to break ground" → expect signal flag emission, no status change.
   - `first_move_ins` → expect Complete promotion + `first_move_ins=true` signal flag (consumable by AGENT.6 once it ships).
   - Mixed-tenure article with one existing component → expect `multi_tenure_review` with a prefilled suggested missing counterpart project, not merged unit evidence on the existing project.

   Confirm each produces the expected interpreter output, reason code, and resolution / review-item behavior.
7. **Flip `news_use_legacy_semantic` off** on Render after smoke success. Render kill switches remain available as fast disable.
8. **Move legacy code paths** in news integration to `news/extraction_legacy.py` per AGENT.2 sub-sequence step 8 (separate ship).

The semantic-layer cutover is independent of the AGENT.reset event. AGENT.reset's truncate-and-reseed simplifies because all evidence rows after the reset will be written under the final semantic-layer logic.

---

## 11. Testing

Testing for the LLM-backed Pass 2c interpreter follows the same pattern as the existing `extract_v2` test/A-B/eval pipeline. The deterministic LADBS port has its own simpler unit-test pattern.

- **Reason-code registry tests** (already shipped in foundation commit; extend): registry validates with no duplicates or dangling codes; every reason code referenced from production code is present; every `review_item_template` value matches a known template in the allowlist; per-market `reason_code_extension` entries register cleanly against `(news_v1, <field_name>)` without colliding with base codes.
- **Prompt assembly tests:** given a base prompt + per-market YAML, the assembled system prompt contains the expected sections, the market addendum follows the base, and the reason-code registry includes the market extensions.
- **`InterpreterContext` plumbing tests:** verify `project_state`, `jurisdiction_policy`, `recent_evidence`, and `market_glossary` are populated correctly before Pass 2c is invoked. No tools are wired; verify that.
- **Fixture-driven prompt-eval tests:** golden-article fixtures (drawn from the D.6 smoke set + accumulating production paste-link articles) with expected `(field_name, canonical_value, confidence, reason_code, signal_flags)` outputs. These run against a live LLM at acceptance time and against a mock/recorded-response client in CI. New fixtures are added as researcher feedback surfaces edge cases.
- **A/B harness slice for `interpret_v1`:** reuse the existing `tcg-pipeline news ab-extract` harness pattern, scoped to interpretation. Each prompt version (`interpret_v1`, `interpret_v2`, ...) runs against the same fixture set; metrics tracked include reason-code distribution, confidence distribution, gap rate, unmappable rate, and reviewer-acceptance rate when historical reviewer-decision data is available. Promote prompt versions when measured quality improves, same gating as `extract_v2`.
- **Jurisdiction-policy tests:** the same fixture article processed under `high` and `low` jurisdictions produces the expected divergent outputs (review item vs auto-promote with medium confidence).
- **Multi-market glossary tests:** an article from a market with an addendum produces the expected per-market reason codes (e.g., `news_status_ulurp_certification` for an NYC article); an article from a market without an addendum still interprets cleanly (gap-detection signals fire as expected).
- **Glossary-gap tests:** synthetic articles with deliberately unfamiliar phrases produce `glossary_gap_observed=true` with the unfamiliar phrase preserved and `confidence=low`; truly unmappable phrases produce the appropriate `*_unmappable` reason code.
- **Tense-classification tests:** fixture articles per tense category produce the expected `metadata.tense` value and the expected reason-code shape (e.g., forward-looking → signal flag only; past_concurrent ambiguous → jurisdiction-gated reason code).
- **Feedback-payload tests:** review items derived from Pass 2c output preserve reason code, system recommendation, anchors, proposed/current values, and enough decision metadata for later rule/prompt/eval feedback.
- **LADBS port tests:** the deterministic LADBS interpreter produces identical resolved status/dates as the pre-port code on the existing LA dataset (gold-test, regression-style).
- **End-to-end smoke (manual, AGENT.2 step 7 acceptance):** the cases enumerated in §10 step 6, run against a live LLM, with reason-code distribution and resolution_log / review-queue audit verified.

---

## 12. Open questions

1. **Tense classification bare-past-tense default.** The doc currently says undated past-tense status verbs in older articles default to `past_concurrent`. Worth a researcher conversation before locking — alternative is to require an explicit date anchor for any historical-context interpretation.
2. **Pass 2c model selection over time.** Resolved for step 7 v1: Opus 4.7. Quality is the primary driver and Opus 4.7 is already the default extraction model. The AGENT.1 multi-provider abstraction makes Opus → Haiku (or other) a config change after the A/B harness shows another model meets quality bar at lower cost. No 30-day waiting period — the existing A/B harness pattern + spot-check sampler + reviewer-acceptance signal is the upgrade path.
3. **`ProductType` enum expansion (§5.2.2).** Deferred for step 7 v1. The interpreter writes the current enum values and preserves hotel / senior-care / student-housing / mixed-use specificity as signal flags. Revisit explicit enum additions after step 7 produces enough examples to justify the schema/UI/export change.
4. **`rent_or_sale` field-class promotion (§5.6.1).** Still unresolved and not part of step 7 v1. The CRITICAL safety rule in §5.6.2 applies now: unstated tenure is recorded as tenure unknown and never defaulted to for-sale. Full evidence-write capability requires a later researcher decision because it adds resolver, contradiction, override, and matcher implications.
5. **`asset_class` field as a separate dimension.** As an alternative or complement to ProductType expansion, consider a new `asset_class` field that captures compound categories (SFR/BTR, townhome rental, CCRC, mixed-use) explicitly. Trade-off: cleaner architectural separation between physical form (`product_type`) and operational class (`asset_class`), but adds another field to the schema. Either path resolves the SFR/townhome-rental display question raised in §5.6.5.
6. **Vague delivery-date projection convention (§5.4).** Resolved for step 7 v1: use the mid-month convention in §5.4 (for example "late 2026" → `2026-12-15`) because it is semantically more honest than first-of-month while still sorting predictably.
7. **`forward_looking` date extraction.** The Pass 2c prompt rubric currently says forward-looking phrases extract a projected date when present. Whether we use the same projection conventions as §5.4 (early/spring/mid/fall/late → seasonal midpoints) or treat forward-looking dates more conservatively (require explicit month/year) is a judgment call.
8. **CEQA milestone → pipeline_status interpretation (§5.12).** "Draft EIR Submitted" → Pending and "Environmental Review Completed (full EIR)" → Approved per the TCG status definitions, but operationalizing CEQA milestones from news language requires its own design (which milestones count, how to detect "full EIR" vs "categorical exemption", how to handle EIR challenges). Tracked as a follow-on after step 7 ships.
9. **Pre-Leasing → Complete transition cadence (AGENT.6).** Resolved 2026-05-06: AGENT.6 sweep stops once project hits Complete (first move-ins or CofO). Recorded here for cross-reference; no further design needed in this doc.
10. **Researcher feedback dataset.** Resolved for step 7 v1: semantic review items must preserve structured source anchors, reason codes, system recommendations, researcher decisions, final values, and reviewer notes where present. This feeds rule/prompt/eval improvement first; direct model fine-tuning is future scope.
11. **Pass 2c tool access.** Resolved for step 7 v1: no tools. Pass 2c is a single-shot classifier with rich pre-computed `InterpreterContext` input. Add narrow tools later only against documented failure modes — not speculatively. The agent layer (AGENT.2 escalation) already exists for cases requiring dynamic cross-source lookup.
12. **Multi-market glossary alert thresholds.** Defaults proposed in §5.14.3: gap rate > 15% per field, unmappable rate > 5% per field, rejection rate > established-markets baseline + 2σ. These are first-cut and may need tuning after the first new-market onboarding (Santa Monica or future) produces real distribution data. Tracked here so the thresholds are reviewable rather than buried in code.
13. **Per-market glossary admin UI.** v1 ships glossary YAML edits via git/deploy. An in-app admin UI for managing per-market addenda without a deploy is future scope, fits naturally in Phase J (admin console). Tracked here so it's not forgotten when J.1 / J.2 designs solidify.

---

## 13. Revision history

- **2026-05-06 (revision 1)** — Initial draft. Output schema, news status interpreter (tense classification, strong / ambiguous / forward-looking phrase tables), other field interpreters, jurisdiction policy schema with `permit_data_quality` and operational upgrade criteria, resolver corroboration logic (permit-driven, news-driven low-quality, news-driven high-quality with `news_status_uncorroborated` review item), source profile structure (`news_v1` + `ladbs_v1` port), migration plan, testing strategy. Pre-Leasing detection scoped out (AGENT.6).
- **2026-05-06 (revision 2)** — Expanded scope to capture additional semantic-layer responsibilities surfaced during researcher review. Major additions:
  - **§5.2 expanded** — Proposed `ProductType` enum expansion (HOTEL, senior care subcategories IL/AL/MC/SNF/CCRC, STUDENT_HOUSING, MIXED_USE) with signal-flag bridge until enum migration ships.
  - **§5.6 NEW (CRITICAL)** — Tenure interpretation. Captures the user-flagged failure mode of silently classifying single-family-rental / townhome-rental projects as for-sale. Defines the never-default-unstated-tenure safety rule, strong rental / for-sale phrase tables, SFR/BTR/townhome-rental compound detection, and mixed-tenure project handling. Proposes promoting `rent_or_sale` to Evidence-derived; flagged as requiring researcher sign-off.
  - **§5.7 NEW** — Vague-location language ("corner of A and B", landmark-relative, block-level) → coordinates. v1 handles intersection language; landmark-relative deferred to v2.
  - **§5.8 NEW** — Project name cleaning + alias detection.
  - **§5.9 NEW** — Identifier extraction (LA case numbers, LADBS permit numbers, etc.).
  - **§5.10 NEW** — Developer role disambiguation; addresses the architecture-firm-as-developer Phase A failure mode at the source.
  - **§5.11 NEW** — Status nuance for delayed / cancelled / abandoned articles. Adds `project_cancellation_review` review-item type.
  - **§5.12 NEW** — Future-scope fields with reason codes pre-defined for forward compatibility.
  - **§10 step 4 updated** — Three additive ReviewItemType enum values noted (`news_status_uncorroborated`, `multi_tenure_review`, `project_cancellation_review`).
  - **§12 expanded** — New open questions on `ProductType` enum expansion, `rent_or_sale` field-class promotion, separate `asset_class` field as alternative, mid-month vs 1st-of-month projection convention, CEQA milestone interpretation. Pre-Leasing → Complete transition cadence resolved.
- **2026-05-07 (revision 3)** — Researcher decision pass before implementation:
  - Strong physical news signals auto-promote; ambiguous early-construction signals remain jurisdiction-policy gated.
  - In high-quality permit jurisdictions, ambiguous uncorroborated news produces one deduplicated review item per project/proposed status/reason. Additional independent articles attach to the same item; three or more independent articles strengthen the recommendation/priority but still do not auto-promote without permit evidence or researcher action.
  - Forward-looking status language creates signal flags only and no review item by default.
  - CoStar/Pipedream status can be displayed as context but does not corroborate status.
  - Tenure unstated is rendered as tenure unknown and never defaults to for-sale.
  - Mixed-tenure articles should suggest missing counterpart project creation when one component already exists.
  - ProductType enum expansion is deferred for step 7 v1; use `OTHER` + signal flags.
  - Vague delivery dates use mid-month projection conventions.
  - Semantic review items must preserve structured feedback data for future rule/prompt/eval improvement and later agent training/evaluation.
- **2026-05-07 (revision 4)** — LLM-backed Pass 2c pivot + multi-market glossary architecture. The deterministic-phrase-table approach for news interpretation is replaced; news_v1 interpretation is now an LLM-backed single-shot Opus call. Major changes:
  - **§2 principle 1 reframed** — "LLM where the source is natural prose; deterministic where the source is structured." News and other prose sources use LLM interpretation; LADBS / CoStar / Pipedream / future structured permit feeds use deterministic rules. Both implementations conform to the same `SemanticInterpreter` Protocol and emit the same output schema.
  - **§2 principles 10 and 11 NEW** — Multi-market glossary architecture (base TCG rubric + per-market YAML addenda); Pass 2c is a single-shot classifier with no tools.
  - **§3.2 expanded** — `InterpreterContext` documented as the input-context contract carrying `project_state`, `jurisdiction_policy`, `market_glossary`, `recent_evidence`, `source_profile`. No tools.
  - **§3.4 NEW** — Interpretation strategy per source profile table: `news_v1` LLM-backed; `ladbs_v1` / future `costar_v1` / `pipedream_v1` deterministic; `developer_website_v1` / `alert_rss_v1` (future, if added) LLM-backed.
  - **§4 fully rewritten** — News status interpreter is now Pass 2c, a single-shot Opus 4.7 call with the `interpret_v1` prompt. Prompt content covers tense classification rubric, strong / ambiguous / forward-looking vocabulary, jurisdiction-policy gating, reason-code registry. Module lives at `src/tcg_pipeline/semantic/news/pass2c.py`; prompts at `src/tcg_pipeline/news/prompts/interpret_v1/system.md` and `schema.json`. Capability key `semantic.news_v1` for cost accounting under the shared `news` bucket. No tools. Reuses AGENT.1 multi-provider LLM abstraction. Tense classification moves into the Pass 2c prompt rubric (model emits `metadata.tense`); the prior "deterministic preprocessing pass" framing is gone. Pass 2c output is audited in `news_semantic_interpretations`, separate from `news_extractions`. The `news_use_legacy_semantic` settings flag remains the cutover gate (Render default `true` until controlled smoke).
  - **§4.3 LLM-fallback section deleted** — Pass 2c is the primary path, not a fallback. The "30 days of operational data" framing is removed.
  - **§5 framing updated** — All `news_v1` field interpreters share the same Pass 2c call. The system prompt covers all field domains in one rubric; one Pass 2c call emits multiple `SemanticInterpretation` records. Sub-section material reframed as prompt-content rubric, not Python phrase tables.
  - **§5.13 NEW** — Multi-market glossary structure. Base TCG rubric is jurisdiction-agnostic; per-market addenda live in `config/markets/<slug>/semantic_glossary.yaml` with phrase / TCG-canonical-mapping / reason-code-extension / notes per locale-specific term. Composed into the Pass 2c system prompt at inference time. Reason-code registry dynamically extends with per-market `reason_code_extension` entries, including optional confidence / status-promotion / corroboration / signal-only metadata overrides.
  - **§5.14 NEW** — Glossary gap detection. Three mechanisms: (1) make-best-fit + `glossary_gap_observed=true` signal flag at `confidence=low` rather than refuse; (2) `*_unmappable` reason codes per field domain when even best-fit fails; (3) per-market reason-code distribution monitoring with auto-alerts on gap-rate / unmappable-rate / reviewer-rejection-rate thresholds.
  - **§5.15 NEW** — New-market glossary onboarding process. Phase I.1 onboarding gains a required step: collect 50-100 representative articles, run through interpreter, draft canonical mappings for recurring unfamiliar phrases, ship via `config/markets/<slug>/semantic_glossary.yaml`, validate metrics drop below alert thresholds.
  - **§10 step 3 rewritten** — From "build deterministic phrase tables" to "build the news_v1 Pass 2c LLM call (single-shot Opus, structured output, no tools)."
  - **§11 testing strategy rewritten** — From phrase-table unit tests to fixture-driven prompt eval + A/B harness slice (reusing the existing `tcg-pipeline news ab-extract` pattern), plus per-market glossary tests, glossary-gap tests, and prompt-assembly tests. LADBS port keeps deterministic gold-test pattern.
  - **§12 question 2 reframed** — Pass 2c model is Opus 4.7 today; downgrade to Haiku is a future config change driven by A/B measurement, not a 30-day wait.
  - **§12 questions 11, 12, 13 NEW** — Pass 2c tool access (resolved: none); multi-market glossary alert thresholds (proposed defaults; reviewable); per-market glossary admin UI (future scope, Phase J).
  - **Cost impact:** Pass 2c cost is tracked separately from extraction under capability key `semantic.news_v1` in `llm_cost_usage`, while sharing the broader `news` bucket cap. The Pass 2c call is incremental over Pass 2b but uses far less context (interpretation, not full-article extraction); estimated marginal cost ~$0.05-0.10/article. At LA scale ~$2.50-15/day; at 25-market scale ~$12-125/day. Quality-driven, not cost-driven, decision.
- **2026-05-07 (revision 5)** — Pass 2c runway cleanup + audit persistence:
  - Per-market glossary `reason_code_extension` entries can now carry optional `confidence_default`, `promotes_status_alone`, `requires_corroboration`, and `signal_only` metadata. `promotes_status_alone` is valid only for `pipeline_status` entries.
  - Glossary loader validates section-specific `tcg_*` canonical keys so typoed canonical mappings fail at load time while non-`tcg_*` supplementary metadata remains allowed.
  - Pass 2c audit persistence is locked as a separate `news_semantic_interpretations` table linked to the source `news_extractions.id`, keeping extraction and semantic-interpretation prompt lineages separate.
  - Render cutover posture remains conservative: `NEWS_USE_LEGACY_SEMANTIC=true` on API and worker until controlled smoke passes, with semantic model/provider/token settings kept in parity across both services.
- **2026-05-07 (revision 6)** - Jurisdiction policy loader and Pass 2c hardening:
  - Current jurisdiction policy YAML files added for LA City (`high` / `wait_for_permit_corroboration`) and Santa Monica (`low` / `auto_promote_unverified`).
  - Missing jurisdiction policy now defaults to `low` / `auto_promote_unverified` and is surfaced in the prompt payload as `policy_source: default`.
  - Pass 2c parser records root-array recovery diagnostics and recognizes Anthropic/OpenAI truncation stop-reason vocabulary.
- **2026-05-07 (revision 7)** - News integration cutover wiring:
  - When `news_use_legacy_semantic=false`, news integration runs Pass 2c after matching, passes matched project state, matched-project jurisdiction policy, and bounded recent evidence into the prompt, persists the audit row, and maps current semantic interpretations into evidence/review flow.
  - Strong semantic status reason codes can mark evidence as `promotes_status_alone`; the status resolver honors that marker for news-backed auto-promotion.
  - Semantic review-item templates create/update structured `news_status_uncorroborated`, `multi_tenure_review`, and `project_cancellation_review` items with `semantic_interpretation_id` in payloads.
  - Article-source jurisdiction policy is retained only as `fallback_jurisdiction_policy` for unmatched/new-candidate references, which preserves correct LA behavior for unscoped sources such as Urbanize LA.

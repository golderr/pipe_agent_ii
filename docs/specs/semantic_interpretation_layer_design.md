# Semantic Interpretation Layer Design

> **Status:** Design — not yet implemented.
> **Implementation owner:** AGENT.2 sub-sequence step 7.
> **Implementation contract for:** the shared semantic field interpretation layer specified in `agentic_escalation_design.md` §5.1.1.
> **Last updated:** 2026-05-06

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

**v1 in-scope (proposed promotion to Evidence-derived; requires researcher sign-off):** `rent_or_sale` (tenure). This is currently Source-populated direct but the news interpreter has a critical safety reason to write tenure evidence — see §5.6.

**v1 in-scope (cross-field interpretation that does not write canonical evidence yet, but emits structured signal flags consumed by the matcher / resolver / future fields):** vague-location language → coordinates (§5.7), project name cleaning + alias detection (§5.8), identifier extraction (§5.9), developer role disambiguation (§5.10), status nuance for delayed / cancelled / abandoned articles (§5.11).

**Forward-compatible (future-scope fields):** the interpreter framework supports `stories`, `retail_sf` / `office_sf` / `hotel_keys` / `total_sf`, `affordable_type`, `entitlement_type` / `appeal_status` / `ceqa_status` once they graduate from Source-populated direct to Evidence-derived. Reason codes are pre-defined now (§5.12) to avoid registry rework later.

This document does NOT cover:

- The agent runner architecture (covered in `agentic_escalation_design.md`).
- Pre-Leasing detection via leasing-site discovery (covered in roadmap row AGENT.6). Step 7 emits the trigger signal flags AGENT.6 consumes.
- LLM-based interpretation fallback (deferred to v2 per §4.3).

---

## 2. Design principles

1. **Deterministic-first.** Anything mappable by deterministic phrase / signal / value rules resolves through deterministic code. LLM interpretation is reserved for genuinely unstructured or ambiguous source language and runs only when the deterministic path produces no high-confidence answer. Applies to all five field domains.

2. **Source-profile-owned mapping.** TCG semantics are not buried in any one extractor. Each source profile (`news_v1`, `ladbs_v1`, future `costar_v1`, `pipedream_v1`, `permit_v1`) declares its own `SemanticInterpreterProfile` with the per-field interpreter implementations it uses. Common deterministic logic is shared as utilities; profile-specific logic stays in the profile.

3. **Honest output, not silent decisions.** Every interpreter output carries a reason code, a confidence, source anchors, and (where relevant) signal flags. Resolver and review-queue display this context. We never silently collapse an ambiguous signal to a clean status; we record the ambiguity in the reason code and let the resolver corroborate or the researcher decide.

4. **Tense matters.** Forward-looking news language must never promote a status field. Past/concurrent and historical-with-date language can. Tense classification is a required preprocessing step before status output.

5. **Permit data is the only cross-source corroborator for status.** CoStar and Pipedream are evidence sources for physical attributes and seed data, but their status fields cannot corroborate news status signals because both are stale by design. The single cross-source corroboration path is `news_status_signal × permit_signal`. CoStar status agreement is displayed as supporting context but does not gate the algorithmic flow.

6. **Per-jurisdiction policy decides ambiguous cases.** "Broke ground"-class news language is ambiguous (ceremonial vs. actual). Jurisdictions with verified high-quality permit feeds do not promote alone on ambiguous news; they create a review item recommending stay-current. Jurisdictions without strong permit coverage promote with `confidence=medium` and an explicit "no permit corroboration" reason. New jurisdictions default to the latter; the former is earned operationally.

7. **TCG status taxonomy is preserved as-is.** The granular Conceptual / Proposed / Pending / Approved / Under Construction / Pre-Leasing / Complete / Stalled / Inactive taxonomy stays. The interpreter changes how source signals map *into* the taxonomy; it does not collapse or extend the buckets.

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

`InterpreterContext` provides read-only access to project state (current resolved values, jurisdiction config, recent evidence) so interpreters can produce output that depends on context. Project state is fetched once per integration call and shared across interpreters, not refetched per field.

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

## 4. News status interpreter (`news_v1.pipeline_status`)

This is the substantive bulk of step 7's first build. The other field interpreters in §5 follow the same pattern with simpler rules.

### 4.1 Tense classification (required preprocessing)

Before any status output is emitted, every candidate signal phrase is classified into one of three tense categories:

- **`past_concurrent`** — actually happened, anchored to a recent date (article publication date or a stated date in the article). Phrases: *broke ground*, *construction has begun*, *is underway*, *construction is well underway*, *started construction*, *construction commenced*, *began construction*.
- **`historical_dated`** — actually happened, anchored to an explicit historical date in the article. Phrases: *broke ground in spring 2025*, *construction started two years ago*, *broke ground last March*.
- **`forward_looking`** — has not happened yet. Phrases: *plans to break ground*, *is expected to break ground*, *will begin construction*, *set to break ground*, *scheduled to start*, *groundbreaking is anticipated*, *expects to break ground*.

Tense classification is deterministic — a regex/phrase-table-driven preprocessing pass. The phrase table lives at `src/tcg_pipeline/semantic/news/tense.py`.

Disambiguation rule for bare past-tense verbs without explicit temporal context (e.g., "the project broke ground"): if the article is contemporary news (publication date within 60 days of paste/scrape time), treat as `past_concurrent`; otherwise treat as `historical_dated` *only* if the article carries a parseable date elsewhere that contextualizes it. Bare past-tense verbs in older articles without any date context default to `past_concurrent` — better to surface a possibly-stale signal as a review-able status promotion than to silently drop it.

Tense classification runs once per article extraction; the classified signals flow into the status interpreter.

### 4.2 Phrase tables

#### 4.2.1 Strong vertical signals (promote regardless of jurisdiction)

These signals require demonstrable vertical construction and are unambiguous in news language. They promote to U/C with `confidence=high` and `requires_corroboration=false` in any jurisdiction. They also emit signal flags that AGENT.6 leasing-site detection consumes.

| Phrase / pattern | TCG status | Reason code | Signal flag |
|---|---|---|---|
| "topped out", "topping out", "topped off" | Under Construction | `news_topped_out` | `topped_out=true` |
| "framing complete", "framing has been completed", "wood framing topped out" | Under Construction | `news_framing_complete` | `framing_complete=true` |
| "concrete pour", "foundation poured", "foundation has been poured", "first concrete pour" | Under Construction | `news_concrete_pour` | `concrete_pour=true` |
| "halfway through construction", "more than halfway built" | Under Construction | `news_construction_midpoint` | — |
| "vertical construction underway", "vertical construction has begun" | Under Construction | `news_vertical_construction` | — |

#### 4.2.2 Strong completion signals (promote regardless of jurisdiction)

| Phrase / pattern | TCG status | Reason code | Signal flag |
|---|---|---|---|
| "ribbon cutting", "ribbon-cutting ceremony" | Complete | `news_ribbon_cutting` | `ribbon_cutting=true` |
| "first residents", "first move-ins", "residents have moved in", "tenants have moved in" | Complete | `news_first_move_ins` | `first_move_ins=true` |
| "officially opened", "now open", "opens to residents" | Complete | `news_officially_opened` | `now_open=true` |
| "fully delivered", "construction is complete" | Complete | `news_construction_complete` | — |

Per the TCG status definitions, "first move-ins" and "first sales completed" both map to Complete. The CofO path remains primary for Complete in jurisdictions with high-quality permit data, but news first-move-ins evidence stands on its own.

#### 4.2.3 Ambiguous early-construction signals (jurisdiction-policy gated)

Tense-gated: only `past_concurrent` and `historical_dated` produce status output. `forward_looking` produces signal-flag-only output per §4.2.4.

| Phrase / pattern | Reason code (`high` jurisdiction) | Reason code (`low` jurisdiction) |
|---|---|---|
| "broke ground" | `news_status_uncorroborated_high_quality_permit_jurisdiction` | `news_groundbreaking_unverified_low_quality_permit_jurisdiction` |
| "groundbreaking ceremony", "broke ground ceremonially" | same | same |
| "started construction", "construction has begun" | same | same |
| "construction is underway", "construction is now underway" | same | same |
| "began construction" | same | same |

In a `high` jurisdiction, the interpreter emits a `SemanticInterpretation` with:

- `canonical_value=Under Construction`
- `confidence=medium`
- `requires_corroboration=true`
- `reason_code=news_status_uncorroborated_high_quality_permit_jurisdiction`

The resolver does NOT promote on this output alone. It creates a `news_status_uncorroborated` review item per §7.3.

In a `low` jurisdiction, the interpreter emits the same canonical value with:

- `canonical_value=Under Construction`
- `confidence=medium`
- `requires_corroboration=false`
- `reason_code=news_groundbreaking_unverified_low_quality_permit_jurisdiction`

The resolver promotes; the project's `resolution_log` row carries the explicit "no permit corroboration" reason, and the Resolution tab UI displays it honestly.

When permit evidence later arrives in either jurisdiction, the resolver re-runs and the row updates per §7.

#### 4.2.4 Forward-looking signals (signal flag only, no status)

Forward-looking phrases never write `pipeline_status` evidence. They emit a signal flag with the projected date when extractable, and an anchor passage.

| Phrase / pattern | Output |
|---|---|
| "plans to break ground", "expects to break ground" | `signal_flags={"groundbreaking_expected_at": <date \| null>}`, reason `news_status_forward_looking_signal_flag_only` |
| "will begin construction in <date>" | same with date populated |
| "scheduled to start construction in <date>" | same |
| "groundbreaking is anticipated for <date>" | same |

The resolver may use `groundbreaking_expected_at` for delivery-year estimation. The project's `pipeline_status` is unchanged.

#### 4.2.5 Other signal-flag-only phrases (no direct status promotion)

- "leasing office is now open", "now accepting applications", "leasing has begun", "pre-leasing has begun" → emits signal flag `news_leasing_marketing_observed=true`. Does NOT promote to Pre-Leasing on its own. Pre-Leasing is determined by AGENT.6 leasing-site detection (see roadmap row).
- News-flagged "stalled" / "halted" / "indefinitely delayed" → emits signal flag `stalled_news_observed=true`. Does NOT promote to Stalled. Phase E.2 auto-stall detection (12+ months no evidence) is the actual mechanism for promoting to Stalled. The signal flag is informational and may be displayed on the project detail page.

### 4.3 LLM interpretation fallback (deferred to v2)

Step 7 v1 ships the deterministic phrase-table interpreter only. The LLM fallback for ambiguous prose ("the developer has begun preparing the site for what will eventually be a 480-unit tower") is deferred to v2 and ships only after we measure how often the deterministic table misses real signals against the smoke fixture set + 30 days of production paste-link / scheduled-cron output. The LLM call site, when added, registers a stable capability key (`semantic.news_status_v1`) in line with the J.1 admin console plan.

---

## 5. Other field interpreters (`news_v1`)

### 5.1 `pipeline_status` — see §4

### 5.2 `product_type`

The current TCG `ProductType` enum is residential-centric: `apartment | condo | townhome | single_family | micro_co_living | other`. Step 7 v1 keeps the enum as-is for the live build but **proposes an expansion** that requires researcher sign-off before merging.

#### 5.2.1 Existing enum mapping

Deterministic mapping from the news extractor's `candidate_product_type` to the existing `ProductType`:

- "apartment", "apartments", "rental apartment", "apartment building" → `APARTMENT`
- "condo", "condominium", "for-sale apartment" → `CONDO`
- "townhome", "townhouse", "rowhome", "rowhouse" → `TOWNHOME`
- "single-family detached", "single-family home", "detached home" → `SINGLE_FAMILY`
- "micro-unit", "co-living", "shared housing" → `MICRO_CO_LIVING`

Note: `ProductType` and `AgeRestriction` are independent enums per the existing schema. A 55+ apartment building is `(APARTMENT, SENIOR)`, not its own product type.

#### 5.2.2 Proposed enum expansion (requires researcher decision)

The current enum collapses several operationally-distinct asset classes into `OTHER`. Forecasting depends on per-class absorption curves and unit economics that differ materially. Proposed additions:

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

Reason codes: `news_product_type_explicit_apartment`, `news_product_type_explicit_condo`, `news_product_type_explicit_townhome`, `news_product_type_explicit_single_family`, `news_product_type_explicit_micro_co_living`, `news_product_type_care_based_senior_<subtype>`, `news_product_type_hotel`, `news_product_type_student_housing`, `news_product_type_mixed_use`, `news_product_type_unmappable`.

### 5.3 `age_restriction`

Deterministic phrase mapping:

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

  Reason codes: `news_delivery_date_projected_<season>`, `news_delivery_date_projected_quarter`, `news_delivery_date_projected_year_only`.

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

`rent_or_sale` is currently a Source-populated direct field (read-only for MVP). Step 7 **proposes promoting it to Evidence-derived** so the news interpreter can write tenure evidence. This is a substantive schema/workflow change requiring researcher sign-off:

- Resolver gains a `rent_or_sale` resolver (most recent explicit-source-stated value wins).
- Contradiction detection covers tenure mismatches.
- Override API allows researcher overrides on tenure.
- Matcher gains tenure as a key matching dimension (rental and for-sale projects at the same address are treated as separate projects unless an explicit `counterpart` relationship exists).

Until the promotion ships, the news interpreter still emits tenure observations as signal flags, but does not write `rent_or_sale` evidence rows.

#### 5.6.2 CRITICAL safety rule: never default unstated tenure

The single most important rule in tenure interpretation: **if the article does not explicitly state tenure, do NOT default to for-sale.**

The motivating failure mode: a news article saying "this 80-unit townhome project will deliver in 2027" without explicit tenure language. The traditional default in some markets is to assume "townhome" = for-sale, but the SFR/BTR (single-family-rental / build-to-rent / build-for-rent) asset class has grown materially since 2020 and townhome rental communities are common. Silently classifying an SFR townhome project as for-sale would be a real and recurring error that contaminates downstream forecasting.

When tenure is unstated, the interpreter:
- Emits `signal_flags={"tenure_unstated": true}`.
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
- Matcher behavior: writes evidence to both linked projects if they exist (linked via `counterpart` or `phase` relationship); otherwise creates a `multi_tenure_review` review item asking the researcher to set up the relationships and re-process.

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
- **`affordable_type`** — "100% affordable LIHTC project", "ED1 streamlined", "TOC bonus", "density bonus project". Currently emitted as signal flags (`lihtc_observed`, `ed1_observed`, `toc_observed`, `density_bonus_observed`); becomes canonical when `affordable_type` graduates.
- **`entitlement_type` / `appeal_status` / `ceqa_status`** — "Draft EIR released", "Final EIR certified", "EIR challenged in court", "appeal denied". Reasons reserved: `news_ceqa_status_*`, `news_appeal_status_*`. CEQA milestone interpretation interacts with `pipeline_status` per the TCG status definitions ("Draft EIR Submitted" → Pending; "Environmental Review Completed (full EIR)" → Approved); a follow-on design covers operationalizing it. Tracked as an open question in §12.

Until graduation, the interpreter emits these as signal flags only; no evidence rows are written.

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
# config/jurisdictions/santa_monica.yaml
slug: santa_monica
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

- New jurisdictions added to `config/jurisdictions/` default to `permit_data_quality: low`, `news_status_promotion_policy: auto_promote_unverified`. The new-market onboarding checklist (Phase I.1) requires explicit acknowledgement of the default.
- Missing jurisdiction config file → loader fails fast at startup with a clear error directing the operator to create the file.

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
  "reason_code": "news_status_uncorroborated_high_quality_permit_jurisdiction"
}
```

The review-queue card uses the existing decision-card UI shape with one specialization: a default-recommendation banner ("System suggests: keep Approved"). Decision keys are unchanged (`a` accept-new, `s` keep-current, `d` defer, `f` custom).

If permit evidence arrives later that promotes the project to U/C, the resolver invalidates the open `news_status_uncorroborated` item (it is now consistent with the resolved status) and surfaces the news evidence as a supporting row on the new permit-driven resolution. The audit trail records that the review item was superseded by permit corroboration rather than by researcher action.

A new value `news_status_uncorroborated` is added to the `ReviewItemType` enum. Migration is additive (new enum value), conservative per the AGENT.reset config-table preservation rule.

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
3. **Build `news_v1` interpreters** per §4-5. Behind a `news_use_legacy_semantic` settings flag (default `false` in code, `true` on Render until controlled smoke). When the flag is `true`, the news integrator uses legacy semantics; when `false`, it uses the new interpreters.
4. **Wire the resolver corroboration step** per §7. Three additive `ReviewItemType` enum values are added in this step: `news_status_uncorroborated` (§7.3), `multi_tenure_review` (§5.6.6), and `project_cancellation_review` (§5.11). All three are additive migrations; no existing review-item rows are rewritten.
5. **Add jurisdiction config files** for LA City (`high`) and any other current jurisdictions (`low`). Document the operational data that earned LA City `high`.
6. **Run the smoke suite end-to-end:** the D.6 5-article fixture set + paste-link smokes with explicit cases:
   - `topped_out` strong signal → expect U/C, high confidence, regardless of jurisdiction.
   - `broke_ground` ambiguous + LA (`high` jurisdiction) → expect `news_status_uncorroborated` review item, no status promotion.
   - `broke_ground` ambiguous + a hypothetical Santa Monica `low` jurisdiction → expect U/C promotion with `confidence=medium`, no review item.
   - Forward-looking "plans to break ground" → expect signal flag emission, no status change.
   - `first_move_ins` → expect Complete promotion + `first_move_ins=true` signal flag (consumable by AGENT.6 once it ships).

   Confirm each produces the expected interpreter output, reason code, and resolution / review-item behavior.
7. **Flip `news_use_legacy_semantic` off** on Render after smoke success. Render kill switches remain available as fast disable.
8. **Move legacy code paths** in news integration to `news/extraction_legacy.py` per AGENT.2 sub-sequence step 8 (separate ship).

The semantic-layer cutover is independent of the AGENT.reset event. AGENT.reset's truncate-and-reseed simplifies because all evidence rows after the reset will be written under the final semantic-layer logic.

---

## 11. Testing

- **Unit tests:** one fixture-driven test per phrase-table entry per field. Inputs are short article fragments; outputs are `(canonical_value, confidence, reason_code, signal_flags)`. Easy to grow as researcher feedback comes in.
- **Per-field integration tests:** full extraction → interpretation → evidence write → resolution flow, with a representative article per case.
- **Jurisdiction-policy tests:** the same article processed under `high` and `low` jurisdictions produces the expected divergent outputs.
- **Tense-classification tests:** unit tests per tense category with positive and negative phrases.
- **Reason-code stability tests:** every reason code referenced in code is present in the registry; the registry has no dangling entries.
- **End-to-end smoke (manual, AGENT.2 step 7 acceptance):** the cases enumerated in §10 step 6, run against a live LLM, with reason-code distribution and resolution_log / review-queue audit verified.

---

## 12. Open questions

1. **Tense classification bare-past-tense default.** The doc currently says undated past-tense status verbs in older articles default to `past_concurrent`. Worth a researcher conversation before locking — alternative is to require an explicit date anchor for any historical-context interpretation.
2. **LLM fallback for ambiguous prose.** Step 7 v1 ships deterministic only. Criteria for promoting LLM fallback to v2 (and the prompt design) need to be defined after 30 days of v1 operational data.
3. **`ProductType` enum expansion (§5.2.2).** Whether to introduce `HOTEL`, `SENIOR_CARE_INDEPENDENT_LIVING`, `SENIOR_CARE_ASSISTED_LIVING`, `SENIOR_CARE_MEMORY_CARE`, `SENIOR_CARE_SKILLED_NURSING`, `SENIOR_CARE_CCRC`, `STUDENT_HOUSING`, and `MIXED_USE` as explicit enum values, or whether `OTHER` + signal flags is sufficient. Forecasting uses these as distinct asset classes; conflating them inside `OTHER` loses real information. Schema impact: enum migration + one-time rewrite of existing `OTHER` rows that carry the relevant signal flags.
4. **`rent_or_sale` field-class promotion (§5.6.1).** Whether to graduate `rent_or_sale` from Source-populated direct to Evidence-derived so the news interpreter can write tenure evidence. Schema impact: resolver gains a tenure resolver, contradiction detection covers tenure, override API allows tenure overrides, matcher gains tenure as a key matching dimension. The CRITICAL safety rule in §5.6.2 applies in either case (signal flag emitted regardless), but full evidence-write capability requires this promotion.
5. **`asset_class` field as a separate dimension.** As an alternative or complement to ProductType expansion, consider a new `asset_class` field that captures compound categories (SFR/BTR, townhome rental, CCRC, mixed-use) explicitly. Trade-off: cleaner architectural separation between physical form (`product_type`) and operational class (`asset_class`), but adds another field to the schema. Either path resolves the SFR/townhome-rental display question raised in §5.6.5.
6. **Mid-month vs 1st-of-month projection convention (§5.4).** The doc uses mid-month dates for vague timing language ("late 2026" → `2026-12-15`). Researcher may prefer 1st-of-month (`2026-12-01`) for cleaner display. Differences are tiny (15 days) and easy to flip.
7. **`forward_looking` date extraction.** The phrase-table currently says forward-looking phrases extract a projected date when present. Whether we use the same projection conventions as §5.4 (early/spring/mid/fall/late → seasonal midpoints) or treat forward-looking dates more conservatively (require explicit month/year) is a judgment call.
8. **CEQA milestone → pipeline_status interpretation (§5.12).** "Draft EIR Submitted" → Pending and "Environmental Review Completed (full EIR)" → Approved per the TCG status definitions, but operationalizing CEQA milestones from news language requires its own design (which milestones count, how to detect "full EIR" vs "categorical exemption", how to handle EIR challenges). Tracked as a follow-on after step 7 ships.
9. **Pre-Leasing → Complete transition cadence (AGENT.6).** Resolved 2026-05-06: AGENT.6 sweep stops once project hits Complete (first move-ins or CofO). Recorded here for cross-reference; no further design needed in this doc.

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

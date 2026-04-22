# Evidence Layer — Implementation Decisions

> Answers to clarifying questions before implementation begins.
> These decisions are authoritative — treat them as amendments to the integration guide.

**Date:** 2026-04-20

---

## 1. Phase Ordering — Phase 1 before Phase 2

**Confirmed: Phase 1 (schema + backfill) first, then Phase 2 (resolution engine).**

You're right and this is important. The resolution engine needs real backfilled evidence to validate against. Building the engine first and testing against synthetic data risks tuning against fake data and missing edge cases that only appear in the real Pipedream/CoStar/LADBS records. Phase 1 is non-breaking, unblocks everything, and gives Phase 2 a real dataset to validate against.

Sequence:
1. Phase 1a: Alembic migrations (new tables + new columns)
2. Phase 1b: `config/source_tiers.yaml`
3. Phase 1c: Backfill script (PSR → Evidence + synthesized Pipedream evidence — see Q5 backfill clarification below)
4. Phase 1d: Backfill developer_registry
5. Phase 2: Resolution engine, tested against backfilled evidence

---

## 2. delivery_year — Column Strategy

**Keep `date_delivery` (Date type). Do NOT add a separate integer `delivery_year` column.**

The guide uses "delivery_year" as a conceptual field name, but the resolution engine should continue writing `date_delivery` using a normalized date convention:

- **Explicit dates from sources:** Store the actual date when available (e.g., `2028-06-01` from CoStar's Year Built + Month Built). When only a year is provided, normalize to `{year}-01-01`.
- **Estimated dates from the formula:** Store as `{estimated_year}-07-01` (midpoint convention).

The `delivery_year_provenance` column (VARCHAR) carries whether it's `explicit_government`, `explicit_tcg`, `explicit_news`, `explicit_costar`, or `estimated_calc` — that tells the consumer whether the date precision is real or synthetic.

The resolution field resolver should be named `resolve_delivery_year()` but it writes to `project.date_delivery`. No schema confusion needed.

---

## 3. source_type — Logical Names, Not Adapter Names

**Use logical/canonical publisher names, not runtime adapter names.**

Evidence `source_type` should be:

| Logical source_type | Maps from adapter/source_name |
|---|---|
| `ladbs_permit` | `ladbs_permits` (pi9x-tg5x Bldg-New) |
| `ladbs_permit` | `ladbs_permit_activity` (pi9x-tg5x non-Bldg-New) |
| `ladbs_inspection` | `ladbs_inspections` (9w5z-rg2h) |
| `ladbs_cofo` | `ladbs_cofo` (3f9m-afei) |
| `pipedream` | `pipedream` |
| `costar` | `costar` |

Rationale: The two permit adapters (`ladbs_permits` and `ladbs_permit_activity`) hit the same Socrata dataset with different filters. They represent the same publisher (LADBS permits). The resolution engine and tier config should not need to know that we split the query into two adapters — that's an implementation detail.

**Create a mapping function** (e.g., `get_logical_source_type(source_name: str) -> str`) and a config dict that maps runtime source names → logical source types. Use this during evidence insertion and during the backfill.

For the backfill: `ProjectSourceRecord.source_name` values in the DB will be the runtime names. The backfill script should use the same mapping to convert them to logical source types.

The `source_tiers.yaml` config uses logical source types (as already written in the guide).

---

## 4. Append-Only Semantics — Hash-Based Dedup

**Do NOT insert a new evidence row for identical re-pulls.** Only insert when `source_row_hash` has changed or when this is a genuinely new observation.

The current incremental overlap window intentionally re-pulls recent rows to catch late-arriving updates. When a re-pulled row's `source_row_hash` matches the most recent evidence row for the same `(project_id, source_type, source_record_id)`, skip the insert.

Pseudocode:
```python
existing = get_most_recent_evidence(project_id, source_type, source_record_id)
if existing and existing.raw_data_hash == compute_hash(raw_record):
    # Identical re-pull — skip
    return
# Changed or new — insert evidence
insert_evidence(...)
```

This keeps the evidence table from ballooning with duplicate rows from the overlap window while still preserving every real change. The table is still conceptually append-only — nothing is ever updated or deleted — we just don't insert duplicates.

Add a `raw_data_hash` column (VARCHAR(64), nullable) to the evidence table for this check. It mirrors the existing `source_row_hash` concept from PSR.

---

## 5. Backfill — evidence_date Fallback and Pipedream-Origin Fields

Two issues here, both important:

### 5a. evidence_date Fallback for PSR Rows

Use this fallback chain:
1. A field-specific date from `mapped_fields` if available (e.g., `status_evidence_date`, `permit_issue_date`, `cofo_issue_date`, `inspection_date`)
2. `source_updated_at` (when the source system last modified the record)
3. `source_created_at` (when the source system created the record)
4. `first_seen_at` (when our system first pulled it)
5. `NULL` as absolute last resort (should be rare)

For Pipedream seed PSRs specifically: use the project's `status_date` or `last_edit_date` as the best proxy, since Pipedream records represent researcher-verified snapshots at a point in time.

### 5b. Pipedream-Origin Fields Without PSR Coverage (CRITICAL)

This is the most important backfill nuance. The Pipedream seed ingester creates `Project` records with all fields populated AND creates a `ProjectSourceRecord` per project. However, the PSR's `mapped_fields` only contains a subset of fields (project_name, canonical_address, pipeline_status, status_date, city, state, zip, total_units, market_rate_units, affordable_units, developer). Other Pipedream-origin fields on the Project (product_type, age_restriction, stories, delivery_date, rent_or_sale, planner fields, etc.) have no PSR backing.

**Decision: Synthesize a comprehensive "pipedream seed" evidence row per project** from the current Project field values for any Pipedream-seeded project. This evidence row should contain ALL fields that Pipedream populated — not just what's in the PSR's `mapped_fields`.

Implementation:
```python
for project in pipedream_seeded_projects:
    # Build extracted_fields from project's current values
    extracted_fields = {
        "pipeline_status": {"value": project.pipeline_status.value, "confidence": "high"},
        "total_units": {"value": project.total_units, "confidence": "high"},
        "affordable_units": {"value": project.affordable_units, "confidence": "high"},
        "market_rate_units": {"value": project.market_rate_units, "confidence": "high"},
        "product_type": {"value": project.product_type.value, "confidence": "high"},
        "age_restriction": {"value": project.age_restriction.value, "confidence": "high"},
        "developer": {"value": project.developer, "confidence": "high"},
        "date_delivery": {"value": str(project.date_delivery), "confidence": "high"},
        # ... all other Pipedream-populated fields
    }
    # Strip None values
    extracted_fields = {k: v for k, v in extracted_fields.items() if v["value"] is not None}
    
    insert_evidence(
        project_id=project.id,
        source_type="pipedream",
        source_tier=1,
        ingest_method="seed_import",
        evidence_date=project.last_edit_date or project.status_date or project.created_at.date(),
        raw_data=psr.raw_payload if psr exists else None,
        extracted_fields=extracted_fields,
    )
```

This ensures the resolver has evidence covering every field Pipedream populated. Without this, the resolver would compute empty results for fields like product_type and age_restriction and overwrite good data with Unknown.

**Additionally:** For PSR rows that DO exist for Pipedream projects, you should still create evidence rows from those PSRs, but merge/deduplicate against the synthesized row — don't create two Pipedream evidence rows for the same fields. The synthesized row should be the canonical one, with the PSR's `raw_payload` attached as the `raw_data`.

---

## 6. extracted_fields Shape — Uniform Wrapper, Nullable Confidence

**Use the `{field: {value, confidence}}` wrapper shape for ALL evidence rows.** For structured sources where confidence is implicit, set `confidence` to `null`.

```json
// Structured source (LADBS permit via Socrata)
{
  "total_units": {"value": 450, "confidence": null},
  "pipeline_status": {"value": "Approved", "confidence": null}
}

// LLM-extracted (news article via deep research)
{
  "total_units": {"value": 450, "confidence": "high"},
  "developer": {"value": "CIM Group", "confidence": "medium"}
}
```

Rationale: A single shape is cleaner for the resolver. When `confidence` is null, the resolver treats it as "confidence determined by source tier" (which is exactly what the guide says for structured sources). No branching needed in resolver code to handle two different shapes.

During backfill, use the same shape: `{field: {value: v, confidence: null}}` for all seed/collector evidence rows.

---

## 7. AgeRestriction Enum — Preserve for Now

**Keep `Non Age-Restricted` in the enum. Preserve backward compatibility. The resolver should be able to emit it.**

The guide's intent was that "silence is not evidence of non-restriction" — meaning a source that doesn't mention age restriction doesn't count as evidence for `Non Age-Restricted`. But when a source *explicitly* says "no age restriction" or "all ages" (like CoStar's Market Segment = "All"), `Non Age-Restricted` is the correct resolved value.

The enum stays as-is:
- `Non Age-Restricted` — explicitly confirmed no restriction
- `Senior` — age-restricted for seniors
- `Student` — student housing
- `Unknown` — no source has addressed this field

The resolver:
- Sources that explicitly state "all ages" / "non-restricted" → evidence for `Non Age-Restricted`
- Sources that don't mention age restriction → NO evidence for this field (null in extracted_fields)
- Default when no evidence exists → `Unknown`

This avoids a breaking migration and keeps all existing tests passing. No cleanup needed.

---

## 8. Unit Split When Total Changes — Keep Last Explicit Split

**Keep the last explicit affordable/market_rate values. Do NOT null them out. Flag for researcher awareness.**

When total_units changes but no source provides a new split:
- `total_units` → updated to the new value
- `affordable_units` → stays at last explicit value
- `market_rate_units` → stays at last explicit value
- Validation runs: if `affordable + market_rate != total (±2)`, generate a review item: *"Total units updated to {new}. Affordable/market-rate split ({aff}/{mr}) may need revision — sum no longer matches total."*

The researcher can then update the split or confirm it's correct. Nulling out the split would lose data. Holding the total would block legitimate updates.

---

## 9. StatusHistory — Resolution Engine Should Append

**Yes, the resolution engine should append a `StatusHistory` row when it changes `pipeline_status`.**

`StatusHistory` is not legacy — it's the human-readable audit trail. Evidence is the machine-readable source of truth.

When `resolve_status()` determines a new status:
```python
if resolved_status != project.pipeline_status:
    session.add(StatusHistory(
        project_id=project.id,
        status=resolved_status,
        status_date=evidence_date_of_determining_evidence,
        source="resolution_engine",
        notes=f"Resolved from evidence. Rule: {rule_code}. Confidence: {confidence}."
    ))
```

This keeps StatusHistory as a complete progression log that a researcher can read chronologically, regardless of whether the change came from a seed import, a collector, or the resolution engine.

---

## 10. status_confidence vs. confidence — Dual-Write During Transition

**Add the new `confidence` column. Keep `status_confidence`. Write both in lockstep during Phases 1-3.**

Phase 1: Add `confidence` column (new) with default `'low'`. Leave `status_confidence` untouched.

Phase 2-3: When the resolution engine computes overall confidence, write it to both `confidence` AND `status_confidence`. They'll be identical during this period.

Phase 4+: Once all consumers are migrated to read `confidence`, deprecate `status_confidence` in a future migration. Don't rush this — it's a read-only concern.

This avoids a breaking migration, avoids a dead column confusion, and gives us a clean transition path.

---

## 11. Unmatched Records — Insert Evidence with NULL project_id

**Yes, insert evidence rows for unmatched records even when `create_new_candidates = false`.**

The evidence exists regardless of whether we create a review item for it. An unmatched LADBS permit is still a real observation that might match a project later (via a future identifier link or address normalization fix).

When a reviewer eventually links the evidence to a project (or when a future collector run matches it), the `project_id` gets backfilled:
```python
UPDATE evidence SET project_id = :linked_project_id WHERE id = :evidence_id
```

This is the one exception to "evidence is never updated" — `project_id` assignment on unmatched evidence is a linking operation, not a data change.

The `create_new_candidates = false` flag only controls whether a ReviewItem is created, not whether evidence is recorded.

---

## 12. Likelihood Engine — Base Rate Only for V1

**Ship v1 with base_rate[status] only. Signal adjustments active in code but effectively zero for signals we can't yet detect.**

Implementation:
```python
def compute_likelihood(resolved: dict, evidence: list[Evidence]) -> tuple[float, dict]:
    status = resolved['pipeline_status']
    if status == PipelineStatus.UNDER_CONSTRUCTION:
        return 1.00, {"base": 1.00, "signals_applied": [], "final": 1.00}
    
    base = BASE_RATES[status]
    signals_applied = []
    
    # Signals we CAN compute from current data:
    if _has_recent_activity(evidence, days=90):
        signals_applied.append({"name": "recent_activity_under_90_days", "value": 0.05})
    if _is_top_tier_developer(resolved.get('developer')):
        signals_applied.append({"name": "top_tier_developer", "value": 0.05})
    if _has_permits_filed(evidence):
        signals_applied.append({"name": "permits_filed", "value": 0.08})
    if _no_activity_over_period(evidence, months=12, cap=24):
        signals_applied.append({"name": "no_activity_12_to_24_months", "value": -0.10})
    if _no_activity_over_period(evidence, months=24):
        signals_applied.append({"name": "no_activity_over_24_months", "value": -0.20})
    
    # Signals we CANNOT compute yet (need news/deep research):
    # construction_financing_announced, public_opposition_or_lawsuit,
    # sales_or_leasing_center_open, land_assembly_incomplete,
    # prior_phase_delivered_same_site, affordable_or_inclusionary_component,
    # unknown_or_first_time_developer
    # → These will activate when evidence with signal_flags arrives
    
    adjustment = sum(s["value"] for s in signals_applied)
    final = max(0.02, min(0.98, base + adjustment))
    
    return final, {"base": base, "signals_applied": signals_applied, "final": final}
```

So the engine is fully structured and extensible — it just has limited signal coverage initially. As news/deep research sources come online and populate `signal_flags` on evidence rows, the signals will fire automatically without code changes (just config).

---

## Additional Clarifications (proactively addressing edge cases)

These weren't in your questions but are important to nail down before implementation:

### 13. Fields Outside the Resolution Engine

The resolution engine covers 6 conceptual fields (status, units×3, product_type, delivery_year, age_restriction, developer). The Project table has ~50 other fields (canonical_address, stories, acres, retail_sf, planner fields, zoning, etc.).

**Decision: Only these 6 fields (plus confidence + likelihood) are resolver-owned in Phases 1-3.** All other fields continue to be written directly by ingesters/seed, exactly as they are today. The evidence table still captures the raw data for those fields, but the resolution engine doesn't try to resolve them.

This can be revisited later with a generic "most-recent-wins" resolver for secondary fields, but that's out of scope for now. Don't overcomplicate the initial implementation.

### 14. Review Item Generation — Keep Differ Through Phase 3

**Keep the differ alongside the resolution engine through Phase 3.** Don't fold review-item logic into the resolver yet.

During Phase 3, the flow is:
1. Insert evidence
2. Run `resolve_project()` → updates Project fields
3. Run differ on the *old* vs *new* Project values → generates ReviewItems as before

The differ already handles ReviewItem creation well. Folding that logic into the resolver would mean rewriting all review-item logic and all tests that assert on ReviewItem creation. Not worth it now. Plan a Phase 6 cleanup to consolidate.

The resolution engine can generate *additional* review items for things the differ doesn't handle:
- Confidence-based flags ("this field changed but confidence is LOW")
- Corroboration requirements ("permit issued alone — needs review before UC promotion")
- Auto-stall detection ("no evidence for 12+ months")

These are additive, not replacements for the differ's output.

### 15. "Most Recent" Tie-Break Semantics

When multiple evidence rows compete for "most recent wins":

1. **Primary sort:** `evidence.evidence_date` DESC (the real-world date — when the event happened)
2. **Tie-break 1:** `evidence.collected_at` DESC (when our system saw it — more recent pull wins)
3. **Tie-break 2:** `source_tier` ASC (lower tier number = higher authority)

This means: a government permit issued on 2026-03-15 that we pulled on 2026-04-01 beats a news article published on 2026-03-15 that we pulled on 2026-04-10. The real-world date is primary; collected_at only breaks ties; tier only breaks double-ties.

### 16. Researcher Override Shape — Structured

**Use `{field: {value, set_by, set_at, note}}` for `researcher_override`.**

```json
{
  "pipeline_status": {
    "value": "Stalled",
    "set_by": "nate",
    "set_at": "2026-04-15T14:30:00Z",
    "note": "Developer confirmed project on hold pending financing"
  },
  "total_units": {
    "value": 300,
    "set_by": "nate",
    "set_at": "2026-04-15T14:30:00Z",
    "note": "Confirmed with developer — reduced from 450"
  }
}
```

The resolver reads `researcher_override[field]["value"]` and skips resolution for any field present. The metadata (`set_by`, `set_at`, `note`) is for audit trail and future UI rendering.

The existing `researcher_override` column is already JSONB and nullable. No schema change needed — just document the expected shape and handle legacy null/unstructured values gracefully in the resolver (if `researcher_override` is a plain `{field: value}` dict, treat it as `{field: {value: value, set_by: "legacy", set_at: null, note: null}}`).

### 17. Phase 2 Discrepancy Logging

**Log discrepancies to a `resolution_log` table, not stdout or a file.**

```sql
CREATE TABLE resolution_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    field           VARCHAR(120) NOT NULL,
    current_value   JSONB,
    resolved_value  JSONB,
    evidence_ids    UUID[],         -- which evidence rows drove the resolution
    rule_applied    VARCHAR(120),   -- e.g., "most_recent_wins", "forward_only_promotion"
    confidence      VARCHAR(10),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Add this table in the Phase 1 migration (it's just a table, no behavior change). Phase 2's `resolve-all` CLI command writes to it. This gives us a queryable record of what the resolver would change and why — much more useful than grep-ing log files.

After Phase 2 validation, this table also serves as the ongoing audit trail for resolution decisions. Keep it permanently.

### 18. Test Backward Compatibility

Existing tests assert specific Project field values after `persist_collected_records()`. Phase 3 will change those values because they'll be computed by the resolver instead of written directly.

**Plan:**
1. Phase 2: Run `resolve-all` against backfilled evidence. Compare resolver output to current Project values. Log all discrepancies to `resolution_log`.
2. Review discrepancies. Most should be explainable (e.g., "resolver picked a different most-recent source" or "confidence computed differently").
3. Phase 3: Update test assertions where the resolver's output is the *correct* new behavior. Add comments explaining why the assertion changed.
4. For any cases where the resolver output is worse than the current value, tune the resolution rules before proceeding.

Don't try to make every test pass unchanged — that would mean building a resolver that replicates every quirk of the current direct-write logic, which defeats the purpose.

---

## 19. Phase 3-4 Follow-Up Decisions

**Date:** 2026-04-21

These decisions were made during the Phase 3 remediation and Phase 4 developer-canonicalization follow-up pass.

### 19a. Phase 3 Fixes Were Intentional

The following changes were intentional and should be treated as part of the evidence-layer retrofit, not incidental refactors:

- `StatusHistory` entries now use the underlying evidence `source_type` and `evidence_type`, not just `resolution_engine`.
- The project diff snapshot now covers all resolver-owned user-facing fields, including `affordable_units`, `market_rate_units`, `product_type`, `date_delivery`, `age_restriction`, and `developer`.
- `resolution_log` now includes `confidence_reason` and `likelihood_breakdown`. `status_confidence` remains excluded because it mirrors `confidence` during the transition.
- Unit-split protection includes both:
  - a split-source allowlist for `affordable_units` / `market_rate_units`
  - a `unit_split_mismatch` review flag when total units change but the preserved split no longer sums to total

### 19b. Developer Review Flags Are Not Gated On Field Delta

For developer canonicalization:

- `fuzzy_review` (75-89) must always emit a review flag
- `new_registry_entry` must always emit a review flag

This is true even when the resolved canonical developer name already equals `project.developer`. Alias creation / registry expansion is reviewer-visible behavior on its own.

### 19c. Registry Writes Are Sweep-Only

Normal collector, seed, and `resolve-all` resolution must **not** create or merge `developer_registry` / `developer_alias` rows.

Developer canonicalization during normal resolution is read-only:

- compute canonical name
- apply canonical project value
- emit review flags / logs
- do **not** mutate the registry

The only supported write path for registry / alias mutations is:

- `python -m tcg_pipeline canonicalize-developers --apply`

### 19d. Bootstrap Runbook Before Live Collection

Before the first normal collector or seed run on a DB that has the evidence-layer schema but an empty developer registry, bootstrap in this order:

1. `alembic upgrade head`
2. `python scripts/backfill_developers.py`
3. `python -m tcg_pipeline canonicalize-developers --apply`
4. Then resume `collect_source`, `seed_pipedream`, `seed_costar`, and other normal apply-mode workflows

Rationale:

- the backfill seeds one canonical row per existing `projects.developer`
- the canonicalization sweep creates aliases and merges obvious duplicates
- only after that should ongoing collector evidence rely on the registry for review semantics

### 19e. Registry Snapshot Caching

Developer canonicalization uses a session-scoped registry snapshot cache:

- cache is stored per SQLAlchemy session
- reused across `resolve_developer()` and likelihood developer checks
- invalidated whenever the sweep creates aliases, creates registry rows, or merges registry rows

This is a scale safeguard for `resolve-all` and large collector runs.

### 19f. Sweep Merge Semantics

`canonicalize-developers --apply` may merge duplicate registry rows during the registry-scanning phase. This is expected and part of the one-time bootstrap path.

Operationally:

- CLI output must report merge counts
- re-running the sweep after manual registry edits should be done deliberately

### 19g. Shadow-Mode Semantics

Shadow-mode canonicalization is intentional:

- `resolve-all` without `--apply` may still log canonicalized developer values in `resolution_log.resolved_value`
- this reflects the resolver's computed output, not persisted registry / alias writes

Downstream readers of `resolution_log` should treat shadow rows as "what the resolver would do," not proof that canonicalization side effects were committed.

### 20. Review Decision Workflow and Evidence Relink

The next backend step after Phase 4 is the review-decision workflow for discovery items.

Decisions:

- `NEW_CANDIDATE` and `POSSIBLE_MATCH` accept actions relink all orphan evidence rows for the same `(logical source_type, source_record_id)` where `project_id IS NULL`.
- Acceptance can target either an existing project or a newly created project stub.
- `POSSIBLE_MATCH` acceptance links to the reviewer-selected project only.
- `ReviewDecision.field_overrides` are merged into `Project.researcher_override` before `resolve_project(apply=True)` so Tier 0 overrides apply on first resolve.
- Acceptance synchronously re-runs `resolve_project()` in the same transaction.
- Resolver-driven field writes from acceptance create `ChangeLog` rows with `change_type = RESEARCHER_CONFIRMED`.
- Acceptance also upserts a `ProjectSourceRecord` cache row so future collector reruns can source-record-match immediately instead of reopening discovery.

### 20a. Reject Semantics

Reject uses `DismissedRecord`; evidence is preserved and remains immutable.

Decisions:

- Rejecting a discovery review item creates a `DismissedRecord` keyed by `(source_run.source_name, source_record_id)`.
- Future collector runs still capture evidence for that source row, but they suppress new `NEW_CANDIDATE` / `POSSIBLE_MATCH` review items for dismissed keys.
- This preserves the observation for future undismiss or audit workflows without re-cluttering active review queues.

### 20b. Idempotency and Scope

Decisions:

- Only `OPEN` review items may mutate state.
- Evidence relink only updates orphan rows (`project_id IS NULL`).
- Reject is idempotent on the dismissed key; it should not create duplicate `DismissedRecord` rows.
- This workflow pass targets discovery-item accept/reject/defer semantics. Status-change review acceptance remains a later follow-up.

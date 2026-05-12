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

This is the most important backfill nuance. The Pipedream seed ingester creates `Project` records with all fields populated AND creates a `ProjectSourceRecord` per project. However, the PSR's `mapped_fields` only contains a subset of fields (project_name, canonical_address, pipeline_status, status_date, city, state, zip, total_units, market_rate_units, affordable_units, workforce_units, developer). Other Pipedream-origin fields on the Project (product_type, age_restriction, stories, delivery_date, rent_or_sale, planner fields, etc.) have no PSR backing.

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
        "workforce_units": {"value": project.workforce_units, "confidence": "high"},
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

**Keep the last explicit affordable/workforce/market_rate values. Do NOT null them out. Flag for researcher awareness.**

When total_units changes but no source provides a new split:
- `total_units` → updated to the new value
- `affordable_units` → stays at last explicit value
- `workforce_units` → stays at last explicit value; if unknown, stays `NULL`
- `market_rate_units` → stays at last explicit value
- Validation runs: if all three buckets are known and `affordable + workforce + market_rate != total (±2)`, or if known buckets exceed total by more than 2, generate a review item. Unknown buckets remain `NULL` in the UI and are not silently treated as another bucket.

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

**Use `{field: {value, set_by, set_at, note}}` semantics for researcher overrides.**

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

The resolver reads active `researcher_overrides` rows and skips resolution for any field present. The metadata (`set_by`, `set_at`, `note`) is for audit trail and future UI rendering.

Historical note: this originally used the nullable `projects.researcher_override` JSONB column. C.c promoted overrides into the `researcher_overrides` table, and C.tail.2 retired the legacy JSONB column after table-backed reads were verified.

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
- The project diff snapshot now covers all resolver-owned user-facing fields, including `affordable_units`, `workforce_units`, `market_rate_units`, `product_type`, `date_delivery`, `age_restriction`, and `developer`.
- `resolution_log` now includes `confidence_reason` and `likelihood_breakdown`. `status_confidence` remains excluded because it mirrors `confidence` during the transition.
- Unit-split protection includes both:
  - a split-source allowlist for `affordable_units` / `workforce_units` / `market_rate_units`
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
- `ReviewDecision.field_overrides` are written into `researcher_overrides` before `resolve_project(apply=True)` so Tier 0 overrides apply on first resolve.
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

### 20c. Review Workflow Hardening

Additional refinements after senior review:

- Accept must fail fast if matching evidence rows already belong to a different project. Review acceptance may link orphan evidence into the chosen project, but it must not silently mix evidence histories across projects.
- Accept must also fail fast if an existing `ProjectSourceRecord` for the same `(source_name, source_record_id)` already belongs to another project. Silent PSR re-parenting is not allowed.
- Once a discovery source record is dismissed, future unmatched collector runs skip both evidence insertion and review-item creation for that source record. Dismissed discoveries should not keep accumulating orphan evidence rows over time.
- Identifier conflicts discovered during accept are surfaced to the operator; acceptance still succeeds, but conflicting identifiers are not re-attached away from their current owning project.
- If accept-triggered `resolve_project()` emits review flags (for example permit-issued-alone or unit-split mismatch), the workflow creates a follow-up `STATUS_CHANGE` review item so the reviewer sees that additional researcher attention is still required after acceptance.

### 21. Conditional Researcher Overrides

**Superseded by §22 for Phase B/C UI behavior.** This section remains as historical context for Phase A behavior and any code paths not yet migrated to review-protected overrides.

Researcher-selected values should not pin a field forever by default. They should hold until genuinely newer evidence appears.

This is an intentional refinement of the guide's earlier "Tier 0 never clobbered" rule. Tier 0 remains available for explicit sticky locks, but normal review-driven overrides are now conditional by default.

Decisions:

- Review-generated overrides use `mode = until_newer_evidence` by default.
- Each such override stores a `baseline` copied from the field resolution that the reviewer overrode:
  - `evidence_date`
  - `collected_at`
  - `source_tier`
  - `source_type`
  - `evidence_ids`
  - `rule_applied`
- During resolution, the field first computes the normal evidence winner.
- If the current winning evidence is not newer than the override baseline, the override still wins.
- If the current winning evidence is newer than the override baseline, the override yields and the newer evidence wins.
- Comparison uses the same ordering tuple as field resolution:
  1. `evidence_date`
  2. `collected_at`
  3. `source_tier`
- Historical supersession design: once an `until_newer_evidence` override lost to newer evidence during `resolve_project(apply=True)`, that field override would be removed and emit a supersession flag once, not on every future resolve.

Backward compatibility:

- Legacy override payloads without `mode` / `baseline` are treated as sticky.

### 21a. STATUS_CHANGE Rejection Semantics

Rejecting a status-change review item should block the same evidence from re-applying, but it must not block genuinely newer evidence.

2026-04-27 implementation note: §22 supersedes the silent-supersession parts of this section. A rejected status still writes a researcher override, but newer contradictory evidence now opens an `override_contradiction` review item instead of clearing the override or emitting `researcher_override_superseded`.

Decisions:

- Rejecting a `STATUS_CHANGE` item writes a conditional override for `pipeline_status` using the review item's prior status as the override value.
- The override baseline is copied from the current status field resolution at reject time.
- The workflow immediately re-runs `resolve_project()` after writing the override so the project reverts in the same transaction.
- If the review item is stale and the current status resolution no longer matches the rejected candidate, no override is written.
- When newer evidence later contradicts the reviewer-selected status, the resolver emits an `override_contradiction` review item so the researcher can accept the new value, keep the old value, defer, or enter a custom value.

### 21b. Observation Ordering Refinement

The shared observation ordering now explicitly follows "most recent wins" semantics before source preference:

1. `evidence_date`
2. `collected_at`
3. source-specific priority preference
4. `source_tier`

This closes the earlier developer-resolution bug where an older high-priority source could beat a newer lower-priority source. Re-running `resolve-all` may therefore shift some resolved developer values toward newer evidence.

### 21c. Delivery-Date Override Provenance

When `date_delivery` is currently supplied by a researcher override:

- `project.date_delivery` still stores the override date
- `project.delivery_year_provenance` is set to `researcher_override`

If the override is explicitly cleared or an `override_contradiction` accept-new decision clears it, provenance returns to the winning evidence-derived value such as `explicit_government`, `explicit_tcg`, `explicit_news`, `explicit_costar`, or `estimated_calc`. Newer evidence alone no longer silently supersedes delivery-date overrides.

### 21d. Delivery-Estimate Fill Policy

**Date:** 2026-04-23

Phase A validation accepted `estimated_calc` as a valid blank-filler for `date_delivery` when the project is in `Proposed`, `Pending`, or `Approved` and no explicit date is available.

Decisions:

- `estimated_calc` may populate a previously null `project.date_delivery`.
- The resolved date remains explicitly tagged by `project.delivery_year_provenance = estimated_calc`.
- Downstream consumers may filter, down-weight, or visually flag these dates based on provenance without changing the core resolution rule.

Rationale:

- The Phase A review packet spot-checked 10 representative null-to-estimate rows and found the formula sane enough to accept the full 218-row bucket.
- Carrying a low-confidence estimate is more useful than leaving the field blank, as long as provenance stays explicit.

### 21e. Small-Delta Units Policy

**Date:** 2026-04-23

Phase A validation accepted small `total_units` changes automatically and held larger deltas for researcher review.

Decisions:

- Any source may overwrite `project.total_units` when `abs(resolved_value - current_value) <= 5`.
- Larger deltas require researcher review before apply.
- The preferred hold primitive is a `researcher_override` reviewed through §22 semantics: the held value remains current, while genuinely newer contradictory evidence opens an `override_contradiction` review item.

Rationale:

- Small unit-count differences usually behave like measurement or source-format noise.
- Larger changes often signal project-identity ambiguity, phase splits, or different source scoping.

### 21f. Article Evidence Priority For Delivery Dates

**Date:** 2026-04-23

This is a forward-looking rule for the Phase D article collector.

Decisions:

- When article evidence exists for `date_delivery` and the article is dated within the last 6 months, that article evidence outranks CoStar for the `date_delivery` field.
- This is a field-specific source-priority override for `date_delivery` only.
- It does **not** change the general source-tier hierarchy for other fields.

Rationale:

- Articles often capture operator-stated timeline updates before automated source refreshes catch up.
- Treating this as a delivery-date-specific override preserves the general evidence model while still reflecting how timeline updates appear in practice.

### 21f-bis. News Delivery Phrase Canonicalization

**Date:** 2026-04-29

Phase D structural extraction canonicalizes imprecise article delivery phrases to
stable synthetic dates before the LLM extraction pass sees them.

Decisions:

- Quarter phrases use the midpoint month of the quarter: Q1 -> February 1,
  Q2 -> May 1, Q3 -> August 1, Q4 -> November 1.
- Seasonal phrases use midpoint months: spring -> April 1, summer -> July 1,
  fall -> October 1, winter -> January 1 of the stated year.
- Timing phrases use coarse period markers: early -> March 1, mid -> July 1,
  late -> November 1.
- Bare weekday/date phrases are parsed relative to the article publication date
  when available, not the worker runtime date.

Rationale:

- The canonical values are anchors for extraction and review, not claims that
  the source stated an exact day.
- Stable article-relative parsing keeps re-extraction deterministic.

### 21g. Developer Override Protection During Canonicalization Apply

**Date:** 2026-04-23

Phase A apply surfaced a case where `canonicalize-developers --apply` rewrote a project developer value that was already protected by a researcher override.

Decisions:

- `canonicalize-developers --apply` may continue to manage registry rows and aliases, but it must not rewrite `project.developer` when `researcher_overrides` contains an active `developer` entry.
- If the project later needs to reconcile that field, the resolution engine remains the source of truth because it understands override semantics and supersession.

Rationale:

- Project-level developer overrides are part of the evidence-layer review workflow and must not be bypassed by registry-maintenance sweeps.
- Registry cleanup and project-field mutation are related but distinct responsibilities.

### 22. Review-Protected Overrides (Supersedes §21 conditional-override model)

**Date:** 2026-04-23

During the Phase B/C UI design sessions, a cleaner override model emerged that supersedes the earlier sticky / `until_newer_evidence` split in §21. The new rule is referred to throughout `docs/specs/ui_requirements.md` and the UI design as **review-protected**.

#### 22.1 The rule

Researcher inputs are **review-protected, not sticky, and not silently-replaceable**.

- A researcher override (inline field edit, review-queue Keep-old decision, Custom-value decision, or any other mechanism that records a human-set value for a field) writes a value that becomes the project's current value.
- The override **does not auto-expire.**
- The override **does not silently yield to newer evidence.**
- When newly arriving evidence contradicts the override, a review item is generated at minimum MEDIUM priority.
- Until that review item is decided, the override's value remains displayed as current.

This supersedes both prior modes:

- `sticky` (old "Tier 0 never clobbered") — too rigid; human inputs rotted silently without recheck.
- `until_newer_evidence` (§21) — too permissive; newer evidence silently superseded without the researcher knowing.

The new model makes every human decision a standing watch: the decision holds, but the system proactively surfaces contradictions instead of either ignoring or hiding them.

#### 22.2 What counts as contradiction

The contradiction threshold varies by field to avoid spurious review items for insignificant differences.

| Field | Contradiction definition |
|---|---|
| `pipeline_status` | Any evidence implying a different resolved status value. |
| `total_units` | Any evidence with a different value AND `abs(delta) > 5`. Deltas ≤ 5 do not contradict (per §21e small-delta policy). |
| `affordable_units`, `workforce_units`, `market_rate_units` | Same threshold as `total_units` (`abs(delta) > 5`). |
| `developer` | Any evidence with a different string after canonicalization. Identical canonicals do not contradict. |
| `product_type` | Explicit disagreement. |
| `age_restriction` | Explicit disagreement. |
| `date_delivery` | Any explicit date more than 30 days different, OR any article within 6 months (per §21f) suggesting a different delivery. |

For other fields without explicit thresholds above, contradiction = any explicit disagreement.

#### 22.3 Priority escalation

The priority of the contradiction review item reflects the strength of the contradicting evidence:

- **HIGH** — Strong Tier 1 evidence (government source with real dates and substantive content), multi-source agreement (≥2 sources converge on a different value), or delta exceeds a large magnitude threshold (e.g., unit delta > 50).
- **MEDIUM** (minimum) — Single source, any tier, contradicting an existing override. Never LOW: contradicting a human decision is never low-priority, even if the evidence is weak.

#### 22.4 Display

- Fields with active overrides are displayed normally with their override value as the current value.
- The field's source badge reflects the overrider (`You` / `NG` / etc.).
- Hovering the field reveals override metadata: set-by, set-at, note.
- When a contradiction review item is pending for a field, the field gets the "in current review batch" highlight (see `ui_requirements.md` §6.4).

#### 22.5 Clearing an override

- Researchers can clear an override explicitly via the Project Detail → Overrides tab, or via the field-hover controls.
- Clearing triggers a resolution re-run for the project.
- The cleared override is logged in ChangeLog.
- An override cleared by the researcher who set it, or by another TCG researcher, produces the same audit trail — attribution is preserved.

#### 22.6 Interaction with the Review Queue

- When new evidence contradicts an existing override, a review item is inserted into the Review Queue like any other change. The row is rendered with the `⚠ contradicts your override [set-date]` inline warning.
- The researcher has the standard options: Accept new, Keep old (re-affirms the override with a new baseline timestamp), Defer, or Custom.
- Accepting new evidence clears the override automatically; the accepted value becomes the resolved (non-override) value.
- Keep-old re-affirms the override. Its set-at timestamp updates to reflect the re-affirmation.

#### 22.7 Backward compatibility

- Existing overrides with `mode = sticky` are treated as review-protected (equivalent to the new default). Their baselines, if any, are preserved but no longer used as "yield thresholds."
- Existing overrides with `mode = until_newer_evidence` are treated as review-protected. Their baselines are preserved for audit but are no longer the mechanism by which new evidence wins — new contradicting evidence now always generates a review item regardless of baseline comparison.
- C.c promoted overrides into a `researcher_overrides` table for auditability and per-field indexing, and C.tail.2 removed the previous `Project.researcher_override` JSONB payload.
- The `mode` field may continue to exist in override payloads for audit purposes but no longer determines resolution outcomes.

#### 22.8 Interaction with STATUS_CHANGE rejection (§21a)

§21a remains valid in its mechanics — rejecting a STATUS_CHANGE writes a `pipeline_status` researcher override. Under §22, that override is review-protected:

- It blocks the rejected evidence from re-applying.
- It does not block newer evidence from generating future review items.
- Instead of emitting a one-shot `researcher_override_superseded` flag when newer evidence wins, the resolution engine now always emits a review item for the contradiction, allowing the researcher to re-affirm or accept.

#### 22.9 Rationale

- Pipedream's historical pattern (sticky human inputs) had a silent-rot failure mode: inputs from 2023 stayed authoritative even when reality changed.
- The §21 conditional model fixed the silent-rot problem but introduced a silent-overwrite problem: newer evidence won without the researcher knowing their input had been superseded.
- §22 eliminates both silent failures by surfacing every override-contradiction as an explicit review decision. The researcher maintains authority, and the system maintains audit visibility.

#### 22.10 Implementation scope

- C.i added `tcg_pipeline.review.contradictions` as the first-class contradiction service. It is invoked during `resolve_project(apply=True)` and exposes a batch `detect_contradictions(project_ids)` entrypoint for direct/deferred evidence ingest and backfill paths. The `detect-contradictions` CLI provides dry-run/apply audits before large resolve/backfill operations. See `docs/specs/data_model_changes.md` for the `ReviewItem` extension.
- `review_workflow.py` accepts new, keep-old, defer, and custom decisions for contradiction rows. The old `researcher_override_superseded` flag path has been replaced by `override_contradiction` review item emission.
- UI surfaces the review items as normal queue entries with the `⚠ contradicts your override` warning.

#### 22.11 System-authored regression overrides

The review-protected rule above applies to human/researcher-authored overrides. The
regression-handling agent may also write a system-authored `pipeline_status`
override when a `status_regression_candidate` is auto-accepted. These rows are
distinct from researcher overrides:

- `set_by = agent.status_regression_candidate`
- `mode = until_newer_evidence`
- the baseline is the evidence frontier that supported the auto-accepted regression
- the linked review item and `change_log` row use `auto_accepted` audit markers

System-authored regression overrides are intentionally allowed to yield to newer
evidence without creating an override-contradiction review item. When a fresher
status signal wins, the resolver clears the system-authored override and the
newer evidence-derived status becomes current. The supersession remains
auditable through `resolution_log.metadata.superseded_override`. This is a
narrow exception to section 22: it does not apply to inline researcher edits,
Keep-old decisions, Custom decisions, or any other human-authored override. Those
remain review-protected and require an explicit contradiction review before they
can be replaced.

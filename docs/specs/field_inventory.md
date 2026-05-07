# Field Inventory — Classification for UI Write Paths

Updated: 2026-04-24

This document classifies every Pipedream field (and related project-level fields) into one of five classes. The class determines the UI write path and visual treatment per `docs/specs/ui_requirements.md` §14.

**Prerequisite status:** This is a **Phase B prerequisite**. The classifications below were audited against `src/tcg_pipeline/db/models.py` on 2026-04-24. Any future schema additions should update this file in the same PR as the migration/model change.

**Audit task:**

1. Read the classification table below.
2. Open `src/tcg_pipeline/db/models.py` alongside.
3. For each row: confirm the class, the write path, and whether the field still exists on `Project`. Update as needed.
4. Add any Project fields not in this table (the inventory grew past the original 81 Pipedream fields).
5. Commit with a note in this file's header recording the audit date + auditor.

---

## 1. Classification Scheme

| Class | Write path | UI behavior | Resolution engine involvement |
|---|---|---|---|
| **Evidence-derived** | Inline edit creates a `researcher_override` row. | Blue user badge appears on the field. `⚠ contradicts your override` review items generated on new contradicting evidence. Hover shows override metadata. | Yes — engine owns the field today and override is consulted via `resolve_project`. |
| **Source-populated direct** | Read-only for MVP. Later either promote to Evidence-derived or teach source ingesters to respect direct-field overrides. | Source badge and evidence/source hover still display. Edit control is disabled with a tooltip: "Managed by source updates in MVP." | Not currently resolver-owned; collectors/ingesters can write the project column directly. |
| **Researcher-authored** | Inline edit writes directly to the project row via the FastAPI. ChangeLog entry created. | User badge on field. No override semantics; no review item generation. | No — field is outside the resolution engine's scope. |
| **Relationships** | Relationship picker opens (not inline text edit). Writes to `project_relationships` / `project_identifiers` / `status_history` / `jurisdictions` / etc. | Separate section or picker UI; no inline text editing. | No (except where relationship changes trigger re-resolution, e.g., phase linking may affect confidence). |
| **Computed** | Not editable. Source badge shows `—` or system-specific marker. | Read-only. Hover may show the derivation rule and inputs. | Produced by the engine or by DB columns; UI displays, never writes. |

---

## 2. Pipedream Field Classification (81 fields)

Ordered by the `ARCHITECTURE.md` §3d groupings.

### 2.1 Identity & Location (11 fields)

| Pipedream Field | DB Field | Class | Notes |
|---|---|---|---|
| ProjectID | `ProjectIdentifier.tcg_pipedream_id` | Relationship | FK record in `project_identifiers`, type `tcg_pipedream_id`. Not edited directly. |
| Name | `project_name` | Researcher-authored | Human-named project. Edits write directly. |
| Developer | `developer` | Evidence-derived | Contradicted by news, CoStar, developer websites. Governed by canonicalization. |
| Address | `canonical_address` (derived) + `raw_addresses[]` (stored) | Split | `canonical_address` is **Computed** (normalizer output). `raw_addresses[]` is **Researcher-authored** — edit a raw address, canonical auto-recomputes. |
| State | `state` | Researcher-authored | Location metadata. Rarely changes. |
| County | `county` | Researcher-authored | |
| City | `city` | Researcher-authored | |
| Zip | `zip` | Researcher-authored | |
| Region | `tcg_region` | Researcher-authored | TCG submarket classification. |
| Lat | `lat` | Researcher-authored | Geocoded but user-overridable. |
| Long | `lng` | Researcher-authored | |

### 2.2 Project Details (16 live fields + 1 planned field)

| Pipedream Field | DB Field | Class | Notes |
|---|---|---|---|
| RentFS | `rent_or_sale` | Source-populated direct | CoStar/Pipedream provide this, but the resolution engine does not own it yet. Read-only for MVP. |
| MRUnits | `market_rate_units` | Evidence-derived | §22.2 contradiction threshold: delta > 5. |
| AffUnits | `affordable_units` | Evidence-derived | §22.2 contradiction threshold: delta > 5. Allowlisted sources only (LAHD, Pipedream, SM Dev Tracking, news). |
| WorkforceUnits | `workforce_units` | Evidence-derived | Added in AGENT.2 step 6. Workforce units are a component of total units, distinct from both affordable and market-rate units. Default is NULL when unknown. |
| TotUnits | `total_units` | Evidence-derived | §22.2 contradiction threshold: delta > 5. |
| Acres | `acres` | Source-populated direct | Public records + CoStar. Read-only for MVP. |
| RetailSF | `retail_sf` | Source-populated direct | CoStar/Pipedream. Read-only for MVP. |
| OfficeSF | `office_sf` | Source-populated direct | CoStar/Pipedream. Read-only for MVP. |
| HKeys | `hotel_keys` | Source-populated direct | CoStar/Pipedream. Read-only for MVP. |
| ProdType | `product_type` | Evidence-derived | |
| Elevation | `stories` | Source-populated direct | Public records / CoStar / Pipedream. Read-only for MVP. |
| Senior | `age_restriction` | Evidence-derived | |
| PercS | `pct_studio` | Source-populated direct | Unit-mix detail. Read-only for MVP. |
| Perc1B | `pct_1bed` | Source-populated direct | Unit-mix detail. Read-only for MVP. |
| Perc2B | `pct_2bed` | Source-populated direct | Unit-mix detail. Read-only for MVP. |
| PercOther | `pct_other_bed` | Source-populated direct | Unit-mix detail. Read-only for MVP. |
| PercBedSum | (validation only, not stored) | Computed | Sum validation; not persisted. |

### 2.3 Status & Dates (9 fields)

| Pipedream Field | DB Field | Class | Notes |
|---|---|---|---|
| CurrStatus | `pipeline_status` | Evidence-derived | Core resolution field. Forward-only progression with evidence-type gating. |
| CurrStatusDate | `status_date` | Evidence-derived | Comes from evidence dates via status_date resolver. |
| DeliveryDate | `date_delivery` | Evidence-derived | §22.2: contradiction if explicit date > 30 days different OR article within 6 months disagrees. |
| PStat1-6 | `status_history` rows | Relationship | Stored in the `status_history` child table; appended by resolution engine on status change. |
| PStatDate1-6 | `status_history.status_date` | Relationship | |

### 2.4 Jurisdiction & Reference (3 fields)

| Pipedream Field | DB Field | Class | Notes |
|---|---|---|---|
| Jurisdiction | `jurisdiction_id` (FK to `jurisdictions`) | Relationship | Per `data_model_changes.md` §2, jurisdictions are a first-class table. Auto-populatable by ZIMAS/LAHD. Can also be manually set via a picker. |
| RefNum | `ProjectIdentifier.case_number` | Relationship | FK record in `project_identifiers`. |
| APN | `ProjectIdentifier.apn` | Relationship | Stored as an identifier record, not a scalar `Project.apn` column. |

### 2.5 Planner / Contact Info (8 fields)

| Pipedream Field | DB Field | Class | Notes |
|---|---|---|---|
| Plan1Name | `planner_1_name` | Researcher-authored | Staff contact info; human-entered. ZIMAS could auto-populate in future. |
| Plan1City | `planner_1_city` | Researcher-authored | |
| Plan1Email | `planner_1_email` | Researcher-authored | |
| Plan1Phone | `planner_1_phone` | Researcher-authored | |
| Plan2Name | `planner_2_name` | Researcher-authored | |
| Plan2City | `planner_2_city` | Researcher-authored | |
| Plan2Email | `planner_2_email` | Researcher-authored | |
| Plan2Phone | `planner_2_phone` | Researcher-authored | |

### 2.6 Notes & Sources (7 fields)

| Pipedream Field | DB Field | Class | Notes |
|---|---|---|---|
| Notes | `researcher_notes` → `project_notes` child table | Researcher-authored | Append-only under the new notes model (`data_model_changes.md` §10). |
| Site1 | `source_urls[]` | Researcher-authored | |
| Site2 | `source_urls[]` | Researcher-authored | |
| Site3 | `source_urls[]` | Researcher-authored | |
| Site4 | `source_urls[]` | Researcher-authored | |
| PersonalNotes | `personal_notes` → `project_notes` child table | Researcher-authored | Append-only. |
| ChangeNotes | `change_notes` → `project_notes` child table | Researcher-authored | Append-only. |

### 2.7 Project Relationships (9 fields)

| Pipedream Field | DB Field | Class | Notes |
|---|---|---|---|
| PrevName1 | `previous_names[]` | Researcher-authored | |
| PrevName2 | `previous_names[]` | Researcher-authored | |
| CorrP | `project_relationships (duplicate)` | Relationship | Picker UI to select the canonical project. |
| PCPart | `project_relationships (counterpart)` | Relationship | |
| RelP1 | `project_relationships` | Relationship | |
| RelP2 | `project_relationships` | Relationship | |
| RelP3 | `project_relationships` | Relationship | |
| RelP4 | `project_relationships` | Relationship | |
| RelP5 | `project_relationships` | Relationship | |
| RelP6 | `project_relationships` | Relationship | |

### 2.8 Workflow / Admin (10 fields)

| Pipedream Field | DB Field | Class | Notes |
|---|---|---|---|
| Complete | (internal workflow, not migrated) | — | Not migrated. Phase A decision. |
| Editor | `last_editor` | Computed | Set automatically on any update. |
| EditDate | `last_edit_date` | Computed | Set automatically. |
| NewEntry | (internal workflow) | — | Not migrated. |
| Import | `import_source` | Researcher-authored | Provenance tag for imports. |
| ImportDate | `import_date` | Computed | Set automatically on import. |
| UpComplete | (internal workflow) | — | Not migrated. |
| UpCompleteDate | (internal workflow) | — | Not migrated. |
| NewPID | (migration only) | — | Not migrated. |
| EditLog | (internal workflow) | — | Not migrated. |

---

## 3. Additional Project Fields (Not From Pipedream)

These exist on the `Project` schema but are not part of the 81-field Pipedream inventory. Include in the UI as appropriate.

### 3.1 Identity (non-Pipedream)

| DB Field | Class | Notes |
|---|---|---|
| `id` | Computed | UUID primary key. |
| `created_at` | Computed | |
| `updated_at` | Computed | |
| `created_by` | Computed | User who created the project (seed, collector, manual). |
| `market` | Relationship | Legacy market slug retained during migration. Future UI should use `market_id` / `markets.slug` once B.0b lands. |
| `location` | Computed | PostGIS point derived from `lat` / `lng`. |
| `geocode_confidence` | Computed | Generated by ingesters/geocoder from coordinate quality; not user-editable. |

### 3.2 Source-populated descriptive fields

| DB Field | Class | Notes |
|---|---|---|
| `applicant` | Source-populated direct | From LADBS permit applicant fields. Read-only for MVP. |
| `description` | Source-populated direct | From permit/work description or source detail. Read-only for MVP. |

### 3.3 CoStar-originated fields

| DB Field | Class | Notes |
|---|---|---|
| `costar_id` | Relationship | FK via `project_identifiers`. |
| `costar_submarket` | Source-populated direct | From CoStar exports. Read-only for MVP. |
| `total_sf` | Source-populated direct | From CoStar RBA. Read-only for MVP. |
| `parking_spaces` | Source-populated direct | From CoStar exports. Read-only for MVP. |
| `style` | Source-populated direct | From CoStar exports. Read-only for MVP. |
| `property_type` | Source-populated direct | Raw CoStar property type. Read-only for MVP. |
| `affordable_type` | Source-populated direct | From CoStar exports. Read-only for MVP. |
| `owner` | Source-populated direct | Property owner, often from CoStar or public records. Read-only for MVP. |
| `true_owner` | Source-populated direct | From CoStar exports. Read-only for MVP. |
| `architect` | Source-populated direct | From CoStar exports. Read-only for MVP. |
| `zoning` | Source-populated direct | From ZIMAS, LADBS, or CoStar. Read-only for MVP. |

### 3.4 Resolution engine outputs

| DB Field | Class | Notes |
|---|---|---|
| `confidence` | Computed | Resolution engine output. |
| `status_confidence` | Computed | Dual-write alias during transition. |
| `likelihood` | Computed | Resolution engine output. |
| `likelihood_breakdown` | Computed | JSONB with component contributions. |
| `confidence_reason` | Computed | Human-readable explanation. |
| `last_evidence_date` | Computed | Max `evidence_date` over evidence rows for this project. |
| `delivery_year_provenance` | Computed | `explicit_government` / `explicit_news` / `explicit_costar` / `estimated_calc` / `researcher_override`. |
| `status_source` | Computed | Source label attached to current status resolution/source update. |

### 3.5 Source-populated lifecycle / planning fields

| DB Field | Class | Notes |
|---|---|---|
| `date_construction_start` | Source-populated direct | Currently sourced from CoStar / source updates, not resolver-owned. Read-only for MVP. |
| `entitlement_type` | Source-populated direct | Future planning-source detail. Read-only for MVP. |
| `appeal_status` | Source-populated direct | Future planning-source detail. Read-only for MVP. |
| `ceqa_status` | Source-populated direct | Future planning-source detail. Read-only for MVP. |

### 3.6 Researcher workflow flags

| DB Field | Class | Notes |
|---|---|---|
| `inclusion_in_analysis` | Researcher-authored | Toggle. Sticky across resolution runs (per ROADMAP C.m). |
| `inclusion_in_exhibit` | Researcher-authored | Toggle. Sticky. |
| `inclusion_note` | Researcher-authored | Free text explaining why the project is included/excluded. |
| `last_reviewed_by` | Computed | Written by review workflow. UI displays but does not edit directly. |
| `last_reviewed_date` | Computed | Written by review workflow. UI displays but does not edit directly. |

### 3.7 Researcher override storage

| DB Field | Class | Notes |
|---|---|---|
| `researcher_overrides` | Child table | Active override storage (`data_model_changes.md` §6.4). The legacy `projects.researcher_override` JSONB column was retired in C.tail.2. |

### 3.8 Additional identifiers and relationships

| Source | Storage | Class | Notes |
|---|---|---|---|
| LADBS PCIS ID | `project_identifiers` | Relationship | Established by matcher during collection. |
| ZIMAS case number | `project_identifiers` | Relationship | |
| LADBS permit IDs | `project_identifiers` | Relationship | |

---

## 4. Summary Counts

Target distribution across the 81 Pipedream fields:

- **Evidence-derived**: ~9 fields (status/status date, unit counts, product type, age restriction, delivery date, developer)
- **Source-populated direct**: ~27 fields (rent/sale, physical attributes, unit mix, CoStar submarket, owner, zoning, applicant/description, lifecycle/planning details)
- **Researcher-authored**: ~30 fields (notes, source URLs, planner contacts, location metadata, previous names, etc.)
- **Relationships**: ~15 fields (ProjectID, RefNum, status history, project relationships, jurisdiction)
- **Computed**: ~10 fields (last editor, edit date, import date, validation-only fields, workflow state)
- **Not migrated**: ~5 fields (internal workflow markers)

---

## 5. Write Path Summary

Each class has a specific write path the UI must use:

### 5.1 Evidence-derived

```
User inline-edits a Core field on Project Detail
  → UI calls FastAPI POST /override/set
  → API writes a row to researcher_overrides (project_id, field_name, value, user, timestamp)
  → API calls resolve_project(project_id, apply=True)
  → Resolution engine reads the override, applies it as the current value
  → Engine schedules contradiction detection against existing evidence
  → ChangeLog entry written
```

### 5.2 Source-populated direct

```
User cannot edit in MVP.
  -> UI displays the current value, source badge, and source/evidence hover where available
  -> Edit affordance is disabled
  -> Future Phase C/E choice: promote field to resolver-owned Evidence-derived, or make ingesters respect field-level overrides
```

### 5.3 Researcher-authored

```
User inline-edits a researcher-authored field
  → UI calls FastAPI POST /project/{id}/field
  → API writes directly to projects.{field_name}
  → ChangeLog entry written
  → No override semantics, no contradiction detection
```

### 5.4 Relationships

```
User clicks relationship picker (e.g., "link phase sibling")
  → UI opens project search modal
  → User selects target project
  → UI calls FastAPI POST /project/{id}/relationship
  → API writes to project_relationships table
  → Both projects' resolution may re-run if the relationship affects confidence
  → ChangeLog entries on both affected projects
```

### 5.5 Computed

```
User cannot edit. Field is read-only in the UI.
Source badge shows `—`.
Hover shows the derivation rule or evidence ids.
```

### 5.6 Append-only notes (special case within Researcher-authored)

```
User adds a note in a Notes section
  → UI calls FastAPI POST /project/{id}/note
  → API writes a new row to project_notes (project_id, note_type, author, text, timestamp)
  → Previous notes remain
  → Latest note displayed inline; full history via hover
```

---

## 6. UI Display Hints

Guidance for the frontend when rendering each class:

- **Evidence-derived fields** get a visible source badge reflecting the winning evidence's source type. If overridden, the badge shifts to `You` / `NG` with override metadata in hover.
- **Source-populated direct fields** get a visible source badge when provenance is known, but are disabled for editing in MVP so source refreshes do not silently overwrite researcher work.
- **Researcher-authored fields** get a source badge reflecting the author (`You` / `NG` / `Pipedream` for legacy values).
- **Relationships** render as linked project names, clickable to navigate to the linked project's detail view.
- **Computed fields** render with a `—` badge and read-only styling (italic gray text or dashed border).

---

## 7. Audit Findings

Findings from the 2026-04-24 audit:

1. **`raw_addresses[]` vs `canonical_address` edit path confirmed.** Both fields exist. `canonical_address` stays Computed; users edit raw address strings and the server re-normalizes.
2. **`delivery_year` is not a live `Project` column.** The live schema has `date_delivery` plus `delivery_year_provenance`. Keep `date_delivery` Evidence-derived and `delivery_year_provenance` Computed.
3. **Source-populated direct exact list expanded.** Added missing direct-source fields from `models.py`, `ingesters/costar.py`, `ingesters/pipedream.py`, and LADBS source adapters: `applicant`, `description`, `total_sf`, `parking_spaces`, `style`, `property_type`, `affordable_type`, `architect`, `date_construction_start`, `entitlement_type`, `appeal_status`, and `ceqa_status`.
4. **`inclusion_in_analysis` / `inclusion_in_exhibit` exist.** They remain Researcher-authored and are wired in Phase C.m through the Project Detail inclusion panel.
5. **Planner fields exist.** Preserve as Researcher-authored for now because they are in the Pipedream inventory. Dropping them would be a product decision, not a schema-audit correction.

No unclassified live `Project` columns remain after this audit.

---

## 8. Audit Log

| Date | Auditor | Changes |
|---|---|---|
| 2026-04-23 | Claude (initial scaffold) | Pre-filled classifications from ARCHITECTURE.md §3d and ROADMAP context. Not yet verified against the live schema. |
| 2026-04-24 | Codex | Audited against `src/tcg_pipeline/db/models.py`, `ingesters/costar.py`, `ingesters/pipedream.py`, and `source_adapters/ladbs.py`. Added missing live `Project` fields and resolved audit questions. |

---

## 9. Cross-References

- `docs/specs/ui_requirements.md` §14 — how each class renders in the UI.
- `docs/specs/data_model_changes.md` — schema changes required (jurisdictions, project_notes, researcher_overrides table).
- `docs/specs/EVIDENCE_LAYER_DECISIONS.md` §22 — override semantics driving the Evidence-derived write path.
- `ARCHITECTURE.md` §3d — the original 81-field inventory.
- `ARCHITECTURE.md` §3e — master project record schema.
- `src/tcg_pipeline/db/models.py` — the live SQLAlchemy models.

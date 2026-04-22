# Prompt for Claude Code / Codex

Copy everything below this line and paste it as your prompt:

---

We are making a significant architectural shift to the TCG Pipeline Tracker. We are adding an **evidence layer** — an append-only evidence store where all incoming data is captured as immutable evidence rows, and the Project record becomes a derived view computed by per-field resolution rules.

A comprehensive integration guide has been written that explains:
- What the codebase currently looks like (full audit of what's built)
- What the evidence layer is and why we're adding it
- The exact schema changes needed
- The resolution engine design (per-field rules for status, units, product_type, delivery_year, age_restriction, developer)
- The likelihood engine
- How the current architecture maps to the target architecture
- A phased retrofit plan (5 phases, incremental, non-breaking)
- Which files need to change and what new files are needed

**Read this file carefully:**

```
docs/specs/EVIDENCE_LAYER_INTEGRATION_GUIDE.md
```

After reading it thoroughly, I need you to:

1. **Confirm you understand the current codebase structure** — especially `db/models.py`, `db/collect.py`, `db/seed.py`, `matching/matcher.py`, `status_rules.py`, and how data flows from collectors through persistence.

2. **Confirm you understand the target architecture** — evidence as append-only, Project as computed view, per-field resolution rules, the role of the resolution engine.

3. **Ask me any clarifying questions** before we start implementing. Things you might want to clarify:
   - Anything ambiguous in the field resolution rules
   - Edge cases you're unsure about
   - Ordering decisions within a phase
   - How to handle backward compatibility for tests
   - Whether to start with Phase 1 (schema) or Phase 2 (resolution engine logic) first
   - How the backfill should handle data that doesn't cleanly map

**No code yet.** I want to make sure we're aligned before any implementation begins. Read the guide, think it through, and come back with questions.

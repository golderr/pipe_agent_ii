# Reviewer Usefulness Review — Runbook

> **What this is:** the operational runbook for the post-deployment automated review of the news extraction expansion's Primary → Verifier → Arbiter pipeline. The scheduled remote agent reads this document at run time as its source of truth — when this doc changes, the next scheduled review uses the new thresholds, queries, and escalation rules without redeployment.
>
> **Why it exists:** decision recorded 2026-05-18 — reviewer usefulness is automated, not memory-dependent. Human turnover or developer attention drift should not silently degrade the threshold-enforcement check on this pipeline.
>
> **Maintained by:** Nate Goldstein + Claude Code. Updates land via PR.
> **Last updated:** 2026-05-18.

---

## 1. When this review fires

**Schedule:** self-arming weekly cron (Mondays 09:00 America/Los_Angeles); switches to monthly cadence after 4 successful "deployed and threshold-checked" runs.

**Arming trigger:** the scheduled agent skips silently on every run until the git tag `cycle1-news-extraction-v1` exists on the `main` branch. The tag is pushed at the moment Chunks 6/7/8 of the news extraction expansion (`D.EXP.6` / `D.EXP.7` / `D.EXP.8`) actually go live in production. From that moment forward, the next scheduled run executes the full review.

**Cadence-state tracking:** the agent reads `docs/review_logs/` to count prior review reports. If `count < 4` → weekly mode. If `count >= 4` → monthly mode (run only when last report is ≥ 28 days old).

**Skip behavior:** when the agent skips (tag not pushed, or monthly mode but last run too recent), it writes a one-line log entry to a `docs/review_logs/skipped.log` file but does NOT open an issue or send email.

---

## 2. What this review measures

Three **primary metrics** (gate thresholds) plus three **drift signals** (early warning).

### 2.1 Primary metrics (target thresholds in parens)

1. **Arbiter-final acceptance rate (≥ 80%)** — of audit-target fields where the arbiter produced a final value AND a human reviewer subsequently saw the value, what fraction did the human accept without override? Below 80% means the arbiter is making decisions humans reject — either the arbiter prompt needs tuning or the trigger threshold is firing arbiter on cases it shouldn't.

2. **Verifier precision on flags (≥ 90%)** — of verifier `unsupported` / `contradicted` calls (i.e., flags against the primary extractor), what fraction did the human reviewer agree with? Below 90% means the verifier is over-flagging — wasting arbiter cycles and reviewer time on non-issues.

3. **Verifier recall on `missing_but_stated` (≥ 70%)** — of cases where a human reviewer caught a primary-extractor gloss-over, what fraction had the verifier ALSO flagged as `missing_but_stated`? Below 70% means the verifier is missing the same things the primary missed — verifier prompt needs strengthening, or the closed audit doesn't cover enough of the field where the gloss-over happened.

### 2.2 Drift signals (early-warning, no hard threshold)

4. **Per-target `not_stated` rate, week-over-week** — for each of the 14 audit targets, what fraction of articles return `not_stated`? Sustained increase in any single target's `not_stated` rate suggests the primary prompt is retreating on that field. Flag when WoW change > +15 percentage points sustained across 2 reviews.

5. **Arbiter invocation rate** — fraction of articles where the arbiter fires at least once. If this climbs > 30%, either the verifier is over-flagging (see metric 2) or the primary is degrading. Cross-reference with primary-trustworthiness signal trend.

6. **Per-article arbiter cost** — should stay within the budget cap configured in `D.EXP.5A`. Spikes > 2× the cap indicate prompt drift or pathologically ambiguous article batches.

---

## 3. Where to look in the schema

The agent formulates queries against the live schema at run time. The structural hooks it uses:

- **`ChangeLog`** filtered to `source = 'news_article'` with `reviewed_by` populated → reviewer dispositions on news-derived field changes. `field`, `old_value`, `new_value`, `reviewed_by`, `reviewed_by_email`, `timestamp` give the per-decision record needed for metric 1.

- **`NewsProjectReference.candidate_field_audit`** (Primary's 14-key audit) + **`verifier_audit`** (Verifier's 5-state output) + **`arbiter_audit`** (Arbiter chain when invoked) — the audit trail per audit target needed for metrics 2 and 3 and signals 4 and 6.

- **`ReviewItem`** with `item_type` in (`LOW_CONFIDENCE`, `OVERRIDE_CONTRADICTION`, `MULTI_TENURE_REVIEW`, `STATUS_CHANGE`, `STATUS_REGRESSION_REVIEW`) → human dispositions on news-triggered review items. Cross-reference with `NewsProjectReference` via `review_item_id`.

- **`AgentRun`** filtered to news arbiter runs → arbiter invocation counts and per-run cost for signals 5 and 6.

- **`NewsArticle`** — the article-level denominator for signal 5 (arbiter invocation rate).

**Window**: by default the review computes metrics over the **last 7 days** in weekly mode, **last 28 days** in monthly mode. For metric 3 specifically, the agent needs reviewer-corrected gloss-overs — those land as `ChangeLog` rows where `old_value` was null/missing but the reviewer set a value sourced from the article. The agent uses the `NewsProjectReference` cross-reference to confirm the source article and check the verifier_audit for that field.

---

## 4. Decision rules — what to do per outcome

### 4.1 All thresholds pass

Write `docs/review_logs/reviewer_usefulness_YYYY-MM-DD.md` with the report. Do NOT open an issue. Do NOT send email. Schedule the next run per cadence.

### 4.2 One or two primary metrics below threshold, no drift signals firing

- Write the dated report
- **Open a GitHub issue** titled `[Cycle 1 review] <metric_name> below threshold (<value> vs <target>)` with the report contents inlined and the suggested next steps from §5
- **Email `ng@theconcordgroup.com`** subject `[Cycle 1 review] <metric_name> below threshold` with: which metric, by how much, link to the report, suggested next steps
- Continue normal cadence — do NOT escalate to weekly if already monthly

### 4.3 Any primary metric below threshold by ≥ 20 percentage points

- Same as §4.2 PLUS:
- Title prefix issue and email with `[URGENT]`
- Recommend in the issue body: **suspend D.EXP.6 / 7 / 8 auto-writes** (set the kill switch documented in §6 below) until investigation closes

### 4.4 Drift signal firing without primary threshold failure

- Write the dated report including the drift signal detail
- Open a GitHub issue (no email) titled `[Cycle 1 review] Drift signal: <signal>`
- Recommendation in the issue: monitor for one more cycle before action

### 4.5 Two consecutive reviews below threshold on the same metric

- Email `[ESCALATION]` to `ng@theconcordgroup.com` (in addition to the per-review issue)
- The escalation email lists the trend across both reviews and recommends prompt-tuning work be scheduled

### 4.6 Cost spike > 2× the budget cap

- Open issue and email regardless of other metric outcomes
- Recommendation: investigate arbiter trigger threshold and verifier-blind-to-confidence rule before next run

---

## 5. Suggested next steps per metric failure

### Arbiter-final acceptance rate < 80%

- Pull the lowest-acceptance audit targets from the report
- Review 10 most-recent arbiter decisions on those targets — is the arbiter prompt's enum/normalization spec wrong for that field?
- Check whether the verifier is feeding the arbiter low-quality signals (e.g., `ambiguous` on cases the human reviewer found trivially clear)
- Consider tuning the arbiter trigger threshold (currently: any non-`supported` verifier state)

### Verifier precision on flags < 90%

- Pull 10 most-recent verifier `unsupported` / `contradicted` calls that humans overrode
- Check whether the verifier prompt is too aggressive — is it flagging on minor passage ambiguity that the primary correctly resolved?
- Confirm the verifier-blind-to-confidence rule is still in effect (regression detector: if a high-confidence primary value with clear citation is being flagged, the verifier may have started receiving confidence inputs by accident)

### Verifier recall on `missing_but_stated` < 70%

- Pull 10 most-recent gloss-overs caught by humans but missed by verifier
- For each, check whether the field is in the closed 14-target audit (if not, the verifier doesn't audit it — by design — and the recall miss may be acceptable)
- If the field IS in the closed audit, the verifier prompt needs strengthening for that field specifically
- Consider whether the article body context length is truncating before the missed passage

### Drift signal 4 (`not_stated` rate climbing on a target)

- Pull 10 most-recent extractions where the target was `not_stated` — sample for cases where the article clearly states the field
- If the primary is retreating on the target, the prompt may need anti-retreat reinforcement for that field
- Confirm no upstream change (extraction prompt revision, model swap) coincides with the WoW increase

### Drift signal 5 (arbiter invocation rate climbing)

- Check whether one specific audit target is driving the increase
- If yes, that target's verifier or primary is the issue
- If diffuse across targets, suspect a model or prompt change — bisect against the deploy log

### Drift signal 6 (per-article arbiter cost spike)

- Pull the highest-cost article and its arbiter audit chain
- Confirm batched-per-project_reference is working (one arbiter call per reference, not per disputed field)
- Confirm per-article budget cap is enforced and not bypassed
- Check for token-bloated prompts (verifier output being passed to arbiter verbatim vs. summarized)

---

## 6. Kill switches and rollback hooks

The agent does NOT toggle kill switches automatically. It only recommends. The available switches when escalation is warranted:

- **`AGENT_ALLOW_LIVE_LLM_NEWS`** (existing per-profile gate from `AGENT.gates`) — set false in Render to stop the news agent loop entirely. Affects `D.EXP.5V` / `5A` since they share the news profile.
- **Per-D.EXP-stage kill switches** (to be defined in `D.EXP.5V` / `5A` design) — likely `EXPANSION_VERIFIER_ENABLED` and `EXPANSION_ARBITER_ENABLED` env vars defaulting true once arms. When false, primary extractions flow directly to Evidence as today (the pre-expansion behavior).
- **Schema fallback** — if a deeper rollback is needed, the `Project` columns from `D.EXP.1` and `NewsProjectReference` columns from `D.EXP.2` are nullable and forward-compatible; turning off the verifier/arbiter does NOT require dropping columns.

---

## 7. Report template

The agent writes one Markdown file per review at `docs/review_logs/reviewer_usefulness_YYYY-MM-DD.md` in this format:

```markdown
# Reviewer Usefulness Review — YYYY-MM-DD

**Cadence mode:** weekly | monthly (run N of 4 in weekly | monthly)
**Window covered:** YYYY-MM-DD to YYYY-MM-DD
**Article volume in window:** N news-driven extractions
**Deployment baseline:** git tag cycle1-news-extraction-v1, pushed YYYY-MM-DD

## Primary metrics

| Metric | Value | Threshold | Pass? |
|---|---|---|---|
| Arbiter-final acceptance rate | XX.X% | ≥ 80% | ✅ / ❌ |
| Verifier precision on flags | XX.X% | ≥ 90% | ✅ / ❌ |
| Verifier recall on `missing_but_stated` | XX.X% | ≥ 70% | ✅ / ❌ |

## Drift signals

| Signal | This run | Prior run | Trend |
|---|---|---|---|
| Highest `not_stated`-rate target | <target>: XX.X% | <target>: XX.X% | +N.N pp |
| Arbiter invocation rate | XX.X% | XX.X% | +N.N pp |
| Per-article arbiter cost (p95) | $X.XX | $X.XX | trend |

## Per-target breakdown

[14-target table with `not_stated` rate, verifier 5-state distribution, arbiter invocation count]

## Recommendations

[Generated from §5 based on which metrics/signals fired. Empty if all green.]

## Drill-through

- Top 5 arbiter overrides by human reviewers in this window: [list with NewsProjectReference IDs]
- Top 5 verifier flags overridden by humans in this window: [list]
- Top 5 human-caught gloss-overs missed by verifier in this window: [list]

## Cadence decision

Next scheduled run: YYYY-MM-DD HH:MM PT (cadence: weekly | monthly)
```

---

## 8. Cost guardrails (this review's own cost)

The review itself costs minimal LLM tokens (the agent runs SQL queries and assembles a Markdown report; it does NOT re-extract or re-verify any article). Expected per-run cost: < $0.50.

If the review's own cost exceeds $5 in a single run, that's a bug — file an issue and investigate before the next scheduled run.

---

## 9. When to update this document

Update via PR when:

- A threshold needs tuning (e.g., 80% acceptance rate proves too aggressive once real reviewer behavior is observed)
- A new drift signal is identified
- A new audit target is added to the closed 14 (or one is removed)
- A new kill switch is introduced
- The report template needs new fields
- The cadence rules change (e.g., quarterly mode after 12 months)

**Do NOT** update via direct commit to `main` — the schedule reads this doc on every run and a typo can produce false alarms.

---

## 10. Cross-references

- **Expansion overview**: `docs/specs/news_extraction_expansion_overview.md`
- **Roadmap row**: `ROADMAP.md` `D.EXP.review` under `Cross-cutting: News extraction expansion (D.EXP.*)`
- **AGENT.gates (per-profile kill switches)**: `ROADMAP.md` `AGENT.gates` row + `docs/ops/news_agent_monitoring.md`
- **Change-impact framework**: `docs/ops/change_impact_classification.md` (use to determine reset tier when a metric failure forces rollback)

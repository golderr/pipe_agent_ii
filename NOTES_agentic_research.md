# Agentic Research & Google Alerts — Design Notes

> Context: These notes capture ideas from comparing Steve/SRK's Miami-Dade pipeline agent approach (Claude Code-driven sessions with agentic Google searching) with our structured pipeline. Goal is to wire these concepts into our roadmap. Written 2026-05-01.

---

## 1. Google Alerts as a Passive Source

### Concept
Google Alerts monitors the web for new content matching saved queries and pushes notifications via email or RSS. Free, zero-maintenance, and surprisingly effective at catching coverage our structured scrapes miss — especially from smaller blogs, local news outlets, and municipal sites we haven't built dedicated collectors for.

### Alert Strategy
- **Per-project alerts:** For high-priority or low-confidence projects, create alerts like `"Oceanwide Plaza" Los Angeles development`
- **Per-developer alerts:** `"Related Companies" Los Angeles residential` — catches new projects before they hit our known sources
- **Neighborhood + keyword alerts:** `"Hollywood" "apartment" "units" approved` — casts a wider net for projects we don't know about yet
- **Municipal/regulatory alerts:** `"LA Planning Commission" residential approved` — catches agenda items, hearing results

### Ingestion Flow

```
Google Alerts
    ↓
RSS feed (preferred) or dedicated Gmail inbox
    ↓
Alert Collector (new collector, runs on schedule)
    - Polls RSS feed or reads inbox via API
    - Deduplicates URLs against evidence layer (skip if URL already ingested)
    - For each new URL:
        ↓
    Existing News Pipeline (Pass 0 → 1 → 2a → 2b)
        - httpx + trafilatura fetch
        - Structural extraction
        - Haiku triage (is this about a real project?)
        - Opus extraction (if triage passes)
        ↓
    Matching engine (existing)
        - Match against known projects
        - If match: new evidence row, flag for review if conflicts
        - If no match: candidate new project, enters review queue
```

### Key Decisions Needed
- **RSS vs Gmail API for ingestion?** RSS is simpler (just poll a feed URL), no auth needed. Gmail API is more robust but adds OAuth complexity. Recommendation: start with RSS.
- **How many alerts?** Google caps at 1,000 alerts per account. At scale we'd need to be strategic — prioritize low-confidence projects and active developers rather than blanketing everything.
- **Alert management lifecycle:** Alerts should be created/updated/deleted as projects move through statuses. A completed or cancelled project doesn't need active alerts anymore. This could be a background job that syncs alert config with project state.
- **Source tier:** Tier 2 (same as news), since the underlying content is news articles. The alert is just the discovery mechanism.

### Implementation Sketch
- New file: `src/collectors/google_alerts_collector.py`
- Config: RSS feed URL(s) stored in `catalog.sources` with source_type = 'google_alerts'
- Schedule: Poll every 6-12 hours (alerts aren't real-time anyway)
- Dedup: Check URL against `evidence.raw_url` before processing
- Output: Feeds directly into existing `news_extraction_pipeline`

---

## 2. Agentic Research Pass ("Smart Assistant")

### Concept
A research agent that actively explores the web for information about projects in our database — going beyond fixed scrapers and passive alerts. It can improvise search strategies, follow leads, cross-reference sources, and make judgment calls about what's worth investigating. Think of it as a research analyst that periodically reviews the portfolio and fills in gaps.

### When Does It Run?
Three trigger modes (not mutually exclusive):

1. **Scheduled sweep:** Nightly or weekly, picks the N projects with lowest confidence scores or most stale evidence and researches them. Fully automated.
2. **Event-triggered:** Fires when specific conditions are met:
   - New project enters the database with confidence < threshold
   - Contradiction detected between sources
   - Project hasn't had new evidence in X days
   - Status change detected on a related project (e.g., same developer)
3. **Human-initiated:** Reviewer clicks "Research this project" from the UI, agent does a deep dive on that specific project. Results appear in evidence layer for review.

### What Does the Agent Actually Do?
For a given project, the agent would:

1. **Assess gaps:** Query the evidence layer — what do we know? What's missing? What has low confidence or conflicting values?
2. **Formulate search strategy:** Based on gaps, generate targeted queries:
   - `"{project name}" {city} development status 2026`
   - `"{developer name}" {city} new project`
   - `"{address}" permit approved`
   - `site:planning.lacity.org "{project name}"`
   - Variations with alternate names, nearby addresses
3. **Execute searches:** Call a search API (Google Custom Search, Brave, SerpAPI)
4. **Fetch and analyze results:** For each promising result, fetch the page and run it through LLM extraction
5. **Synthesize findings:** Determine if new info was found, whether it confirms or conflicts with existing evidence
6. **Write back:** Insert new evidence rows into the evidence layer with source attribution
7. **Self-assess:** Log what it searched, what it found, what it didn't find — this feeds back into improving future searches

### Architecture Options

#### Option A: FastAPI Endpoint + Claude Tool Use (Recommended for us)
Our backend is already FastAPI on Render. Add an endpoint that:
- Accepts a project ID (or list, or "lowest confidence N")
- Runs an agent loop using Claude API with tool_use
- Tools available to the agent: `web_search`, `fetch_page`, `query_evidence_layer`, `insert_evidence`
- Agent decides which tools to call and in what order
- Results written directly to Supabase

**Pros:** Fits our existing stack, no new infrastructure, we control the tools and prompts, easy to iterate.
**Cons:** Long-running requests need background job handling (Render supports this). Claude API costs per run.

```
FastAPI endpoint: POST /api/research/{project_id}
    ↓
Background task (or Celery/ARQ worker)
    ↓
Agent loop (Claude API with tool_use)
    Tools:
    - search_web(query) → Google/Brave/Serp API
    - fetch_and_extract(url) → httpx + trafilatura + LLM extraction
    - get_project_evidence(project_id) → Supabase query
    - get_project_gaps(project_id) → resolution engine query
    - submit_evidence(project_id, data, source_url) → evidence layer insert
    ↓
Evidence layer (existing)
    ↓
Review queue (if conflicts detected)
```

#### Option B: AWS Bedrock Agent
Bedrock Agents is a managed service where you define an agent with a foundation model + action groups (tools). AWS handles the orchestration loop, memory, and hosting.

**Pros:** Managed infrastructure, built-in session management, scales automatically.
**Cons:** Vendor lock-in to AWS (we're on Render/Vercel/Supabase), action groups have specific format requirements, harder to debug, adds AWS to our stack. Bedrock agents are also somewhat rigid in how they handle multi-step reasoning compared to raw tool_use.

**Verdict:** Overkill for us right now. Makes more sense for orgs already on AWS or needing to run agents at massive scale. Our FastAPI + Claude tool_use approach gives us the same capability with less complexity.

#### Option C: LangGraph / CrewAI / Agent Framework
Use an open-source agent framework to manage the orchestration loop.

**Pros:** Pre-built patterns for tool routing, memory, retries.
**Cons:** Another dependency, abstraction layer over what's already a simple loop, frameworks change fast.

**Verdict:** Not worth the added complexity. Claude's tool_use is straightforward enough that a simple while loop with tool dispatch is all we need.

### Learning & Improvement

The agent should get smarter over time:

- **Search strategy learning:** Track which query patterns yielded useful results vs. dead ends. Over time, prioritize effective patterns. This can be as simple as a `research_log` table that records (query, source_found, was_useful).
- **Source discovery:** When the agent finds a useful source we don't have a dedicated scraper for, flag it. If it keeps finding good info from the same site, that's a signal to build a collector for it.
- **Reviewer feedback loop:** When a reviewer accepts or rejects evidence the agent surfaced, that signal feeds back into the agent's confidence calibration. This is the piece Steve has that we've been planning (D.late.A/B).

---

## 3. Comparison: Steve's Approach vs. What We'd Build

| Dimension | Steve (Claude Code sessions) | Our approach (system-integrated agent) |
|---|---|---|
| Trigger | Human starts a Claude Code chat | Scheduled, event-driven, or human-initiated from UI |
| Orchestration | Claude Code is the runtime | FastAPI + Claude tool_use API |
| State | Excel doc (ephemeral per session) | Supabase evidence layer (persistent, versioned) |
| Search | Claude Code's built-in web search | Search API (Google/Brave) via tool_use |
| Output | Updated Excel rows | Evidence rows with provenance, conflicts flagged |
| Review | Human reads Excel | Review queue with decision cards, batch commit |
| Learning | ML model on reviewer decisions | Research log + reviewer feedback loop |
| Scalability | One geo per session, human required | Multi-geo, automated, human optional |

### Steve's Key Insight Worth Adopting
The "4 search variations per project" pattern is smart. Rather than one generic search, running multiple targeted queries with different angles (project name, developer + city, address + permit, neighborhood + units) dramatically improves recall. Our agent should do this systematically.

---

## 4. Roadmap Integration (Suggested)

These features map to existing and new roadmap phases:

### Phase D additions:
- **D.7c: Google Alerts collector** — New passive source. RSS-based polling, feeds into existing news extraction pipeline. Low effort, high value.
- **D.8: Alert lifecycle management** — Auto-create/update/delete alerts based on project state and confidence scores.

### New Phase (F? or E.x):
- **Agentic Research Pass (v1):**
  - FastAPI endpoint + background worker
  - Claude tool_use agent with search/fetch/evidence tools
  - Triggered manually from project detail page ("Research this project" button)
  - Results feed into evidence layer → review queue

- **Agentic Research Pass (v2):**
  - Scheduled sweep mode (lowest confidence projects)
  - Event triggers (new project, contradiction, stale evidence)
  - Research log and search strategy tracking
  - Source discovery flagging

- **Reviewer Feedback Learning (D.late.A/B):**
  - Track accept/reject decisions on agent-surfaced evidence
  - Feed back into agent confidence calibration
  - Inform search strategy prioritization

---

## 5. Research Log — Long-Term Strategy Learning

### Concept
The agent's persistent memory lives in the database, not in the LLM's context window. A `research_log` table records every search the agent performs and whether it yielded useful results. Over time, this log becomes a learning corpus that the agent reads at the start of each run to inform its strategy — which query patterns work, which sources are productive, which approaches are dead ends for specific project types or geographies.

### Schema: `research_log`

```sql
create table research_log (
    id              uuid primary key default gen_random_uuid(),
    project_id      uuid references projects(id),
    run_id          uuid not null,              -- groups actions within a single research session
    created_at      timestamptz default now(),
    query_text      text not null,              -- the actual search query used
    query_strategy  text,                       -- category: "project_name_search", "developer_search", "address_permit", "planning_commission", "neighborhood_keyword", etc.
    source_url      text,                       -- URL of result that was fetched (null if search returned nothing useful)
    source_domain   text,                       -- extracted domain for aggregation (e.g., "urbanize.city", "planning.lacity.org")
    yielded_evidence boolean default false,     -- did this result in a new evidence row?
    evidence_accepted boolean,                  -- null until reviewed; true/false after reviewer decision
    notes           text                        -- agent's own notes on why it tried this / what it found
);

-- Indexes for the agent to query efficiently
create index idx_research_log_project on research_log(project_id);
create index idx_research_log_strategy on research_log(query_strategy, yielded_evidence);
create index idx_research_log_domain on research_log(source_domain, yielded_evidence);
```

### How the Agent Uses It

**At the start of a research run**, the agent queries the log to build its strategy:

1. **What's already been tried for this project?**
   - `SELECT query_text, yielded_evidence FROM research_log WHERE project_id = $1 ORDER BY created_at DESC`
   - Avoids repeating failed searches. If "Oceanwide Plaza permit 2026" returned nothing last week, try a different angle.

2. **What query strategies work best overall?**
   - `SELECT query_strategy, COUNT(*) as attempts, SUM(CASE WHEN yielded_evidence THEN 1 ELSE 0 END) as hits FROM research_log GROUP BY query_strategy`
   - If "planning_commission" queries yield evidence 40% of the time but "neighborhood_keyword" only 5%, prioritize accordingly.

3. **Which domains are most productive?**
   - `SELECT source_domain, COUNT(*) as total, AVG(CASE WHEN evidence_accepted THEN 1.0 ELSE 0.0 END) as accept_rate FROM research_log WHERE evidence_accepted IS NOT NULL GROUP BY source_domain`
   - If urbanize.city results get accepted 85% of the time but some blog gets accepted 10%, weight accordingly.

4. **What works for similar projects?**
   - For a new senior living project, check what strategies worked for other senior living projects: `SELECT query_strategy, source_domain, yielded_evidence FROM research_log rl JOIN projects p ON rl.project_id = p.id WHERE p.product_type = 'senior_living' AND yielded_evidence = true`

### Agent Prompt Integration

At the start of each run, a summary of the research log is injected into the agent's system prompt:

```
Based on historical research data:
- Most effective strategies: planning_commission (42% hit rate), developer_name_search (31%), project_name_search (28%)
- Least effective: neighborhood_keyword (5%), generic_news_search (8%)
- Top domains by reviewer acceptance: urbanize.city (87%), planning.lacity.org (91%), la.curbed.com (72%)
- For this project specifically: 3 prior searches attempted, last on 2026-04-15, none yielded new evidence. Previously tried: [list]
- Suggested approach: Try planning commission minutes and developer portfolio search (not yet attempted for this project).
```

### Reviewer Feedback Closure

The loop closes when `evidence_accepted` gets populated:
1. Agent searches → finds article → extracts evidence → writes to evidence layer
2. `research_log` row created with `yielded_evidence = true`, `evidence_accepted = null`
3. Reviewer accepts or rejects the evidence in the review queue
4. Trigger or background job updates `research_log.evidence_accepted` based on reviewer decision
5. Next agent run reads updated log — now it knows not just what *found* results, but what found *good* results

This is the "learning from reviewer decisions" capability that Steve has, implemented as a data feedback loop rather than a separate ML model.

---

## 6. Open Questions

- What search API to use? Google Custom Search ($5/1K after free tier), Brave Search API (cheaper, $3/1K), SerpAPI ($50/mo for 5K). Need to evaluate result quality for real estate queries specifically.
- How many agent research runs per day/week? Cost = (search API calls + Claude API tokens) × projects researched. Need to model this.
- Should the agent have access to paid/gated sources (CoStar, etc.) or only public web? Start with public, expand later.
- How do we handle the agent finding the same article from multiple search queries? Dedup at URL level before extraction (same as alerts).
- Do we need a dedicated "research queue" in the UI, or does evidence from the agent just flow into the existing review queue? Start with existing review queue, tag source_type = 'agent_research' so reviewers can filter.

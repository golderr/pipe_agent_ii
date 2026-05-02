# Urbanize LA Source Strategy

## Status

- Source slug: `urbanize_la`
- Status: seeded by Alembic migration `202605010025` for D.2a
- Intended Phase D role: only live scheduled-source pilot
- Logical source type: `news_article`
- Source scope: market-unscoped (`market_id = NULL`, `jurisdiction_id = NULL`)
- Validation date: 2026-04-30 local / 2026-05-01 UTC

## Access

- Public article pages on `https://la.urbanize.city`
- No credentials required in D.2v validation
- Fetch path: `polite`
- Article pages returned HTTP 200 and open body text with the existing Pass 0 fetcher
- Page chrome includes a free-article/subscription modal string, but validation articles still extracted as `paywall_state = open`

## Discovery

Primary incremental discovery:

- `https://la.urbanize.city/rss.xml`
- Returned HTTP 200 with `Content-Type: application/rss+xml; charset=utf-8`
- Current feed contained 10 items spanning 2026-04-27 through 2026-04-30
- Recent publishing pattern in validation sample: roughly 2-3 LA posts per weekday morning, with RSS timestamps around 13:00-13:15 UTC

Backfill discovery:

- `https://la.urbanize.city/sitemap.xml`
- Returned HTTP 200 and is a sitemap index with:
  - `https://la.urbanize.city/sitemap.xml?page=1`
  - `https://la.urbanize.city/sitemap.xml?page=2`
- Validation count: 12,111 post URLs total
- Validation count since 2025-05-01: 988 URLs with `lastmod >= 2025-05-01T00:00:00Z`
- Sitemap date range observed: 2016-06-17 through 2026-04-30
- `https://la.urbanize.city/feed` returned 404; use `/rss.xml`
- `https://la.urbanize.city/sitemap_index.xml` returned 404; use `/sitemap.xml`

Archive/browse paths:

- Homepage is usable for current article discovery but RSS should be authoritative for incremental runs.
- Neighborhood pages such as `/neighborhood/downtown` and `/neighborhood/santa-monica` returned HTTP 200.
- `/search/` is disallowed by robots and should not be crawled.

## Robots And Rate Limit

Robots URL:

- `https://la.urbanize.city/robots.txt`
- Returned HTTP 200 with `Content-Type: text/plain`

Relevant robots posture:

- `User-agent: *` disallows admin, login, search, oEmbed, and Drupal system paths.
- Article paths under `/post/...`, RSS, sitemap, and neighborhood pages were not disallowed in the observed file.
- `User-agent: ChatGPT-User` and `User-agent: GPTBot` are disallowed. The collector should use the project-identifying `TCGPipelineTracker` user agent, not either of those bot names.
- No crawl-delay directive observed.

D.2a collector posture:

- Cache robots.txt for 24 hours.
- Use RSS for daily incrementals and sitemap pages for backfill.
- Start with conservative per-host throttling, for example one request every 1-2 seconds, plus `Retry-After` handling.
- Do not auto-pause on ordinary 404s for article or optional endpoint misses.
- Seed `schedule_cron = '30 7 * * *'` in `America/Los_Angeles`. This runs roughly 75-90 minutes after the observed RSS publication window around 06:00-06:15 PT; D.6 can add jitter around the configured time.

## Scope And Matching

D.2a seeds `urbanize_la` without a market or jurisdiction. The collector
ingests all eligible Urbanize LA URLs; the matcher decides relevance across live
markets. This is intentional:

- LA-relevant articles should match LA projects.
- Santa Monica articles may match the Santa Monica market as it comes online.
- Orange County or other non-modeled articles may become discarded references or
  new-candidate/graveyard signal.
- D.B dry-run cost should use the configured source window, not an LA-filtered
  subset.

The unscoped matcher path in D.4 is load-bearing for this source.

## D.2v Article Validation

The five URLs below were submitted through `POST /research/articles` in the development DB. Because this environment has no Anthropic key, queued jobs were run with triage/extraction/integration disabled. Pass 0 fetch, metadata persistence, body extraction, SourceRun/job completion, and Pass 1 structural extraction did run.

| URL | Result | Body chars | Structural observations |
| --- | --- | ---: | --- |
| `https://la.urbanize.city/post/la-world-trade-center-be-converted-512-apartments` | `fetched`, HTTP 200, open | 2,298 | 11 signals: Jamison developer dictionary hits and 512/686 apartment counts. Address was in title/body but not captured as an address signal. |
| `https://la.urbanize.city/post/affordable-housing-pitched-property-2101-w-8th-street-westlake` | `fetched`, HTTP 200, open | 1,506 | 5 signals: addresses for 751-757 S. Alvarado and 740 S. Alvarado, one 57-unit prior-project count, and proposed-status phrase. Primary title address `2101 W. 8th Street` was not captured from title. |
| `https://la.urbanize.city/post/downtown-womens-center-project-rises-501-e-5th-street` | `fetched`, HTTP 200, open | 1,442 | 2 signals: 97-unit count and apartment product type. Completion text was in body but not represented in the summary counts checked here. |
| `https://la.urbanize.city/post/tishman-speyer-rolls-out-updated-plans-two-additional-santa-monica-sites` | `fetched`, HTTP 200, open | 1,584 | 11 signals: proposed-status phrases, WS Communities developer dictionary hits, 1318 Lincoln address, and 120/110/35 unit counts. Good multi-reference pressure case. |
| `https://la.urbanize.city/post/work-begins-83-acre-development-former-westminster-mall` | `fetched`, HTTP 200, open | 1,553 | 4 signals: proposed-status and product-type phrases. The `2,250 residential units` comma-formatted count was not captured by Pass 1. |

Validation article IDs in the development DB:

- `7c8b3756-291c-457e-b393-bcca7915928a`
- `c34023cf-c5cb-4e3c-92dd-a8454903c01b`
- `3226cb8c-dca7-4291-af38-cd18249e8fd9`
- `0eb28d46-2eb8-41af-b21c-065d92740a30`
- `870e9afa-59ec-4c4e-90d8-32b517aa2d77`

## Quality Notes

- Urbanize HTML is friendly to the existing Pass 0 stack: metadata title, author, publication date, body text, and open/paywall state persisted cleanly.
- RSS descriptions include full-ish body HTML and tags; D.2a still fetches canonical article pages so body extraction and hash behavior stay consistent across paste/scheduled/backfill paths.
- Sitemap is large enough for configurable backfill windows but small enough to process politely in one dry-run pass.
- D.2a-prep implemented the first Pass 1 tightening slice before high-volume backfill:
  - capture comma-formatted unit counts such as `2,250 residential units`
  - scan title/headline metadata for title-only addresses such as `2101 W. 8th Street`
  - support numbered street names such as `8th Street`
  - review completion-date extraction for phrases like `Completion is expected in Fall 2027`
- All validation articles returned `paywall_state = open`. No actually gated Urbanize article was found during D.2v, so D.2a should not add speculative Urbanize paywall logic until one is observed.
- Article-update behavior was not tested. D.2a/D.6 should treat changed `body_text_hash` on refetch as a known monitoring gap for dedup and stale-body behavior.

## Pre-D.6 Staging Gate

D.2v ran through `news_paste_a_link` with no Anthropic key in the dev DB. Before
scheduled scraping is enabled in D.6:

- Seed `urbanize_la` and repeat the five URLs through host routing so the
  source-specific config path is exercised.
- Rerun those same URLs in staging with an Anthropic key.
- Confirm Haiku triage, Opus extraction/re-extraction if triggered, matching,
  evidence integration, and review payloads produce sensible output.

2026-05-01 configured-environment smoke:

- Command: `python scripts/run_d6_urbanize_smoke.py --allow-non-staging --token d6-smoke-20260501c`
- Environment observed by app settings: `APP_ENV=development`; this was not a separate staging deployment.
- Source path: scheduled-style `news_scrape` job using `urbanize_la` and `PoliteNewsCollector`.
- Result: 5 discovered, 5 fetched, 5 triaged relevant, 5 extraction passes `ok`, 0 failed fetches, 0 block-like/transient failures, 0 cost-cap skips.
- Report artifact: `data/output/d6_urbanize_smoke_d6-smoke-20260501c.json` (ignored by git).
- Repeat-run cleanup: `python scripts/run_d6_urbanize_smoke.py --allow-non-staging --cleanup-token <token>` deletes smoke articles, jobs, source runs, review items, and article evidence for a prior token. The runner refuses production environments even with `--allow-non-staging`.
- Finding: the first run with `NEWS_EXTRACT_MAX_TOKENS=2500` truncated the multi-site Santa Monica article. The default and `.env.example` were raised to `5000`; staging/production worker env vars should use the same value before cron is enabled.

2026-05-01 staging smoke follow-up:

- The first staging smoke produced 13 Opus calls for 5 hand-picked relevant articles because Pass 3a fired on every article and Pass 3b fired on 3. Before enabling cron, D.6 tightens Pass 3a structural-conflict detection and reruns the smoke.
- Urbanize LA backfill now uses `news_sources.config.backfill_window_days = 56` (8 weeks). This is an LA-specific mature-market window; future geographies can choose longer per-source windows during onboarding when they need more history to bootstrap project awareness.
- Expected D.B cost after tightening is roughly `$80-110` for the 8-week slice; reconfirm after the next staging smoke before requesting Nate's approval.
- Post-tightening smoke `d6-smoke-staging-20260501b` reduced Pass 3a to 1 trigger and Pass 3b to 1 trigger across the 4 articles that reached extraction. The fifth article was skipped by the daily cost cap after the day's earlier smoke spend, so D.6 still needs one clean 5/5 rerun after cap reset or an approved temporary cap bump.

## Light Reconnaissance Of Deferred Sources

LA YIMBY:

- Canonical host observed as `https://layimby.com`
- `https://layimby.com/robots.txt` returned HTTP 200 and disallows `/wp-admin/` while allowing `/wp-admin/admin-ajax.php`
- `https://layimby.com/feed` returned RSS
- `https://layimby.com/sitemap.xml` returned a sitemap index
- Looks suitable as a future WordPress-like fixture/source for D.late.E1

The Real Deal LA:

- Root robots file is `https://therealdeal.com/robots.txt`; `/la/robots.txt` returned 404
- `https://therealdeal.com/la/feed/` returned RSS for Los Angeles
- `/la/sitemap.xml` and root `/sitemap.xml` returned 404 in validation
- The site has heavier frontend/paywall/commercial-news noise than Urbanize. Keep as D.late.E2 and require source-specific section filtering plus compliance review before any advanced fetch path.

## D.2a Implementation

- `urbanize_la` is seeded as the only active scheduled Phase D source.
- The row is market-unscoped (`market_id = NULL`, `jurisdiction_id = NULL`); matcher decides relevance.
- `bizjournals_la` is inactive and unscheduled until paid-source capability ships.
- `urbanize_la -> news_article` is present in `LOGICAL_SOURCE_TYPE_BY_SOURCE_NAME`.
- Host routing for `la.urbanize.city` comes from `news_sources.config.hosts` with a short in-process API cache.
- Initial cron candidate is seeded as `30 7 * * *` in `America/Los_Angeles`; D.6 may add jitter.
- Backfill horizon is configured as `backfill_window_days = 56`; the D.B backfill CLI should read this value and default to 56 days when a source leaves it unset.
- Sanitized Urbanize validation fixtures and LA YIMBY-like RSS/article samples live under `tests/fixtures/news/`.

## Open Issues For D.6

- Repeat the successful five-URL Anthropic smoke on the actual staging or
  production worker environment before enabling scheduled cron; the local
  configured-environment run used `APP_ENV=development`.
- Confirm the daily cron after a short production observation window.
- Move per-host rate limiting to Redis before multiple concurrent news workers
  can hit the same publisher host.

## Code References

- `src/tcg_pipeline/news/urls.py`
- `src/tcg_pipeline/news/ingest.py`
- `src/tcg_pipeline/news/structural.py`
- `src/tcg_pipeline/workers/news_jobs.py`
- `src/tcg_pipeline/source_tiers.py`
- `docs/specs/news_research_design.md`

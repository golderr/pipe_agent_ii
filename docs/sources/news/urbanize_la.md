# Urbanize LA Source Strategy

## Status

- Source slug: `urbanize_la`
- Status: validated for D.2a implementation; not yet seeded in `news_sources`
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

D.2a recommendation:

- Cache robots.txt for 24 hours.
- Use RSS for daily incrementals and sitemap pages for backfill.
- Start with conservative per-host throttling, for example one request every 1-2 seconds, plus `Retry-After` handling.
- Do not auto-pause on ordinary 404s for article or optional endpoint misses.
- Seed `schedule_cron = '30 7 * * *'` in `America/Los_Angeles`. This runs roughly 75-90 minutes after the observed RSS publication window around 06:00-06:15 PT; D.6 can add jitter around the configured time.

## Scope And Matching

D.2a should seed `urbanize_la` without a market or jurisdiction. The collector
ingests all eligible Urbanize LA URLs; the matcher decides relevance across live
markets. This is intentional:

- LA-relevant articles should match LA projects.
- Santa Monica articles may match the Santa Monica market as it comes online.
- Orange County or other non-modeled articles may become discarded references or
  new-candidate/graveyard signal.
- D.B dry-run cost should use the full 12-month sitemap count, not an LA-filtered
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
- RSS descriptions include full-ish body HTML and tags; D.2a should still fetch canonical article pages so body extraction and hash behavior stay consistent across paste/scheduled/backfill paths.
- Sitemap is large enough for a 12-month backfill but small enough to process politely in one dry-run pass.
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

## Open Issues For D.2a

- Seed `urbanize_la` as the only active scheduled Phase D source.
- Seed it with `market_id = NULL` and `jurisdiction_id = NULL`; matcher decides relevance.
- Disable/unschedule `bizjournals_la` until paid-source capability ships.
- Add `urbanize_la -> news_article` to `LOGICAL_SOURCE_TYPE_BY_SOURCE_NAME`.
- Add host routing for `la.urbanize.city` from `news_sources.config` or source YAML, with short in-process cache.
- Confirm daily cron after a short production observation window. Initial candidate: `30 7 * * *` in `America/Los_Angeles`, with D.6 jitter.
- Reuse the sanitized Urbanize validation fixtures and LA YIMBY-like RSS/article samples in `tests/fixtures/news/` so the polite collector stays source-generic and tests do not refetch live URLs.

## Code References

- `src/tcg_pipeline/news/urls.py`
- `src/tcg_pipeline/news/ingest.py`
- `src/tcg_pipeline/news/structural.py`
- `src/tcg_pipeline/workers/news_jobs.py`
- `src/tcg_pipeline/source_tiers.py`
- `docs/specs/news_research_design.md`

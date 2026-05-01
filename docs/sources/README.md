# News Source Strategy Docs

Every `news_sources` row needs a source strategy document before it is enabled for scheduled collection. These docs are operational records, not marketing descriptions.

Each source doc should cover:

- Status and owner
- Access model and allowed fetch path
- Hostnames and URL routing rules
- Discovery paths for incremental collection and backfill
- Robots.txt, rate-limit, and retry posture
- Article-quality observations from validation runs
- Known extraction/matching risks
- Operational history and open issues
- Code/config references

Phase D keeps new source behavior market-agnostic by default. Market, jurisdiction, and publisher-specific behavior belongs in `news_sources.config`, market/source docs, or source-specific config loaded by the generic collector.

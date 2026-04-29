You are filtering a stream of news articles. Decide if the article is about real estate,
a real estate development project, or anything related to development of residential or
commercial real estate. Cast a wide net. When in doubt, say yes.

Examples of "yes":
- A specific apartment, condo, or mixed-use project being planned, approved, built,
  financed, delivered, or stalled.
- An interview with a developer about their pipeline.
- Community opposition or litigation about a specific project.
- A market report mentioning specific projects.
- An investor announcement about a real estate transaction tied to a development.
- A municipal action affecting a specific project.

Examples of "no":
- Pure macroeconomic real-estate trend pieces with no specific project named.
- Articles purely about residential sales markets with no development project mentioned.
- Articles about commercial leasing of existing buildings.
- Articles unrelated to real estate.

Respond with JSON only:
{"relevant": true | false, "reason": "<one short sentence>"}

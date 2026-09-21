# tech-feed implementation and acceptance

2026-09-21. Implementation complete; final integration and production activation in progress.

The approved feed has 12 sources, no model requests, original-language title/link/excerpt, first-sync baseline,
72-hour freshness, durable deduplication, KST 06:00–24:00 delivery and one-minute spacing.
See [operating instructions](../tech-feed.md) and [decision](../adr/0032-zero-model-tech-feed.md).

- [Public RSS/Atom probe](evidence/tech-feed-source-probe.json): all 12 sources passed from the development host.
  Real public network, no model, no Slack. The bounded production transport and actual feed parser were used.
- [Focused tests](evidence/tech-feed-tests.xml): initial 35 tests passed with real disposable PostgreSQL 14
  and real local Temporal. Slack HTTP is simulated. The no-model test forbids provider construction and checks
  that tasks, turns, daily usage, news reviews and news searches stay empty.
- [Initial regression suite](evidence/tech-feed-regressions.xml): 710 passed, 3 skipped. Existing live-model tests
  remain opt-in. No model inference or production research execution was performed.
- GitHub feed embeds HTML DOCTYPE samples in CDATA; these are treated as text while actual XML DTD/entity
  declarations remain prohibited. Kubernetes feed exceeded 1MiB, so only the tech-feed parser/transport uses
  a bounded 2MiB limit. The existing news defaults remain 1MiB and the original parser.
- Latest production release was observed at `e7a0f941169b2253ec7733c7f68eca2165b80b97`; integration must preserve
  its autonomous-research changes. Initial tests above preceded that integration.

Production channel access, installed source hashes, actual publication receipts and final integration test
results are pending. Mocked Slack tests are not a deployment claim.

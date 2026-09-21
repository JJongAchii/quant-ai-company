# tech-feed implementation and acceptance

2026-09-21. Implemented, integrated, deployed and activated in the existing `#tech-feeds` channel.

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
- Merged the latest production/main release `e7a0f941169b2253ec7733c7f68eca2165b80b97` without conflicts.
  Its research implementation and deployment overlays are preserved.
- [Integrated regression suite](evidence/tech-feed-integrated-tests.xml): **992 passed, 36 skipped**, including
  36 focused tech-feed checks, with real disposable PostgreSQL 14 and local Temporal; Slack is simulated.
  Skips: 30 pinned-qlab checks, 3 operator-only Linux research runtime checks and 3 opt-in Codex checks.
  `uv run --frozen ruff check .` passed. No research run, training or live model inference was initiated.
- Slack UI inspection resolved the user's existing channel as `#tech-feeds` (`C0C2KPB76KE`), not singular
  `#tech-feed`. No new channel or app will be created.

## Production acceptance

- [Deployment receipt](evidence/tech-feed-deployment.json): exact code release
  `7e41b3307879098f649b182bf07b52b8de840882`, installed package hashes verified in all five image variants.
  All eight services running; API, both model runtimes and PostgreSQL health checks healthy.
  PostgreSQL container was retained. Existing hot-news settings, roles, credentials and research profiles
  were preserved. Before cutover there were no due turns, pending/sending outbox messages, running news
  calls or active research jobs/missions. The existing backup procedure produced a checksummed, secret-free
  backup in the existing bucket before migration/cutover.
- [Retained company worker pin](evidence/tech-feed-worker-pin.json): the existing 3070 poller's company
  runtime/config was advanced to the same commit; prior releases remain registered. Only company code paths
  and commit changed. No research repository/input/profile edits, experiments, training or new missions.
- Computer-use was needed only to inspect the existing Slack channel and add the already-installed Reporter.
  The channel was confirmed as `C0C2KPB76KE`; real Slack history then confirmed membership/access.
- [Production preview](evidence/tech-feed-production-preview.json): all 12 real feeds were baselined
  at 22:04 KST. **1,808 existing items were recorded without publishing historical content.**
  Real Temporal Cloud history confirms the dedicated running workflow, successful collection activity and
  persistent 600-second timer. Feed project tasks and turns: **0**.
- [Active service and Slack receipt](evidence/tech-feed-production-active.json): collection and publication
  enabled; all sources healthy. The explicit setup/status message was delivered once with no error:
  message ID `52902eba-cfde-58d7-977d-e405a62d327e`, Slack ts `1789996019.208189`, verified through real
  channel history with matching `client_msg_id`. It is labeled a setup confirmation, **not a news article**.
  [Open the confirmation](https://achiisquantresearch.slack.com/archives/C0C2KPB76KE/p1789996019208189).

At the 22:07 KST acceptance snapshot no fresh article had arrived after the baseline; article publications
were therefore still zero. The next natural collection is due around 22:14 KST. Historical articles were
not relabeled as new or replayed to manufacture delivery evidence. Real production collection and real Slack
status delivery are distinguished above from automated article-delivery tests using simulated Slack HTTP.

The feed incurs no model/API subscription fees and adds no paid service. Existing server/database/Temporal
usage continues and increases slightly. Disable both `TECH_FEED_ENABLED` and `TECH_FEED_PUBLISH_ENABLED`
and recreate the company processes to pause; retain receipts and never automatically retry uncertain posts.

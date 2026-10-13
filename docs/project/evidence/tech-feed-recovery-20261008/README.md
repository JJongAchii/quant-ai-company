# Tech Scout recovery — 2026-10-08

Restored live delivery after a read-only diagnosis found a shared collector/dispatcher allowlist mismatch.
The user's reply approved repair and recurrence prevention. No old Quant/scoter intent was changed.

- `diagnosis.json`: last delivery 2026-10-02 11:42 KST; collector eight vs dispatcher ten allowed channels.
  Both had Tech collection/publication enabled and the same owner/channel/source definitions.
- Policy v2 hashes the effective Tech authorization, target, source definitions and activation rather than
  unrelated global allowlists. Target permission revocation, source changes and expiry still fail closed.
- `operation.json`: exact existing dispatch base, selective three-module overlays, unchanged independent
  container IDs/images/restart counters, and receipt-first Tech-only Temporal handoff at a timer boundary.
  The original research worker remained running. The old Temporal run/history and DB checkpoints remain.
- Dedicated collector has DB/Temporal secrets only, no Slack/model credentials; 256MiB limit. It used about
  65MiB at the idle check, with no cgroup OOM events. No server/resource resize was performed here.
- 202 certainly-unsent, still-fresh v1 cards were revalidated against their original feed definitions,
  item content, saved text, project revision and author. The plan was persisted and applied with its exact
  digest. 404 PG updates, zero model calls, zero direct Slack writes. Message IDs/text/timestamps were retained.
  Full plan/apply receipts remain root-private on the host; Git contains public IDs/hashes and summaries.
- `production.json`: real PostgreSQL, Temporal Cloud, Slack `auth.test` and `chat.getPermalink` verification.
  At 10:04 KST, eight restored posts were delivered and 194 were pending. The dedicated workflow completed
  a natural RSS collection activity across all 12 sources; all succeeded, no new entries in that cycle.
  No synthetic article or extra Slack confirmation message was posted. Chat-history access was not added.
- 87 focused/deployment checks passed; complete local regression 1,639 passed / 48 skipped / 1 live deselected.
  Automated Slack/model outcomes in these suites are simulated, not production acceptance.
- Linux CI run 37709693302 on adc260e: 1,640 passed / 47 skipped / 1 deselected; empty-auth Codex catalog/config
  protocol passed separately with no inference. Later documentation-only evidence commits do not change
  these qualified source bytes.

No 48-hour observation requirement. Pending recovery cards drain normally at 60-second spacing, while
new feed items continue to be collected. Expired, delivered, attempted or uncertain rows are not replayed.
The deployment changes only Tech-specific modules; the whole mixed production image is not represented
as a clean full-main deployment. Future full-main operations must retain the Tech worker/profile and
`TECH_FEED_DEDICATED_WORKER=true`; the old shared queue registration may remain idle for legacy histories.

One image preparation attempt failed because BuildKit interpreted a raw image ID as a repository name;
no running service changed. A named local tag fixed this. One workflow status read failed before any
handoff receipt/effect existed; a subsequent read confirmed the timer boundary before the sole handoff.

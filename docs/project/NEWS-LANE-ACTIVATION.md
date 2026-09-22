# Dedicated Reporter news lane

2026-09-22. Production release `4fc8132bb31d56eb1ce3f7a3c57d98afba7a8124`.

The owner asked for one news task to keep running separately from research. This
change adds one reserved Codex lane and a dedicated `news-worker` process, without
changing model selection, publication hours, source scope, login or usage policy.

## Implementation

- Normal company/maintenance requests retain the existing single-slot file lock.
  All service-issued `news-` request IDs share one additional news lock. Existing
  request digests, receipt paths and uncertain outcomes remain intact.
- A dedicated `news-worker` container owns RSS/original collection and the single
  `-news-model` workflow/activity queue. It has no Slack token, lake credentials,
  research mounts or dependency on the research `worker` container.
- Existing news workflow IDs move at their next safe tick via continue-as-new.
  Earlier histories and frozen model request IDs remain recoverable; no workflow
  is terminated or replaced with a different identity.
- Research backlog no longer defers screening, editing or discovery. Runtime
  pauses, atomic company-wide reservations and actual subscription limits still
  apply. The observed production company/maintenance daily caps were already
  unset (`null`); this change does not set, remove or increase a cap.
- The new process is limited to 192 MiB and included in backup/release/restore
  lifecycle handling. The same server, database, Codex runtime and dispatcher
  remain shared failure dependencies. This is not a high-availability redesign.

## Validation scope

Tests use real PostgreSQL and Temporal, including a legacy-history replay and
same-ID transition to the news queue. They exercise news progress with no company
worker, company/news overlap, one-at-a-time editorial/search, atomic last-budget
reservation, frozen requests and global pauses. The Codex executable and Slack
HTTP in automated tests are simulated; process execution itself is real.

Local validation passed **1,012 tests**, with 36 documented opt-in/environment
skips and zero failures; `ruff` passed. The [JUnit report](evidence/news-lane-tests.xml)
covers real PostgreSQL/Temporal and simulated model/Slack behavior, not deployment.

[CI and production-history replay](evidence/news-lane-validation.json) passed:
1,012 CI tests, 35 skips and one deselected opt-in live test; 44 focused tests
after integrating the latest research change; the actual legacy editorial and
discovery histories replayed with zero activities or model calls executed.

## Production activation

[PR 57](https://github.com/JJongAchii/quant-ai-company/pull/57) was merged after CI.
The [release receipt](evidence/news-lane-release.json) records a 7.6-second model
drain, pre-change backup, source-byte verification of all five application image
variants, and nine running/healthy services after cutover at 11:52 KST.
PostgreSQL was not recreated. Roles, Slack credentials, research configuration,
schema, model allowance and uncertain receipts were preserved. No live model or
Slack call was replayed under a new request ID as part of deployment.

The [pre-change observation](evidence/news-lane-before.json) records a screening
request waiting with `busy`, the old shared queue, and no runtime quota pause.
The pre-existing discovery failure `invalid_search_candidates` is separate from
capacity and is preserved, not automatically replayed under a new request ID.

## Actual subscription and Slack observations

At 11:59 KST, [database, Temporal and real Slack observations](evidence/news-lane-after.json)
confirmed both original news workflow IDs on `quant-company-news-model`, with new
runs linked by continue-as-new. Collection continued on its dedicated queue.
The new screening request `news-screen-0960a7e8-6935-46da-b66d-9e5edffd121b`
completed at 11:56:36; editorial request `news-24dcbc84-de98-4d87-a92c-1f6cdd49c835`
completed at 11:58:45, both without errors. The earlier frozen screening request
in the before snapshot had already completed at 11:43, before this cutover; that
completion is not credited to the new lane.

Reporter delivered four natural news posts at 11:58:46–11:58:51 to `#hot-news`.
All four outbox rows were delivered on one recorded attempt, and the real Slack
history matched their client message IDs and Reporter bot `B0C2V3TJ230`. This is
observed delivery, not an exactly-once guarantee. No synthetic live message was
sent. Tech Scout's identity, channel, delivery policy and workflow were unchanged.

[Actual model receipts and resource observations](evidence/news-lane-observation.json)
show completed subscription calls in the news lane overlapping ten company calls
for a total of 162.953 seconds. News screening and editing did not overlap each
other. The [in-flight snapshot](evidence/news-lane-concurrent.json) separately
records simultaneous execution and instantaneous CPU/memory measurements.

During the approximately 6.6-minute post-activation observation, all nine services
remained running, with zero OOM events or restarts. The Codex container peaked at
182.1 MiB of its unchanged 512 MiB cap; the new news worker peaked at 78.6 MiB of
192 MiB. Host available memory at the final snapshot was 630.2 MiB. These are
short-window measurements, not a sustained-load or high-availability guarantee.
The unrelated maintenance process touched its 128 MiB cap (two reclaim events,
zero OOM); its limit was not changed.

At the final snapshot, 24 of 25 feeds were healthy. `yonhap-international` had one
`feed_parse_or_policy_error` and its normal retry was scheduled for 12:03 KST.
The older blocked web-discovery request remains blocked; that path is not claimed
healthy. Neither issue prevented the observed RSS screening/editorial delivery.
Publication hours and shared subscription/quota handling remain unchanged; a
global pause, exhausted subscription allowance or shared infrastructure failure
can still pause news.

# Dedicated Reporter news lane

2026-09-22. Implementation and local qualification; production activation pending.

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

The [pre-change observation](evidence/news-lane-before.json) records a screening
request waiting with `busy`, the old shared queue, and no runtime quota pause.
The pre-existing discovery failure `invalid_search_candidates` is separate from
capacity and is preserved, not automatically replayed under a new request ID.

Actual release, natural subscription completion, Slack receipts and memory/OOM
observations will be recorded after cutover. No live deployment claim is made by
the local tests alone.

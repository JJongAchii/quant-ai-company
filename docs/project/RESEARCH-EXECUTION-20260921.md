# Company research execution — 2026-09-21

User approval: **응 진행해** (2026-09-21, current conversation), for connecting the
company's approved research, 3070 execution, verification and reporting. The provider
message UUID is not exposed; no replacement identifier is invented. Engineering
acceptance replays the already-approved P11 scope and adds zero scientific trials.

## Implemented boundary

- Latest company main and PR30 consumed-evidence changes are integrated. Director
  keeps Astra/max. Reporter, existing maintenance and staff evaluation remain in the
  same service. Company-wide turn quotas are not introduced.
- A release-reviewed fixed recipe exposes only catalog, request and status to the
  director. The owner's exact approval in the same authenticated Slack thread binds
  job ID, revision and manifest. Existing thread cancellation also cancels research.
- The 3070 pulls one persistent lease. Local launch intent, an acknowledged preparation
  barrier and process identity prevent ambiguous relaunch. Lost HTTP responses retry
  the same receipt/archive. Offline waiting does not consume model turns.
- A separate Temporal queue reconciles results. The server hashes actual returned
  files and verifies the pinned original audit revalidation before rendering HTML.
  Mismatches remain `awaiting_audit` with no model-visible performance.
- Report publication registers source, artifact and director completion task together.
  The normal director final-response path supplies the owner mention. Report creation
  and actual Slack delivery are separate observable states.

The first recipe is `kr-etf-p11-replay-v1`: three fixed development-period candidates,
code `02649715bd3661826253e1ce84f8002d2a74c822`, original audit scope `e1c51cf90a17`,
objective `03d58ee449e6`, 193 original audit files and 33 economic output files.
It produces an **equivalent replay**, not a new independent research verdict.
New strategies still require their own specification and independent audit evidence.

## Verification record

Tests use real PostgreSQL and local Temporal; synthetic Slack/model inputs are
labelled. Worker lifecycle tests use real detached subprocesses. Current integration
also caught and repaired the maintenance observer's undersized saved-prompt filter
(ADR0029). Final counts and real hardware acceptance receipts belong in
`docs/project/evidence/research-execution-*` after those runs complete.

`scripts/accept_research_server.py` requires an isolated `company_research_acceptance_*`
database, synthetic identity and separate Temporal task queue. It sends no messages
to Slack and makes no model calls. Its actual 3070 backtest and report transport can
be tested on the existing server without replacing the production company processes.
Physical Mac shutdown, genuine Slack approval ingress and production activation must
be reported separately; the test harness cannot establish them.

## Operational review

Production activation uses [the runbook](../runbooks/research-worker.md) and the
explicit compose overlay. The user's existing policy remains: fixes/tests/PR are
automatic, production changes follow review. No additional cloud instance, 5090,
paid model API, live trading or new market-data subscription is included.

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

The final local suite passed **644 tests**, with two explicit skips and one live-model
test deselected. It used real PostgreSQL and local Temporal. Linux CI is recorded
separately. Reporter PR39 and its actual deployed code `4a8eb43` are integrated.

On the existing Lightsail server, a separate database, Temporal Cloud queue and
loopback API submitted two intentional engineering replays to `DESKTOP-5T00NAF`
(RTX 3070). Each had one execution receipt, six qualification/evaluation commands,
33 economic files matching the original and 193 original audit files revalidated.
There were zero new scientific trials. Four S3 objects (two HTML and two ZIP) were
downloaded again and their hashes checked, not just acknowledged by an upload API.

The first computation completed at 01:03:36 UTC, before its 01:04 restart; this is
post-computation recovery evidence. The second ran 01:14:04.419–01:14:59.842 UTC.
Its polling daemon restarted at 01:14:04.811 with the child alive before/after and
the same process identity. The test server also restarted at 01:14:06–07 UTC.
Both jobs reached `completed`, each with one source/artifact/director task.

The first received bundle was withheld by our validator's incorrect object/list
assumption for `riskConstraintResults`. The actual producer emits three constraint
outcomes in a list. After the code repair, an authenticated operator revalidated
the same archive without re-executing that job. The original withheld event and
the recovery event are preserved. This was an implementation defect, not a failed
strategy or employee evaluation. The S3 publisher was also corrected to use the
`botocore` SDK actually installed in the existing image.

[Machine-readable acceptance](evidence/research-execution-20260921/acceptance-summary.json)
and [server/S3 receipts](evidence/research-execution-20260921/server-evidence.json)
include exact commits, IDs, timestamps and validation boundaries. The independent
execution-path audit is tracked separately in `RESEARCH-EXECUTION-AUDIT-REQUEST.json`.

`scripts/accept_research_server.py` requires an isolated `company_research_acceptance_*`
database, synthetic identity and separate Temporal task queue. It sends no messages
to Slack and makes no model calls. Its actual 3070 backtest and report transport can
be tested on the existing server without replacing the production company processes.
Physical Mac shutdown, genuine Slack approval ingress and production activation must
be reported separately; the test harness cannot establish them.

The Mac transferred committed code and frozen evidence during bootstrap; it was not
the running API tunnel, executor, database, workflow worker or result destination.
The 3070 test services used its existing user service manager (`Linger=no`). Windows
logout/reboot and long-term service startup have not been tested by these replays.

## Staff feedback

The existing staff bank now includes director exercises distinguishing audit-pending
results from completed reports and engineering replay from a new confirmation, plus
operations exercises distinguishing HTTP acknowledgement from the persisted cancel
state. Their public provenance points to ADR0029; generated answer keys remain private.
The original maintenance tests also exposed a saved-prompt size cutoff that dropped
valid context, and passed after repair. These are additions to the existing periodic
evaluation/maintenance process; this change does not claim that staff have already
passed the new production exercises or acquired general expert proficiency.

## Operational review

Production activation uses [the runbook](../runbooks/research-worker.md) and the
explicit compose overlay. The user's existing policy remains: fixes/tests/PR are
automatic, production changes follow review. No additional cloud instance, 5090,
paid model API, live trading or new market-data subscription is included.

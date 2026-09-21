# Research execution activation — 2026-09-21

## Current state

PR40 was merged and production was activated at **04:51:34 UTC (13:51 KST)** on
`9a90b5eeff1f7f3685099b0c05241df1306d93fa`. The owner approved this operation with
**운영 반영·실제 Slack 인수 진행**. The approval and observed receipts are in
[`evidence/research-activation-20260921`](evidence/research-activation-20260921).

The server and 3070 initially ran the same company commit. A concurrent Reporter
session subsequently deployed `0e89d998022abfb626cf29a4ff99012cd83ffb74`; its 51 research
audit-scope files match `9a90b5e` byte for byte. The prepared job and 3070 retain their
original `9a90b5e` execution pin. The new research job is prepared in the existing
research-center thread. At **05:17 UTC (14:17 KST)** an actual Slack API read still
found no owner reply after the approval prompt. The database records **pending owner
Slack approval**; an executed production replay and final research delivery must not be
claimed from deployment or preparation alone.

## Verified activation

- PR40's final-head CI passed after the deployment repair. Forty focused tests also
  checked the maintenance release host and deployment contract. The repair preserves
  the enabled research overlay during future update, backup and rollback commands;
  it does not change the 51-file audited research scope.
- Four images were built from the merged company archive and the verified, unchanged
  quant-data archive at `d6d7d0ed066ec49541e9acdd657c9ec5692ffc52`.
- The existing backup procedure saved the old release's DB, configuration and model
  receipts. A separate S3 GET read **19,910,960 bytes** and verified SHA256
  `50f5b8d74241bc32d5045ace10b3f5d6b6c92456c3720fa0978c719faaeaacec`.
- Ingress paused with zero due model turns and zero pending/sending/uncertain outbox
  records. The additive migration completed before seven application services switched.
  Health checks passed for all eight containers, including the unchanged database.
- PostgreSQL retained the same container ID, start time and restart count across this
  activation. Its existing count of one belongs to the earlier, separately disclosed
  cleanup incident; this deployment added no DB restart. Deployed role configuration
  hashes before/after also matched exactly.
- Director remains `gpt-6-astra` / `max`; `research_control` is enabled. Reporter,
  staff development and independent staff review flags remain enabled.
- The 3070 has a clean exact company checkout and frozen dependencies. Six input hashes
  and 193 original audit-scope hashes were rechecked without launching research.
  Worker and direct server-tunnel user units are enabled with `Linger=yes`.
- Forwarding identity UID24021 was checked against host accounts and all host-visible
  process UIDs/GIDs, including container processes. Its key permits only the selected
  API forwarding destination. Direct API health succeeded and an SSH shell request
  was denied. The worker credential received HTTP401 from an operator-only endpoint.

The prior [cleanup incident](RESEARCH-CLEANUP-INCIDENT-20260921.md) remains part of the
record. The current activation does not erase that incident or establish that every
possible message during it was preserved.

## Concurrent deployment and disk observation

After this activation, disk use reached 94%. A proposed cleanup was limited to three
unused image tags for the old main-history commit `ae7517c5`; current and rollback
images were excluded. Before deletion, the expected-current-commit check failed
because the separate Reporter deployment changed `current` to `0e89d99`. Both attempts
stopped at that check. A subsequent inspection confirmed all three named images still
exist, and no container or volume was removed. No disk-space recovery is claimed.

The Reporter change and passing PR41 CI were identified independently. Its research
overlay, job records and database were preserved, and the research-scope comparison
found no changed files. The other Orca session was sent the pending job's immutable
pin and coordination requirements using the orchestration skill. Sending that message
does not prove the other session has acknowledged it. The 05:17 UTC observation found
6,124,752,896 bytes available, eight running containers and no new database restart.
This later free-space observation is not evidence that this activation removed images.
Any further release/3070 pin changes require coordination with the pending execution.

## Actual Slack acceptance boundary

Preparation was explicitly imported as an authorized administrative request,
`operator:research-activation-prepare-20260921`; it was not presented as Slack ingress.
The real Astra/max director consulted the catalog, requested the fixed recipe and
completed its three-turn preparation task. The service posted the exact manifest and
owner mention in [the research thread](https://achiisquantresearch.slack.com/archives/C0C2B9EUEGM/p1789966459779259).

- Project: `9aac0de4-2b97-5195-a720-287d324234f3`, revision 4.
- Prepared job: `939b54bb-1004-56f4-b2ae-86512617f1c6`.
- Manifest SHA256: `f0fb9af6a98756438d95b36530a583b01dec771c469d7e72333f4fea8e4ec4b0`.
- Recipe: fixed `kr-etf-p11-replay-v1`, scientific code `02649715bd3661826253e1ce84f8002d2a74c822`.
- The actual owner Slack command, accepted ingress receipt, execution archive, verified
  report/source/artifact records and final Slack delivery remain to be recorded.

The required command is `연구 승인 939b54bb-1004-56f4-b2ae-86512617f1c6 f0fb9af6a987`
in that same Slack thread. The operator must not create an owner-origin event to stand
in for it. Once the real command is accepted, the deployed server and enabled 3070
service can process the job without this Mac session; acceptance is complete only
after its actual execution and final delivery receipts have been checked.

The already-approved P11 replay adds zero scientific trials. It provides no new
confirmation, baseline comparison, capital allocation or live trading approval.

## Remaining operational limits

The running research path uses the server and 3070 directly. A physical Mac power-off
or Windows reboot was not performed. `Linger=yes` keeps the Linux user service manager
after logout; it cannot start a powered-off PC or promise Windows recovery.

Future company releases must keep the 3070's company code pin coordinated with the
server release, as specified in the runbook. The maintenance overlay repair preserves
configuration but does not itself distribute a new company checkout to the 3070.
Returned archives remain bound to their original job's company commit.

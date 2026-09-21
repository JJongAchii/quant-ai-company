# Shared-host cleanup incident — 2026-09-21

The root implementation session caused an unintended production PostgreSQL shutdown
while removing the temporary research acceptance SSH identity. This was an operator
error during acceptance cleanup, outside the new research execution code. It is not
covered by the execution-path leak audit.

## Cause

`useradd --system` allocated UID999 to `qresearch-accept`. The PostgreSQL container
also ran under host-visible UID999. When `userdel` refused removal because processes
still used that UID, the cleanup incorrectly issued `pkill -TERM -u 999`. The listed
processes were PostgreSQL, not the temporary SSH tunnel. Inspecting the numeric UID
alone did not establish ownership; terminating all processes with that UID was wrong.

## Observed impact and recovery

- PostgreSQL received a smart shutdown request at **01:28:20.153 UTC**. It completed
  its shutdown checkpoint and stopped at **01:28:20.206 UTC**.
- Docker restarted the same production PostgreSQL container. It reported ready for
  connections at **01:28:20.808 UTC**; `RestartCount=1`, health `healthy`.
- The company dispatcher also restarted once after its DB connection was interrupted.
  The API, model worker, Slack socket and Codex runtime did not restart in this incident.
- A subsequent API health request returned HTTP200 and DB health true. The inspection
  at 01:31:47 UTC found zero `sending`/`uncertain` outbox rows, zero pending outbox rows
  and zero Slack socket error-log lines since the incident.

These observations establish service recovery, not proof that every possible inbound
Slack event during the interruption was preserved. No restore operation or blanket
no-data-loss claim is made. A fresh actual director-to-Slack report subsequently
completed at 01:40:49 UTC; a separate Slack API read retrieved its final message with
the owner's mention. Its [receipt](evidence/research-execution-20260921/director-slack-delivery.json)
records the administrative reporting origin and does not claim owner research approval.

## Corrective action

The temporary worker/tunnel, both acceptance containers, temporary DB and test Temporal
workflow were stopped/removed after the test DB was backed up to the existing private S3
bucket and GET/hash verified. The original worker research artifacts and published
HTML/ZIP evidence remain available. The temporary SSH account/configuration and its API
credentials were removed. Production research activation is still pending review.

The deployment runbook now requires an explicitly selected UID checked against host
accounts **and container processes**. Cleanup must revoke the exact key/configuration
and must never use UID-wide termination to force account removal. An account-removal
failure requires process/cgroup inspection; leaving a disabled account is preferable
to touching another service. Production activation must review this incident and the
forwarding identity before enabling the new research overlay.

Evidence: `evidence/research-execution-20260921/cleanup-incident-raw.txt`,
`incident-recovery-database.json`, `database-backup.json`, `worker-cleanup.json` and
`server-cleanup-correction.json`. The initial cleanup script's claim that production
containers were unchanged was incorrect; the correction explicitly supersedes it.

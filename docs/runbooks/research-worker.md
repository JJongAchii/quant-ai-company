# Approved research worker

## Activate after code and acceptance review

1. Pin the same reviewed company commit on the server and in a clean 3070 checkout.
   Install the committed dependencies with `uv sync --frozen`; server image already
   includes the `lake` extra and S3 SDK. Run the additive DB migration before enabling
   the overlay. Preserve the normal company roles/news configuration.
2. Provision the 3070 paths in `deploy/research-worker.example.json`: clean P11
   execution checkout at `02649715bd3661826253e1ce84f8002d2a74c822`, existing Python
   3.11.15 environment, six frozen input files, and original audit evidence checkout
   at `b1c813e1e99bcc6fb9f1329562c17ea14e483c7f` plus its original ignored artifacts.
   The source input directory must contain the recipe's repository-relative paths.
   Put the separately pinned workspace objective at the configured evidence objective
   path (SHA256 prefix `03d58ee449e6`); exclude this provisioned file locally from Git
   status without changing the committed source. Verify all pins before launch.
3. Generate a dedicated worker API token (at least 32 characters), store it in the
   server secret `research-worker-token` and a mode0600 file on the 3070. It grants
   no operator access. Keep the existing backup S3 key in the worker-container-only
   `research-backup-aws.json` secret with `AccessKeyId`/`SecretAccessKey` fields. Never
   expose either credential to Codex, Slack, Git, model tools or execution artifacts.
4. Provision a dedicated SSH forwarding identity from 3070 to the existing server.
   Pin its host key using the already trusted administrative SSH connection. Permit
   only local TCP forwarding to `127.0.0.1:8000`, no remote forwards, PTY, agent/X11
   forwarding or shell. Do not copy the administrator's private key to the worker.
   The API remains bound to server loopback; no public port/domain is needed.
   Allocate an explicit host UID only after checking both host accounts and running
   container process UIDs. Docker process UIDs can exist without a host passwd entry;
   automatic system-user allocation is not proof of isolation. The acceptance cleanup
   incident used UID999, which also belonged to the PostgreSQL container.
5. Create `${STATE_DIR}/research`, owned by container UID10001. Set
   `RESEARCH_REPORT_BUCKET` to the existing approved backup bucket, then apply
   `compose.yaml` with `research.compose.yaml`. The overlay enables api, Slack
   ingress, model worker and dispatcher consistently. Existing S3 lifecycle expiry
   applies; monitor disk use and preserve needed long-lived research evidence before
   backup expiry. Generated viewing links expire after seven days.
6. Install the worker/tunnel user-unit templates on the 3070 and enable its existing
   WSL boot/user-service persistence. These are research services, not data collection
   schedules. Collector scheduling remains on EC2. Confirm service startup and
   reconnection after restarting the polling daemon; don't claim a Windows reboot
   test until one is actually performed.
7. In a Slack research thread ask the director for the registered fixed P11 replay.
   Inspect the prepared scope, then send the exact `연구 승인 <job UUID> <digest12>`
   command displayed by the service. `연구 상태` reads durable jobs without a model
   call. `연구 취소 <job UUID>` or normal thread `중단` requests cancellation.

## Review evidence

Check worker receipt code/config/input/host identity, the original audit's actual
193-file revalidation and every returned economic output hash. The original audit
alone cannot validate new outcomes. A byte mismatch stays `awaiting_audit`; new
experiments are not eligible for the replay report. Confirm the real S3 object,
source/artifact IDs, director task and actual Slack delivery receipt separately.
Review `research_jobs` through project status or operator DB access without exporting
lease tokens. Worker logs/launch records remain private on the worker.

## Recovery and rollback

- Worker offline: leave the existing queued/claimed job. Do not clear a lease or create
  another job because a heartbeat timed out.
- Lost preparing response: retry the persisted heartbeat before launch. Only the
  server's `running` acknowledgement authorizes launch. `cancel_requested` forbids it.
- Ambiguous launch/dead process without a terminal receipt: state stays `uncertain`.
  Reconcile its immutable launch/boot/PID receipts before an operator resolves it.
  Never delete local state or manually requeue the lease to make a dashboard green.
- Publication failure: the already-received archive stays immutable and is retried
  under the same object identity. A model/provider outage can delay the final summary;
  the report/source and pending director task remain in PostgreSQL.
- After a reviewed validator repair or restoration of the exact original evidence,
  an operator may call `POST /v1/research/jobs/{job_id}/revalidate` with the existing
  `artifact_sha256`, `expected_revision` and reason `validator_repaired` or
  `evidence_restored`. This requires the operator token, rejects worker/model access,
  and records the current validator commit. It only resumes validation of a withheld
  archive; it does not mark the audit passed, change the execution receipt or relaunch
  a backtest. Changed owner revisions and mismatched archives remain blocked.
- To roll back, cancel or finish active research first, preserve jobs/receipts and
  returned artifacts, stop the worker/tunnel units and remove the research overlay.
  Return to the previously reviewed company image. Leave additive research tables
  and evidence intact. Remove only the dedicated forwarding identity and worker token
  if deactivating permanently; keep administrator identities unchanged.

An isolated acceptance environment must use a separate DB/task queue, loopback port
and temporary forwarding identity, and must preserve the production BOT processes.
The 2026-09-21 acceptance did not preserve this boundary during account cleanup;
review the [incident](../project/RESEARCH-CLEANUP-INCIDENT-20260921.md) before activation.
Remove its own container, credentials/tunnel, test workflow and DB only after evidence
has been recovered. Never run broad Docker/Git cleanup against the shared server.
Revoke the exact SSH key and forwarding configuration first. If removal of an idle
temporary account fails, inspect its processes and container cgroups; do not force the
cleanup with UID-wide process termination (`pkill -u`, `killall -u`, or equivalent).
Leave the disabled account for investigation rather than stopping another container.

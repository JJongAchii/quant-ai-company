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
   Persist `COMPANY_RESEARCH_ENABLED=true` in `runtime.env` as well. The reviewed
   maintenance release executor reads that flag to preserve the overlay during
   updates, backup and rollback. Keep the 3070 company checkout pinned to the same
   release when updating the company; completed archives retain their original pin.
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
### Proven company-pin precheck failure

The 2026-09-21 activation left the worker's `config.company_commit` on an old
release after the checkout and server/job pin had advanced. Check all three
identities together during a release: clean worker checkout HEAD, active worker
configuration pin, and server `COMPANY_CODE_COMMIT`/the approved job's company pin.
Do not amend the approved job pin to fit a stale worker configuration.

`deploy/research_preparation_recovery.py` is an operator CLI for this one failure
shape. It is not a worker/model tool or public API. It accepts only the exact
acknowledged `failed` attempt with reason `repository-commit-mismatch`, the original
8-file hash manifest, no subdirectories/command receipts/output, a dead executor
and process group, and a corrected clean company checkout. Other failures remain
blocked. The fixed P11 recipe, science code, snapshots and output hashes stay fixed.

1. Stop the polling unit and preserve its failure evidence. Correct only
   `company_commit` in the active worker config; the helper requires every other
   config field to match the failed snapshot. Keep polling stopped through both
   commands below. The helper additionally takes the existing `worker.lock`.
   Keep the recovery code in a **separate clean Git checkout**, detached at the
   reviewed recovery-tool commit, on both hosts. Do not switch the running company
   checkout away from its approved release to install this helper. `tool_identity()`
   resolves the helper file's own checkout, requires it to be clean, and records its
   exact HEAD. Use the existing company Python environment with a process-local
   import path when a separate tool environment is unnecessary:

   ```sh
   PYTHONPATH="$TOOL_CHECKOUT/src" "$COMPANY_PYTHON" \
     "$TOOL_CHECKOUT/deploy/research_preparation_recovery.py" <subcommand-and-options>
   ```

   `TOOL_CHECKOUT` is the separate committed tools checkout; `COMPANY_PYTHON` is the
   existing company interpreter. This changes neither the runtime checkout nor its
   `0e89d998022abfb626cf29a4ff99012cd83ffb74` configuration/job pin in this incident.
   The server command must inherit the existing approved DB/settings environment;
   do not pass credentials on the command line. Both helper invocations must use
   the same tool commit and actor as the generated proof.

2. Prepare a private request JSON using the observed job/project IDs, revision,
   recipe/manifest, original Slack approval event and approved user, failed sequence,
   failed launch ID, old/current company commits and SHA256 of each of the 8 original
   attempt files. It contains no lease token. Use the reviewed helper from a clean,
   committed tool checkout on each host and the same `operator:<actor>` identity.
3. On the worker run:

   ```sh
   uv run --frozen python deploy/research_preparation_recovery.py prepare-local \
     --config /operator/path/research-worker.json \
     --request /operator/path/recovery-request.json \
     --recovery-id <one-fixed-UUID> --actor operator:<actor>
   ```

   The helper preserves every original byte under
   `state_dir/failure-history/<job>-<recovery>` and keeps the original directory
   under `state_dir/preparation-recoveries/<recovery>/original`. It installs only
   four prepared files, with the same assignment/lease/sequence and a new launch ID.
   The corrected config pin is the only config change. A public `proof.json` appears
   only after durable installation; private state and launch records never go to Git
   or stdout. The proof's `proof_digest` is SHA256 of canonical JSON (sorted keys,
   comma/colon separators, UTF-8, `ensure_ascii=False`), not a hash of whitespace.
4. Transfer only that secret-free proof to the server through the existing approved
   administrative path. In the normal server configuration/DB environment run:

   ```sh
   uv run --frozen python deploy/research_preparation_recovery.py resume-server \
     --proof /operator/path/proof.json --proof-digest <returned-digest> \
     --actor operator:<actor>
   ```

   If the API image has no Git, do not install Git or replace the runtime image
   for this repair. On the administrative host, verify the separate tool checkout's
   clean status, exact HEAD and the helper's SHA256. Copy only that reviewed helper
   and the secret-free proof into the API container. Verify the copied bytes again
   before importing them. In the existing API entrypoint/settings environment, an
   operator may then call the reviewed function directly with that attested commit:

   ```python
   import hashlib, importlib.util, json, sys
   from pathlib import Path
   from quant_company.company import Company
   from quant_company.config import Settings

   path = Path("/tmp/research_preparation_recovery.py")
   assert hashlib.sha256(path.read_bytes()).hexdigest() == "<host-verified-helper-sha256>"
   spec = importlib.util.spec_from_file_location("operator_preparation_recovery", path)
   module = importlib.util.module_from_spec(spec)
   sys.modules[spec.name] = module
   spec.loader.exec_module(module)
   proof = module.RecoveryProof.model_validate_json(Path("/tmp/proof.json").read_text())
   result = module.resume_server(
       Company(Settings(), roles={}), proof, "<returned-proof-digest>",
       actor="operator:<actor>", tool_commit="<host-verified-full-tool-commit>",
   )
   print(json.dumps(result, sort_keys=True))
   ```

   This bounded administrative invocation replaces only the unavailable Git
   attestation step inside `tool_identity()`. It does not change the function's
   proof/row checks or grant this operation to the worker or a model. Preserve the
   host commit, clean-status and both helper-byte checks in the operator evidence.

   The helper locks project before job, checks the original approval, active revision,
   manifest, server/job commit, lease hash, failed update digest and sequence, and
   refuses any received artifact or other unresolved worker job. It changes only the
   current scheduling state to `claimed` and clears the stale notification marker.
   The lease, approval, sequence, update digest and prior error are preserved. An
   attributable `research_preparation_recovered` event records both launch IDs,
   failure/proof hashes, actor and the actual recovery-tool commit.
5. Resume polling only after both helpers return successfully. The normal worker
   advances the existing sequence for `running/preparing`, waits for the server's
   `running` acknowledgement and then spawns. Duplicate recovery IDs return the
   original result without rearming, including after execution has progressed.

An incomplete local journal is a manual reconciliation blocker. Never delete it or
restart polling to finish the operation; a retry cannot install another attempt.
Preserve both failure copies and the journal for review. The receipt distinguishes
one failed technical launch from **zero economic executions** at that point.
`execution_count=1` in the eventual replay receipt describes the sole economic
replay, and this recovery adds zero scientific trials.

### Other recovery paths

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

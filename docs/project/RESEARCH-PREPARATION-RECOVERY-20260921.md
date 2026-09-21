# Production research preparation recovery — 2026-09-21

## Observed failure

The owner sent the complete current approval command in the existing research-center
thread. Actual Slack API data and PostgreSQL agree on owner `U0C250E23NW`, revision 5,
job `6a64df0e-9c60-555f-aa3a-a80a70e7f985`, manifest
`f0fb9af6a98756438d95b36530a583b01dec771c469d7e72333f4fea8e4ec4b0` and inbound event
`slack:T0C1YRDRPNF:C0C2B9EUEGM:1789970318.691419:director`.
Approval was committed at **05:58:39.116 UTC** and the 3070 claimed the job at
05:58:42.495236 UTC. The approval was not constructed by an operator.

The root operator synchronized the worker's Git checkout to the server's `0e89d998`
but omitted the separate `company_commit` field in its runtime configuration. That
field still contained `9a90b5e`. The executor's existing repository identity check
therefore stopped preparation with `repository-commit-mismatch`. This was an
operator configuration error, not an unsuccessful strategy result.

The failed child has launch ID `942d7e7c58694b8eb85172d5be20decf` and a matching
terminal receipt. The eight files in its directory were retrieved with rsync and
their hashes matched the worker's recorded hashes. There was no checkout, command
receipt, audit command, economic output or ZIP. The executor and its process group
were no longer alive. One preparation process started; **zero research commands or
economic executions** ran. Independent hosts' wall clocks are not used to infer
ordering; the durable approval, claim and heartbeat sequence provides that ordering.

The server preserved the approved job as `failed`, sequence 3, and the director's
owner-tagged failure notice was delivered. Its reduced public code `worker_error`
does not replace the detailed local terminal receipt.

## Repair scope and preserved evidence

The owner already authorized this fixed P11 replay and operational acceptance. The
repair retains the same approval event, owner, project/revision, manifest, lease and
sequence. The recipe's scientific code, data, costs, period and candidate set remain
frozen. A failed preparation is not counted as a new scientific trial or hidden from
the eventual run record.

At 06:04 UTC the dedicated research polling service was stopped, the failed process
and group were checked, and only the active configuration's `company_commit` was
corrected to `0e89d998022abfb626cf29a4ff99012cd83ffb74`. The original configuration and
failed attempt remain preserved. The server, Slack, model, database and direct SSH
tunnel services were kept running. A fresh server backup was saved before recovery.

The operator recovery tool is limited to this demonstrable failure before any
research command. It must reject ambiguous outcomes, changed approvals/revisions,
running processes, unexpected files, or any evidence that research has started.
The old directory is retained with hashes; a new prepared attempt uses the existing
worker's server acknowledgement and launch path. Repeating the same recovery must
not create another execution. It adds no model or worker permission to approve jobs.

## Acceptance status

The actual bound Slack approval is verified. Configuration correction and failure
preservation are complete. Recovery implementation, tests and independent review
are in progress; the production replay and final report have not yet completed.

Receipts are in
[`evidence/research-recovery-20260921`](evidence/research-recovery-20260921).
Raw lease-bearing worker files are retained privately outside Git. The separate
[approval-only routing defect](RESEARCH-ACTIVATION-20260921.md#actual-owner-ingress-and-approval-only-routing-defect)
remains a proposed repair; it did not prevent the full v5 command from being accepted.

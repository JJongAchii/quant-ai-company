# ADR 0031 — Persistent, evidence-driven autonomous research

Status: approved implementation plan, 2026-09-21. Production activation and the
first scientific brief remain separate decisions under the existing review policy.
User authorization: “Implement the plan.” No provider message ID was exposed.

## Outcome

An owner approves a research mission once. Within its immutable objective, data,
development window, code scope and resource policy, employees propose hypotheses,
challenge assumptions, implement experiments, interpret results and choose follow-up
work. A completed report is a checkpoint in a continuing mission. The first executable
adapter is domestic ETF strategy research; model/claim missions remain draft-only
until their own evaluators are implemented.

The owner selected continuous follow-up within the upper goal, key discussion in
each research thread, and direct owner work ahead of autonomous background work.
Keep current best evidence, including negative findings. Agreement between employees
does not establish empirical validity. Do not promise a globally optimal strategy.

## Responsibilities and authority

- Director owns priority and decisions; researcher proposes hypotheses and interprets
  outcomes; financial strategist challenges the mechanism; data checks availability.
- Existing engineer and validator roles receive mission-scoped work. Engineer changes
  only approved research paths. Validator uses a separate request context and cannot
  implement the candidate it audits. Existing model/effort policy is retained.
- Mission approval is an authenticated owner event bound to its exact digest and
  Slack thread. Models cannot approve themselves or mutate approved criteria.
- Short approval text must never reach the general amendment router while a relevant
  approval is pending. Buttons carry an opaque server-side binding, not authority.
- Corporate service/playbook changes use the existing reviewed maintenance PR flow.
  Research commits may execute inside the approved mission without merging main.

## Durable state and contracts

PostgreSQL owns mission, hypothesis/review/selection and trial state; existing Temporal
reconciliation schedules one durable stage at a time. Model calls use the existing
provider/turn machinery. Every stage uses stable identities and stored responses.
Do not create a separate generic agent framework or an in-memory research queue.

Preserve the P11 equivalent-replay contract verbatim. New adaptive assignments carry
an approved mission digest, proposal, exact code/config/input identity and a registered
execution profile. Their outputs are measured and audited, never compared with invented
expected performance hashes. Technical repairs and scientific trials have separate
records. Cycle budgets are frozen by the future scientific brief; all cycles retain
cumulative trial history. No company-wide call cap or technical retry exhaustion is
reintroduced. An unchanged failure waits with backoff and a concrete repair/resume
condition instead of generating identical experiments.

An owner may also freeze `search.max_total_trials` for a finite research scope. The
counter survives cycle renewal, excludes technical failures, and stops new selection
after the last result's interpretation/audit/publication. An omitted total preserves
existing serialized mission identities. The first six-variant brief proposes a total
of six; this is a scientific scope, not a service-wide rate limit.

The canonical loop is proposal → independent challenge → decision → implementation →
qualification → execution → interpretation → best selection → audit/report checkpoint
→ follow-up. Decisions cite completed predecessor evidence; first decisions cite the
registered baseline. A worse latest trial does not replace the best feasible trial.

## Execution and audit placement

Only the existing 3070 executes heavy work. Owner-submitted work has queue priority;
running experiments are not preempted. Offline workers retain durable waiting state.
The worker has bubblewrap available and a read-only namespace smoke test passed during
planning. New code runs without host home, credentials, sealed inputs or network, with
only approved runtime/code/input mounts and a per-job writable output directory.

Use committed code and hash-bound input snapshots. Record launch intent before spawn,
reconcile process identity after restart, and never resubmit an uncertain execution.
Release preparation validates worker checkout and configuration as a pair and retains
the exact version needed by already-approved jobs.

For this company path, the server receives an immutable audit package and invokes an
independent validator plus existing qlab verification/report tools. This is the explicit
company-specific replacement for the Mac retrieval/audit step in personal research;
the personal workflow is unchanged. Audit scope includes reported trial versions and
actual artifacts. Missing/unverified audits withhold performance from Slack and shared
knowledge. No live capital allocation or new confirmation is authorized by discovery.

A terminal validator runtime failure requires an operator reconciliation note before a
new inference ID is created. If the same attempt is still waiting, its completed reads
remain attached to that attempt. If the controller has already opened exactly one empty
successor attempt, prior chunks whose full-file SHA-256 still equals the successor's
immutable file map remain as recovery evidence, but they do not count as evidence seen by
the new provider thread. The successor must receive every required text byte again; changed
binding or package files are likewise read from the new immutable map. The failed receipt,
superseded turn and retry event remain durable, and the final audit checker verifies prior
chunks against their original completed validator turns while requiring a complete current
attempt read set. This exception applies only to validator audits; ordinary scientific
retries remain attempt-local. Audit output starts with the qlab frontmatter contract so a
completed prose judgment cannot fail later merely because its machine-readable header was
omitted.

The immutable package may also contain operator-produced causal supplements when their
source contract, verifier source and execution receipt are all included in qlab scope.
The service checks the receipt against every trial's exact input hashes and protected-code
hashes before packaging it; a receipt from a different data or engine version is rejected.
For the first KRX ETF snapshot, the supplement states the missing historical collection/
revision vintages rather than inferring them. A committed checker on the 3070 verifies
point-in-time keys, next-session fills and invariance of all registered features and
simulations to deterministic positive per-ticker adjusted-price rescaling, while recording
no performance values. This addresses latest-anchor rescaling only and does not prove that
KRX never corrected a historical official return or classification.

Core engine, adapter, signal and causal-supplement chunks remain resident in the validator's
final prompt after they are read. Other large result chunks remain hash-addressed and may be
evicted after their read receipt is stored. If the resident evidence cannot fit the bounded
provider context, the stage fails closed instead of asking the validator to judge code bytes
that are no longer present.

## Acceptance and rollout

Use real disposable PostgreSQL/Temporal for approval races, restart, duplicate/lost
responses, priority, lifecycle and publication tests. Test the new worker sandbox on
3070 with synthetic non-performance fixtures before a scientific run. Distinguish
fixture model/Slack tests from actual subscription and Slack receipts.

Prepare the first ETF scientific brief from existing P11 history and real data facts.
Do not execute new economic trials before its separate owner approval. After approval,
require at least two real distinct trials with evidence-linked feedback, preservation
of the incumbent, independent audit, published report and automatic follow-up selection.
All production activation includes a concrete tested release and rollback plan.

Company implementation and operational evidence live only in quant-ai-company. The
qws metadata repository stores its contract/checkpoints; quant-lab holds isolated
research code, existing qlab utilities and scientific history. Existing P11 receipts,
Reporter operation and other research workspaces are preserved.

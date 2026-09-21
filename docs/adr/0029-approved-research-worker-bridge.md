# ADR 0029 — Owner-approved research execution outside the Mac session

Status: implementation; production activation requires the existing review policy.

The company already owns Slack conversations, PostgreSQL records and Temporal execution.
The first ETF pilot was coordinated by a Mac session. This change connects those records
to the existing RTX 3070, without running backtests in the company container.

## Decision

The 3070 pulls work through an outbound SSH tunnel to the server's loopback API. A dedicated
worker token can only poll, heartbeat and return its assigned research work. It cannot call
operator endpoints. No domain, public research API, extra host or paid model is required.
Provisioning uses a restricted forwarding-only SSH identity and an independently supervised
worker service; the Mac is not a relay. The collector remains on EC2.

The first committed recipe replays all three already-approved P11 candidates using exact
code, configurations and development snapshots. Models may request that recipe and inspect
status; they cannot supply commands, paths, a different commit or an approval. A human owner
confirms the displayed job ID and manifest digest in the same authenticated Slack thread.
The approval event, project revision and immutable manifest are stored before work is offered.

PostgreSQL assigns one durable worker lease. Timeout means disconnected/unknown, never a
new lease or permission to run again. The worker records launch intent and receipts on disk,
uses process identity and a lock, and reconciles an ambiguous launch after restart. Cancel or
revision changes prevent new execution and publication; running work is stopped and reconciled.
Worker loss leaves an observable persistent job, with no retry-count exhaustion or fallback.
Temporal runs short reconciliation activities on a separate queue so research cannot occupy
the only model activity slot. Terminal notifications and report records commit together.

## Evidence and scope

An existing audit is not a fresh verdict on new results. The replay must match the frozen
audited input/configuration and every registered economic output byte for byte. The worker
revalidates the original independent audit against its complete original scope. The company
checks the actual returned files against the committed reference and publishes an explicitly
labelled equivalent-replay report derived from those files. The original audit is linked with
its scope and hash. This is engineering acceptance and adds zero scientific trials.

Mismatch, missing files, wrong host or unavailable audit keeps performance out of model
context and Slack. New research recipes require an approved specification and actual independent
audits for their own scope; the replay verdict cannot authorize a new experiment or deployment.
The original SEARCH, audit, data and pilot output remain unchanged.

## Acceptance

Use real PostgreSQL/Temporal for duplicate approval, lost claim response, worker restart,
offline waiting, project revision/cancellation races and output identity tests. A real server
to 3070 replay proves the transport and execution path. Report explicitly whether Slack ingress,
model calls, physical Mac shutdown and production deployment were actually exercised. Preserve
the fixed recipe, source artifacts, delivery receipts and rollback instructions in this repository.

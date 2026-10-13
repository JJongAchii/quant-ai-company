# ADR 0038: Owner-controlled model assignments

Date: 2026-10-06. Status: implemented locally; production activation is separate.

The owner requested replaceable employee models, persistent explicit selections, and a
selection for one task. Initial delivery includes model discovery, pin/reset, history,
rollback and task overrides. Automated ranking, recurring evaluations and automatic
upgrades are later work. A new release is not evidence of improved company task quality.

## Authority and state

The existing model-account owner and dedicated channel also own persistent assignments.
Exact Slack commands are parsed by the service before any model dispatch. Signature,
workspace, user, channel and bot-echo checks remain in force; authority is checked again
when executing a command. Task selection is allowed in an ordinary permitted work channel
or DM addressed to the employee, never in the account-control channel. Unsupported syntax
gets help without model inference. Model proposals cannot change assignments.

PostgreSQL stores commands, immutable revision snapshots, catalog checks and execution
bindings. The existing account-control Temporal activity processes one assignment command
per tick. Account/model command work never queues behind inference. The activity change
does not change workflow commands or require a new workflow history branch. A command,
its policy change and its Slack outbox receipt commit in one transaction. Duplicate events
and activity retries recover the existing outcome. Uncertain Slack delivery follows the
existing outbox reconciliation policy.

Precedence is task override, employee pin, then packaged role defaults. A maintainer without
its own pin inherits the engineer assignment. Optimized news search and screening have
their own targets, preserving their current small-model defaults. Non-optimized news
search inherits Reporter unless explicitly pinned. Quant Scout's model-backed editorial
pipeline can be assigned; its Slack identity remains outbound only. Tech Scout has no
model. The independent Claude review runtime is outside this Codex policy.

An explicit director model selection is an owner-approved replacement of ADR 0027's
concrete flagship ID for new requests. Director still requires `max`; the service does
not infer "best" from a model name, version or catalog order. Packaged startup defaults
retain their existing validation. No capability, staff activation or delegation permission
is changed by assigning a model.

## Discovery and validation

The private runtime exposes bearer-authenticated `GET /v1/models/{primary|backup}` and
uses the pinned official Codex app-server `initialize`, `initialized`, `model/list` protocol.
Pagination and process I/O are bounded. No thread or turn is started. Only model IDs and
supported efforts leave the runtime. Account login remains official ChatGPT authentication;
no Slack/database credentials, paid API fallback or account rotation are introduced.

CLI 0.154.0 offers `--ignore-user-config` on `exec`, but not app-server. Catalog discovery
therefore refuses a profile containing `config.toml`, uses a temporary empty working
directory, and explicitly disables tools/plugins/hooks/agents. It requires the same
validated CLI version and a ChatGPT login. The account's catalog is not a guarantee of
remaining capacity or successful inference. Provider errors are sanitized.

Every pin, reset, restore or task selection validates the exact model/effort against a
fresh catalog for the selected account. An account revision change during discovery
rejects the command. Rollback creates a new revision and validates restored settings,
including defaults exposed by removing a pin. Unavailable targets are not substituted.
Status/history remain available without fetching a catalog or spending inference tokens.

## Execution boundaries

Policy resolution occurs within the transaction that freezes a new request. Shared policy
locks serialize with activation; readers across processes use the same committed revision.
The wire `ProviderRequest` and its digest do not acquire optional policy fields. Selection
provenance is stored separately in `model_execution_bindings`.

Frozen requests keep their original model and effort on retry. Web search inherits the
calling turn's frozen selection. Audit session continuations inherit their predecessor's
model and effort because the runtime binds a session to those values. Staff practice freezes
selection at enrollment; maintenance comparisons/replays keep their original configuration.
Global pin changes affect new requests, not already frozen runs.

Task selections persist for that employee's turns within the same task. Delegated tasks,
new follow-up messages that create another task, and separately scheduled future tasks use
their own assignments. Task overrides do not edit the employee's global pin.

## Rollout and evidence

The additive migration is idempotent. `MODEL_ASSIGNMENTS_ENABLED` defaults to false and
requires configured account control. Every request-producing worker, including any pinned
research worker, must be updated before activation. No legacy gateway rewrites already
frozen requests. Disabling the flag is not rollback; use a revision restore while running
the updated code. See [runbook](../runbooks/model-assignments.md).

Validation distinguishes real PostgreSQL/Temporal, synthetic Slack and Codex subprocess
responses, and an opt-in real CLI configuration/protocol probe with empty authentication.
That probe is not real account availability or real model inference evidence.

Sources: [Codex model/list](https://learn.chatgpt.com/docs/app-server#list-models-modellist),
[model selection guidance](https://developers.openai.com/api/docs/guides/model-selection).

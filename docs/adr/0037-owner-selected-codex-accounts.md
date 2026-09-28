# ADR 0037: Owner-selected Codex authentication profiles

Date: 2026-09-23. Status: implemented and active in production; both distinct accounts enrolled.

## Decision

The company has one selected ChatGPT profile, shared by its Codex-backed staff, news,
web searches and maintenance. The configured owner selects `primary` or `backup`
with an exact command in a dedicated private Slack channel shared with the director app.
Ingress and execution both check the configured owner and exact channel ID. Account
commands elsewhere are ignored before model dispatch. Authentication is enrolled separately
by an operator using the official interactive Codex device login. No automatic account
rotation, API fallback, credential transfer through Slack or model-generated switch is added.

PostgreSQL stores commands, owner/event identity, selected profile/revision, request
bindings, quota cooldowns, wake receipts and audit outcomes. Slack ingress only commits a
command. A dedicated Temporal task queue processes it independently of inference, using
the existing private runtime to check login status. Replies use the existing durable outbox.

The isolated runtime receives an allowlisted profile and revision in HTTP headers.
`ProviderRequest`, its digest and request ID are unchanged. Both profiles share the existing
receipt directory, cancellation map and company/news locks; each has a distinct `CODEX_HOME`.
The runtime holds no Slack/database credentials. Login status reports authentication only,
never estimated token balance or an assurance that the next request will succeed.

## Request and switch boundary

The short database reservation transaction serializes with account selection. A reservation
made before the switch keeps its profile even if its subprocess starts later. New requests
and requests explicitly denied for quota can use the newly selected profile. A request
waiting behind a known account cooldown has not been dispatched and can also move.

The runtime independently permits a change of profile only for an explicit structured quota
denial with a newer selection revision. Before starting, it archives the denial and writes a
durable running receipt. Completed calls return the original result. Orphaned running,
cancelled and failed/uncertain calls are preserved for reconciliation; changing account
cannot authorize another inference. Legacy receipts without a profile belong to `primary`,
revision 0. This also protects calls submitted before the feature was enabled.

A quota response pauses only its profile. Selection does not reset a known cooldown, and
an exhausted target is rejected until its next permitted attempt. Both accounts can be
unavailable; tasks then remain queued. One owner notice is persisted per selected revision
after an account command establishes its dedicated channel thread as the destination. Lost Slack replies
retain the outbox's uncertain-delivery semantics.

Successful switches bring explicit quota waits forward without editing frozen inputs or
completed/blocked results. Ordinary turn workflows receive persisted, retryable wake
signals; polling news/staff/maintenance resume on their next cycle. The signal change is
Temporal-patched per iteration for old histories. Already-recorded legacy timers finish
before their next iteration adopts signals. A quota denial arriving after a switch gets a short
retry delay for the same request, avoiding a stale 15-minute wait.

## Operations and limits

Feature activation is explicit and requires an owner in `SLACK_ALLOWED_USERS`. Disabled
deployments retain the existing primary profile. Runtime and all model consumers must be
covered together. When research execution requires a pinned worker binary, the private
account gateway applies the same database routing to its unchanged requests. Its image
and company code identity stay pinned; only its model endpoint configuration changes.
The gateway also owns the control Temporal worker and has no Slack or ChatGPT credentials.
Old workflows poll the durable quota gate every 30 seconds without repeating inference;
already-recorded legacy timers keep their remaining duration once. Other updated clients
route directly. Backups stop this additional database writer, and later releases retain
the overlay and validate the exact pinned worker image.

Credentials remain only in host-mounted private directories. Enrollment never copies or
overwrites another profile. An expired login needs interactive reauthentication. No model
call is used as a status probe. Existing Codex credit/purchase settings remain an operator
responsibility; this feature neither enables purchases nor promises subscription capacity.

See [runbook](../runbooks/model-accounts.md) and [verification](../project/MODEL-ACCOUNTS-VALIDATION.md).

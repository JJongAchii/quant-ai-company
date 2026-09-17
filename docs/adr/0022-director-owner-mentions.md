# ADR-0022: Notify the Slack owner when a result or decision is ready

Date: 2026-09-17. Status: accepted. User request: the director tags the requester
for final conclusions/results and when their confirmation is needed.

The server adds one explicit Slack user mention to the persisted outbox text for
a root director's completed answer (including a question awaiting owner input),
conversation clarification, or blocked work requiring intervention. Artifact-only
completion produces a bounded result summary. Maintenance PR review requests,
final review outcomes, and application completion/blockers also notify the owner.

Progress, delegations, peer messages, employee results, status queries, approval
receipts and automatically retried budget waits do not mention anyone. A director
acknowledging a submitted maintenance review leaves the eventual notification to
the maintenance service. A PR's stable review notice carries the mention; a
separate diagnostic progress notice does not produce a second review ping.

The recipient comes from the project's stored owner, checked against the Slack
allowlist; model text cannot choose it. Notification tokens supplied in message
content are escaped, preserving ordinary links. Eligible messages remove any
duplicate owner token before inserting the single server-owned mention. No
database migration or new model decision field is required. Decisions stay typed
and old durable responses retain their original shape.

Rendering happens when the message is committed, not when dispatched: queued
progress stays unmentioned even if the task completes before delivery. Existing
message IDs, revision fencing, rate-limit retries and uncertain-send handling
remain in effect. Historical outbox messages are not rewritten or replayed.

Reference: [Slack message formatting](https://docs.slack.dev/messaging/formatting-message-text/#mentioning-users)
defines `<@USERID>` and escaping. Automatic name parsing remains disabled (no
`link_names` argument); explicit mentions are used. A delivery receipt verifies the
message, not the user's device notification preferences or that they read it.

Validation: real PostgreSQL regression tests cover recipient scope, final versus
progress, clarification/blockers, artifacts, child/peer messages, maintenance
intake/PR/final outcomes, replay and mocked Slack rate limits. Production delivery
evidence is recorded separately under `docs/project/evidence`.

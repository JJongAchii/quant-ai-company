# Quant Scout publication recovery — 2026-10-06

Restored and verified: the seven policy-blocked research cards were delivered to
`#quant-feeds` by Quant Scout at **09:02:27–09:08:34 KST**, one recorded send attempt
each. Real `auth.test` and seven `chat.getPermalink` responses passed. Collection
and editorial Temporal workflows remained running, publication was enabled, and
all 16 enabled sources had zero recorded failures at 09:30 KST.

## Cause and scoped fix

Disk cleanup had already restored the server before this repair. The remaining
failure was configuration drift: the preserved Quant worker retained eight Slack
allowed channels, while API/dispatch had nine after another channel was added.
Policy v21 fingerprints that global list, so valid cards failed closed as
`quant_feed_policy_changed` before any Slack attempt.

Only the Quant worker was recreated, with the same verified application image
`sha256:2110d56b77c35c89bfab28185f4924e4eec2db85878b4cab9ec92dbdaef73d79`
(`ea092f4`). Its only environment change was `SLACK_ALLOWED_CHANNELS`. The dedicated
model runtime, API, dispatch, news, company/research, housing, PostgreSQL and every
other existing container retained their ID, image, running state and restart count
across the scoped change. The shared deployment lock and drained Quant lane were
checked before recreating the worker.

## Seven-post reconciliation

This was not an unconditional digest replacement or a new model critique.
`scripts/reconcile_quant_unsent.py` proves the current policy differs from the old
one only by the appended allowlist entry; Quant's owner/channel, permissions,
sources, role, web setting and enabled publication gate must still match.

Before changing the exact seven rows, it requires zero attempts, no send timestamp,
no started delivery, the expected stale error, the unchanged source configuration,
and the same frozen original hashes and evidence spans. It deterministically
revalidates the settled draft and separate critic responses, all quality checks,
stored brief equality and the exact rendered card. Each original model call,
response, bundle, original and card is preserved. Other states, ambiguous effects,
material changes, corrections and prior-delivery cases are refused.

The read-only plan was persisted before applying its exact digest. Applying made
14 PostgreSQL updates (seven publication bindings and seven existing outbox states),
zero model calls and zero direct Slack writes. The normal dispatch service delivered
the cards, retaining the existing 06:00–24:00 KST window and >=60-second spacing.

Example actual delivery:
[Oracle-Parametrized Constant Function Market Makers](https://achiisquantresearch.slack.com/archives/C0C3K8ZB9PB/p1791245314272139).

## Validation and receipts

- Full local regression: **1,437 passed, 46 skipped, 1 opt-in live test deselected**;
  Ruff and `git diff --check` passed.
- Targeted Quant regression: **163 passed, 1 skipped**. Reconciliation tests use
  real disposable PostgreSQL databases and explicitly simulated model/Slack inputs.
- Actual production alignment, PostgreSQL reconciliation, running Temporal activities,
  Slack acceptance, bot identity and permalinks are separately recorded in
  [RESULT.json](RESULT.json), [alignment.json](alignment.json), [plan.json](plan.json),
  [apply.json](apply.json), [delivery.json](delivery.json) and [workflows.json](workflows.json).
- The historically poor Robeco governance post remains unchanged, as instructed.
  No new paid API, resource increase, credentials, bot scope or 48-hour prerequisite
  was introduced.

## Operational boundary

The original v21 policy still includes the global allowlist. Future channel-list
changes must synchronize preserved Quant workers and dispatch, then compare their
actual policies before publication. These helpers are explicit operator actions,
not an automatic uncertain-write replay mechanism. Existing requested/applied
receipts refuse blind reruns; reconcile observed database state after a crash.

The existing chat:write-only bot cannot read channel history. Verification uses
its identity, settled outbox timestamp and Slack permalink instead. This recovery
does not independently reproduce paper performance or establish long-run curation
precision. Poor evidence continues to be held rather than published.

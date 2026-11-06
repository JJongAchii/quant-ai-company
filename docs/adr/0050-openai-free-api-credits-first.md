# 0050: Owner-capped OpenAI API budget before ChatGPT subscription

Date: 2026-11-05; updated 2026-11-06. Status: proposed implementation; production activation pending.

The owner requests API-first staff execution followed by the existing signed-in Codex subscription. On November 6
the owner explicitly replaces the grant-expiry prerequisite with an assumed **$120 monthly service spend cap**.
This authorizes bounded API spend; it does not establish that every API charge is covered by a free subscription benefit.

Version 2 policy has a stable budget ID, monthly_cap_usd at most 120, and explicit api_spend_authorized=true.
It derives an immutable KST calendar-month window and ID, using the shared durable locked ledger. No billing-cycle
dates or free-only assertion are required. Version 1 confirmed-expiring-grant policies remain compatible.
The service reserves conservative maximum cost before dispatch, settles bounded token usage and preserves unknown
reservations. API budget exhaustion or explicit nonexecuting rejection uses the subscription; possibly executed
requests never automatically replay. Credit exhaustion pauses API until the next monthly window, without purchasing credits.

Preserve selected models/effort, input digests, account revisions, typed proposals and canonical receipts. Model family
provider=codex remains wire-compatible; usage.backend, api_budget_mode and billing_mode identify the actual path.
Native search, pinned continuations, images, backup accounts and unpriced/legacy-effort requests keep their subscription path.
No API key enters the CLI environment. Operator status says API monthly budget rather than verified free credits.

Production follows the reviewed-main release path. The release operator receives the final main SHA, the existing key
through a private file, the exact version 2 policy and OPENAI_API_ENABLED=true. Development does not edit the host.
See [runbook](../runbooks/openai-credits.md) for activation and reconciliation.

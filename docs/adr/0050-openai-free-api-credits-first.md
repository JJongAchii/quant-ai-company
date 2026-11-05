# 0050: Confirmed free OpenAI API credits before ChatGPT subscription

Date: 2026-11-05. Status: implementation proposed; disabled until owner billing facts and operator activation.

The owner asks to use the Pro subscription's expiring API benefit before the staff's signed-in Codex allowance.
The benefit covers direct Responses API usage and excludes API-key Codex usage. Keep the official ChatGPT
CLI authentication boundary and add an explicitly enabled direct Responses transport in the isolated runtime.

A remaining-credit grant has an immutable ID, explicit start/expiry, a maximum $200 allowance and an operator
confirmation of free-only organization billing. It is not a balance scraped from credentials or an automatically
renewing $200 assumption. Both runtime containers reserve and settle against the same durable locked ledger.
Routing is service-owned, with existing input digests, typed proposals and canonical request receipts.
Legacy wire provider=codex continues to identify the company model family; usage.backend and billing_mode
distinguish actual Responses API execution from the official Codex CLI. No API key enters the CLI environment.

Only requests with validated prices and no pinned continuation/native search/images may use the API.
Explicit pre-execution denials or an unavailable allowance use the subscription. Potentially executed requests
never automatically replay on either route. Budget includes pending reservations, 30m cache writes and
long-context rates; output reasoning is not counted twice. Unknown outcomes keep reservations.

The default-off release adds no service, database credential, host unit or render change. Activation files and
the runtime.env switch go to the release operator with the reviewed, CI-green main SHA. No host edits are made
by this development task. See [runbook](../runbooks/openai-credits.md) for exact paths and activation facts.

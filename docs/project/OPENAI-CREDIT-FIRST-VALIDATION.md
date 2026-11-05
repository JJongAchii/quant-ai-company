# OpenAI credit-first staff execution validation

2026-11-05. Implementation prepared for review; default off. Production activation and live API qualification pending.

The owner requests API-budget-first execution followed by the existing ChatGPT subscription. The isolated runtime
now reserves a confirmed, expiring free-credit allowance before a direct Responses API request. A known refusal or
unavailable allowance uses the subscription; uncertain or already executed calls never automatically replay.

Local verification: **283 passed, 3 skipped** in 74.19s; repository Ruff and diff checks passed. This combines real
disposable PostgreSQL 14, real Compose configuration validation, a mocked Responses transport and real isolated
synthetic CLI subprocesses. The three skips are opt-in real Codex configuration probes. This is not evidence of
actual benefit activation, model API billing, a production release or real Slack integration.

Coverage includes concurrent shared reservations, expiry and missing setup, strict free-only confirmation,
budget/credit refusal to subscription, cached results after disabling API, cancellation/timeouts/5xx, crash windows,
immutable grants, selected model/effort, numeric subscription continuation usage, private gateway compatibility,
read-only deployment mounts and secret-free owner status. Existing provider/account/ops tests also passed.

The release adds a default-off overlay to the existing two model containers; it adds no service, host unit or render
image requirement. The operator needs the actual remaining grant, expiry, free-only billing confirmation and key
before enabling it, after a reviewed CI-green main release. Development did not change production.

- [Machine-readable receipt](evidence/openai-credit-first-20261105/local-checks.json)
- [Activation and reconciliation runbook](../runbooks/openai-credits.md)
- [Architecture decision](../adr/0050-openai-free-api-credits-first.md)

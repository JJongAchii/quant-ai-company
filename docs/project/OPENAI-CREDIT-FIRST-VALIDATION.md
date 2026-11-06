# OpenAI API-first staff execution validation

2026-11-06. The owner requests a **$120 KST calendar-month service cap**, then the existing subscription.
The latest version 2 policy no longer requires the provider's actual credit expiry or a free-only billing assertion.
It does not guarantee all charges are offset by the Pro benefit. The owner confirmed auto recharge is disabled.

Local qualification after integrating production main 7dda5b6: **451 passed, 3 skipped** in 144.34s; repository Ruff
and diff checks passed. Real disposable PostgreSQL 14 and real Compose configuration are used. Responses HTTP
and subscription inference are simulated (the latter uses real synthetic subprocesses). The skipped tests are
explicitly opt-in real Codex configuration probes. Key file mode 0600 and model metadata GET authentication were
also confirmed; these are not an inference or provider-billing qualification.

Coverage includes the $120 authorization limit, KST month/year/leap-month boundaries, budget exhaustion to
subscription, retrying API after the next month, shared reservations, explicit API refusals, ambiguous-call replay
prevention, existing sessions/effort, account routing and non-secret operational display. Existing Claude API,
release, account and operator-status regressions passed.

Production was only inspected. It was running CI-green main 7dda5b6, with no drift. API key/policy files and the
enable setting were absent at inspection. PR #220 has not been reviewed or merged. Review, CI-green main release
and the release operator's key/policy/switch installation are still required by AGENTS.md; this evidence does not
claim the server was switched.

- [Latest check receipt](evidence/openai-monthly-120-20261106/local-checks.json)
- [Exact monthly policy](evidence/openai-monthly-120-20261106/monthly-budget-policy.json)
- [Operator handoff, target main SHA pending review/merge](evidence/openai-monthly-120-20261106/operator-handoff.json)
- [Activation and reconciliation runbook](../runbooks/openai-credits.md)
- [Earlier version 1 grant-policy tests](evidence/openai-credit-first-20261105/local-checks.json)

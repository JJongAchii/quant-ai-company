# Quant Company service

This is the independent, subscription-first company runtime. Its source of truth is the private JJongAchii/quant-ai-company repository.
Work in an isolated qws attachment; the package/CLI name remains quant-company. Existing research repos are read-only
integration targets until a particular research task is authorized.

- Python 3.11+, FastAPI, PostgreSQL, Temporal. Production never uses an in-memory task queue.
- Keep workflow decisions deterministic. PostgreSQL owns company records; Temporal owns
  execution, retries and timers. A turn has a stable ID and one committed outcome.
- Accept only signed Slack events from configured workspace/users/channels. Ignore bot echoes.
- Model output is a typed proposal. The service validates permissions, revision and limits
  before applying messages, delegations, memories or tool calls.
- Codex is isolated from Slack/database credentials and uses official ChatGPT authentication.
  Do not silently switch to paid API credentials or unsandboxed execution.
- Every external effect may be ambiguous after a crash. Preserve receipts; do not claim
  exactly-once delivery or automatically replay an uncertain charge or write.
- No heavy training, backtests, live orders or automatic 5090 fallback in this service slice.
- Test against real PostgreSQL and Temporal where available. Mark simulated Slack and real
  Codex checks separately. Never label mocked cloud/Slack integration as deployed.
- Do not print secrets or copy credential files into Git. Deployment templates reference secrets.
- Tests: `uv run pytest`; lint: `uv run ruff check .`; demo: documented in README.

The root thread owns qws INTENT/STATUS and final integration. Builders do not spawn agents,
change the contract, or judge research results.

Company code, ADRs, roadmap, acceptance reports and new operational evidence belong in this
repository only. Do not append company records to quant-workspace. The original qws contract
and historical evidence there are frozen provenance; read them without duplicating or rewriting
the contract. New evidence goes under docs/project/evidence, with summaries under docs/project.

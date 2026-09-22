# First implementation interfaces

Python package `quant_company`, Python 3.11+. Shared Pydantic models are in `contracts.py`.
Role IDs: director, financial_strategist, researcher_kr, researcher_global, researcher_crypto,
data, engineer, validator, risk, operations, reporter, tech_scout. First four active: director,
financial_strategist, researcher_kr, data. Reporter is activated only by hot-news. Tech Scout remains
inactive because it is a deterministic outbound identity, not a model employee. `roles.json` is a JSON
array of `Role` objects, packaged in `src/quant_company/`.

## Codex runtime (separate container)

- ASGI import `quant_company.providers.codex_runtime:app`.
- `POST /v1/turns`, Authorization Bearer `MODEL_RUNTIME_TOKEN`, JSON `ProviderRequest` → `ProviderResponse`.
- `GET /healthz` returns health only; no secrets or account metadata.
- Error JSON: `{code, message, retry_after_seconds}`. Codes: quota, auth, busy, uncertain,
  timeout, invalid_output, unavailable. 429/401/409/502 as appropriate.
- Environment: CODEX_HOME, CODEX_BIN=codex, MODEL_RUNTIME_TOKEN, CODEX_JOBS_DIR,
  CODEX_TIMEOUT_SECONDS=300. request_id is stable across transport retries.
- Official ChatGPT login only; reject API-key auth/fallback. Child environment does not inherit
  Slack/database/AWS credentials. Per-call sandbox read-only; host/project config ignored;
  shell, app, MCP/plugin, computer and subagent tools disabled. Model proposes typed service tools.
- Request result is durably cached by ID + input digest. A crash after starting a call is an
  uncertain call until an operator reconciles it; no silent paid or duplicate fallback.

## Company service

- `quant-company migrate`, `serve`, `worker`, `dispatch`, `slack-socket`, `demo`, `slack-manifests` commands.
- ASGI factory `quant_company.api:create_app`.
- Environment: DATABASE_URL, OPERATOR_TOKEN, MODEL_PROVIDER=codex|fixture,
  MODEL_RUNTIME_URL, MODEL_RUNTIME_TOKEN, SLACK_TEAM_ID, SLACK_ALLOWED_USERS (JSON list),
  SLACK_ALLOWED_CHANNELS (JSON list), SLACK_CREDENTIALS_FILE (JSON object by role ID),
  TEMPORAL_ADDRESS, TEMPORAL_NAMESPACE, TEMPORAL_API_KEY (optional), TEMPORAL_TLS,
  COMPANY_MAX_DAILY_TURNS=100, COMPANY_MAX_TASK_TURNS=8, COMPANY_MAX_DEPTH=3.
- Slack credentials: `{role_id: {app_id, bot_user_id, bot_token, app_token, signing_secret}}`.
  `app_token` with `connections:write` is required for Socket Mode; `signing_secret` is required only for HTTP.
  The inactive outbound-only `tech_scout` entry needs only `app_id`, `bot_user_id` and `bot_token`.
- Default transport is Socket Mode: `slack-socket` maintains authenticated outbound WebSockets with
  the official Slack SDK. Workspace/user/channel/app checks and DB commit precede the envelope ACK.
  Duplicate deliveries share the same inbox key as HTTP. No public domain/port is required.
- Optional `/slack/events/{role_id}` verifies the role-specific signature before parsing.
- `slack-manifests --output DIR` defaults to Socket Mode. Optional HTTP mode requires
  `--transport http --base-url https://...`. Never create/install apps automatically.
- `/healthz`; authenticated `/v1/agents`, `/v1/projects`, `/v1/projects/{id}`,
  `/v1/requests`, `/v1/projects/{id}/revise`, `/v1/tasks/{id}/retry`.
- Temporal workflow IDs are stable turn IDs. PostgreSQL owns company task state and inbox/outbox;
  Temporal owns the execution of each bounded turn and its delayed retries.
- The dispatch process drains durable turn starts and Slack outbox. Bot messages are mirrored
  to Slack; internal delivery is direct and does not depend on Slack bot events.
- No live trade or arbitrary shell tool in this slice. Coding results can be artifacts.

## Deployment

### Zero-model tech feed

- `quant-company tech-feed probe|collect|status [--output PATH]`.
- `TECH_FEED_ENABLED=false`, `TECH_FEED_PUBLISH_ENABLED=false`, `TECH_FEED_CHANNEL_ID`,
  `TECH_FEED_OWNER_USER`, optional `TECH_FEED_SOURCES_FILE`. Slack allowlists still apply.
- Separate PostgreSQL `tech_feed_*` state and `company-tech-feed-collection-v1` Temporal workflow;
  task queue suffix `-tech-feed`. Uses the dedicated `tech_scout` credential and the existing outbox with
  message kind `tech_feed`; queued legacy Reporter deliveries migrate before sending.
- No ProviderRequest, turns, model budgets, automatic summaries/translations or interactive Tech Scout calls.
- See [tech-feed](docs/tech-feed.md) for first-sync, freshness, delivery window and uncertainty semantics.

### Host

Single AWS Lightsail host with service, Slack socket receiver, worker/dispatcher, PostgreSQL and isolated Codex
runtime. Temporal Cloud in production; local Temporal dev server is for development only.
Caddy is an optional `https` Compose profile. Default Lightsail ingress allows only the configured
SSH IPv4 /32. HTTP/HTTPS port opening must be an explicit deployment option.
Secrets/config are operator-provided local files, excluded from Git and images. Use separate
model/core container networks; do not mount Docker socket. DB is not published to the internet.
AWS resources are prepared as reviewable templates/scripts; do not create any resources.

Transport revision (2026-09-16): user has no domain and asks to reuse the AWS account serving
EC2/Insight-Invest. Socket Mode is an implementation choice within the approved persistent Slack
company contract; it removes the domain prerequisite without changing bot identity or task semantics.

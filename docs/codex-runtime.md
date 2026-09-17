# Private Codex subscription runtime

This service exposes official Codex CLI **0.154.0** to the company core. It uses
the operator's saved ChatGPT login, with no API-key fallback. The gateway does not
read or copy the credential file. Codex itself manages authentication and refresh.

## Process and container boundary

Run the runtime in its dedicated container, without Slack, database, AWS or Docker
credentials. Mount only its dedicated Codex authentication directory and durable
jobs volume. Do not mount a developer home, project checkout, host skills, or
system Codex configuration into the container. Use an init process (`init: true`)
so a container exit terminates/reaps descendants. This is a private service, not
a public subscription proxy.

Required environment:

| Variable | Meaning |
| --- | --- |
| `MODEL_RUNTIME_TOKEN` | Private bearer credential shared only with the company core |
| `CODEX_HOME` | Dedicated persisted CLI authentication directory |
| `CODEX_JOBS_DIR` | Dedicated persisted receipt directory on a local POSIX filesystem |
| `CODEX_BIN` | Official pinned CLI executable; default `codex` |
| `CODEX_TIMEOUT_SECONDS` | Per-inference wall time; default 300, maximum 3600 seconds |

Authenticate as the runtime user with the official `codex login --device-auth`
flow. API-key environment variables are rejected. An existing API-key login is
also rejected by a captured `codex login status` check. Never paste credentials
into a prompt or commit them to a repository.

Start one ASGI worker on the private container network:

```sh
uv run uvicorn quant_company.providers.codex_runtime:app --host 0.0.0.0 --port 8090 --workers 1
```

The filesystem lock prevents concurrent inference across runtime objects, but
explicit cancellation targets an in-process task. **One worker is required.**
`GET /healthz` is liveness only; it does not verify the subscription, login or quota.

The child gets only `PATH`, `HOME`, `LANG`, `LC_ALL`, `TZ`, `CODEX_HOME` and
`NO_COLOR`. User config/rules are ignored, the working directory is temporary
and set untrusted using a top-level `projects={...}` TOML override (the pinned
CLI rejects a quoted path in a dotted override key). Project instructions are disabled, and the CLI uses a read-only
sandbox. Shell, hooks, apps, plugins, MCP configuration, browser/computer,
subagents, host skills and memory generation are disabled. Ordinary employee and maintenance proposal
turns also disable native web search. A service-authorized `ProviderRequest.web_search=true` enables
live search and its code-mode bridge for a separate durable, budgeted discovery call.
CLI 0.154.0 needs all three bridge flags (`code_mode`, `code_mode_host`, `code_mode_only`)
for the configured Responses Lite models; `web_search="live"` alone exposes no usable search.
The event parser accepts only completed native search items in that scope, including the pinned
CLI's duplicate item/call ID transport quirk. Other executable event types remain rejected. No
dangerous sandbox or hook-trust bypass is used. Container isolation remains
necessary: CLI configuration is not a substitute for withholding host secrets.

## Interface

`POST /v1/turns` accepts shared `ProviderRequest` and returns `ProviderResponse`.
All POST routes require `Authorization: Bearer <MODEL_RUNTIME_TOKEN>`.

```python
client = RuntimeClient(base_url, token, timeout_seconds=360)
result = await client.run(provider_request)
status = await client.cancel(provider_request.request_id)
```

The output schema sent to Codex is `{ "decision_json": "..." }`. The string is
decoded and validated against the same `AgentDecision` used by the consumer.
The envelope is private to the CLI boundary; it does not change the HTTP model.
Invalid/empty decisions, non-finite JSON, duplicate JSON keys, naive follow-up
timestamps and invalid token units are rejected. Usage counts remain integer
tokens; follow-up times preserve timezone offsets. No financial metrics are read.
Codex `item.completed` entries of type `error` are diagnostic messages and are
discarded. They do not authorize success: a zero exit code, exactly one
`turn.completed`, no `turn.failed` or top-level `error`, and a validated final decision are still
required. Executable tool items remain blocked even when diagnostic items occur.

Requests are limited to 512 KiB; CLI stdout to 1 MiB and stderr to 64 KiB. The CLI
version, configuration and login probes each have a 15-second limit. The configuration
probe uses all required restrictions, a nonexistent output-schema path, and empty
stdin. It must receive the pinned CLI's explicit empty-input rejection with no
JSON events before a `running` receipt can be written. Configuration errors are
therefore reported as `unavailable` without claiming inference started. Raw stdout/stderr and
prompt text are not written into receipts or returned as error messages. The
typed result itself can contain project information and must be protected.

## Receipts and retries

Version 1 execution receipts are atomic JSON files bound to the request ID and
SHA-256 of the complete canonical request. A pre-arrival cancellation has only
the request ID because its input has not arrived. Receipt contents and directory are
fsynced. The runtime syncs `running` before spawning inference and commits
`complete` with the validated result afterward. A repeated completed request
returns the cached result without invoking Codex or requiring current quota.

| Outcome | Behavior for the same ID |
| --- | --- |
| Complete | Return durable cached result |
| Changed prompt/model | `uncertain`, HTTP 409; no inference |
| Active capacity | `busy`, HTTP 409; retry after 5 seconds |
| Explicit structured quota denial | `quota`, HTTP 429; defer 15 minutes before another attempt |
| Preflight login/version failure | No inference started; fix setup before retrying |
| Auth failure after starting, timeout, malformed output | Persist terminal failure; operator review |
| Process crash / missing completion / orphan `running` receipt | `uncertain`; do not re-infer |
| Corrupt receipt | `uncertain`; preserve the file |

The HTTP client has no internal retry loop. Transport timeouts return `uncertain`:
the company service may recover the **same** request ID to obtain its receipt,
but must not invent a new inference ID to get around an ambiguous outcome.

`POST /v1/turns/{request_id}/cancel` explicitly cancels a superseded turn and
terminates its process group. It returns `cancelled`, `completed`, or `uncertain`.
A cancellation received before the turn creates a durable tombstone so a late
request cannot begin. HTTP disconnection alone does not cancel the turn: it may
still finish and cache a recoverable result. Graceful shutdown cancels active
processes. Following a hard runtime/container crash, orphan receipts require
operator reconciliation; this gateway does not guess process identity or kill
an unrelated PID. Do not erase uncertain receipts or replay them automatically.

Keep receipts with the company backup and retain them while their task IDs remain
recoverable. Restoring an older backup cannot establish whether an unrecorded
external inference occurred. This implementation does not claim exactly-once
execution across lost storage or remote provider failures.

## Validation and current limits

```sh
uv run pytest -q tests/test_codex_runtime.py
uv run ruff check src/quant_company/providers tests/test_codex_runtime.py
CODEX_CONFIG_PROBE=1 uv run pytest -q tests/test_codex_config_probe.py
```

The tests run a real subprocess containing a fake Codex executable, including
process-tree cleanup, quota, malformed output, orphan detection, typed time/JSON
contracts and HTTP-to-durable-result replay. They do not call a model or validate
live subscription capacity. The opt-in configuration test runs the actual local
CLI with an empty authentication directory, no prompt and a missing schema; it
does not run inference. The integrating thread must run the explicitly
authorized real Codex smoke with the committed code before calling the live
provider qualified. Production AWS/Slack connectivity remains a separate check.

Official sources checked with the local CLI on 2026-09-16:
[non-interactive CLI and JSONL](https://learn.chatgpt.com/docs/non-interactive-mode),
[authentication](https://learn.chatgpt.com/docs/auth),
[configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).
The runtime rejects other CLI versions until their flags and behavior are
requalified; it does not silently remove unsupported restrictions.

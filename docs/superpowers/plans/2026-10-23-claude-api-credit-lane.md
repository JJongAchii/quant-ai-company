# Claude API Credit Lane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** claude-runtime sends the owner-approved contracts to the Claude API (paid from the Max 20x monthly credit), falls back to the API for subscription-first contracts only when the subscription definitively did not run, and never spends more than $190 in any 31 days.

**Architecture:**
- A new module `providers/claude_api.py` holds the API lane: price table, budget ledger, pauses, the status file and the streaming HTTP call (`httpx`).
- `ClaudeRunner` routes each request by `output_contract`, keeps one receipt per request ID with an `attempts` list, and falls back between lanes only on definitive non-execution.
- Small edits carry a "cancel only if you hold the shared slot" option from the newsletter writer to the runtime.
- The host ops probe reads the lane's status file into ops status and watch.

**Tech Stack:** Python 3.12, httpx (already a dependency), FastAPI, pytest with `httpx.MockTransport` and the existing fake Claude CLI fixture.

**Spec:** `docs/superpowers/specs/2026-10-23-claude-api-credit-lane-design.md`. Read it first. Section numbers like "spec §9-3" refer to it.

## Global Constraints

- **No new dependencies.** `pyproject.toml` and `uv.lock` stay unchanged; both are render-image inputs (`deploy/release.py` `RENDER_FILES`).
- **Do not touch render-image inputs:** `src/quant_company/contracts.py`, `src/quant_company/video/*` and `src/quant_company/briefing/{contracts,execution}.py`.
- **Request identity is unchanged.** `request_digest(request)` and request IDs are computed as today. The lane choice is never part of the request.
- **AGENTS.md (verbatim):**
  - "Every external effect may be ambiguous after a crash. Preserve receipts; do not claim exactly-once delivery or automatically replay an uncertain charge or write."
  - "Do not silently switch to paid API credentials or unsandboxed execution."
  - "Do not print secrets or copy credential files into Git."
- **The API key** is read only from the file named by `CLAUDE_API_KEY_FILE`, stripped of surrounding whitespace, and used only in the `x-api-key` header. It must never appear in any environment passed to the CLI, any receipt, status file, log line, fault message or test snapshot. `FORBIDDEN_AUTH` and `environment()` stay as they are.
- **API-first contracts** (`API_FIRST`): `video_shorts_v1`, `newsletter_v1`, `video_performance_v1`, `video_weekly_v1`, `video_answer_v1`, `agent_decision`, `video_screen_fix_v1`. Every other contract is subscription-first.
- **Prices**, USD per million tokens:
  - `claude-opus-5-5`: input 4, output 20, cache_write 5, cache_read 0.20
  - `claude-opus-5`: input 5, output 25, cache_write 6.25, cache_read 0.50
  - Source comment: `https://platform.claude.com/docs/en/about-claude/pricing (checked 2026-10-23)`.
- **Budget:** a rolling window of `31 * 86400` seconds. The cap comes from `CLAUDE_API_CAP_USD`: default `0`, which means the lane is off; production uses `190`.
- **Pauses:**

  | Trigger | Pause |
  |---|---|
  | uncertain or timeout | `1800` s |
  | 429 rate limit | `retry-after` seconds, else `60` |
  | 529 | `60` s |
  | 429 with `error.details.error_code == "enforced_spend_limit_reached"` | until the next 1st at 00:00 UTC |
  | 401 or 403 | until restart (not persisted) |

- **Concurrency:** API calls run in their own slots, at most `2` at a time. They never take `.runtime.lock`.
- **API call constants:**
  - `API_URL = "https://api.anthropic.com/v1/messages"`, `API_VERSION = "2023-06-01"`
  - `TOOL = "StructuredOutput"`
  - `SYSTEM = "Return your final answer by calling the StructuredOutput tool exactly once, with input that matches its schema. Do not answer in plain text."`
- **`max_tokens`:** `128000` for every contract, except `newsletter_v1`, `video_performance_v1`, `video_answer_v1` and `agent_decision`, which use `64000`.
- **Input estimate for a reservation:** `len(request.prompt.encode()) // 2 + 3000 * len(request.attachments)` tokens.
- **Tests:** run with `uv run pytest` and lint with `uv run ruff check .`. Simulated API and real API checks are reported separately; nothing in the test suite reaches api.anthropic.com.

## Review Focus

These are the five failure modes most likely to bite. Each has a pinned test, listed with its owning task.

1. **A key file that is empty or ends with a newline.** Trim the newline. An empty file means "no key", so the API is not executed and the request goes to the subscription. It must not cause a 401 storm. (Task 2: `test_key_file_is_trimmed_and_empty_means_no_key`.)
2. **An unreadable or corrupt ledger.** The lane fails closed: no API calls, and the status says `ledger_unreadable`. Subscription work continues. (Task 1: `test_corrupt_ledger_fails_closed`.)
3. **The same request ID asked again while its API call runs.** The second ask gets `pending`, never a second paid call. (Task 3: `test_second_ask_during_api_call_is_pending_not_a_second_call`.)
4. **A runtime restart during an API call.** The receipt still says `running` with backend `api`. A re-ask is `uncertain` and is never replayed, and the reservation stays counted. (Task 3: `test_restart_during_api_call_is_uncertain_and_keeps_reservation`.)
5. **An SSE stream with thinking blocks before the tool call, whose JSON input arrives split across many `input_json_delta` events.** The pieces are assembled into one input. A text-only reply is `invalid_output`. (Task 2: `test_thinking_then_split_tool_input_is_assembled` and `test_text_only_reply_is_invalid_output`.)

---

### Task 1: Price table, budget ledger, pauses and status file

**Files:**
- Create: `src/quant_company/providers/claude_api.py` (part A)
- Test: `tests/test_claude_api_lane.py` (new)

**Interfaces:**
- Produces:
  - `Price(input, output, cache_write, cache_read)`: a frozen dataclass, USD per MTok.
  - `PRICES: dict[str, Price]`
  - `cost_usd(model: str, usage: dict) -> float`
  - `max_tokens(contract: str) -> int`
  - `reservation_usd(request) -> float`
  - `WINDOW = 31 * 86400`
  - `Budget(directory: Path, cap_usd: float, clock=time.time)`, with these methods:
    - `reserve(request_id: str, model: str, amount: float, route: str) -> bool`. `route` is `"api_first"` or `"fallback"`.
    - `settle(request_id: str, amount: float) -> None`
    - `release(request_id: str) -> None`
    - `spent() -> float`
    - `pause(reason: str, until: float | None, persist: bool = True) -> None`
    - `paused() -> dict | None`, returning `{"reason": str, "until": float | None}`
    - `write_status(enabled: bool) -> None`
  - Files under `directory`: `ledger.json` and `status.json`. The directory is created with mode 0700, and writes go through `codex_runner.atomic_json` under `fcntl.flock` on `directory/.ledger.lock`.

- [ ] **Step 1: Write the failing tests**

```python
def test_cost_uses_all_four_token_kinds():
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000,
             "cache_creation_input_tokens": 1_000_000, "cache_read_input_tokens": 1_000_000}
    assert cost_usd("claude-opus-5-5", usage) == pytest.approx(4 + 20 + 5 + 0.20)
    assert cost_usd("claude-opus-5", usage) == pytest.approx(5 + 25 + 6.25 + 0.50)

def test_reservation_is_input_estimate_plus_full_max_tokens():
    request = ProviderRequest(request_id="video-shorts-1", model="claude-opus-5-5", prompt="가" * 1000,
                              output_contract="video_shorts_v1")
    assert reservation_usd(request) == pytest.approx((3000 // 2) * 4 / 1e6 + 128000 * 20 / 1e6)

def test_reservations_never_cross_the_cap_in_31_days(tmp_path):
    now = [1_000_000.0]
    budget = Budget(tmp_path, 10.0, clock=lambda: now[0])
    assert budget.reserve("a", "claude-opus-5-5", 6.0, "api_first")
    assert not budget.reserve("b", "claude-opus-5-5", 5.0, "api_first")  # 6 + 5 > 10
    budget.settle("a", 1.0)
    assert budget.reserve("b", "claude-opus-5-5", 5.0, "fallback") and budget.spent() == pytest.approx(6.0)
    now[0] += WINDOW + 1
    assert budget.spent() == 0 and budget.reserve("c", "claude-opus-5-5", 9.5, "api_first")

def test_release_returns_the_reservation(tmp_path): ...   # reserve 6, release, spent()==0
def test_zero_cap_reserves_nothing(tmp_path): ...         # Budget(tmp, 0).reserve(..., 0.01, ...) is False
def test_corrupt_ledger_fails_closed(tmp_path):
    (tmp_path / "ledger.json").write_text("{not json")
    budget = Budget(tmp_path, 190.0)
    assert not budget.reserve("a", "claude-opus-5-5", 0.01, "api_first")
    budget.write_status(enabled=True)
    assert json.loads((tmp_path / "status.json").read_text())["state"] == "ledger_unreadable"

def test_pauses_persist_except_key_pauses(tmp_path):
    now = [1000.0]
    budget = Budget(tmp_path, 190.0, clock=lambda: now[0])
    budget.pause("uncertain", 1000.0 + 1800)
    budget.pause("key", None, persist=False)
    assert budget.paused()["reason"] == "key"   # first matching active pause reported
    again = Budget(tmp_path, 190.0, clock=lambda: now[0])  # a restart forgets the key pause only
    assert again.paused() == {"reason": "uncertain", "until": 2800.0}
    now[0] = 2801.0
    assert again.paused() is None

def test_status_file_shows_spend_cap_pause_and_routes(tmp_path):
    budget = Budget(tmp_path, 190.0, clock=lambda: 5000.0)
    budget.reserve("a", "claude-opus-5-5", 1.0, "api_first"); budget.settle("a", 0.5)
    budget.reserve("b", "claude-opus-5-5", 1.0, "fallback"); budget.settle("b", 0.25)
    budget.write_status(enabled=True)
    status = json.loads((tmp_path / "status.json").read_text())
    assert status["state"] == "on" and status["cap_usd"] == 190.0
    assert status["spent_31d_usd"] == pytest.approx(0.75)
    assert status["calls_31d"] == {"api_first": 1, "fallback": 1}
    assert status["last_fallback_at"] == 5000.0 and status["paused"] is None
```

- [ ] **Step 2: Run them to verify they fail.** Run `uv run pytest tests/test_claude_api_lane.py -q`. Expected: ImportError, because `claude_api` does not exist yet.

- [ ] **Step 3: Implement part A in `src/quant_company/providers/claude_api.py`.**
  - The `status.json` keys are `state`, `enabled`, `cap_usd`, `spent_31d_usd`, `calls_31d`, `last_fallback_at`, `paused` and `updated_at`.
  - `state` is one of `"off"` (disabled or a cap of 0), `"paused"`, `"on"` or `"ledger_unreadable"`.
  - Ledger entries are `{request_id, model, route, at, reserved, actual}`. `actual` is `None` while an entry is reserved.
  - `spent()` sums `actual if actual is not None else reserved` over the entries newer than `WINDOW`. Older entries are dropped on write.
  - `paused()` reports the highest-priority active pause, in the order `key` > `spend_limit` > `uncertain` > `rate_limit`/`overloaded`. Persisted pauses live in `ledger.json` under `"pauses"`; the `key` pause lives only in memory.

- [ ] **Step 4: Run the tests to verify they pass.** Same command. Expected: PASS.

- [ ] **Step 5: Commit.** `git commit -m "feat(claude-api): price table, 31-day budget ledger, pauses and status file"`

---

### Task 2: Streaming API call

**Files:**
- Modify: `src/quant_company/providers/claude_api.py` (part B)
- Test: `tests/test_claude_api_lane.py`

**Interfaces:**
- Consumes: `max_tokens` (Task 1).
- Produces:
  - `read_key(path: Path | None) -> str | None`. Returns `None` when the file is missing or empty.
  - `class ApiNotExecuted(Exception)` with attributes `reason: str`, `consumed: bool` and `pause: tuple[str, float | None, bool] | None`. The pause tuple is `(reason, until, persist)`.
  - `body(request, schema: dict, title: str) -> dict`. This is the exact JSON sent.
  - `async def call(request, schema: dict, title: str, key: str, *, transport=None, clock=time.time) -> tuple[dict, dict]`. It returns `(tool_input, usage)`. `usage` has `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens` and `model`.
  - Failure outcomes of `call`:
    - It raises `ApiNotExecuted` for any non-200 response.
    - It raises `ProviderFault("uncertain", ...)` for an `error` event, a dropped stream or a model mismatch after the 200.
    - It raises `ProviderFault("invalid_output", ...)` when `stop_reason` is not `tool_use`, or when no `StructuredOutput` tool_use arrives.
- Request body (spec §4):
  - `model`, `max_tokens`, `stream: true`
  - `output_config: {"effort": request.reasoning_effort or "high"}`
  - `system: SYSTEM`
  - `tools: [{"name": TOOL, "description": title, "input_schema": schema}]`
  - `tool_choice: {"type": "auto"}`
  - `messages: [{"role": "user", "content": [*image blocks in order, {"type": "text", "text": request.prompt}]}]`
  - Image blocks use the same shape as `claude_runner.stdin_payload`.
  - Do not send `cache_control` or `thinking`.
  - Headers: `x-api-key`, `anthropic-version: API_VERSION`, `content-type: application/json`.
- Non-200 mapping to `ApiNotExecuted`:

  | Status | `consumed` | `pause` |
  |---|---|---|
  | 429 with `enforced_spend_limit_reached` | `False` | `("spend_limit", next 1st 00:00 UTC, True)` |
  | other 429 | `False` | `("rate_limit", now + retry-after or 60, True)` |
  | 529 | `False` | `("overloaded", now + 60, True)` |
  | 401, 403 | `False` | `("key", None, False)` |
  | 500, 504 | `True` | `None` |
  | any other | `False` | `None` |

  `ApiNotExecuted.reason` is `spend_limit`, `rate_limit`, `overloaded`, `key`, `server` (for 500 and 504) or `rejected` (for any other status).

- `httpx.Timeout(connect=10, read=300, write=60, pool=10)`, with `trust_env=False` and `follow_redirects=False`. The response body of an error is parsed only for `error.type` and `error.details.error_code`, and never copied into a fault message.

- [ ] **Step 1: Write the failing tests.** Use an `sse(*events)` helper that builds `text/event-stream` bytes, and `httpx.MockTransport` handlers that record each request.

```python
async def test_body_carries_model_effort_tool_and_images_in_order(): ...
    # body(req with 2 attachments, effort "xhigh") -> output_config.effort == "xhigh", tools[0]["name"] == "StructuredOutput",
    # content types == ["image", "image", "text"], "cache_control" not in json.dumps(body), "thinking" not in body
async def test_thinking_then_split_tool_input_is_assembled():
    # thinking block (thinking_delta + signature_delta), then a tool_use block whose input arrives as 3 input_json_delta parts,
    # message_delta stop_reason "tool_use" usage.output_tokens=1234 (cumulative), message_start usage input/cache values
    tool_input, usage = await call(...)
    assert tool_input == {"say": "x", "n": 3} and usage["output_tokens"] == 1234 and usage["model"] == "claude-opus-5-5"
async def test_text_only_reply_is_invalid_output(): ...          # stop_reason end_turn, text block only -> invalid_output
async def test_max_tokens_stop_is_invalid_output(): ...          # stop_reason max_tokens -> invalid_output
async def test_error_event_after_200_is_uncertain(): ...         # event: error {"type":"overloaded_error"} -> uncertain
async def test_model_mismatch_is_uncertain(): ...                # message_start.message.model "claude-opus-5" for an opus-5-5 request
@pytest.mark.parametrize("status,headers,payload,reason,consumed", [
    (429, {"retry-after": "7"}, {"error": {"type": "rate_limit_error"}}, "rate_limit", False),
    (429, {}, {"error": {"type": "rate_limit_error", "details": {"error_code": "enforced_spend_limit_reached"}}}, "spend_limit", False),
    (529, {}, {"error": {"type": "overloaded_error"}}, "overloaded", False),
    (401, {}, {"error": {"type": "authentication_error"}}, "key", False),
    (500, {}, {"error": {"type": "api_error"}}, "server", True),
    (400, {}, {"error": {"type": "invalid_request_error"}}, "rejected", False)])
async def test_refusals_before_the_stream_are_not_executed(status, headers, payload, reason, consumed): ...
    # assert error.reason == reason and error.consumed is consumed; rate_limit pause until == now + 7;
    # spend_limit pause until == next 1st 00:00 UTC; key pause persist is False
def test_key_file_is_trimmed_and_empty_means_no_key(tmp_path): ...   # "sk-x\n" -> "sk-x"; "" or missing -> None
async def test_key_appears_only_in_the_header(): ...                  # request body bytes and every raised fault text lack the key
```

- [ ] **Step 2: Run them to verify they fail.** Run `uv run pytest tests/test_claude_api_lane.py -q`. Expected: the Task 2 tests fail with ImportError on `call`, `body`, `read_key` and `ApiNotExecuted`.

- [ ] **Step 3: Implement part B.** Parse SSE line by line with `response.aiter_lines()`:
  - Collect `content_block_start` blocks by index, and append the `partial_json` of each `input_json_delta` to its block.
  - Take `stop_reason` and the cumulative `usage.output_tokens` from the last `message_delta`.
  - Take the input and cache usage, and `message.model`, from `message_start`.
  - Ignore `ping`.
  - Treat a stream that ends without `message_stop` as `uncertain`.

- [ ] **Step 4: Run the tests to verify they pass.** Expected: PASS.

- [ ] **Step 5: Commit.** `git commit -m "feat(claude-api): streaming Messages API call with StructuredOutput tool and refusal mapping"`

---

### Task 3: Routing, receipts, fallback and shared-slot cancel in `ClaudeRunner`

**Files:**
- Modify: `src/quant_company/providers/claude_runner.py`
- Test: `tests/test_claude_api_lane.py`. Reuse the `fake_claude` fixture by importing it from `tests/test_claude_runtime.py`.

**Interfaces:**
- Consumes: `Budget`, `reservation_usd`, `cost_usd`, `call`, `read_key` and `ApiNotExecuted` (Tasks 1–2).
- Produces:
  - New `ClaudeConfig` fields: `api_enabled: bool = False`, `api_cap_usd: float = 0.0` (validated `0 <= x <= 1000`), `api_key_file: Path | None = None`.
  - `API_FIRST: frozenset[str]` (Global Constraints).
  - `ClaudeRunner(config, *, process_runner=None, source_environment=None, api_transport=None, clock=time.time)`
  - `ClaudeRunner.cancel(request_id, shared_only=False) -> str`. It returns `"kept"` when `shared_only` is set and the request is running on the API lane.
  - `runner_from_environment()` reads `CLAUDE_API_ENABLED == "true"`, `CLAUDE_API_CAP_USD` (float, default 0) and `CLAUDE_API_KEY_FILE`.
  - The API lane directory is `jobs_dir / ".api"`.
- Refactor the tail of `parse_result` into `structured_response(request, structured: dict, usage: dict) -> ProviderResponse`. It does the schema validation, keeps today's `invalid_output` reasons, and builds the `ProviderResponse`. The CLI and API paths both use it; the CLI passes today's usage dict unchanged.
- **API usage dict:** the four token counts, plus these fields:
  - `actual_model` (from the stream)
  - `effort`
  - `effort_evidence="requested_api_parameter"`
  - `billing_mode="api_credit"`
  - `backend="api"`
  - `cost_usd` (rounded to 6 places)
  - `api_version`
- **Receipt changes:** keep today's keys, and add `"backend": "subscription"|"api"` and `"attempts": [...]`. Each attempt is `{"backend", "outcome", "status"?, "at"}`, with `outcome` one of `not_executed`, `consumed`, `uncertain`, `invalid_output`, `timeout`, `complete`, `auth` or `quota`.
- **Routing (spec §1, §9-3)**, inside `run()` after today's model and contract checks and the `_cached` lookup:
  1. `api_ok` requires all of the following:
     - the lane is enabled with a cap above 0;
     - a key is present;
     - the model is in `PRICES`;
     - there is no active pause;
     - the receipt has no attempt with `outcome` in `{consumed, uncertain, timeout}` and backend `api`;
     - `reserve()` succeeds. `route` is `"api_first"` for `API_FIRST` and `"fallback"` otherwise.
  2. **`API_FIRST` contracts:**
     - If `api_ok`, run the API path.
     - On `ApiNotExecuted`, apply its pause, release or settle the reservation, append the attempt, then run today's CLI path.
  3. **Other contracts:**
     - Run today's CLI path.
     - If it raises `auth` or `quota` (both mean nothing ran: preflight, or zero output tokens) and `api_ok`, run the API path.
     - On `ApiNotExecuted`, re-raise the original CLI fault, keeping its `deferred` receipt.
     - A receipt that is `deferred` and not yet due skips the CLI and tries the API when `api_ok`; otherwise it raises `quota` as today.
  4. **API path:**
     - Write the receipt as `running` with backend `api` and record the request in `self.active` tagged `"api"`.
     - Take one of 2 `asyncio.Semaphore` slots, then call `call(...)`.
     - On success: validate with `structured_response`, settle `cost_usd`, write the receipt as `complete`.
     - On `invalid_output`: settle the cost, write the receipt as `failed`/`invalid_output`.
     - On `uncertain`, a timeout (enforced with `config.process_limit(request)`) or cancellation: settle the reservation as spent, pause the lane with `("uncertain", now + 1800)`, write the receipt as `failed` with that outcome, and do not try the CLI.
     - Call `budget.write_status(...)` after every change.
  5. **A re-ask that finds a `running` api receipt:** if the ID is in `self.active`, raise `ProviderFault("pending", ..., 20)`; otherwise raise `uncertain`.

- [ ] **Step 1: Write the failing tests.** Build `ClaudeConfig` from `fake_claude` plus a key file, `api_enabled=True` and `api_cap_usd=190`, and use a MockTransport.

```python
async def test_api_first_contract_runs_on_the_api_and_receipt_says_so(...):
    # newsletter_v1 request -> MockTransport called once, fake CLI calls() == [], response.usage["billing_mode"] == "api_credit",
    # receipt["backend"] == "api", second run() returns the cached result without another HTTP call
async def test_subscription_first_contract_stays_on_the_cli_while_it_works(...): ...   # video_episode_v1 -> no HTTP call
async def test_cli_quota_or_auth_falls_back_to_the_api(...): ...    # configure(error="rate_limit") and auth loggedIn False -> HTTP once, status calls_31d.fallback == 1
@pytest.mark.parametrize("change", [{"crash": True}, {"incomplete": "error_max_turns"}, {"result_is_error": True}])
async def test_cli_uncertain_or_invalid_never_reaches_the_api(...): ...
async def test_api_refusal_sends_api_first_work_to_the_subscription(...): ...   # 529 -> CLI completes, attempts == [api not_executed 529, subscription complete]
async def test_api_uncertain_is_never_replayed_and_pauses_the_lane(...): ...    # error event -> fault uncertain; re-ask -> uncertain, no HTTP; another newsletter request within 30 min goes to CLI
async def test_disabled_lane_or_zero_cap_or_missing_key_changes_nothing(...): ...  # each -> today's CLI behaviour, no HTTP
async def test_cap_reached_sends_api_first_work_to_the_subscription(...): ...   # cap 1.0 (< reservation) -> CLI
async def test_second_ask_during_api_call_is_pending_not_a_second_call(...): ... # slow transport; concurrent run() -> pending; HTTP count 1
async def test_restart_during_api_call_is_uncertain_and_keeps_reservation(...): ...  # running api receipt + new runner -> uncertain; ledger reserved counted
async def test_api_call_runs_while_the_cli_slot_is_busy(...): ...  # hold .runtime.lock (configure delay on a video call) -> newsletter completes via API meanwhile
async def test_shared_only_cancel_keeps_an_api_turn_and_cancels_a_cli_turn(...): ...  # "kept" vs "cancelled"; the API turn still completes
async def test_key_never_reaches_the_cli_environment_or_receipts(...): ...      # calls()[*]["env"] and every receipt file lack the key string
async def test_invalid_api_output_is_definitive_and_costs_what_it_used(...): ...  # schema mismatch -> invalid_output; re-ask same; ledger actual == cost
async def test_deferred_receipt_goes_to_the_api_when_the_lane_is_open(...): ...  # CLI quota with the API paused -> deferred; pause ends -> re-ask completes on the API without the CLI
```

- [ ] **Step 2: Run them to verify they fail.** Run `uv run pytest tests/test_claude_api_lane.py -q`. Expected: the Task 3 tests fail (unknown config fields or no routing).

- [ ] **Step 3: Implement the routing in `claude_runner.py`.** Move today's CLI body of `run()` unchanged into `_run_cli(request, path, digest)`.

- [ ] **Step 4: Run the new tests and the existing runtime tests.** Run `uv run pytest tests/test_claude_api_lane.py tests/test_claude_runtime.py tests/test_staff_independent_review.py -q`. Expected: PASS.

- [ ] **Step 5: Commit.** `git commit -m "feat(claude-runtime): route approved contracts to the API credit lane with subscription fallback"`

---

### Task 4: Shared-slot cancel from the newsletter writer to the runtime

**Files:**
- Modify: `src/quant_company/providers/codex_runtime.py`, the `cancel` endpoint in `create_app`
- Modify: `src/quant_company/providers/client.py`, `RuntimeClient.cancel`
- Modify: `src/quant_company/newsletter/writer.py`, `yield_to_video`
- Test: `tests/test_claude_runtime.py` (endpoint and client) and `tests/test_newsletter.py` (writer)

**Interfaces:**
- Consumes: `ClaudeRunner.cancel(request_id, shared_only=False)` (Task 3).
- Produces:
  - Endpoint `POST /v1/turns/{request_id}/cancel?scope=shared`. It calls `runner.cancel(request_id, shared_only=True)` only when the runner is a `ClaudeRunner`; other runners get a plain `cancel(request_id)`. The response is `{"request_id", "status"}`, where `status` can now be `"kept"`.
  - `RuntimeClient.cancel(request_id: str, shared_only: bool = False) -> str`. It accepts `"kept"`.
  - `yield_to_video`: calls `cancel(request_id, shared_only=True)` and skips `store.yielded` when the result is `"kept"`. `cancel_replaced` is unchanged.

- [ ] **Step 1: Write the failing tests.**

```python
async def test_shared_scope_cancel_reports_kept_over_http(...): ...   # app from create_app(ClaudeRunner with an API turn running) -> client.cancel(id, shared_only=True) == "kept"
async def test_newsletter_does_not_yield_a_turn_the_runtime_kept(...): ...  # fake provider.cancel returns "kept" -> issue attempts unchanged, request still set
async def test_newsletter_still_yields_a_cancelled_turn(...): ...    # existing behaviour with "cancelled"
```

- [ ] **Step 2: Run them to verify they fail.** Run `uv run pytest tests/test_claude_runtime.py tests/test_newsletter.py -q -k "kept or yield"`. Expected: FAIL.

- [ ] **Step 3: Implement the three edits.** Fake providers in existing newsletter tests may need a `shared_only=False` parameter.

- [ ] **Step 4: Run the tests to verify they pass.** Run `uv run pytest tests/test_claude_runtime.py tests/test_newsletter.py -q`. Expected: PASS.

- [ ] **Step 5: Commit.** `git commit -m "feat(newsletter): keep an email turn that runs on the API lane instead of yielding it"`

---

### Task 5: Deployment wiring, ops probe, watch alerts and status line

**Files:**
- Modify: `deploy/compose.yaml`
- Modify: `deploy/ops_probe.py`
- Modify: `src/quant_company/ops/watch.py`
- Modify: `src/quant_company/ops/status.py`
- Test: the existing ops test files (find them with `grep -ln claude_login tests/`) and the compose tests (`grep -ln "claude-runtime" tests/`)

**Interfaces:**
- **compose**, `claude-runtime` service:
  - environment: `CLAUDE_API_ENABLED: ${CLAUDE_API_ENABLED:-false}`, `CLAUDE_API_CAP_USD: ${CLAUDE_API_CAP_USD:-0}`, `CLAUDE_API_KEY_FILE: /run/secrets/claude_api_key`
  - `secrets: [model_runtime_token, claude_api_key]`
  - top-level secret: `claude_api_key: file: ${STATE_DIR:-/var/lib/quant-company}/secrets/claude_api_key`
- **ops_probe:** `claude_api(path=STATE / 'claude/jobs/.api/status.json') -> dict | None`. It returns the status file's keys, or `None` when the file is absent, and is added to the snapshot as `value['claude_api']` with a `'claude-api:'` probe error on OSError or ValueError.
- **watch:** `claude_api_findings(api: dict | None, now) -> list`, appended where `claude_login_findings` is.

  | Key | Severity | When | Korean title |
  |---|---|---|---|
  | `claude_api:paused:<reason>` | warning; critical for `key` and `spend_limit` | `paused` is set and its reason is not `rate_limit` or `overloaded` | `Claude API 멈춤(<사유>)` |
  | `claude_api:cap80` | warning | `spent_31d_usd >= 0.8 * cap_usd` | `Claude API 31일 사용 $X / $190` |
  | `claude_api:fallback` | warning | `last_fallback_at` within 30 minutes | `구독 대신 API로 대본을 쓰는 중`, with the line `CLAUDE_LOGIN` |
  | `claude_api:ledger` | critical | `state == "ledger_unreadable"` | — |

  - Korean labels for pause reasons: `key` 키 문제, `spend_limit` Console 지출 한도, `uncertain` 결과 불확실(30분 쉼), `ledger_unreadable` 장부 읽기 실패.
- **status:**
  - `host()` copies `payload.get("claude_api")` reduced to `state`, `spent_31d_usd`, `cap_usd` and `paused`.
  - `status_text` adds one line when the value is present: `🤖 Claude API: 31일 ${spent:.2f} / ${cap:.0f} · <켜짐|꺼짐|쉼(사유)>`.

- [ ] **Step 1: Write the failing tests.**

```python
def test_compose_gives_the_api_key_only_to_claude_runtime(): ...  # parse compose.yaml; only claude-runtime lists claude_api_key; defaults false/0
def test_probe_reads_the_lane_status_and_tolerates_absence(tmp_path): ...
def test_watch_alerts_on_key_spend_limit_cap80_fallback_and_ledger(): ...  # one finding per case; rate_limit pause -> no finding
def test_status_text_shows_the_api_line(): ...  # "🤖 Claude API: 31일 $12.50 / $190 · 켜짐"
```

- [ ] **Step 2: Run them to verify they fail.** Expected: FAIL.

- [ ] **Step 3: Implement the edits.**

- [ ] **Step 4: Run the tests to verify they pass,** together with the full ops and compose test files. Expected: PASS.

- [ ] **Step 5: Commit.** `git commit -m "feat(ops): claude-api status in ops status and watch; key secret for claude-runtime only"`

---

### Task 6: Docs, ADR and the full suite

**Files:**
- Modify: `docs/claude-runtime.md`. Add a section on the API credit lane:
  - routing table, cap, pauses and the kill switch `CLAUDE_API_ENABLED=false`;
  - status file path;
  - the owner's key-file command below, exactly.
- Create: `docs/adr/0048-claude-api-credit-lane.md`. Record the owner's decision (2026-10-23, quotes from spec "왜 바꾸나"), the paid-surface guard rails, and why strict JSON output is not used.
- Modify: `docs/deployment.md`. Record that the secret file must exist before the release that adds it, and the `runtime.env` values `CLAUDE_API_ENABLED=true` and `CLAUDE_API_CAP_USD=190`, which the operator sets.

The owner's key-file command (owner runs it; the key never appears on screen or in shell history):
```bash
ssh -t -i ~/.ssh/quant-company-lightsail-rsa ubuntu@15.165.137.122 'read -rsp "Claude API key: " k; echo; printf %s "$k" | sudo install -D -m 444 -o root -g root /dev/stdin /var/lib/quant-company/secrets/claude_api_key; unset k; sudo test -s /var/lib/quant-company/secrets/claude_api_key && echo saved'
```

- [ ] **Step 1: Write the docs and the ADR.**
- [ ] **Step 2: Run the full suite and lint.** Run `TEST_DATABASE_URL=... uv run pytest -q` and `uv run ruff check .`. Expected: all pass. Report counts.
- [ ] **Step 3: Commit.** `git commit -m "docs: Claude API credit lane runbook and ADR 0048"`

# Analyst Trading Signals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On every KRX regular session the Analyst posts two long/short signals in the AM briefing thread (open→close before the open, close→next open before the close). A model-free "always long" interim signal goes out if the Analyst is late, and every signal is graded automatically.

**Architecture:**
- A new package `quant_company.signals` owns the slot calendar, quote snapshots, the typed proposal, posts, grading and the scorecard.
- A `signals` loop in the brief-data worker schedules slots, takes the snapshot and opens a `trading_signal` Analyst task in the thread project. It sends the interim signal, grades and reports.
- The Analyst turn runs in the existing worker turn loop. Small hooks in `company.py`, `web_tools.py`, `task_control.py` and `slack.py` route `trading_signal` tasks and posts to the signals module.

**Tech Stack:** Python 3.11+, PostgreSQL, psycopg, pydantic, `exchange_calendars` (already a dependency), pytest (asyncio auto), `httpx.MockTransport` for simulated Slack, qdata (lake group, local only).

**Spec:** `docs/superpowers/specs/2026-11-18-analyst-trading-signals-design.md`. Read it first. "spec §N" refers to its 설계 sections.

**Test database:** tests skip without PostgreSQL. Start a disposable cluster once per session:
```bash
export PGDATA="$SCRATCH/pg"            # any empty directory outside the repo
initdb -D "$PGDATA" -U postgres -A trust >/dev/null
pg_ctl -D "$PGDATA" -o "-p 55432 -k $PGDATA" -l "$PGDATA/log" start
export TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/postgres
```

## Global Constraints

- **Flag:** Settings `analyst_signals_enabled: bool = False`.
  - The Settings validator raises `"Analyst signals require an enabled publishing briefing"` unless `briefing_enabled and briefing_publish_enabled`.
  - `deploy/compose.yaml` `x-db-env` gets `ANALYST_SIGNALS_ENABLED: ${ANALYST_SIGNALS_ENABLED:-false}` after `BRIEFING_KR_CLOSE_DUE_MINUTES`.
- **Do not modify render-image inputs** (`deploy/release.py` `render_changes`):
  - `src/quant_company/contracts.py`, `src/quant_company/web_fetch.py`;
  - `src/quant_company/briefing/{__init__,contracts,execution}.py`;
  - anything under `src/quant_company/video/`;
  - `pyproject.toml`, `uv.lock`.
- **Do not change** `AgentDecision`, `ToolRequest`, `roles.json`, or any input of `BriefStore.policy()`. Signals add no field to the briefing policy digest.
- **Slot times.** O is the XKRX `session_open`. C is the override `close_at` or XKRX `session_close` (`briefing.schedule.close`). All times are UTC-aware.

  | Slot | snapshot_at | snapshot_latest | tools_until | fallback_at | send_at | entry_at | exit_at |
  |---|---|---|---|---|---|---|---|
  | intraday | O−90m | O−60m | O−45m | O−32m | O−30m | O | C |
  | overnight | C−60m | C−60m | C−35m | C−22m | C−20m | C | O of the next XKRX session not closed by an override |

  - `fallback_at` is two minutes before `send_at`. This is this plan's call, so that with a 15-second loop the interim post reaches Slack before the send time.
- **One clock:** every signals time decision uses `briefing.schedule.utcnow()`, and the value is stored, so tests patch a single clock as the `brief` fixture does. This replaces the spec's "DB `now()`" as this plan's call: the worker and the brief-data worker run on the same host.
- **Limits:** tool turns 3, `web_search` 2, `web_read` 4, format re-asks 2, fault retries 2 (at least 5 minutes apart). Task priority is `-100`.
- **Kinds and IDs:**
  - Task kind and message kind: `"trading_signal"`.
  - Slot ID: `stable(f"trading-signal:{day}:{kind}")`.
  - Task ID: the slot ID for revision 1, then `stable(f"trading-signal:{day}:{kind}:r{revision}")`.
  - Snapshot source ID: `f"signal:{day}:{kind}:snapshot"`.
  - Post message IDs: `stable(f"trading-signal-post:{slot_id}:{part}")` with part `analyst|fallback|unparsed`. Scorecard: `stable(f"trading-signal-report:{day}")`.
- **Fallback rule:** always `long`. Never a rule chosen after reading results.
- **Baseline copy for the instruction** (spec table; local lake KOSPI 2010-01~2026-07-21, KODEX 200 2012-01~):
  - intraday always long: KOSPI 48.6%, KODEX 200 49.9%;
  - intraday follow prior US (SPY) session: KOSPI 47.1%, KODEX 200 47.7%;
  - the same US rule hits the gap 75.5% and close→close 62.9%;
  - overnight always long: KOSPI 59.4% (+8.7bp/day).
- **Grading:**
  - KOSPI uses official open/close from qdata `load_krx_index`, read in a credential-isolated subprocess. Refuse objects over 4 MiB.
  - KODEX 200 uses Yahoo `069500.KS` daily bars (`interval=1d&range=1mo&events=div`).
  - Returns are in bp. A return of exactly 0 is a tie and is left out of hit rates.
  - Grade from 18:30 KST, retrying every 30 minutes.
  - The report goes out once per KST day: when every return due that day is final, or at 21:00 KST.
- **Statistics:** two-sided exact binomial sign test on the days the Analyst's direction differed from a baseline. Only `p < 0.05` may be worded as better or worse.
- **AGENTS.md (verbatim):**
  - "Every external effect may be ambiguous after a crash. Preserve receipts; do not claim exactly-once delivery or automatically replay an uncertain charge or write."
  - "Test against real PostgreSQL where available. Mark simulated Slack and real Codex checks separately. Never label mocked cloud/Slack integration as deployed."
  - "No heavy training, backtests, live orders or automatic 5090 fallback in this service slice."
- **Fixed copy (Korean):**
  - Fallback second line: `롱 · Analyst 판단 아님 · 규칙 신호(항상 롱, 과거 적중 약 50%)`; overnight uses `약 59%`.
  - Fallback third line: `Analyst 신호가 늦어지고 있습니다(사유: {reason}). 나오는 대로 이어서 올립니다.`
  - Scorecard footer: `수수료·세금·호가 차이 제외. 숏은 KODEX 200 반대 방향으로 계산`.
  - Unparsed header: `{emoji} {label} · 형식 확인 실패 · 채점 제외`.

## Review Focus

1. **Same-day project:** when the AM edition arrives after a standalone signal project was made, the overnight slot must reuse the intraday slot's project. Signals of one day share one thread. Test in Task 5.
2. **Restart between snapshot and commit:** asking the same slot twice must create one task and one snapshot source. Test in Task 5.
3. **KST/UTC day boundary:** a tick at 00:30 KST (15:30 UTC the day before) schedules the KST day's slots. Test in Task 5.
4. **Foreign source IDs:** a proposal citing a source of another project (for example yesterday's snapshot) is a format error, not a signal. Test in Task 6.
5. **Owner question during a signal:** the signal's intermediate `say` is never posted. The owner's queued question still runs right after and is not held or blocked. Test in Task 6.

---

### Task 1: Flag, schema and typed contracts

**Files:**
- Create: `src/quant_company/signals/__init__.py` (docstring only), `src/quant_company/signals/contracts.py`, `src/quant_company/signals/schema.sql`
- Modify: `src/quant_company/config.py` (field after `briefing_kr_close_basis`, check in `explicit_simulation`), `src/quant_company/db.py` (migration list after `("quant_company.briefing", "schema.sql")`), `deploy/compose.yaml`
- Test: `tests/test_signals_contracts.py`

**Interfaces:**
- Produces, in `signals/contracts.py`:
  - `SIGNAL_KIND = "trading_signal"`;
  - `SignalKind = Literal["intraday","overnight"]`, `Direction = Literal["long","short"]`, `Timing = Literal["on_time","late","after_entry"]`;
  - `MAX_TOOL_TURNS = 3`, `MAX_SEARCHES = 2`, `MAX_READS = 4`, `MAX_FORMAT_RETRIES = 2`, `MAX_FAULT_RETRIES = 2`, `FAULT_RETRY_MINUTES = 5`, `FALLBACK_DIRECTION = "long"`;
  - `@dataclass(frozen=True) class Slot(day: date, kind: SignalKind, snapshot_at, snapshot_latest, tools_until, fallback_at, send_at, entry_at, exit_at: datetime, exit_day: date)`;
  - `class SignalReason(StrictModel): text: str (1..200); source_ids: list[str] (1..5)`;
  - `class SignalProposal(StrictModel): kind, session_day: date, direction: Direction, confidence: int (strict, 50..90), reasons: list[SignalReason] (1..3), invalidation: str (1..200), source_ids: list[str] (1..20)`.
- Produces the tables:
  - `trading_signals`: one row per slot, `UNIQUE(session_day, kind)`. Columns:
    - identity and slot times: `id uuid PK`, `session_day`, `kind`, `channel`, `owner_user`, the eight Slot times, `exit_day`;
    - `state` CHECK in (`scheduled`, `asked`, `committed`, `fallback_sent`, `committed_late`);
    - links: `project_id`, `task_id`, `snapshot jsonb`, `snapshot_source_id`;
    - judgment: `baselines jsonb`, `proposal jsonb`, `timing`, `committed_at`, `unparsed bool default false`;
    - posts: `analyst_message_id`, `fallback_message_id`, `unparsed_message_id`, `fallback_reason`, `fallback_sent_at`;
    - retries: `format_failures int default 0`, `fault_retries int default 0`, `last_error`, `retry_at`;
    - `created_at`, `updated_at`.
  - `trading_signal_returns`: one row per (signal_id, instrument). Columns: `instrument` CHECK in (`KOSPI`, `KODEX200`), `state` CHECK in (`final`, `pending`, `excluded`), `entry_price numeric`, `exit_price numeric`, `long_bp numeric`, `detail`, `receipt jsonb`, `reported_on date`, `updated_at`.
  - `trading_signal_reports`: `day date PK`, `project_id`, `message_id`, `summary jsonb`, `created_at`.

- [ ] **Step 1: Write the failing tests**
  - `test_flag_requires_publishing_briefing`: `Settings(analyst_signals_enabled=True)` raises a ValidationError containing "Analyst signals require an enabled publishing briefing". With `briefing_enabled=True, briefing_publish_enabled=True` it passes.
  - `test_flag_does_not_change_briefing_policy(brief)`: reuse `brief` from `tests/test_briefing.py`. The `BriefStore.policy()` value is equal before and after setting `analyst_signals_enabled = True`.
  - `test_compose_passes_flag`: `"ANALYST_SIGNALS_ENABLED: ${ANALYST_SIGNALS_ENABLED:-false}"` is in `deploy/compose.yaml`.
  - `test_proposal_bounds`: valid proposal parses. `confidence=49`, `confidence=91`, `confidence="60"`, 4 reasons, a 201-character reason, `direction="롱"` and an extra key each raise ValidationError.
  - `test_migration_creates_signal_tables(company)`: the three tables exist after `company.db.migrate()`, and inserting two rows with the same (session_day, kind) fails.
- [ ] **Step 2:** `uv run pytest tests/test_signals_contracts.py -q`. Expect failures: import error / missing field.
- [ ] **Step 3: Implement** the field, the validator line, the compose line, the migration entry, `schema.sql` (`CREATE TABLE IF NOT EXISTS`) and `contracts.py`.
- [ ] **Step 4:** rerun. Expect PASS. Then `uv run pytest tests/test_briefing.py -q` (the policy test imports its fixture). Expect PASS.
- [ ] **Step 5: Commit** `feat(signals): flag, schema and typed proposal`.

### Task 2: Slot calendar

**Files:**
- Create: `src/quant_company/signals/schedule.py`
- Test: `tests/test_signals_schedule.py`

**Interfaces:**
- Consumes `briefing.schedule.exchange`, `close`, `KST`.
- Produces:
  - `slots_for(day: date, changes: dict | None = None) -> list[Slot]`. It returns `[intraday, overnight]`, or `[]` when `day` is not a session or an override closes it.
  - `kst_day(at: datetime) -> date`.

- [ ] **Step 1: Write the failing tests** (KST wall times via `.astimezone(KST)`)
  - `test_normal_friday` (2026-11-20):
    - intraday 07:30 / 08:00 / 08:15 / 08:28 / 08:30 / 09:00 / 15:30;
    - overnight snapshot 14:30, tools 14:55, fallback 15:08, send 15:10, entry 15:30, exit 2026-11-23 09:00 with `exit_day == date(2026,11,23)`.
  - `test_csat_override_moves_overnight`: 2026-11-19 with `CalendarOverride(market="KR", day=..., close_at="2026-11-19T16:30:00+09:00", source_url="https://global.krx.co.kr/")` gives overnight snapshot 15:30, send 16:10, entry 16:30. Intraday send stays 08:30.
  - `test_first_session_of_year`: 2027-01-04 gives intraday snapshot 08:30, send 09:30, entry 10:00.
  - `test_year_end_overnight_exit`: 2026-12-30 overnight `exit_day == date(2027,1,4)`, exit 10:00.
  - `test_weekend_and_override_closure`: 2026-11-21 → `[]`. A `closed=True` override → `[]`. A closed next session is skipped for `exit_day`.
  - `test_kst_day_boundary`: `kst_day(datetime(2026,11,19,15,30,tzinfo=UTC)) == date(2026,11,20)`.
- [ ] **Step 2:** `uv run pytest tests/test_signals_schedule.py -q`. Expect FAIL (module missing).
- [ ] **Step 3: Implement** with `exchange("KR", day.year).session_open(str(day))` and `close("KR", day, changes)`. For the next session, walk `next_session` until `changes.get(("KR", d))` is not closed.
- [ ] **Step 4:** rerun. Expect PASS.
- [ ] **Step 5: Commit** `feat(signals): slot calendar`.

### Task 3: Quote snapshot

**Files:**
- Create: `src/quant_company/signals/snapshot.py`
- Modify: `src/quant_company/briefing/data_reader.py` (`fetch_chart` gains `params: str = "interval=1d&range=5d"`; the default request path is unchanged)
- Test: `tests/test_signals_snapshot.py`

**Interfaces:**
- Produces:
  - `Instrument(symbol: str, label: str, kinds: frozenset[str], live: bool)` (NamedTuple) and `CATALOGUE: dict[str, Instrument]` with keys `es ES=F`, `nq NQ=F`, `spy SPY`, `ewy EWY`, `usdkrw KRW=X`, `kospi ^KS11`, `kodex200 069500.KS`, `kosdaq ^KQ11` (both kinds), plus `n225 ^N225`, `hsi ^HSI`, `shanghai 000001.SS` (overnight only).
    - `live=True` for es, nq, usdkrw in both kinds.
    - In the overnight snapshot `live=True` also applies to kospi, kodex200, kosdaq, n225, hsi and shanghai.
  - `parse_quote(payload: dict, symbol: str) -> dict` with keys `symbol, value, source_time, session_day, session_open, previous_close, change_pct`.
    - Dates use `meta.exchangeTimezoneName`, never New York. `briefing.data_reader.parse_chart` must not be reused: for KRX bars its New York dates make today's bar look like the prior one.
  - `read_snapshot(kind: SignalKind, fetch=fetch_chart, *, clock=utcnow) -> dict` returns `{"kind","observed_at","quotes":{key:dict},"errors":{key:code},"receipts":{key:sha256}}`.
    - Use at most 8 threads and a 25 s total wait, like `read_us_close`.
    - A quote is `delayed` when it is live for this kind and `observed_at - source_time > 20 min`.
  - `snapshot_text(snapshot: dict) -> str`: one line per quote, `{label} | {value} | 원천 HH:MM KST | 전일 대비 {±x.xx}%` with an optional ` | 지연`, then `빠짐: {key}({code}), ...`.
  - `follow_us(snapshot: dict) -> Direction | None`: the sign of SPY `value/previous_close-1`. Returns `None` if 0 or missing.

- [ ] **Step 1: Write the failing tests** with a helper `chart(symbol, tz, price, at, bars)` that builds a Yahoo v8 payload.
  - `test_krx_previous_close_uses_seoul_dates`: bars for 11-18 and 11-19 at 09:00 KST and `regularMarketTime` 11-19 15:30 KST give `session_day == date(2026,11,19)`, `previous_close` = the 11-18 close and `session_open` = the 11-19 open.
  - `test_overnight_catalogue_and_delay`: a kospi source time 25 minutes before `observed_at` is `delayed`. The same age on `spy` is not.
  - `test_failures_are_listed_not_guessed`: a fetch returning `{"ok": False, "error": "chart_network_unavailable"}` for `hsi` puts it in `errors`, and `snapshot_text` ends with `빠짐: hsi(chart_network_unavailable)`.
  - `test_follow_us_sign`: SPY 750 vs 745 → `"long"`; equal → `None`.
  - `test_fetch_chart_default_path_unchanged`: a fake connection records `/v8/finance/chart/%5EGSPC?interval=1d&range=5d` for the default and `...?interval=1d&range=1mo&events=div` with `params=`.
- [ ] **Step 2:** run `uv run pytest tests/test_signals_snapshot.py -q`. Expect FAIL.
- [ ] **Step 3: Implement** `snapshot.py` and the `params` keyword.
- [ ] **Step 4:** rerun, plus `uv run pytest tests/test_briefing.py tests/test_briefing_loops.py -q`. Expect PASS.
- [ ] **Step 5: Commit** `feat(signals): quote snapshot`.

### Task 4: Instruction and post copy

**Files:**
- Create: `src/quant_company/signals/instructions.py`, `src/quant_company/signals/render.py`
- Test: extend `tests/test_signals_contracts.py`

**Interfaces:**
- Produces:
  - `instruction(slot: Slot, source_id: str, text: str) -> str`.
  - `final_note(reason: str) -> str`: the service note before a forced final or a re-ask. It contains `도구 없이 지금 최종 답` and the reason.
  - `window(slot: Slot) -> str`: intraday `시가 09:00 진입 → 종가 15:30 청산`; overnight `종가 15:30 진입 → 11월 23일(월) 시가 09:00 청산`.
  - `analyst_post(slot, proposal, snapshot, *, late_at: datetime | None = None, fallback: Direction | None = None) -> str`.
  - `fallback_post(slot: Slot, reason: str) -> str`.
  - `unparsed_post(slot: Slot, say: str) -> str`.
  - `scorecard_post(day: date, lines: list[str], summary: dict) -> str` (filled in Task 9; here it renders `lines` plus the footer).

- [ ] **Step 1: Write the failing tests**
  - `test_instruction_has_every_required_element`. The intraday instruction for 2026-11-20 contains:
    - `09:00`, `15:30`, `08:30`, `08:15`, the source ID, and the snapshot text verbatim;
    - `48.6`, `49.9`, `47.1`, `75.5`, `62.9`, `59.4`;
    - `밤사이 정보는 대부분 시초가에 반영`, `롱 또는 숏`, `주문이 아니`, `SignalProposal`, `artifacts`.
    - The overnight instruction lacks `밤사이 정보는 대부분 시초가에 반영` and contains `11월 23일`.
  - `test_analyst_post_matches_spec_sketch`. Line 1 is `📈 장중 신호 · 11월 20일(금)  시가 09:00 진입 → 종가 15:30 청산`. Line 2 is `롱 · 확신도 58%`. The post contains `근거`, `1. `, `틀렸다고 볼 조건: ` and a `기준 시세 07:30` line. It never contains `누적`.
  - `test_late_post_header`: `late_at` 08:41 KST with `fallback="long"` and a short proposal gives line 1 `📈 장중 신호 · 늦음 08:41 · 임시 신호와 반대`, then the window line.
  - `test_fallback_post_copy`: intraday line 2 equals the Global Constraints copy with `약 50%`; overnight uses `약 59%`; line 3 carries the reason.
  - `test_unparsed_post_header`: `🌙 오버나잇 신호 · 형식 확인 실패 · 채점 제외`.
- [ ] **Step 2:** run. Expect FAIL.
- [ ] **Step 3: Implement**. Use Korean weekday letters `월화수목금토일`. Instruction requirements:
  - The proposal goes in exactly one artifact with JSON content; artifact `source_ids` stays empty and IDs go inside the JSON.
  - Every reason cites at least one approved source ID, and the snapshot ID is always cited.
- [ ] **Step 4:** rerun. Expect PASS.
- [ ] **Step 5: Commit** `feat(signals): instruction and post copy`.

### Task 5: Scheduling, asking and the loop

**Files:**
- Create: `src/quant_company/signals/store.py` (class `SignalStore`), `src/quant_company/signals/runner.py`, `tests/signals_fixtures.py`
- Modify: `src/quant_company/runtime.py` (`brief_data_loops`), `src/quant_company/slack.py` (thread routing query near `EXISTS(SELECT 1 FROM brief_editions b ...)`)
- Test: `tests/test_signals_store.py`

**Interfaces:**
- Consumes `slots_for`, `kst_day`, `read_snapshot`, `snapshot_text`, `follow_us`, `instruction`, `Company._new_task`, `Company._project`, `briefing.schedule.overrides`.
- Produces in `SignalStore(company)`:
  - `schedule(at) -> int`: inserts the slots of `kst_day(at)` and the next KST day. ON CONFLICT DO NOTHING; returns rows inserted.
  - `due_asks(at) -> list[dict]`: rows in `scheduled` with `snapshot_at <= at`. An intraday row is left out while no AM edition is delivered and `at < snapshot_latest`.
  - `ask(row, snapshot, at) -> bool`, in one transaction, with `SELECT ... FOR UPDATE` and returning False unless still `scheduled`:
    - project: the same day's other slot's project if it has one; else the AM edition project (`brief_editions` day/`am`/`committed`, channel and owner from settings, `projects.thread_ts IS NOT NULL`); else a standalone project `stable(f"signal-project:{day}")` titled `{M}월 {D}일 매매 신호`;
    - the snapshot source: approved, `available_at = observed_at`, content `snapshot_text`;
    - `baselines` `{"always_long": "long", "follow_us": follow_us(...)}`;
    - the task: `SIGNAL_KIND`, priority `-100`, instruction from `instruction()`;
    - state `asked`.
- `SignalStore._thread_project(conn, row) -> dict`: the project rule above (other slot's project, then AM edition project, then the standalone project). `ask` uses it, and Task 7's `send_fallbacks` reuses it.
- Produces `SignalRunner(company, *, fetch=fetch_chart, reader=None)` with `async def tick() -> dict`. Here it runs schedule, then asks (fetching outside the transaction), and returns counts. Every blocking call goes through `asyncio.to_thread`, as in `BriefDataCollector`. Tasks 7–9 add their calls to `tick`, and Task 8 makes `read_kospi` the default `reader`.
- `brief_data_loops` appends `Loop("signals", runner.tick, timeout=120, attempts=2, retry_seconds=5, delay=15)` only when `analyst_signals_enabled`.
- `tests/signals_fixtures.py` provides:
  - `signals` fixture: the `company` with briefing and signals on, channel `CQUANT`, owner `UHUMAN`, BRIEFER role loaded;
  - `am_project(company, day, thread_ts="100.001") -> dict`: inserts a project and a committed `brief_editions` am row;
  - `snapshot(kind)`: a recorded snapshot dict;
  - `proposal(**over)`: a valid proposal dict.

- [ ] **Step 1: Write the failing tests**
  - `test_schedule_is_idempotent`: two calls return 4 then 0.
  - `test_tick_at_kst_midnight_schedules_kst_day` (Review Focus 3).
  - `test_ask_uses_am_thread_and_priority`: with `am_project` present the task has agent `market_brief`, kind `trading_signal` and priority `-100`, and its instruction contains `signal:2026-11-20:intraday:snapshot`.
  - `test_ask_twice_creates_one_task_and_source` (Review Focus 2).
  - `test_intraday_waits_for_am_edition_until_latest`: no AM edition at 07:31 → not due; at 08:00 → due, and the ask creates the standalone project.
  - `test_overnight_reuses_intraday_project` (Review Focus 1): the standalone project for intraday, then `am_project` created, then the overnight ask uses the same project.
  - `test_thread_reply_in_standalone_project_reaches_analyst(credentials)`: after the standalone project gets `thread_ts`, `SlackIngress.accept(BRIEFER, payload, ...)` creates a `market_brief` task.
  - `test_loop_only_when_enabled`: `brief_data_loops` has names `["brief-data"]` when off and `["brief-data", "signals"]` when on.
- [ ] **Step 2:** `uv run pytest tests/test_signals_store.py -q`. Expect FAIL.
- [ ] **Step 3: Implement** the store methods, the runner and the two hooks. The routing query adds `OR EXISTS(SELECT 1 FROM trading_signals s WHERE s.project_id=p.id)` to the `briefing` column.
- [ ] **Step 4:** rerun, plus `uv run pytest tests/test_briefing_loops.py -q`. Expect PASS.
- [ ] **Step 5: Commit** `feat(signals): schedule slots and ask the Analyst`.

### Task 6: Analyst turn hooks and commit

**Files:**
- Modify: `src/quant_company/signals/store.py` (module functions below)
- Modify `src/quant_company/company.py`:
  - `prepare_turn`: the "Allowed tools" line, and the daily cap check, which skips `SIGNAL_KIND`;
  - `commit_turn`: a branch right after the `held` check, and `publish=` of the `say` message;
  - `block_turn`: an early branch;
  - `_new_turn`: no owner-facing limit message for `SIGNAL_KIND`.
- Modify: `src/quant_company/web_tools.py` (`prepare`: skip execution when `tools_allowed` is False; no daily-cap fault for `SIGNAL_KIND`), `src/quant_company/task_control.py` (`held` returns False for `SIGNAL_KIND`)
- Test: `tests/test_signals_turns.py`

**Interfaces:**
- `allowed_tools(conn, task: dict, tools: list[str]) -> list[str]` returns `[]` when any of these holds:
  - `now() >= tools_until`;
  - tool turns used `>= MAX_TOOL_TURNS`: completed turns of the task whose `response->'decision'->'tools'` is non-empty;
  - `format_failures > 0`.
  Otherwise it returns `tools` minus `web_search` once 2 `tool:web_search` messages exist, and minus `web_read` once 4 exist.
- `tools_allowed(conn, task, decision) -> bool`: every requested tool is in `allowed_tools`, and the counts after this decision stay within limits.
- `commit_turn(company, conn, project, task, turn, response, decision) -> dict | None`:
  - Returns `None` when `decision.status == "continue"`, it has tools, no artifacts, and `tools_allowed`. The general path then applies the tools and queues the next turn.
  - Otherwise it parses with `briefing.editor.artifact(response, SignalProposal)`, then checks:
    - kind and day equal the slot's;
    - every ID (proposal and reasons) is an approved source of this project;
    - the snapshot ID is present.
  - Valid proposal: lock the slot row, then:
    - set the timing: `on_time` if the state is `asked`, else `late` if `now() < entry_at`, else `after_entry`;
    - store the proposal, `committed_at=now()` and state `committed` or `committed_late`;
    - insert the post with `Company._message(conn, project, task["id"], BRIEFER, SIGNAL_KIND, text, message_id=...)` only if `now() < exit_at`;
    - save the turn as completed with its response and the task as completed, and add a `turn_completed` event.
    - It returns `{"state": "completed", "task_status": "completed", "duplicate": False}`.
  - Invalid proposal:
    - increment `format_failures` and store `last_error`;
    - if `format_failures <= MAX_FORMAT_RETRIES` and `now() < exit_at`: add `Company._message(conn, project, task["id"], "service:signals", "signal_note", final_note(error), recipient=BRIEFER, publish=False)`, complete this turn and `Company._new_turn(conn, task)`;
    - otherwise: mark `unparsed`, post `unparsed_post` if `say` is not empty and `now() < exit_at`, and complete the task.
    - Never raise for a model-format problem.
- `turn_blocked(conn, task: dict, reason: str) -> None`: stores `last_error=reason` and `retry_at=now()+5 min`. `block_turn` still blocks the turn and task but posts no status message for `SIGNAL_KIND`.
- General-path change: `publish=task["kind"] not in {"answer", SIGNAL_KIND} or final`.
- `company.py` imports the hook as `from .signals.store import commit_turn as commit_signal_turn`, inside the branch, like the research imports.

- [ ] **Step 1: Write the failing tests.** Drive real turns: `company.prepare_turn(turn_id)`, then `company.commit_turn(turn_id, ProviderResponse(...))` with fixture decisions.
  - `test_on_time_commit_posts_once`: one `trading_signal` message whose text starts `📈 장중 신호`, and timing `on_time`. Committing the same response again returns `duplicate`.
  - `test_tool_round_say_not_published` (part of Review Focus 5): a `continue` decision with one `calculate` tool and `say="확인 중"`. The `say` is stored in messages with no outbox row, and a next turn is queued.
  - `test_tools_after_cutoff_are_refused`: with the clock at `tools_until` the prepared prompt contains `Allowed tools: []`. A decision with a tool then becomes a re-ask: a `signal_note` message and a new turn, with no web request executed (`web_requests` empty).
  - `test_search_caps`: after 2 `tool:web_search` messages, `allowed_tools` excludes `web_search`. A third search makes `web_tools.prepare` return `[]`.
  - `test_foreign_source_is_a_format_error` (Review Focus 4): a proposal citing another project's source ID gives `format_failures == 1` and no post.
  - `test_third_format_failure_posts_say_unparsed`: after three invalid answers the post starts with `📈 장중 신호 · 형식 확인 실패 · 채점 제외`, `unparsed` is true, and nothing is graded later.
  - `test_daily_cap_does_not_hold_signals`: with the company cap at 0 a signal turn prepares `ready`, while a `work` task defers with `daily_turn_budget`.
  - `test_routing_hold_does_not_hold_signal`: a pending `routing` task in the project; the signal turn still prepares `ready`.
  - `test_owner_question_runs_after_signal` (Review Focus 5): an owner question queued during the signal; after the signal commits, `due_turns()` lists the owner turn and it prepares `ready`.
  - `test_block_turn_posts_nothing_for_signal`: `company.block_turn(turn_id, "auth")` leaves no `status` message for the task and stores `last_error == "auth"`.
- [ ] **Step 2:** `uv run pytest tests/test_signals_turns.py -q`. Expect FAIL.
- [ ] **Step 3: Implement** the module functions and the hooks.
- [ ] **Step 4:** rerun, plus `uv run pytest tests/test_company.py tests/test_task_control.py tests/test_web_research.py tests/test_briefing.py tests/test_director_mentions.py -q`. Expect PASS.
- [ ] **Step 5: Commit** `feat(signals): Analyst signal turns`.

### Task 7: Interim signal, late posts, outbox and retries

**Files:**
- Modify: `src/quant_company/signals/store.py` (`send_fallbacks`, `retry`, `gate`)
- Modify `src/quant_company/slack.py`:
  - `_claim_one`: add `'trading_signal'` to `m.kind IN ('control','maintenance','status')` and call `SignalStore(self.company).gate(conn, row)` for that kind;
  - `settle`: add `'trading_signal'` to the kinds that set a missing `projects.thread_ts`.
- Test: extend `tests/test_signals_store.py`

**Interfaces:**
- `send_fallbacks(at) -> int`: rows in `scheduled` or `asked` with `fallback_at <= at < entry_at`, locked. Get the project with `_thread_project`, post `fallback_post(slot, reason)` and set state `fallback_sent` and `fallback_sent_at`. At or after `entry_at` (for example, the worker was down all morning), set the state with reason `서비스 중단` and post nothing: a "시가 진입" signal after the open is useless. The reason comes from the task and turn state:

  | Task / turn state | Reason |
  |---|---|
  | no task | `준비 중 오류` |
  | queued while another turn runs | `앞선 업무가 끝나기를 기다리는 중` |
  | running | `분석이 아직 끝나지 않음` |
  | waiting, error `quota` | `모델 사용 한도` |
  | waiting, other error | `모델 혼잡` |
  | blocked, `auth` | `모델 로그인 만료` |
  | other blocked | `모델 응답 오류` |

- `retry(at) -> int`:
  - A blocked signal task with `retry_at <= at < exit_at` and `fault_retries < 2` gets `fault_retries += 1`, task status `pending` and `Company._new_turn`.
  - A task whose revision differs from its project's, while `at < entry_at`, gets a fresh task at the current revision with the Global Constraints ID, and the row's `task_id` updates.
- `gate(conn, row) -> bool`:
  - Posts after the slot's `exit_at` become `stale` (`signal_after_exit`).
  - An analyst post of a slot whose fallback post is not `delivered` (`pending` or `sending`) defers 5 s (`pending`, `next_at`).
  - A non-root post in a standalone project waits until the project has `thread_ts`, then copies it into the row and the dict.
  - Scorecard posts always pass.

- [ ] **Step 1: Write the failing tests**
  - `test_fallback_sent_once_at_fallback_at`: before 08:28 → 0; at 08:28 → 1 with line 2 `롱 · Analyst 판단 아님 · 규칙 신호(항상 롱, 과거 적중 약 50%)`; again → 0.
  - `test_no_fallback_when_on_time`: a committed signal at 08:20 means no fallback at 08:28.
  - `test_fallback_reason_from_auth_block`: after `block_turn(..., "auth")` the reason is `모델 로그인 만료`.
  - `test_fallback_when_model_never_answers`: no turn ever runs, and the interim post still exists.
  - `test_no_fallback_post_after_entry`: the first tick after a downtime at 09:05 records reason `서비스 중단` and creates no post.
  - `test_late_signal_after_fallback`: a commit at 08:41 posts `📈 장중 신호 · 늦음 08:41 · 임시 신호와 반대` for a short proposal, with timing `late`. A commit after 09:00 gives `after_entry`. A commit after 15:30 records but posts nothing.
  - `test_analyst_post_waits_for_fallback_delivery`: with the fallback `pending`, `gate` returns False and the analyst row stays `pending` with a later `next_at`.
  - `test_signal_post_passes_routing_hold(credentials)`: a pending routing task in the project. `SlackOutbox(..., httpx.MockTransport(...)).send_one()` still sends the signal (simulated Slack).
  - `test_standalone_thread_ts_from_first_delivery(credentials)`: after the first post is delivered with ts `300.001`, the next signal post is sent with `thread_ts == "300.001"`.
  - `test_blocked_signal_retried_twice`: after `auth` blocks, a retry happens at +5 min and +10 min, then none.
  - `test_revision_change_recreates_task`: after `UPDATE projects SET revision=2`, `retry` creates a task with the `:r2` ID before entry.
  - `test_uncertain_signal_post_not_resent(credentials)`: a simulated Slack timeout leaves the post `uncertain`, and later `send_one()` calls make no second request for it.
  - `test_signals_never_enqueue_public_outputs(monkeypatch)`: with `VideoStore.enqueue_isolated` and `NewsletterStore.enqueue_isolated` patched to raise, schedule → ask → commit → fallback runs without error.
- [ ] **Step 2:** run. Expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** rerun, plus `uv run pytest tests/test_briefing.py -k slack -q`. Expect PASS.
- [ ] **Step 5: Commit** `feat(signals): interim signal, late posts and outbox gate`.

### Task 8: Grading

**Files:**
- Create: `src/quant_company/signals/grading.py`, `src/quant_company/signals/lake.py`
- Modify: `src/quant_company/briefing/data.py` (extract `run_lake_module`; `query` calls it with `quant_company.briefing.data_reader`), `src/quant_company/signals/store.py` (`grading_due`, `save_returns`)
- Test: `tests/test_signals_grading.py`

**Interfaces:**
- `run_lake_module(root: str, module: str, payload: dict, *, timeout: int = 45) -> dict`: today's `query` body, unchanged in environment, lock and limits.
- `signals.lake.main()`:
  - stdin `{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}`;
  - stdout `{"ok": true, "rows": [{"date","open","close"}], "source": {...}}` for `index == "KOSPI"`;
  - fixed error codes: `dataset_exceeds_signal_budget` when `inspect_dataset("krx_index")` reports `size_bytes > 4*1024*1024`, `lake_access_or_format_unavailable` otherwise.
- `signals.lake.read(api, start: date, end: date) -> dict`: the body `main()` calls with `qdata.api`, so tests can pass a fake `api`.
- `read_kospi(root: str, start: date, end: date) -> dict`: `run_lake_module(root, "quant_company.signals.lake", ...)`. It becomes `SignalRunner`'s default `reader`.
- `grading.long_return_bp(entry: Decimal, exit: Decimal) -> Decimal`: `(exit/entry-1)*10000`, rounded to 0.01.
- `grading.outcome(direction, long_bp) -> Literal["hit","miss","tie"]`.
- `grading.kodex_bars(payload: dict) -> tuple[dict[date, dict], set[date] | None]`: bars by Seoul date with `open`, `close`, `volume`, and ex-dividend dates. The set is `None` when the payload has no `events` key.
- `store.grading_due(at) -> list[dict]`: slots that still lack a final or excluded return for either instrument, when all of these hold:
  - `exit_at < at`;
  - the KST time is 18:30 or later on or after the grading day (`session_day` for intraday, `exit_day` for overnight);
  - no `pending` return of the slot has `updated_at > at - 30 min`.
- `store.save_returns(returns: list[dict]) -> int`: upserts a `trading_signal_returns` row per (signal, instrument).
- Rules:
  - KOSPI final only from the official session rows: open and close of D for intraday; close of D and open of `exit_day` for overnight. Missing rows → `pending`.
  - KODEX 200 is `pending` when a bar is missing or `open == close` with `volume > 0`.
  - Overnight KODEX 200 is `excluded` with detail `분배락` when `exit_day` is an ex-dividend date. When the dividend set is `None`, it is `final` with detail `분배 미확인`.
  - Every receipt stores the qdata `source` or the Yahoo sha256.

- [ ] **Step 1: Write the failing tests**
  - `test_returns_and_outcomes`: `long_return_bp(Decimal("100"), Decimal("101")) == Decimal("100.00")`. `outcome("short", Decimal("-31.00")) == "hit"`. `outcome("long", Decimal("0")) == "tie"`.
  - `test_kospi_final_needs_official_rows`: with the reader returning no row for D the returns stay `pending`. When the reader adds it they turn `final`.
  - `test_suspicious_yahoo_bar_is_pending`: open == close with volume 10 → KODEX200 `pending`.
  - `test_overnight_dividend_excluded`: ex-dividend on `exit_day` → `excluded`, detail `분배락`. Without events → `final`, detail `분배 미확인`.
  - `test_lake_budget_refusal`: a fake `api` with `size_bytes = 5*1024*1024` makes `lake.read(api, ...)` return `dataset_exceeds_signal_budget`.
  - `test_query_refactor_keeps_behavior`: `briefing.data.query` with `root=""` still returns `lake_not_connected`.
- [ ] **Step 2:** run. Expect FAIL.
- [ ] **Step 3: Implement** with the reader and the chart injected through `SignalRunner`.
- [ ] **Step 4:** rerun, plus `uv run pytest tests/test_briefing_loops.py tests/test_briefing_kr_close.py -q`. Expect PASS.
- [ ] **Step 5: Commit** `feat(signals): grade signals against official and ETF prices`.

### Task 9: Scorecard

**Files:**
- Modify: `src/quant_company/signals/grading.py` (`sign_test_p`, `summarize`), `src/quant_company/signals/render.py` (`scorecard_post`), `src/quant_company/signals/store.py` (`report`)
- Test: extend `tests/test_signals_grading.py`

**Interfaces:**
- `sign_test_p(wins: int, losses: int) -> float`: `min(1, 2*sum(comb(n,k) for k<=min(w,l))/2**n)`. Returns 1.0 for n=0.
- `summarize(slots: list[dict]) -> dict`. Each item is one slot: `kind`, `analyst` (direction or None), `timing`, `unparsed`, `received` (direction or None), `baselines`, and `returns` (instrument → {state, long_bp}). Only final returns are counted. Per kind and instrument it gives:
  - `analyst` (n, hits, misses, ties, mean_bp, sum_bp), counting timing `on_time`/`late` only and not `unparsed`;
  - `received`, `always_long`, and `follow_us` (intraday only);
  - `versus[baseline]` (wins and losses on disagreement days, and `p`);
  - `counts` (on_time, late, fallback, after_entry, unparsed, pending) and `fallback_reasons` (reason → count).
- The report always summarizes every slot since the first one. There is no date-range parameter.
- "Received" is the direction of the last `delivered` post (analyst or fallback) whose Slack `sent_ts` is earlier than `entry_at`.
- `report(at) -> bool`:
  - Only after 18:30 KST and once per KST day (`trading_signal_reports` PK).
  - Posts when every return due today is final or excluded, or when `at >= 21:00 KST`.
  - Today's lines are the intraday slot of today and the overnight slot whose `exit_day` is today. Also added: returns that became final since the last report, marked `지난 보류분`.
  - Sets `reported_on`, then posts into today's intraday slot's project, or the overnight slot's.
  - Skips when today has neither a slot nor a return.
- Wording:
  - for `p >= 0.05`: `→ 아직 우연과 구분할 수 없음`;
  - for `p < 0.05`: `→ {기준선}보다 낫다` or `→ {기준선}보다 못하다` (more wins = 낫다).

- [ ] **Step 1: Write the failing tests**
  - `test_sign_test_values`: `sign_test_p(5,4) == 1.0`, `round(sign_test_p(9,1),4) == 0.0215`, `sign_test_p(0,0) == 1.0`.
  - `test_summary_counts_and_received`: two intraday slots, one on time short (hit) and one fallback long (miss). Analyst n=1 hits=1; received n=2 hits=1; `counts.fallback == 1`.
  - `test_report_once_and_cutoff`: at 19:00 with a KOSPI return pending → no post. At 21:00 → one post containing `채점 보류` and the footer. A second call → False.
  - `test_late_final_is_reported_as_previous_pending`: a return finalised the next day appears under `지난 보류분` once.
  - `test_scorecard_wording`: 9 wins, 1 loss vs always long → `항상 롱보다 낫다`. 5 wins, 4 losses → `아직 우연과 구분할 수 없음`.
- [ ] **Step 2:** run. Expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** rerun. Expect PASS.
- [ ] **Step 5: Commit** `feat(signals): daily scorecard`.

### Task 10: Docs, evidence and qualification

**Files:**
- Create:
  - `docs/adr/0052-analyst-daily-trading-signals.md`;
  - `scripts/qualify_signals.py`;
  - `docs/superpowers/specs/assets/2026-11-18-analyst-trading-signals/signal_baselines_s3.txt`;
  - `tests/test_signals_qualification.py`.
- Modify: `docs/briefing.md` (new section `## Analyst 매매 신호`), `docs/project/README.md` (one line linking the ADR)
- Test: `tests/test_signals_qualification.py`

**Interfaces:**
- `scripts/qualify_signals.py`:
  - Inputs: `--snapshot PATH` (JSON from `read_snapshot`), `--edition PATH` (edition text), `--kind`, `--day`, `--output PATH`.
  - It builds the same prompt a signal turn gets, runs one request through `provider_for(company)` and validates with the Task 6 parser. It writes the request digest, the model, the proposal or the error, and the latency in seconds.
  - It refuses with `qualification_requires_codex_preview` unless `model_provider == "codex"` and `analyst_signals_enabled` is False. It never writes outbox rows.

- [ ] **Step 1: Write the failing test** `test_qualification_refuses_live_or_fixture`: the fixture provider → refusal. `analyst_signals_enabled=True` → refusal. The outbox count stays 0.
- [ ] **Step 2:** run. Expect FAIL.
- [ ] **Step 3: Implement** the script.
  - ADR 0052 covers the decision, timing, interim rule, grading sources, what it does not do, and that real Codex and Slack evidence is separate. It also states that the pending Opus xhigh briefing switch (`staff_models.BRIEFING`) must first show that signal turns still commit before the send time.
  - The `docs/briefing.md` section covers the operator values: the flag, the release-operator handoff, and how to read the scorecard.
- [ ] **Step 4: Re-run the frozen baselines on the production lake.**
  - Run `QDATA_LAKE=s3://insight-invest-datalake/qdata ~/Quant/quant-data/.venv/bin/python -I docs/superpowers/specs/assets/2026-11-18-analyst-trading-signals/signal_baselines.py > .../signal_baselines_s3.txt`.
  - Expected: KOSPI rows start in 2016-01, and the rules are unchanged. If S3 credentials are absent, write that sentence into the file instead and continue.
- [ ] **Step 5: Full verification:** `uv run ruff check .` gives `All checks passed!`, and `uv run pytest -q` passes with no new skips except those needing qdata or live models.
- [ ] **Step 6: Commit** `docs(signals): ADR 0052, operator guide and baseline evidence`.

## After the plan (not tasks)

- Open the PR from `JJongAchii/feat-analyst-daily-kospi`. The owner merges with "Create a merge commit". `deploy/release.py` releases CI-green main.
- Hand the release operator the main SHA and `ANALYST_SIGNALS_ENABLED=true`. Changes needed: no render image, no new service, no host unit.
- Real Codex qualification (`scripts/qualify_signals.py` on the host by the operator) and the first five trading days of observation are separate evidence under `docs/project/evidence`.
- During one market session, record how many minutes Yahoo's KRX quotes trail real time (spec §2).

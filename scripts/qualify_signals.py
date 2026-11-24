"""Opt-in live qualification of one Analyst trading signal on the real signal path, in a disposable local PostgreSQL
database. It never constructs the Slack sender and never touches production.

Run it from a repository checkout that has the local ChatGPT login for Codex (CODEX_HOME, default ~/.codex) and a
disposable local PostgreSQL cluster (TEST_DATABASE_URL, or .local/test-env.json):

    uv run --frozen python scripts/qualify_signals.py --live --kind overnight --fetch-snapshot \\
        --edition <file> --output docs/project/evidence/<name>.json

The script creates a database on that cluster, migrates it, adds an AM-edition-like project carrying the edition
text, freezes the snapshot and asks the Analyst through SignalStore exactly as the signals loop does, then runs the
signal task with TurnExecutor and a direct CodexRunner until it commits, is re-asked to exhaustion or stops on a fault.
The signals clock starts at the slot's snapshot time and runs in real time, so the tool cutoff and the on-time/late
timing follow the real latency. The receipt is written to --output and the database is dropped.

CodexRunner runs only the validated Codex CLI (src/quant_company/providers/codex_release.json) and refuses any other
version before calling the model; when the codex on PATH is another version, pass the validated one with --codex-bin."""

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager, nullcontext
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb

from quant_company.briefing import schedule
from quant_company.briefing.contracts import BRIEFER
from quant_company.briefing.store import BriefStore
from quant_company.company import Company, as_json, stable
from quant_company.config import Settings
from quant_company.contracts import ProviderFault
from quant_company.execution import TurnExecutor
from quant_company.signals.contracts import MAX_FORMAT_RETRIES, MAX_TOOL_TURNS
from quant_company.signals.schedule import kst_day, slots_for
from quant_company.signals.snapshot import read_snapshot, snapshot_text
from quant_company.signals.store import SignalStore, slot_id

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
# Three tool rounds, the answer and two format re-asks: the most turns one signal task can take.
MAX_TURNS = MAX_TOOL_TURNS + 1 + MAX_FORMAT_RETRIES
CHANNEL, OWNER = "CQUALIFY", "UQUALIFY"
EDITION_INSTRUCTION = ("이 발간본과 원문을 기준으로 후속 질문에 답한다. 새 정보는 새 조회 시각을 표시한다. "
                       "금융전략 업무는 사용자가 명시적으로 요청할 때만 전달한다.")


class Refused(ValueError):
    """Refused before any database, network or model use."""


def local_admin(admin: str | None) -> str:
    """The admin URL of a local PostgreSQL cluster to create the disposable database on; anything else is refused."""
    if not admin:
        local = Path(".local/test-env.json")
        admin = os.environ.get("TEST_DATABASE_URL") or (
            json.loads(local.read_text())["database_url"] if local.exists() else None)
    if not admin or conninfo_to_dict(admin).get("host") not in LOCAL_HOSTS:
        raise Refused("qualification_requires_local_database")
    return admin


def pick_slot(kind: str, day: date | None, after: datetime, changes):
    """The slot of kind on day, else the first one whose trade starts after `after` (the next signal of that kind)."""
    days = [day] if day else [kst_day(after) + timedelta(days=offset) for offset in range(15)]
    for candidate in days:
        for slot in slots_for(candidate, changes):
            if slot.kind == kind and (day or slot.entry_at > after):
                return slot
    raise ValueError("qualification_day_has_no_slot")


def settings_for(database_url: str) -> Settings:
    """The disposable company: the briefing and signals on, a closed Slack identity that no sender ever uses, web
    tools on as in production, and no account wrapping (the runner is the provider)."""
    return Settings(database_url=database_url, model_provider="codex", fixture_mode=False,
                    briefing_enabled=True, briefing_publish_enabled=True, analyst_signals_enabled=True,
                    briefing_channel_id=CHANNEL, briefing_owner_user=OWNER, company_web_enabled=True,
                    model_accounts_enabled=False, model_assignments_enabled=False, company_max_task_turns=8,
                    company_max_daily_turns=0)


def edition_project(company: Company, day: date, text: str) -> dict:
    """A committed AM edition of day whose parent post counts as delivered, with the edition text as its first part,
    as BriefStore.flush leaves it (no originals: the qualification has none)."""
    edition = next(e for e in schedule.editions(day, CHANNEL, OWNER) if e.kind == "am")
    with company.db.transaction() as conn:
        project = conn.execute("""INSERT INTO projects(id,title,instruction,owner_user,channel,thread_ts)
            VALUES (%s,%s,%s,%s,%s,%s) RETURNING *""", (stable("brief-project:" + edition.id), f"{day} am 시장 브리핑",
                                                       EDITION_INSTRUCTION, OWNER, CHANNEL, "qualification.0001")
                               ).fetchone()
        conn.execute("""INSERT INTO brief_editions(id,day,kind,channel,owner_user,definition,due_at,cutoff,expires_at,
            policy_digest,publish,state,project_id,committed_at)
            VALUES (%s,%s,'am',%s,%s,%s,%s,%s,%s,%s,true,'committed',%s,%s)""",
                     (edition.id, day, CHANNEL, OWNER, Jsonb(edition.model_dump(mode="json")), edition.due_at,
                      edition.cutoff, edition.expires_at, BriefStore(company).policy(), project["id"], edition.due_at))
        # The posted edition is a briefing message of the thread; here it is never published.
        company._message(conn, project, None, BRIEFER, "briefing", text, publish=False)
    return project


class FaultLog:
    """The provider as TurnExecutor sees it, keeping the code and message of every fault it raises (the runner's own
    fixed text, never model output or credentials), since the executor records only the code."""

    def __init__(self, provider, faults: list):
        self.provider, self.faults = provider, faults

    async def run(self, request):
        try:
            return await self.provider.run(request)
        except ProviderFault as exc:
            self.faults.append({"request_id": request.request_id, "code": exc.code, "message": exc.message[:300]})
            raise

    async def cancel(self, request_id):
        return await self.provider.cancel(request_id)


class SignalsClock:
    """briefing.schedule.utcnow for this run: the slot's snapshot time plus the real time elapsed since the start."""

    def __init__(self, start: datetime):
        self.start, self.real_start = start, datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.start + (datetime.now(UTC) - self.real_start)


def tool_names(response) -> list[str]:
    return [tool["name"] for tool in ((response or {}).get("decision") or {}).get("tools", [])]


async def run_signal(company: Company, executor: TurnExecutor, task_id: str) -> list[dict]:
    """Execute the signal task's queued turns one at a time until it settles, is re-asked to exhaustion or stops."""
    turns = []
    for _ in range(MAX_TURNS):
        with company.db.transaction() as conn:
            queued = conn.execute("""SELECT id,sequence FROM turns WHERE task_id=%s AND status='queued'
                ORDER BY sequence LIMIT 1""", (task_id,)).fetchone()
        if not queued:
            break
        started = time.monotonic()
        result = await executor.execute(str(queued["id"]))
        latency = round(time.monotonic() - started, 1)
        with company.db.transaction() as conn:
            stored = conn.execute("SELECT status,error,response FROM turns WHERE id=%s", (queued["id"],)).fetchone()
        turns.append({"sequence": queued["sequence"], "state": result["state"],
                      "task_status": result.get("task_status"), "reason": result.get("reason"),
                      "turn_status": stored["status"], "turn_error": stored["error"],
                      "tools": tool_names(stored["response"]), "latency_seconds": latency})
        print(json.dumps(turns[-1], ensure_ascii=False), flush=True)
        if result["state"] != "completed" or result.get("task_status") == "completed":
            break
    return turns


def outcome(slot: dict, turns: list[dict]) -> str:
    if slot["state"] in {"committed", "committed_late"}:
        return "committed"
    if slot["unparsed"]:
        return "unparsed"
    last = turns[-1]["state"] if turns else None
    return {"defer": "deferred", "blocked": "blocked", "stale": "stale"}.get(last, "incomplete")


def evidence(company: Company, identity: str) -> dict:
    with company.db.transaction() as conn:
        slot = conn.execute("""SELECT state,timing,proposal,committed_at,format_failures,unparsed,last_error,task_id,
            snapshot_source_id,snapshot_at,tools_until,send_at,entry_at,exit_at FROM trading_signals WHERE id=%s""",
                            (identity,)).fetchone()
        posts = conn.execute("""SELECT m.text,o.status AS outbox_status FROM messages m JOIN outbox o ON o.id=m.id
            WHERE m.kind='trading_signal' ORDER BY m.created_at""").fetchall()
        notes = [row["text"] for row in conn.execute("""SELECT text FROM messages WHERE kind='signal_note'
            ORDER BY created_at""")]
        web = conn.execute("""SELECT operation,arguments,(receipt->>'ok')::boolean AS ok FROM web_requests
            ORDER BY created_at""").fetchall()
        answers = [row["text"] for row in conn.execute("""SELECT text FROM messages WHERE kind='answer'
            AND task_id=%s ORDER BY created_at""", (slot["task_id"],))]
        task = conn.execute("SELECT status,error,turn_count FROM tasks WHERE id=%s", (slot["task_id"],)).fetchone()
    return {"slot": slot, "posts": posts, "signal_notes": notes, "web_requests": web, "answers": answers,
            "task": task}


def git(*args) -> str | None:
    try:
        return subprocess.check_output(["git", *args], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


async def qualify(*, admin: str | None, kind: str, edition: str, output: Path, live: bool, snapshot: dict | None = None,
                  fetch_snapshot: bool = False, day: date | None = None, provider=None, jobs_dir: Path | None = None,
                  codex_bin: str = "codex", timeout_seconds: int = 960, synthetic_edition: bool = False,
                  claude_bin: str | None = None) -> dict:
    """Run one signal on the real path and write the receipt to output. Refused (before anything is created) without
    live, without a local cluster or without exactly one snapshot of the kind. provider replaces the CodexRunner in
    tests; the receipt then says no real model was called. claude_bin runs the signal model of the settings
    (ANALYST_SIGNALS_MODEL, Fable) on ClaudeRunner with that CLI, as production does for signal turns."""
    if not live:
        raise Refused("qualification_requires_live")
    admin = local_admin(admin)
    if (snapshot is None) == (not fetch_snapshot):
        raise Refused("qualification_requires_one_snapshot")
    if snapshot is not None and snapshot.get("kind") != kind:
        raise Refused("qualification_snapshot_kind_mismatch")
    real_now = schedule.utcnow
    database = "company_signal_qualification_" + uuid4().hex
    connection = conninfo_to_dict(admin)
    connection["dbname"] = database
    status, faults = git("status", "--porcelain", "--", "src", "scripts/qualify_signals.py"), []
    receipt = {"started_at": datetime.now(UTC).isoformat(), "state": "error", "kind": kind,
               "real_model": provider is None, "slack_sender_constructed": False,
               "database": {"kind": "disposable database on a local PostgreSQL cluster", "name": database,
                            "dropped": False},
               "code_commit": git("rev-parse", "HEAD"), "uncommitted_code": None if status is None else bool(status),
               "edition": {"sha256": hashlib.sha256(edition.encode()).hexdigest(), "characters": len(edition),
                           "synthetic": synthetic_edition},
               "turns": [], "completed_model_turns": 0, "provider_faults": faults, "proposal": None, "posts": []}
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(database)))
    try:
        company = Company(settings_for(make_conninfo(**connection)))
        company.db.migrate()
        role = company.role(BRIEFER)
        if claude_bin:
            # Model assignments are off in this company, so the signal task's own selection is not read: the Analyst
            # role carries the signal model instead, which is what production's selection gives a signal turn.
            role = role.model_copy(update={"model": company.settings.analyst_signals_model,
                                           "reasoning_effort": company.settings.analyst_signals_effort})
            company.roles[BRIEFER] = role
        receipt.update(model=role.model, reasoning_effort=role.reasoning_effort,
                       provider="claude" if claude_bin else "codex")
        slot = pick_slot(kind, day, real_now(), schedule.overrides(company.settings))
        clock = SignalsClock(slot.snapshot_at)
        if fetch_snapshot:
            snapshot = await asyncio.to_thread(read_snapshot, kind, clock=real_now)
        receipt.update(session_day=str(slot.day), clock={
            "simulated_start": slot.snapshot_at.isoformat(), "real_start": clock.real_start.isoformat(),
            **{name: getattr(slot, name).isoformat() for name in ("tools_until", "send_at", "entry_at", "exit_at")}},
            snapshot={"origin": "live read (--fetch-snapshot)" if fetch_snapshot else "file",
                      "observed_at": snapshot["observed_at"], "quotes": sorted(snapshot["quotes"]),
                      "errors": snapshot["errors"], "receipts": snapshot["receipts"], "text": snapshot_text(snapshot)})
        edition_project(company, slot.day, edition)
        schedule.utcnow = clock
        store, identity = SignalStore(company), slot_id(slot.day, kind)
        at = clock()
        store.schedule(at)
        due = [row for row in store.due_asks(at) if str(row["id"]) == identity]
        if not due or not store.ask(due[0], snapshot, at):
            raise ValueError("qualification_slot_not_asked")
        with company.db.transaction() as conn:
            task_id = str(conn.execute("SELECT task_id FROM trading_signals WHERE id=%s", (identity,)
                                       ).fetchone()["task_id"])
        if provider is None and claude_bin:
            provider = local_claude_runner(claude_bin, jobs_dir, timeout_seconds)
            receipt["real_model"] = True
        if provider is None:
            from quant_company.providers.codex_runner import CodexRunner, RunnerConfig

            provider = CodexRunner(RunnerConfig(
                codex_home=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))),
                jobs_dir=jobs_dir or Path(tempfile.mkdtemp(prefix="signal-qualification-codex-")),
                codex_bin=codex_bin, timeout_seconds=timeout_seconds))
        with local_claude_login() if claude_bin else nullcontext():
            receipt["turns"] = await run_signal(company, TurnExecutor(company, FaultLog(provider, faults)), task_id)
        receipt["completed_model_turns"] = sum(turn["state"] == "completed" for turn in receipt["turns"])
        found = evidence(company, identity)
        slot_row = found["slot"]
        receipt.update(state=outcome(slot_row, receipt["turns"]), proposal=slot_row["proposal"],
                       timing=slot_row["timing"], committed_at=slot_row["committed_at"],
                       format_failures=slot_row["format_failures"], unparsed=slot_row["unparsed"],
                       last_error=slot_row["last_error"], task=found["task"], posts=found["posts"],
                       signal_notes=found["signal_notes"], web_requests=found["web_requests"],
                       answers=found["answers"])
    except Exception as exc:
        receipt["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        raise
    finally:
        schedule.utcnow = real_now
        try:
            with psycopg.connect(admin, autocommit=True) as conn:
                conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))
            receipt["database"]["dropped"] = True
        finally:
            receipt["completed_at"] = datetime.now(UTC).isoformat()
            receipt["notes"] = notes(receipt)
            output.parent.mkdir(parents=True, exist_ok=True)
            temporary = output.with_name(output.name + ".tmp")
            temporary.write_text(json.dumps(as_json(receipt), ensure_ascii=False, indent=2) + "\n")
            temporary.replace(output)
    return as_json(receipt)


def local_claude_runner(claude_bin: str, jobs_dir: Path | None, timeout_seconds: int):
    """ClaudeRunner as the Claude runtime builds it, with claude_bin as the Fable CLI. Run it inside
    local_claude_login()."""
    from quant_company.providers.claude_runner import ClaudeConfig, ClaudeRunner

    return ClaudeRunner(ClaudeConfig(
        auth_dir=Path.home() / ".claude", jobs_dir=jobs_dir or Path(tempfile.mkdtemp(prefix="signal-qualification-claude-")),
        fable_binary=claude_bin, timeout_seconds=min(timeout_seconds, 600), usage_credits_disabled_confirmed=True))


@contextmanager
def local_claude_login():
    """While it is open, the CLI keeps its default login: the runtime points it at its isolated login
    (CLAUDE_CONFIG_DIR), and on macOS the default login is found in the keychain by USER. The rest of the environment,
    the exact-version and subscription preflight and the command are production's."""
    from quant_company.providers import claude_runner

    original = claude_runner.environment

    def environment(config, source):
        result = original(config, source)
        result.pop("CLAUDE_CONFIG_DIR", None)
        if source.get("USER"):
            result["USER"] = source["USER"]
        return result

    claude_runner.environment = environment
    try:
        yield
    finally:
        claude_runner.environment = original


def notes(receipt: dict) -> list[str]:
    completed, faults = receipt["completed_model_turns"], receipt["provider_faults"]
    stop = f": the provider raised {faults[0]['code']} ({faults[0]['message']})" if faults else ""
    if not receipt["real_model"]:
        provider = "Injected provider: no real model was called."
    elif receipt.get("provider") == "claude":
        provider = ("Provider: ClaudeRunner with the signal model's CLI on the local Claude subscription login (the CLI's "
                    "default login, not the runtime's isolated CLAUDE_CONFIG_DIR), no API credentials; its job receipts "
                    "were kept outside the repository.")
    else:
        provider = ("Provider: CodexRunner on the local ChatGPT login (CODEX_HOME), no API credentials; its job receipts "
                    "were kept outside the repository.")
    return [
        provider,
        f"{completed} model turn(s) completed." if completed else f"No model turn completed{stop}.",
        "A disposable database on a local PostgreSQL cluster was created for this run and "
        + ("dropped afterwards" if receipt["database"]["dropped"] else "NOT dropped (see error)")
        + "; production, its database and its host were not touched.",
        "The Slack sender (SlackOutbox) was never constructed: the posts listed exist only as undispatched outbox rows "
        "of the disposable database.",
        "The signals clock started at the slot's snapshot time and advanced in real time, so the tool cutoff and the "
        "on-time/late timing reflect the real latency. Grading, the interim post and the scorecard were not run.",
        "The snapshot was read live from Yahoo at the time shown." if receipt.get("snapshot", {}).get("origin", "")
        .startswith("live") else "The snapshot was loaded from a file.",
        "The edition text is synthetic test text, not a published edition." if receipt["edition"]["synthetic"] else
        "The edition text was supplied by the operator.",
        "The proposal is a model output for a contract and latency check, not a trading recommendation.",
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="Required: allow real model calls")
    parser.add_argument("--kind", required=True, choices=["intraday", "overnight"])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--snapshot", type=Path, help="JSON written by signals.snapshot.read_snapshot")
    source.add_argument("--fetch-snapshot", action="store_true", help="Read the snapshot live from Yahoo now")
    parser.add_argument("--edition", required=True, type=Path, help="Edition text for the thread")
    parser.add_argument("--synthetic-edition", action="store_true", help="Record the edition text as synthetic")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--day", type=date.fromisoformat, help="Session day (default: the next signal of the kind)")
    parser.add_argument("--database-url", help="Admin URL of a local cluster (default: TEST_DATABASE_URL)")
    parser.add_argument("--jobs-dir", type=Path, help="Codex job receipts (default: a new temporary directory)")
    parser.add_argument("--codex-bin", default="codex", help="The validated Codex CLI (providers/codex_release.json)")
    parser.add_argument("--claude-bin", help="Run the signal model (ANALYST_SIGNALS_MODEL) on this Claude CLI "
                        "(claude_runner.FABLE_CLI_VERSION for Fable) instead of Codex")
    parser.add_argument("--timeout-seconds", type=int, default=960)
    args = parser.parse_args(argv)
    if not args.live:
        print(json.dumps({"state": "refused", "error": "qualification_requires_live"}))
        raise SystemExit(2)
    snapshot = json.loads(args.snapshot.read_text()) if args.snapshot else None
    try:
        receipt = asyncio.run(qualify(
            admin=args.database_url, kind=args.kind, edition=args.edition.read_text(), output=args.output.resolve(),
            live=True, snapshot=snapshot, fetch_snapshot=args.fetch_snapshot, day=args.day, jobs_dir=args.jobs_dir,
            codex_bin=args.codex_bin, timeout_seconds=args.timeout_seconds, synthetic_edition=args.synthetic_edition,
            claude_bin=args.claude_bin))
    except Refused as exc:
        print(json.dumps({"state": "refused", "error": str(exc)}))
        raise SystemExit(2) from None
    print(json.dumps({key: receipt.get(key) for key in ("state", "timing", "real_model")}))
    raise SystemExit(0 if receipt["state"] == "committed" else 1)


if __name__ == "__main__":
    sys.exit(main())

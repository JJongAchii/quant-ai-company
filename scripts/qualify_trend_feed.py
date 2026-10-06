"""Opt-in real RSS/original/Codex qualification; local disposable DB, no Slack or NAVER credentials.

Advances only the feature clock to the next morning to exercise its frozen daily slot.
This is a transport/editorial check, not three elapsed days or an operational delivery.
"""

import argparse
import asyncio
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from quant_company.company import Company, as_json
from quant_company.config import Settings
from quant_company.providers.codex_runner import CodexRunner, RunnerConfig
from quant_company.trend_feed import schedule
from quant_company.trend_feed.runner import TrendFeedCollector, TrendFeedEditor
from quant_company.trend_feed.sources import fetch_google
from quant_company.trend_feed.store import TrendFeedStore


async def qualify(output, codex_bin="codex"):
    admin = os.environ.get("TEST_DATABASE_URL") or json.loads(Path(".local/test-env.json").read_text())["database_url"]
    config = conninfo_to_dict(admin)
    if config.get("host") not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Requires a disposable local PostgreSQL cluster")
    name = "company_trend_qualification_" + uuid4().hex
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
    config["dbname"] = name
    runtime, real_clock = None, schedule.utcnow
    report = {"checked_at": datetime.now(UTC).isoformat(), "state": "failed",
              "database": "real-disposable-postgresql", "network": "real-public-rss-and-permitted-originals",
              "model": "real-codex-subscription", "naver": "not-connected", "slack": "not-connected",
              "deployed": False, "clock": "synthetic next-morning cutoff; source timestamps unchanged",
              "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
              "uncommitted_code": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True)),
              "qualification_scope": "One frozen daily briefing, up to two calls, preview only; not 3-day observation"}
    try:
        company = Company(Settings(database_url=make_conninfo(**config), trend_feed_enabled=True,
            trend_feed_publish_enabled=False, trend_feed_channel_id="CQUALIFICATION",
            trend_feed_owner_user="UQUALIFICATION", slack_allowed_users=["UQUALIFICATION"],
            slack_allowed_channels=["CQUALIFICATION"], trend_feed_naver_enabled=False,
            company_max_daily_turns=2))
        company.db.migrate()
        store = TrendFeedStore(company)
        receipt = await asyncio.to_thread(fetch_google)
        report["rss"] = {k: v for k, v in receipt.items() if k not in {"raw_xml", "entries"}}
        report["rss"]["entries"] = len(receipt.get("entries", []))
        if not receipt.get("ok") or not receipt.get("entries"):
            raise ValueError("live_rss_unavailable")
        at = real_clock()
        with company.db.transaction() as conn:
            conn.execute("UPDATE trend_feed_source SET next_at=%s", (at,))
        store.save_snapshot(store.claim_source(), receipt)
        cutoff, _, _ = schedule.times(real_clock())
        if cutoff <= real_clock():
            cutoff += timedelta(days=1)
        schedule.utcnow = lambda: cutoff
        digest = store.claim_enrichment()
        await TrendFeedCollector(company).enrich(digest)
        bundle = store.freeze()["bundle"]
        report["input"] = [{"id": c["id"], "title": c["title"], "traffic": c["traffic"],
            "articles": [{k: v for k, v in a.items() if k != "content"} | {"chars": len(a["content"])}
                         for a in c["articles"]]} for c in bundle["candidates"]]
        role = company.roles["reporter"]
        report["configured_model"] = {"model": role.model, "reasoning_effort": role.reasoning_effort}
        runtime = CodexRunner(RunnerConfig(
            codex_home=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))),
            jobs_dir=Path(".local") / name, codex_bin=codex_bin, timeout_seconds=360))
        editor = TrendFeedEditor(company, runtime)
        await editor.tick()
        with company.db.transaction() as conn:
            invalid = conn.execute("SELECT 1 FROM trend_feed_calls WHERE state='invalid'").fetchone()
        if invalid:
            await editor.tick()
        schedule.utcnow = lambda: cutoff + timedelta(minutes=30)
        report["finalize"] = store.finalize()
        report["preview"] = store.preview()["text"]
        with company.db.transaction() as conn:
            calls = conn.execute("SELECT id,stage,state,error,response FROM trend_feed_calls ORDER BY stage").fetchall()
            report["calls"] = calls
            report["outbox_count"] = conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"]
        report["state"] = ("passed" if any(c["state"] == "completed" for c in calls)
                           and 1 <= len(calls) <= 2 and report["outbox_count"] == 0 else "failed")
    except Exception as exc:
        # Do not persist arbitrary provider exception text or environment values.
        report["error_type"] = type(exc).__name__
    finally:
        schedule.utcnow = real_clock
        if runtime:
            await runtime.close()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(as_json(report), ensure_ascii=False, indent=2) + "\n")
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
    return report["state"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--output", type=Path, default=Path(".local/trend-feed-live.json"))
    parser.add_argument("--codex-bin", default="codex", help="Path to the service's pinned CLI; never replaces global Codex")
    args = parser.parse_args()
    result = asyncio.run(qualify(args.output, args.codex_bin))
    print(result)
    raise SystemExit(0 if result == "passed" else 1)

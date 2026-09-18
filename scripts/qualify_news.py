"""Opt-in one-call Reporter qualification with real RSS/original/Codex and disposable PostgreSQL.

No Slack credentials, posting or production connections. A reviewed hold/ignore is a valid
editorial outcome; it is not evidence that coverage or journalistic accuracy is fully qualified.
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
from quant_company.news.contracts import load_sources
from quant_company.news.feeds import fetch_feed
from quant_company.news.originals import fetch_original
from quant_company.news.runner import NewsEditor
from quant_company.news.store import NewsStore
from quant_company.providers.codex_runner import CodexRunner, RunnerConfig


async def qualify(output):
    admin = os.environ.get("TEST_DATABASE_URL") or json.loads(Path(".local/test-env.json").read_text())["database_url"]
    config = conninfo_to_dict(admin)
    if config.get("host") not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Requires a disposable local PostgreSQL cluster")
    name = "company_news_qualification_" + uuid4().hex
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
    config["dbname"] = name
    runtime = None
    report = {"checked_at": datetime.now(UTC).isoformat(), "state": "failed", "database": "real-disposable-postgresql",
              "network": "real-public-rss-and-original", "model": "real-codex-subscription",
              "slack": "not-connected", "deployed": False, "source": "fed-press",
              "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
              "uncommitted_code": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True)),
              "qualification_scope": "One current-feed original, one frozen model call, validated editorial proposal; no live publication."}
    try:
        company = Company(Settings(database_url=make_conninfo(**config), company_news_enabled=True,
                                   news_publish_enabled=False, news_channel_id="CQUALIFICATION",
                                   news_owner_user="UQUALIFICATION", slack_allowed_users=["UQUALIFICATION"],
                                   slack_allowed_channels=["CQUALIFICATION"], company_max_daily_turns=1,
                                   news_max_age_hours=72))
        company.db.migrate()
        store = NewsStore(company)
        store.sync_sources()
        source = next(s for s in load_sources() if s.id == "fed-press")
        receipt = await asyncio.to_thread(fetch_feed, source)
        if not receipt["ok"] or not receipt["entries"]:
            raise ValueError("Live RSS unavailable")
        entry = max((e for e in receipt["entries"] if e["published_at"]), key=lambda e: e["published_at"])
        # Select from the actual feed. Preserve the real publication time; the qualification has
        # a declared 72-hour freshness window and seeds a normal resumed collector, not a new alert.
        with company.db.transaction() as conn:
            conn.execute("UPDATE news_sources SET next_at=now()+interval '1 day'")
            conn.execute("UPDATE news_sources SET next_at=now(),last_success=%s WHERE id='fed-press'",
                         (datetime.now(UTC)-timedelta(minutes=10),))
        claimed = store.claim_source()
        store.save_feed(claimed, {**receipt, "entries": [entry]})
        article = store.claim_article()
        if not article:
            raise ValueError("No eligible original in the declared 72-hour qualification window")
        original, _ = await asyncio.to_thread(fetch_original, article["url"])
        store.save_original(article, original)
        report["input"] = {"url": article["url"], "title": article["title"], "published_at": article["published_at"],
                           "rss_sha256": receipt["sha256"], "original_ok": original.get("ok"),
                           "original_sha256": original.get("original_sha256"), "original_bytes": original.get("original_bytes"),
                           "article_extraction": original.get("article_extraction"), "article_chars": original.get("article_chars"),
                           "excerpt_truncated": original.get("excerpt_truncated"),
                           "freshness_window_hours": 72, "resumed_collector_state": "synthetically seeded"}
        if not original.get("ok"):
            raise ValueError("Live original unavailable")
        runtime = CodexRunner(RunnerConfig(codex_home=Path(os.environ.get("CODEX_HOME", str(Path.home()/".codex"))),
                                          jobs_dir=Path(".local")/name, timeout_seconds=300))
        result = await NewsEditor(company, runtime).tick()
        report["result"] = result
        with company.db.transaction() as conn:
            calls = conn.execute("SELECT id,state,error,result,response FROM news_reviews").fetchall()
            report["calls"] = calls
            report["outbox_count"] = conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"]
        report["state"] = "passed" if result["state"] == "completed" and len(calls) == 1 and report["outbox_count"] == 0 else "failed"
    finally:
        if runtime:
            await runtime.close()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(as_json(report), ensure_ascii=False, indent=2)+"\n")
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
    return report["state"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--output", type=Path, default=Path(".local/news-live.json"))
    args = parser.parse_args()
    result = asyncio.run(qualify(args.output))
    print(result)
    raise SystemExit(0 if result == "passed" else 1)

"""Opt-in real subscription exercises on a disposable DB; no Slack or production writes.

Runs the actual configured models and specialist packs. Objective synthetic results are not
general expert qualifications; explanations and unseen-domain transfer remain unscored.
"""

import argparse
import asyncio
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import httpx
from psycopg.conninfo import conninfo_to_dict

from quant_company.company import Company, as_json
from quant_company.config import Settings
from quant_company.providers.client import RuntimeClient
from quant_company.providers.codex_runner import CodexRunner, RunnerConfig
from quant_company.providers.codex_runtime import create_app
from quant_company.staff.packs import STAFF
from quant_company.staff.runner import StaffRunner
from quant_company.staff.store import StaffStore, status


async def qualify(args):
    if os.environ.get("REAL_STAFF_CODEX") != "1":
        raise SystemExit("Opt in with REAL_STAFF_CODEX=1")
    url = os.environ["TEST_DATABASE_URL"]
    if not conninfo_to_dict(url).get("dbname", "").startswith("staff_qualification_"):
        raise SystemExit("Requires dedicated disposable staff_qualification_* database")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    settings = Settings(database_url=url, model_provider="codex", company_code_commit=commit,
                        slack_allowed_users=["staff-qualification"], company_staff_development_enabled=False)
    company = Company(settings)
    company.db.migrate()
    store = StaffStore(company)
    selected = args.employees.split(",") if args.employees else list(STAFF)
    ids = [store.enqueue(r, "staff-qualification", "baseline") for r in selected]
    runtime = CodexRunner(RunnerConfig(codex_home=Path(os.environ["CODEX_HOME"]),
                                      jobs_dir=args.output.parent / "staff-codex-jobs", timeout_seconds=300))
    token = "local-staff-qualification-not-production"
    client = RuntimeClient("http://staff.test", token, timeout_seconds=360,
                           transport=httpx.ASGITransport(app=create_app(runner=runtime, token=token)))
    runner = StaffRunner(company, client)
    outcomes = []
    try:
        for _ in range(len(ids)*4):
            result = await runner.tick(manual=True)
            outcomes.append(result)
            print(json.dumps(result), flush=True)
            if result["state"] in {"idle", "defer"}:
                break
    finally:
        await runtime.close()
        with company.db.transaction() as conn:
            report = status(conn, company, "staff-qualification")
            runs = conn.execute("""SELECT id::text,employee,purpose,state,model,code_commit,pack_snapshot,
                suite_version,public_case,case_digest,grade,final_answer,error,created_at,completed_at
                FROM staff_runs ORDER BY created_at""").fetchall()
            calls = conn.execute("""SELECT id,run_id::text,sequence,request,response,tools,completed_at
                FROM staff_calls ORDER BY created_at""").fetchall()
            side_effects = {t: conn.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"]
                            for t in ("projects", "tasks", "outbox", "memories")}
        evidence = as_json({"checked_at": datetime.now(UTC), "code_commit": commit, "provider": "real-codex-subscription",
                           "database": "real-disposable-postgresql", "temporal": "separately_integration_tested",
                           "slack": "not_used", "production": "not_deployed_by_this_script",
                           "general_expertise_qualified": False, "report": report,
                           "runs": runs, "calls": calls, "company_side_effect_counts": side_effects})
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+"\n")
        print(json.dumps({"evidence": str(args.output), "run_count": len(runs), "calls": len(calls),
                          "outcomes": [{"employee": r["employee"], "state": r["state"],
                                        "objective_passed": (r["grade"] or {}).get("objective_passed")}
                                       for r in runs]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(".local/staff-live.json"))
    parser.add_argument("--employees")
    asyncio.run(qualify(parser.parse_args()))

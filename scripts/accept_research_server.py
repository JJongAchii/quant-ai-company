"""Isolated server/3070 engineering acceptance; never attaches to a production company DB.

Transport uses a synthetic Slack owner. Actual backtests run only on the registered
3070, with the existing approved fixed P11 recipe. No Slack messages or model calls
are sent by this harness. PostgreSQL and Temporal remain real persistent services.
"""

import argparse
import asyncio
import json
import platform
from pathlib import Path

import uvicorn
from psycopg.conninfo import conninfo_to_dict
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from quant_company.api import create_app
from quant_company.company import Company
from quant_company.config import Settings
from quant_company.contracts import AgentDecision, ProviderResponse
from quant_company.research.store import ResearchStore
from quant_company.research.workflow import ResearchWorkflow
from quant_company.runtime import connect, make_research_worker
from quant_company.slack import SlackIngress

OWNER = "UACCEPTANCE"
CHANNEL = "CACCEPTANCE"
TEAM = "TRESEARCHFIXTURE"
CREDENTIAL = {"app_id": "AFIXTURE", "bot_user_id": "UBOTFIXTURE", "signing_secret": "synthetic-fixture-only"}


def controlled_company():
    settings = Settings()
    database = conninfo_to_dict(settings.database_url).get("dbname", "")
    if (not database.startswith("company_research_acceptance_") or not settings.fixture_mode
            or settings.model_provider != "fixture" or not settings.company_research_enabled
            or settings.slack_allowed_users != [OWNER] or settings.slack_allowed_channels != [CHANNEL]
            or settings.slack_team_id != TEAM or settings.slack_credentials_file is not None
            or not settings.temporal_task_queue.startswith("research-acceptance-")):
        raise SystemExit("Isolated acceptance database, synthetic identity and dedicated task queue required")
    return Company(settings)


def seed(company):
    company.db.migrate()
    with company.db.transaction() as conn:
        existing = conn.execute("SELECT id,state,manifest_digest FROM research_jobs LIMIT 1").fetchone()
    if existing:
        if existing["state"] == "pending_approval":
            approve_fixture(company, existing)
        return snapshot(company)
    result = company.ingest(event_key="fixture:research-acceptance", owner=OWNER, channel=CHANNEL, thread_ts="1.0",
                             text="합성 Slack 제어 입력으로 승인된 P11 고정 재현의 서버·3070 경로를 검증합니다.")
    with company.db.transaction() as conn:
        turn = conn.execute("SELECT id FROM turns WHERE task_id=%s", (result["task_id"],)).fetchone()
    identity = str(turn["id"])
    company.prepare_turn(identity)
    company.commit_turn(identity, ProviderResponse(request_id=identity, provider="synthetic-control", decision=AgentDecision(
        say="고정 명세 준비", status="continue", tools=[{"name": "research_control", "arguments": {
            "action": "request", "recipe_id": "kr-etf-p11-replay-v1"}}])))
    with company.db.transaction() as conn:
        row = conn.execute("SELECT id,manifest_digest FROM research_jobs WHERE project_id=%s",
                           (result["project_id"],)).fetchone()
    approve_fixture(company, row)
    return snapshot(company)


def approve_fixture(company, row):
    SlackIngress(company.settings, company, {"director": CREDENTIAL}).accept("director", {
        "team_id": TEAM, "api_app_id": CREDENTIAL["app_id"], "event": {
            "type": "message", "user": OWNER, "channel": CHANNEL, "thread_ts": "1.0", "ts": "2.0",
            "text": f"연구 승인 {row['id']} {row['manifest_digest'][:12]}"}}, CREDENTIAL)


def snapshot(company):
    with company.db.transaction() as conn:
        row = conn.execute("SELECT * FROM research_jobs ORDER BY created_at LIMIT 1").fetchone()
        if not row:
            return {"state": "not_seeded"}
        status = ResearchStore(company).status(conn, str(row["project_id"]))
        counts = {table: conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
                  for table in ("research_jobs", "sources", "artifacts", "outbox")}
    return {"acceptance_kind": "real_server_worker_with_synthetic_slack_ingress", "production_deployed": False,
            "real_slack_ingress": False, "real_model_used": False, "physical_mac_shutdown_test": False,
            "server_hostname": platform.node(), "company_commit": company.settings.company_code_commit,
            "job_id": str(row["id"]), "project_id": str(row["project_id"]), "state": row["state"],
            "manifest_digest": row["manifest_digest"], "artifact_sha256": row["artifact_sha256"],
            "html_sha256": (row["report"] or {}).get("html_sha256"),
            "source_id": (row["report"] or {}).get("source_id"), "records": counts,
            "status": status}


async def serve(company):
    company.db.migrate()
    client = await connect(company.settings)
    identity = company.settings.temporal_task_queue
    try:
        await client.start_workflow(ResearchWorkflow.run, id=identity,
                                    task_queue=identity + "-research",
                                    id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE)
    except WorkflowAlreadyStartedError:
        pass
    app = create_app(company.settings, company, {"director": CREDENTIAL})
    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=8000, access_log=False))
    async with make_research_worker(client, company):
        await server.serve()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["seed", "serve", "snapshot"])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    company = controlled_company()
    if args.action == "serve":
        asyncio.run(serve(company))
        return
    data = seed(company) if args.action == "seed" else snapshot(company)
    rendered = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered)


if __name__ == "__main__":
    main()

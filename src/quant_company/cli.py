import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from .company import Company
from .config import Settings


def manifests(company, base_url, output, transport="socket"):
    output.mkdir(parents=True, exist_ok=True)
    for role in company.roles.values():
        if not role.active:
            continue
        value = {
            "display_information": {"name": "Quant " + role.name, "description": role.mission[:140]},
            "features": {"bot_user": {"display_name": "quant-" + role.id, "always_online": True},
                         "app_home": {"home_tab_enabled": False, "messages_tab_enabled": True,
                                      "messages_tab_read_only_enabled": False}},
            "oauth_config": {"scopes": {"bot": ["app_mentions:read", "chat:write", "channels:history",
                                                    "groups:history", "im:history", "im:write"]}},
            "settings": {"event_subscriptions": {"bot_events": ["app_mention", "message.channels", "message.groups", "message.im"]},
                         "interactivity": {"is_enabled": False}, "org_deploy_enabled": False,
                         "socket_mode_enabled": transport == "socket", "token_rotation_enabled": False},
        }
        if transport == "http":
            value["settings"]["event_subscriptions"]["request_url"] = base_url.rstrip("/") + "/slack/events/" + role.id
        (output / f"{role.id}.json").write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


async def demo(settings):
    from .execution import FixtureProvider, TurnExecutor

    if not settings.fixture_mode or settings.model_provider != "fixture":
        raise SystemExit("Demo requires FIXTURE_MODE=true MODEL_PROVIDER=fixture and a disposable database")
    company = Company(settings)
    company.db.migrate()
    request = company.ingest(event_key="demo:" + str(uuid4()), text="합성 예제로 네 직원의 협업 경로를 확인하세요.",
                             owner="demo")
    executor = TurnExecutor(company, FixtureProvider())
    for _ in range(40):
        with company.db.transaction() as conn:
            rows = conn.execute("""SELECT t.id FROM turns t JOIN tasks k ON k.id=t.task_id
                WHERE k.project_id=%s AND t.status='queued' ORDER BY t.created_at""",
                                (request["project_id"],)).fetchall()
        if not rows:
            break
        for row in rows:
            await executor.execute(str(row["id"]))
    state = company.project_state(request["project_id"])
    print(json.dumps({"mode": "fixture-direct-executor", "temporal_used": False, "slack_connected": False,
                      "real_model_used": False, **state}, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(prog="quant-company")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    sub.add_parser("worker")
    sub.add_parser("dispatch")
    sub.add_parser("slack-socket")
    sub.add_parser("demo")
    slack = sub.add_parser("slack-manifests")
    slack.add_argument("--transport", choices=["socket", "http"], default="socket")
    slack.add_argument("--base-url")
    slack.add_argument("--output", type=Path, default=Path(".local/slack-manifests"))
    args = parser.parse_args()
    settings = Settings()
    if args.command == "migrate":
        Company(settings).db.migrate()
    elif args.command == "serve":
        import uvicorn

        uvicorn.run("quant_company.api:create_app", factory=True, host=args.host, port=args.port)
    elif args.command == "worker":
        from .runtime import worker_main

        asyncio.run(worker_main(settings))
    elif args.command == "dispatch":
        from .runtime import dispatch_main

        asyncio.run(dispatch_main(settings))
    elif args.command == "slack-manifests":
        if args.transport == "http" and not (args.base_url or "").startswith("https://"):
            parser.error("Slack public callback URL must use HTTPS")
        manifests(Company(settings), args.base_url, args.output, args.transport)
    elif args.command == "slack-socket":
        from .socket_mode import socket_main

        asyncio.run(socket_main(settings))
    elif args.command == "demo":
        asyncio.run(demo(settings))

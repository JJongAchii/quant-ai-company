import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from .company import Company
from .config import Settings
from .tech_feed.contracts import TECH_FEED_AGENT


def manifests(company, base_url, output, transport="socket", include_reporter=False, include_tech_scout=False,
              include_market_brief=False, include_trend_scout=False):
    output.mkdir(parents=True, exist_ok=True)
    for role in company.roles.values():
        optional = ((include_reporter and role.id == "reporter")
                    or (include_tech_scout and role.id == TECH_FEED_AGENT)
                    or (include_market_brief and role.id == "market_brief")
                    or (include_trend_scout and role.id == "trend_scout"))
        if not role.active and not optional:
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
        if role.id == "director":
            value["settings"]["interactivity"] = {"is_enabled": True}
            if transport == "http":
                value["settings"]["interactivity"]["request_url"] = base_url.rstrip("/") + "/slack/events/director"
        if role.id == "reporter":
            value["display_information"]["name"] = "Reporter"
            value["features"]["bot_user"]["display_name"] = "reporter"
            value["settings"]["event_subscriptions"]["bot_events"].append("entity_details_requested")
        if role.id == "market_brief":
            value["display_information"]["name"] = "Analyst"
            value["features"]["bot_user"]["display_name"] = "analyst"
        if role.id in {TECH_FEED_AGENT, "trend_scout"}:
            value = {
                "display_information": {"name": role.name, "description": role.mission[:140]},
                "features": {"bot_user": {"display_name": role.id.replace("_", "-"), "always_online": False}},
                "oauth_config": {"scopes": {"bot": ["chat:write"]}},
                "settings": {"org_deploy_enabled": False, "socket_mode_enabled": False,
                             "token_rotation_enabled": False},
            }
            if role.id == "trend_scout" and company.settings.trend_feed_on_demand_enabled:
                value["oauth_config"]["scopes"]["bot"] += ["app_mentions:read", "channels:history"]
                value["settings"]["socket_mode_enabled"] = transport == "socket"
                value["settings"]["event_subscriptions"] = {"bot_events": ["app_mention", "message.channels"]}
        if transport == "http":
            subscriptions = value["settings"].get("event_subscriptions")
            if subscriptions is not None:
                subscriptions["request_url"] = base_url.rstrip("/") + "/slack/events/" + role.id
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
    sub.add_parser("data-watch-worker")
    sub.add_parser("briefing-data-worker")
    sub.add_parser("housing-feed-worker")
    sub.add_parser("news-worker")
    sub.add_parser("quant-feed-worker")
    sub.add_parser("dispatch")
    sub.add_parser("slack-socket")
    news = sub.add_parser("news")
    news.add_argument("action", choices=["status", "collect", "review", "probe"])
    news.add_argument("--output", type=Path)
    briefing = sub.add_parser("briefing")
    briefing.add_argument("action", choices=["status", "preview", "collect", "data", "review", "probe", "data-probe", "qualify"])
    briefing.add_argument("--output", type=Path)
    tech_feed = sub.add_parser("tech-feed")
    tech_feed.add_argument("action", choices=["status", "collect", "probe"])
    tech_feed.add_argument("--output", type=Path)
    trend_feed = sub.add_parser("trend-feed")
    trend_feed.add_argument("action", choices=["status", "collect", "probe", "preview"])
    trend_feed.add_argument("--output", type=Path)
    housing_feed = sub.add_parser("housing-feed")
    housing_feed.add_argument("action", choices=["status", "collect", "probe"])
    housing_feed.add_argument("--output", type=Path)
    quant_feed = sub.add_parser("quant-feed")
    quant_feed.add_argument("action", choices=["status", "probe", "preview"])
    quant_feed.add_argument("--live", action="store_true")
    quant_feed.add_argument("--output", type=Path)
    sub.add_parser("demo")
    maintenance = sub.add_parser("maintenance")
    maintenance.add_argument("--config", type=Path, required=True)
    sub.add_parser("maintenance-status")
    staff = sub.add_parser("staff")
    staff.add_argument("action", choices=["status", "enqueue", "tick", "review"])
    staff.add_argument("--employee")
    staff.add_argument("--owner")
    staff.add_argument("--id")
    staff.add_argument("--disposition", choices=["confirmed", "disputed"])
    staff.add_argument("--note")
    release = sub.add_parser("maintenance-release")
    release.add_argument("action", choices=["next", "archive", "activity", "finish"])
    release.add_argument("--id")
    release.add_argument("--config", type=Path, default=Path("/etc/quant-company/maintenance.json"))
    slack = sub.add_parser("slack-manifests")
    slack.add_argument("--transport", choices=["socket", "http"], default="socket")
    slack.add_argument("--base-url")
    slack.add_argument("--output", type=Path, default=Path(".local/slack-manifests"))
    slack.add_argument("--include-reporter", action="store_true")
    slack.add_argument("--include-tech-scout", action="store_true")
    slack.add_argument("--include-market-brief", action="store_true")
    slack.add_argument("--include-trend-scout", action="store_true")
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
    elif args.command == "data-watch-worker":
        from .runtime import data_watch_worker_main

        asyncio.run(data_watch_worker_main(settings))
    elif args.command == "briefing-data-worker":
        from .runtime import brief_data_worker_main

        asyncio.run(brief_data_worker_main(settings))
    elif args.command == "housing-feed-worker":
        from .runtime import housing_feed_worker_main

        asyncio.run(housing_feed_worker_main(settings))
    elif args.command == "news-worker":
        from .runtime import news_worker_main

        asyncio.run(news_worker_main(settings))
    elif args.command == "quant-feed-worker":
        from .runtime import quant_feed_worker_main

        asyncio.run(quant_feed_worker_main(settings))
    elif args.command == "dispatch":
        from .runtime import dispatch_main

        asyncio.run(dispatch_main(settings))
    elif args.command == "slack-manifests":
        if args.transport == "http" and not (args.base_url or "").startswith("https://"):
            parser.error("Slack public callback URL must use HTTPS")
        manifests(Company(settings), args.base_url, args.output, args.transport,
                  args.include_reporter, args.include_tech_scout, args.include_market_brief, args.include_trend_scout)
    elif args.command in {"news", "tech-feed", "briefing", "quant-feed", "housing-feed", "trend-feed"}:
        if args.command == "news":
            from .news.commands import command
        elif args.command == "briefing":
            from .briefing.commands import command
        elif args.command == "tech-feed":
            from .tech_feed.commands import command
        elif args.command == "housing-feed":
            from .housing_feed.commands import command
        elif args.command == "trend-feed":
            from .trend_feed.commands import command
        else:
            from .quant_feed.commands import command

        result = asyncio.run(command(settings, args.action, **({"live": args.live} if args.command == "quant-feed" else {})))
        rendered = json.dumps(result, default=str, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered)
        else:
            print(rendered)
    elif args.command == "slack-socket":
        from .socket_mode import socket_main

        asyncio.run(socket_main(settings))
    elif args.command == "demo":
        asyncio.run(demo(settings))
    elif args.command == "maintenance":
        from .maintenance.policy import MaintenanceConfig
        from .maintenance.runner import run_maintenance

        config = MaintenanceConfig.model_validate_json(args.config.read_text())
        asyncio.run(run_maintenance(Company(settings), config))
    elif args.command == "maintenance-status":
        with Company(settings).db.transaction() as conn:
            exists = conn.execute("SELECT to_regclass('maintenance_jobs') AS name").fetchone()
            rows = [] if not exists["name"] else conn.execute("""SELECT id,kind,state,problem_key,
                payload->'finding'->>'category' AS category,
                payload->'finding'->>'title' AS title,
                payload->'finding'->'evaluation'->>'mode' AS evaluation_mode,
                payload->'evaluation' AS evaluation,
                receipt,error,created_at,updated_at FROM maintenance_jobs ORDER BY created_at DESC LIMIT 50""").fetchall()
        print(json.dumps(rows, default=str, ensure_ascii=False, indent=2))
    elif args.command == "staff":
        from .staff.runner import StaffRunner
        from .staff.store import StaffStore, status

        company = Company(settings)
        owner = args.owner or (settings.slack_allowed_users[0] if settings.slack_allowed_users else "")
        if args.action == "status":
            with company.db.transaction() as conn:
                result = status(conn, company, owner, args.employee)
        elif args.action == "enqueue":
            if not args.employee:
                parser.error("staff enqueue requires --employee")
            result = {"run_id": StaffStore(company).enqueue(args.employee, owner, identity=args.id)}
        elif args.action == "review":
            if not args.id or not args.disposition or not args.note:
                parser.error("staff review requires --id, --disposition and --note")
            StaffStore(company).review(args.id, args.disposition, args.note)
            result = {"ok": True}
        else:
            result = asyncio.run(StaffRunner(company).tick(manual=True))
        print(json.dumps(result, default=str, ensure_ascii=False, indent=2))
    elif args.command == "maintenance-release":
        from .maintenance.releases import command

        command(Company(settings), args.action, args.id, args.config)

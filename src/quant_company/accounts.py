"""Owner-selected ChatGPT profiles. Routing never changes a model request's identity."""

import asyncio
import math
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from psycopg import sql
from psycopg.types.json import Jsonb
from temporalio import activity
from temporalio.service import RPCError, RPCStatusCode

from .command_help import HELP_COMMANDS
from .command_help import HELP_TEXT as COMMAND_HELP
from .contracts import ProviderFault
from .providers.client import RuntimeClient
from .providers.codex_runner import request_digest

LABELS = {"primary": "기본", "backup": "예비"}
COMMANDS = {
    "모델 계정 상태": {"action": "status", "target": None},
    "모델 계정 기본으로 전환": {"action": "switch", "target": "primary"},
    "모델 계정 예비로 전환": {"action": "switch", "target": "backup"},
}
HELP_TEXT = ("계정 명령을 인식하지 못했습니다. 아래 문장을 그대로 보내 주세요. 계정은 변경되지 않았습니다.\n"
             + COMMAND_HELP)


def help_text(text):
    return COMMAND_HELP if text.strip().casefold() in HELP_COMMANDS else HELP_TEXT


def parse_command(text):
    # Exact commands only: questions, quotes and arbitrary account paths are not instructions.
    return COMMANDS.get(text.strip())


def enqueue(conn, company, project, task, event_key, command):
    if not company.settings.model_accounts_enabled:
        text = "모델 계정 전환 기능이 아직 활성화되지 않았습니다. 운영 설정을 확인해 주세요."
        conn.execute("UPDATE tasks SET status='completed',result=%s WHERE id=%s", (text, task["id"]))
        company._message(conn, project, task["id"], "director", "status", text)
        return
    conn.execute("""INSERT INTO model_account_commands(id,event_key,project_id,owner_user,action,target)
        VALUES (%s,%s,%s,%s,%s,%s)""", (task["id"], event_key, project["id"], project["owner_user"],
                                     command["action"], command["target"]))
    conn.execute("UPDATE tasks SET status='waiting' WHERE id=%s", (task["id"],))


class AccountProvider:
    def __init__(self, company, client):
        self.company = company
        self.client = client

    def select(self, request):
        with self.company.db.transaction() as conn:
            # All reservations and switches serialize at this short transaction boundary.
            policy = conn.execute("SELECT * FROM model_account_policy WHERE id=1 FOR UPDATE").fetchone()
            saved = conn.execute("SELECT * FROM model_account_calls WHERE request_id=%s FOR UPDATE",
                                 (request.request_id,)).fetchone()
            digest = request_digest(request)
            if saved and saved["input_digest"] != digest:
                raise ProviderFault("uncertain", "Request ID is bound to different input.")
            if saved and saved["state"] not in {"quota", "waiting"}:
                # Completed calls recover their receipt; in-flight/ambiguous calls keep their owner.
                return {"profile": saved["profile"], "revision": saved["revision"]}, 0
            account = {"profile": policy["profile"], "revision": policy["revision"]}
            state = conn.execute("SELECT * FROM model_accounts WHERE profile=%s", (policy["profile"],)).fetchone()
            seconds = max(0, math.ceil((state["paused_until"] - datetime.now(UTC)).total_seconds())) \
                if state["paused_until"] else 0
            conn.execute("""INSERT INTO model_account_calls(request_id,input_digest,profile,revision,state)
                VALUES (%s,%s,%s,%s,%s) ON CONFLICT(request_id) DO UPDATE SET
                profile=excluded.profile,revision=excluded.revision,state=excluded.state,updated_at=now()""",
                         (request.request_id, digest, account["profile"], account["revision"],
                          "waiting" if seconds else "dispatching"))
            return account, seconds

    def finish(self, request, account, fault=None):
        with self.company.db.transaction() as conn:
            policy = conn.execute("SELECT * FROM model_account_policy WHERE id=1 FOR UPDATE").fetchone()
            updated = conn.execute("""UPDATE model_account_calls SET state=%s,updated_at=now()
                WHERE request_id=%s AND profile=%s AND revision=%s AND state<>'completed' RETURNING request_id""",
                                   (fault.code if fault else "completed", request.request_id,
                                    account["profile"], account["revision"])).fetchone()
            switched = policy["profile"] != account["profile"] and policy["revision"] > account["revision"]
            if not updated:
                return switched
            if fault is None:
                conn.execute("""UPDATE model_accounts SET last_success_at=now(),
                    last_fault=CASE WHEN paused_until>now() THEN last_fault ELSE NULL END WHERE profile=%s""",
                             (account["profile"],))
            elif fault.code == "quota":
                until = datetime.now(UTC) + timedelta(seconds=max(5, min(fault.retry_after_seconds or 900, 604800)))
                state = conn.execute("""UPDATE model_accounts SET paused_until=GREATEST(paused_until,%s),
                    last_fault='quota' WHERE profile=%s RETURNING *""", (until, account["profile"])).fetchone()
                if (not switched and state["notified_revision"] != policy["revision"]
                        and policy["control_project_id"]):
                    project = self.company._project(conn, policy["control_project_id"])
                    if (project["owner_user"] == self.company.settings.model_accounts_owner_user
                            and project["channel"] == self.company.settings.model_accounts_channel_id):
                        self.company._message(conn, project, None, "director", "status",
                            f"{LABELS[account['profile']]} 계정에서 사용 한도 응답을 받아 모델 작업이 대기 중입니다. "
                            "`모델 계정 상태`로 확인한 뒤 전환 명령을 보내 주세요. 자동으로 계정을 바꾸지는 않습니다.")
                        conn.execute("UPDATE model_accounts SET notified_revision=%s WHERE profile=%s",
                                     (policy["revision"], account["profile"]))
            elif fault.code == "auth":
                conn.execute("UPDATE model_accounts SET last_fault='auth',authentication='needs_login' WHERE profile=%s",
                             (account["profile"],))
            return switched

    async def run(self, request):
        account, seconds = await asyncio.to_thread(self.select, request)
        if seconds:
            raise ProviderFault("quota", "The selected subscription is waiting before another attempt.", seconds)
        try:
            response = await self.client.run(request, account=account)
        except ProviderFault as fault:
            switched = await asyncio.to_thread(self.finish, request, account, fault)
            # A denial from an old in-flight call may arrive after the owner has switched.
            # Let the existing workflow retry its unchanged ID promptly in that case.
            if switched and fault.code == "quota":
                raise ProviderFault("quota", fault.message, 5) from None
            raise
        await asyncio.to_thread(self.finish, request, account)
        return response

    async def cancel(self, request_id):
        return await self.client.cancel(request_id)


def resume_quota_waits(conn, command_id, revision):
    rows = conn.execute("""UPDATE turns SET due_at=now(),updated_at=now()
        WHERE status='waiting' AND error='quota' RETURNING id""").fetchall()
    for row in rows:
        conn.execute("""INSERT INTO model_account_wakes(command_id,turn_id,revision) VALUES (%s,%s,%s)
            ON CONFLICT DO NOTHING""", (command_id, row["id"], revision))
    for table in ("news_triages", "news_reviews", "news_searches", "staff_runs", "quant_feed_calls"):
        if not conn.execute("SELECT to_regclass(%s) AS name", ("public." + table,)).fetchone()["name"]:
            continue
        conn.execute(sql.SQL("UPDATE {} SET next_at=now() WHERE error='quota' AND state IN ('running','queued')")
                     .format(sql.Identifier(table)))
    if conn.execute("SELECT to_regclass('public.maintenance_calls') AS name").fetchone()["name"]:
        conn.execute("UPDATE maintenance_calls SET due_at=now() WHERE error='quota' AND response IS NULL")
    conn.execute("""UPDATE runtime_control SET paused_until=NULL,reason=NULL
        WHERE id=1 AND reason IN ('quota','subscription_quota')""")
    return len(rows)


def status_text(policy, accounts):
    lines = [f"회사 공용 모델 계정: {LABELS[policy['profile']]} · 전환 기록 #{policy['revision']}"]
    auth = {"chatgpt": "ChatGPT 로그인 확인", "needs_login": "로그인 필요", "unavailable": "확인 실패",
            "unknown": "미확인"}
    for item in accounts:
        detail = auth[item["authentication"]]
        if item["paused_until"] and item["paused_until"] > datetime.now(UTC):
            retry_at = item["paused_until"].astimezone(ZoneInfo("Asia/Seoul")).strftime("%m/%d %H:%M KST")
            detail += f" · 한도 응답으로 대기, 재시도 가능 {retry_at}"
        elif item["last_fault"] == "quota":
            detail += " · 최근 한도 응답, 재시도 가능"
        elif item["last_success_at"]:
            detail += " · 응답 수신 기록 있음"
        lines.append(f"• {LABELS[item['profile']]}: {detail}")
    lines.append("로그인 확인은 잔여 사용량 확인이 아닙니다. 사용 가능 여부는 다음 실제 요청에서 확인합니다.")
    return "\n".join(lines)


class AccountControl:
    def __init__(self, company, client=None, temporal=None):
        self.company = company
        self.client = client
        self.temporal = temporal

    def pending(self):
        with self.company.db.transaction() as conn:
            return conn.execute("""SELECT id FROM model_account_commands WHERE state='requested'
                ORDER BY sequence LIMIT 1""").fetchone()

    def apply(self, command_id, statuses):
        with self.company.db.transaction() as conn:
            policy = conn.execute("SELECT * FROM model_account_policy WHERE id=1 FOR UPDATE").fetchone()
            command = conn.execute("""SELECT * FROM model_account_commands WHERE state='requested'
                ORDER BY sequence LIMIT 1 FOR UPDATE""").fetchone()
            if not command or command["id"] != command_id:
                return False
            project = self.company._project(conn, command["project_id"])
            authorized = (self.company.settings.model_accounts_enabled
                and command["owner_user"] == self.company.settings.model_accounts_owner_user
                and command["owner_user"] in self.company.settings.slack_allowed_users
                and project["owner_user"] == command["owner_user"]
                and project["channel"] == self.company.settings.model_accounts_channel_id
                and project["channel"] in self.company.settings.slack_allowed_channels)
            if not authorized:
                text, outcome = "계정 제어 권한 또는 설정이 변경되어 요청을 적용하지 않았습니다.", "rejected"
            else:
                for item in statuses:
                    conn.execute("UPDATE model_accounts SET authentication=%s,checked_at=now() WHERE profile=%s",
                                 (item["authentication"], item["profile"]))
                conn.execute("UPDATE model_account_policy SET control_project_id=%s WHERE id=1", (project["id"],))
                accounts = conn.execute("SELECT * FROM model_accounts ORDER BY profile DESC").fetchall()
                target = next((item for item in accounts if item["profile"] == command["target"]), None)
                outcome, text = "status", ""
                if command["action"] == "switch":
                    if target["profile"] == policy["profile"]:
                        outcome, text = "unchanged", "이미 선택된 계정입니다.\n"
                    elif target["authentication"] != "chatgpt":
                        outcome, text = "rejected", "대상 계정의 ChatGPT 로그인을 확인할 수 없어 전환하지 않았습니다.\n"
                    elif target["paused_until"] and target["paused_until"] > datetime.now(UTC):
                        outcome, text = "rejected", "대상 계정도 한도 응답으로 대기 중이어서 전환하지 않았습니다.\n"
                    else:
                        policy = conn.execute("""UPDATE model_account_policy SET profile=%s,revision=revision+1
                            WHERE id=1 RETURNING *""", (target["profile"],)).fetchone()
                        resumed = resume_quota_waits(conn, command_id, policy["revision"])
                        outcome = "switched"
                        text = (f"{LABELS[target['profile']]} 계정으로 전환했습니다. 새 요청과 한도로 대기하던 요청에 적용됩니다. "
                                f"일반 업무 {resumed}건에 재개 신호를 예약했습니다.\n"
                                "진행 중인 요청은 기존 계정에서 마무리합니다. 뉴스·직원 점검은 다음 주기에 재개합니다.\n")
                text += status_text(policy, accounts)
            receipt = {"outcome": outcome, "profile": policy["profile"], "revision": policy["revision"]}
            conn.execute("""UPDATE model_account_commands SET state='completed',receipt=%s,completed_at=now()
                WHERE id=%s""", (Jsonb(receipt), command_id))
            conn.execute("UPDATE tasks SET status='completed',result=%s WHERE id=%s", (text, command_id))
            self.company._message(conn, project, command_id, "director", "status", text)
            self.company._event(conn, "model_account_control", {"command_id": str(command_id),
                "owner": command["owner_user"], "action": command["action"], "target": command["target"],
                **receipt}, project["id"])
            return True

    def wakes(self):
        with self.company.db.transaction() as conn:
            return conn.execute("SELECT * FROM model_account_wakes WHERE NOT done ORDER BY revision LIMIT 100").fetchall()

    def acknowledged(self, row):
        with self.company.db.transaction() as conn:
            conn.execute("UPDATE model_account_wakes SET done=true WHERE command_id=%s AND turn_id=%s",
                         (row["command_id"], row["turn_id"]))

    async def tick(self):
        from .model_control import ModelControl

        # Existing activity/queue keeps Temporal histories unchanged and works during inference quota waits.
        processed = await ModelControl(self.company, self.client).tick()
        pending = None if processed else await asyncio.to_thread(self.pending)
        if pending:
            if self.client is None:
                self.client = RuntimeClient(self.company.settings.model_runtime_url,
                                            self.company.settings.model_runtime_token.get_secret_value())
            try:
                statuses = await self.client.accounts()
            except ProviderFault:
                statuses = [{"profile": profile, "authentication": "unavailable"} for profile in LABELS]
            await asyncio.to_thread(self.apply, pending["id"], statuses)
        if self.temporal:
            for row in await asyncio.to_thread(self.wakes):
                try:
                    await self.temporal.get_workflow_handle("company-turn-" + str(row["turn_id"])).signal(
                        "model_account_changed", row["revision"])
                except RPCError as exc:
                    if exc.status != RPCStatusCode.NOT_FOUND:
                        raise
                await asyncio.to_thread(self.acknowledged, row)

    @activity.defn(name="company_accounts_tick")
    async def activity_tick(self):
        await self.tick()

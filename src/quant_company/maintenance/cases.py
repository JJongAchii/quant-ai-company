"""Durable Slack case destinations. Model output never selects a channel or update target."""

from uuid import NAMESPACE_URL, uuid5

from psycopg.types.json import Jsonb

from ..data_watch.reporting import kst
from . import explain
from .identity import AGENT
from .policy import digest
from .requests import permitted, report, service


def identity(key):
    return str(uuid5(NAMESPACE_URL, "maintenance-case:" + key))


def installed(conn):
    return bool(conn.execute("SELECT to_regclass('maintenance_cases') AS name").fetchone()["name"])


def lookup(conn, project_id):
    if not installed(conn):
        return None
    return conn.execute("SELECT * FROM maintenance_cases WHERE project_id=%s", (project_id,)).fetchone()


def allowed(conn, company, case):
    if not company.settings.company_improvements_enabled or AGENT not in company.roles:
        return False
    source = company._project(conn, case["source_project_id"], lock=False)
    target = company._project(conn, case["project_id"], lock=False)
    from ..data_watch.reporting import incident_allowed
    from ..data_watch.store import DataWatchStore

    incident = conn.execute("SELECT * FROM data_watch_incidents WHERE project_id=%s", (source["id"],)).fetchone()
    if incident and not incident_allowed(conn, DataWatchStore(company), incident):
        return False
    runtime = service(conn, company, case["owner_user"])
    return (runtime.get("enabled") and permitted(company, source) and permitted(company, target)
            and company.roles[AGENT].active
            and source["revision"] == case["source_revision"]
            and source["owner_user"] == target["owner_user"] == case["owner_user"]
            and target["channel"] == company.settings.improvements_channel_id)


def url(project):
    if not project["thread_ts"]:
        return None
    return f"https://app.slack.com/archives/{project['channel']}/p{project['thread_ts'].replace('.', '')}"


def destination(conn, case):
    target = conn.execute("SELECT * FROM projects WHERE id=%s", (case["project_id"],)).fetchone()
    root = conn.execute("SELECT status,error FROM outbox WHERE id=%s", (case["root_message_id"],)).fetchone()
    return {"case_id": str(case["id"]), "channel": target["channel"], "thread_url": url(target),
            "delivery": root["status"], "delivery_error": root["error"]}


def current(conn, case):
    job = conn.execute("SELECT * FROM maintenance_jobs WHERE id=%s", (case["request_id"],)).fetchone()
    value = report(conn, job, case["owner_user"])
    linked = value.get("case_id", str(job["id"]))
    row = conn.execute("SELECT updated_at FROM maintenance_jobs WHERE id=%s", (linked,)).fetchone()
    value["last_progress_at"] = str(row["updated_at"]) if row else str(job["updated_at"])
    call = conn.execute("""SELECT id,error,due_at,created_at FROM maintenance_calls
        WHERE job_id=%s AND response IS NULL ORDER BY created_at DESC LIMIT 1""", (linked,)).fetchone()
    if call:
        value["model_wait"] = {key: str(item) if item is not None else None for key, item in call.items()}
    application = conn.execute("""SELECT state,error,updated_at FROM maintenance_applications
        WHERE job_id=%s""", (linked,)).fetchone()
    if application:
        value["application"] = {key: str(item) if item is not None else None for key, item in application.items()}
    return value


def described(conn, company, case):
    """current() plus what started the case, for the owner-facing text only."""
    value = current(conn, case)
    value["facts"] = explain.facts(conn, company, case["request_id"], value)
    return value


def card(case, value, runtime):
    text = explain.text(value, runtime)
    if value.get("pr"):
        text += "\nPR: " + value["pr"]["url"]
    text += f"\n개선 케이스 {str(case['id'])[:8]} · 최근 진행 {kst(value['last_progress_at'])}"
    text += "\n궁금한 점은 이 스레드에 물어보세요. ‘상태’라고 쓰면 최신 기록을 다시 보여 줍니다."
    return text


def attach(conn, company, request_id, source):
    """Called in the request transaction, never sweeps historical jobs into new channels."""
    if (not company.settings.company_improvements_enabled or AGENT not in company.roles
            or not permitted(company, source) or source["channel"].startswith("D")):
        return None
    old = conn.execute("SELECT * FROM maintenance_cases WHERE request_id=%s", (request_id,)).fetchone()
    if old:
        return destination(conn, old)
    case_id = identity(str(request_id))
    target = source
    if source["channel"] != company.settings.improvements_channel_id or lookup(conn, source["id"]):
        target = conn.execute("""INSERT INTO projects(id,title,instruction,owner_user,channel)
            VALUES (%s,%s,%s,%s,%s) RETURNING *""",
            (identity(case_id + ":project"), f"개선 {case_id[:8]}", source["instruction"],
             source["owner_user"], company.settings.improvements_channel_id)).fetchone()
    case = {"id": case_id, "request_id": request_id, "source_project_id": source["id"],
            "source_revision": source["revision"], "project_id": target["id"],
            "owner_user": source["owner_user"], "root_message_id": identity(case_id + ":root")}
    text = card(case, described(conn, company, case), service(conn, company, case["owner_user"]))
    if source["id"] != target["id"]:
        text += f"\n원 요청: {url(source)}"
    company._message(conn, target, None, AGENT, "maintenance", text, message_id=case["root_message_id"])
    conn.execute("""INSERT INTO maintenance_cases
        (id,request_id,source_project_id,source_revision,project_id,owner_user,root_message_id,last_snapshot)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""", (*case.values(), digest(text)))
    conn.execute("UPDATE maintenance_jobs SET payload=payload || %s WHERE id=%s",
                 (Jsonb({"slack_case_id": case_id}), request_id))
    return destination(conn, case)


def gate(conn, company, outbox):
    """Check again after claiming; revoked access must also stop a queued card/approval notice."""
    case = lookup(conn, outbox["project_id"])
    if not case:
        return (company.settings.company_improvements_enabled
                and outbox["channel"] == company.settings.improvements_channel_id
                and outbox["channel"] in company.settings.slack_allowed_channels
                and company._project(conn, outbox["project_id"], lock=False)["owner_user"]
                in company.settings.slack_allowed_users and not outbox.get("update_ts"))
    if not allowed(conn, company, case):
        return False
    if outbox.get("update_ts"):
        root = conn.execute("SELECT * FROM outbox WHERE id=%s", (case["root_message_id"],)).fetchone()
        return (root["agent"] == AGENT and root["status"] == "delivered"
                and root["sent_ts"] == outbox["update_ts"] and root["channel"] == outbox["channel"])
    return True


def refresh(company):
    """No model, GitHub, or Slack network call. All effects are durable outbox entries."""
    with company.db.transaction() as conn:
        if not installed(conn) or not company.settings.company_improvements_enabled:
            return 0
        if not conn.execute("SELECT pg_try_advisory_xact_lock(71350226) AS ok").fetchone()["ok"]:
            return 0
        rows = conn.execute("SELECT * FROM maintenance_cases ORDER BY checked_at,id LIMIT 50").fetchall()
        for case in rows:
            conn.execute("UPDATE maintenance_cases SET checked_at=now() WHERE id=%s", (case["id"],))
            if not allowed(conn, company, case):
                continue
            target = company._project(conn, case["project_id"])
            root = conn.execute("SELECT * FROM outbox WHERE id=%s", (case["root_message_id"],)).fetchone()
            if root["status"] != "delivered" or not root["sent_ts"] or not target["thread_ts"]:
                continue
            value = described(conn, company, case)
            runtime = service(conn, company, case["owner_user"])
            source = company._project(conn, case["source_project_id"])
            link_id = identity(str(case["id"]) + ":source-link")
            if source["id"] != target["id"] and not conn.execute(
                "SELECT 1 FROM messages WHERE id=%s", (link_id,)).fetchone():
                company._message(conn, source, None, "director", "maintenance",
                    f"개선 케이스 {str(case['id'])[:8]}의 진행·질문·승인: {url(target)}", message_id=link_id)
            text = card(case, value, runtime)
            if source["id"] != target["id"]:
                text += f"\n원 요청: {url(source)}"
            snapshot = digest(text)
            # Never overtake an in-flight/uncertain update, or retry a rejected write automatically.
            unsettled = conn.execute("""SELECT 1 FROM outbox WHERE project_id=%s AND update_ts=%s
                AND status NOT IN ('delivered','stale') LIMIT 1""", (target["id"], root["sent_ts"])).fetchone()
            if snapshot != case["last_snapshot"] and not unsettled:
                update_id = identity(str(case["id"]) + ":update:" + snapshot)
                if not conn.execute("SELECT 1 FROM messages WHERE id=%s", (update_id,)).fetchone():
                    company._message(conn, target, None, AGENT, "maintenance", text, message_id=update_id)
                    conn.execute("UPDATE outbox SET update_ts=%s WHERE id=%s", (root["sent_ts"], update_id))
                conn.execute("UPDATE maintenance_cases SET last_snapshot=%s WHERE id=%s", (snapshot, case["id"]))
            # Owner attention is a separate thread reply; card refreshes never ping the owner.
            pr = value.get("pr")
            if pr and value["state"] == "pr_open" and not value.get("error"):
                notice_id = str(uuid5(NAMESPACE_URL, f"maintenance-pr:{value['case_id']}:{target['id']}"))
                notice = (explain.notice(value, runtime) + f"\nPR: {pr['url']}"
                          f" (검토 기준 커밋 {(value.get('head') or '미확인')[:12]})")
            elif value["state"] == "blocked":
                notice_id = identity(str(case["id"]) + ":blocked:" + digest([value['case_id'], value.get('error')]))
                notice = explain.notice(value, runtime)
            else:
                continue
            if not conn.execute("SELECT 1 FROM messages WHERE id=%s", (notice_id,)).fetchone():
                company._message(conn, target, None, AGENT, "maintenance", notice,
                                 message_id=notice_id, notify_owner=True)
                if pr and value["state"] == "pr_open":
                    conn.execute("UPDATE maintenance_cases SET announced_candidate=%s WHERE id=%s",
                                 (Jsonb({"job_id": value["case_id"], "head": value.get("head"),
                                         "notice_id": notice_id}), case["id"]))
        return len(rows)


def status_text(conn, company, project):
    if not installed(conn):
        return "개선 케이스 서비스가 아직 설치되지 않았습니다."
    case = lookup(conn, project["id"])
    if case and allowed(conn, company, case):
        return card(case, described(conn, company, case), service(conn, company, project["owner_user"]))
    rows = conn.execute("""SELECT * FROM maintenance_cases WHERE owner_user=%s
        ORDER BY created_at DESC,id DESC LIMIT 20""", (project["owner_user"],)).fetchall()
    return explain.list_text([(described(conn, company, case), destination(conn, case)["thread_url"])
                              for case in rows if allowed(conn, company, case)])

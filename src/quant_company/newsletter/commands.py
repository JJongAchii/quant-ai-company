"""Owner commands to the director for the email's recipients. Deterministic, like the daily-limit command: no model
reads them, and the address appears masked everywhere except the recipient table. Text that only looks like a command
or carries an address gets the usage line instead of a model turn."""

import re

from psycopg.types.json import Jsonb

from ..company import PolicyError, stable
from . import store as records
from .store import STATE_NAMES, NewsletterStore, mask, valid_address

COMMAND = re.compile(r"이메일\s*(추가|재동의|삭제|목록|시험\s*발송)\s*"
                     r"(?:해\s*(?:줘|주세요|줄래)|보여\s*(?:줘|주세요)|알려\s*(?:줘|주세요))?"
                     r"(?:(?:\s*[:：]\s*|\s+)(.+?))?\s*[.!。]*", re.S)
ACTIONS = {"추가": "add", "재동의": "reconsent", "삭제": "remove", "목록": "list"}
ADDRESS_LIKE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
USAGE = {"action": "usage"}
USAGE_TEXT = ("저녁 브리핑 메일 수신자 명령은 @quant-director에게 이렇게 보내 주세요: `이메일 추가 <주소>` · `이메일 재동의 <주소>` · "
              "`이메일 삭제 <주소>` · `이메일 목록` · `이메일 시험 발송 <주소>`. 메일 주소가 들어간 글은 모델에 보내지 않습니다.")


def unlink(text):
    # Slack sends a typed address as <mailto:a@b.c|a@b.c>.
    text = re.sub(r"<mailto:([^|>]+)\|[^>]*>", r"\1", text)
    return re.sub(r"<mailto:([^>]+)>", r"\1", text).strip()


def parse(text):
    """A recipient command; USAGE for text that starts like one or carries an address (an address never reaches a
    model); None for anything else."""
    text = unlink(text)
    match = COMMAND.fullmatch(text)
    if match:
        action = ACTIONS.get(match[1], "test")
        argument = (match[2] or "").strip().lower()
        if action == "list":
            return dict(USAGE) if argument else {"action": "list"}
        return {"action": action, "address": argument if valid_address(argument) else None}
    return dict(USAGE) if text.startswith("이메일") or ADDRESS_LIKE.search(text) else None


def accepts(text, command, agent):
    """The command an ingest may carry for TEXT: what parse() reads, or the usage line for any command-like text (the
    only answer outside the director)."""
    parsed = parse(text)
    if command == USAGE:
        return parsed is not None and agent in {"director", "maintainer"}
    return command == parsed and agent == "director"


def authorized(company, project, task, command):
    settings = company.settings
    reachable = (project["owner_user"] in settings.slack_allowed_users and bool(project["channel"])
                 and (project["channel"] in settings.slack_allowed_channels or project["channel"].startswith("D")))
    if command == USAGE:
        return reachable and task["agent"] in {"director", "maintainer"}
    return reachable and task["agent"] == "director" and project["owner_user"] == settings.briefing_owner_user


def apply(conn, company, project, task, event_key, command):
    if not authorized(company, project, task, command):
        raise PolicyError("Newsletter recipients are managed only by the briefing owner through the director")
    store = NewsletterStore(company)
    action, address = command["action"], command.get("address")
    recipient = None
    if action == "usage":
        result, reply = "usage", USAGE_TEXT
    elif action == "list":
        result, reply = "listed", listing(conn, company)
    elif not address:
        result, reply = "invalid_address", ("이메일 주소 형식을 확인하지 못했습니다. 예: `이메일 추가 name@example.com`")
    else:
        recipient = store.recipient(conn, address, lock=True)
        result, reply = {"add": add, "reconsent": reconsent, "remove": remove, "test": test}[action](
            conn, store, recipient, address, project, event_key)
        recipient = store.recipient(conn, address)
    receipt = {"action": action, "result": result, "recipient": mask(address) if address else None}
    conn.execute("""INSERT INTO newsletter_commands(event_key,owner_user,project_id,action,recipient_id,receipt)
        VALUES(%s,%s,%s,%s,%s,%s)""", (event_key, project["owner_user"], project["id"], action,
                                       recipient["id"] if recipient else None, Jsonb(receipt)))
    company._event(conn, "newsletter_command", receipt, project["id"])
    conn.execute("UPDATE tasks SET status='completed',result=%s WHERE id=%s", (reply, task["id"]))
    company._message(conn, project, task["id"], task["agent"], "control", reply)
    return receipt


def active_count(conn):
    return conn.execute("SELECT count(*) AS n FROM newsletter_recipients WHERE state='active'").fetchone()["n"]


def add(conn, store, recipient, address, project, event_key):
    shown = mask(address)
    if recipient and recipient["state"] == "unsubscribed":
        return "refused_unsubscribed", (f"{shown}은 받는 분이 직접 수신 거부한 주소라 다시 켜지 않았습니다. 본인이 다시 동의한 것을 "
                                        "확인하셨다면 `이메일 재동의 <주소>`로 켤 수 있습니다.")
    if recipient and recipient["state"] == "active":
        return "already_active", f"{shown}은 이미 수신 중입니다. 수신 중 {active_count(conn)}명."
    if recipient:
        store.set_state(conn, recipient["id"], "active")
        result = "re_enabled"
    else:
        conn.execute("INSERT INTO newsletter_recipients(id,address,state) VALUES(%s,%s,'active')",
                     (stable("newsletter-recipient:" + address), address))
        result = "added"
    return result, (f"저녁 브리핑 메일 수신자 {'다시 켬' if result == 're_enabled' else '추가'} · {shown} · 수신 중 "
                    f"{active_count(conn)}명. {flag_note(store)}")


def reconsent(conn, store, recipient, address, project, event_key):
    shown = mask(address)
    if not recipient:
        return "unknown", f"{shown}은 등록되지 않은 주소입니다. `이메일 추가 <주소>`로 추가해 주세요."
    if recipient["state"] != "unsubscribed":
        return "not_unsubscribed", f"{shown}은 수신 거부 상태가 아닙니다(현재: {STATE_NAMES[recipient['state']]})."
    store.set_state(conn, recipient["id"], "active")
    return "re_consented", (f"본인 재동의를 확인한 것으로 기록하고 {shown}의 수신을 다시 켰습니다. 수신 중 {active_count(conn)}명.")


def remove(conn, store, recipient, address, project, event_key):
    shown = mask(address)
    if not recipient:
        return "unknown", f"{shown}은 등록되지 않은 주소입니다."
    if recipient["state"] != "active":
        return "not_active", f"{shown}은 이미 받지 않는 주소입니다(현재: {STATE_NAMES[recipient['state']]})."
    store.set_state(conn, recipient["id"], "removed")
    return "removed", f"저녁 브리핑 메일 수신을 껐습니다 · {shown} · 기록은 남깁니다. 수신 중 {active_count(conn)}명."


def test(conn, store, recipient, address, project, event_key):
    shown = mask(address)
    if recipient and recipient["state"] == "unsubscribed":
        return "refused_unsubscribed", f"{shown}은 받는 분이 직접 수신 거부한 주소라 시험 메일도 보내지 않습니다."
    edition = store.latest_edition(conn)
    if not edition:
        return "no_edition", "보낼 마감 브리핑이 아직 없습니다. 오늘 저녁 브리핑이 발송된 뒤 다시 요청해 주세요."
    at = records.utcnow()
    issue = store.ensure_issue(conn, edition, at, auto=False)
    if issue["state"] == "skipped":
        return "no_email", (f"{edition['day']:%m/%d} 마감 브리핑에는 주요 이슈 분석이 없어 메일을 만들 수 없습니다.")
    store.add_test(conn, issue, address, project, event_key, at)
    wait = " 메일을 먼저 작성하므로 몇 분 걸릴 수 있습니다." if issue["state"] == "writing" else ""
    return "queued", (f"이메일 시험 발송 접수 · {shown} · {edition['day']:%m/%d} 마감 브리핑 메일 · 이 주소에만 보냅니다."
                      f"{wait} 결과는 이 스레드에 남깁니다.")


def flag_note(store):
    return ("매 거래일 마감 브리핑 뒤 발송됩니다." if store.settings.newsletter_enabled
            else "자동 발송은 아직 꺼져 있습니다(NEWSLETTER_ENABLED=false).")


def listing(conn, company):
    rows = conn.execute("SELECT address,state FROM newsletter_recipients ORDER BY created_at").fetchall()
    counts = {state: sum(row["state"] == state for row in rows) for state in STATE_NAMES}
    head = (f"저녁 브리핑 메일 수신자 {len(rows)}명 · 수신 중 {counts['active']} · 삭제 {counts['removed']} · "
            f"수신 거부 {counts['unsubscribed']}")
    lines = [f"• {mask(row['address'])} · {STATE_NAMES[row['state']]}" for row in rows[:50]]
    return "\n".join([head, *lines, flag_note(NewsletterStore(company))])

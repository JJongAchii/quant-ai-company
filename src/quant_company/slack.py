import hashlib
import hmac
import json
import re
import time
from datetime import timedelta

import httpx

from .company import Company, PolicyError, as_json, now
from .config import Settings


class SlackIngress:
    def __init__(self, settings: Settings, company: Company, credentials: dict | None = None):
        self.settings = settings
        self.company = company
        if credentials is not None:
            self.credentials = credentials
        elif settings.slack_credentials_file:
            self.credentials = json.loads(settings.slack_credentials_file.read_text())
        else:
            self.credentials = {}

    def verify(self, role: str, body: bytes, timestamp: str, signature: str, at=None):
        credential = self.credentials.get(role)
        if not credential or not credential.get("signing_secret"):
            raise PolicyError("Slack employee is not configured")
        if len(body) > 262144:
            raise PolicyError("Slack body too large")
        try:
            stamp = int(timestamp)
        except (TypeError, ValueError) as exc:
            raise PolicyError("Invalid Slack timestamp") from exc
        if abs((at if at is not None else time.time()) - stamp) > 300:
            raise PolicyError("Expired Slack signature")
        signed = b"v0:" + timestamp.encode() + b":" + body
        expected = "v0=" + hmac.new(credential["signing_secret"].encode(), signed, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature or ""):
            raise PolicyError("Invalid Slack signature")
        return credential

    def accept(self, role, payload: dict, credential: dict):
        if payload.get("type") == "url_verification":
            return {"challenge": payload.get("challenge", "")}
        if (not self.settings.slack_team_id or payload.get("team_id") != self.settings.slack_team_id
                or payload.get("api_app_id") != credential["app_id"]):
            raise PolicyError("Unexpected Slack workspace or app")
        event = payload.get("event", {})
        if (event.get("type") not in {"app_mention", "message"}
                or event.get("bot_id") or event.get("subtype") or not event.get("user")):
            return {"ok": True, "ignored": True}
        user = event["user"]
        if user not in self.settings.slack_allowed_users:
            return {"ok": True, "ignored": True}
        channel = event.get("channel", "")
        is_dm = event.get("channel_type") == "im" or channel.startswith("D")
        if not is_dm and channel not in self.settings.slack_allowed_channels:
            return {"ok": True, "ignored": True}
        text = event.get("text", "")
        timestamp = event.get("ts")
        if not text.strip() or not timestamp:
            return {"ok": True, "ignored": True}
        mentions = set(re.findall(r"<@([A-Z0-9]+)>", text))
        known = {item["bot_user_id"]: name for name, item in self.credentials.items()}
        targets = {known[mention] for mention in mentions if mention in known}
        thread_ts = event.get("thread_ts") or timestamp
        if is_dm:
            target = role
        elif targets:
            if role not in targets:
                return {"ok": True, "ignored": True}
            target = role
        else:
            if role not in {"director", "reporter"} or not event.get("thread_ts"):
                return {"ok": True, "ignored": True}
            with self.company.db.transaction() as conn:
                project = conn.execute("""SELECT p.id,EXISTS(SELECT 1 FROM news_events n WHERE n.project_id=p.id) AS news
                    FROM projects p WHERE channel=%s AND thread_ts=%s""",
                                       (channel, thread_ts)).fetchone()
            if not project:
                return {"ok": True, "ignored": True}
            target = "reporter" if project["news"] else "director"
            if role != target:
                return {"ok": True, "ignored": True}
        text = re.sub(r"<@[A-Z0-9]+>", "", text).strip()
        if target == "director":
            from .maintenance.applications import accept_approval
            from .owner_controls import parse_daily_limit_command

            command = parse_daily_limit_command(text)
            if command:
                result = self.company.ingest(
                    event_key=f"slack:{payload['team_id']}:{channel}:{timestamp}:{target}", text=text,
                    owner=user, agent=target, channel=channel, thread_ts=thread_ts, daily_limit_command=command)
                return {"ok": True, "owner_control": True, **result}

            approval = accept_approval(
                self.company, text=text, owner=user, channel=channel, thread_ts=thread_ts,
                event_key=f"slack:{payload['team_id']}:{channel}:{timestamp}:{target}", event_ts=timestamp)
            if approval is not None:
                return {"ok": True, **approval}
        revise = text.startswith(("수정:", "변경:", "revise:"))
        if revise:
            text = text.split(":", 1)[1].strip()
            target = "director"
        from .task_control import immediate

        result = self.company.ingest(
            event_key=f"slack:{payload['team_id']}:{channel}:{timestamp}:{target}",
            text=text, owner=user, agent=target, channel=channel, thread_ts=thread_ts, revise=revise,
            status_only=text.strip().lower() in {"상태", "진행 상황", "status"},
            interpret=target == 'director' and not revise,
            control_action=immediate(text) if target == 'director' and not revise else None,
        )
        return {"ok": True, **result}


class SlackOutbox:
    def __init__(self, company: Company, credentials: dict, transport=None):
        self.company = company
        self.credentials = credentials
        self.transport = transport

    def recover_uncertain(self):
        # A process may have died after Slack accepted a request. Never blindly send it again.
        with self.company.db.transaction() as conn:
            conn.execute("""UPDATE outbox SET status='uncertain',error='sender_lease_expired'
                WHERE status='sending' AND started_at<now()-interval '2 minutes'""")

    def claim(self):
        with self.company.db.transaction() as conn:
            row = conn.execute("""SELECT o.*,m.kind AS message_kind FROM outbox o JOIN messages m ON m.id=o.id
                LEFT JOIN tasks k ON k.id=m.task_id JOIN projects p ON p.id=o.project_id
                WHERE o.status='pending' AND o.next_at<=now() AND
                (m.kind IN ('control','maintenance','status') OR
                 ((p.status='active' OR k.kind='answer') AND NOT EXISTS
                  (SELECT 1 FROM tasks r WHERE r.project_id=o.project_id AND r.kind='routing'
                   AND r.status NOT IN ('completed','superseded'))))
                ORDER BY o.created_at FOR UPDATE OF o SKIP LOCKED LIMIT 1""").fetchone()
            if not row:
                return None
            if row["message_kind"] == "news":
                from .news.store import NewsStore

                if not NewsStore(self.company).delivery_allowed(conn, row):
                    conn.execute("UPDATE outbox SET status='stale',error='news_policy_or_freshness_changed' WHERE id=%s",
                                 (row["id"],))
                    return None
                row["news_broadcast"] = conn.execute("SELECT broadcast FROM news_publications WHERE id=%s",
                                                    (row["id"],)).fetchone()["broadcast"]
            project = conn.execute("SELECT revision FROM projects WHERE id=%s", (row["project_id"],)).fetchone()
            if row["revision"] != project["revision"]:
                conn.execute("UPDATE outbox SET status='stale' WHERE id=%s", (row["id"],))
                return None
            if row["agent"] not in self.credentials:
                conn.execute("UPDATE outbox SET status='blocked',error='missing_slack_identity' WHERE id=%s",
                             (row["id"],))
                return None
            conn.execute("UPDATE outbox SET status='sending',attempts=attempts+1,started_at=now() WHERE id=%s",
                         (row["id"],))
            return as_json(row)

    def settle(self, row, status, *, error=None, delay=0, sent_ts=None):
        with self.company.db.transaction() as conn:
            conn.execute("""UPDATE outbox SET status=%s,error=%s,next_at=%s,sent_ts=%s WHERE id=%s
                AND status='sending'""", (status, error, now() + timedelta(seconds=delay), sent_ts, row["id"]))
            if status == "delivered" and sent_ts and row.get("message_kind") == "news" and not row["thread_ts"]:
                conn.execute("UPDATE projects SET thread_ts=%s WHERE id=%s AND thread_ts IS NULL",
                             (sent_ts, row["project_id"]))

    async def send_one(self):
        import asyncio

        await asyncio.to_thread(self.recover_uncertain)
        row = await asyncio.to_thread(self.claim)
        if not row:
            return False
        token = self.credentials[row["agent"]]["bot_token"]
        # The stable client_msg_id helps correlation; it is not an exactly-once guarantee.
        body = {"channel": row["channel"], "thread_ts": row["thread_ts"],
                "text": row["text"] if row["agent"] == "reporter" else f"[지시 v{row['revision']}] {row['text']}",
                "client_msg_id": row["id"],
                "unfurl_links": False, "unfurl_media": False,
                "metadata": {"event_type": "quant_company_message", "event_payload": {"id": row["id"]}}}
        if row["thread_ts"] is None:
            del body["thread_ts"]
        elif row.get("news_broadcast"):
            body["reply_broadcast"] = True
        try:
            async with httpx.AsyncClient(timeout=12, transport=self.transport) as client:
                response = await client.post("https://slack.com/api/chat.postMessage", json=body,
                                             headers={"Authorization": "Bearer " + token})
            if response.status_code == 429:
                try:
                    delay = max(1, min(int(response.headers.get("retry-after", "30")), 86400))
                except ValueError:
                    delay = 30
                await asyncio.to_thread(self.settle, row, "pending", error="rate_limited", delay=delay)
                return True
            if response.status_code >= 500:
                await asyncio.to_thread(self.settle, row, "uncertain", error="slack_server_error")
                return True
            result = response.json()
            if result.get("ok") and (row.get("message_kind") != "news" or result.get("ts")):
                await asyncio.to_thread(self.settle, row, "delivered", sent_ts=result.get("ts"))
            elif result.get("ok"):
                await asyncio.to_thread(self.settle, row, "uncertain", error="missing_slack_message_receipt")
            else:
                error = str(result.get("error", "slack_rejected"))[:100]
                await asyncio.to_thread(self.settle, row, "blocked", error=error)
        except (httpx.HTTPError, ValueError):
            await asyncio.to_thread(self.settle, row, "uncertain", error="delivery_outcome_unknown")
        return True

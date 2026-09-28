import hashlib
import hmac
import json
import re
import time
from datetime import timedelta

import httpx

from .company import Company, PolicyError, as_json, now
from .config import Settings
from .quant_feed.contracts import QUANT_FEED_AGENT
from .tech_feed.contracts import TECH_FEED_AGENT


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
        if payload.get("type") == "block_actions":
            return self.accept_interaction(role, payload, credential)
        if payload.get("type") == "url_verification":
            return {"challenge": payload.get("challenge", "")}
        if (not self.settings.slack_team_id or payload.get("team_id") != self.settings.slack_team_id
                or payload.get("api_app_id") != credential["app_id"]):
            raise PolicyError("Unexpected Slack workspace or app")
        event = payload.get("event")
        if isinstance(event, dict) and event.get("type") == "entity_details_requested":
            if role != "reporter":
                return {"ok": True, "ignored": True}
            from .housing_feed.panel import HousingMapPanel

            return HousingMapPanel(self.company, self.credentials).accept(payload, event, credential)
        if role == TECH_FEED_AGENT:
            # Outbound-only identity: even an accidentally configured callback never creates model work.
            return {"ok": True, "ignored": True, "reason": "tech_feed_delivery_identity_is_not_interactive"}
        if role == QUANT_FEED_AGENT:
            return {"ok": True, "ignored": True, "reason": "quant_feed_delivery_identity_is_not_interactive"}
        event = event if isinstance(event, dict) else {}
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
        from .accounts import parse_command as parse_account_command

        account_text = re.sub(r"<@[A-Z0-9]+>", "", text).strip()
        account_command = parse_account_command(account_text)
        account_channel = channel == self.settings.model_accounts_channel_id
        if account_channel:
            if role != "director" or user != self.settings.model_accounts_owner_user:
                return {"ok": True, "ignored": True, "reason": "account_channel_scope"}
        elif account_command is not None:
            return {"ok": True, "ignored": True, "reason": "account_channel_required"}
        mentions = set(re.findall(r"<@([A-Z0-9]+)>", text))
        known = {item["bot_user_id"]: name for name, item in self.credentials.items()}
        targets = {known[mention] for mention in mentions if mention in known}
        if account_channel and targets and targets != {"director"}:
            return {"ok": True, "ignored": True, "reason": "account_channel_scope"}
        thread_ts = event.get("thread_ts") or timestamp
        improvements = (self.settings.company_improvements_enabled
                        and channel == self.settings.improvements_channel_id)
        if role == "maintainer" and not improvements:
            return {"ok": True, "ignored": True}
        if account_channel:
            target = "director"
        elif self.settings.data_watch_enabled and channel == self.settings.data_watch_channel_id:
            if role != "data" or user != self.settings.data_watch_owner_user:
                return {"ok": True, "ignored": True}
            target = "data"
        elif improvements:
            if role != "maintainer":
                return {"ok": True, "ignored": True}
            target = "maintainer"
        elif is_dm:
            target = role
        elif targets:
            if role not in targets:
                return {"ok": True, "ignored": True}
            target = role
        else:
            if role not in {"director", "reporter"} or not event.get("thread_ts"):
                return {"ok": True, "ignored": True}
            with self.company.db.transaction() as conn:
                project = conn.execute("""SELECT p.id,(EXISTS(SELECT 1 FROM news_events n WHERE n.project_id=p.id)
                    OR EXISTS(SELECT 1 FROM news_digests d WHERE d.project_id=p.id)) AS news
                    FROM projects p WHERE channel=%s AND thread_ts=%s""",
                                       (channel, thread_ts)).fetchone()
            if not project:
                return {"ok": True, "ignored": True}
            target = "reporter" if project["news"] else "director"
            if role != target:
                return {"ok": True, "ignored": True}
        original_text = text
        text = re.sub(r"<@[A-Z0-9]+>", "", text).strip()
        if account_channel and not text:
            text = original_text.strip()
        approval_context = {"origin": "event_callback", "team_id": payload["team_id"],
                            "app_id": payload["api_app_id"], "owner": user, "channel": channel,
                            "thread_ts": thread_ts, "event_ts": timestamp,
                            "provider_event_id": payload.get("event_id"), "original_text": original_text}
        if target == "maintainer":
            from .maintenance.applications import accept_approval

            approval = accept_approval(
                self.company, text=text, owner=user, channel=channel, thread_ts=thread_ts,
                event_key=f"slack:{payload['team_id']}:{channel}:{timestamp}:{target}", event_ts=timestamp)
            if approval is not None:
                return {"ok": True, **approval}
        if target == "director":
            if account_channel:
                result = self.company.ingest(
                    event_key=f"slack:{payload['team_id']}:{channel}:{timestamp}:{target}", text=text,
                    owner=user, agent=target, channel=channel, thread_ts=thread_ts,
                    account_command=account_command, account_help=account_command is None)
                return {"ok": True, "owner_control": account_command is not None,
                        "account_help": account_command is None, **result}
            from .maintenance.applications import accept_approval
            from .owner_controls import parse_daily_limit_command

            command = parse_daily_limit_command(text)
            if command:
                result = self.company.ingest(
                    event_key=f"slack:{payload['team_id']}:{channel}:{timestamp}:{target}", text=text,
                    owner=user, agent=target, channel=channel, thread_ts=thread_ts, daily_limit_command=command)
                return {"ok": True, "owner_control": True, **result}

            from .research.approvals import has_targets, short_command

            research_reply = short_command(text) and has_targets(self.company, user, channel, thread_ts)
            if not research_reply:
                approval = accept_approval(
                    self.company, text=text, owner=user, channel=channel, thread_ts=thread_ts,
                    event_key=f"slack:{payload['team_id']}:{channel}:{timestamp}:{target}", event_ts=timestamp)
                if approval is not None:
                    return {"ok": True, **approval}
        revise = target != "maintainer" and text.startswith(("수정:", "변경:", "revise:"))
        if revise:
            text = text.split(":", 1)[1].strip()
            target = "director"
        from .task_control import immediate

        result = self.company.ingest(
            event_key=f"slack:{payload['team_id']}:{channel}:{timestamp}:{target}",
            text=text, owner=user, agent=target, channel=channel, thread_ts=thread_ts, revise=revise,
            status_only=text.strip().lower() in ({"상태", "진행 상황", "status", "목록", "list"}
                                               if target in {"maintainer", "data"} else {"상태", "진행 상황", "status"}),
            interpret=target == 'director' and not revise,
            control_action=immediate(text) if target == 'director' and not revise else None,
            approval_context=approval_context if target == "director" else None,
        )
        return {"ok": True, **result}

    def accept_interaction(self, role, payload, credential):
        from .research.approvals import ACTIONS, ApprovalEvent

        try:
            if (role != "director" or payload["type"] != "block_actions"
                    or payload["team"]["id"] != self.settings.slack_team_id
                    or payload["api_app_id"] != credential["app_id"]
                    or len(payload["actions"]) != 1):
                raise PolicyError("Unexpected research Slack interaction")
            action, message, container = payload["actions"][0], payload["message"], payload["container"]
            user, channel = payload["user"]["id"], payload["channel"]["id"]
            selected = ACTIONS[action["action_id"]]
            if (action["type"] != "button" or action["block_id"] != "research_approval:" + action["value"]
                    or user not in self.settings.slack_allowed_users
                    or (not channel.startswith("D") and channel not in self.settings.slack_allowed_channels)
                    or message["user"] != credential["bot_user_id"]
                    or message.get("app_id", credential["app_id"]) != credential["app_id"]
                    or message["ts"] != container["message_ts"] or channel != container["channel_id"]
                    or container["type"] not in {"message", "message_attachment"}
                    or container.get("is_ephemeral", False)):
                raise PolicyError("Invalid research Slack message identity")
            event = ApprovalEvent(origin="block_actions", team_id=payload["team"]["id"], app_id=payload["api_app_id"],
                owner=user, channel=channel, thread_ts=message["thread_ts"], event_ts=action["action_ts"],
                original_text=action["text"]["text"], binding=action["value"], action=selected, message_ts=message["ts"])
            if event.original_text != ("승인" if selected == "approve" else "취소"):
                raise PolicyError("Research approval button label changed")
        except (KeyError, TypeError, ValueError, AttributeError):
            raise PolicyError("Malformed research Slack interaction") from None
        result = self.company.ingest(event_key=event.event_key(), text=event.original_text, owner=user,
            agent="director", channel=channel, thread_ts=event.thread_ts, approval_context=event.model_dump(mode="json"))
        return {"ok": True, "research_approval": True, **result}


class SlackOutbox:
    def __init__(self, company: Company, credentials: dict, transport=None):
        self.company = company
        self.credentials = credentials
        self.transport = transport

    def recover_uncertain(self):
        # A process may have died after Slack accepted a request. Never blindly send it again.
        with self.company.db.transaction() as conn:
            rows = conn.execute("""UPDATE outbox SET status='uncertain',error='sender_lease_expired'
                WHERE status='sending' AND started_at<now()-interval '2 minutes' RETURNING id""").fetchall()
            from .news.digest import NewsDigestStore

            for row in rows:
                NewsDigestStore.settle_members(conn, row, "uncertain", error="sender_lease_expired")

    def defer_news(self, conn, row, *, claimed=False):
        from .news import schedule

        if row["message_kind"] not in {"news", "news_digest"} or not self.company.settings.news_delivery_window_enabled:
            return False
        at = schedule.utcnow()
        waiting = False
        if schedule.quiet(self.company.settings, at):
            until = schedule.opening(at)
            if row["message_kind"] == "news":
                conn.execute("UPDATE news_publications SET morning_day=%s WHERE id=%s", (until.date(), row["id"]))
        else:
            if row["message_kind"] == "news":
                conn.execute("""UPDATE news_publications p SET morning_day=%s FROM outbox o
                    WHERE p.id=o.id AND o.id=%s AND o.created_at<%s AND p.morning_day IS NULL
                    AND p.digest_id IS NULL AND NOT EXISTS (SELECT 1 FROM news_digests d
                        WHERE d.day=%s AND d.channel=o.channel AND d.part=0)""",
                             (schedule.opening(at).date(), row["id"], schedule.opening(at), schedule.opening(at).date()))
            waiting = row["message_kind"] == "news" and conn.execute(
                "SELECT 1 FROM news_publications WHERE id=%s AND morning_day IS NOT NULL AND digest_id IS NULL",
                (row["id"],)).fetchone()
            until = at + timedelta(seconds=30)
        if schedule.quiet(self.company.settings, at) or waiting:
            conn.execute("""UPDATE outbox SET status='pending',next_at=%s,started_at=NULL,
                attempts=GREATEST(0,attempts-%s),error=%s WHERE id=%s AND status IN ('pending','sending')""",
                         (until, int(claimed), "awaiting_morning_digest" if waiting else "news_quiet_hours", row["id"]))
            return True
        return False

    def before_send(self, row):
        with self.company.db.transaction() as conn:
            current = conn.execute("SELECT status FROM outbox WHERE id=%s FOR UPDATE", (row["id"],)).fetchone()
            if not current or current["status"] != "sending":
                return False
            if row["agent"] == "maintainer":
                from .maintenance.cases import gate

                if not gate(conn, self.company, row):
                    conn.execute("UPDATE outbox SET status='stale',error='maintenance_scope_changed' WHERE id=%s",
                                 (row["id"],))
                    return False
            if row["message_kind"] == "tech_feed":
                from .tech_feed.store import TechFeedStore

                return TechFeedStore(self.company).gate(conn, row, claimed=True)
            if row["message_kind"] == "housing_feed":
                from .housing_feed.store import HousingFeedStore

                return HousingFeedStore(self.company).gate(conn, row, claimed=True)
            if row["message_kind"] == "quant_feed":
                from .quant_feed.store import QuantFeedStore

                return QuantFeedStore(self.company).gate(conn, row, claimed=True)
            if row["message_kind"] == "data_watch":
                from .data_watch.reporting import gate
                from .data_watch.store import DataWatchStore

                return gate(conn, DataWatchStore(self.company), row)
            return not self.defer_news(conn, row, claimed=True)

    def claim(self):
        with self.company.db.transaction() as conn:
            row = conn.execute("""SELECT o.*,m.kind AS message_kind FROM outbox o JOIN messages m ON m.id=o.id
                LEFT JOIN tasks k ON k.id=m.task_id JOIN projects p ON p.id=o.project_id
                WHERE o.status='pending' AND o.next_at<=now() AND
                (m.kind IN ('control','maintenance','status') OR
                 ((p.status='active' OR k.kind='answer') AND NOT EXISTS
                  (SELECT 1 FROM tasks r WHERE r.project_id=o.project_id AND r.kind='routing'
                   AND r.status NOT IN ('completed','superseded'))))
                ORDER BY (EXISTS(SELECT 1 FROM quant_feed_publications q WHERE q.id=o.id AND q.correction)) DESC,
                o.created_at FOR UPDATE OF o SKIP LOCKED LIMIT 1""").fetchone()
            if not row:
                return None
            if self.defer_news(conn, row):
                return None
            if row["message_kind"] == "data_watch":
                from .data_watch.reporting import gate
                from .data_watch.store import DataWatchStore

                if not gate(conn, DataWatchStore(self.company), row):
                    return None
            if row["message_kind"] == "tech_feed":
                from .tech_feed.store import TechFeedStore

                if not TechFeedStore(self.company).gate(conn, row):
                    return None
            if row["message_kind"] == "housing_feed":
                from .housing_feed.store import HousingFeedStore

                if not HousingFeedStore(self.company).gate(conn, row):
                    return None
            if row["message_kind"] == "quant_feed":
                from .quant_feed.store import QuantFeedStore

                if not QuantFeedStore(self.company).gate(conn, row):
                    return None
            if row["message_kind"] in {"news", "news_digest"}:
                from .news.digest import NewsDigestStore
                from .news.store import NewsStore

                allowed = (NewsDigestStore(self.company).allowed(conn, row) if row["message_kind"] == "news_digest"
                           else NewsStore(self.company).delivery_allowed(conn, row))
                if not allowed:
                    conn.execute("UPDATE outbox SET status='stale',error='news_policy_or_freshness_changed' WHERE id=%s",
                                 (row["id"],))
                    NewsDigestStore.settle_members(conn, row, "stale", error="news_policy_or_freshness_changed")
                    return None
                if row["message_kind"] == "news":
                    row["news_broadcast"] = conn.execute("SELECT broadcast FROM news_publications WHERE id=%s",
                                                        (row["id"],)).fetchone()["broadcast"]
            project = conn.execute("SELECT revision FROM projects WHERE id=%s", (row["project_id"],)).fetchone()
            if row["revision"] != project["revision"]:
                conn.execute("UPDATE outbox SET status='stale' WHERE id=%s", (row["id"],))
                if row["message_kind"] == "news_digest":
                    NewsDigestStore.settle_members(conn, row, "stale", error="project_revision_changed")
                return None
            if row["agent"] not in self.credentials:
                conn.execute("UPDATE outbox SET status='blocked',error='missing_slack_identity' WHERE id=%s",
                             (row["id"],))
                if row["message_kind"] == "news_digest":
                    NewsDigestStore.settle_members(conn, row, "blocked", error="missing_slack_identity")
                return None
            conn.execute("UPDATE outbox SET status='sending',attempts=attempts+1,started_at=now() WHERE id=%s",
                         (row["id"],))
            return as_json(row)

    def settle(self, row, status, *, error=None, delay=0, sent_ts=None):
        with self.company.db.transaction() as conn:
            changed = conn.execute("""UPDATE outbox SET status=%s,error=%s,next_at=%s,sent_ts=%s WHERE id=%s
                AND status='sending' RETURNING id""", (status, error, now() + timedelta(seconds=delay), sent_ts, row["id"])).fetchone()
            if not changed:
                return
            if row.get("message_kind") == "news_digest":
                from .news.digest import NewsDigestStore

                NewsDigestStore.settle_members(conn, row, status, sent_ts, error)
            if status == "delivered" and sent_ts and row.get("message_kind") in {"news", "news_digest"} and not row["thread_ts"]:
                conn.execute("UPDATE projects SET thread_ts=%s WHERE id=%s AND thread_ts IS NULL",
                             (sent_ts, row["project_id"]))
            if status == "delivered" and sent_ts and row["agent"] == "maintainer" and not row["thread_ts"]:
                conn.execute("UPDATE projects SET thread_ts=%s WHERE id=%s AND thread_ts IS NULL",
                             (sent_ts, row["project_id"]))
            if status == "delivered" and sent_ts and row.get("message_kind") == "data_watch" and not row["thread_ts"]:
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
                "text": row["text"] if row["agent"] in {"reporter", TECH_FEED_AGENT, QUANT_FEED_AGENT, "maintainer"}
                or row["message_kind"] == "data_watch"
                else f"[지시 v{row['revision']}] {row['text']}",
                "client_msg_id": row["id"],
                "unfurl_links": False, "unfurl_media": False,
                "metadata": {"event_type": "quant_company_message", "event_payload": {"id": row["id"]}}}
        from .research.approvals import blocks_for_outbox

        blocks = await asyncio.to_thread(blocks_for_outbox, self.company, row, self.credentials[row["agent"]])
        if blocks:
            body["blocks"] = blocks
        if row.get("message_kind") == "housing_feed" and self.company.settings.housing_map_panel_enabled:
            from .housing_feed.maps import work_object
            from .housing_feed.store import HousingFeedStore

            notice = await asyncio.to_thread(HousingFeedStore(self.company).notice_for_message, row["id"])
            if notice and notice.map_location:
                body["metadata"] = {"entities": [work_object(notice)]}
        if row["thread_ts"] is None:
            del body["thread_ts"]
        elif row.get("news_broadcast"):
            body["reply_broadcast"] = True
        method = "chat.postMessage"
        if row.get("update_ts"):
            method = "chat.update"
            body = {key: body[key] for key in ("channel", "text")}
            body["ts"] = row["update_ts"]
        try:
            async with httpx.AsyncClient(timeout=12, transport=self.transport) as client:
                if not await asyncio.to_thread(self.before_send, row):
                    return False
                response = await client.post("https://slack.com/api/" + method, json=body,
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
            requires_receipt = (row.get("message_kind") in {"news", "news_digest", "tech_feed", "quant_feed", "data_watch", "housing_feed"}
                                or row["agent"] == "maintainer")
            if row.get("update_ts") and result.get("ts") != row["update_ts"] and result.get("ok"):
                await asyncio.to_thread(self.settle, row, "uncertain", error="slack_update_receipt_mismatch")
            elif row.get("message_kind") in {"data_watch", "housing_feed"} and result.get("ok") and (
                not isinstance(result.get("ts"), str) or not re.fullmatch(r"\d+\.\d+", result["ts"])
                or result.get("channel", None if row["message_kind"] == "housing_feed" else row["channel"]) != row["channel"]
            ):
                await asyncio.to_thread(self.settle, row, "uncertain", error=f"{row['message_kind']}_delivery_receipt_mismatch")
            elif result.get("ok") and (not requires_receipt or result.get("ts")):
                await asyncio.to_thread(self.settle, row, "delivered", sent_ts=result.get("ts"))
            elif result.get("ok"):
                await asyncio.to_thread(self.settle, row, "uncertain", error="missing_slack_message_receipt")
            else:
                error = str(result.get("error", "slack_rejected"))[:100]
                await asyncio.to_thread(self.settle, row, "blocked", error=error)
        except (httpx.HTTPError, ValueError):
            await asyncio.to_thread(self.settle, row, "uncertain", error="delivery_outcome_unknown")
        return True

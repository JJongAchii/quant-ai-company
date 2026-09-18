"""PostgreSQL queues, frozen editorial calls and atomic outbox publication."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from psycopg.types.json import Jsonb

from ..company import as_json, fingerprint, stable
from ..contracts import ProviderRequest
from ..owner_controls import effective_limits
from .contracts import NewsSource, load_sources
from .editor import bounded_prompt, render, validate_review
from .feeds import timestamp


class NewsStore:
    def __init__(self, company):
        self.company = company
        self.db = company.db

    def sources(self):
        return {item.id: item for item in load_sources(self.company.settings.news_sources_file)}

    def policy(self):
        settings = self.company.settings
        return fingerprint({"enabled": settings.company_news_enabled, "publish": settings.news_publish_enabled,
                            "channel": settings.news_channel_id, "owner": settings.news_owner_user,
                            "allowed_channels": settings.slack_allowed_channels,
                            "allowed_users": settings.slack_allowed_users,
                            "max_age": settings.news_max_age_hours,
                            "sources": [s.model_dump() for s in self.sources().values()]})

    def authorized(self):
        settings = self.company.settings
        return (settings.company_news_enabled and settings.news_owner_user in settings.slack_allowed_users
                and settings.news_channel_id in settings.slack_allowed_channels
                and settings.news_channel_id.startswith(("C", "G"))
                and "reporter" in self.company.roles and self.company.roles["reporter"].active)

    def sync_sources(self):
        sources = self.sources()
        with self.db.transaction() as conn:
            conn.execute("UPDATE news_sources SET enabled=false WHERE NOT (id=ANY(%s))", (list(sources),))
            for source in sources.values():
                config = source.model_dump()
                conn.execute("""INSERT INTO news_sources(id,config,config_digest,enabled) VALUES(%s,%s,%s,%s)
                    ON CONFLICT(id) DO UPDATE SET config=excluded.config,config_digest=excluded.config_digest,
                    enabled=excluded.enabled,
                    etag=CASE WHEN news_sources.config_digest=excluded.config_digest THEN news_sources.etag END,
                    modified=CASE WHEN news_sources.config_digest=excluded.config_digest THEN news_sources.modified END""",
                             (source.id, Jsonb(config), fingerprint(config), source.enabled))

    def claim_source(self):
        if not self.company.settings.company_news_enabled:
            return None
        self.sync_sources()
        with self.db.transaction() as conn:
            row = conn.execute("""SELECT * FROM news_sources WHERE enabled AND next_at<=now()
                AND (lease_until IS NULL OR lease_until<now()) ORDER BY next_at,id
                FOR UPDATE SKIP LOCKED LIMIT 1""").fetchone()
            if row:
                conn.execute("UPDATE news_sources SET lease_until=now()+interval '2 minutes',last_attempt=now() WHERE id=%s",
                             (row["id"],))
            return row

    def save_feed(self, claimed, receipt):
        at = datetime.now(UTC)
        with self.db.transaction() as conn:
            source = conn.execute("SELECT * FROM news_sources WHERE id=%s FOR UPDATE", (claimed["id"],)).fetchone()
            if not source or not source["enabled"] or source["config_digest"] != claimed["config_digest"]:
                return {"state": "stale"}
            interval = source["config"]["poll_seconds"]
            if not receipt["ok"]:
                delay = min(3600, interval * (2 ** min(source["failures"], 4)))
                conn.execute("""UPDATE news_sources SET failures=failures+1,error=%s,receipt=%s,
                    lease_until=NULL,next_at=%s WHERE id=%s""",
                             (receipt.get("error", "feed_failed"), Jsonb(as_json(receipt)),
                              at+timedelta(seconds=delay), source["id"]))
                return {"state": "source_error"}
            added = 0
            for entry in receipt["entries"]:
                identity = fingerprint([source["id"], entry["url"], entry["digest"]])
                published = entry["published_at"]
                horizon = (timedelta(hours=self.company.settings.news_max_age_hours) if source["last_success"]
                           else timedelta(minutes=self.company.settings.news_initial_lookback_minutes))
                state = "collected" if source["config"]["use_for_summary"] else "discovery_only"
                pending_date = (published is None and source["config"].get("undated_publication") == "page_metadata"
                                and entry["updated_at"] is not None and at-horizon <= entry["updated_at"] <= at+timedelta(minutes=5))
                if not pending_date and (published is None or published < at-horizon or published > at+timedelta(minutes=5)):
                    state = "historical_or_undated"
                inserted = conn.execute("""INSERT INTO news_articles(id,source_id,url,title,summary,feed_digest,
                    published_at,updated_at,source_digest,state,retrieval) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING RETURNING id""",
                                        (identity, source["id"], entry["url"], entry["title"],
                                         entry["summary"] if source["config"]["use_for_summary"] else "",
                                         entry["digest"], published, entry["updated_at"], source["config_digest"], state,
                                         Jsonb({"earliest_publication": (at-horizon).isoformat()}) if pending_date else None)).fetchone()
                added += bool(inserted)
            metadata = {k: v for k, v in receipt.items() if k != "entries"}
            metadata["latest_publication"] = max((e["published_at"] for e in receipt["entries"]
                                                  if e["published_at"]), default=None)
            if receipt.get("not_modified"):
                metadata["latest_publication"] = (source["receipt"] or {}).get("latest_publication")
            conn.execute("""UPDATE news_sources SET failures=0,error=NULL,receipt=%s,last_success=%s,
                lease_until=NULL,next_at=%s,etag=COALESCE(%s,etag),modified=COALESCE(%s,modified) WHERE id=%s""",
                         (Jsonb(as_json(metadata)), at, at+timedelta(seconds=interval),
                          receipt.get("etag"), receipt.get("modified"), source["id"]))
            return {"state": "collected", "added": added, "source": source["id"]}

    def claim_article(self):
        with self.db.transaction() as conn:
            conn.execute("""UPDATE news_articles SET state='expired' WHERE state IN ('collected','fetching','ready','held')
                AND published_at<now()-make_interval(hours=>%s)""", (self.company.settings.news_max_age_hours,))
            row = conn.execute("""SELECT a.*,s.config FROM news_articles a JOIN news_sources s ON s.id=a.source_id
                WHERE a.state IN ('collected','fetching') AND a.next_at<=now() AND s.enabled
                AND s.config->>'use_for_summary'='true' AND a.source_digest=s.config_digest
                ORDER BY a.collected_at,a.id FOR UPDATE OF a SKIP LOCKED LIMIT 1""").fetchone()
            if row:
                conn.execute("""UPDATE news_articles SET state='fetching',attempts=attempts+1,
                    next_at=now()+interval '2 minutes' WHERE id=%s""", (row["id"],))
            return row

    def save_original(self, article, receipt):
        if (article["retrieval"] or {}).get("earliest_publication"):
            receipt = {"earliest_publication": article["retrieval"]["earliest_publication"], **receipt}
        source = NewsSource.model_validate(article["config"])
        ok = (receipt.get("ok") and receipt.get("publisher_host") in article["config"]["article_hosts"]
              and source.allows_article(receipt.get("url", article["url"]))
              and not receipt.get("content_truncated") and len(receipt.get("content", "")) >= 120)
        original_date = timestamp(receipt.get("published_at"))
        published = article["published_at"]
        if published is None and receipt.get("ok"):
            cutoff = timestamp((article["retrieval"] or {}).get("earliest_publication"))
            if not (original_date and cutoff and cutoff <= original_date <= datetime.now(UTC)+timedelta(minutes=5)):
                ok = False
                receipt = {**receipt, "error": "publication_time_unconfirmed_or_old"}
            else:
                published = original_date
        elif published and original_date and abs(original_date-published) > timedelta(days=1):
            ok = False
            receipt = {**receipt, "error": "feed_original_date_conflict"}
        retryable = (receipt.get("error") == "web_network_unavailable"
                     or receipt.get("http_status") in {429, 500, 502, 503, 504})
        failed_state = "collected" if retryable and article["attempts"] < 2 else "fetch_failed"
        with self.db.transaction() as conn:
            conn.execute("""UPDATE news_articles SET state=%s,content=%s,retrieval=%s,error=%s,published_at=%s,
                next_at=now()+interval '5 minutes' WHERE id=%s AND state='fetching'""",
                         ("ready" if ok else failed_state, receipt.get("content", "")[:6000] if ok else None,
                          Jsonb(as_json({k: v for k, v in receipt.items() if k not in {"content", "links"}})),
                          None if ok else receipt.get("error", "original_not_usable"), published, article["id"]))

    def prepare_review(self):
        if not self.authorized():
            return {"state": "paused"}
        self.sync_sources()
        policy = self.policy()
        with self.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(71350227)")
            busy = conn.execute("""SELECT 1 FROM turns t JOIN tasks k ON k.id=t.task_id JOIN projects p ON p.id=k.project_id
                WHERE t.status IN ('queued','running','waiting') AND t.due_at<=now() AND k.revision=p.revision
                AND p.status='active' LIMIT 1""").fetchone()
            if busy or conn.execute("SELECT 1 FROM runtime_control WHERE paused_until>now()").fetchone():
                return {"state": "defer"}
            active = conn.execute("SELECT * FROM news_reviews WHERE state='running' ORDER BY created_at LIMIT 1 FOR UPDATE").fetchone()
            if active and active["policy_digest"] != policy:
                conn.execute("UPDATE news_reviews SET state='stale',completed_at=now() WHERE id=%s", (active["id"],))
                self._stale_articles(conn, active["id"])
                return {"state": "stale"}
            if active:
                if active["next_at"] > datetime.now(UTC):
                    return {"state": "defer"}
                return {"state": "ready", "request": active["request"]}
            # A held item is revisited only when new material arrives, not on an unbounded model timer.
            rows = []
            for state in ("ready", "held"):
                rows.extend(conn.execute("""SELECT a.*,s.config FROM news_articles a JOIN news_sources s ON s.id=a.source_id
                    WHERE a.state=%s AND s.enabled AND s.config->>'use_for_summary'='true'
                    AND a.source_digest=s.config_digest AND a.published_at>=now()-make_interval(hours=>%s)
                    AND a.published_at<=now()+interval '5 minutes'
                    ORDER BY a.collected_at,a.id LIMIT 6""", (state, self.company.settings.news_max_age_hours)).fetchall())
            primary = [r["id"] for r in rows if r["state"] == "ready"]
            if not primary:
                return {"state": "idle"}
            articles = [{"id": r["id"], "url": r["url"], "title": r["title"], "content": r["content"],
                         "excerpt_truncated": bool((r["retrieval"] or {}).get("excerpt_truncated")),
                         "published_at": r["published_at"], "publisher": r["config"]["publisher"],
                         "license_url": r["config"].get("license_url"), "license_name": r["config"].get("license_name", ""),
                         "kind": r["config"]["kind"], "origin_group": r["config"]["origin_group"]} for r in rows]
            for article in articles:
                existing = conn.execute("""SELECT n.id::text FROM news_events n
                    JOIN news_publications p ON p.event_id=n.id
                    JOIN news_articles a ON p.article_ids @> jsonb_build_array(a.id)
                    JOIN projects project ON project.id=n.project_id
                    WHERE a.url=%s AND project.channel=%s AND project.owner_user=%s
                    AND n.updated_at>now()-interval '3 days' ORDER BY n.updated_at DESC LIMIT 1""",
                                        (article["url"], self.company.settings.news_channel_id,
                                         self.company.settings.news_owner_user)).fetchone()
                article["existing_event_id"] = existing["id"] if existing else None
            required_events = [a["existing_event_id"] for a in articles if a["existing_event_id"]]
            events = conn.execute("""SELECT n.id::text,n.headline,n.last_facts,o.status AS root_delivery FROM news_events n
                JOIN outbox o ON o.id=n.root_message_id
                JOIN projects p ON p.id=n.project_id WHERE p.channel=%s AND p.owner_user=%s
                AND n.updated_at>now()-interval '3 days'
                ORDER BY (n.id::text=ANY(%s)) DESC,n.updated_at DESC LIMIT 30""",
                                  (self.company.settings.news_channel_id, self.company.settings.news_owner_user,
                                   required_events)).fetchall()
            bundle = as_json({"primary_ids": primary, "articles": articles, "events": events,
                              "as_of": datetime.now(UTC), "mode": "publish" if self.company.settings.news_publish_enabled else "preview"})
            identity = "news-" + str(uuid4())
            role = self.company.role("reporter")
            request = ProviderRequest(request_id=identity, model=role.model, reasoning_effort=role.reasoning_effort,
                                      prompt=bounded_prompt(bundle))
            conn.execute("INSERT INTO daily_usage(day,reserved) VALUES(CURRENT_DATE,0) ON CONFLICT DO NOTHING")
            used = conn.execute("SELECT reserved FROM daily_usage WHERE day=CURRENT_DATE FOR UPDATE").fetchone()["reserved"]
            cap = effective_limits(conn, self.company)["company"]
            if cap is not None and used >= cap:
                return {"state": "defer"}
            conn.execute("UPDATE daily_usage SET reserved=reserved+1 WHERE day=CURRENT_DATE")
            conn.execute("INSERT INTO news_reviews(id,request,bundle,policy_digest) VALUES(%s,%s,%s,%s)",
                         (identity, Jsonb(request.model_dump()), Jsonb(bundle), policy))
            return {"state": "ready", "request": request.model_dump()}

    @staticmethod
    def _stale_articles(conn, identity):
        # A policy change must not implicitly re-spend on a request whose remote outcome may be unknown.
        conn.execute("""UPDATE news_articles SET state='review_stale',error='review_policy_changed' WHERE id IN
            (SELECT jsonb_array_elements_text(bundle->'primary_ids') FROM news_reviews WHERE id=%s)
            AND state='ready'""", (identity,))

    def fault(self, identity, code, seconds=0):
        with self.db.transaction() as conn:
            if code in {"quota", "busy", "unavailable"}:
                delay = max(60, min(seconds or 300, 604800))
                conn.execute("UPDATE news_reviews SET error=%s,next_at=now()+make_interval(secs=>%s) WHERE id=%s AND state='running'",
                             (code, delay, identity))
            else:
                # Preserve uncertain/invalid calls and their article ownership; do not spend on a fresh ID.
                conn.execute("UPDATE news_reviews SET state='blocked',error=%s,completed_at=now() WHERE id=%s AND state='running'",
                             (code, identity))
                conn.execute("""UPDATE news_articles SET state='review_blocked',error=%s WHERE id IN
                    (SELECT jsonb_array_elements_text(bundle->'primary_ids') FROM news_reviews WHERE id=%s)
                    AND state='ready'""", (code, identity))

    def commit_review(self, response):
        at = datetime.now(UTC)
        policy = self.policy()
        with self.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(71350227)")
            saved = conn.execute("SELECT * FROM news_reviews WHERE id=%s FOR UPDATE", (response.request_id,)).fetchone()
            if not saved:
                raise ValueError("news_unknown_review")
            body = response.model_dump(mode="json")
            if saved["response"] is not None:
                if fingerprint(saved["response"]) != fingerprint(body):
                    raise ValueError("news_committed_response_changed")
                return {"state": saved["state"], "duplicate": True}
            if saved["state"] != "running" or not self.authorized() or saved["policy_digest"] != policy:
                conn.execute("UPDATE news_reviews SET state='stale',response=%s,completed_at=now() WHERE id=%s",
                             (Jsonb(body), response.request_id))
                self._stale_articles(conn, response.request_id)
                return {"state": "stale"}
            if response.provider != self.company.settings.model_provider:
                raise ValueError("news_unexpected_provider")
            review = validate_review(response, saved["bundle"])
            ids = [a["id"] for a in saved["bundle"]["articles"]]
            actual = conn.execute("""SELECT a.*,s.config,s.enabled,s.config_digest FROM news_articles a
                JOIN news_sources s ON s.id=a.source_id WHERE a.id=ANY(%s)""", (ids,)).fetchall()
            articles = {r["id"]: {**r, "publisher": r["config"]["publisher"],
                                   "license_url": r["config"].get("license_url"),
                                   "license_name": r["config"].get("license_name", "")} for r in actual}
            primary = set(saved["bundle"]["primary_ids"])
            results = []
            for index, item in enumerate(review.items):
                state = {"hold": "held", "ignore": "ignored", "publish": "previewed"}[item.disposition]
                selected = [articles[identity] for identity in item.article_ids]
                valid = all(r["enabled"] and r["config"]["use_for_summary"] and r["source_digest"] == r["config_digest"]
                            and r["published_at"] is not None
                            and at-timedelta(hours=self.company.settings.news_max_age_hours) <= r["published_at"] <= at+timedelta(minutes=5)
                            for r in selected)
                if item.disposition == "publish" and conn.execute("""SELECT 1 FROM news_events n
                    JOIN projects p ON p.id=n.project_id WHERE n.last_facts=%s AND p.channel=%s AND p.owner_user=%s
                    AND n.updated_at>now()-interval '3 days' LIMIT 1""",
                                                                  (item.facts, self.company.settings.news_channel_id,
                                                                   self.company.settings.news_owner_user)).fetchone():
                    valid = False
                if item.disposition == "publish" and not valid:
                    state = "held"
                elif item.disposition == "publish" and self.company.settings.news_publish_enabled:
                    state = self._publish(conn, saved, item, index, articles, policy, at)
                conn.execute("UPDATE news_articles SET state=%s WHERE id=ANY(%s) AND state IN ('ready','held')",
                             (state, [identity for identity in item.article_ids if identity in primary or state == "queued"]))
                results.append({"state": state, "headline": item.headline, "reason": item.reason})
            conn.execute("UPDATE news_reviews SET state='completed',response=%s,result=%s,completed_at=now() WHERE id=%s",
                         (Jsonb(body), Jsonb(results), response.request_id))
            return {"state": "completed", "items": results}

    def _publish(self, conn, review, item, index, articles, policy, at):
        event_id = item.event_id or stable(f"news-event:{review['id']}:{index}")
        if item.event_id:
            event = conn.execute("SELECT * FROM news_events WHERE id=%s FOR UPDATE", (event_id,)).fetchone()
            project = self.company._project(conn, str(event["project_id"]))
            root = conn.execute("SELECT status,sent_ts FROM outbox WHERE id=%s", (event["root_message_id"],)).fetchone()
            if not root or root["status"] != "delivered" or not root["sent_ts"] or project["status"] != "active":
                return "held"
            project["thread_ts"] = root["sent_ts"]
        else:
            project = conn.execute("""INSERT INTO projects(id,title,instruction,owner_user,channel)
                VALUES(%s,%s,%s,%s,%s) RETURNING *""",
                                   (stable("news-project:"+event_id), item.headline,
                                    "Reporter 뉴스 사건의 근거와 후속 질문을 관리한다.", self.company.settings.news_owner_user,
                                    self.company.settings.news_channel_id)).fetchone()
            conn.execute("INSERT INTO news_events(id,project_id,headline,last_facts) VALUES(%s,%s,%s,%s)",
                         (event_id, project["id"], item.headline, item.facts))
        message_id = stable(f"news-message:{review['id']}:{index}")
        self.company._message(conn, project, None, "reporter", "news", render(item, articles, at), message_id=message_id)
        expires = min(articles[identity]["published_at"] for identity in item.article_ids) + timedelta(hours=self.company.settings.news_max_age_hours)
        conn.execute("""INSERT INTO news_publications(id,event_id,review_id,article_ids,policy_digest,expires_at)
            VALUES(%s,%s,%s,%s,%s,%s)""", (message_id, event_id, review["id"], Jsonb(item.article_ids), policy, expires))
        if item.event_id and item.priority == "urgent":
            conn.execute("UPDATE news_publications SET broadcast=true WHERE id=%s", (message_id,))
        conn.execute("UPDATE news_events SET root_message_id=COALESCE(root_message_id,%s),headline=%s,last_facts=%s,updated_at=%s WHERE id=%s",
                     (message_id, item.headline, item.facts, at, event_id))
        # Follow-up user questions can read the exact originals associated with this event.
        for identity in item.article_ids:
            article = articles[identity]
            conn.execute("""INSERT INTO sources(id,title,uri,content,available_at,approved,project_id,synthetic)
                VALUES(%s,%s,%s,%s,%s,true,%s,false) ON CONFLICT DO NOTHING""",
                         (f"news:{event_id}:{identity[:16]}", article["title"], article["url"], article["content"],
                          at, project["id"]))
        return "queued"

    def delivery_allowed(self, conn, row):
        publication = conn.execute("SELECT * FROM news_publications WHERE id=%s", (row["id"],)).fetchone()
        return bool(publication and self.authorized() and self.company.settings.news_publish_enabled
                    and publication["policy_digest"] == self.policy() and publication["expires_at"] > datetime.now(UTC)
                    and row["channel"] == self.company.settings.news_channel_id)

    def status(self):
        with self.db.transaction() as conn:
            return as_json({"enabled": self.company.settings.company_news_enabled,
                            "publish_enabled": self.company.settings.news_publish_enabled,
                            "channel": self.company.settings.news_channel_id,
                            "sources": conn.execute("SELECT id,enabled,last_success,last_attempt,failures,error,receipt FROM news_sources ORDER BY id").fetchall(),
                            "articles": conn.execute("SELECT state,count(*) AS count FROM news_articles GROUP BY state ORDER BY state").fetchall(),
                            "deliveries": conn.execute("""SELECT o.id::text,o.status,o.error,o.sent_ts,o.created_at
                                FROM outbox o JOIN news_publications p ON p.id=o.id ORDER BY o.created_at DESC LIMIT 20""").fetchall(),
                            "reviews": conn.execute("SELECT id,state,error,result,created_at,completed_at FROM news_reviews ORDER BY created_at DESC LIMIT 10").fetchall()})

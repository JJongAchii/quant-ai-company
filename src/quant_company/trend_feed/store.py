"""Frozen daily inputs, bounded requests and one committed outbox outcome per slot."""

from datetime import timedelta
from itertools import pairwise
from uuid import uuid4

from psycopg.types.json import Jsonb

from ..company import as_json, fingerprint, stable
from ..contracts import ProviderRequest
from ..news.contracts import load_sources
from ..news.feeds import timestamp
from ..owner_controls import effective_limits
from . import schedule
from .contracts import GOOGLE_CANDIDATE_LIMIT, TREND_FEED_AGENT
from .editor import EDITORIAL_POLICY_VERSION, prompt, publication_items, render, response_json, validate_draft
from .sources import trend_summary
from .supplement import major_issues

LOCK = 71351006
FINAL = {"queued", "preview", "expired", "stale"}


class TrendFeedStore:
    def __init__(self, company):
        self.company, self.db = company, company.db

    def authorized(self):
        s = self.company.settings
        role = self.company.roles.get(TREND_FEED_AGENT)
        return (s.trend_feed_enabled and s.trend_feed_owner_user in s.slack_allowed_users
                and s.trend_feed_channel_id in s.slack_allowed_channels
                and s.trend_feed_channel_id.startswith(("C", "G")) and role is not None
                and not (role.active or role.tools or role.can_delegate_to)
                and s.trend_feed_channel_id not in {s.news_channel_id, s.tech_feed_channel_id,
                    s.quant_feed_channel_id, s.housing_feed_channel_id, s.data_watch_channel_id,
                    s.improvements_channel_id, s.model_accounts_channel_id})

    def policy(self):
        s = self.company.settings
        reporter = self.company.roles.get("reporter")
        return fingerprint({"version": 1, "enabled": s.trend_feed_enabled, "publish": s.trend_feed_publish_enabled,
            "editorial_policy": EDITORIAL_POLICY_VERSION,
            "channel": s.trend_feed_channel_id, "owner": s.trend_feed_owner_user,
            "supplement_channel": s.news_channel_id, "supplement_owner": s.news_owner_user,
            "users": [s.trend_feed_owner_user] if s.trend_feed_owner_user in s.slack_allowed_users else [],
            "channels": [s.trend_feed_channel_id] if s.trend_feed_channel_id in s.slack_allowed_channels else [],
            "naver": s.trend_feed_naver_enabled, "model": reporter.model if reporter else None,
            "effort": reporter.reasoning_effort if reporter else None,
            "sources": [source.model_dump() for source in load_sources(s.news_sources_file)],
            "publication_hours": s.trend_feed_publication_hours,
            "on_demand": s.trend_feed_on_demand_enabled, "model_daily_limit": s.trend_feed_model_daily_limit})

    def claim_source(self):
        if not self.authorized():
            return None
        at = schedule.utcnow()
        with self.db.transaction() as conn:
            row = conn.execute("""SELECT * FROM trend_feed_source WHERE id=1 AND next_at<=%s
                AND (lease_until IS NULL OR lease_until<=%s) FOR UPDATE SKIP LOCKED""", (at, at)).fetchone()
            if not row:
                return None
            row["lease_token"] = uuid4()
            conn.execute("UPDATE trend_feed_source SET lease_token=%s,lease_until=%s,last_attempt=%s WHERE id=1",
                         (row["lease_token"], at + timedelta(minutes=2), at))
            return row

    def save_snapshot(self, claimed, receipt):
        at = schedule.utcnow()
        with self.db.transaction() as conn:
            current = conn.execute("SELECT * FROM trend_feed_source WHERE id=1 FOR UPDATE").fetchone()
            if current["lease_token"] != claimed["lease_token"] or not self.authorized():
                return {"state": "stale"}
            receipt = dict(receipt)
            if receipt.get("not_modified"):
                prior = conn.execute("""SELECT receipt FROM trend_feed_snapshots WHERE ok
                    AND receipt ? 'raw_xml' ORDER BY observed_at DESC,id LIMIT 1""").fetchone()
                if prior:
                    receipt["entries"] = prior["receipt"]["entries"]
                else:
                    receipt = {"ok": False, "error": "not_modified_without_baseline"}
            identity = uuid4()
            ok = bool(receipt.get("ok"))
            conn.execute("INSERT INTO trend_feed_snapshots(id,observed_at,ok,receipt) VALUES(%s,%s,%s,%s)",
                         (identity, at, ok, Jsonb(as_json(receipt))))
            count = 0
            if ok:
                for item in receipt.get("entries", []):
                    published = timestamp(as_json(item["published_at"]))
                    if not published or not at - timedelta(days=7) <= published <= at + timedelta(minutes=5):
                        continue
                    conn.execute("""INSERT INTO trend_feed_keywords(keyword,first_seen,last_seen) VALUES(%s,%s,%s)
                        ON CONFLICT(keyword) DO UPDATE SET last_seen=excluded.last_seen""", (item["keyword"], at, at))
                    row = conn.execute("""INSERT INTO trend_feed_observations(snapshot_id,keyword,observed_at,item)
                        VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING keyword""",
                                       (identity, item["keyword"], at, Jsonb(as_json(item)))).fetchone()
                    count += bool(row)
            delay = 600 if ok else min(3600, 600 * 2 ** min(current["failures"], 3))
            conn.execute("""UPDATE trend_feed_source SET lease_token=NULL,lease_until=NULL,next_at=%s,
                failures=%s,error=%s,started_at=CASE WHEN %s THEN COALESCE(started_at,%s) ELSE started_at END,
                last_success=CASE WHEN %s THEN %s ELSE last_success END,
                etag=CASE WHEN %s THEN COALESCE(%s,etag) ELSE etag END,
                modified=CASE WHEN %s THEN COALESCE(%s,modified) ELSE modified END WHERE id=1""",
                         (at + timedelta(seconds=delay), 0 if ok else current["failures"] + 1,
                          None if ok else receipt.get("error", "rss_failed"), ok, at, ok, at,
                          ok, receipt.get("etag"), ok, receipt.get("modified")))
            return {"state": "collected" if ok else "source_error", "observations": count}

    def bundle(self, conn, cutoff, *, latest_only=False):
        start = cutoff - timedelta(days=1)
        rows = conn.execute("""SELECT o.*,k.first_seen FROM trend_feed_observations o
            JOIN trend_feed_keywords k ON k.keyword=o.keyword WHERE observed_at>=%s AND observed_at<%s
            ORDER BY observed_at, snapshot_id,keyword""", (start, cutoff)).fetchall()
        if latest_only:
            latest = conn.execute("""SELECT id FROM trend_feed_snapshots WHERE ok AND observed_at<%s
                ORDER BY observed_at DESC LIMIT 1""", (cutoff,)).fetchone()
            rows = [row for row in rows if latest and row["snapshot_id"] == latest["id"]]
        candidates = {}
        for row in rows:
            item = row["item"]
            c = candidates.get(row["keyword"])
            if c is None:
                c = {"id": fingerprint(row["keyword"])[:24], "kind": "rising_search", "title": item["title"], "traffic": "",
                     "traffic_floor": None, "peak_snapshot": str(row["snapshot_id"]),
                     "first_seen": row["first_seen"], "new": row["first_seen"] >= start,
                     "articles": [], "naver": {"state": "unavailable", "reason": "not_checked"}}
                candidates[row["keyword"]] = c
            if ((item["traffic_floor"] if item["traffic_floor"] is not None else -1)
                    > (c["traffic_floor"] if c["traffic_floor"] is not None else -1)):
                c.update(traffic=item["traffic"], traffic_floor=item["traffic_floor"],
                         peak_snapshot=str(row["snapshot_id"]))
            c.update(last_seen=row["observed_at"], news=item["news"], latest_snapshot=str(row["snapshot_id"]))
        ordered = sorted(candidates.values(), key=lambda c: (-(c["traffic_floor"] or 0),
                         -c["last_seen"].timestamp(), c["title"]))[:GOOGLE_CANDIDATE_LIMIT]
        supplements = major_issues(conn, self.company.settings, cutoff)
        source = conn.execute("SELECT started_at FROM trend_feed_source WHERE id=1").fetchone()
        successes = [r["observed_at"] for r in conn.execute("""SELECT observed_at FROM trend_feed_snapshots
            WHERE ok AND observed_at>=%s AND observed_at<%s ORDER BY observed_at""", (start, cutoff)).fetchall()]
        coverage = max(start, source["started_at"]) if source["started_at"] else start
        boundaries = [min(coverage, cutoff), *successes, cutoff]
        gap = not successes or any(b - a > timedelta(minutes=30) for a, b in pairwise(boundaries))
        return as_json({"cutoff": cutoff, "candidate_scope": "latest_rss" if latest_only else "rolling_24h",
                        "candidates": ordered + supplements, "coverage_start": source["started_at"],
                        "partial_history": not source["started_at"] or source["started_at"] > start,
                        "last_success": successes[-1] if successes else None, "collection_gap": gap,
                        "snapshot_ids": sorted({c[k] for c in ordered for k in ("peak_snapshot", "latest_snapshot")})})

    def freeze(self):
        if not self.authorized():
            return None
        at = schedule.utcnow()
        cutoff, send, expiry = schedule.slot_times(at, self.company.settings.trend_feed_publication_hours)
        if at < cutoff:
            return None
        s = self.company.settings
        slot = str(send.date()) if send.hour == 8 else f"{send.date()}T{send.hour:02}:00"
        identity = stable(f"trend-feed:{slot}:KR:{s.trend_feed_channel_id}")
        with self.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
            row = conn.execute("SELECT * FROM trend_feed_digests WHERE id=%s", (identity,)).fetchone()
            if row:
                if row["state"] not in FINAL and at >= row["expires_at"]:
                    row = conn.execute("UPDATE trend_feed_digests SET state='expired' WHERE id=%s RETURNING *",
                                       (identity,)).fetchone()
                return row
            bundle = self.bundle(conn, cutoff, latest_only=len(s.trend_feed_publication_hours) > 1)
            bundle["publication_at"] = send.isoformat()
            return conn.execute("""INSERT INTO trend_feed_digests
                (id,day,channel,owner_user,cutoff,send_at,expires_at,policy_digest,state,bundle)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (identity, send.date(), s.trend_feed_channel_id, s.trend_feed_owner_user, cutoff, send, expiry,
                 self.policy(), "expired" if at >= expiry else "preparing", Jsonb(bundle))).fetchone()

    def request(self, event_key, *, owner, channel, thread_ts):
        s, at = self.company.settings, schedule.utcnow()
        if not (self.authorized() and s.trend_feed_publish_enabled and s.trend_feed_on_demand_enabled
                and owner == s.trend_feed_owner_user and channel == s.trend_feed_channel_id):
            return {"ok": True, "ignored": True, "reason": "trend_request_scope"}
        identity = stable("trend-request:" + event_key)
        with self.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
            existing = conn.execute("SELECT id,state FROM trend_feed_digests WHERE event_key=%s", (event_key,)).fetchone()
            if existing:
                return {"ok": True, "duplicate": True, "id": str(existing["id"]), "state": existing["state"]}
            if conn.execute("SELECT 1 FROM runtime_control WHERE paused_until>%s", (at,)).fetchone():
                return {"ok": True, "ignored": True, "reason": "company_paused"}
            cached = conn.execute("""SELECT * FROM trend_feed_digests WHERE draft IS NOT NULL AND enriched
                AND state IN ('queued','preview') AND policy_digest=%s AND cutoff>=%s AND cutoff<=%s
                ORDER BY cutoff DESC LIMIT 1""", (self.policy(), at - timedelta(minutes=10), at)).fetchone()
            bundle = dict(cached["bundle"]) if cached else self.bundle(conn, at, latest_only=True)
            bundle.update(on_demand=True, requested_at=at.isoformat(), refresh_pending=not bool(cached))
            conn.execute("""INSERT INTO trend_feed_digests(id,day,channel,owner_user,cutoff,send_at,expires_at,
                policy_digest,bundle,draft,enriched,kind,event_key,thread_ts,requested_at,reused_digest_id)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'on_demand',%s,%s,%s,%s)""",
                (identity, at.astimezone(schedule.KST).date(), channel, owner,
                 cached["cutoff"] if cached else at, at + timedelta(minutes=8), at + timedelta(minutes=15),
                 self.policy(), Jsonb(bundle), Jsonb(cached["draft"]) if cached else None, bool(cached),
                 event_key, thread_ts, at, cached["id"] if cached else None))
            if not cached:
                conn.execute("UPDATE trend_feed_source SET next_at=LEAST(next_at,%s) WHERE id=1", (at,))
            return {"ok": True, "trend_request": True, "id": str(identity), "cached": bool(cached)}

    def claim_enrichment(self):
        self.freeze()
        if not self.authorized():
            return None
        at = schedule.utcnow()
        with self.db.transaction() as conn:
            token = uuid4()
            row = conn.execute("""SELECT * FROM trend_feed_digests WHERE state='preparing' AND NOT enriched
                AND send_at>%s AND policy_digest=%s AND (lease_until IS NULL OR lease_until<=%s)
                ORDER BY (kind='scheduled') DESC,send_at FOR UPDATE SKIP LOCKED LIMIT 1""",
                (at, self.policy(), at)).fetchone()
            if not row:
                return None
            if row["bundle"].get("refresh_pending"):
                source = conn.execute("SELECT * FROM trend_feed_source WHERE id=1").fetchone()
                if (not source["last_attempt"] or source["last_attempt"] < row["requested_at"]
                        or source["lease_until"] and source["lease_until"] > at):
                    return None
                bundle = self.bundle(conn, at, latest_only=True)
                bundle.update(on_demand=True, requested_at=row["requested_at"].isoformat())
                conn.execute("UPDATE trend_feed_digests SET cutoff=%s,bundle=%s WHERE id=%s",
                             (at, Jsonb(bundle), row["id"]))
            return conn.execute("""UPDATE trend_feed_digests SET lease_token=%s,lease_until=%s
                WHERE id=%s RETURNING *""", (token, at + timedelta(minutes=10), row["id"])).fetchone()

    def save_enrichment(self, claimed, bundle):
        with self.db.transaction() as conn:
            conn.execute("""UPDATE trend_feed_digests SET bundle=%s,enriched=true,lease_until=NULL,lease_token=NULL
                WHERE id=%s AND lease_token=%s AND state='preparing' AND policy_digest=%s""",
                         (Jsonb(as_json(bundle)), claimed["id"], claimed["lease_token"], self.policy()))

    def naver(self, digest_id, kind, payload, client):
        identity = fingerprint([str(digest_id), kind, payload])
        day = schedule.utcnow().astimezone(schedule.KST).date()
        with self.db.transaction() as conn:
            cached = conn.execute("SELECT receipt FROM trend_feed_api_receipts WHERE id=%s", (identity,)).fetchone()
            if cached:
                return cached["receipt"]
            if not client.credentials():
                return {"ok": False, "error": "naver_not_configured"}
            conn.execute("INSERT INTO trend_feed_api_usage(day) VALUES(%s) ON CONFLICT DO NOTHING", (day,))
            granted = conn.execute("UPDATE trend_feed_api_usage SET calls=calls+1 WHERE day=%s AND calls<100 RETURNING calls",
                                   (day,)).fetchone()
            if not granted:
                return {"ok": False, "error": "naver_daily_cap"}
        receipt = client.request(kind, payload)
        with self.db.transaction() as conn:
            conn.execute("""INSERT INTO trend_feed_api_receipts(id,digest_id,kind,request,receipt)
                VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                         (identity, digest_id, kind, Jsonb(payload), Jsonb(as_json(receipt))))
        return receipt

    def cached_trends(self, digest_id, candidates, payload):
        """Reuse a complete original response, never splice its normalized series."""
        found = {}
        with self.db.transaction() as conn:
            receipts = conn.execute("""SELECT * FROM trend_feed_api_receipts WHERE kind='trend'
                AND receipt->>'ok'='true' AND NOT (receipt ? 'cache_source_id') AND request->>'endDate'=%s
                ORDER BY checked_at DESC LIMIT 100""", (payload["endDate"],)).fetchall()
            for receipt in receipts:
                if any(receipt["request"].get(key) != payload.get(key) for key in ("startDate", "endDate", "timeUnit")):
                    continue
                results = receipt["receipt"].get("data", {}).get("results", [])
                if not isinstance(results, list):
                    continue
                reused = False
                for candidate in candidates:
                    matches = [result for result in results if isinstance(result, dict)
                               and result.get("title") == candidate["id"] and result.get("keywords") == [candidate["title"]]]
                    if candidate["id"] in found or len(matches) != 1:
                        continue
                    summary = trend_summary(matches[0], receipt["request"])
                    if summary.get("state") == "available":
                        found[candidate["id"]] = summary
                        reused = True
                if reused:
                    identity = fingerprint([str(digest_id), "cached-trend", receipt["id"]])
                    conn.execute("""INSERT INTO trend_feed_api_receipts(id,digest_id,kind,request,receipt)
                        VALUES(%s,%s,'trend',%s,%s) ON CONFLICT DO NOTHING""",
                        (identity, digest_id, Jsonb(receipt["request"]),
                         Jsonb({**receipt["receipt"], "cache_source_id": receipt["id"]})))
        return found

    def prepare_call(self):
        if not self.authorized() or "reporter" not in self.company.roles:
            return None
        at = schedule.utcnow()
        with self.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
            if conn.execute("SELECT 1 FROM runtime_control WHERE paused_until>%s", (at,)).fetchone():
                return None
            conn.execute("""UPDATE trend_feed_calls SET state='blocked',error='uncertain',completed_at=%s
                WHERE state='running' AND lease_until<=%s""", (at, at))
            row = conn.execute("""SELECT * FROM trend_feed_digests WHERE state='preparing' AND enriched
                AND draft IS NULL AND send_at>%s AND policy_digest=%s
                ORDER BY (kind='scheduled') DESC,send_at LIMIT 1 FOR UPDATE""",
                               (at, self.policy())).fetchone()
            if not row or not row["bundle"]["candidates"]:
                return None
            calls = conn.execute("SELECT * FROM trend_feed_calls WHERE digest_id=%s ORDER BY stage", (row["id"],)).fetchall()
            if calls and calls[-1]["state"] == "queued" and calls[-1]["next_at"] <= at:
                call = conn.execute("""UPDATE trend_feed_calls SET state='running',lease_until=%s
                    WHERE id=%s RETURNING *""", (at + timedelta(minutes=12), calls[-1]["id"])).fetchone()
                return call
            if calls and (len(calls) >= 2 or calls[-1]["state"] != "invalid"):
                return None
            # Share the company's existing DB-day reservation convention.
            day = conn.execute("SELECT CURRENT_DATE AS day").fetchone()["day"]
            usage = conn.execute("""SELECT count(*) AS n,count(*) FILTER(WHERE d.kind='on_demand') AS requests
                            FROM trend_feed_calls c JOIN trend_feed_digests d ON d.id=c.digest_id
                            WHERE c.created_at>=%s AND c.created_at<%s""",
                            (at.astimezone(schedule.KST).replace(hour=0, minute=0, second=0, microsecond=0),
                             at.astimezone(schedule.KST).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1))).fetchone()
            settings = self.company.settings
            request_limit = max(0, settings.trend_feed_model_daily_limit - 2*len(settings.trend_feed_publication_hours))
            if (usage["n"] >= settings.trend_feed_model_daily_limit
                    or row["kind"] == "on_demand" and usage["requests"] >= request_limit):
                conn.execute("UPDATE trend_feed_digests SET bundle=bundle||%s WHERE id=%s",
                             (Jsonb({"editorial_notice": "오늘의 요청 편집 한도에 도달해 미분류 주제를 생략합니다."}), row["id"]))
                return None
            conn.execute("INSERT INTO daily_usage(day,reserved) VALUES(%s,0) ON CONFLICT DO NOTHING", (day,))
            used = conn.execute("SELECT reserved FROM daily_usage WHERE day=%s FOR UPDATE", (day,)).fetchone()["reserved"]
            cap = effective_limits(conn, self.company)["company"]
            if cap is not None and used >= cap:
                return None
            role = self.company.roles["reporter"]
            request = ProviderRequest(request_id="news-trends-" + str(uuid4()), model=role.model,
                                      reasoning_effort=role.reasoning_effort,
                                      prompt=prompt(row["bundle"], calls[-1]["error"] if calls else None))
            from ..model_policy import bind

            request = bind(self.company, conn, request, "reporter")
            conn.execute("UPDATE daily_usage SET reserved=reserved+1 WHERE day=%s", (day,))
            return conn.execute("""INSERT INTO trend_feed_calls(id,digest_id,stage,request,lease_until,created_at)
                VALUES(%s,%s,%s,%s,%s,%s) RETURNING *""", (request.request_id, row["id"], len(calls),
                Jsonb(request.model_dump()), at + timedelta(minutes=12), at)).fetchone()

    def finish_call(self, call, response):
        with self.db.transaction() as conn:
            current = conn.execute("SELECT * FROM trend_feed_calls WHERE id=%s FOR UPDATE", (call["id"],)).fetchone()
            if current["state"] not in {"running", "blocked"} or current["response"] is not None:
                return
            digest = conn.execute("SELECT * FROM trend_feed_digests WHERE id=%s FOR UPDATE", (call["digest_id"],)).fetchone()
            try:
                if response.request_id != call["id"]:
                    conn.execute("""UPDATE trend_feed_calls SET response=%s,state='blocked',
                        error='request_mismatch',completed_at=%s WHERE id=%s""",
                                 (Jsonb(response_json(response)), schedule.utcnow(), call["id"]))
                    return
                draft = validate_draft(response, digest["bundle"]).model_dump()
                state, error = "completed", None
            except ValueError as exc:
                draft, state, error = None, "invalid", str(exc).split("\n")[0][:160]
            conn.execute("UPDATE trend_feed_calls SET response=%s,state=%s,error=%s,completed_at=%s WHERE id=%s",
                         (Jsonb(response_json(response)), state, error, schedule.utcnow(), call["id"]))
            if draft and digest["state"] == "preparing" and digest["policy_digest"] == self.policy():
                conn.execute("UPDATE trend_feed_digests SET draft=%s WHERE id=%s", (Jsonb(draft), digest["id"]))

    def fault_call(self, call, code, seconds=0):
        retry = code in {"busy", "quota", "unavailable"}
        at = schedule.utcnow()
        with self.db.transaction() as conn:
            conn.execute("""UPDATE trend_feed_calls SET state=%s,error=%s,next_at=%s,lease_until=NULL
                WHERE id=%s AND state='running'""", ("queued" if retry else "blocked", code,
                at + timedelta(seconds=max(60, min(seconds or 60, 86400))), call["id"]))

    def finalize(self):
        current_slot = self.freeze()
        if not self.authorized():
            return {"state": "paused"}
        at = schedule.utcnow()
        with self.db.transaction() as conn:
            row = conn.execute("""SELECT * FROM trend_feed_digests WHERE state='preparing'
                AND (send_at<=%s OR kind='on_demand' AND draft IS NOT NULL AND enriched)
                ORDER BY (kind='scheduled') DESC,send_at FOR UPDATE SKIP LOCKED LIMIT 1""", (at,)).fetchone()
            if not row:
                return {"state": current_slot["state"] if current_slot else "idle"}
            state = ("expired" if at >= row["expires_at"] else
                     "stale" if row["policy_digest"] != self.policy() or not self.authorized() else None)
            if state:
                conn.execute("UPDATE trend_feed_digests SET state=%s WHERE id=%s", (state, row["id"]))
                return {"state": state}
            text = render(row["bundle"], row["draft"])
            publish = self.company.settings.trend_feed_publish_enabled
            if publish:
                project_id = stable(f"trend-feed-project:{row['channel']}:{row['owner_user']}")
                conn.execute("""INSERT INTO projects(id,title,instruction,owner_user,channel)
                    VALUES(%s,'search-trends','Korean daily search-interest briefing',%s,%s) ON CONFLICT DO NOTHING""",
                             (project_id, row["owner_user"], row["channel"]))
                project = conn.execute("SELECT * FROM projects WHERE id=%s", (project_id,)).fetchone()
                self.company._message(conn, project, None, TREND_FEED_AGENT, "trend_feed", text, message_id=str(row["id"]))
                conn.execute("UPDATE outbox SET next_at=%s,thread_ts=%s WHERE id=%s",
                             (at if row["kind"] == "on_demand" else row["send_at"], row["thread_ts"], row["id"]))
            state = "queued" if publish else "preview"
            conn.execute("UPDATE trend_feed_digests SET state=%s,content=%s WHERE id=%s", (state, text, row["id"]))
            return {"state": state, "id": str(row["id"]), "items": len(publication_items(row["draft"], row["bundle"]))}

    def gate(self, conn, row, *, claimed=False):
        at = schedule.utcnow()
        digest = conn.execute("SELECT * FROM trend_feed_digests WHERE id=%s", (row["id"],)).fetchone()
        if not (digest and digest["state"] == "queued" and self.authorized()
                and self.company.settings.trend_feed_publish_enabled and digest["policy_digest"] == self.policy()
                and digest["expires_at"] > at and row["channel"] == digest["channel"]
                and row["agent"] == TREND_FEED_AGENT):
            conn.execute("UPDATE outbox SET status='stale',error='trend_policy_or_freshness_changed' WHERE id=%s", (row["id"],))
            return False
        paused = conn.execute("SELECT 1 FROM runtime_control WHERE paused_until>%s", (at,)).fetchone()
        if digest["kind"] == "scheduled" and at < digest["send_at"] or paused:
            conn.execute("""UPDATE outbox SET status='pending',next_at=%s,started_at=NULL,
                attempts=GREATEST(0,attempts-%s) WHERE id=%s""",
                         (max(digest["send_at"], at + timedelta(minutes=1)), int(claimed), row["id"]))
            return False
        return True

    def preview(self):
        at = schedule.utcnow()
        cutoff, _, _ = schedule.slot_times(at, self.company.settings.trend_feed_publication_hours)
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM trend_feed_digests WHERE channel=%s ORDER BY created_at DESC,id DESC LIMIT 1",
                               (self.company.settings.trend_feed_channel_id,)).fetchone()
            bundle = row["bundle"] if row else self.bundle(conn, min(cutoff, at),
                latest_only=len(self.company.settings.trend_feed_publication_hours) > 1)
            return {"mode": "preview", "slack": "not-called", "model": "not-called",
                    "text": row["content"] if row and row["content"] else render(bundle, row["draft"] if row else None)}

    def cleanup(self):
        with self.db.transaction() as conn:
            conn.execute("""DELETE FROM trend_feed_snapshots s WHERE observed_at<%s AND NOT EXISTS (
                SELECT 1 FROM trend_feed_digests d WHERE d.bundle->'snapshot_ids' ? s.id::text)""",
                         (schedule.utcnow() - timedelta(days=90),))

    def status(self):
        at = schedule.utcnow()
        with self.db.transaction() as conn:
            samples = conn.execute("""SELECT count(*) AS attempts,count(*) FILTER(WHERE ok) AS successes
                FROM trend_feed_snapshots WHERE observed_at>=%s""", (at - timedelta(days=1),)).fetchone()
            return as_json({"enabled": self.company.settings.trend_feed_enabled, "authorized": self.authorized(),
                "publish_enabled": self.company.settings.trend_feed_publish_enabled,
                "schedule": "daily " + ", ".join(f"{h:02}:00" for h in self.company.settings.trend_feed_publication_hours)
                            + " Asia/Seoul; cutoff30 minutes before; expiry60 minutes after",
                "next_publication": schedule.next_slot(at, self.company.settings.trend_feed_publication_hours),
                "on_demand_enabled": self.company.settings.trend_feed_on_demand_enabled,
                "model_daily_limit": self.company.settings.trend_feed_model_daily_limit, "last_24h": samples,
                "source": conn.execute("SELECT * FROM trend_feed_source WHERE id=1").fetchone(),
                "naver_usage": conn.execute("SELECT * FROM trend_feed_api_usage ORDER BY day DESC LIMIT 7").fetchall(),
                "model_calls": conn.execute("""SELECT id,stage,state,error,created_at,completed_at FROM trend_feed_calls
                    ORDER BY created_at DESC LIMIT 14""").fetchall(),
                "digests": conn.execute("""SELECT d.id,d.day,d.kind,d.state,d.enriched,d.cutoff,d.send_at,d.expires_at,
                    d.requested_at,d.reused_digest_id,
                    o.status AS delivery_status,o.error,o.sent_ts,
                    extract(epoch FROM (o.started_at-d.send_at)) AS delivery_delay_seconds
                    FROM trend_feed_digests d LEFT JOIN outbox o ON o.id=d.id
                    ORDER BY d.created_at DESC,d.send_at DESC LIMIT 10""").fetchall()})

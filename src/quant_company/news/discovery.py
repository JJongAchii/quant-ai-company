"""Bounded subscription search discovers candidates; the collector verifies originals."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from psycopg.types.json import Jsonb

from ..company import as_json, fingerprint
from ..contracts import ProviderRequest
from ..owner_controls import effective_limits
from ..web_tools import search_prompt, search_result
from .contracts import NEWS_TOPICS
from .feeds import canonical_url
from .store import NewsStore

TOPIC_QUERIES = {
    "economy_finance": "major economy finance markets banking Korea international news",
    "macroeconomics": "inflation interest rates employment GDP central banks Korea world macroeconomics news",
    "geopolitics": "major diplomacy elections conflict sanctions Korea international geopolitics news",
    "trade_energy": "trade tariffs oil energy commodities shipping supply chains Korea international news",
    "industry_technology": "major companies semiconductors AI technology industry Korea international news",
    "world_events": "major world events disasters public health infrastructure Korea international news",
}


class NewsDiscoveryStore(NewsStore):
    def allowed(self):
        return (self.authorized() and self.company.settings.company_web_enabled
                and self.company.settings.news_search_enabled)

    def search_policy(self):
        return fingerprint([self.policy(), self.company.settings.company_web_enabled,
                            self.company.settings.news_search_enabled, "discovery-v1"])

    def prepare(self):
        if not self.allowed():
            return {"state": "paused"}
        self.sync_sources()
        policy = self.search_policy()
        with self.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(71350227)")
            if (conn.execute("SELECT 1 FROM runtime_control WHERE paused_until>now()").fetchone()
                    or conn.execute("""SELECT 1 FROM turns t JOIN tasks k ON k.id=t.task_id
                        JOIN projects p ON p.id=k.project_id WHERE t.status IN ('queued','running','waiting')
                        AND t.due_at<=now() AND k.revision=p.revision AND p.status='active' LIMIT 1""").fetchone()):
                return {"state": "defer"}
            active = conn.execute("""SELECT * FROM news_searches WHERE state IN ('running','blocked')
                ORDER BY created_at LIMIT 1 FOR UPDATE""").fetchone()
            if active:
                # An ambiguous remote call cannot be replaced by another request ID, even after a policy edit.
                if active["state"] == "blocked":
                    return {"state": "blocked", "reason": active["error"]}
                if active["policy_digest"] != policy:
                    conn.execute("UPDATE news_searches SET state='blocked',error='search_policy_changed' WHERE id=%s", (active["id"],))
                    return {"state": "blocked", "reason": "search_policy_changed"}
                if active["next_at"] > datetime.now(UTC):
                    return {"state": "defer"}
                return {"state": "ready", "request": active["request"]}
            latest = conn.execute("SELECT max(completed_at) AS at,count(*) AS n FROM news_searches").fetchone()
            if latest["at"] and latest["at"] > datetime.now(UTC)-timedelta(minutes=30):
                return {"state": "idle"}
            topic = NEWS_TOPICS[latest["n"] % len(NEWS_TOPICS)]
            sources = [s for s in self.sources().values() if s.enabled and s.use_for_summary and s.kind == "media"]
            if not sources:
                return {"state": "idle"}
            held = conn.execute("""SELECT a.id,a.title FROM news_articles a JOIN news_sources s ON s.id=a.source_id
                WHERE a.state='held' AND s.enabled AND a.source_digest=s.config_digest
                AND a.published_at>=now()-make_interval(hours=>%s)
                AND NOT EXISTS (SELECT 1 FROM news_searches n WHERE n.created_at>now()-interval '6 hours'
                    AND n.arguments->'held_ids' @> jsonb_build_array(a.id))
                ORDER BY a.collected_at,a.id LIMIT 2""", (self.company.settings.news_max_age_hours,)).fetchall()
            at = datetime.now(UTC)
            hosts = sorted({host for s in sources for host in s.article_hosts})
            query = (TOPIC_QUERIES[topic] + "; recent 24 hours as of " + at.isoformat()
                     + "; only news articles from " + ", ".join(hosts)
                     + "; Korean and English searches; at most 3 native searches; do not open pages. "
                     + "Also seek independent reporting for: " + "; ".join(r["title"][:120] for r in held))[:1000]
            args = {"query": query, "limit": 6}
            role = self.company.role("reporter")
            request = ProviderRequest(request_id="news-search-"+str(uuid4()), model=role.model,
                                      reasoning_effort=role.reasoning_effort, web_search=True, prompt=search_prompt(args))
            conn.execute("INSERT INTO daily_usage(day,reserved) VALUES(CURRENT_DATE,0) ON CONFLICT DO NOTHING")
            used = conn.execute("SELECT reserved FROM daily_usage WHERE day=CURRENT_DATE FOR UPDATE").fetchone()["reserved"]
            cap = effective_limits(conn, self.company)["company"]
            if cap is not None and used >= cap:
                return {"state": "defer"}
            conn.execute("UPDATE daily_usage SET reserved=reserved+1 WHERE day=CURRENT_DATE")
            args["held_ids"] = [r["id"] for r in held]
            conn.execute("""INSERT INTO news_searches(id,topic,arguments,request,policy_digest)
                VALUES(%s,%s,%s,%s,%s)""", (request.request_id, topic, Jsonb(args), Jsonb(request.model_dump()), policy))
            return {"state": "ready", "request": request.model_dump()}

    def commit(self, response):
        with self.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(71350227)")
            saved = conn.execute("SELECT * FROM news_searches WHERE id=%s FOR UPDATE", (response.request_id,)).fetchone()
            if not saved:
                raise ValueError("news_unknown_search")
            body = response.model_dump(mode="json")
            if saved["response"] is not None:
                if fingerprint(saved["response"]) != fingerprint(body):
                    raise ValueError("news_committed_search_changed")
                return {"state": saved["state"], "duplicate": True}
            if saved["state"] != "running" or not self.allowed() or saved["policy_digest"] != self.search_policy():
                conn.execute("UPDATE news_searches SET state='stale',response=%s,completed_at=now() WHERE id=%s",
                             (Jsonb(body), response.request_id))
                return {"state": "stale"}
            if response.provider != self.company.settings.model_provider:
                raise ValueError("news_unexpected_search_provider")
            receipt = search_result(response, saved["arguments"])
            added = 0
            excluded = 0
            if receipt["ok"]:
                for candidate in receipt["results"]:
                    url = canonical_url(candidate["url"])
                    sources = [s for s in self.sources().values() if s.enabled and s.use_for_summary
                               and s.kind == "media" and s.allows_article(url)]
                    if not sources:
                        excluded += 1
                        continue
                    if conn.execute("SELECT 1 FROM news_articles WHERE url=%s LIMIT 1", (url,)).fetchone():
                        continue
                    source = next((s for s in sources if saved["topic"] in s.topics), sources[0])
                    digest = fingerprint(["search-candidate", url])
                    identity = fingerprint(["media:"+source.origin_group, url, digest])
                    seed = {"discovery_request_id": saved["id"], "earliest_publication":
                            (saved["created_at"]-timedelta(hours=self.company.settings.news_max_age_hours)).isoformat()}
                    inserted = conn.execute("""INSERT INTO news_articles(id,source_id,url,title,summary,feed_digest,
                        source_digest,state,retrieval) VALUES(%s,%s,%s,%s,'',%s,%s,'collected',%s)
                        ON CONFLICT DO NOTHING RETURNING id""",
                                            (identity, source.id, url, candidate["title"][:500], digest,
                                             fingerprint(source.model_dump()), Jsonb(seed))).fetchone()
                    added += bool(inserted)
            receipt = {**receipt, "added": added, "excluded_unregistered": excluded}
            state = "completed" if receipt["ok"] else "blocked"
            conn.execute("""UPDATE news_searches SET state=%s,response=%s,receipt=%s,error=%s,completed_at=now()
                WHERE id=%s""", (state, Jsonb(body), Jsonb(as_json(receipt)), receipt.get("error"), saved["id"]))
            return {"state": state, "added": added, "excluded_unregistered": excluded}

    def search_fault(self, identity, code, seconds=0):
        with self.db.transaction() as conn:
            if code in {"quota", "busy", "unavailable"}:
                conn.execute("""UPDATE news_searches SET error=%s,next_at=now()+make_interval(secs=>%s)
                    WHERE id=%s AND state='running'""", (code, max(60, min(seconds or 300, 604800)), identity))
            else:
                conn.execute("UPDATE news_searches SET state='blocked',error=%s WHERE id=%s AND state='running'", (code, identity))

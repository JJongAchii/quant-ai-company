"""PostgreSQL owns candidate versions, frozen model calls and atomic outbox effects."""

import re
from datetime import timedelta
from uuid import uuid4

from psycopg.types.json import Jsonb

from ..company import as_json, fingerprint, stable
from ..contracts import ProviderRequest
from ..news.feeds import canonical_url
from ..owner_controls import effective_limits
from ..web_tools import search_prompt, search_result
from . import schedule
from .contracts import QUANT_FEED_AGENT, TOPICS, ResearchBrief, load_sources
from .editor import ProposalValidationError, output_contract, prompt, render, validate
from .feeds import aliases

LOCK = 71350249


def _sanitize_original_text(receipt):
    """Make extracted page text JSONB-safe while retaining the original byte receipt."""
    if not receipt.get("ok"):
        return receipt
    pages, replacements = [], 0
    for page in receipt["pages"]:
        text = page["text"]
        replacements += text.count("\x00")
        pages.append({**page, "text": text.replace("\x00", "\ufffd")})
    if not replacements:
        return receipt
    return {**receipt, "pages": pages,
            "text_sanitization": {"nul_replacements": replacements, "replacement": "U+FFFD"}}


def _bounded_pages(pages, budget=42000):
    """Keep all text that fits; redistribute unused page allowances before sampling."""
    if budget < 1:
        raise ValueError("quant_invalid_page_budget")
    allowances = [0] * len(pages)
    remaining = budget
    pending = set(range(len(pages)))
    while pending:
        share = remaining // len(pending)
        short = {i for i in pending if len(pages[i]["text"]) <= share}
        if not short:
            for position, i in enumerate(sorted(pending)):
                allowances[i] = share + (position < remaining % len(pending))
            break
        for i in short:
            allowances[i] = len(pages[i]["text"])
            remaining -= allowances[i]
        pending -= short
    bounded, clipped = [], False
    for page, allowance in zip(pages, allowances, strict=True):
        text, location = page["text"], page["location"]
        if len(text) <= allowance:
            bounded.append({"location": location, "text": text})
            continue
        clipped = True
        if not allowance:
            continue
        count = min(allowance, 2 if len(text) <= allowance * 2 else 3)
        width = allowance // count
        starts = [i * (len(text) - width) // max(1, count - 1) for i in range(count)]
        for index, start in enumerate(starts, 1):
            suffix = f" [{index}/{count}]"
            bounded.append({"location": location[:50-len(suffix)] + suffix,
                            "text": text[start:start+width]})
    return bounded, clipped


def _bibliographic_title(candidate, receipt):
    values = receipt["metadata"].get("citation_title", [])
    if values and isinstance(values[0], str):
        title = " ".join(values[0].split())
        if len(title) > 6:
            return title[:500]
    return candidate["title"]


class QuantFeedStore:
    def __init__(self, company):
        self.company, self.db = company, company.db

    def sources(self):
        return {s.id: s for s in load_sources(self.company.settings.quant_feed_sources_file)}

    def authorized(self):
        s = self.company.settings
        role = self.company.roles.get(QUANT_FEED_AGENT)
        return bool(s.quant_feed_enabled and role and not role.active and not role.tools and not role.can_delegate_to
                    and s.quant_feed_owner_user in s.slack_allowed_users
                    and s.quant_feed_channel_id in s.slack_allowed_channels
                    and s.quant_feed_channel_id.startswith(("C", "G"))
                    and s.quant_feed_channel_id not in {s.news_channel_id, s.tech_feed_channel_id})

    def policy(self):
        s = self.company.settings
        return fingerprint({"version": 14, "enabled": s.quant_feed_enabled, "publish": s.quant_feed_publish_enabled,
                            "owner": s.quant_feed_owner_user, "channel": s.quant_feed_channel_id,
                            "users": s.slack_allowed_users, "channels": s.slack_allowed_channels,
                            "web": s.company_web_enabled, "sources": [x.model_dump() for x in self.sources().values()],
                            "role": self.company.roles[QUANT_FEED_AGENT].model_dump()})

    def sync_sources(self, conn):
        sources = self.sources()
        conn.execute("UPDATE quant_feed_sources SET enabled=false WHERE NOT(id=ANY(%s))", (list(sources),))
        for source in sources.values():
            digest = fingerprint(source.model_dump())
            conn.execute("""INSERT INTO quant_feed_sources(id,config,config_digest,enabled) VALUES(%s,%s,%s,%s)
                ON CONFLICT(id) DO UPDATE SET config=excluded.config,config_digest=excluded.config_digest,
                enabled=excluded.enabled,next_at=CASE WHEN quant_feed_sources.config_digest=excluded.config_digest
                THEN quant_feed_sources.next_at ELSE now() END""",
                         (source.id, Jsonb(source.model_dump()), digest, source.enabled))

    def claim_source(self):
        if not self.authorized():
            return None
        with self.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
            self.sync_sources(conn)
            return conn.execute("""UPDATE quant_feed_sources SET lease_token=%s,lease_until=now()+interval '10 minutes',
                last_attempt=now() WHERE id=(SELECT id FROM quant_feed_sources WHERE enabled AND next_at<=now()
                AND (lease_until IS NULL OR lease_until<=now()) ORDER BY next_at,id FOR UPDATE SKIP LOCKED LIMIT 1)
                RETURNING *""", (uuid4(),)).fetchone()

    def add_candidate(self, conn, source, entry):
        url = canonical_url(entry["url"])
        if not source.allows(url):
            return False
        digest = fingerprint(source.model_dump())
        identity = fingerprint(url)
        # Re-discovery never resets review state or revives uncertain calls. Recheck original weekly.
        return bool(conn.execute("""INSERT INTO quant_feed_candidates(id,source_id,url,title,metadata,source_digest)
            VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(url) DO NOTHING RETURNING id""",
                                 (identity, source.id, url, entry["title"][:500],
                                  Jsonb(as_json(entry.get("metadata", {}))), digest)).fetchone())

    def save_source(self, claimed, receipt):
        source = self.sources().get(claimed["id"])
        if not self.authorized() or not source or not source.enabled or fingerprint(source.model_dump()) != claimed["config_digest"]:
            return {"state": "stale"}
        with self.db.transaction() as conn:
            current = conn.execute("SELECT * FROM quant_feed_sources WHERE id=%s FOR UPDATE", (source.id,)).fetchone()
            if current["lease_token"] != claimed["lease_token"] or current["config_digest"] != claimed["config_digest"]:
                return {"state": "stale"}
            if not receipt.get("ok"):
                delay = max(receipt.get("retry_after", 3600), min(86400, 3600 * 2 ** min(current["failures"], 5)))
                conn.execute("""UPDATE quant_feed_sources SET failures=failures+1,error=%s,receipt=%s,
                    next_at=now()+make_interval(secs=>%s),lease_until=NULL,lease_token=NULL WHERE id=%s""",
                             (receipt.get("error", "source_error"), Jsonb(receipt), delay, source.id))
                return {"state": "source_error", "source": source.id}
            added = sum(self.add_candidate(conn, source, e) for e in receipt.get("entries", []))
            metadata = {k: v for k, v in receipt.items() if k != "entries"}
            metadata.update(discovered=len(receipt.get("entries", [])), added=added)
            conn.execute("""UPDATE quant_feed_sources SET failures=0,error=NULL,receipt=%s,last_success=now(),
                next_at=now()+make_interval(hours=>%s),lease_until=NULL,lease_token=NULL WHERE id=%s""",
                         (Jsonb(as_json(metadata)), source.interval_hours, source.id))
            return {"state": "collected", "added": added, "source": source.id}

    def claim_candidate(self):
        if not self.authorized():
            return None
        with self.db.transaction() as conn:
            return conn.execute("""UPDATE quant_feed_candidates SET lease_token=%s,lease_until=now()+interval '10 minutes'
                WHERE id=(SELECT c.id FROM quant_feed_candidates c JOIN quant_feed_sources s ON s.id=c.source_id
                    WHERE s.enabled AND c.source_digest=s.config_digest AND c.next_at<=now()
                    AND (c.lease_until IS NULL OR c.lease_until<=now())
                    ORDER BY (SELECT count(*) FROM quant_feed_candidates seen
                              WHERE seen.source_id=c.source_id AND seen.last_fetch IS NOT NULL),
                             c.next_at,c.discovered_at,c.id FOR UPDATE OF c SKIP LOCKED LIMIT 1)
                RETURNING *""", (uuid4(),)).fetchone()

    def save_original(self, claimed, receipt):
        source = self.sources().get(claimed["source_id"])
        if not self.authorized() or not source or not source.enabled or fingerprint(source.model_dump()) != claimed["source_digest"]:
            return {"state": "stale"}
        receipt = _sanitize_original_text(receipt)
        with self.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
            candidate = conn.execute("SELECT * FROM quant_feed_candidates WHERE id=%s FOR UPDATE", (claimed["id"],)).fetchone()
            if candidate["lease_token"] != claimed["lease_token"]:
                return {"state": "stale"}
            error = receipt.get("error") if not receipt.get("ok") else None
            metadata_receipt = {k: v for k, v in receipt.items() if k not in {"pages", "links", "metadata"}}
            conn.execute("""UPDATE quant_feed_candidates SET state=%s,last_fetch=now(),receipt=%s,error=%s,
                lease_until=NULL,lease_token=NULL,next_at=now()+interval '7 days' WHERE id=%s""",
                         ("held" if error else "fetched", Jsonb(as_json(metadata_receipt)), error, candidate["id"]))
            if error:
                return {"state": "held", "reason": error}
            if not source.allows(receipt["url"]):
                raise ValueError("quant_unregistered_original")
            metadata = {**candidate["metadata"], **receipt["metadata"], "publisher": source.publisher,
                        "commercial": source.commercial, "title": _bibliographic_title(candidate, receipt),
                        "url": receipt["url"],
                        "landing_url": candidate["url"], "links": receipt["links"]}
            keys = list(dict.fromkeys(aliases(candidate["url"], metadata) + aliases(receipt["url"], metadata)))
            existing = conn.execute("SELECT DISTINCT work_id FROM quant_feed_aliases WHERE alias=ANY(%s)", (keys,)).fetchall()
            if len(existing) > 1:
                conn.execute("UPDATE quant_feed_candidates SET state='held',error='identity_conflict' WHERE id=%s", (candidate["id"],))
                return {"state": "held", "reason": "identity_conflict"}
            work = existing[0]["work_id"] if existing else fingerprint(keys[0])
            conn.execute("INSERT INTO quant_feed_works(id) VALUES(%s) ON CONFLICT DO NOTHING", (work,))
            for key in keys:
                conn.execute("INSERT INTO quant_feed_aliases(alias,work_id) VALUES(%s,%s) ON CONFLICT DO NOTHING", (key, work))
            # Text-based versioning ignores byte-only PDF changes; original byte hash is still retained.
            content_digest = fingerprint([re.sub(r"\s+", " ", p["text"]).strip() for p in receipt["pages"]])
            identity = fingerprint([work, content_digest])
            inserted = conn.execute("""INSERT INTO quant_feed_documents
                (id,work_id,candidate_id,source_digest,content_digest,metadata,receipt,pages)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id""",
                                    (identity, work, candidate["id"], candidate["source_digest"], content_digest,
                                     Jsonb(as_json(metadata)), Jsonb(as_json(metadata_receipt)), Jsonb(receipt["pages"]))).fetchone()
            return {"state": "ready" if inserted else "duplicate", "document_id": identity}

    def bundle(self, conn, document):
        metadata = document["metadata"]
        pages = document["pages"]
        # Include every page and freeze exactly what each reviewer saw. Long pages
        # are sampled at the head/middle/tail so tables and limitations near the
        # end are not systematically hidden by prefix-only clipping.
        clipped, context_clipped = _bounded_pages(pages)
        prior = conn.execute("""SELECT d.brief,d.id,p.id AS publication_id,o.status,o.sent_ts,p.channel
            FROM quant_feed_publications p JOIN quant_feed_documents d ON d.id=p.document_id JOIN outbox o ON o.id=p.id
            WHERE p.work_id=%s AND p.channel=%s ORDER BY o.created_at DESC LIMIT 1""",
                             (document["work_id"], self.company.settings.quant_feed_channel_id)).fetchone()
        bibliographic = {k: v for k, v in metadata.items() if k not in {"links"}}
        links = metadata["links"][:30]
        return as_json({"document_id": document["id"], "as_of": schedule.utcnow(), "metadata": bibliographic,
                        "pages": clipped, "original_sha256": document["receipt"]["original_sha256"],
                        "truncated": document["receipt"].get("truncated", False),
                        "context_clipped": context_clipped,
                        "retrieval": document["receipt"], "links": links, "commercial": metadata["commercial"],
                        "prior": prior, "draft": document["brief"], "previous_critique": document["critique"]})

    def search_bundle(self, conn, stage, slot):
        n = conn.execute("SELECT count(*) AS n FROM quant_feed_calls WHERE stage IN ('discover','weekly')").fetchone()["n"]
        topic = TOPICS[n % len(TOPICS)]
        context = conn.execute("""SELECT metadata->>'title' AS title,metadata->>'landing_url' AS url
            FROM quant_feed_documents ORDER BY reviewed_at DESC NULLS LAST,created_at DESC LIMIT 4""").fetchall()
        query = (f"Quantitative equity research Korea US {topic}; 한국 미국 주식 퀀트 연구. "
                 + ("Find foundational classics, follow-up citations, replication failures, refutations, retractions, "
                    "data/code corrections and missing themes. " if stage == "weekly" else
                    "Find recent substantive academic/institutional research and accessible author full-text copies. ")
                 + "Search Korean and English. Prefer registered publishers: "
                 + ", ".join(s.publisher for s in self.sources().values() if s.enabled)
                 + ". At most 4 native searches. No page opens. Public originals only. Follow-ups/author copies of: "
                 + "; ".join((r["title"] or "")[:70] for r in context))[:1000]
        return {"query": query, "limit": 8, "topic": topic, "slot": slot, "follow_up": context}

    def prepare(self):
        if not self.authorized():
            return {"state": "paused"}
        policy = self.policy()
        with self.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
            if conn.execute("SELECT 1 FROM runtime_control WHERE paused_until>now()").fetchone():
                return {"state": "defer", "reason": "global_quota_pause"}
            # Running IDs are recovered, never replaced. Blocked documents do not freeze unrelated work.
            active = conn.execute("SELECT * FROM quant_feed_calls WHERE state='running' ORDER BY created_at LIMIT 1 FOR UPDATE").fetchone()
            if active:
                if active["policy_digest"] != policy:
                    self.block(conn, active, "policy_changed_requires_reconciliation")
                    return {"state": "blocked", "request_id": active["id"]}
                if active["next_at"] > schedule.utcnow():
                    return {"state": "defer"}
                return {"state": "ready", "request": active["request"]}
            self.sync_sources(conn)
            stage, slot, document, bundle = None, None, None, None
            if self.company.settings.company_web_enabled:
                for operation, due in (("weekly", schedule.weekly_slot()), ("discover", schedule.discovery_slot())):
                    if due and not conn.execute("SELECT 1 FROM quant_feed_calls WHERE stage=%s AND slot=%s", (operation, due)).fetchone():
                        stage, slot = operation, due
                        bundle = self.search_bundle(conn, stage, slot)
                        break
            if stage is None:
                document = conn.execute("""SELECT d.* FROM quant_feed_documents d
                    JOIN quant_feed_candidates c ON c.id=d.candidate_id JOIN quant_feed_sources s ON s.id=c.source_id
                    WHERE d.state='ready' AND s.enabled AND d.source_digest=s.config_digest
                    ORDER BY (d.stage='critique') DESC,
                        (SELECT count(*) FROM quant_feed_documents seen
                         JOIN quant_feed_candidates seen_candidate ON seen_candidate.id=seen.candidate_id
                         WHERE seen_candidate.source_id=c.source_id
                         AND seen.state NOT IN ('ready','reviewing')),
                        (COALESCE(d.receipt->>'fulltext_status','')='html_requires_evidence_check'),
                        d.created_at,d.id FOR UPDATE OF d SKIP LOCKED LIMIT 1""").fetchone()
                if not document:
                    return {"state": "idle"}
                stage, bundle = document["stage"], self.bundle(conn, document)
                if bundle["prior"] and bundle["prior"]["status"] != "delivered":
                    conn.execute("UPDATE quant_feed_documents SET state='held',error='prior_delivery_unresolved' WHERE id=%s", (document["id"],))
                    return {"state": "held", "reason": "prior_delivery_unresolved"}
            conn.execute("INSERT INTO daily_usage(day,reserved) VALUES(CURRENT_DATE,0) ON CONFLICT DO NOTHING")
            used = conn.execute("SELECT reserved FROM daily_usage WHERE day=CURRENT_DATE FOR UPDATE").fetchone()["reserved"]
            cap = effective_limits(conn, self.company)["company"]
            if cap is not None and used >= cap:
                return {"state": "defer", "reason": "daily_model_budget"}
            role = self.company.roles[QUANT_FEED_AGENT]
            searching = stage in {"discover", "weekly"}
            request = ProviderRequest(request_id="quant-feed-" + str(uuid4()), model=role.model,
                                      reasoning_effort=role.reasoning_effort, web_search=searching,
                                      output_contract="agent_decision" if searching else output_contract(stage),
                                      prompt=search_prompt({"query": bundle["query"], "limit": bundle["limit"]}) if searching
                                      else prompt(bundle, stage))
            conn.execute("UPDATE daily_usage SET reserved=reserved+1 WHERE day=CURRENT_DATE")
            conn.execute("""INSERT INTO quant_feed_calls(id,document_id,stage,slot,request,bundle,policy_digest)
                VALUES(%s,%s,%s,%s,%s,%s,%s)""", (request.request_id, document["id"] if document else None,
                                                 stage, slot, Jsonb(request.model_dump()), Jsonb(bundle), policy))
            if document:
                conn.execute("UPDATE quant_feed_documents SET state='reviewing' WHERE id=%s", (document["id"],))
            return {"state": "ready", "request": request.model_dump()}

    @staticmethod
    def block(conn, call, code):
        conn.execute("UPDATE quant_feed_calls SET state='blocked',error=%s,completed_at=now() WHERE id=%s AND state='running'",
                     (code, call["id"]))
        if call["document_id"]:
            conn.execute("UPDATE quant_feed_documents SET state='held',error=%s WHERE id=%s AND state='reviewing'",
                         (code, call["document_id"]))

    def fault(self, identity, code, seconds=0):
        with self.db.transaction() as conn:
            call = conn.execute("SELECT * FROM quant_feed_calls WHERE id=%s FOR UPDATE", (identity,)).fetchone()
            if not call or call["state"] != "running":
                return
            if code in {"quota", "busy", "unavailable"}:
                delay = max(60, min(seconds or 300, 604800))
                conn.execute("UPDATE quant_feed_calls SET error=%s,next_at=now()+make_interval(secs=>%s) WHERE id=%s", (code, delay, identity))
                if code == "quota" and not self.company.settings.model_accounts_enabled:
                    conn.execute("""UPDATE runtime_control SET paused_until=GREATEST(paused_until,now()+make_interval(secs=>%s)),
                        reason='subscription_quota' WHERE id=1""", (delay,))
            else:
                self.block(conn, call, code)

    def commit(self, response):
        with self.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
            saved = conn.execute("SELECT * FROM quant_feed_calls WHERE id=%s FOR UPDATE", (response.request_id,)).fetchone()
            if not saved:
                raise ValueError("quant_unknown_request")
            body = response.model_dump(mode="json")
            if saved["response"] is not None:
                if fingerprint(body) != fingerprint(saved["response"]):
                    raise ValueError("quant_committed_response_changed")
                return {"state": saved["state"], "duplicate": True}
            if saved["state"] != "running" or not self.authorized() or saved["policy_digest"] != self.policy():
                self.block(conn, saved, "stale_policy_or_state")
                return {"state": "stale"}
            if response.provider != self.company.settings.model_provider:
                raise ValueError("quant_wrong_provider")
            if saved["stage"] in {"discover", "weekly"}:
                result = search_result(response, saved["bundle"])
                if result["ok"]:
                    added = 0
                    for entry in result["results"]:
                        source = next((s for s in self.sources().values() if s.enabled and s.allows(entry["url"])), None)
                        if source:
                            added += self.add_candidate(conn, source, {**entry, "metadata": {"discovery_request": saved["id"]}})
                    result["added"] = added
                else:
                    self.block(conn, saved, result["error"])
            else:
                try:
                    result = self.commit_document(conn, saved, response)
                except ProposalValidationError as exc:
                    result = self.repair_validation(conn, saved, exc)
                    if result is None:
                        raise
            conn.execute("""UPDATE quant_feed_calls SET state=CASE WHEN state='running' THEN 'completed' ELSE state END,
                response=%s,receipt=%s,completed_at=now() WHERE id=%s""", (Jsonb(body), Jsonb(as_json(result)), saved["id"]))
            return {"state": "completed", **result}

    @staticmethod
    def repair_validation(conn, saved, error):
        if saved["stage"] not in {"review", "revision"}:
            return None
        document = conn.execute("SELECT * FROM quant_feed_documents WHERE id=%s FOR UPDATE",
                                (saved["document_id"],)).fetchone()
        if conn.execute("SELECT 1 FROM quant_feed_calls WHERE document_id=%s AND stage='repair'",
                        (document["id"],)).fetchone():
            return None
        feedback = error.feedback(saved["bundle"].get("previous_critique"))
        conn.execute("""UPDATE quant_feed_documents SET state='ready',stage='repair',brief=%s,
            critique=%s,reviewed_at=now() WHERE id=%s""", (Jsonb(error.draft), Jsonb(feedback), document["id"]))
        return {"document_state": "ready", "document_id": document["id"], "validation_issue": str(error),
                "validation_issues": error.issues}

    def commit_document(self, conn, saved, response):
        document = conn.execute("SELECT * FROM quant_feed_documents WHERE id=%s FOR UPDATE", (saved["document_id"],)).fetchone()
        corrections = []
        value = validate(response, saved["bundle"], saved["stage"], audit=corrections)
        if saved["stage"] != "critique":
            state = {"publish": "ready", "hold": "held", "reject": "rejected"}[value.disposition]
            if value.disposition == "publish" and value.change == "cosmetic":
                state = "duplicate"
            conn.execute("UPDATE quant_feed_documents SET brief=%s,state=%s,stage='critique',reviewed_at=now() WHERE id=%s",
                         (Jsonb(value.model_dump()), state, document["id"]))
            return {"document_state": state, "document_id": document["id"],
                    "source_corrections": corrections}
        state = "held"
        if value.disposition == "revise" and document["revision"] == 0:
            state = "ready"
            conn.execute("UPDATE quant_feed_documents SET revision=1,stage='revision' WHERE id=%s", (document["id"],))
        elif value.disposition == "pass":
            state = "approved" if self.company.settings.quant_feed_publish_enabled else "preview"
        conn.execute("UPDATE quant_feed_documents SET critique=%s,state=%s,reviewed_at=now() WHERE id=%s",
                     (Jsonb(value.model_dump()), state, document["id"]))
        if state == "approved":
            self.enqueue(conn, document, saved["bundle"].get("prior"))
            state = "queued"
        return {"document_state": state, "document_id": document["id"]}

    def enqueue(self, conn, document, prior):
        s = self.company.settings
        brief = ResearchBrief.model_validate(document["brief"])
        project_id = stable(f"quant-feed-project:{s.quant_feed_channel_id}:{s.quant_feed_owner_user}")
        conn.execute("""INSERT INTO projects(id,title,instruction,owner_user,channel)
            VALUES(%s,'quant-feeds','Evidence-first research briefs; no trading or model turns',%s,%s) ON CONFLICT DO NOTHING""",
                     (project_id, s.quant_feed_owner_user, s.quant_feed_channel_id))
        project = conn.execute("SELECT * FROM projects WHERE id=%s", (project_id,)).fetchone()
        message_id = stable(f"quant-feed:{s.quant_feed_channel_id}:{document['id']}")
        previous_url = None
        if prior and prior["sent_ts"]:
            previous_url = f"https://slack.com/archives/{prior['channel']}/p{prior['sent_ts'].replace('.', '')}"
        self.company._message(conn, project, None, QUANT_FEED_AGENT, "quant_feed",
                              render(brief, document["metadata"], previous_url), message_id=message_id)
        conn.execute("""INSERT INTO quant_feed_publications(id,document_id,work_id,channel,policy_digest,correction,previous_id)
            VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                     (message_id, document["id"], document["work_id"], s.quant_feed_channel_id, self.policy(),
                      brief.change in {"correction", "retraction"}, prior["publication_id"] if prior else None))
        conn.execute("UPDATE outbox SET next_at=%s WHERE id=%s", (schedule.delivery_time(schedule.utcnow()), message_id))
        conn.execute("UPDATE quant_feed_documents SET state='queued' WHERE id=%s", (document["id"],))

    def gate(self, conn, row, *, claimed=False):
        at = schedule.utcnow()
        saved = conn.execute("SELECT * FROM quant_feed_publications WHERE id=%s", (row["id"],)).fetchone()
        if not (saved and self.authorized() and self.company.settings.quant_feed_publish_enabled
                and saved["policy_digest"] == self.policy() and row["channel"] == self.company.settings.quant_feed_channel_id):
            conn.execute("UPDATE outbox SET status='stale',error='quant_feed_policy_changed' WHERE id=%s", (row["id"],))
            return False
        conn.execute("INSERT INTO quant_feed_delivery(channel,next_at) VALUES(%s,%s) ON CONFLICT DO NOTHING", (row["channel"], at))
        pacing = conn.execute("SELECT next_at FROM quant_feed_delivery WHERE channel=%s FOR UPDATE", (row["channel"],)).fetchone()["next_at"]
        until = schedule.delivery_time(max(at, pacing))
        if until > at:
            conn.execute("""UPDATE outbox SET status='pending',next_at=%s,started_at=NULL,
                attempts=GREATEST(0,attempts-%s),error='quant_feed_window_or_spacing' WHERE id=%s""", (until, int(claimed), row["id"]))
            return False
        if claimed:
            conn.execute("UPDATE quant_feed_delivery SET next_at=%s WHERE channel=%s", (at + timedelta(seconds=60), row["channel"]))
        return True

    def status(self):
        with self.db.transaction() as conn:
            return as_json({"enabled": self.company.settings.quant_feed_enabled,
                            "publish_enabled": self.company.settings.quant_feed_publish_enabled,
                            "authorized": self.authorized(), "channel": self.company.settings.quant_feed_channel_id,
                            "daily_publication_cap": None, "delivery_window": "06:00–24:00 Asia/Seoul; >=60s spacing",
                            "scope": "registered sources plus twice-daily rotating discovery; not exhaustive coverage",
                            "sources": conn.execute("SELECT id,last_success,last_attempt,failures,error,receipt,next_at FROM quant_feed_sources ORDER BY id").fetchall(),
                            "documents": conn.execute("SELECT state,count(*) AS count FROM quant_feed_documents GROUP BY state").fetchall(),
                            "recent": conn.execute("""SELECT id,metadata->>'title' AS title,metadata->>'url' AS url,state,stage,error,
                                brief,critique,reviewed_at FROM quant_feed_documents ORDER BY created_at DESC LIMIT 20""").fetchall(),
                            "calls": conn.execute("SELECT id,stage,slot,state,error,created_at,completed_at FROM quant_feed_calls ORDER BY created_at DESC LIMIT 20").fetchall(),
                            "deliveries": conn.execute("""SELECT p.id,o.status,o.sent_ts,o.error,o.attempts,o.next_at
                                FROM quant_feed_publications p JOIN outbox o ON o.id=p.id ORDER BY o.created_at DESC LIMIT 20""").fetchall()})

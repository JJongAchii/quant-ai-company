"""Frozen requests and one committed edition; external delivery remains receipt-based."""

from datetime import timedelta

from psycopg.types.json import Jsonb

from ..company import as_json, fingerprint, stable
from ..contracts import ProviderRequest
from ..owner_controls import effective_limits
from ..staff.packs import coaching, pack
from ..web_tools import search_prompt, search_result
from . import schedule
from .contracts import BRIEFER, BriefProposal, BriefReview, ConditionPatch, SourceDocument, SourcePlan
from .coverage import COVERAGE_VERSION, inventory
from .editor import (
    FORMAT_VERSION,
    PATCH,
    REVIEW,
    VALIDATION_VERSION,
    WRITE,
    apply_condition_patch,
    artifact,
    prompt,
    prune,
    render,
    revision_bundle,
    validate,
    validate_review,
)
from .inputs import market_report, registrations, source_policy
from .planning import PLAN, apply_plan, plan_prompt
from .quality import reconcile

LOCK = 71350241
TERMINAL = {"committed", "previewed", "missed", "stale"}


class BriefStore:
    def __init__(self, company):
        self.company, self.db = company, company.db

    def authorized(self):
        s = self.company.settings
        role = self.company.roles.get(BRIEFER)
        return bool(s.briefing_enabled and role and role.active and s.briefing_owner_user in s.slack_allowed_users
                    and s.briefing_channel_id in s.slack_allowed_channels and s.briefing_channel_id.startswith(("C", "G")))

    def policy(self):
        s = self.company.settings
        return fingerprint({"version": FORMAT_VERSION, "validation_version": VALIDATION_VERSION,
                            "selection_version": COVERAGE_VERSION,
                            "schedule_version": schedule.SCHEDULE_VERSION,
                            "editorial_contract": fingerprint([PLAN, WRITE, REVIEW, PATCH]),
                            "enabled": s.briefing_enabled, "publish": s.briefing_publish_enabled,
                            "analyst_procedure": pack(BRIEFER)["digest"],
                            "lake": s.company_lake_uri,
                            "search": s.briefing_search_enabled, "web": s.company_web_enabled,
                            "channel": s.briefing_channel_id, "owner": s.briefing_owner_user,
                            "allowed_users": s.slack_allowed_users, "allowed_channels": s.slack_allowed_channels,
                            "role": self.company.roles.get(BRIEFER).model_dump() if BRIEFER in self.company.roles else None,
                            "provider": s.model_provider, "sources": source_policy(s),
                            "overrides": [x.model_dump(mode="json") for x in schedule.overrides(s).values()]})

    def register(self, at=None):
        at = at or schedule.utcnow()
        if not self.authorized():
            return []
        definitions = [e for e in schedule.scheduled(self.company.settings, at) if e.starts_at <= at]
        policy = self.policy()
        with self.db.transaction() as conn:
            for definition in definitions:
                state = "missed" if definition.expires_at <= at else "collecting"
                conn.execute("""INSERT INTO brief_editions(id,day,kind,channel,owner_user,definition,due_at,cutoff,
                    expires_at,policy_digest,publish,state,next_collection,next_data) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING""", (definition.id, definition.day, definition.kind,
                        self.company.settings.briefing_channel_id, self.company.settings.briefing_owner_user,
                        Jsonb(definition.model_dump(mode="json")), definition.due_at, definition.cutoff,
                        definition.expires_at, policy, self.company.settings.briefing_publish_enabled, state, at, at))
        return definitions

    def claim_collection(self, at=None):
        at = at or schedule.utcnow()
        self.register(at)
        if not self.authorized():
            return None
        policy = self.policy()
        with self.db.transaction() as conn:
            row = conn.execute("""SELECT * FROM brief_editions WHERE state='collecting' AND cutoff>%s
                AND policy_digest=%s AND next_collection<=%s AND (collection_lease IS NULL OR collection_lease<%s)
                ORDER BY due_at FOR UPDATE SKIP LOCKED LIMIT 1""", (at, policy, at, at)).fetchone()
            if not row:
                return None
            row["lease"] = at+timedelta(minutes=6)
            conn.execute("UPDATE brief_editions SET collection_lease=%s WHERE id=%s", (row["lease"], row["id"]))
            search = conn.execute("SELECT result FROM brief_calls WHERE edition_id=%s AND phase='search' AND state='completed'",
                                  (row["id"],)).fetchone()
            row["candidates"] = (search["result"] or {}).get("results", []) if search else []
            return row

    def save_collection(self, claimed, bundle):
        with self.db.transaction() as conn:
            conn.execute("""UPDATE brief_editions SET bundle=COALESCE(bundle,'{}'::jsonb)||%s,collected_at=%s,collection_lease=NULL,
                next_collection=%s WHERE id=%s AND collection_lease=%s AND state='collecting' AND policy_digest=%s""",
                         (Jsonb(bundle), schedule.utcnow(), schedule.utcnow()+timedelta(minutes=5),
                          claimed["id"], claimed["lease"], self.policy()))

    def _freeze(self, conn, row):
        bundle = {**(row["bundle"] or {"documents": [], "collection_errors": []}), "edition": row["definition"]}
        bundle["analyst_procedure"] = pack(BRIEFER)
        role = self.company.role(BRIEFER)
        bundle["professional_feedback"] = as_json(coaching(conn, row["owner_user"], BRIEFER,
                                                          role.model, role.reasoning_effort))
        data = row["market_data"] or {"documents": [], "observations": [], "contexts": [],
                                      "diagnostics": [{"dataset": "lake", "error": "data_not_collected"}]}
        bundle["documents"] = bundle.get("documents", []) + data["documents"]
        bundle["locked_observations"] = data["observations"]
        bundle["market_context"] = data["contexts"]
        bundle["data_diagnostics"] = data["diagnostics"]
        definition = row["definition"]
        from datetime import date

        changes = schedule.overrides(self.company.settings)
        bundle["exchange_closes"] = {
            market: schedule.close(market, date.fromisoformat(definition[key]), changes).isoformat()
            for market, key in (("US", "us_session"), ("KR", "kr_session")) if definition.get(key)
        }
        morning = conn.execute("""SELECT proposal FROM brief_editions a WHERE a.channel=%s AND a.owner_user=%s
            AND a.day=%s AND a.kind='am' AND a.committed_at IS NOT NULL AND a.proposal IS NOT NULL
            AND (NOT a.publish OR EXISTS(SELECT 1 FROM brief_messages m JOIN outbox o ON o.id=m.id
                WHERE m.edition_id=a.id AND m.part=0 AND o.status='delivered'))""",
                               (row["channel"], row["owner_user"], row["day"])).fetchone() if row["kind"] == "pm" else None
        bundle["morning_watchpoints"] = morning["proposal"]["watchpoints"] if morning else []
        recent = conn.execute("""SELECT day,kind,proposal->'summary' AS summary FROM brief_editions
            WHERE channel=%s AND owner_user=%s AND due_at<%s AND proposal IS NOT NULL
            AND committed_at IS NOT NULL ORDER BY due_at DESC LIMIT 3""",
                              (row["channel"], row["owner_user"], row["due_at"])).fetchall()
        bundle["previous_briefs_context_only"] = as_json(recent)
        return bundle

    def claim_data(self):
        at = schedule.utcnow()
        self.register(at)
        if not self.authorized():
            return None
        with self.db.transaction() as conn:
            row = conn.execute("""SELECT * FROM brief_editions WHERE state='collecting' AND cutoff>%s
                AND next_data<=%s AND policy_digest=%s AND (data_lease IS NULL OR data_lease<%s)
                ORDER BY due_at FOR UPDATE SKIP LOCKED LIMIT 1""", (at, at, self.policy(), at)).fetchone()
            if row:
                row["lease"] = at+timedelta(minutes=2)
                conn.execute("UPDATE brief_editions SET data_lease=%s WHERE id=%s", (row["lease"], row["id"]))
            return row

    def save_data(self, claimed, result):
        at = schedule.utcnow()
        with self.db.transaction() as conn:
            conn.execute("""UPDATE brief_editions SET market_data=%s,data_lease=NULL,next_data=%s
                WHERE id=%s AND data_lease=%s AND state='collecting' AND cutoff>%s AND policy_digest=%s""",
                         (Jsonb(result), at+timedelta(minutes=5), claimed["id"], claimed["lease"], at, self.policy()))

    def prepare(self, at=None):
        at = at or schedule.utcnow()
        if not self.authorized():
            return {"state": "paused"}
        self.register(at)
        self.flush(at)
        policy = self.policy()
        with self.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
            if conn.execute("SELECT 1 FROM runtime_control WHERE paused_until>%s", (at,)).fetchone():
                return {"state": "defer"}
            row = conn.execute("""SELECT * FROM brief_editions WHERE state IN ('collecting','planning','writing','reviewing','revising','final_reviewing')
                AND committed_at IS NULL AND policy_digest=%s AND due_at+interval '10 minutes'>%s
                ORDER BY due_at LIMIT 1 FOR UPDATE""", (policy, at)).fetchone()
            if not row:
                return {"state": "idle"}
            active = conn.execute("SELECT * FROM brief_calls WHERE edition_id=%s AND state='running' ORDER BY phase LIMIT 1",
                                  (row["id"],)).fetchone()
            if active:
                return ({"state": "defer"} if active["next_at"] > at
                        else {"state": "ready", "request": active["request"]})
            if row["state"] == "collecting" and at < row["cutoff"]:
                s = self.company.settings
                if not (s.briefing_search_enabled and s.company_web_enabled and row["collected_at"]):
                    return {"state": "idle"}
                if conn.execute("SELECT 1 FROM brief_calls WHERE edition_id=%s AND phase='search'", (row["id"],)).fetchone():
                    return {"state": "idle"}
                docs = (row["bundle"] or {}).get("documents", [])
                origins = {d.get("origin_group") or d["publisher"] for d in docs if d["kind"] == "media"}
                if (sum(d["kind"] == "media" for d in docs) >= 4 and len(origins) >= 2
                        and not inventory([SourceDocument.model_validate(d) for d in docs])["missing_topics"]
                        and any(market_report(d, row["kind"]) for d in docs)):
                    return {"state": "idle"}
                phase = "search"
                hosts = sorted({h for spec in registrations(s) for h in spec.article_hosts})
                args = {"query": (f"As of {at.isoformat()}, find public original closing market reports for "
                    f"{row['definition']['us_session'] if row['kind'] == 'am' else row['day']} "
                    + ("US S&P 500 Nasdaq Treasury yields, overnight macro and Korea implications. " if row["kind"] == "am"
                       else "Korea KOSPI KOSDAQ KRW investor flows and tonight US economic releases. ")
                    + "Also fill missing policy, geopolitical, corporate/sector and cross-asset coverage; seek opposing evidence. Registered URLs: "
                    + ", ".join(hosts) + "; at most two native searches, no opening pages.")[:1000],
                        "limit": 6}
                model_prompt = search_prompt(args)
                bundle = row["bundle"] or {"documents": []}
                bundle["search_arguments"] = args
            else:
                phase = {"reviewing": "review", "revising": "revise", "final_reviewing": "final_review"}.get(row["state"], "write")
                bundle = self._freeze(conn, row) if row["state"] == "collecting" else row["bundle"]
                if row["state"] in {"collecting", "planning"} and bundle.get("candidate_documents"):
                    phase = "plan"
                if not bundle.get("documents"):
                    conn.execute("UPDATE brief_editions SET state='blocked',error='no_current_originals',bundle=%s WHERE id=%s",
                                 (Jsonb(bundle), row["id"]))
                    return {"state": "blocked", "reason": "no_current_originals"}
                model_prompt = (plan_prompt(bundle) if phase == "plan" else
                                prompt(bundle, phase, row["proposal"] if phase in {"review", "final_review"} else None))
            identity = f"news-brief-{row['id']}-{phase}"
            if conn.execute("SELECT 1 FROM brief_calls WHERE id=%s", (identity,)).fetchone():
                return {"state": "blocked", "reason": "phase_already_attempted"}
            conn.execute("INSERT INTO daily_usage(day,reserved) VALUES(CURRENT_DATE,0) ON CONFLICT DO NOTHING")
            used = conn.execute("SELECT reserved FROM daily_usage WHERE day=CURRENT_DATE FOR UPDATE").fetchone()["reserved"]
            cap = effective_limits(conn, self.company)["company"]
            if cap is not None and used >= cap:
                return {"state": "defer", "reason": "daily_limit"}
            role = self.company.role(BRIEFER)
            request = ProviderRequest(request_id=identity, model=role.model, reasoning_effort=role.reasoning_effort,
                                      prompt=model_prompt, web_search=phase == "search")
            conn.execute("UPDATE daily_usage SET reserved=reserved+1 WHERE day=CURRENT_DATE")
            conn.execute("INSERT INTO brief_calls(id,edition_id,phase,request,next_at) VALUES(%s,%s,%s,%s,%s)",
                         (identity, row["id"], phase, Jsonb(request.model_dump()), at))
            conn.execute("UPDATE brief_editions SET state=%s,bundle=%s WHERE id=%s",
                         ({"search": "collecting", "plan": "planning", "write": "writing", "review": "reviewing",
                           "revise": "revising", "final_review": "final_reviewing"}[phase], Jsonb(bundle), row["id"]))
            return {"state": "ready", "request": request.model_dump()}

    def commit(self, response):
        at = schedule.utcnow()
        with self.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
            call = conn.execute("SELECT * FROM brief_calls WHERE id=%s FOR UPDATE", (response.request_id,)).fetchone()
            if not call:
                raise ValueError("unknown_brief_call")
            body = response.model_dump(mode="json")
            if call["response"] is not None:
                if fingerprint(body) != fingerprint(call["response"]):
                    raise ValueError("committed_brief_response_changed")
                return {"state": call["state"], "duplicate": True}
            row = conn.execute("SELECT * FROM brief_editions WHERE id=%s FOR UPDATE", (call["edition_id"],)).fetchone()
            if (not self.authorized() or row["policy_digest"] != self.policy() or row["committed_at"]
                    or row["due_at"]+timedelta(minutes=10) <= at
                    or call["state"] != "running" or row["state"] in TERMINAL):
                conn.execute("UPDATE brief_calls SET state='stale',response=%s,completed_at=%s WHERE id=%s",
                             (Jsonb(body), at, call["id"]))
                return {"state": "stale"}
            if response.provider != self.company.settings.model_provider:
                raise ValueError("brief_provider_mismatch")
            if call["phase"] == "search":
                result = search_result(response, row["bundle"]["search_arguments"])
                conn.execute("UPDATE brief_editions SET next_collection=%s WHERE id=%s", (at, row["id"]))
            elif call["phase"] == "plan":
                plan = artifact(response, SourcePlan)
                selected = apply_plan(row["bundle"], plan)
                # Check the actual writer context before spending another call.
                prompt(selected, "write")
                result = plan.model_dump(mode="json")
                conn.execute("UPDATE brief_editions SET state='writing',bundle=%s WHERE id=%s",
                             (Jsonb(selected), row["id"]))
            elif call["phase"] in {"write", "revise"}:
                feedback = row["bundle"].get("revision_feedback", {})
                if call["phase"] == "revise" and feedback.get("repair_mode") == "conditions_only":
                    proposed = apply_condition_patch(BriefProposal.model_validate(row["proposal"]),
                        artifact(response, ConditionPatch), feedback["allowed_ids"])
                else:
                    proposed = artifact(response, BriefProposal)
                rejected = validate(proposed, row["bundle"])
                proposal = prune(proposed, rejected)
                proposal, conflicts = reconcile(proposal, row["bundle"])
                row["bundle"]["quote_conflicts"] = conflicts
                result = {"rejected": rejected}
                row["bundle"].pop("revision_feedback", None)
                next_state = "final_reviewing" if call["phase"] == "revise" else "reviewing"
                conn.execute("UPDATE brief_editions SET state=%s,proposal=%s,quality=%s,bundle=%s WHERE id=%s",
                             (next_state, Jsonb(proposal.model_dump(mode="json")), Jsonb(result), Jsonb(row["bundle"]), row["id"]))
            else:
                review = artifact(response, BriefReview)
                proposal = BriefProposal.model_validate(row["proposal"])
                validate_review(review, proposal, row["bundle"])
                rejected = {**(row["quality"] or {}).get("rejected", {}),
                            **dict.fromkeys(review.rejected_ids, "semantic_review")}
                proposal = prune(proposal, set(rejected) | validate(proposal, row["bundle"]).keys())
                validity = {k: v for k, v in review.checks.items() if k not in {"coverage", "depth", "readability", "materiality"}}
                if review.verdict == "withhold" or (not all(validity.values()) and not review.rejected_ids):
                    proposal = None
                parts, quality = render(proposal, row["bundle"], rejected=rejected,
                    fallback="editorial_review_withheld" if proposal is None else None,
                    review_reduced=review.verdict == "reduce")
                quality["editorial_review"] = review.model_dump(mode="json")
                result = review.model_dump(mode="json")
                # One repair only, only after a confirmed response, with 15 minutes left for repair+review.
                repair = (call["phase"] == "review" and (quality["reduced"] or rejected or review.verdict != "publish")
                          and at+timedelta(minutes=15) < row["due_at"]+timedelta(minutes=10)
                          and not conn.execute("SELECT 1 FROM brief_calls WHERE edition_id=%s AND phase='revise'",
                                               (row["id"],)).fetchone())
                if repair:
                    revised_bundle = revision_bundle(row["bundle"], row["proposal"], review, rejected)
                    # Do not reserve a repair whose full frozen context cannot fit the runtime contract.
                    try:
                        prompt(revised_bundle, "revise")
                    except ValueError:
                        repair = False
                if repair:
                    conn.execute("UPDATE brief_editions SET state='revising',bundle=%s,review=%s,quality=%s WHERE id=%s",
                                 (Jsonb(revised_bundle), Jsonb(result), Jsonb(quality), row["id"]))
                else:
                    quality["revision_used"] = call["phase"] == "final_review"
                    conn.execute("""UPDATE brief_editions SET state='ready',proposal=%s,review=%s,rendered=%s,quality=%s
                    WHERE id=%s""", (Jsonb(proposal.model_dump(mode="json")) if proposal else None,
                                     Jsonb(result), Jsonb(parts), Jsonb(quality), row["id"]))
            conn.execute("UPDATE brief_calls SET state='completed',response=%s,result=%s,completed_at=%s WHERE id=%s",
                         (Jsonb(body), Jsonb(result), at, call["id"]))
            return {"state": "completed", "phase": call["phase"]}

    def fault(self, identity, code, seconds=0):
        at = schedule.utcnow()
        with self.db.transaction() as conn:
            row = conn.execute("SELECT edition_id FROM brief_calls WHERE id=%s AND state='running' FOR UPDATE", (identity,)).fetchone()
            if not row:
                return
            if code in {"busy", "quota", "unavailable"}:
                conn.execute("UPDATE brief_calls SET next_at=%s,error=%s WHERE id=%s",
                             (at+timedelta(seconds=max(5, min(seconds or 60, 604800))), code, identity))
            else:
                conn.execute("UPDATE brief_calls SET state='blocked',error=%s WHERE id=%s", (code, identity))
                conn.execute("UPDATE brief_editions SET state='blocked',error=%s WHERE id=%s AND committed_at IS NULL",
                             (code, row["edition_id"]))

    def flush(self, at=None):
        """Dispatcher also calls this: a slow/blocked model must not suppress the deadline notice."""
        at = at or schedule.utcnow()
        if not self.authorized():
            return {"state": "paused"}
        policy = self.policy()
        with self.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
            conn.execute("""UPDATE brief_editions SET state='stale',error='brief_policy_changed'
                WHERE committed_at IS NULL AND state NOT IN ('missed','stale') AND policy_digest<>%s""", (policy,))
            conn.execute("""UPDATE brief_editions SET state='missed',error='delivery_window_expired'
                WHERE committed_at IS NULL AND state NOT IN ('missed','stale') AND expires_at<=%s""", (at,))
            conn.execute("""UPDATE brief_calls c SET state='unresolved',error=COALESCE(c.error,'edition_window_closed')
                FROM brief_editions e WHERE e.id=c.edition_id AND c.state='running'
                AND (e.state IN ('missed','stale') OR e.due_at+interval '10 minutes'<=%s)""", (at,))
            rows = conn.execute("""SELECT * FROM brief_editions WHERE policy_digest=%s AND committed_at IS NULL
                AND state NOT IN ('missed','stale') AND due_at<=%s AND expires_at>%s
                AND (state='ready' OR due_at+interval '10 minutes'<=%s) ORDER BY due_at FOR UPDATE""",
                                (policy, at, at, at)).fetchall()
            for row in rows:
                if row["state"] != "ready":
                    bundle = self._freeze(conn, row)
                    parts, quality = render(None, bundle, fallback=row["error"] or "deadline")
                    # A partial, unreviewed proposal must never become the next edition's morning evidence.
                    row.update(bundle=bundle, rendered=parts, quality=quality, proposal=None)
                if row["publish"]:
                    project = conn.execute("""INSERT INTO projects(id,title,instruction,owner_user,channel)
                        VALUES(%s,%s,%s,%s,%s) RETURNING *""", (stable("brief-project:"+str(row["id"])),
                            f"{row['day']} {row['kind']} 시장 브리핑",
                            "이 발간본과 원문을 기준으로 후속 질문에 답한다. 새 정보는 새 조회 시각을 표시한다. "
                            "금융전략 업무는 사용자가 명시적으로 요청할 때만 전달한다.", row["owner_user"], row["channel"])).fetchone()
                    row["project_id"] = project["id"]
                    self._message(conn, row, project, 0)
                    for doc in row["bundle"].get("documents", []):
                        identity = f"brief:{row['id']}:{doc['id']}"
                        conn.execute("""INSERT INTO sources(id,title,uri,content,available_at,approved,project_id,synthetic,metadata)
                            VALUES(%s,%s,%s,%s,%s,true,%s,%s,%s) ON CONFLICT DO NOTHING""",
                                     (identity, doc["title"], doc["url"], doc["content"], doc["retrieved_at"], project["id"],
                                      self.company.settings.fixture_mode,
                                      Jsonb({"edition_id": str(row["id"]), "cutoff": row["definition"]["cutoff"],
                                             "published_at": doc["published_at"], "sha256": doc["sha256"]})))
                conn.execute("""UPDATE brief_editions SET state=%s,committed_at=%s,project_id=%s,bundle=%s,
                    rendered=%s,quality=%s,proposal=%s WHERE id=%s""",
                             ("committed" if row["publish"] else "previewed", at, row["project_id"], Jsonb(row["bundle"]),
                              Jsonb(row["rendered"]), Jsonb(row["quality"]), Jsonb(row["proposal"]) if row["proposal"] else None, row["id"]))
            parents = conn.execute("""SELECT e.*,o.sent_ts FROM brief_editions e JOIN brief_messages m ON m.edition_id=e.id
                JOIN outbox o ON o.id=m.id WHERE m.part=0 AND o.status='delivered' AND o.sent_ts IS NOT NULL
                AND e.expires_at>%s AND e.policy_digest=%s AND e.state='committed'""", (at, policy)).fetchall()
            for row in parents:
                project = self.company._project(conn, row["project_id"])
                if project["status"] != "active" or project["revision"] != 1:
                    continue
                project["thread_ts"] = row["sent_ts"]
                for part in range(1, len(row["rendered"])):
                    if not conn.execute("SELECT 1 FROM brief_messages WHERE edition_id=%s AND part=%s", (row["id"], part)).fetchone():
                        self._message(conn, row, project, part)
            return {"state": "flushed", "committed": len(rows)}

    def _message(self, conn, row, project, part):
        identity = stable(f"brief-message:{row['id']}:{part}")
        root = stable(f"brief-message:{row['id']}:0") if part else None
        self.company._message(conn, project, None, BRIEFER, "briefing", row["rendered"][part], message_id=identity)
        conn.execute("""INSERT INTO brief_messages(id,edition_id,part,root_id,policy_digest,expires_at)
            VALUES(%s,%s,%s,%s,%s,%s)""", (identity, row["id"], part, root, row["policy_digest"], row["expires_at"]))

    def gate(self, conn, row, *, claimed=False):
        at = schedule.utcnow()
        message = conn.execute("""SELECT m.*,e.owner_user FROM brief_messages m JOIN brief_editions e ON e.id=m.edition_id
            WHERE m.id=%s""", (row["id"],)).fetchone()
        project = self.company._project(conn, row["project_id"])
        valid = (message and self.authorized() and self.company.settings.briefing_publish_enabled
                 and message["policy_digest"] == self.policy() and message["expires_at"] > at
                 and row["channel"] == self.company.settings.briefing_channel_id
                 and message["owner_user"] == self.company.settings.briefing_owner_user
                 and project["status"] == "active" and project["revision"] == row["revision"])
        if valid and message["part"]:
            valid = bool(conn.execute("SELECT 1 FROM outbox WHERE id=%s AND status='delivered' AND sent_ts=%s",
                                      (message["root_id"], row["thread_ts"])).fetchone())
        if not valid:
            conn.execute("UPDATE outbox SET status='stale',error='brief_policy_or_receipt_changed' WHERE id=%s", (row["id"],))
            return False
        if conn.execute("SELECT 1 FROM runtime_control WHERE paused_until>%s", (at,)).fetchone():
            conn.execute("""UPDATE outbox SET status='pending',next_at=%s,started_at=NULL,
                attempts=GREATEST(0,attempts-%s),error='runtime_paused' WHERE id=%s""",
                         (at+timedelta(minutes=1), int(claimed), row["id"]))
            return False
        return True

    def status(self, *, include_rendered=False):
        s = self.company.settings
        with self.db.transaction() as conn:
            rows = conn.execute("""SELECT id,day,kind,state,due_at,cutoff,collected_at,committed_at,error,quality,
                publish,rendered,bundle->'collection_errors' AS collection_errors,
                market_data->'diagnostics' AS data_diagnostics FROM brief_editions
                WHERE channel=%s AND owner_user=%s ORDER BY due_at DESC LIMIT 20""",
                                (s.briefing_channel_id, s.briefing_owner_user)).fetchall()
            deliveries = conn.execute("""SELECT m.edition_id,m.part,o.status,o.error,o.sent_ts FROM brief_messages m
                JOIN outbox o ON o.id=m.id JOIN brief_editions e ON e.id=m.edition_id
                WHERE e.channel=%s AND e.owner_user=%s ORDER BY e.due_at DESC,m.part LIMIT 60""",
                                      (s.briefing_channel_id, s.briefing_owner_user)).fetchall()
            calls = conn.execute("""SELECT c.id,c.edition_id,c.phase,c.state,c.error,c.completed_at FROM brief_calls c
                JOIN brief_editions e ON e.id=c.edition_id WHERE e.channel=%s AND e.owner_user=%s
                ORDER BY e.due_at DESC,c.phase LIMIT 60""", (s.briefing_channel_id, s.briefing_owner_user)).fetchall()
        if not include_rendered:
            for row in rows:
                row.pop("rendered", None)
        return as_json({"enabled": s.briefing_enabled, "publish_enabled": s.briefing_publish_enabled,
                        "authorized": self.authorized(), "editions": rows, "deliveries": deliveries, "calls": calls,
                        "qualification": "Preview is not Slack delivery or five-trading-day qualification."})


def priority_pending(company):
    if not company.settings.briefing_enabled:
        return False
    store = BriefStore(company)
    if not store.authorized():
        return False
    at = schedule.utcnow()
    try:
        policy = store.policy()
    except (ValueError, OSError):
        # A broken optional calendar file must not stop existing news service.
        return False
    with company.db.transaction() as conn:
        return bool(conn.execute("""SELECT 1 FROM brief_editions WHERE policy_digest=%s AND committed_at IS NULL
            AND state IN ('collecting','planning','writing','reviewing','revising','final_reviewing') AND due_at>=%s AND due_at<=%s LIMIT 1""",
                                 (policy, at-timedelta(minutes=10),
                                  at+timedelta(minutes=schedule.PREPARATION_MINUTES+schedule.COLLECTION_MINUTES))).fetchone())

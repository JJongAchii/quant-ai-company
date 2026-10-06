from datetime import UTC, datetime, timedelta
from importlib.resources import files
from uuid import NAMESPACE_URL, uuid4, uuid5

from psycopg.types.json import Jsonb

from ..company import as_json
from ..contracts import ProviderRequest
from .evaluation import validate_replay_plan
from .observation import review_snapshot, safe_rows
from .policy import MaintenanceConfig, Triage, digest


class Deferred(Exception):
    pass


class Store:
    def __init__(self, company, config: MaintenanceConfig):
        self.company, self.db, self.config = company, company.db, config

    def initialize(self):
        self.db.migrate()
        with self.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(71350220)")
            conn.execute(files("quant_company.maintenance").joinpath("schema.sql").read_text())
        self.heartbeat()

    def heartbeat(self):
        with self.db.transaction() as conn:
            conn.execute("""UPDATE maintenance_control SET runtime=runtime || %s ||
                CASE WHEN %s AND NOT runtime ? 'improvements_started_at'
                THEN jsonb_build_object('improvements_started_at',now()) ELSE '{}'::jsonb END WHERE id=1""", (Jsonb(as_json({
                "enabled": self.config.enabled, "allowed_owners": self.config.allowed_owners,
                "max_daily_calls": self.config.max_daily_calls, "poll_seconds": self.config.poll_seconds,
                "heartbeat_at": datetime.now(UTC),
            })), self.company.settings.company_improvements_enabled))

    def prepare_review(self, job):
        """Freeze owner history when the durable request is picked up, before model analysis."""
        with self.db.transaction() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            conn.execute("SET LOCAL statement_timeout='5s'")
            payload = dict(job["payload"])
            review, inputs, review_digest = review_snapshot(
                conn, self.company, payload["owners"], datetime.now(UTC))
            # The explicit request is included even if the bounded recent-history sample omits it.
            request = conn.execute("""SELECT 'message:'||id::text AS key,project_id,task_id,author,kind,text,created_at
                FROM messages WHERE task_id=%s AND kind IN ('human','instruction') ORDER BY created_at LIMIT 1""",
                                   (payload["request_task_id"],)).fetchone()
            if not request:
                raise ValueError("review_requires_recorded_human_request")
            payload.update(review=review, replay_inputs=inputs, review_digest=review_digest,
                           observations=safe_rows([request]))
            conn.execute("UPDATE maintenance_jobs SET state='triage',payload=%s,error=NULL,updated_at=now() WHERE id=%s",
                         (Jsonb(payload), job["id"]))
            for row in payload["observations"] + review["evidence"]:
                conn.execute("""INSERT INTO maintenance_observations(key,job_id,body) VALUES (%s,%s,%s)
                    ON CONFLICT DO NOTHING""", (row["key"], job["id"], Jsonb(row)))

    def collect(self):
        """Anti-join by identity: a late commit with an older timestamp cannot fall behind a cursor."""
        with self.db.transaction() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            conn.execute("SET LOCAL statement_timeout='5s'")
            control = conn.execute("SELECT * FROM maintenance_control WHERE id=1 FOR UPDATE").fetchone()
            if control["next_observe_at"] > datetime.now(UTC):
                return None
            conn.execute("UPDATE maintenance_control SET next_observe_at=%s WHERE id=1",
                         (datetime.now(UTC) + timedelta(seconds=self.config.observe_seconds),))
            # A deferred head must not monopolize observation collection, but keep the buffer finite.
            active = conn.execute("""SELECT count(*) AS count,bool_or(error IS NULL) AS ready
                FROM maintenance_jobs WHERE state IN ('review','triage')""").fetchone()
            if active["ready"] or active["count"] >= 4:
                return None
            rows = conn.execute("""
                SELECT 'message:'||m.id::text AS key, m.project_id, m.task_id, m.author,
                       m.kind, left(m.text,1500) AS text, m.created_at
                FROM messages m JOIN projects p ON p.id=m.project_id
                WHERE p.owner_user=ANY(%s) AND (p.channel=ANY(%s) OR p.channel LIKE 'D%%') AND m.author NOT LIKE 'maintenance%%'
                  AND m.kind IN ('human','answer','delegation','peer','instruction','tool','status')
                  AND NOT EXISTS (SELECT 1 FROM maintenance_observations o WHERE o.key='message:'||m.id::text)
                ORDER BY m.created_at,m.id LIMIT 16
                """, (self.config.allowed_owners, self.company.settings.slack_allowed_channels)).fetchall()
            events = conn.execute("""
                SELECT 'event:'||e.id::text AS key,e.project_id,p.owner_user,e.kind,e.detail,e.created_at
                FROM events e JOIN projects p ON p.id=e.project_id
                WHERE p.owner_user=ANY(%s) AND (p.channel=ANY(%s) OR p.channel LIKE 'D%%')
                  AND e.kind IN ('turn_blocked','task_blocked','research_stage_waiting','data_watch_problem')
                  AND (e.kind<>'data_watch_problem' OR (p.thread_ts IS NOT NULL AND EXISTS(
                    SELECT 1 FROM data_watch_incidents d WHERE d.project_id=p.id AND d.state='active')))
                  AND (NOT %s OR (e.created_at >= %s::timestamptz AND e.kind<>'research_stage_waiting'
                    AND COALESCE(e.detail->>'reason','') NOT IN ('quota','daily_model_budget','task_turn_limit')))
                  AND NOT EXISTS (SELECT 1 FROM maintenance_observations o WHERE o.key='event:'||e.id::text)
                ORDER BY e.id LIMIT 4
                """, (self.config.allowed_owners, self.company.settings.slack_allowed_channels,
                      self.company.settings.company_improvements_enabled,
                      control["runtime"].get("improvements_started_at", datetime.now(UTC).isoformat()))).fetchall()
            from ..data_watch.reporting import incident_allowed
            from ..data_watch.store import DataWatchStore

            events = [event for event in events if event["kind"] != "data_watch_problem" or incident_allowed(
                conn, DataWatchStore(self.company), conn.execute("SELECT * FROM data_watch_incidents WHERE project_id=%s",
                                                               (event["project_id"],)).fetchone())]
            from ..staff.store import maintenance_observations

            owners = self.config.allowed_owners
            if self.company.settings.company_improvements_enabled:
                from .problems import failure_observations

                # Ordinary conversations/ideas do not trigger automatic model work. One owner per case.
                rows = []
                owners = [events[0]["owner_user"]] if events else []
                events = [event for event in events if event["owner_user"] in owners]
                staff = []
                for owner in owners or self.config.allowed_owners:
                    failures = [row for row in maintenance_observations(conn, [owner], unseen=True)
                                if row.get("grade", {}).get("objective_passed") is False
                                and datetime.fromisoformat(row["created_at"]) >= datetime.fromisoformat(
                                    control["runtime"]["improvements_started_at"])]
                    problems = failure_observations(conn, self.company, owner,
                                                    control["runtime"]["improvements_started_at"])
                    if failures or problems:
                        owners, staff = [owner], failures + problems
                        break
                rows = safe_rows(events + staff)
            else:
                rows = safe_rows(rows + events + maintenance_observations(conn, owners, unseen=True))
            if not rows:
                return None
            review, replay_inputs, review_digest = review_snapshot(
                conn, self.company, owners, datetime.now(UTC))
            payload = {"observations": rows, "review": review, "review_digest": review_digest,
                       "replay_inputs": replay_inputs, "owners": owners}
            job_id = str(uuid4())
            conn.execute("INSERT INTO maintenance_jobs(id,kind,state,payload) VALUES (%s,'triage','triage',%s)",
                         (job_id, Jsonb(payload)))
            for row in rows:
                conn.execute("INSERT INTO maintenance_observations(key,job_id,body) VALUES (%s,%s,%s)",
                             (row["key"], job_id, Jsonb(row)))
            if self.company.settings.company_improvements_enabled:
                from .cases import attach, identity

                project_id = next((row["project_id"] for row in rows if row.get("project_id")), None)
                source = (self.company._project(conn, project_id) if project_id else conn.execute(
                    """INSERT INTO projects(id,title,instruction,owner_user,channel)
                    VALUES (%s,'직원 평가 오류 점검','기록된 평가 실패를 진단한다.',%s,%s) RETURNING *""",
                    (identity(job_id + ":observation"), owners[0],
                     self.company.settings.improvements_channel_id)).fetchone())
                attach(conn, self.company, job_id, source)
            return job_id

    def next_job(self):
        with self.db.transaction() as conn:
            return conn.execute("""SELECT * FROM maintenance_jobs
                WHERE state IN ('review','triage','patch','design','evaluate','publish','ci','pr')
                ORDER BY (payload ? 'request_project_id') DESC,(error IS NULL) DESC,updated_at,id LIMIT 1""").fetchone()

    def check_authorization(self, job):
        # A paused historical review must not keep using an owner's records after access is revoked.
        owners = set(job["payload"].get("owners", []))
        if not owners or not owners <= set(self.config.allowed_owners) & set(self.company.settings.slack_allowed_users):
            raise ValueError("review_owner_authorization_changed")
        if job["payload"].get("slack_case_id"):
            from .cases import allowed

            with self.db.transaction() as conn:
                case = conn.execute("SELECT * FROM maintenance_cases WHERE id=%s",
                                    (job["payload"]["slack_case_id"],)).fetchone()
                if not case or not allowed(conn, self.company, case):
                    raise ValueError("review_case_scope_changed")
        if job["payload"].get("request_project_id"):
            from .requests import permitted

            with self.db.transaction() as conn:
                project = self.company._project(conn, job["payload"]["request_project_id"], lock=False)
            if not permitted(self.company, project) or project["owner_user"] not in owners:
                raise ValueError("review_owner_authorization_changed")
            if project["revision"] != job["payload"]["request_revision"]:
                raise ValueError("review_revision_changed")

    def save(self, job_id, state, *, payload=None, receipt=None, error=None):
        with self.db.transaction() as conn:
            conn.execute("""UPDATE maintenance_jobs SET state=%s, payload=COALESCE(%s,payload),
                receipt=COALESCE(%s,receipt),error=%s,updated_at=now() WHERE id=%s""",
                         (state, Jsonb(payload) if payload is not None else None,
                          Jsonb(receipt) if receipt is not None else None, error, job_id))

    def bind_diagnosis(self, job, snapshot, diagnosis):
        """Freeze each diagnostic revision; never overwrite a reserved request or replay input."""
        payload = job["payload"]
        previous = payload.get("diagnosis")
        if previous and previous["scope_digest"] == diagnosis["scope_digest"]:
            return True
        with self.db.transaction() as conn:
            revision = payload.get("diagnostic_revision", 0)
            reserved = conn.execute("SELECT 1 FROM maintenance_calls WHERE job_id=%s LIMIT 1", (job["id"],)).fetchone()
            if previous or payload.get("snapshot") or reserved:
                conn.execute("""INSERT INTO maintenance_revisions(job_id,revision,payload,receipt,reason)
                    VALUES (%s,%s,%s,%s,'current_evidence_changed') ON CONFLICT DO NOTHING""",
                             (job["id"], revision, Jsonb(payload), Jsonb(job["receipt"])))
                revision += 1
            if revision > 3:
                raise ValueError("evidence_churn_requires_review")
            if job["state"] != "triage":
                # A patch/evaluation belongs to its old inputs. Re-diagnose in a linked new job.
                identity = str(uuid5(NAMESPACE_URL, f"maintenance-recheck:{job['id']}:{diagnosis['scope_digest']}"))
                replacement = {key: payload[key] for key in ("owners", "observations", "review", "review_digest",
                               "replay_inputs", "request_project_id", "request_task_id", "request_revision", "instruction",
                               "slack_case_id")
                               if key in payload}
                replacement.update(predecessor=str(job["id"]), diagnostic_revision=revision)
                requested = "request_project_id" in replacement
                conn.execute("""INSERT INTO maintenance_jobs(id,kind,state,payload) VALUES (%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING""", (identity, "review" if requested else "triage",
                                                 "review" if requested else "triage", Jsonb(replacement)))
                conn.execute("""UPDATE maintenance_jobs SET state='superseded',
                    receipt=receipt || %s,error='current_evidence_changed',updated_at=now() WHERE id=%s""",
                             (Jsonb({"recheck_id": identity}), job["id"]))
                return False
            review, inputs, history_digest = review_snapshot(conn, self.company, payload["owners"], datetime.now(UTC))
            payload.update(snapshot=snapshot, diagnosis=diagnosis, diagnostic_revision=revision,
                           review=review, replay_inputs=inputs, review_digest=history_digest)
            payload.pop("investigation_evidence", None)
            payload.pop("investigation_requests", None)
            payload.pop("investigation_round", None)
            payload.pop("investigation_external", None)
            conn.execute("UPDATE maintenance_jobs SET payload=%s,error=NULL,updated_at=now() WHERE id=%s",
                         (Jsonb(payload), job["id"]))
        return True

    def finish_triage(self, job, result: Triage):
        finding = result.finding
        context = job["payload"]
        evidence = {item["key"]: item for item in context["observations"]
                    + context.get("review", {}).get("evidence", []) if not item.get("omitted")}
        diagnostic = context.get("diagnosis", {})
        current_keys = {r["key"] for r in diagnostic.get("source_files", [])}
        investigated = context.get("investigation_evidence", [])
        evidence.update({r["key"]: r for r in investigated})
        current_keys.update(r["key"] for r in investigated)
        external = context.get("investigation_external", [])
        evidence.update({r["key"]: r for r in external})
        if diagnostic:
            evidence[diagnostic["key"]] = {"key": diagnostic["key"], "scope_digest": diagnostic["scope_digest"]}
            evidence.update({r["key"]: r for r in diagnostic["source_files"]})
            current_keys.add(diagnostic["key"])
        known = set(evidence)
        if finding and not set(finding.evidence_keys) <= known:
            raise ValueError("unknown_or_omitted_evidence")
        if finding and not {case.request_key for case in finding.evaluation.cases} <= (
            set(context.get("replay_inputs", {})) & set(finding.evidence_keys)
        ):
            raise ValueError("replay_case_requires_cited_recorded_request")
        if finding:
            validate_replay_plan(finding, context.get("replay_inputs", {}))
            if not current_keys.intersection(finding.evidence_keys):
                raise ValueError("finding_requires_current_implementation_evidence")
            if not set(finding.evidence_keys) - current_keys - {r["key"] for r in external}:
                raise ValueError("finding_requires_recorded_failure_evidence")
        with self.db.transaction() as conn:
            if finding:
                problem_key = finding.problem_key
                existing = conn.execute("SELECT * FROM maintenance_jobs WHERE problem_key=%s", (problem_key,)).fetchone()
                predecessor = None
                if existing:
                    from ..system_state import assessment

                    truth = assessment(conn, existing["id"])
                    if truth["disposition"] in {"resolved", "invalidated"}:
                        fresh_failure = any(
                            datetime.fromisoformat(evidence[k]["created_at"]) > datetime.fromisoformat(truth["created_at"])
                            for k in finding.evidence_keys if k not in current_keys and evidence[k].get("created_at"))
                        if not fresh_failure:
                            conn.execute("UPDATE maintenance_jobs SET state='done',error=NULL,receipt=%s,updated_at=now() WHERE id=%s",
                                         (Jsonb({"case_id": str(existing["id"]), "reason": "No new failure after case assessment."}), job["id"]))
                            return
                    design_upgrade = (existing["payload"].get("finding", {}).get("evaluation", {}).get("mode") == "design_only"
                                      and finding.evaluation.mode == "regression")
                    if existing["state"] == "superseded" or truth["disposition"] in {"resolved", "invalidated"} or design_upgrade:
                        predecessor = str(existing["id"])
                        problem_key += "@" + digest([diagnostic["scope_digest"], finding.evidence_keys])[:16]
                case_id = str(uuid5(NAMESPACE_URL, "quant-company-maintenance:" + problem_key))
                inputs = {case.request_key: context["replay_inputs"][case.request_key]
                          for case in finding.evaluation.cases}
                payload = {"finding": finding.model_dump(), "observations": [evidence[key] for key in finding.evidence_keys],
                           "snapshot": context["snapshot"], "review": context.get("review", {}),
                           "review_digest": context.get("review_digest"), "owners": context["owners"],
                           "replay_inputs": inputs, "replay_inputs_digest": digest(inputs),
                           "evaluation_plan_digest": digest(finding.evaluation.model_dump())}
                payload.update(diagnosis=diagnostic, diagnostic_revision=context.get("diagnostic_revision", 0),
                               predecessor=predecessor, citation_normalization=context.get("citation_normalization"),
                               investigation_evidence=investigated,
                               investigation_external=external,
                               investigation_requests=context.get("investigation_requests", []))
                payload.update({key: context[key] for key in
                                ("request_project_id", "request_task_id", "request_revision", "instruction", "slack_case_id")
                                if key in context})
                if finding.evaluation.mode == "staff_replay":
                    from ..staff.comparisons import source_run

                    source_run(conn, payload)
                existing = conn.execute("SELECT payload->'owners' AS owners FROM maintenance_jobs WHERE problem_key=%s",
                                        (problem_key,)).fetchone()
                if existing and set(existing["owners"]) != set(context["owners"]):
                    raise ValueError("case_owner_scope_mismatch")
                state = "design" if finding.evaluation.mode == "design_only" else "patch"
                conn.execute("""INSERT INTO maintenance_jobs(id,kind,state,problem_key,payload)
                    VALUES (%s,'repair',%s,%s,%s) ON CONFLICT (problem_key) DO NOTHING""",
                             (case_id, state, problem_key, Jsonb(payload)))
                # A resolved/failed case does not restart itself from its own discussion or CI failure.
                # Preserve repeated evidence separately in the triage receipt, linked to the same case.
                receipt = {"case_id": case_id, "reason": result.reason,
                           "evidence_keys": finding.evidence_keys}
            else:
                receipt = {"reason": result.reason}
            conn.execute("UPDATE maintenance_jobs SET state='done',error=NULL,receipt=%s,updated_at=now() WHERE id=%s",
                         (Jsonb(receipt), job["id"]))

    def prepare_call(self, job, phase, prompt, *, model=None, reasoning_effort=None, web_search=False):
        revision = job["payload"].get("diagnostic_revision", 0)
        call_id = f"maint-{job['id']}" + (f"-r{revision}" if revision else "") + f"-{phase}"
        with self.db.transaction() as conn:
            call = conn.execute("SELECT * FROM maintenance_calls WHERE id=%s", (call_id,)).fetchone()
            if call and call["response"]:
                return call
            if call:
                if call["due_at"] > datetime.now(UTC):
                    raise Deferred("model_retry_due")
            if conn.execute("""SELECT 1 FROM turns t JOIN tasks k ON k.id=t.task_id
                JOIN projects p ON p.id=k.project_id
                WHERE t.status IN ('queued','waiting','running') AND t.due_at<=now()
                  AND k.revision=p.revision LIMIT 1""").fetchone():
                raise Deferred("company_work_has_priority")
            pause = conn.execute("SELECT paused_until FROM runtime_control WHERE id=1").fetchone()
            if pause["paused_until"] and pause["paused_until"] > datetime.now(UTC):
                raise Deferred("subscription_paused")
            if call:
                return call
            conn.execute("INSERT INTO daily_usage(day,reserved) VALUES (CURRENT_DATE,0) ON CONFLICT DO NOTHING")
            usage = conn.execute("SELECT reserved FROM daily_usage WHERE day=CURRENT_DATE FOR UPDATE").fetchone()
            count = conn.execute("SELECT count(*) AS n FROM maintenance_calls WHERE created_at>=CURRENT_DATE").fetchone()
            from ..owner_controls import effective_limits

            limits = effective_limits(conn, self.company, self.config.max_daily_calls)
            if ((limits["company"] is not None and usage["reserved"] >= limits["company"])
                    or (limits["maintenance"] is not None and count["n"] >= limits["maintenance"])):
                raise Deferred("daily_model_budget")
            if model is None:
                role = self.company.roles["engineer"]
                model, reasoning_effort = role.model, role.reasoning_effort
            # An explicit replay model retains the frozen effort, including legacy None.
            request = ProviderRequest(request_id=call_id, model=model, reasoning_effort=reasoning_effort,
                                      prompt=prompt, web_search=web_search)
            conn.execute("UPDATE daily_usage SET reserved=reserved+1 WHERE day=CURRENT_DATE")
            return conn.execute("""INSERT INTO maintenance_calls(id,job_id,request) VALUES (%s,%s,%s)
                RETURNING *""", (call_id, job["id"], Jsonb(request.model_dump()))).fetchone()

    def record_call(self, call_id, response):
        with self.db.transaction() as conn:
            conn.execute("UPDATE maintenance_calls SET response=%s,error=NULL WHERE id=%s",
                         (Jsonb(response.model_dump(mode="json")), call_id))

    def defer_call(self, call_id, reason, seconds):
        until = datetime.now(UTC) + timedelta(seconds=max(30, min(seconds or 60, 604800)))
        with self.db.transaction() as conn:
            conn.execute("UPDATE maintenance_calls SET error=%s,due_at=%s WHERE id=%s", (reason, until, call_id))
            if reason == "quota" and not self.company.settings.model_accounts_enabled:
                conn.execute("UPDATE runtime_control SET paused_until=GREATEST(paused_until,%s),reason='quota' WHERE id=1",
                             (until,))

    def finish_pr(self, job, receipt):
        with self.db.transaction() as conn:
            # Report as the existing director, clearly attributed to the maintenance process.
            # The dedicated kind is excluded from observation to prevent self-generated loops.
            projects = {item["project_id"] for item in job["payload"]["observations"] if item.get("project_id")}
            if job["payload"].get("request_project_id"):
                projects.add(job["payload"]["request_project_id"])
            # New cases have one authoritative delivery/approval destination. Legacy jobs retain theirs.
            if job["payload"].get("slack_case_id"):
                projects = set()
            for project_id in sorted(projects):
                project = self.company._project(conn, project_id)
                if project["owner_user"] not in self.config.allowed_owners:
                    continue
                message_id = str(uuid5(NAMESPACE_URL, f"maintenance-pr:{job['id']}:{project_id}"))
                if not conn.execute("SELECT 1 FROM messages WHERE id=%s", (message_id,)).fetchone():
                    mode = job["payload"]["finding"]["evaluation"]["mode"]
                    summary = ("조직·행동 개선의 검토용 설계 PR을 만들었습니다. 효과는 아직 미검증입니다. "
                               if mode == "design_only" else "수정안의 검증을 확인하고 검토용 PR을 만들었습니다. ")
                    self.company._message(conn, project, None, "director", "maintenance",
                                          "[개선 담당] " + summary +
                                          f"{receipt['pr']['url']}\n운영 반영은 검토 후 진행합니다.",
                                          message_id=message_id, notify_owner=True)
            state = "pr_open" if receipt["pr"]["state"] == "open" else "closed"
            conn.execute("UPDATE maintenance_jobs SET state=%s,receipt=%s,error=NULL,updated_at=now() WHERE id=%s",
                         (state, Jsonb(receipt), job["id"]))

    def report_reviews(self):
        from .requests import permitted, progress_text, report, service

        # One notice per distinct durable state, including terminal failure/no-finding and budget waits.
        with self.db.transaction() as conn:
            if not conn.execute("SELECT pg_try_advisory_xact_lock(71350227) AS ok").fetchone()["ok"]:
                return
            jobs = conn.execute("""SELECT * FROM maintenance_jobs j WHERE kind='review'
                AND NOT j.payload ? 'slack_case_id'
                AND NOT EXISTS (SELECT 1 FROM maintenance_cases c WHERE c.request_id=j.id)
                ORDER BY COALESCE(receipt->>'last_report_scan_at',''),created_at LIMIT 50""").fetchall()
            for job in jobs:
                conn.execute("UPDATE maintenance_jobs SET receipt=jsonb_set(receipt,'{last_report_scan_at}',%s) WHERE id=%s",
                             (Jsonb(datetime.now(UTC).isoformat()), job["id"]))
                project = self.company._project(conn, job["payload"]["request_project_id"])
                owner = project["owner_user"]
                if (not permitted(self.company, project) or owner not in self.config.allowed_owners
                        or project["revision"] != job["payload"]["request_revision"]):
                    continue
                value = report(conn, job, owner)
                identity = str(uuid5(NAMESPACE_URL, "maintenance-review-notice:" + digest(value)))
                if conn.execute("SELECT 1 FROM messages WHERE id=%s", (identity,)).fetchone():
                    continue
                self.company._message(conn, project, None, "director", "maintenance",
                                      "[개선 담당] " + progress_text(value, service(conn, self.company, owner)),
                                      message_id=identity,
                                      notify_owner=(value['state'] == 'blocked' or
                                                    (value['state'] in {'done', 'applied', 'closed'}
                                                     and not value.get('error'))))
                if value.get("pr"):
                    pr_id = str(uuid5(NAMESPACE_URL, f"maintenance-pr:{value['case_id']}:{project['id']}"))
                    if not conn.execute("SELECT 1 FROM messages WHERE id=%s", (pr_id,)).fetchone():
                        self.company._message(conn, project, None, "director", "maintenance",
                                              "[개선 담당] 진단에 연결된 PR: " + value["pr"]["url"] +
                                              "\n내용과 검증 범위를 검토한 뒤 반영 여부를 결정해 주세요.", message_id=pr_id,
                                              notify_owner=value['state'] == 'pr_open' and not value.get('error'))

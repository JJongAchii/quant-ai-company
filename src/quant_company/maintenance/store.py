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
            conn.execute("UPDATE maintenance_control SET runtime=%s WHERE id=1", (Jsonb(as_json({
                "enabled": self.config.enabled, "allowed_owners": self.config.allowed_owners,
                "max_daily_calls": self.config.max_daily_calls, "poll_seconds": self.config.poll_seconds,
                "heartbeat_at": datetime.now(UTC),
            })),))

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
            # Bound the backlog. Do not consume records until a triage job is durable.
            if conn.execute("SELECT 1 FROM maintenance_jobs WHERE state IN ('review','triage') LIMIT 1").fetchone():
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
                SELECT 'event:'||e.id::text AS key,e.project_id,e.kind,e.detail,e.created_at
                FROM events e JOIN projects p ON p.id=e.project_id
                WHERE p.owner_user=ANY(%s) AND (p.channel=ANY(%s) OR p.channel LIKE 'D%%') AND e.kind IN ('turn_blocked','task_blocked')
                  AND NOT EXISTS (SELECT 1 FROM maintenance_observations o WHERE o.key='event:'||e.id::text)
                ORDER BY e.id LIMIT 4
                """, (self.config.allowed_owners, self.company.settings.slack_allowed_channels)).fetchall()
            rows = safe_rows(rows + events)
            if not rows:
                return None
            review, replay_inputs, review_digest = review_snapshot(
                conn, self.company, self.config.allowed_owners, datetime.now(UTC))
            payload = {"observations": rows, "review": review, "review_digest": review_digest,
                       "replay_inputs": replay_inputs, "owners": self.config.allowed_owners}
            job_id = str(uuid4())
            conn.execute("INSERT INTO maintenance_jobs(id,kind,state,payload) VALUES (%s,'triage','triage',%s)",
                         (job_id, Jsonb(payload)))
            for row in rows:
                conn.execute("INSERT INTO maintenance_observations(key,job_id,body) VALUES (%s,%s,%s)",
                             (row["key"], job_id, Jsonb(row)))
            return job_id

    def next_job(self):
        with self.db.transaction() as conn:
            return conn.execute("""SELECT * FROM maintenance_jobs
                WHERE state IN ('review','triage','patch','design','evaluate','publish','ci','pr')
                ORDER BY (payload ? 'request_project_id') DESC,updated_at,id LIMIT 1""").fetchone()

    def check_authorization(self, job):
        # A paused historical review must not keep using an owner's records after access is revoked.
        owners = set(job["payload"].get("owners", []))
        if not owners or not owners <= set(self.config.allowed_owners) & set(self.company.settings.slack_allowed_users):
            raise ValueError("review_owner_authorization_changed")
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

    def finish_triage(self, job, result: Triage):
        finding = result.finding
        context = job["payload"]
        evidence = {item["key"]: item for item in context["observations"]
                    + context.get("review", {}).get("evidence", []) if not item.get("omitted")}
        known = set(evidence)
        if finding and not set(finding.evidence_keys) <= known:
            raise ValueError("unknown_or_omitted_evidence")
        if finding and not {case.request_key for case in finding.evaluation.cases} <= (
            set(context.get("replay_inputs", {})) & set(finding.evidence_keys)
        ):
            raise ValueError("replay_case_requires_cited_recorded_request")
        if finding:
            validate_replay_plan(finding, context.get("replay_inputs", {}))
        with self.db.transaction() as conn:
            if finding:
                case_id = str(uuid5(NAMESPACE_URL, "quant-company-maintenance:" + finding.problem_key))
                inputs = {case.request_key: context["replay_inputs"][case.request_key]
                          for case in finding.evaluation.cases}
                payload = {"finding": finding.model_dump(), "observations": [evidence[key] for key in finding.evidence_keys],
                           "snapshot": context["snapshot"], "review": context.get("review", {}),
                           "review_digest": context.get("review_digest"), "owners": context["owners"],
                           "replay_inputs": inputs, "replay_inputs_digest": digest(inputs),
                           "evaluation_plan_digest": digest(finding.evaluation.model_dump())}
                payload.update({key: context[key] for key in
                                ("request_project_id", "request_task_id", "request_revision") if key in context})
                existing = conn.execute("SELECT payload->'owners' AS owners FROM maintenance_jobs WHERE problem_key=%s",
                                        (finding.problem_key,)).fetchone()
                if existing and set(existing["owners"]) != set(context["owners"]):
                    raise ValueError("case_owner_scope_mismatch")
                state = "design" if finding.evaluation.mode == "design_only" else "patch"
                conn.execute("""INSERT INTO maintenance_jobs(id,kind,state,problem_key,payload)
                    VALUES (%s,'repair',%s,%s,%s) ON CONFLICT (problem_key) DO NOTHING""",
                             (case_id, state, finding.problem_key, Jsonb(payload)))
                # A resolved/failed case does not restart itself from its own discussion or CI failure.
                # Preserve repeated evidence separately in the triage receipt, linked to the same case.
                receipt = {"case_id": case_id, "reason": result.reason,
                           "evidence_keys": finding.evidence_keys}
            else:
                receipt = {"reason": result.reason}
            conn.execute("UPDATE maintenance_jobs SET state='done',receipt=%s,updated_at=now() WHERE id=%s",
                         (Jsonb(receipt), job["id"]))

    def prepare_call(self, job, phase, prompt, *, model=None):
        call_id = f"maint-{job['id']}-{phase}"
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
            if (usage["reserved"] >= self.company.settings.company_max_daily_turns
                    or count["n"] >= self.config.max_daily_calls):
                raise Deferred("daily_model_budget")
            model = model or self.company.roles["engineer"].model
            request = ProviderRequest(request_id=call_id, model=model, prompt=prompt)
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
            if reason == "quota":
                conn.execute("UPDATE runtime_control SET paused_until=GREATEST(paused_until,%s),reason='quota' WHERE id=1",
                             (until,))

    def finish_pr(self, job, receipt):
        with self.db.transaction() as conn:
            # Report as the existing director, clearly attributed to the maintenance process.
            # The dedicated kind is excluded from observation to prevent self-generated loops.
            projects = {item["project_id"] for item in job["payload"]["observations"] if item.get("project_id")}
            if job["payload"].get("request_project_id"):
                projects.add(job["payload"]["request_project_id"])
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
                                          message_id=message_id)
            state = "pr_open" if receipt["pr"]["state"] == "open" else "closed"
            conn.execute("UPDATE maintenance_jobs SET state=%s,receipt=%s,error=NULL,updated_at=now() WHERE id=%s",
                         (state, Jsonb(receipt), job["id"]))

    def report_reviews(self):
        from .requests import permitted, progress_text, report, service

        # One notice per distinct durable state, including terminal failure/no-finding and budget waits.
        with self.db.transaction() as conn:
            jobs = conn.execute("""SELECT * FROM maintenance_jobs WHERE kind='review'
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
                                      message_id=identity)
                if value.get("pr"):
                    pr_id = str(uuid5(NAMESPACE_URL, f"maintenance-pr:{value['case_id']}:{project['id']}"))
                    if not conn.execute("SELECT 1 FROM messages WHERE id=%s", (pr_id,)).fetchone():
                        self.company._message(conn, project, None, "director", "maintenance",
                                              "[개선 담당] 진단에 연결된 PR: " + value["pr"]["url"] +
                                              "\n내용과 검증 범위를 검토한 뒤 반영 여부를 결정해 주세요.", message_id=pr_id)

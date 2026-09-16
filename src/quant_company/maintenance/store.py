import json
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from uuid import NAMESPACE_URL, uuid4, uuid5

from psycopg.types.json import Jsonb

from ..company import as_json
from ..contracts import ProviderRequest
from .policy import SECRET, MaintenanceConfig, Triage


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

    def collect(self):
        """Anti-join by identity: a late commit with an older timestamp cannot fall behind a cursor."""
        with self.db.transaction() as conn:
            control = conn.execute("SELECT * FROM maintenance_control WHERE id=1 FOR UPDATE").fetchone()
            if control["next_observe_at"] > datetime.now(UTC):
                return None
            conn.execute("UPDATE maintenance_control SET next_observe_at=%s WHERE id=1",
                         (datetime.now(UTC) + timedelta(seconds=self.config.observe_seconds),))
            # Bound the backlog. Do not consume records until a triage job is durable.
            if conn.execute("SELECT 1 FROM maintenance_jobs WHERE state='triage' LIMIT 1").fetchone():
                return None
            rows = conn.execute("""
                SELECT 'message:'||m.id::text AS key, m.project_id, m.task_id, m.author,
                       m.kind, left(m.text,2500) AS text, m.created_at
                FROM messages m JOIN projects p ON p.id=m.project_id
                WHERE p.owner_user=ANY(%s) AND m.author NOT LIKE 'maintenance%%'
                  AND m.kind IN ('human','answer','delegation','peer','instruction','tool','status')
                  AND NOT EXISTS (SELECT 1 FROM maintenance_observations o WHERE o.key='message:'||m.id::text)
                ORDER BY m.created_at,m.id LIMIT 16
                """, (self.config.allowed_owners,)).fetchall()
            events = conn.execute("""
                SELECT 'event:'||e.id::text AS key,e.project_id,e.kind,e.detail,e.created_at
                FROM events e JOIN projects p ON p.id=e.project_id
                WHERE p.owner_user=ANY(%s) AND e.kind='turn_blocked'
                  AND NOT EXISTS (SELECT 1 FROM maintenance_observations o WHERE o.key='event:'||e.id::text)
                ORDER BY e.id LIMIT 4
                """, (self.config.allowed_owners,)).fetchall()
            rows = as_json(rows + events)
            if not rows:
                return None
            # Omit whole suspect observations; never treat redaction as exhaustive secret detection.
            for row in rows:
                if SECRET.search(json.dumps(row)):
                    key = row["key"]
                    row.clear()
                    row.update(key=key, omitted="possible_secret; operator review required")
            job_id = str(uuid4())
            conn.execute("INSERT INTO maintenance_jobs(id,kind,state,payload) VALUES (%s,'triage','triage',%s)",
                         (job_id, Jsonb({"observations": rows})))
            for row in rows:
                conn.execute("INSERT INTO maintenance_observations(key,job_id,body) VALUES (%s,%s,%s)",
                             (row["key"], job_id, Jsonb(row)))
            return job_id

    def next_job(self):
        with self.db.transaction() as conn:
            return conn.execute("""SELECT * FROM maintenance_jobs
                WHERE state IN ('triage','patch','publish','ci','pr')
                ORDER BY updated_at,id LIMIT 1""").fetchone()

    def save(self, job_id, state, *, payload=None, receipt=None, error=None):
        with self.db.transaction() as conn:
            conn.execute("""UPDATE maintenance_jobs SET state=%s, payload=COALESCE(%s,payload),
                receipt=COALESCE(%s,receipt),error=%s,updated_at=now() WHERE id=%s""",
                         (state, Jsonb(payload) if payload is not None else None,
                          Jsonb(receipt) if receipt is not None else None, error, job_id))

    def finish_triage(self, job, result: Triage):
        finding = result.finding
        known = {item["key"] for item in job["payload"]["observations"] if not item.get("omitted")}
        if finding and not set(finding.evidence_keys) <= known:
            raise ValueError("unknown_or_omitted_evidence")
        with self.db.transaction() as conn:
            if finding:
                case_id = str(uuid5(NAMESPACE_URL, "quant-company-maintenance:" + finding.problem_key))
                payload = {"finding": finding.model_dump(), "observations": [item for item in
                           job["payload"]["observations"] if item["key"] in finding.evidence_keys]}
                conn.execute("""INSERT INTO maintenance_jobs(id,kind,state,problem_key,payload)
                    VALUES (%s,'repair','patch',%s,%s) ON CONFLICT (problem_key) DO NOTHING""",
                             (case_id, finding.problem_key, Jsonb(payload)))
                # A resolved/failed case does not restart itself from its own discussion or CI failure.
                # Preserve repeated evidence separately in the triage receipt, linked to the same case.
                receipt = {"case_id": case_id, "reason": result.reason,
                           "evidence_keys": finding.evidence_keys}
            else:
                receipt = {"reason": result.reason}
            conn.execute("UPDATE maintenance_jobs SET state='done',receipt=%s,updated_at=now() WHERE id=%s",
                         (Jsonb(receipt), job["id"]))

    def prepare_call(self, job, phase, prompt):
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
            model = self.company.roles["engineer"].model
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
            for project_id in sorted(projects):
                project = self.company._project(conn, project_id)
                if project["owner_user"] not in self.config.allowed_owners:
                    continue
                message_id = str(uuid5(NAMESPACE_URL, f"maintenance-pr:{job['id']}:{project_id}"))
                if not conn.execute("SELECT 1 FROM messages WHERE id=%s", (message_id,)).fetchone():
                    self.company._message(conn, project, None, "director", "maintenance",
                                          "[개선 담당] 수정안의 CI를 확인하고 검토용 PR을 만들었습니다. "
                                          f"{receipt['pr']['url']}\n운영 반영은 검토 후 진행합니다.",
                                          message_id=message_id)
            state = "pr_open" if receipt["pr"]["state"] == "open" else "closed"
            conn.execute("UPDATE maintenance_jobs SET state=%s,receipt=%s,error=NULL,updated_at=now() WHERE id=%s",
                         (state, Jsonb(receipt), job["id"]))

"""Research intent, owner approval and durable worker reconciliation.

All cross-table mutations lock project before job. A lost worker lease is never recycled.
The only strings sent to a model are allowlisted status facts and verified report sources.
"""

import hmac
import re
import secrets
from uuid import UUID

from psycopg.types.json import Jsonb

from ..company import PolicyError, as_json, fingerprint, now, stable
from .contracts import Assignment, ResearchTool, WorkerUpdate
from .recipes import load_recipe, recipe_digest

BUSY = ("claimed", "running", "cancel_requested", "uncertain")
TERMINAL = ("awaiting_audit", "completed", "cancelled", "failed")
STATE_TEXT = {
    "pending_approval": "명세 확인·승인 대기", "queued": "3070 실행 대기", "claimed": "3070 접수·상태 확인 중",
    "running": "3070 실행 중", "cancel_requested": "중단 요청·워커 확인 대기", "uncertain": "실행 상태 대사 필요",
    "received": "결과 수신·감사 대조 대기", "awaiting_audit": "감사 확인 필요·성과 비공개",
    "completed": "고정 결과 재현·리포트 생성 완료", "cancelled": "중단 확인", "failed": "실행 오류·결과 보존",
}


def parse_command(text):
    value = text.strip()
    if value == "연구 상태":
        return {"action": "status"}
    match = re.fullmatch(r"연구 (승인|취소) ([0-9a-f-]{36})(?: ([0-9a-f]{12}))?", value)
    if not match:
        return None
    try:
        job_id = str(UUID(match[2]))
    except ValueError:
        return None
    if (match[1] == "승인") != bool(match[3]):
        return None
    return {"action": "approve" if match[1] == "승인" else "cancel", "job_id": job_id, "digest": match[3]}


def cancel_project(conn, project_id):
    """Called within the transaction that changes the project revision."""
    conn.execute("""UPDATE research_jobs SET state=CASE WHEN state=ANY(%s) THEN 'cancel_requested'
        ELSE 'cancelled' END,error='project_revision_changed',updated_at=now()
        WHERE project_id=%s AND state NOT IN ('completed','cancelled','failed','awaiting_audit')""",
                 (list(BUSY), project_id))


def public_job(row, *, worker_seen=None):
    result = {key: row[key] for key in ("id", "revision", "recipe_id", "manifest_digest", "state",
                                       "created_at", "updated_at", "heartbeat_at")}
    result["status_text"] = STATE_TEXT[row["state"]]
    if row["recipe_id"] != "kr-etf-p11-replay-v1" and row["state"] == "completed":
        result["status_text"] = "개발 실험·독립 감사·보고서 완료"
    if row.get("error") == "publication_unavailable" and row["state"] == "received":
        result["status_text"] = "결과 수신 완료·보고서 저장소 연결 확인 중"
    seen = row["heartbeat_at"] or worker_seen
    result["worker_connected_recently"] = bool(seen and (now() - seen).total_seconds() <= 120)
    result["approval_required"] = row["state"] == "pending_approval"
    result["performance_visible"] = row["state"] == "completed"
    # Do not expose worker exceptions or a partially verified report to models.
    if row["state"] == "completed" and row.get("report"):
        result["source_id"] = row["report"]["source_id"]
        result["report_uri"] = row["report"]["uri"]
    return as_json(result)


class ResearchStore:
    def __init__(self, company):
        self.company = company

    def require_enabled(self):
        if not self.company.settings.company_research_enabled:
            raise PolicyError("Research execution is not activated")

    def _manifest_valid(self, conn, row):
        if row["recipe_id"] == "kr-etf-p11-replay-v1":
            return row["manifest_digest"] == recipe_digest(load_recipe(row["recipe_id"]))
        from .adaptive_contracts import AdaptiveManifest, digest_model

        if not self.company.settings.company_autonomous_research_enabled:
            return False
        manifest = AdaptiveManifest.model_validate(row["manifest"])
        mission = conn.execute("SELECT * FROM research_missions WHERE id=%s", (manifest.mission_id,)).fetchone()
        return bool(mission and str(row["mission_id"]) == str(manifest.mission_id)
                    and str(row["trial_id"]) == str(manifest.trial_id)
                    and digest_model(manifest) == row["manifest_digest"]
                    and mission["manifest_digest"] == manifest.mission_digest
                    and mission["spec"] == manifest.spec.model_dump(mode="json")
                    and mission["revision"] == row["revision"]
                    and mission["approval_event_id"] == row["approval_event_id"]
                    and mission["owner_user"] == row["approved_by"])

    def revalidate(self, job_id, value):
        """Operator recovery of a received archive; never creates another execution lease."""
        self.require_enabled()
        with self.company.db.transaction() as conn:
            project, row = self._locked(conn, job_id)
            if (row["revision"] != value.expected_revision or row["revision"] != project["revision"]
                    or project["status"] != "active" or not row["approval_event_id"]
                    or not self._manifest_valid(conn, row)
                    or row["artifact_sha256"] != value.artifact_sha256 or not row["artifact_path"]):
                raise PolicyError("Research revalidation requires the same approved revision and received archive")
            if row["state"] in {"received", "completed"}:
                return {"ok": True, "state": row["state"], "duplicate": True}
            if row["state"] != "awaiting_audit":
                raise PolicyError("Only a withheld received archive may be revalidated")
            conn.execute("""UPDATE research_jobs SET state='received',error=NULL,
                notified_state=NULL,updated_at=now() WHERE id=%s""", (job_id,))
            self.company._event(conn, "research_revalidation_requested", {
                "job_id": job_id, "artifact_sha256": value.artifact_sha256,
                "revision": value.expected_revision, "reason": value.reason, "actor": "operator",
                "validator_company_commit": self.company.settings.company_code_commit,
            }, str(project["id"]))
            return {"ok": True, "state": "received", "duplicate": False}

    def status(self, conn, project_id):
        worker = conn.execute("SELECT last_seen FROM research_workers WHERE id='worker'").fetchone()
        rows = conn.execute("SELECT * FROM research_jobs WHERE project_id=%s ORDER BY created_at DESC LIMIT 10",
                            (project_id,)).fetchall()
        return {"enabled": self.company.settings.company_research_enabled,
                "jobs": [public_job(r, worker_seen=worker["last_seen"] if worker else None) for r in rows]}

    def request(self, conn, project, task, recipe_id):
        self.require_enabled()
        if project["status"] != "active":
            raise PolicyError("Research request requires an active project")
        if not project["channel"] or not project["thread_ts"]:
            raise PolicyError("Research approval requires the owner's Slack thread")
        if project["owner_user"] not in self.company.settings.slack_allowed_users:
            raise PolicyError("Research owner is not authorized")
        if not re.fullmatch(r"[a-f0-9]{40}", self.company.settings.company_code_commit):
            raise PolicyError("Research submission requires a pinned company release")
        recipe = load_recipe(recipe_id)
        digest = recipe_digest(recipe)
        identity = stable(f"research:{project['id']}:{project['revision']}:{recipe.id}:{digest}")
        row = conn.execute("SELECT * FROM research_jobs WHERE id=%s", (identity,)).fetchone()
        if row:
            return public_job(row)
        row = conn.execute("""INSERT INTO research_jobs
            (id,project_id,task_id,revision,recipe_id,manifest,manifest_digest,company_commit)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                           (identity, project["id"], task["id"], project["revision"], recipe.id,
                            Jsonb(recipe.model_dump()), digest, self.company.settings.company_code_commit)).fetchone()
        from .approvals import ReplayApprovalAdapter, publish_approval

        target = next(t for t in ReplayApprovalAdapter(self.company).targets(conn, project) if str(t.target_id) == identity)
        publish_approval(self.company, conn, project, task["id"], target,
                         f"연구 실행 명세: {recipe.title}\n{recipe.description}\n"
                         f"실행: 3070 · 코드 {recipe.code_commit[:12]} · 지시 v{project['revision']}\n"
                         f"확인한 명세로 실행하려면 승인 버튼이나 이 스레드에 ‘승인’을 보내주세요.\n"
                         f"전체 명령: 연구 승인 {identity} {digest[:12]}")
        self.company._event(conn, "research_requested", {"job_id": identity, "manifest_digest": digest}, project["id"])
        return public_job(row)

    def tool(self, conn, project, task, arguments):
        if task["agent"] != "director":
            raise PolicyError("Research control requires the director")
        value = ResearchTool.model_validate(arguments)
        if value.action in {"catalog", "status"}:
            if value.recipe_id or value.job_id:
                raise PolicyError("Research catalog/status accepts no job or recipe override")
            if value.action == "status":
                return self.status(conn, project["id"])
            recipe = load_recipe()
            return {"enabled": self.company.settings.company_research_enabled,
                    "recipes": [{"id": recipe.id, "title": recipe.title, "description": recipe.description,
                                 "manifest_digest": recipe_digest(recipe), "requires_owner_approval": True}]}
        if value.action == "request" and value.recipe_id and not value.job_id:
            return self.request(conn, project, task, value.recipe_id)
        # Model proposals cannot approve or cancel arbitrary running research. Cancellation uses
        # the authenticated owner's explicit command or the existing owner task-control router.
        raise PolicyError("Use an owner research cancellation command; models cannot approve/cancel jobs")

    def owner_command(self, conn, project, task, event_key, command):
        if not event_key.startswith("slack:") or project["owner_user"] not in self.company.settings.slack_allowed_users:
            raise PolicyError("Research commands require authenticated Slack owner input")
        if command["action"] == "status":
            data = self.status(conn, project["id"])
            text = "연구 업무 상태\n" + ("\n".join(
                f"• {j['id']}: {j['status_text']}" + (" · 워커 연결 대기" if not j["worker_connected_recently"]
                                                      and j["state"] in {"queued", *BUSY} else "")
                for j in data["jobs"]) or "등록된 연구 실행이 없습니다.")
        else:
            row = conn.execute("SELECT * FROM research_jobs WHERE id=%s AND project_id=%s FOR UPDATE",
                               (command["job_id"], project["id"])).fetchone()
            if not row:
                raise PolicyError("Research job is not in this owner's thread")
            if command["action"] == "approve":
                self.require_enabled()
                if (row["revision"] != project["revision"] or project["status"] != "active"
                        or command["digest"] != row["manifest_digest"][:12]
                        or row["manifest_digest"] != recipe_digest(load_recipe(row["recipe_id"]))):
                    raise PolicyError("Research approval does not match the current specification")
                if row["state"] == "pending_approval":
                    conn.execute("""UPDATE research_jobs SET state='queued',approval_event_id=%s,approved_by=%s,
                        approved_at=now(),updated_at=now() WHERE id=%s""",
                                 (event_key, project["owner_user"], row["id"]))
                    self.company._event(conn, "research_approved", {"job_id": str(row["id"]),
                                        "manifest_digest": row["manifest_digest"], "approval_event_id": event_key}, project["id"])
                    text = "승인한 연구를 3070 대기열에 등록했습니다. 연결이 없으면 업무를 보존하고 기다립니다."
                else:
                    text = "이 명세의 기존 실행을 유지합니다. " + STATE_TEXT[row["state"]]
            else:
                state = "cancel_requested" if row["state"] in BUSY else "cancelled"
                if row["state"] in {"completed", "failed", "cancelled"}:
                    state = row["state"]
                conn.execute("UPDATE research_jobs SET state=%s,updated_at=now() WHERE id=%s", (state, row["id"]))
                self.company._event(conn, "research_cancel_requested", {"job_id": str(row["id"]),
                                    "owner_event_id": event_key}, project["id"])
                text = STATE_TEXT[state] + ". 실행 상태가 불명확하면 재제출하지 않고 확인합니다."
        conn.execute("UPDATE tasks SET status='completed',result=%s WHERE id=%s", (text, task["id"]))
        self.company._message(conn, project, task["id"], "director", "status", text)
        return text

    def _locked(self, conn, job_id, lease_token=None):
        row = conn.execute("SELECT project_id FROM research_jobs WHERE id=%s", (job_id,)).fetchone()
        if not row:
            raise PolicyError("Unknown research job")
        project = self.company._project(conn, str(row["project_id"]))
        job = conn.execute("SELECT * FROM research_jobs WHERE id=%s FOR UPDATE", (job_id,)).fetchone()
        if lease_token is not None and not hmac.compare_digest(job["lease_token"] or "", lease_token):
            raise PolicyError("Research lease mismatch")
        if job["revision"] != project["revision"] and job["state"] not in TERMINAL:
            cancel_project(conn, project["id"])
            job = conn.execute("SELECT * FROM research_jobs WHERE id=%s", (job_id,)).fetchone()
        return project, job

    def poll(self):
        with self.company.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(71350220)")
            conn.execute("INSERT INTO research_workers(id) VALUES ('worker') ON CONFLICT(id) DO UPDATE SET last_seen=now()")
            old = conn.execute("SELECT id FROM research_jobs WHERE worker_id='worker' AND state=ANY(%s)",
                               (list(BUSY),)).fetchone()
            if old:
                project, row = self._locked(conn, old["id"])
                action = ("cancel" if row["state"] == "cancel_requested" else
                          "run" if row["state"] == "claimed" and row["sequence"] == 0 else "reconcile")
            else:
                if not self.company.settings.company_research_enabled:
                    return {"assignment": None}
                queued = conn.execute("""SELECT r.id FROM research_jobs r JOIN projects p ON p.id=r.project_id
                    WHERE r.state='queued' AND p.status='active' AND r.revision=p.revision
                    AND (r.mission_id IS NULL OR EXISTS(SELECT 1 FROM research_missions m
                        WHERE m.id=r.mission_id AND m.state='active'))
                    AND NOT EXISTS(SELECT 1 FROM tasks t WHERE t.project_id=p.id AND t.kind='routing'
                                   AND t.status NOT IN ('completed','superseded'))
                    ORDER BY r.priority,r.created_at,r.id LIMIT 1""").fetchone()
                if not queued:
                    return {"assignment": None}
                project, row = self._locked(conn, queued["id"])
                from ..task_control import waiting_router

                if row["state"] != "queued" or project["status"] != "active" or waiting_router(conn, project["id"]):
                    return {"assignment": None}
                if not self._manifest_valid(conn, row):
                    raise PolicyError("Registered research manifest changed")
                row = conn.execute("""UPDATE research_jobs SET state='claimed',worker_id='worker',lease_token=%s,
                    claimed_at=now(),updated_at=now() WHERE id=%s RETURNING *""",
                                   (secrets.token_hex(32), row["id"])).fetchone()
                action = "run"
                self.company._event(conn, "research_claimed", {"job_id": str(row["id"]), "worker": "worker"}, project["id"])
            values = dict(job_id=row["id"], project_id=row["project_id"], revision=row["revision"],
                          recipe_id=row["recipe_id"], manifest_digest=row["manifest_digest"],
                          approval_event_id=row["approval_event_id"], lease_token=row["lease_token"], action=action)
            if row["recipe_id"] != "kr-etf-p11-replay-v1":
                from .adaptive_contracts import AdaptiveAssignment

                assignment = AdaptiveAssignment(**values, manifest=row["manifest"])
            else:
                assignment = Assignment(**values)
            return {"assignment": assignment.model_dump(mode="json")}

    def heartbeat(self, job_id, update: WorkerUpdate):
        with self.company.db.transaction() as conn:
            _, row = self._locked(conn, job_id, update.lease_token)
            digest = fingerprint(update.model_dump(exclude={"lease_token"}))
            if update.sequence <= row["sequence"]:
                if update.sequence == row["sequence"] and row["update_digest"] != digest:
                    raise PolicyError("Worker sequence reused for a different update")
                return {"ok": True, "state": row["state"], "duplicate": True}
            if row["state"] in TERMINAL or row["state"] == "received":
                return {"ok": True, "state": row["state"], "duplicate": True}
            state = update.state
            if row["state"] == "cancel_requested" and state in {"running", "uncertain"}:
                state = "cancel_requested"
            # Error text is deliberately reduced; raw worker logs remain in the worker receipt.
            reason = update.reason if re.fullmatch(r"[a-z_]{1,80}", update.reason) else "worker_error" if update.reason else None
            conn.execute("""UPDATE research_jobs SET state=%s,sequence=%s,update_digest=%s,
                heartbeat_at=now(),updated_at=now(),error=%s WHERE id=%s""",
                         (state, update.sequence, digest, reason, job_id))
            return {"ok": True, "state": state, "duplicate": False}

    def artifact_received(self, job_id, lease_token, artifact_path, digest):
        with self.company.db.transaction() as conn:
            _, row = self._locked(conn, job_id, lease_token)
            if row["artifact_sha256"]:
                if row["artifact_sha256"] != digest:
                    raise PolicyError("Different artifact already recorded for this research execution")
                return {"ok": True, "state": row["state"], "duplicate": True}
            if row["state"] in {"cancel_requested", "cancelled"}:
                state = "cancelled"
            elif row["state"] not in {"claimed", "running", "uncertain"}:
                raise PolicyError("Research job cannot accept a result in its current state")
            else:
                state = "received"
            conn.execute("""UPDATE research_jobs SET artifact_sha256=%s,artifact_path=%s,state=%s,
                heartbeat_at=now(),updated_at=now() WHERE id=%s""", (digest, str(artifact_path), state, job_id))
            return {"ok": True, "state": state, "duplicate": False}

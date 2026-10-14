"""Owner-activated research questions and service-recorded gaps. No model calls."""

import json
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from ..company import PolicyError, as_json, fingerprint, stable
from .approvals import ApprovalTarget, publish_approval
from .missions import MissionStore, typed
from .planning_contracts import GapRequest, QuestionSpec

OPEN_QUESTION_LIMIT = 3


def require_enabled(company):
    if not company.settings.research_planning_enabled:
        raise PolicyError("Research planning is disabled")


class QuestionStore:
    def __init__(self, company):
        self.company = company

    def draft(self, conn, project, payload, *, actor, origin="owner_request", program_id=None,
              parent_question_id=None):
        require_enabled(self.company)
        spec = typed(QuestionSpec, payload)
        if project["status"] != "active":
            raise PolicyError("Research questions require an active project")
        self.company._check_sources(conn, project["id"], spec.source_refs)
        if program_id is not None:
            try:
                program_id = str(UUID(str(program_id)))
            except ValueError as exc:
                raise PolicyError("Question program ID is invalid") from exc
            found = conn.execute("SELECT project_id FROM research_programs WHERE id=%s", (program_id,)).fetchone()
            if not found or found["project_id"] != project["id"]:
                raise PolicyError("Question program is outside this project")
        value = spec.model_dump(mode="json")
        digest = fingerprint(value)
        identity = stable(f"research-question:{project['id']}:{project['revision']}:{program_id}:{digest}")
        if not conn.execute("SELECT 1 FROM research_questions WHERE id=%s", (identity,)).fetchone():
            conn.execute("""INSERT INTO research_questions(id,project_id,owner_user,project_revision,program_id,digest,
                title,origin,parent_question_id,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (identity, project["id"], project["owner_user"], project["revision"], program_id, digest,
                 spec.question[:200], origin, parent_question_id, actor))
            conn.execute("""INSERT INTO research_question_revisions(question_id,revision,payload,digest,created_by)
                VALUES(%s,1,%s,%s,%s)""", (identity, Jsonb(value), digest, actor))
            self.company._event(conn, "research_question_proposed", {"question_id": identity, "digest": digest,
                "origin": origin}, project["id"])
        return self.snapshot(conn, identity)

    def owner_command(self, conn, question_id, *, owner, revision, manifest_digest, event_key, action):
        require_enabled(self.company)
        row = conn.execute("SELECT * FROM research_questions WHERE id=%s FOR UPDATE", (question_id,)).fetchone()
        if not row:
            raise PolicyError("Unknown research question")
        project = self.company._project(conn, row["project_id"])
        identity = {"id": row["id"], "owner_user": row["owner_user"], "revision": row["project_revision"],
                    "manifest_digest": row["digest"]}
        MissionStore(self.company)._owner(conn, project, identity, owner=owner, revision=revision,
            manifest_digest=manifest_digest, event_key=event_key, action=action, target_kind="research_question")
        if action == "approve":
            if row["state"] == "open" and row["activated_event_id"] == event_key:
                return self.snapshot(conn, question_id)
            if row["state"] != "proposed":
                raise PolicyError("Question activation requires a proposed question")
            active = conn.execute("""SELECT count(*) AS n FROM research_questions
                WHERE project_id=%s AND state IN ('open','waiting')""", (row["project_id"],)).fetchone()["n"]
            if active >= OPEN_QUESTION_LIMIT:
                raise PolicyError("Open research question limit reached; close a question first")
            conn.execute("UPDATE research_questions SET state='open',activated_event_id=%s,updated_at=now() WHERE id=%s",
                         (event_key, question_id))
        elif action == "cancel":
            if row["state"] != "closed":
                conn.execute("UPDATE research_questions SET state='closed',updated_at=now() WHERE id=%s", (question_id,))
        else:
            raise PolicyError("Unsupported research question control")
        self.company._event(conn, "research_question_" + action, {"question_id": str(question_id),
            "digest": manifest_digest, "owner_event_id": event_key}, row["project_id"])
        return self.snapshot(conn, question_id)

    def snapshot(self, conn, question_id):
        row = conn.execute("SELECT * FROM research_questions WHERE id=%s", (question_id,)).fetchone()
        if not row:
            raise PolicyError("Unknown research question")
        revision = conn.execute("""SELECT payload FROM research_question_revisions WHERE question_id=%s
            AND revision=%s""", (question_id, row["current_revision"])).fetchone()
        sets = conn.execute("""SELECT id,question_revision,state,char_total,digest,created_at FROM research_evidence_sets
            WHERE question_id=%s ORDER BY created_at DESC,id DESC LIMIT 3""", (question_id,)).fetchall()
        gaps = conn.execute("""SELECT id,kind,subject,resolver,required_evidence,recheck_on,state FROM research_gaps
            WHERE question_id=%s AND state='open' ORDER BY created_at,id""", (question_id,)).fetchall()
        return as_json({key: row[key] for key in ("id", "project_id", "program_id", "state", "origin", "priority",
            "current_revision", "digest", "title", "activated_event_id", "created_at")}
            | {"spec": revision["payload"], "evidence_sets": sets, "open_gaps": gaps})

    def status(self, conn, project):
        rows = conn.execute("SELECT id FROM research_questions WHERE project_id=%s ORDER BY created_at,id",
                            (project["id"],)).fetchall()
        return {"questions": [self.snapshot(conn, row["id"]) for row in rows],
                "limits": {"open_questions": OPEN_QUESTION_LIMIT},
                "authority": "Read-only. Questions open only through authenticated owner approval."}


def open_gap(conn, project_id, request: GapRequest, *, dependency_fingerprint, question_id=None,
             evidence_set_id=None, run_id=None, candidate_id=None):
    """One open gap per subject; repeated detection links to it instead of duplicating."""
    request = typed(GapRequest, request)
    key = fingerprint({"kind": request.kind, "subject": request.subject})
    conn.execute("""INSERT INTO research_gaps(id,project_id,question_id,evidence_set_id,run_id,candidate_id,kind,
        subject,subject_key,resolver,required_evidence,recheck_on,dependency_fingerprint)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
        (uuid4(), project_id, question_id, evidence_set_id, run_id, candidate_id, request.kind, request.subject, key,
         request.resolver, request.required_evidence, request.recheck_on, dependency_fingerprint))
    return str(conn.execute("SELECT id FROM research_gaps WHERE project_id=%s AND subject_key=%s AND state='open'",
                            (project_id, key)).fetchone()["id"])


class QuestionApprovalAdapter:
    kind = "research_question"

    def __init__(self, company):
        self.company = company

    def targets(self, conn, project):
        if not self.company.settings.research_planning_enabled:
            return []
        return [ApprovalTarget(kind=self.kind, target_id=row["id"], revision=row["project_revision"],
            manifest_digest=row["digest"], title=row["title"],
            state="pending_approval" if row["state"] == "proposed" else row["state"], created_at=row["created_at"])
            for row in conn.execute("SELECT * FROM research_questions WHERE project_id=%s ORDER BY created_at,id",
                                    (project["id"],))]

    def apply(self, conn, project, task, event_key, action, target):
        QuestionStore(self.company).owner_command(conn, target.target_id, owner=project["owner_user"],
            revision=target.revision, manifest_digest=target.manifest_digest, event_key=event_key, action=action)
        text = ("연구 질문을 열었습니다. 이 질문 범위에서 근거를 모으고 후보를 비교합니다. "
                "실험이나 프로그램 과제 등록은 별도 승인 경로를 따릅니다."
                if action == "approve" else "연구 질문 제안을 닫았습니다.")
        conn.execute("UPDATE tasks SET status='completed',result=%s WHERE id=%s", (text, task["id"]))
        self.company._message(conn, project, task["id"], "director", "status", text)


def planning_tool(company, conn, project, task, arguments):
    if task["agent"] != "director":
        raise PolicyError("Planning tools require the director")
    require_enabled(company)
    store = QuestionStore(company)
    if arguments == {"action": "planning_status"}:
        return store.status(conn, project)
    if (arguments.get("action") != "planning_question_draft"
            or not {"action", "spec"} <= set(arguments) <= {"action", "spec", "program_id"}):
        raise PolicyError("Planning tools only draft questions or read status; the owner activates questions")
    snapshot = store.draft(conn, project, arguments["spec"], actor="director", program_id=arguments.get("program_id"))
    target = next(t for t in QuestionApprovalAdapter(company).targets(conn, project) if str(t.target_id) == snapshot["id"])
    if target.state == "pending_approval" and not conn.execute("""SELECT 1 FROM research_approval_bindings
            WHERE target_kind='research_question' AND target_id=%s AND manifest_digest=%s""",
            (target.target_id, target.manifest_digest)).fetchone():
        publish_approval(company, conn, project, task["id"], target,
            "연구 질문 승인 요청: " + snapshot["spec"]["question"]
            + "\n승인하면 이 질문으로 근거를 모으고 후보를 비교합니다. 실험·프로그램 권한은 포함하지 않습니다.\n"
            + json.dumps(snapshot["spec"], ensure_ascii=False, indent=2))
    return snapshot

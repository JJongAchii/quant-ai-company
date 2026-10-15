"""Read-only planning commands. Never calls models; database work is rolled back."""

import json
from pathlib import Path
from uuid import UUID

from ..company import Company, PolicyError, as_json
from .planning_retrieval import compute
from .planning_store import QuestionStore
from .planning_triage import replay


def run_report(conn, run_id):
    try:
        run_id = str(UUID(str(run_id)))
    except ValueError as exc:
        raise PolicyError("Malformed planning run ID") from exc
    run = conn.execute("SELECT * FROM research_planning_runs WHERE id=%s", (run_id,)).fetchone()
    if not run:
        raise PolicyError("Unknown planning run")
    question = conn.execute("""SELECT payload FROM research_question_revisions WHERE question_id=%s AND revision=%s""",
                            (run["question_id"], run["question_revision"])).fetchone()
    evidence = conn.execute("SELECT digest,state,notes,members FROM research_evidence_sets WHERE id=%s",
                            (run["evidence_set_id"],)).fetchone()
    usage = conn.execute("""SELECT count(*) FILTER (WHERE t.request IS NOT NULL) AS requests,
        coalesce(sum((t.response->'usage'->>'input_tokens')::bigint),0)::bigint AS input_tokens,
        coalesce(sum((t.response->'usage'->>'cached_input_tokens')::bigint),0)::bigint AS cached_input_tokens,
        coalesce(sum((t.response->'usage'->>'output_tokens')::bigint),0)::bigint AS output_tokens
        FROM turns t JOIN research_stage_attempts a ON a.task_id=t.task_id
        JOIN research_mission_stages s ON s.id=a.stage_id WHERE s.planning_run_id=%s""", (run_id,)).fetchone()
    # Attributed to this run's stage tasks, so concurrent production activity cannot hide or fake an effect.
    side_effects = conn.execute("""WITH stage_tasks AS (SELECT a.task_id FROM research_stage_attempts a
            JOIN research_mission_stages s ON s.id=a.stage_id WHERE s.planning_run_id=%(run)s)
        SELECT (SELECT count(*) FROM messages WHERE task_id IN (SELECT task_id FROM stage_tasks)) AS messages,
        (SELECT count(*) FROM outbox o JOIN messages m ON m.id=o.id
            WHERE m.task_id IN (SELECT task_id FROM stage_tasks)) AS outbox,
        (SELECT count(*) FROM tasks WHERE parent_id IN (SELECT task_id FROM stage_tasks)) AS delegated_tasks,
        (SELECT count(*) FROM research_program_tasks p JOIN research_planning_candidates c
            ON c.task_digest=p.digest AND p.program_id=%(program)s WHERE c.run_id=%(run)s) AS registered_program_tasks""",
        {"run": run_id, "program": run["program_id"]}).fetchone()
    return as_json({
        "run": {key: run[key] for key in ("id", "question_id", "question_revision", "program_id", "mode", "state",
                                          "outcome", "hold_reason", "created_at", "updated_at")},
        "question": question["payload"],
        "evidence_set": {"digest": evidence["digest"], "state": evidence["state"], "notes": evidence["notes"],
                         "members": [{key: m[key] for key in ("source_id", "role", "rank", "full_text", "chars")}
                                     for m in evidence["members"]]},
        "stages": conn.execute("""SELECT stage,attempt,state,error,context->'_planning_hold' AS hold
            FROM research_mission_stages WHERE planning_run_id=%s ORDER BY created_at,id""", (run_id,)).fetchall(),
        "candidates": conn.execute("""SELECT ordinal,route,digest,task_digest,payload FROM research_planning_candidates
            WHERE run_id=%s ORDER BY ordinal""", (run_id,)).fetchall(),
        "reviews": conn.execute("""SELECT c.ordinal,r.kind,r.payload FROM research_planning_reviews r
            JOIN research_planning_candidates c ON c.id=r.candidate_id WHERE r.run_id=%s ORDER BY c.ordinal,r.kind""",
            (run_id,)).fetchall(),
        "decision": conn.execute("""SELECT payload,service_order,override FROM research_planning_decisions
            WHERE run_id=%s""", (run_id,)).fetchone(),
        "gaps": conn.execute("""SELECT kind,subject,resolver,required_evidence,recheck_on,state FROM research_gaps
            WHERE run_id=%s ORDER BY created_at,id""", (run_id,)).fetchall(),
        "follow_up_questions": conn.execute("""SELECT id,state,title FROM research_questions
            WHERE parent_question_id=%s AND origin='follow_up' ORDER BY created_at,id""",
            (run["question_id"],)).fetchall(),
        "usage": usage,
        "side_effects": side_effects,
        "scope": "Shadow planning record. A literature and plan review, not an experiment result."})


def command(settings, action, *, question=None, bundle=None, run=None, company=None):
    if action == "replay-triage":
        return replay(json.loads(Path(bundle).read_text()))
    company = company or Company(settings)
    with company.db.transaction() as conn:
        try:
            if action == "status":
                projects = conn.execute("SELECT DISTINCT project_id FROM research_questions ORDER BY project_id").fetchall()
                return as_json({"projects": [QuestionStore(company).status(conn, company._project(
                    conn, row["project_id"], lock=False)) for row in projects]})
            if action == "preview":
                return compute(company, conn, question)
            if action == "run-report":
                return run_report(conn, run)
            raise ValueError("Unknown research planning action")
        finally:
            conn.rollback()

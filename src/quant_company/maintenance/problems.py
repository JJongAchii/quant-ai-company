"""Current technical failures only; observation never replays the failed operation."""

from .policy import digest


def _contract_error(error):
    if error in {"invalid_output", "stage_response_rejected", "stage_contract_rejected",
                 "invalid_assessment_response", "output_format_retry_limit"}:
        return error
    if "validation error" in error and "[type=" in error:
        # Pydantic diagnostics may contain private research values after the message.
        return error.split("[type=", 1)[0].strip()[:400]
    return None


def failure_observations(conn, company, owner, since):
    rows = []
    tables = conn.execute("""SELECT to_regclass('research_programs') AS programs,
        to_regclass('research_mission_stages') AS stages,
        to_regclass('staff_independent_reviews') AS reviews""").fetchone()
    if tables["programs"] and tables["stages"]:
        stages = conn.execute("""SELECT s.id,s.stage,s.actor,s.attempt,s.error,s.task_id,
            s.program_id,s.mission_id,s.updated_at,p.id AS project_id
            FROM research_mission_stages s
            LEFT JOIN research_missions rm ON rm.id=s.mission_id
            LEFT JOIN research_programs rp ON rp.id=COALESCE(s.program_id,rm.program_id)
            JOIN projects p ON p.id=COALESCE(rp.project_id,rm.project_id)
            JOIN tasks t ON t.id=s.task_id
            WHERE p.owner_user=%s AND p.status='active' AND t.revision=p.revision
              AND t.agent=s.actor AND t.kind='research_stage'
              AND (rp.state='active' OR (rp.id IS NULL AND rm.state='active'))
              AND (s.mission_id IS NULL OR rm.state='active')
              AND s.state='waiting' AND s.error IS NOT NULL AND s.updated_at>=%s
              AND s.attempt>=2
              AND (p.channel=ANY(%s) OR p.channel LIKE 'D%%')
            ORDER BY s.updated_at DESC,s.id LIMIT 16""",
            (owner, since, company.settings.slack_allowed_channels)).fetchall()
        for stage in stages:
            error = _contract_error(stage["error"])
            if not error:
                continue
            key = "research-contract:" + digest([str(stage["id"]), stage["attempt"]])[:32]
            if conn.execute("SELECT 1 FROM maintenance_observations WHERE key=%s", (key,)).fetchone():
                continue
            rows.append({"key": key, "project_id": stage["project_id"], "task_id": stage["task_id"],
                "author": stage["actor"], "kind": "research_contract_failure", "created_at": stage["updated_at"],
                "detail": {"stage_id": str(stage["id"]), "stage": stage["stage"],
                    "attempt": stage["attempt"], "error": error, "program_id": stage["program_id"],
                    "mission_id": stage["mission_id"]},
                "text": "A current approved research stage repeatedly failed its response contract. "
                        "Diagnose the service contract; do not change research decisions, data readiness or approval.",
                "scope": "Technical diagnosis only; private research values and results are not supplied."})
            if len(rows) == 4:
                break
    if tables["reviews"]:
        reviews = conn.execute("""SELECT v.id,v.run_id,v.model,v.error,v.created_at,r.employee
            FROM staff_independent_reviews v JOIN staff_runs r ON r.id=v.run_id
            WHERE r.owner_user=%s AND r.state='completed' AND v.state='blocked'
              AND v.error IN ('uncertain','invalid_review') AND v.created_at>=%s
              AND NOT EXISTS(SELECT 1 FROM maintenance_observations o WHERE o.key='staff-review:'||v.id)
            ORDER BY v.created_at DESC,v.id LIMIT 4""", (owner, since)).fetchall()
        rows.extend({"key": "staff-review:" + review["id"], "author": review["employee"],
            "kind": "staff_independent_review_failure", "created_at": review["created_at"],
            "detail": {"review_id": review["id"], "run_id": str(review["run_id"]),
                "model": review["model"], "error": review["error"],
                "operator_reconciliation_required": review["error"] == "uncertain"},
            "text": "An independent explanation review is blocked. Preserve the original request and receipts. "
                    "An uncertain outcome requires operator reconciliation; do not replay it or score it as an "
                    "employee failure. Investigate the transport and validation boundary without weakening it.",
            "scope": "Operational review failure only; the objective employee grade remains unchanged."}
            for review in reviews)
    return rows

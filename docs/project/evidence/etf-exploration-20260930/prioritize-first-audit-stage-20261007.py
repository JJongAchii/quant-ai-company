"""Prioritize the already-queued current audit, preserving signed research resources."""

import json
import pathlib
import subprocess

code = r'''
import json
from psycopg.types.json import Jsonb
from quant_company.company import Company
from quant_company.config import Settings
from quant_company.task_control import held
company = Company(Settings())
operation = "first-scientific-audit-priority:5e5e2013:20261007"
with company.db.transaction() as conn:
    project = company._project(conn, "9aac0de4-2b97-5195-a720-287d324234f3")
    assert project["revision"] == 5 and project["status"] == "active"
    prior = conn.execute("SELECT id,detail FROM events WHERE project_id=%s AND kind=%s AND detail->>'operation_id'=%s",
        (project["id"], "research_audit_priority_reconciled", operation)).fetchone()
    if prior:
        result = {**prior["detail"], "event_id": prior["id"], "already_committed": True}
    else:
        program = conn.execute("SELECT * FROM research_programs WHERE id=%s FOR UPDATE",
            ("f7deaf96-e677-5afe-93d4-18ac387043bb",)).fetchone()
        assert program["state"] == "active" and program["revision"] == 5
        assert program["manifest_digest"] == "c3ba5268d71c87bd2c6226160bc720498137bd291396a054970a1f1e7ebcf2db"
        assert program["approval_event_id"].startswith("slack:")
        stage = conn.execute("SELECT * FROM research_mission_stages WHERE id=%s FOR UPDATE",
            ("5e5e2013-373c-5868-9fc9-242985e085b3",)).fetchone()
        assert stage["stage"] == "audit" and stage["actor"] == "validator" and stage["attempt"] == 1
        assert stage["state"] == "running" and stage["error"] is None and stage["result"] is None
        assert not stage["context"].get("_audit_hold")
        task = conn.execute("SELECT * FROM tasks WHERE id=%s FOR UPDATE", (stage["task_id"],)).fetchone()
        assert str(task["id"]) == "a88948c2-58fc-5aa6-897a-76a691dd51dc"
        assert task["priority"] == 100 and task["status"] == "pending" and task["turn_count"] == 2
        assert not held(conn, project, task)
        turn = conn.execute("SELECT * FROM turns WHERE id=%s FOR UPDATE",
            ("8520ba60-39ff-52e3-8af4-bb3a02ffcad0",)).fetchone()
        assert turn["task_id"] == task["id"] and turn["status"] == "queued" and turn["request"] is None
        assert turn["workflow_started"] and turn["response"] is None
        assert conn.execute("SELECT 1 FROM events WHERE project_id=%s AND kind=%s AND id=759",
            (project["id"], "research_audit_lineage_delivery_reconciled")).fetchone()
        detail = {"schema_version": 1, "operation_id": operation,
            "program_id": str(program["id"]), "program_digest": program["manifest_digest"],
            "stage_id": str(stage["id"]), "task_id": str(task["id"]), "turn_id": str(turn["id"]),
            "previous_priority": 100, "priority": 0,
            "reason": "Normal Temporal execution repeatedly defers for an unrelated higher_priority_request",
            "signed_resources_priority_changed": False, "provider_request_changed": False,
            "context_or_audit_verdict_changed": False, "other_tasks_changed": False,
            "provider_calls_by_operator": 0, "scientific_jobs_by_operator": 0,
            "authority": "Existing owner request to get actual approved research through execution and reporting; root-reviewed in PR105",
            "state": "current_audit_task_prioritized_for_normal_execution"}
        conn.execute("UPDATE tasks SET priority=0 WHERE id=%s", (task["id"],))
        event = conn.execute("INSERT INTO events(project_id,kind,detail) VALUES(%s,%s,%s) RETURNING id",
            (project["id"], "research_audit_priority_reconciled", Jsonb(detail))).fetchone()
        result = {**detail, "event_id": event["id"], "already_committed": False}
print(json.dumps(result))
'''
completed = subprocess.run(["docker", "exec", "-i", "quant-company-api-1", "python",
    "/app/entrypoint.py", "python", "-"], input=code, capture_output=True, text=True, timeout=60)
output = pathlib.Path("/var/lib/quant-company/releases/first-audit-priority-reconciled-20261007.json")
if completed.returncode:
    error = output.with_suffix(".private-error.log")
    error.write_text(completed.stderr)
    error.chmod(0o600)
    raise RuntimeError("audit_priority_reconciliation_failed_check_private_host_log")
result = json.loads(completed.stdout)
output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
output.chmod(0o600)
print(json.dumps(result, indent=2))

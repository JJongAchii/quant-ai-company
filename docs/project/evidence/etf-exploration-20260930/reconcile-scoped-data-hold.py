"""Resume only candidate2's technical data-format hold after the aligned cutover.

Preserve all original requests, responses, reads, attempts and scientific authority.
A durable project event reconciles a lost process receipt without replaying science.
"""

import hashlib
import json
import pathlib
import subprocess

SOURCE = "86aea8cdf03b5543666813c97689acb1e8158a6d"
STATE = pathlib.Path("/var/lib/quant-company")
REMEDIATION = "scoped-data-schema-86:candidate2:attempt3"
OUTPUT = STATE / "releases" / ("scoped-data-hold-reconciled-" + SOURCE + ".json")
cutover = json.loads((STATE / "releases" / ("scoped-data-cutover-" + SOURCE + ".json")).read_bytes())
assert cutover["phase"] == "server_and_worker_active" and cutover["source_commit"] == SOURCE
assert pathlib.Path("/opt/quant-company/current").resolve().name == SOURCE
native_path = STATE / "codex/jobs/ba475f3d-6feb-572e-a419-746c6e8d2cc6.validation.json"
native = json.loads(native_path.read_bytes())
assert native["state"] == "complete" and native["artifact_contract_valid"] and native["canonical_scope_matched"]
assert native["source_commit"] == SOURCE
assert cutover["worker_alignment"]["phase"] == "active_worker_aligned"
rows = json.loads(subprocess.check_output([
    "docker", "inspect", "quant-company-api-1", "quant-company-worker-1"
], text=True))
for row in rows:
    assert row["State"]["Running"]
    assert "COMPANY_CODE_COMMIT=" + SOURCE in row["Config"]["Env"]
    assert SOURCE in row["Config"]["Image"]

code = r'''
import json
from psycopg.types.json import Jsonb
from quant_company.company import Company, fingerprint
from quant_company.config import Settings
company = Company(Settings())
remediation = "scoped-data-schema-86:candidate2:attempt3"
with company.db.transaction() as conn:
    project = company._project(conn, "9aac0de4-2b97-5195-a720-287d324234f3")
    assert project["revision"] == 5 and project["status"] == "active"
    program = conn.execute("SELECT * FROM research_programs WHERE id=%s FOR UPDATE",
        ("f7deaf96-e677-5afe-93d4-18ac387043bb",)).fetchone()
    assert program["state"] == "active" and program["revision"] == 5
    assert program["manifest_digest"] == "c3ba5268d71c87bd2c6226160bc720498137bd291396a054970a1f1e7ebcf2db"
    assert program["approval_event_id"].startswith("slack:")
    previous = conn.execute("SELECT id,detail FROM events WHERE project_id=%s AND kind=%s "
        "AND detail->>'remediation_id'=%s ORDER BY id LIMIT 1",
        (project["id"], "research_data_format_reconciled", remediation)).fetchone()
    if previous:
        result = {**previous["detail"], "event_id": previous["id"], "already_committed": True}
    else:
        stage = conn.execute("SELECT * FROM research_mission_stages WHERE id=%s FOR UPDATE",
            ("0b9b6ce9-6c3d-5dee-a9c5-0bbc18089ac5",)).fetchone()
        task = conn.execute("SELECT * FROM research_program_tasks WHERE id=%s FOR UPDATE",
            ("6c54b553-c382-5a90-96d9-20d765552343",)).fetchone()
        assert stage["program_id"] == program["id"] and stage["stage"] == "program_data"
        assert stage["actor"] == "data" and stage["state"] == "waiting" and stage["attempt"] == 3
        assert str(stage["task_id"]) == "83b60177-5b5a-5b36-a8d1-10bfa5b3a245"
        assert stage["context"]["_program_hold"] == {
            "reason": "repeated_data_output_contract_failure", "failure_count": 3}
        assert stage["context"]["_data_output_failures"] == 3
        assert "Scoped assessments cannot carry a legacy exploratory decision or policy fields" in stage["error"]
        assert task["program_id"] == program["id"] and task["state"] == "proposed"
        assert task["data_assessment"] is None and task["decision"] is None and task["mission_id"] is None
        assert conn.execute("SELECT count(*) AS n FROM research_program_reservations WHERE program_id=%s",
            (program["id"],)).fetchone()["n"] == 0
        evidence = conn.execute("SELECT attempt,task_id,response,error,completed_at "
            "FROM research_stage_attempts WHERE stage_id=%s ORDER BY attempt", (stage["id"],)).fetchall()
        assert len(evidence) == 3
        before = fingerprint(evidence)
        context = dict(stage["context"])
        context.pop("_program_hold")
        context["_data_output_failures"] = 0
        detail = {
            "schema_version": 1, "remediation_id": remediation,
            "source_commit": "86aea8cdf03b5543666813c97689acb1e8158a6d",
            "program_id": str(program["id"]), "program_digest": program["manifest_digest"],
            "stage_id": str(stage["id"]), "candidate_id": str(task["id"]),
            "previous_attempt": 3, "previous_format_failures": 3,
            "previous_hold": stage["context"]["_program_hold"],
            "previous_context_digest": fingerprint(stage["context"]),
            "previous_attempts_digest": before, "previous_error": stage["error"],
            "state": "technical_hold_reconciled_new_normal_attempt_allowed",
            "scientific_authority_changed": False, "readiness_applied_by_operator": False,
            "prior_attempts_and_reads_reused_as_new_evidence": False,
            "scientific_trials_added_by_operator": 0,
            "authority": "Existing owner deployment/continuation request; scoped schema repair root-reviewed in PR105"}
        conn.execute("UPDATE research_mission_stages SET context=%s,retry_at=now(),updated_at=now() WHERE id=%s",
            (Jsonb(context), stage["id"]))
        assert fingerprint(conn.execute("SELECT attempt,task_id,response,error,completed_at "
            "FROM research_stage_attempts WHERE stage_id=%s ORDER BY attempt", (stage["id"],)).fetchall()) == before
        event = conn.execute("INSERT INTO events(project_id,kind,detail) VALUES(%s,%s,%s) RETURNING id",
            (project["id"], "research_data_format_reconciled", Jsonb(detail))).fetchone()
        result = {**detail, "event_id": event["id"], "already_committed": False}
print(json.dumps(result, ensure_ascii=False))
'''
completed = subprocess.run(["docker", "exec", "-i", "quant-company-api-1", "python",
    "/app/entrypoint.py", "python", "-"],
    input=code, capture_output=True, text=True, timeout=90)
if completed.returncode:
    # Fixed error class/status only; the private stderr stays on this host.
    private = OUTPUT.with_suffix(".private-error.log")
    private.write_text(completed.stderr)
    private.chmod(0o600)
    raise RuntimeError("technical_reconciliation_failed_check_private_host_receipt")
result = json.loads(completed.stdout)
result["native_qualification_sha256"] = hashlib.sha256(native_path.read_bytes()).hexdigest()
if OUTPUT.exists():
    old = json.loads(OUTPUT.read_bytes())
    assert old["event_id"] == result["event_id"] and old["remediation_id"] == REMEDIATION
else:
    OUTPUT.write_text(json.dumps(result, sort_keys=True, indent=2, ensure_ascii=False) + "\n")
    OUTPUT.chmod(0o600)
print(json.dumps(result, ensure_ascii=False))

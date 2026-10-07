"""Reconcile the exact second packet rejected solely for 6171 characters of notes.

Default is a rolled-back production probe under the reviewed bounded-note patch.
Validate and commit the original cached response through the existing stage service.
No provider call, new scientific job, audit verdict or publication from reconciliation.
"""

import argparse
import hashlib
import json
import pathlib
import re
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument("--apply", action="store_true")
arguments = parser.parse_args()
receipt_path = pathlib.Path("/var/lib/quant-company/codex/jobs/8520ba60-39ff-52e3-8af4-bb3a02ffcad0.json")
receipt_bytes = receipt_path.read_bytes()
assert hashlib.sha256(receipt_bytes).hexdigest() == "fac5a0b9fd97acfac637054202a9290e22934b776b74c7681b8f6a2fefcc16b7"
receipt = json.loads(receipt_bytes)
assert receipt["state"] == "complete" and receipt["request_id"] == "8520ba60-39ff-52e3-8af4-bb3a02ffcad0"
payload = {
    "apply": arguments.apply, "response": receipt["result"],
    "input_digest": receipt["input_digest"],
    "runtime_receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
}

code = r'''
import hashlib
import json
import pathlib
from datetime import UTC, datetime

from psycopg.types.json import Jsonb
from quant_company.company import Company, fingerprint
from quant_company.config import Settings
from quant_company.contracts import ProviderRequest, ProviderResponse
from quant_company.providers.codex_runner import request_digest
from quant_company.research.audit_delivery import MAX_NOTES, packet_data
from quant_company.research.controller import commit_stage
from quant_company.task_control import held

assert MAX_NOTES == 8000

payload = json.loads(PAYLOAD_LITERAL)
operation = "first-mission-second-packet-note-reconciliation:5e5e2013:20261007"
company = Company(Settings())
response = ProviderResponse.model_validate(payload["response"])
identity = "8520ba60-39ff-52e3-8af4-bb3a02ffcad0"
assert response.request_id == identity and response.provider == "codex" and response.thread_id

class RollbackProbe(Exception):
    pass

def read_entry(entry):
    content = pathlib.Path(entry["path"]).read_bytes()
    assert hashlib.sha256(content).hexdigest() == entry["sha256"]
    return content

result = None
try:
    with company.db.transaction() as conn:
        project = company._project(conn, "9aac0de4-2b97-5195-a720-287d324234f3")
        assert project["revision"] == 5 and project["status"] == "active"
        program = conn.execute("SELECT * FROM research_programs WHERE id=%s FOR UPDATE",
            ("f7deaf96-e677-5afe-93d4-18ac387043bb",)).fetchone()
        assert program["state"] == "active" and program["revision"] == 5
        assert program["manifest_digest"] == "c3ba5268d71c87bd2c6226160bc720498137bd291396a054970a1f1e7ebcf2db"
        assert program["approval_event_id"].startswith("slack:")
        existing = conn.execute("SELECT id,detail FROM events WHERE project_id=%s AND kind=%s "
            "AND detail->>'operation_id'=%s ORDER BY id LIMIT 1",
            (project["id"], "research_audit_bounded_notes_reconciled", operation)).fetchone()
        if existing:
            result = {**existing["detail"], "event_id": existing["id"], "already_committed": True}
        else:
            mission = conn.execute("SELECT * FROM research_missions WHERE id=%s FOR UPDATE",
                ("2ce40574-6368-5cef-b706-b4e67441b3de",)).fetchone()
            assert mission["state"] == "active" and mission["revision"] == project["revision"]
            assert str(mission["program_id"]) == str(program["id"])
            stage = conn.execute("SELECT * FROM research_mission_stages WHERE id=%s FOR UPDATE",
                ("5e5e2013-373c-5868-9fc9-242985e085b3",)).fetchone()
            assert stage["mission_id"] == mission["id"] and stage["stage"] == "audit"
            assert stage["actor"] == "validator" and stage["attempt"] == 1 and stage["state"] == "waiting"
            assert stage["result"] is None and stage["retry_at"] is None
            assert stage["context"]["_audit_hold"] == {
                "reason": "audit_runtime_requires_reconciliation",
                "diagnostic": "proposal_rejected:audit_packet_review_invalid"}
            assert stage["context"].get("required_lineage_reads") == []
            assert str(stage["task_id"]) == "a88948c2-58fc-5aa6-897a-76a691dd51dc"
            task = conn.execute("SELECT * FROM tasks WHERE id=%s FOR UPDATE", (stage["task_id"],)).fetchone()
            assert task["kind"] == "research_stage" and task["agent"] == "validator"
            assert task["status"] == "blocked" and task["error"] == "stage_response_rejected"
            assert task["revision"] == project["revision"] and task["turn_count"] == 2 and task["priority"] == 0
            assert not held(conn, project, task)
            turn = conn.execute("SELECT * FROM turns WHERE id=%s FOR UPDATE", (identity,)).fetchone()
            assert turn["task_id"] == task["id"] and turn["sequence"] == 2 and turn["status"] == "blocked"
            assert turn["response"] is None and turn["error"] == "stage_response_rejected"
            assert turn["revision"] == project["revision"]
            request = ProviderRequest.model_validate(turn["request"])
            assert request.request_id == identity and request_digest(request) == payload["input_digest"]
            assert request.session.id == str(task["id"]) and request.session.previous_request_id == "f405eb0f-5168-5309-b875-8e15eed1bb53"
            prior = conn.execute("SELECT response,status FROM turns WHERE id=%s", (request.session.previous_request_id,)).fetchone()
            assert prior["status"] == "completed" and prior["response"]["thread_id"] == response.thread_id
            packet = conn.execute("SELECT * FROM research_audit_packets WHERE turn_id=%s FOR UPDATE", (identity,)).fetchone()
            assert packet["stage_id"] == stage["id"] and packet["attempt"] == 1
            assert packet["notes"] is None and packet["reviewed_at"] is None
            data = packet_data(request.prompt)
            assert data["phase"] == "review" and data["read_chunks"] == packet["chunks"]
            assert data["packet_digest"] == packet["digest"] == fingerprint(packet["chunks"])
            assert data["audit"] == stage["context"]["audit"]
            decision = response.decision
            assert decision.status == "complete" and len(decision.artifacts) == 1
            assert not (decision.say.strip() or decision.tools or decision.messages or decision.delegations
                or decision.memories or decision.follow_up)
            value = json.loads(decision.artifacts[0].content)
            assert set(value) == {"notes", "packet_digest"} and value["packet_digest"] == packet["digest"]
            assert isinstance(value["notes"], str) and len(value["notes"]) == len(value["notes"].strip()) == 6171 <= MAX_NOTES
            assert packet["digest"] == "15c62df6ed11ae8e159cb943dd9130423dd996cc5c95e1631ebf28fd1c632441"
            audit = stage["context"]["_audit"]
            assert audit["delivery_version"] == 2 and audit["binding"]["validator_request_id"] == "f405eb0f-5168-5309-b875-8e15eed1bb53"
            mappings = stage["context"]["_private_files"]
            standalone = json.loads(read_entry(mappings["mission/scientific-lineage.json"]))
            history_bytes = read_entry(mappings["audit/scope/history.json"])
            history = json.loads(history_bytes)
            assert audit["history"] == history and history["scientific_lineage"] == standalone
            manifest_entry = audit["required_reads"]["audit/scope/history.json"]
            assert manifest_entry["sha256"] == hashlib.sha256(history_bytes).hexdigest()
            assert manifest_entry["characters"] == len(history_bytes.decode())
            assert "scope/history.json" in stage["context"]["audit"]["scope"]
            assert stage["context"]["audit"]["requires_all_text_files_read"] is True
            assert standalone["scientific_lineage_id"] == "b6a31ea2-8da9-5f67-97dd-2e304cde30c3"
            assert standalone.get("origins")
            original_request_digest = fingerprint(turn["request"])
            original_packet_digest = fingerprint(packet["chunks"])
            protected = fingerprint({key: value for key, value in stage["context"].items()
                if key != "_audit_hold"})
            job_ledger = fingerprint(conn.execute("SELECT * FROM research_jobs WHERE mission_id=%s ORDER BY id",
                (mission["id"],)).fetchall())
            reservations = fingerprint(conn.execute("SELECT * FROM research_program_reservations WHERE program_id=%s ORDER BY job_id",
                (program["id"],)).fetchall())
            assert conn.execute("SELECT count(*) AS n FROM research_mission_publications WHERE trial_id=%s",
                ("de3597b0-e358-5a58-af5e-9f3c79df53e9",)).fetchone()["n"] == 0
            context = dict(stage["context"])
            context.pop("_audit_hold")
            conn.execute("UPDATE research_mission_stages SET state='running',context=%s,error=NULL,updated_at=now() WHERE id=%s",
                (Jsonb(context), stage["id"]))
            conn.execute("UPDATE tasks SET status='running',error=NULL WHERE id=%s", (task["id"],))
            conn.execute("UPDATE turns SET status='running',error=NULL,updated_at=now() WHERE id=%s", (identity,))
            conn.execute("UPDATE research_stage_attempts SET error=NULL WHERE stage_id=%s AND attempt=1", (stage["id"],))
            refreshed = conn.execute("SELECT * FROM turns WHERE id=%s", (identity,)).fetchone()
            committed = commit_stage(company, conn, project, task, refreshed, response)
            assert committed == {"state": "completed", "audit_packet_reviewed": True}
            after = conn.execute("SELECT * FROM research_mission_stages WHERE id=%s", (stage["id"],)).fetchone()
            completed_turn = conn.execute("SELECT * FROM turns WHERE id=%s", (identity,)).fetchone()
            following = conn.execute("SELECT id,status,request,sequence FROM turns WHERE task_id=%s AND sequence=3",
                (task["id"],)).fetchone()
            assert after["state"] == "running" and after["attempt"] == 1 and after["result"] is None
            assert completed_turn["status"] == "completed" and completed_turn["response"] == response.model_dump(mode="json")
            assert following["status"] == "queued" and following["request"] is None
            assert fingerprint(completed_turn["request"]) == original_request_digest
            assert fingerprint({key: value for key, value in after["context"].items()
                if key != "_audit_hold"}) == protected
            assert fingerprint(conn.execute("SELECT chunks FROM research_audit_packets WHERE turn_id=%s", (identity,)).fetchone()["chunks"]) == original_packet_digest
            assert fingerprint(conn.execute("SELECT * FROM research_jobs WHERE mission_id=%s ORDER BY id",
                (mission["id"],)).fetchall()) == job_ledger
            assert fingerprint(conn.execute("SELECT * FROM research_program_reservations WHERE program_id=%s ORDER BY job_id",
                (program["id"],)).fetchall()) == reservations
            detail = {
                "schema_version": 1, "operation_id": operation, "observed_at": datetime.now(UTC).isoformat(),
                "program_id": str(program["id"]), "program_digest": program["manifest_digest"],
                "mission_id": str(mission["id"]), "stage_id": str(stage["id"]), "task_id": str(task["id"]),
                "attempt": 1, "request_id": identity, "input_digest": payload["input_digest"],
                "runtime_receipt_sha256": payload["runtime_receipt_sha256"], "packet_digest": packet["digest"],
                "original_hold": stage["context"]["_audit_hold"], "original_context_digest": fingerprint(stage["context"]),
                "standalone_lineage_sha256": mappings["mission/scientific-lineage.json"]["sha256"],
                "frozen_history_sha256": manifest_entry["sha256"], "lineage_equal_to_frozen_history": True,
                "lineage_review_route": "All canonical lineage bytes retained in required audit/scope/history.json packets",
                "notes_characters": 6171, "previous_service_note_bound": 6000, "reviewed_service_note_bound": MAX_NOTES,
                "provider_thread_id": response.thread_id, "following_turn_id": str(following["id"]),
                "request_response_packet_scope_and_budget_preserved": True,
                "new_provider_calls": 0, "new_scientific_jobs": 0, "audit_verdict_applied": False,
                "manual_publication": False, "source_rollout": "reviewed_two_file_audit_patch",
                "registered_scientific_commit": company.settings.company_code_commit,
                "authority": "Existing owner deploy/continue requests; fixed repair reviewed in PR105 before effect",
                "state": "cached_second_packet_committed_normal_audit_continuation_queued",
            }
            if not payload["apply"]:
                result = {**detail, "state": "production_transaction_probe_passed_rolled_back", "applied": False}
                raise RollbackProbe
            event = conn.execute("INSERT INTO events(project_id,kind,detail) VALUES(%s,%s,%s) RETURNING id",
                (project["id"], "research_audit_bounded_notes_reconciled", Jsonb(detail))).fetchone()
            result = {**detail, "event_id": event["id"], "already_committed": False, "applied": True}
except RollbackProbe:
    pass
print(json.dumps(result, ensure_ascii=False, sort_keys=True))
'''.replace("PAYLOAD_LITERAL", repr(json.dumps(payload, ensure_ascii=False)))

completed = subprocess.run([
    "docker", "exec", "-i", "quant-company-api-1", "python", "/app/entrypoint.py", "python", "-",
], input=code, capture_output=True, text=True, timeout=60)
output_dir = pathlib.Path("/var/lib/quant-company/releases")
output = output_dir / ("second-audit-note-reconciled-20261007.json" if arguments.apply
    else "second-audit-note-dry-run-20261007.json")
if completed.returncode:
    private_error = output.with_suffix(".private-error.log")
    private_error.write_text(completed.stderr)
    private_error.chmod(0o600)
    print(json.dumps({"state": "probe_failed_transaction_rolled_back",
        "exception_types": re.findall(r"(?m)^(?:[A-Za-z_][A-Za-z0-9_]*\.)*([A-Za-z_][A-Za-z0-9_]*)(?:: |$)", completed.stderr),
        "operator_code_lines": re.findall(r'File "<stdin>", line ([0-9]+)', completed.stderr)}))
    raise RuntimeError("audit_note_reconciliation_failed_check_private_host_log")
assert receipt_path.read_bytes() == receipt_bytes
result = json.loads(completed.stdout)
output.write_text(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
output.chmod(0o600)
print(json.dumps(result, indent=2, ensure_ascii=False))

"""Read actual prompt contracts and successor stage receipts without result numbers.

This helper uses only a PostgreSQL read-only transaction and existing native
runtime receipts. Full prompts and provider responses are never printed.
"""

import argparse
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

QUERY = """
BEGIN READ ONLY;
SET LOCAL statement_timeout='10s';
WITH recent AS (
 SELECT s.* FROM research_mission_stages s
 WHERE s.mission_id='2ce40574-6368-5cef-b706-b4e67441b3de'
 ORDER BY s.created_at DESC,s.id DESC LIMIT 8
)
SELECT json_build_object(
 'paused',(SELECT paused_until IS NOT NULL FROM runtime_control WHERE id=1),
 'activity',json_build_object(
   'running_turns',(SELECT count(*) FROM turns WHERE status='running'),
   'sending_outbox',(SELECT count(*) FROM outbox WHERE status='sending'),
   'active_jobs',(SELECT count(*) FROM research_jobs WHERE state IN
     ('queued','claimed','running','cancel_requested','uncertain'))),
 'authority',(SELECT json_build_object('program_state',r.state,'program_digest',r.manifest_digest,
   'mission_state',m.state,'mission_digest',m.manifest_digest,'cycle',m.cycle,
   'cycle_trials',m.cycle_trials,'cumulative_trials',m.cumulative_trials)
   FROM research_programs r JOIN research_missions m ON m.program_id=r.id
   WHERE m.id='2ce40574-6368-5cef-b706-b4e67441b3de'),
 'stages',(SELECT coalesce(json_agg(x),'[]'::json) FROM (
   SELECT s.id,s.stage,s.actor,s.state,s.attempt,s.task_id,left(s.error,300) AS error,
     s.retry_at,s.created_at,s.updated_at,s.result->>'decision' AS decision,
     s.context->'mission'->'stage'->>'proposal_id' AS proposal_id,
     s.context->'review_evidence' AS review_evidence,
     s.context->'_technical_review_evidence' AS attached_review_evidence,
     s.context->'required_technical_reads' AS required_technical_reads,
     s.context ? '_repair_hold' AS independent_repair_held,
     t.priority AS task_priority,t.status AS task_status,
     (SELECT count(*) FROM research_stage_reads q WHERE q.stage_id=s.id AND q.attempt=s.attempt) AS read_chunks,
     (SELECT coalesce(json_agg(c.id::text ORDER BY c.created_at,c.id),'[]'::json)
       FROM research_mission_challenges c WHERE c.mission_id=s.mission_id
       AND c.proposal_id::text=s.context->'mission'->'stage'->>'proposal_id') AS current_challenge_ids
   FROM recent s LEFT JOIN tasks t ON t.id=s.task_id ORDER BY s.created_at DESC,s.id DESC)x),
 'requests',(SELECT coalesce(json_agg(x),'[]'::json) FROM (
   SELECT t.id,t.task_id,t.sequence,t.status,left(t.error,300) AS error,t.created_at,t.updated_at,
     s.id AS stage_id,s.stage,t.request
   FROM turns t JOIN recent s ON s.task_id=t.task_id
   ORDER BY t.created_at DESC,t.id DESC LIMIT 8)x),
 'attempts',(SELECT coalesce(json_agg(x),'[]'::json) FROM (
   SELECT a.stage_id,a.attempt,left(a.error,300) AS error,a.created_at,a.completed_at,a.response
   FROM research_stage_attempts a JOIN recent s ON s.id=a.stage_id
   ORDER BY a.created_at DESC LIMIT 8)x),
 'trials',(SELECT coalesce(json_agg(x),'[]'::json) FROM (
   SELECT id,proposal_id,state,job_id,to_jsonb(research_mission_trials)->'technical_closed_at' AS technical_closed_at,
     result_id,ordinal,feasible,created_at,updated_at FROM research_mission_trials
   WHERE mission_id='2ce40574-6368-5cef-b706-b4e67441b3de' ORDER BY created_at)x),
 'jobs',(SELECT coalesce(json_agg(x),'[]'::json) FROM (
   SELECT id,trial_id,state,company_commit,claimed_at,heartbeat_at,left(error,300) AS error,
     created_at,updated_at,artifact_sha256
   FROM research_jobs WHERE mission_id='2ce40574-6368-5cef-b706-b4e67441b3de'
   ORDER BY created_at)x));
COMMIT;
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    assert re.fullmatch(r"[0-9a-f]{40}", args.commit)
    applied_path = Path("/var/lib/quant-company/releases") / ("technical-review-" + args.commit + "-applied.json")
    raw = json.loads(subprocess.check_output([
        "docker", "exec", "quant-company-postgres-1", "psql", "-U", "postgres",
        "-d", "quant_company", "-qAt", "-v", "ON_ERROR_STOP=1", "-c", QUERY,
    ], text=True, timeout=30))
    applied = json.loads(applied_path.read_bytes()) if applied_path.is_file() else {}
    cutover = applied.get("completed_at")
    stages = {row["id"]: row for row in raw["stages"]}
    for row in raw["requests"]:
        request = row.pop("request") or {}
        prompt = request.get("prompt", "")
        row.update({key: request.get(key) for key in ("request_id", "model", "reasoning_effort")})
        row["turn_created_after_cutover"] = (
            datetime.fromisoformat(row["created_at"]) >= datetime.fromisoformat(cutover)
            if cutover else None
        )
        row["prompt_characters"] = len(prompt)
        context = json.loads(prompt.split("\nMISSION DATA JSON:\n", 1)[1]) if "\nMISSION DATA JSON:\n" in prompt else {}
        row["stage_capabilities_present"] = bool(context.get("stage_capabilities"))
        contract = context.get("selection_contract") or {}
        schema = context.get("output_schema") or {}
        row["technical_review_schema_present"] = set(schema.get("required", [])) == {"trial_id", "failed_job_id", "decision", "rationale", "source_ids"}
        row["review_evidence"] = context.get("review_evidence")
        row["required_technical_reads"] = context.get("required_technical_reads", [])
        row["repair_contract_present"] = set(schema.get("$defs", {})) >= {"RepairArtifact", "RepairHold"}
        row["failed_build"] = {key: context.get("failed_build", {}).get(key)
                               for key in ("job_id", "code_commit", "failure_source_ids")}
        row["selection_contract"] = contract
        responses = schema.get("properties", {}).get("responses", {})
        enum = schema.get("$defs", {}).get("ChallengeResponse", {}).get("properties", {}).get("challenge_id", {}).get("enum")
        expected = stages[row["stage_id"]]["current_challenge_ids"]
        row["selection_schema"] = {"minItems": responses.get("minItems"), "maxItems": responses.get("maxItems"), "enum": enum}
        row["selection_scope_matches_db"] = (
            contract.get("current_challenge_ids") == expected == enum
            and responses.get("minItems") == responses.get("maxItems") == len(expected)
            if row["stage"] == "selection" else None
        )
        identity = request.get("request_id")
        if identity and re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", identity):
            receipt = Path("/var/lib/quant-company/codex/jobs") / (identity + ".json")
            if receipt.is_file():
                runtime = json.loads(receipt.read_bytes())
                row["runtime"] = {key: runtime.get(key) for key in ("state", "started_at", "completed_at", "cli_version")}
                row["runtime"]["fault_code"] = runtime.get("fault", {}).get("code")
                started = runtime.get("started_at")
                row["runtime"]["started_after_cutover"] = (
                    datetime.fromtimestamp(started, UTC) >= datetime.fromisoformat(cutover)
                    if cutover and isinstance(started, (int, float)) else None
                )
    for row in raw["attempts"]:
        response = row.pop("response") or {}
        artifacts = response.get("decision", {}).get("artifacts", [])
        contents = []
        row["artifact_json_error_count"] = 0
        for artifact in artifacts:
            if isinstance(artifact.get("content"), str):
                try:
                    content = json.loads(artifact["content"], strict=False)
                except json.JSONDecodeError:
                    row["artifact_json_error_count"] += 1
                    continue
                if isinstance(content, dict):
                    contents.append(content)
        row["selection_decisions"] = [item.get("decision") for item in contents if item.get("decision") in {"execute", "revise"}]
        ids = [r.get("challenge_id") for item in contents for r in item.get("responses", [])]
        row["submitted_challenge_ids"] = ids
        row["duplicate_challenge_ids"] = len(ids) - len(set(ids))
        expected = stages[row["stage_id"]]["current_challenge_ids"]
        row["extra_challenge_ids"] = sorted(set(ids) - set(expected))
        row["missing_challenge_ids"] = sorted(set(expected) - set(ids))
    def read(query):
        return json.loads(subprocess.check_output([
            "docker", "exec", "quant-company-postgres-1", "psql", "-U", "postgres",
            "-d", "quant_company", "-qAt", "-v", "ON_ERROR_STOP=1", "-c",
            "BEGIN READ ONLY; SET LOCAL statement_timeout='10s'; " + query + "; COMMIT;",
        ], text=True, timeout=30))
    exists = read("SELECT json_build_object('present',to_regclass('research_technical_dispositions') IS NOT NULL)")
    raw["technical_dispositions"] = read("""SELECT coalesce(json_agg(x),'[]'::json) FROM (
        SELECT trial_id,mission_id,job_id,payload,digest,created_at FROM research_technical_dispositions
        WHERE mission_id='2ce40574-6368-5cef-b706-b4e67441b3de' ORDER BY created_at)x""") if exists["present"] else []
    raw["read_receipts"] = read("""SELECT coalesce(json_agg(x),'[]'::json) FROM (
        SELECT q.stage_id,q.attempt,q.path,q.sha256,q.character_offset,q.next_offset,q.created_at
        FROM research_stage_reads q JOIN research_mission_stages s ON s.id=q.stage_id
        WHERE s.mission_id='2ce40574-6368-5cef-b706-b4e67441b3de' AND s.created_at>='2026-10-12T02:00:00Z'
        ORDER BY q.created_at DESC LIMIT 40)x""")
    print(json.dumps({"observed_at": datetime.now(UTC).isoformat(), "database_mutated": False,
                      "cutover_state": applied.get("state"), "cutover_completed_at": cutover,
                      **raw}, indent=2))


if __name__ == "__main__":
    main()

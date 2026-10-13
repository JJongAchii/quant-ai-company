"""Bounded read-only progress observer; never calls a model or mutates company rows.

--once prints only execution/admission/publication metadata. Otherwise retains
45-second snapshots on the existing host for at most four hours. No Slack sends.
"""

import argparse
import hashlib
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--once", action="store_true")
args = parser.parse_args()
query = """BEGIN TRANSACTION READ ONLY;
SELECT json_build_object(
 'mission',(SELECT json_build_object('id',id,'state',state,'revision',revision,'cumulative_trials',cumulative_trials)
     FROM research_missions WHERE id='2ce40574-6368-5cef-b706-b4e67441b3de'),
 'trial',(SELECT json_build_object('id',id,'state',state,'job_id',job_id,'updated_at',updated_at)
     FROM research_mission_trials WHERE id='de3597b0-e358-5a58-af5e-9f3c79df53e9'),
 'stages',(SELECT coalesce(json_agg(row_to_json(x)),'[]'::json) FROM (
     SELECT s.id,s.stage,s.actor,s.state,s.attempt,s.error,s.retry_at,s.task_id,s.updated_at,
       s.context->'_audit_hold' AS audit_hold,
       (SELECT sum((value->>'characters')::int) FROM jsonb_each(coalesce(s.context#>'{_audit,required_reads}','{}'::jsonb))) AS required_text_characters,
       (SELECT count(*) FROM research_audit_packets p WHERE p.stage_id=s.id AND p.notes IS NOT NULL) AS reviewed_packets,
       (SELECT sum(length(c.value->>'content')) FROM research_audit_packets p,
          LATERAL jsonb_array_elements(p.chunks) c WHERE p.stage_id=s.id AND p.notes IS NOT NULL) AS delivered_text_characters,
       (SELECT json_build_object('status',k.status,'priority',k.priority,'turn_count',k.turn_count,'error',k.error)
          FROM tasks k WHERE k.id=s.task_id) AS task,
       (SELECT coalesce(json_agg(row_to_json(y)),'[]'::json) FROM (
          SELECT id,sequence,status,error,updated_at FROM turns WHERE task_id=s.task_id ORDER BY sequence DESC LIMIT 3)y) AS recent_turns
     FROM research_mission_stages s WHERE s.mission_id='2ce40574-6368-5cef-b706-b4e67441b3de'
       AND s.stage IN ('interpretation','audit','meaning') ORDER BY s.created_at)x),
 'meaning_recorded',(SELECT exists(SELECT 1 FROM research_meaning_reviews WHERE trial_id='de3597b0-e358-5a58-af5e-9f3c79df53e9')),
 'publication',(SELECT json_build_object('trial_id',trial_id,'digest',digest,'source_id',payload->>'source_id')
     FROM research_mission_publications WHERE trial_id='de3597b0-e358-5a58-af5e-9f3c79df53e9'));
ROLLBACK;"""


def snapshot():
    raw = subprocess.check_output(["docker", "exec", "quant-company-postgres-1", "psql", "-U", "postgres",
        "-d", "quant_company", "-qAt", "-v", "ON_ERROR_STOP=1", "-c", query], text=True, timeout=30)
    value = json.loads(raw)
    for stage in value["stages"]:
        for turn in stage["recent_turns"]:
            receipt = Path("/var/lib/quant-company/codex/jobs") / (turn["id"] + ".json")
            if receipt.is_file():
                content = receipt.read_bytes()
                native = json.loads(content)
                turn["runtime"] = {key: native.get(key) for key in
                    ("state", "started_at", "completed_at", "fault_code", "thread_id")}
                turn["runtime"]["thread_id"] = (native.get("result") or {}).get("thread_id")
                turn["runtime_receipt_sha256"] = hashlib.sha256(content).hexdigest()
    return {"observed_at": datetime.now(UTC).isoformat(), **value}


if args.once:
    print(json.dumps(snapshot(), indent=2, sort_keys=True))
else:
    os.umask(0o077)
    root = Path("/var/lib/quant-company/releases")
    target = root / "first-trial-monitor-20261007.jsonl"
    latest = root / "first-trial-monitor-20261007-latest.json"
    deadline = time.monotonic() + 14400
    while time.monotonic() < deadline:
        try:
            value = snapshot()
        except Exception as exc:
            value = {"observed_at": datetime.now(UTC).isoformat(), "observer_error_type": type(exc).__name__}
        with target.open("a") as output:
            output.write(json.dumps(value, sort_keys=True) + "\n")
        temporary = latest.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
        os.replace(temporary, latest)
        time.sleep(45)

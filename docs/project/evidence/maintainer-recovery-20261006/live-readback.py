import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

COMMIT = "fadc0e54be0c470068bb18011cc6a1a9ec8ee971"


def run(args):
    r = subprocess.run(args, capture_output=True, timeout=30)
    if r.returncode:
        raise RuntimeError("read-only live observation failed: " + args[0])
    return r.stdout.decode().strip()


def sql(q):
    return json.loads(
        run(
            [
                "docker",
                "exec",
                "-u",
                "postgres",
                "quant-company-postgres-1",
                "psql",
                "-XAt",
                "-v",
                "ON_ERROR_STOP=1",
                "-d",
                "quant_company",
                "-c",
                q,
            ]
        )
    )


def hashes(rows):
    return {
        r["id"]: hashlib.sha256(json.dumps(r, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        for r in rows
    }


cut = json.loads(
    (
        Path("/var/lib/quant-company/releases") / ("maintainer-recovery-" + COMMIT + "-cutover.json")
    ).read_text()
)
start = cut["started_at"]
jobs = sql(
    "SELECT COALESCE(jsonb_agg(to_jsonb(s)),'[]'::jsonb) FROM (SELECT id,kind,state,error,created_at,updated_at,receipt->'pr' AS pr FROM maintenance_jobs WHERE created_at>='"
    + start
    + "'::timestamptz ORDER BY created_at)s"
)
observations = sql(
    "SELECT COALESCE(jsonb_agg(to_jsonb(s)),'[]'::jsonb) FROM (SELECT key,job_id,body->>'kind' AS kind,body->'detail' AS detail,created_at FROM maintenance_observations WHERE body->>'kind' IN ('research_contract_failure','staff_independent_review_failure') ORDER BY created_at)s"
)
calls = sql(
    "SELECT COALESCE(jsonb_agg(to_jsonb(s)),'[]'::jsonb) FROM (SELECT id,job_id,error,created_at,due_at,length(request->>'prompt') AS prompt_characters,response IS NOT NULL AS has_response FROM maintenance_calls WHERE created_at>='"
    + start
    + "'::timestamptz ORDER BY created_at)s"
)
cases = sql(
    "SELECT COALESCE(jsonb_agg(to_jsonb(s)),'[]'::jsonb) FROM (SELECT id,request_id,source_project_id,source_revision,project_id,created_at,checked_at FROM maintenance_cases WHERE created_at>='"
    + start
    + "'::timestamptz)s"
)
reviews = hashes(
    sql(
        "SELECT COALESCE(jsonb_agg(to_jsonb(v)),'[]'::jsonb) FROM staff_independent_reviews v WHERE state='blocked' AND error='uncertain'"
    )
)
allcalls = hashes(sql("SELECT COALESCE(jsonb_agg(to_jsonb(c)),'[]'::jsonb) FROM maintenance_calls c"))
priority = sql("""SELECT COALESCE(jsonb_agg(to_jsonb(s)),'[]'::jsonb) FROM (
 SELECT t.id,t.status,t.due_at,k.agent,k.kind,k.status AS task_status,p.status AS project_status
 FROM turns t JOIN tasks k ON k.id=t.task_id JOIN projects p ON p.id=k.project_id
 WHERE t.status IN ('queued','waiting','running') AND t.due_at<=now() AND k.revision=p.revision
 ORDER BY t.due_at LIMIT 8)s""")
control = sql("SELECT to_jsonb(c) FROM maintenance_control c WHERE id=1")
container = json.loads(run(["docker", "inspect", "quant-company-maintenance-1"]))[0]
proof = json.loads(
    run(
        [
            "docker",
            "exec",
            "quant-company-maintenance-1",
            "python",
            "-c",
            "import os,inspect,json,hashlib;from quant_company.maintenance.problems import failure_observations; print(json.dumps({'commit':os.environ.get('COMPANY_CODE_COMMIT'),'problems_module_sha256':hashlib.sha256(inspect.getsource(failure_observations).encode()).hexdigest()}))",
        ]
    )
)
print(
    json.dumps(
        {
            "captured_at": datetime.now(UTC).isoformat(),
            "scope": "Real production PostgreSQL/container readback; no manual model calls or external writes",
            "container": {
                "id": container["Id"],
                "image_id": container["Image"],
                "running": container["State"]["Running"],
                "oom_killed": container["State"]["OOMKilled"],
                "restart_count": container["RestartCount"],
            },
            "installed_code": proof,
            "control": control,
            "new_jobs": jobs,
            "technical_observations": observations,
            "new_cases": cases,
            "new_calls": calls,
            "company_priority_turns": priority,
            "original_uncertain_reviews_unchanged": all(
                reviews.get(k) == v for k, v in cut["uncertain_review_row_sha256_before"].items()
            ),
            "original_maintenance_calls_unchanged": all(
                allcalls.get(k) == v for k, v in cut["existing_maintenance_call_sha256_before"].items()
            ),
        },
        indent=2,
    )
)

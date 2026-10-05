"""Read filesystem pressure, failed backup phases and current research tasks."""

import json
import pathlib
import re
import shutil
import subprocess
from collections import Counter
from datetime import UTC, datetime


def run(command):
    return subprocess.check_output(command, text=True, stderr=subprocess.STDOUT, timeout=120)


journal = run(["journalctl", "-u", "quant-company-backup.service", "--since", "2026-10-03", "--no-pager", "-o", "cat"])
frames = re.findall(r'File "([^"]+)", line (\d+), in ([A-Za-z0-9_]+)', journal)
copy_error_prefixes = Counter()
for line in journal.splitlines():
    if line.startswith("shutil.Error: "):
        # Journald can truncate a long shutil.Error list mid-string; extract only
        # source path prefixes, without evaluating it or printing the raw line.
        for source in re.findall(r"\('/var/lib/quant-company/([^']+)',", line):
            copy_error_prefixes["/".join(pathlib.Path(source).parts[:3])] += 1
disks = {path: dict(zip(["total", "used", "free"], shutil.disk_usage(path), strict=True))
         for path in ["/", "/var/lib/quant-company", "/var/lib/docker"]}
backups = [{"name": p.name, "bytes": p.stat().st_size}
           for p in sorted(pathlib.Path("/var/lib/quant-company/backups").glob("company-*")) if p.is_file()]
sql = """
SELECT json_build_object(
 'tasks',(SELECT json_agg(t) FROM (SELECT id,parent_id,agent,kind,status,error,turn_count,created_at,
   left(instruction,500) AS instruction,left(result,1000) AS result
   FROM tasks WHERE project_id='9aac0de4-2b97-5195-a720-287d324234f3' AND revision=5
   ORDER BY created_at DESC,id DESC LIMIT 10)t),
 'turns',(SELECT json_agg(t) FROM (SELECT t.id,t.task_id,t.status,t.attempts,t.error,t.due_at,t.updated_at
   FROM turns t JOIN tasks k ON k.id=t.task_id WHERE k.project_id='9aac0de4-2b97-5195-a720-287d324234f3'
   AND k.revision=5 ORDER BY t.created_at DESC,t.id DESC LIMIT 10)t),
 'review_correction',(SELECT json_agg(o) FROM (SELECT id,status,update_ts,sent_ts,attempts,error FROM outbox
   WHERE project_id='9aac0de4-2b97-5195-a720-287d324234f3' AND update_ts='1791241541.860959')o),
 'proposal_trace',(SELECT json_agg(t) FROM (SELECT t.id,t.sequence,t.status,t.response#>>'{decision,status}' AS decision_status,
   t.response#>'{decision,tools}' AS tools,left(t.response#>>'{decision,say}',600) AS say,
   jsonb_array_length(t.response#>'{decision,artifacts}') AS artifact_count
   FROM turns t WHERE t.task_id='658d0f0c-ff15-569d-ae1d-96fadb575972'
   ORDER BY t.sequence DESC LIMIT 5)t),
 'current_program_stages',(SELECT json_agg(s) FROM (SELECT id,stage,actor,state,attempt,error,retry_at,task_id,
   left(result::text,3000) AS result FROM research_mission_stages
   WHERE program_id='f7deaf96-e677-5afe-93d4-18ac387043bb' ORDER BY created_at DESC LIMIT 5)s),
 'current_stage_attempts',(SELECT json_agg(a) FROM (SELECT a.stage_id,a.attempt,a.task_id,a.error,a.completed_at,
   left(a.response::text,3500) AS response FROM research_stage_attempts a JOIN research_mission_stages s ON s.id=a.stage_id
   WHERE s.program_id='f7deaf96-e677-5afe-93d4-18ac387043bb' ORDER BY a.created_at DESC LIMIT 3)a))
"""
db = json.loads(run(["docker", "exec", "quant-company-postgres-1", "psql", "-U", "postgres", "-d", "quant_company", "-qAt", "-v", "ON_ERROR_STOP=1", "-c", sql]))
print(json.dumps({
    "observed_at": datetime.now(UTC).isoformat(), "disks": disks,
    "inodes": run(["df", "-Pi", "/", "/var/lib/docker"]),
    "docker_usage": [json.loads(line) for line in run(["docker", "system", "df", "--format", "{{json .}}"] ).splitlines()],
    "state_top_level_bytes": run(["du", "-x", "-B1", "--max-depth=1", "/var/lib/quant-company"]),
    "state_apparent_bytes": run(["du", "-x", "--apparent-size", "-B1", "--max-depth=1", "/var/lib/quant-company"]),
    "backups": backups,
    "backup_failure_trace": [{"file": pathlib.Path(f).name, "line": int(n), "function": fn} for f,n,fn in frames],
    "copy_error_prefixes": dict(copy_error_prefixes.most_common(12)),
    "error_counts": {phrase: journal.count(phrase) for phrase in ["No space left on device", "failed to start", "CalledProcessError", "ValueError", "Timed out"]},
    "failed_compose_operations": re.findall(r"'(stop|start|exec)'.*?returned non-zero exit status (\d+)", journal),
    "database": db,
}, ensure_ascii=False, indent=2))

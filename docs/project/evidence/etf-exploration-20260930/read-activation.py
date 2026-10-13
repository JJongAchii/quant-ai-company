"""Read public operating identities and the exact owner notice; never replay writes."""

import fcntl
import hashlib
import json
import pathlib
import re
import subprocess
from datetime import UTC, datetime


def run(args):
    return subprocess.check_output(args, text=True, timeout=120, stderr=subprocess.STDOUT)


state = pathlib.Path("/var/lib/quant-company")
names = run(
    ["docker", "ps", "-a", "--filter", "label=com.docker.compose.project=quant-company", "--format", "{{.Names}}"]
).splitlines()
rows = json.loads(run(["docker", "inspect", *names]))
containers = {
    r["Name"].lstrip("/"): {
        "id": r["Id"], "image": r["Config"]["Image"], "image_id": r["Image"],
        "state": r["State"]["Status"], "health": r["State"].get("Health", {}).get("Status"),
        "started_at": r["State"]["StartedAt"], "restart_count": r["RestartCount"],
        "oom_killed": r["State"]["OOMKilled"],
        "exit_code": r["State"]["ExitCode"], "finished_at": r["State"]["FinishedAt"],
        "restart_policy": r["HostConfig"]["RestartPolicy"]["Name"],
        "roles_file": dict(e.split("=", 1) for e in r["Config"]["Env"]).get("ROLES_FILE"),
        "briefing_enabled": dict(e.split("=", 1) for e in r["Config"]["Env"]).get("BRIEFING_ENABLED", "false"),
    }
    for r in rows
}
query = """
SELECT json_build_object(
 'project',(SELECT row_to_json(p) FROM (SELECT id,revision,status,owner_user,channel,thread_ts FROM projects WHERE id='9aac0de4-2b97-5195-a720-287d324234f3')p),
 'programs',(SELECT json_agg(p) FROM (SELECT id,state,revision,manifest_digest,approval_event_id,approved_at FROM research_programs WHERE project_id='9aac0de4-2b97-5195-a720-287d324234f3' ORDER BY created_at,id)p),
 'pause',(SELECT row_to_json(r) FROM runtime_control r WHERE id=1),
 'owner_review',coalesce((SELECT json_agg(b) FROM (SELECT b.id,b.message_id,b.target_id,b.manifest_digest,b.app_id,o.status,o.sent_ts,o.attempts,o.error FROM research_approval_bindings b JOIN outbox o ON o.id=b.message_id WHERE b.target_kind='program' AND b.target_id='a9854436-e65c-5b5d-a811-4bbccd74f542' ORDER BY b.id)b),'[]'::json),
 'usage',(SELECT row_to_json(u) FROM (SELECT count(*) AS reservations,coalesce(sum(CASE WHEN settled_at IS NULL THEN reserved_seconds ELSE actual_seconds END),0) AS compute_seconds,count(*) FILTER(WHERE scientific_trial OR settled_at IS NULL) AS trials FROM research_program_reservations WHERE program_id IN (SELECT id FROM research_programs WHERE project_id='9aac0de4-2b97-5195-a720-287d324234f3'))u),
 'program_mission_count',(SELECT count(*) FROM research_missions WHERE program_id IN (SELECT id FROM research_programs WHERE project_id='9aac0de4-2b97-5195-a720-287d324234f3')),
 'program_active_jobs',(SELECT count(*) FROM research_jobs WHERE state IN ('queued','claimed','running','uncertain','cancel_requested') AND mission_id IN (SELECT id FROM research_missions WHERE program_id IN (SELECT id FROM research_programs WHERE project_id='9aac0de4-2b97-5195-a720-287d324234f3'))),
 'worker',(SELECT row_to_json(w) FROM research_workers w WHERE id='worker'),
 'other_program_reviews',coalesce((SELECT json_agg(r) FROM (SELECT p.id,p.manifest_digest,p.state,p.spec,b.id AS binding_id,o.id AS message_id,o.status AS delivery_status,o.sent_ts FROM research_programs p LEFT JOIN research_approval_bindings b ON b.target_id=p.id AND b.target_kind='program' LEFT JOIN outbox o ON o.id=b.message_id WHERE p.project_id='9aac0de4-2b97-5195-a720-287d324234f3' AND p.revision=5 AND p.state IN ('draft','active') AND p.id<>'a9854436-e65c-5b5d-a811-4bbccd74f542' ORDER BY p.created_at,p.id)r),'[]'::json))
"""
database = json.loads(run([
    "docker", "exec", "quant-company-postgres-1", "psql", "-U", "postgres", "-d", "quant_company",
    "-qAt", "-v", "ON_ERROR_STOP=1", "-c", query,
]))
code = r"""
import json,os,sys,pathlib,httpx
result=json.loads(sys.argv[1]);project=result['project'];bindings=result['owner_review']
credential=json.loads(pathlib.Path(os.environ['SLACK_CREDENTIALS_FILE']).read_text())['director']
if len(bindings)==1 and bindings[0]['status']=='delivered':
 reply=httpx.get('https://slack.com/api/conversations.replies',headers={'Authorization':'Bearer '+credential['bot_token']},params={'channel':project['channel'],'ts':project['thread_ts'],'oldest':bindings[0]['sent_ts'],'latest':bindings[0]['sent_ts'],'inclusive':'true','limit':10},timeout=30).json()
 proof={'ok':reply.get('ok',False),'error':reply.get('error')}
 matches=[m for m in reply.get('messages',[]) if m.get('ts')==bindings[0]['sent_ts']]
 if len(matches)==1:
  message=matches[0]
  actions=[a for b in message.get('blocks',[]) if b.get('type')=='actions' for a in b.get('elements',[])]
  proof.update({'ts':message['ts'],'thread_ts':message.get('thread_ts'),'app_id':message.get('app_id'),'bot_user_matches':message.get('user')==credential['bot_user_id'],'owner_mentioned':('<@'+project['owner_user']+'>') in message.get('text',''),'full_digest_visible':bindings[0]['manifest_digest'] in message.get('text',''),'text_excerpt':message.get('text','')[:1000],'buttons':[{'action_id':a.get('action_id'),'binding_matches':a.get('value')==str(bindings[0]['id'])} for a in actions]})
 result['slack_readback']=proof
print(json.dumps(result,ensure_ascii=False))
"""
if len(database["owner_review"]) == 1 and database["owner_review"][0]["status"] == "delivered":
    database = json.loads(run([
        "docker", "exec", "quant-company-slack-socket-1", "python", "-c", code, json.dumps(database),
    ]))
log = run(["docker", "logs", "--since", "10m", "quant-company-slack-socket-1"])
connections = re.findall(r"Slack Socket Mode connected for (\d+) employees", log)
profiles = {
    n: hashlib.sha256((state / "config" / n).read_bytes()).hexdigest()
    for n in ["roles.json", "research-profiles.json", "research-qlab.json", "runtime.env"]
}
role_errors = {}
startup_errors = {}
for name in containers:
    if name == "quant-company-postgres-1":
        continue
    recent = run(["docker", "logs", "--since", "5m", "--tail", "100", name])
    errors = sorted(set(re.findall(r"Invalid permissions in role ([a-z_]+)", recent)))
    if errors:
        role_errors[name] = errors
    failures = re.findall(r"(?:ValueError|RuntimeError|PermissionError|ConnectionError|AssertionError): ([^\n]{1,300})", recent)
    if failures:
        startup_errors[name] = [re.sub(r"x(?:app|ox[bpars])-[A-Za-z0-9-]+|wss://\S+", "[REDACTED]", item) for item in failures[-3:]]
roles = json.loads((state / "config/roles.json").read_text())
role_facts = [{k: role.get(k) for k in ["id", "active", "model", "reasoning_effort", "tools", "can_delegate_to"]} for role in roles]
socket_probe = r"""
import json,pathlib,logging
from quant_company.company import load_roles
from quant_company.config import Settings
roles=load_roles(Settings());active=[r.id for r in roles.values() if r.active]
connections=0
for table in ['tcp','tcp6']:
 for line in pathlib.Path('/proc/net/'+table).read_text().splitlines()[1:]:
  fields=line.split()
  if fields[3]=='01' and fields[2].split(':')[-1]=='01BB': connections+=1
print(json.dumps({'expected_active_roles':active,'established_https_connections':connections,'logger_default_level':logging.getLogger('quant_company.socket').getEffectiveLevel()}))
"""
socket_facts = json.loads(run(["docker", "exec", "quant-company-slack-socket-1", "python", "-c", socket_probe])) if containers["quant-company-slack-socket-1"]["state"] == "running" else None
with (state / ".backup.lock").open("a") as lock:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        backup_lock_busy = True
    else:
        backup_lock_busy = False
        fcntl.flock(lock, fcntl.LOCK_UN)
recent_receipts = []
for receipt in sorted((state / "releases").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:12]:
    value = json.loads(receipt.read_text())
    selected = value.get("selected_services")
    recent_receipts.append({"name": receipt.name, **{k: value.get(k) for k in ["phase", "state", "commit", "company_commit", "completed_at"]},
                            "selected_services": list(selected) if isinstance(selected, dict) else selected})
backup_unit = run(["systemctl", "show", "quant-company-backup.service", "-p", "ActiveState", "-p", "Result", "-p", "ExecMainStatus", "-p", "ExecMainStartTimestamp", "-p", "ExecMainExitTimestamp"])
backup_journal = run(["journalctl", "-u", "quant-company-backup.service", "--since", "2026-10-03", "--no-pager", "-o", "cat"])
backup_errors = {name: phrase in backup_journal for name, phrase in {
    "maintenance_config_rejected": "Maintenance backup config is not the reviewed public configuration",
    "restart_command_failed": "'start'",
    "systemd_timeout": "start operation timed out",
    "unsafe_research_tree": "Research evidence tree missing or unsafe",
    "disk_full": "No space left on device",
    "permission_denied": "Permission denied",
}.items()}
print(json.dumps({
    "observed_at": datetime.now(UTC).isoformat(),
    "global_current": str(pathlib.Path("/opt/quant-company/current").resolve()),
    "containers": containers, "profiles": profiles, "database": database,
    "socket_connection_employee_counts": connections,
    "role_errors": role_errors, "role_facts": role_facts,
    "startup_errors": startup_errors,
    "socket_transport": socket_facts,
    "backup_lock_busy": backup_lock_busy, "recent_receipts": recent_receipts,
    "backup_unit": backup_unit, "backup_error_indicators": backup_errors,
}, ensure_ascii=False, indent=2))

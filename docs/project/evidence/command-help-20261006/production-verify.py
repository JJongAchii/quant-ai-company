"""Read-only live help, fleet, authorization and backup checks. No Slack sends."""

import datetime
import hashlib
import importlib.util
import json
import pathlib
import re
import subprocess

ROOT = pathlib.Path("/opt/quant-company/operator-releases/command-help-20261006")
STATE = pathlib.Path("/var/lib/quant-company")
PRIOR = pathlib.Path("/opt/quant-company/operator-releases/codex-upgrade-20261006/production-cutover.py")
if hashlib.sha256(PRIOR.read_bytes()).hexdigest() != "bc61547f1d79a3b6c21fa62c973fbbd5547aced9aaca005d4ec4cec7bb6985a3":
    raise RuntimeError("reviewed_helpers_changed")
spec = importlib.util.spec_from_file_location("reviewed_read_helpers", PRIOR)
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
run, sql = helpers.run, helpers.sql
stage = json.loads((STATE / "releases/command-help-20261006-stage.json").read_text())
cutover = json.loads((STATE / "releases/command-help-20261006-cutover.json").read_text())
plan = json.loads((ROOT / "build/plan.json").read_text())
before = json.loads((ROOT / "baseline.json").read_text())
if stage["state"] != "staged" or cutover["state"] != "active" or stage["commit"] != cutover["commit"]:
    raise RuntimeError("deployment_not_active")


def python(name, code):
    return json.loads(run(["docker", "exec", "quant-company-" + name + "-1", "/app/.venv/bin/python", "-c", code]))


fresh = helpers.inspect(before)
services = {}
for name, row in fresh.items():
    old = before[name]
    if not row["State"]["Running"] or row["State"]["OOMKilled"]:
        raise RuntimeError("service_not_stable:" + name)
    services[name] = {"id": row["Id"], "image": row["Image"], "running": True, "oom": False, "restarts": row["RestartCount"]}
    if name in stage["targets"]:
        if row["Image"] != stage["image"] or helpers.signature(row) != helpers.signature(old):
            raise RuntimeError("active_cohort_configuration_changed:" + name)
        code = """import hashlib,importlib.util,json,pathlib
from quant_company.accounts import help_text
from quant_company.command_help import HELP_TEXT
from quant_company.model_policy import HELP_TEXT as MODEL_HELP
assert all(help_text(v)==HELP_TEXT==MODEL_HELP for v in ['도움말','명령어','help',' HELP ','모델 도움말','모델 명령어'])
root=pathlib.Path(importlib.util.find_spec('quant_company').origin).parent
files={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
print(json.dumps({'root':str(root),'files':files,'guide_sha256':hashlib.sha256(HELP_TEXT.encode()).hexdigest()}))"""
        source = python(name, code)
        if source["root"] != plan["package_root"] or source["files"] != plan["after"]:
            raise RuntimeError("active_source_inventory_changed:" + name)
        services[name].update(source_files_verified=len(source["files"]), guide_aliases_verified=True,
                              guide_sha256=source["guide_sha256"], configuration_preserved=True)
    elif row["Image"] != old["Image"] or row["Id"] != old["Id"]:
        raise RuntimeError("non_target_container_replaced:" + name)

requests = helpers.requests()
if any(requests[table].get(k) != v for table, rows in cutover["frozen_requests"].items() for k, v in rows.items()):
    raise RuntimeError("frozen_requests_changed")
account = sql("SELECT row_to_json(s) FROM (SELECT profile,revision FROM model_account_policy WHERE id=1)s")
assignment = sql("SELECT row_to_json(s) FROM (SELECT revision,bindings FROM model_assignment_policy WHERE id=1)s")
if account != cutover["account_policy"] or assignment != cutover["assignment_policy"]:
    raise RuntimeError("model_or_account_policy_changed")
api = python("api", """import json,os,pathlib,urllib.request,urllib.error
token=os.getenv('OPERATOR_TOKEN') or pathlib.Path(os.environ['OPERATOR_TOKEN_FILE']).read_text().strip()
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def get(path,auth=True):
 headers={'Authorization':'Bearer '+token} if auth else {}
 with opener.open(urllib.request.Request('http://127.0.0.1:8000'+path,headers=headers),timeout=30) as response:
  return json.load(response)
try:
 get('/v1/model-assignments',False)
 unauthorized=False
except urllib.error.HTTPError as error:
 unauthorized=error.code==401
agents=get('/v1/agents')
print(json.dumps({'health':get('/healthz',False),'model_assignments':get('/v1/model-assignments'),
 'active_agents':[r['id'] for r in agents if r['active']],'unauthorized_rejected':unauthorized}))""")
if not api["health"]["ok"] or not api["model_assignments"]["enabled"] or not api["unauthorized_rejected"]:
    raise RuntimeError("operator_api_unconfirmed")
auth = python("api", """import json,os,pathlib,urllib.request
credentials=json.loads(pathlib.Path(os.environ['SLACK_CREDENTIALS_FILE']).read_text())
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
result={}
for role in ACTIVE:
 credential=credentials[role]
 request=urllib.request.Request('https://slack.com/api/auth.test',data=b'',
  headers={'Authorization':'Bearer '+credential['bot_token']},method='POST')
 with opener.open(request,timeout=15) as response:
  row=json.load(response)
 result[role]=bool(row.get('ok') and row.get('team_id')==os.environ['SLACK_TEAM_ID']
  and row.get('user_id')==credential['bot_user_id'])
print(json.dumps(result))""".replace("ACTIVE", repr(api["active_agents"])))
logs = subprocess.run(["docker", "logs", "--since", cutover["started_at"], "quant-company-slack-socket-1"],
                      text=True, capture_output=True, timeout=30)
logtext = logs.stdout + logs.stderr
connected = re.findall(r"Slack Socket Mode connected for (\d+) employees", logtext)
socket = fresh["slack-socket"]
pids = [line.split()[0] for line in run(["docker", "top", socket["Id"], "-eo", "pid,comm"]).splitlines()[1:]]
inodes = set()
for pid in pids:
    for fd in (pathlib.Path("/proc") / pid / "fd").iterdir():
        try:
            link = str(fd.readlink())
        except FileNotFoundError:
            continue
        if link.startswith("socket:["):
            inodes.add(link[8:-1])
tls_connections = 0
for leaf in ("tcp", "tcp6"):
    for line in (pathlib.Path("/proc") / str(socket["State"]["Pid"]) / "net" / leaf).read_text().splitlines()[1:]:
        parts = line.split()
        if parts[3] == "01" and parts[2].endswith(":01BB") and parts[9] in inodes:
            tls_connections += 1
if not auth or not all(auth.values()) or tls_connections < len(api["active_agents"]) or "could not stay connected" in logtext:
    raise RuntimeError("slack_connection_unconfirmed")
temporal = python("news-worker", """import asyncio,json,os,pathlib
from temporalio.client import Client
from temporalio.api.enums.v1 import TaskQueueType
from temporalio.api.taskqueue.v1 import TaskQueue
from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest
async def main():
 key=os.getenv('TEMPORAL_API_KEY') or pathlib.Path(os.environ['TEMPORAL_API_KEY_FILE']).read_text().strip()
 client=await Client.connect(os.environ['TEMPORAL_ADDRESS'],namespace=os.environ['TEMPORAL_NAMESPACE'],api_key=key,
  tls=os.getenv('TEMPORAL_TLS','false').lower()=='true')
 description=await client.get_workflow_handle('company-model-accounts-v1').describe()
 queue=await client.workflow_service.describe_task_queue(DescribeTaskQueueRequest(namespace=client.namespace,
  task_queue=TaskQueue(name=description.task_queue),task_queue_type=TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY))
 print(json.dumps({'status':description.status.name,'activity_pollers':len(queue.pollers)}))
asyncio.run(main())""")
if temporal["status"] != "RUNNING" or temporal["activity_pollers"] < 1:
    raise RuntimeError("account_workflow_unconfirmed")
backup = cutover["backup"]
if helpers.sha256(pathlib.Path(backup["backup"])) != backup["sha256"]:
    raise RuntimeError("backup_digest_mismatch")
bucket, key = backup["s3_uri"].removeprefix("s3://").split("/", 1)
cloud = json.loads(run(["aws", "s3api", "head-object", "--bucket", bucket, "--key", key]))
if cloud["ContentLength"] != pathlib.Path(backup["backup"]).stat().st_size or cloud.get("ServerSideEncryption") != "AES256":
    raise RuntimeError("cloud_backup_unconfirmed")
guard = pathlib.Path("/etc/systemd/system/quant-company-release.service.d/model-assignments.conf")
timer = run(["systemctl", "show", "quant-company-release.timer", "--property=ActiveState", "--value"]).strip()
if not guard.is_file() or timer != "active":
    raise RuntimeError("existing_release_guard_unconfirmed")
result = {"at": datetime.datetime.now(datetime.UTC).isoformat(), "state": "active", "commit": stage["commit"],
          "services": services, "source_changed_files": stage["changed_files"], "operator": api,
          "account_policy": account, "assignment_policy": assignment,
          "frozen_requests_preserved": sum(len(v) for v in cutover["frozen_requests"].values()),
          "slack": {"active_bot_authentication": auth, "startup_employee_count": int(connected[-1]) if connected else None,
                    "owned_established_tls_connections": tls_connections, "probe_messages_sent": 0,
                    "owner_help_delivery_verified": False, "scope": "real auth, sockets and deployed guide; no synthetic owner messages"},
          "temporal": temporal, "backup": {**backup, "cloud_bytes": cloud["ContentLength"], "encryption": cloud["ServerSideEncryption"]},
          "existing_release_guard_preserved": True, "actual_model_inference_calls_for_help": 0,
          "stage_receipt_sha256": hashlib.sha256((STATE / "releases/command-help-20261006-stage.json").read_bytes()).hexdigest(),
          "cutover_receipt_sha256": hashlib.sha256((STATE / "releases/command-help-20261006-cutover.json").read_bytes()).hexdigest()}
print(json.dumps(result, indent=2))

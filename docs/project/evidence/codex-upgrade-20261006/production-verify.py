"""Read-only, sanitized production verification; no model turns or Slack sends."""

import datetime
import hashlib
import json
import pathlib
import re
import subprocess

STATE = pathlib.Path("/var/lib/quant-company")
STAGE = STATE / "releases/codex-upgrade-20261006-stage.json"
CUTOVER = STATE / "releases/codex-upgrade-20261006-cutover.json"


def run(args, *, timeout=90):
    result = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("verification_failed:" + args[0])
    return result.stdout


def sql(query):
    return json.loads(run(["docker", "exec", "-u", "postgres", "quant-company-postgres-1", "psql", "-XAt",
                           "-v", "ON_ERROR_STOP=1", "-d", "quant_company", "-c", query]))


def python(service, code):
    return json.loads(run(["docker", "exec", "quant-company-" + service + "-1", "/app/.venv/bin/python", "-c", code]))


stage = json.loads(STAGE.read_text())
cutover = json.loads(CUTOVER.read_text())
if stage["state"] != "staged" or cutover["state"] != "active" or cutover["commit"] != stage["commit"]:
    raise RuntimeError("deployment_not_active")
baseline = json.loads((STATE / "releases/codex-upgrade-20261006-cutover-baseline.json").read_text())
names = [r["Name"] for r in baseline.values()]
fresh = json.loads(run(["docker", "inspect", *names]))
services = {}
for row in fresh:
    name = row["Name"].removeprefix("/quant-company-").removesuffix("-1")
    old = baseline[name]
    env = dict(item.split("=", 1) for item in row["Config"]["Env"])
    services[name] = {"id": row["Id"], "image": row["Image"], "running": row["State"]["Running"],
                      "oom": row["State"]["OOMKilled"], "restarts": row["RestartCount"],
                      "health": row["State"].get("Health", {}).get("Status"),
                      "memory_limit": row["HostConfig"]["Memory"]}
    if not services[name]["running"] or services[name]["oom"]:
        raise RuntimeError("service_not_stable:" + name)
    if name in cutover["target_names"]:
        image = stage["images"][old["Image"]]
        if row["Image"] != image["id"] or row["Config"]["Labels"].get("io.quant-company.codex-upgrade") != stage["commit"]:
            raise RuntimeError("active_image_changed:" + name)
        if "codex-runtime" not in name and env.get("MODEL_ASSIGNMENTS_ENABLED") != "true":
            raise RuntimeError("assignment_flag_missing:" + name)
        code = """import hashlib,importlib.util,json,pathlib
root=pathlib.Path(importlib.util.find_spec('quant_company').origin).parent
files={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
print(json.dumps({'root':str(root),'files':files}))"""
        actual = python(name, code)
        plan = next(p for p in json.loads(pathlib.Path("/opt/quant-company/operator-releases/codex-upgrade-20261006/plan.json").read_text())
                    if p["base_image"] == old["Image"])
        if actual["root"] != image["package_root"] or actual["files"] != plan["after"]:
            raise RuntimeError("active_source_changed:" + name)
        release = python(name, """import hashlib,json,subprocess,pathlib
from quant_company.providers.codex_release import RELEASE
binary=pathlib.Path('/usr/local/lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex')
with binary.open('rb') as f:
 digest=hashlib.file_digest(f,'sha256').hexdigest()
print(json.dumps({'actual_cli':subprocess.check_output(['codex','--version'],text=True).strip(),'expected_cli':RELEASE['version'],'binary_sha256':digest,'expected_binary_sha256':RELEASE['linux_x64']['binary_sha256']}))""")
        if release["actual_cli"] != "codex-cli " + release["expected_cli"] or release["binary_sha256"] != release["expected_binary_sha256"]:
            raise RuntimeError("live_cli_integrity_changed")
        services[name]["cli_release"] = release
        services[name].update(package_root=actual["root"], source_files_verified=len(actual["files"]),
                              feature_commit=stage["commit"], changed_files=len(image["changed"]))
    elif row["Id"] != old["Id"] or row["Image"] != old["Image"]:
        raise RuntimeError("unrelated_service_changed:" + name)

frozen_counts = {}
for table, saved in cutover["frozen_requests"].items():
    if not re.fullmatch(r"[a-z][a-z0-9_]*", table):
        raise RuntimeError("unrecognized_request_table")
    columns = sql("SELECT json_agg(column_name) FROM information_schema.columns "
                  f"WHERE table_schema='public' AND table_name='{table}' AND column_name IN ('request','provider_request')")
    column = columns[0]
    rows = sql(f"SELECT coalesce(json_agg(s),'[]') FROM (SELECT id::text,md5({column}::text) digest "
               f"FROM {table} WHERE {column} IS NOT NULL)s")
    now = {r["id"]: r["digest"] for r in rows}
    if any(now.get(identity) != digest for identity, digest in saved.items()):
        raise RuntimeError("frozen_request_changed:" + table)
    frozen_counts[table] = len(saved)

api = python("api", """import json,os,pathlib,urllib.request
token=os.getenv('OPERATOR_TOKEN') or pathlib.Path(os.environ['OPERATOR_TOKEN_FILE']).read_text().strip()
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def get(path,authorized=True):
 headers={'Authorization':'Bearer '+token} if authorized else {}
 with opener.open(urllib.request.Request('http://127.0.0.1:8000'+path,headers=headers),timeout=30) as response:
  return json.load(response)
status=get('/v1/model-assignments')
agents=get('/v1/agents')
try:
 get('/v1/model-assignments',False)
 unauthorized=False
except urllib.error.HTTPError as error:
 unauthorized=error.code==401
print(json.dumps({'status':status,'agents':[{'id':r['id'],'name':r['name'],'active':r['active'],'model':r['model'],'reasoning_effort':r.get('reasoning_effort')} for r in agents],
                  'unauthorized_rejected':unauthorized,'health':get('/healthz',False)}))""")
if not api["status"]["enabled"] or not api["health"]["ok"] or not api["unauthorized_rejected"]:
    raise RuntimeError("operator_api_not_qualified")

profile = sql("SELECT row_to_json(s) FROM (SELECT profile,revision FROM model_account_policy WHERE id=1)s")
catalog = python("news-worker", """import json,os,pathlib,urllib.request
token=os.getenv('MODEL_RUNTIME_TOKEN') or pathlib.Path(os.environ['MODEL_RUNTIME_TOKEN_FILE']).read_text().strip()
url=os.environ['MODEL_RUNTIME_URL'].rstrip('/')+'/v1/models/'+PROFILE
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open(urllib.request.Request(url,headers={'Authorization':'Bearer '+token}),timeout=60) as response:
 data=json.load(response)
try:
 opener.open(url,timeout=30)
 data['unauthorized_rejected']=False
except urllib.error.HTTPError as error:
 data['unauthorized_rejected']=error.code==401
print(json.dumps(data))""".replace("PROFILE", repr(profile["profile"])))
if not catalog["unauthorized_rejected"] or not any(r['model'] == 'gpt-6.1-sol' for r in catalog['models']):
    raise RuntimeError("private_catalog_auth_unconfirmed")

runtime_catalogs = {}
for name in ("codex-runtime", "quant-codex-runtime"):
    row = next(r for r in fresh if r["Name"] == "/quant-company-" + name + "-1")
    token_mount = next(m for m in row["Mounts"] if m["Destination"] == "/run/secrets/model_runtime_token")
    token = pathlib.Path(token_mount["Source"]).read_text().strip()
    address = next(n["IPAddress"] for n in row["NetworkSettings"]["Networks"].values() if n["IPAddress"])
    import urllib.error
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    url = "http://" + address + ":8080/v1/models/" + profile["profile"]
    with opener.open(urllib.request.Request(url, headers={"Authorization": "Bearer " + token}), timeout=60) as response:
        data = json.load(response)
    try:
        opener.open(url, timeout=30)
        rejected = False
    except urllib.error.HTTPError as error:
        rejected = error.code == 401
    if (not rejected or data["cli_version"] != "0.160.1" or not data["checked_at"]
            or not any(r["model"] == "gpt-6.1-sol" for r in data["models"])):
        raise RuntimeError("live_runtime_catalog_contract_failed:" + name)
    runtime_catalogs[name] = {**data, "unauthorized_rejected": rejected}

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
 print(json.dumps({'workflow_id':description.id,'status':description.status.name,'task_queue':description.task_queue,
                   'activity_pollers':len(queue.pollers)}))
asyncio.run(main())""")
if temporal["status"] != "RUNNING" or temporal["activity_pollers"] < 1:
    raise RuntimeError("account_control_workflow_not_running")

logs = subprocess.run(["docker", "logs", "--since", cutover["started_at"], "quant-company-slack-socket-1"],
                      capture_output=True, text=True, timeout=30)
logtext = logs.stdout + logs.stderr
connected = re.findall(r"Slack Socket Mode connected for (\d+) employees", logtext)
active = [r["id"] for r in api["agents"] if r["active"]]
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
print(json.dumps(result))""".replace("ACTIVE", repr(active)))
socket_row = next(r for r in fresh if r["Name"] == "/quant-company-slack-socket-1")
pids = [line.split()[0] for line in run(["docker", "top", socket_row["Id"], "-eo", "pid,comm"]).splitlines()[1:]]
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
    for line in (pathlib.Path("/proc") / str(socket_row["State"]["Pid"]) / "net" / leaf).read_text().splitlines()[1:]:
        parts = line.split()
        if parts[3] == "01" and parts[2].endswith(":01BB") and parts[9] in inodes:
            tls_connections += 1
slack = {"startup_log_employee_count": int(connected[-1]) if connected else None,
         "active_employees": active, "bot_authentication": auth, "owned_established_tls_connections": tls_connections,
         "verification_scope": "running Socket Mode process, owned TLS sockets and real Slack auth.test; no probe messages",
         "connection_failure_observed": "could not stay connected" in logtext,
         "actual_owner_model_command": "catalog delivered before upgrade; fresh owner input not synthesized", "probe_messages_sent": 0}
if not active or not all(auth.values()) or tls_connections < len(active) or slack["connection_failure_observed"]:
    raise RuntimeError("slack_socket_connection_unconfirmed")

bindings = sql("SELECT coalesce(json_agg(s),'[]') FROM (SELECT target,selection->>'source' source,"
               "selection->>'model' model,selection->>'reasoning_effort' reasoning_effort,count(*) "
               "FROM model_execution_bindings GROUP BY 1,2,3,4 ORDER BY 1,2,3,4)s")
commands = sql("SELECT coalesce(json_agg(s),'[]') FROM (SELECT state,count(*) FROM model_assignment_commands GROUP BY state)s")
guard = pathlib.Path("/etc/systemd/system/quant-company-release.service.d/model-assignments.conf")
timer = run(["systemctl", "show", "quant-company-release.timer", "--property=ActiveState", "--value"]).strip()
if not guard.is_file() or timer != "active":
    raise RuntimeError("release_guard_unconfirmed")

result = {"at": datetime.datetime.now(datetime.UTC).isoformat(), "state": "active", "commit": stage["commit"],
          "archive_sha256": stage["archive_sha256"], "services": services, "operator": api,
          "account_policy": profile, "runtime_http_catalog": catalog, "runtime_catalogs": runtime_catalogs, "temporal": temporal, "slack": slack,
          "frozen_request_counts": frozen_counts, "frozen_requests_preserved": True,
          "model_execution_bindings": bindings, "model_assignment_commands": commands,
          "backup": cutover["backup"], "automatic_code_cutover_guarded": True, "release_timer": timer,
          "deliberate_model_turns_started": 0, "disk": run(["df", "-h", "/var/lib/docker"]),
          "memory": run(["free", "-m"]),
          "stage_receipt_sha256": hashlib.sha256(STAGE.read_bytes()).hexdigest(),
          "cutover_receipt_sha256": hashlib.sha256(CUTOVER.read_bytes()).hexdigest()}
print(json.dumps(result, indent=2))

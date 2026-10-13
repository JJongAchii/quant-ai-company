"""Host-only, receipt-first Tech repair. Preserve the running research worker."""

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
from pathlib import Path

STATE = Path("/var/lib/quant-company")
OPERATION = STATE / "operations/tech-feed-recovery-20261008"
WORKER = "quant-company-worker-1"
DISPATCH = "quant-company-dispatch-1"
TECH = "quant-company-tech-feed-worker-1"
BASE_STORE = "47ae6bea3c795683ae5cc604f478ea07659f8ad33a2da65fc3e5c28ed83248f7"
OVERLAY = STATE / "config/tech-feed-recovery-20261008.compose.json"


def execute(command, source=None):
    result = subprocess.run(command, input=source, capture_output=True, timeout=300, check=False)
    if result.returncode:
        raise ValueError("operator_command_failed:" + command[0])
    return result.stdout.decode()


def save(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


def inspect(name):
    return json.loads(execute(["docker", "inspect", name]))[0]


def inventory():
    names = execute(["docker", "ps", "-a", "--filter", "label=com.docker.compose.project=quant-company",
                     "--format", "{{.Names}}"] ).splitlines()
    return {name: {"id": row["Id"], "image_id": row["Image"], "running": row["State"]["Running"],
                   "restarts": row["RestartCount"], "oom": row["State"]["OOMKilled"]}
            for name in names for row in [inspect(name)]}


def container(name, code):
    return json.loads(execute(["docker", "exec", "-i", name, "python", "/app/entrypoint.py", "python", "-"],
                              code.encode()))


def compose(row):
    command = ["docker", "compose", "--project-name", "quant-company", "--env-file",
               str(STATE / "config/runtime.env")]
    for path in row["Config"]["Labels"]["com.docker.compose.project.config_files"].split(","):
        if not Path(path).is_file():
            raise ValueError("recorded_compose_missing")
        command += ["-f", path]
    return command


def deploy(source, commit):
    journal = OPERATION / "deployment.json"
    if journal.exists() or OVERLAY.exists():
        raise ValueError("existing_operation_requires_reconciliation")
    OPERATION.mkdir(parents=True, exist_ok=True)
    before = inventory()
    worker, dispatch = inspect(WORKER), inspect(DISPATCH)
    if not worker["State"]["Running"] or not dispatch["State"]["Running"] or TECH in before:
        raise ValueError("unexpected_service_state")
    legacy = container(WORKER, """import json
from quant_company.config import Settings
from quant_company.company import Company
from quant_company.tech_feed.store import TechFeedStore
s=Settings(); t=TechFeedStore(Company(s))
print(json.dumps({'channels':s.slack_allowed_channels,'users':s.slack_allowed_users,'policy':t.policy()}))
""")
    images = {}
    for service, row in (("tech-feed-worker", dispatch), ("dispatch", dispatch)):
        base_tag = "quant-company-tech-recovery-base:" + row["Image"].removeprefix("sha256:")[:12]
        execute(["docker", "tag", row["Image"], base_tag])
        tag = "quant-company-tech-recovery:" + commit[:12] + "-" + service
        execute(["docker", "build", "--network", "none", "--pull=false", "-f",
                 str(source / "deploy/Dockerfile.tech-feed-update"), "--build-arg", "BASE_IMAGE=" + base_tag,
                 "--build-arg", "BASE_STORE_SHA256=" + BASE_STORE, "--build-arg", "RELEASE_COMMIT=" + commit,
                 "-t", tag, str(source)])
        images[service] = json.loads(execute(["docker", "image", "inspect", tag]))[0]["Id"]
    definition = json.loads(execute([*compose(dispatch), "config", "--format", "json"]))["services"]["dispatch"]
    environment = dict(value.split("=", 1) for value in dispatch["Config"]["Env"])
    required_files = {"DATABASE_PASSWORD_FILE", "TEMPORAL_API_KEY_FILE", "ROLES_FILE", "TECH_FEED_SOURCES_FILE"}
    collector_env = {key: value for key, value in environment.items()
                     if not key.endswith("_FILE") or key in required_files}
    needed = [collector_env[key] for key in required_files if key in collector_env]
    volumes = [volume for volume in definition.get("volumes", [])
               if any(path == volume["target"] or path.startswith(volume["target"] + "/") for path in needed)]
    collector = {key: definition[key] for key in ("entrypoint", "restart", "init", "user", "read_only", "tmpfs",
                 "cap_drop", "security_opt", "pids_limit", "logging", "stop_grace_period") if key in definition}
    collector.update(image=images["tech-feed-worker"], command=["python", "-m", "quant_company.tech_feed.worker"],
                     environment=collector_env, volumes=volumes,
                     secrets=[{"source": "database_password"}, {"source": "temporal_api_key"}],
                     networks={"core": {}, "service_egress": {}}, mem_limit="256m", cpus=0.25)
    save(OVERLAY, {"services": {"dispatch": {"image": images["dispatch"], "environment": environment},
                               "tech-feed-worker": collector}})
    record = {"state": "prepared", "source_commit": commit, "inventory_before": before,
              "legacy": legacy, "images": images,
              "patch_sha256": {name: hashlib.sha256((source / "src/quant_company/tech_feed" / name).read_bytes())
                               .hexdigest() for name in ("store.py", "recovery.py", "worker.py")},
              "shared_worker_preserved": True}
    save(journal, record)
    execute([*compose(dispatch), "-f", str(OVERLAY), "up", "-d", "--no-deps", "--no-build", "--pull", "never",
             "tech-feed-worker"])
    record["state"] = "collector_started"
    save(journal, record)
    print(json.dumps(record, ensure_ascii=False))


def handoff():
    journal = OPERATION / "handoff.json"
    if journal.exists():
        raise ValueError("existing_handoff_requires_reconciliation")
    record = json.loads((OPERATION / "deployment.json").read_text())
    if record["state"] != "collector_started" or not inspect(TECH)["State"]["Running"]:
        raise ValueError("new_collector_not_running")
    previous = container(TECH, """import asyncio,json
from temporalio.client import Client
from quant_company.config import Settings
async def run():
 s=Settings(); c=await Client.connect(s.temporal_address,namespace=s.temporal_namespace,
   api_key=s.temporal_api_key.get_secret_value() or None,tls=s.temporal_tls)
 d=await c.get_workflow_handle('company-tech-feed-collection-v1').describe()
 print(json.dumps({'workflow_id':d.id,'run_id':d.run_id,'status':d.status.name,
                  'pending_activities':len(d.raw_description.pending_activities),'task_queue':d.task_queue}))
asyncio.run(run())
""")
    if previous["status"] != "RUNNING" or previous["pending_activities"]:
        raise ValueError("collection_must_be_at_timer_boundary")
    save(journal, {"state": "dispatch_recreate_requested", "previous": previous})
    dispatch = inspect(DISPATCH)
    if dispatch["Id"] != record["inventory_before"][DISPATCH]["id"]:
        raise ValueError("dispatch_changed_since_review")
    sending = container(DISPATCH, """import json
from quant_company.company import Company
from quant_company.config import Settings
with Company(Settings()).db.transaction() as c:
 c.execute('SET TRANSACTION READ ONLY')
 print(json.dumps(c.execute("SELECT count(*) AS n FROM outbox WHERE status='sending'").fetchone()))
""")
    if sending["n"]:
        raise ValueError("sending_outbox_must_drain")
    execute([*compose(dispatch), "-f", str(OVERLAY), "up", "-d", "--no-deps", "--no-build", "--pull", "never", "dispatch"])
    save(journal, {"state": "handoff_requested", "previous": previous})
    result = container(TECH, """import asyncio,json
from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from quant_company.config import Settings
from quant_company.tech_feed.workflow import TechFeedCollectionWorkflow
async def run():
 s=Settings(); c=await Client.connect(s.temporal_address,namespace=s.temporal_namespace,
   api_key=s.temporal_api_key.get_secret_value() or None,tls=s.temporal_tls)
 old=c.get_workflow_handle('company-tech-feed-collection-v1'); d=await old.describe()
 if d.status.name!='RUNNING' or d.raw_description.pending_activities:
  raise ValueError('collection_must_be_at_timer_boundary')
 await c.get_workflow_handle(d.id,run_id=d.run_id).terminate('Tech-only dedicated queue recovery 2026-10-08')
 new=await c.start_workflow(TechFeedCollectionWorkflow.run,id=d.id,
   task_queue=s.temporal_task_queue+'-tech-feed-dedicated',
   id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY)
 after=await new.describe()
 print(json.dumps({'state':'handed_off','workflow_id':d.id,'old_run_id':d.run_id,
                  'new_run_id':after.run_id,'task_queue':after.task_queue,'status':after.status.name}))
asyncio.run(run())
""")
    result["previous"] = previous
    save(journal, result)
    runtime = STATE / "config/runtime.env"
    text = runtime.read_text()
    if not any(line.startswith("TECH_FEED_DEDICATED_WORKER=") for line in text.splitlines()):
        runtime.write_text(text.rstrip() + "\nTECH_FEED_DEDICATED_WORKER=true\n")
    after = inventory()
    for name, before in record["inventory_before"].items():
        if name != DISPATCH and after.get(name) != before:
            raise ValueError("independent_service_changed:" + name)
    record.update(state="deployed", inventory_after=after, independent_services_preserved=True)
    save(OPERATION / "deployment.json", record)
    print(json.dumps(result, ensure_ascii=False))


def reconcile(action, digest):
    path = OPERATION / (action + ".json")
    if path.exists():
        raise ValueError("existing_reconciliation_requires_review")
    legacy = json.loads((OPERATION / "deployment.json").read_text())["legacy"]
    plan = json.loads((OPERATION / "plan.json").read_text()) if action == "apply" else None
    if action == "apply" and (not digest or digest != plan["plan_digest"]):
        raise ValueError("reviewed_digest_required")
    if action == "apply":
        save(path, {"state": "apply_requested", "plan_digest": digest})
    code = """import json
from quant_company.config import Settings
from quant_company.company import Company
from quant_company.tech_feed.store import TechFeedStore
from quant_company.tech_feed.recovery import candidates,reconcile
s=TechFeedStore(Company(Settings()))
"""
    code += "legacy=" + repr(legacy) + "\n"
    code += "ids=" + repr([row["id"] for row in plan["publications"]]) + "\n" if plan else \
        "ids=candidates(s,legacy['policy'])\n"
    code += "print(json.dumps(reconcile(s,ids,legacy['channels'],legacy['users'],legacy['policy'],apply_digest=" \
        + repr(digest if action == "apply" else None) + "),ensure_ascii=False))\n"
    result = container(TECH, code)
    save(path, result)
    print(json.dumps(result, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("deploy", "handoff", "plan", "apply"))
    parser.add_argument("--source", type=Path)
    parser.add_argument("--commit")
    parser.add_argument("--apply-digest")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise ValueError("root_required")
    os.umask(0o077)
    with (STATE / ".backup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == "deploy":
            deploy(args.source, args.commit)
        elif args.action == "handoff":
            handoff()
        else:
            reconcile(args.action, args.apply_digest)


if __name__ == "__main__":
    main()

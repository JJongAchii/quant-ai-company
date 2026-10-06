"""Root operator readback. Outputs metadata and digests, never credentials or prompts."""

import datetime
import hashlib
import json
import pathlib
import subprocess
import sys


def run(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=40)
    if result.returncode:
        raise RuntimeError("readback_command_failed:" + args[0])
    return result.stdout


def sql(query):
    return json.loads(run(["docker", "exec", "-u", "postgres", "quant-company-postgres-1",
                           "psql", "-XAt", "-v", "ON_ERROR_STOP=1", "-d", "quant_company", "-c", query]))


names = [n for n in run(["docker", "ps", "-a", "--format", "{{.Names}}"])
         .splitlines() if n.startswith("quant-company-")]
rows = json.loads(run(["docker", "inspect", *names]))
envpath = pathlib.Path("/var/lib/quant-company/config/runtime.env")
services = {}
for row in rows:
    name = row["Name"].removeprefix("/quant-company-").removesuffix("-1")
    env = dict(item.split("=", 1) for item in row["Config"].get("Env", []))
    services[name] = {
        "id": row["Id"], "image": row["Image"], "tag": row["Config"]["Image"],
        "revision": row["Config"]["Labels"].get("org.opencontainers.image.revision"),
        "compose_files": row["Config"]["Labels"].get("com.docker.compose.project.config_files"),
        "running": row["State"]["Running"], "oom": row["State"]["OOMKilled"],
        "health": row["State"].get("Health", {}).get("Status"), "restarts": row["RestartCount"],
        "memory_limit": row["HostConfig"]["Memory"],
        "model_assignments_overlay": row["Config"]["Labels"].get("io.quant-company.model-assignments"),
        "config": {k: env[k] for k in ("COMPANY_CODE_COMMIT", "MODEL_ACCOUNTS_ENABLED",
                   "MODEL_ASSIGNMENTS_ENABLED", "MODEL_ACCOUNTS_OWNER_USER", "MODEL_ACCOUNTS_CHANNEL_ID",
                   "SLACK_ALLOWED_USERS", "SLACK_ALLOWED_CHANNELS", "MODEL_RUNTIME_URL") if k in env},
    }

tables = sql("SELECT coalesce(json_agg(tablename),'[]') FROM pg_tables WHERE schemaname='public'")
database = {}
for table, field in (("turns", "status"), ("tasks", "status"), ("outbox", "status"),
                     ("model_account_calls", "state"), ("maintenance_jobs", "state"),
                     ("quant_feed_calls", "state"), ("news_reviews", "state"),
                     ("staff_calls", "state"), ("model_assignment_commands", "state")):
    columns = sql("SELECT json_agg(column_name) FROM information_schema.columns "
                  f"WHERE table_schema='public' AND table_name='{table}'") if table in tables else []
    if field in (columns or []):
        database[table] = sql(f"SELECT coalesce(json_agg(t),'[]') FROM "
                              f"(SELECT {field},count(*) FROM {table} GROUP BY {field})t")
database["account_policy"] = sql("SELECT row_to_json(s) FROM "
                                 "(SELECT profile,revision FROM model_account_policy WHERE id=1)s")
database["frozen_requests"] = sql("SELECT coalesce(json_agg(t),'[]') FROM "
                                  "(SELECT id::text,md5(request::text) digest FROM turns "
                                  "WHERE request IS NOT NULL ORDER BY id)t")
if "model_assignment_policy" in tables:
    database["assignment_policy"] = sql("SELECT row_to_json(s) FROM "
                                        "(SELECT revision,bindings FROM model_assignment_policy WHERE id=1)s")
result = {"at": datetime.datetime.now(datetime.UTC).isoformat(),
          "current": str(pathlib.Path("/opt/quant-company/current").resolve()),
          "runtime_env_sha256": hashlib.sha256(envpath.read_bytes()).hexdigest(),
          "services": services, "database": database,
          "timer": run(["systemctl", "show", "quant-company-release.timer", "--property=ActiveState", "--value"]).strip(),
          "release_service": run(["systemctl", "show", "quant-company-release.service", "--property=ActiveState", "--value"]).strip(),
          "disk": run(["df", "-h", "/var/lib/docker"]), "memory": run(["free", "-m"])}
print(json.dumps(result, indent=2))

if len(sys.argv) > 1 and sys.argv[1] == "snapshot-source":
    root = pathlib.Path("/tmp/model-assignments-source-20261006")
    root.mkdir(mode=0o700, exist_ok=False)
    active_roots = {}
    for image in {row["Image"] for row in rows if row["Name"] not in
                  ("/quant-company-postgres-1", "/quant-company-claude-runtime-1")}:
        row = next(r for r in rows if r["Image"] == image)
        destination = root / image.split(":")[1]
        destination.mkdir()
        package_root = run(["docker", "exec", row["Id"], "/app/.venv/bin/python", "-c",
                            "import importlib.util,pathlib;print(pathlib.Path(importlib.util.find_spec('quant_company').origin).parent)"]).strip()
        run(["docker", "cp", row["Id"] + ":" + package_root,
             str(destination / "quant_company")])
        active_roots[image] = {"roots": [package_root],
                               "services": [r["Name"].removeprefix("/quant-company-").removesuffix("-1")
                                            for r in rows if r["Image"] == image],
                               "files": {str(p.relative_to(destination / "quant_company")): hashlib.sha256(p.read_bytes()).hexdigest()
                                         for p in (destination / "quant_company").rglob("*")
                                         if p.is_file() and "__pycache__" not in p.parts}}
    (root / "baseline.json").write_text(json.dumps(result, indent=2))
    (root / "active-roots.json").write_text(json.dumps(active_roots, indent=2))
    run(["tar", "czf", str(root) + ".tar.gz", "-C", str(root), "."])

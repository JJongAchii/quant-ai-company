"""Private, source-mounted qualification using existing images; no production replacement.

Run as root on the operational host after transferring a verified Git archive.
The separate runtime has only the existing model auth/jobs/token mounts. It shares
the durable Quant lane lock, and never receives database or Slack credentials.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

STATE = Path("/var/lib/quant-company")
OPERATION = STATE / "operations/quant-feed-structural-20260929"
CURRENT = Path("/opt/quant-company/current")
NEGATIVE = "eab01733dab66494bd5391d6b47154a3c80633108993eb4c0bf85210e59a6003"
RUNTIME = "quant-structural-preview-runtime"
CLIENT = "quant-structural-preview-client"


def run(argv, timeout=60):
    result = subprocess.run(argv, capture_output=True, timeout=timeout)
    if result.returncode:
        # Never expose docker inspect environment, command args or model content.
        raise RuntimeError(f"{argv[0]} failed with status {result.returncode}")
    return result.stdout.decode().strip()


def inspect(name):
    return json.loads(run(["docker", "inspect", name]))[0]


def inventory():
    names = run(["docker", "ps", "-aq"]).split()
    return {row["Name"]: {"id": row["Id"], "image": row["Image"], "running": row["State"]["Running"],
                          "oom": row["State"]["OOMKilled"], "restarts": row["RestartCount"]}
            for row in json.loads(run(["docker", "inspect", *names]))
            if row["Name"].startswith("/quant-company-")}


def activity():
    sql = """SELECT json_build_object(
      'running', (SELECT count(*) FROM quant_feed_calls WHERE state='running'),
      'pending', (SELECT count(*) FROM quant_feed_publications p JOIN outbox o ON o.id=p.id
                  WHERE o.status IN ('pending','sending')),
      'paused', EXISTS(SELECT 1 FROM runtime_control WHERE paused_until>now()),
      'publications', (SELECT count(*) FROM quant_feed_publications))::text"""
    return json.loads(run(["docker", "exec", "-u", "postgres", "quant-company-postgres-1", "psql", "-XAt",
                           "-v", "ON_ERROR_STOP=1", "-d", "quant_company", "-c", sql]))


def env(row):
    return dict(value.split("=", 1) for value in row["Config"]["Env"])


def create(name, row, source, environment, mounts, networks, command, memory):
    env_file = OPERATION / (name + ".env")
    with env_file.open("w") as stream:
        os.chmod(env_file, 0o600)
        stream.write("\n".join(key + "=" + value for key, value in environment.items()) + "\n")
    argv = ["docker", "create", "--name", name, "--init", "--user", "10001:10001", "--read-only",
            "--cap-drop=ALL", "--security-opt=no-new-privileges:true", "--pids-limit=128", "--cpus=1",
            "--memory=" + memory, "--tmpfs", "/tmp:size=64m,mode=1777", "--network", networks[0],
            "--env-file", str(env_file), "-e", "PYTHONPATH=/qualification/source/src",
            "--mount", f"type=bind,src={source},dst=/qualification/source,readonly"]
    for mount in mounts:
        if mount["Type"] != "bind":
            raise ValueError("preview_requires_explicit_bind_mounts")
        item = f"type=bind,src={mount['Source']},dst={mount['Destination']}"
        argv.extend(["--mount", item + ("" if mount["RW"] else ",readonly")])
    run([*argv, row["Image"], *command])
    for network in networks[1:]:
        run(["docker", "network", "connect", network, name])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--positive", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--live", action="store_true", required=True)
    args = parser.parse_args()
    if (os.geteuid() != 0 or CURRENT.resolve().name != args.base
            or not re.fullmatch(r"[a-z0-9-]{1,50}", args.label)
            or not re.fullmatch(r"[a-f0-9]{64}", args.positive)
            or not args.source.resolve().is_relative_to(OPERATION)):
        raise ValueError("preview_requires_validated_source_and_current_base")
    source = args.source.resolve()
    if not (source / "scripts/qualify_quant_editorial.py").is_file():
        raise ValueError("preview_source_missing")
    before = inventory()
    source_digest = hashlib.sha256(json.dumps({str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
                                             for p in sorted((source / "src").rglob("*"))
                                             if p.is_file() and "__pycache__" not in p.parts}, sort_keys=True).encode()).hexdigest()
    runtime = inspect("quant-company-codex-runtime-1")
    client = inspect("quant-company-quant-feed-worker-1")
    runtime_env, client_env = env(runtime), env(client)
    if client_env.get("QUANT_FEED_PUBLISH_ENABLED") != "false":
        raise ValueError("publication_not_paused")
    pending = activity()
    if any(pending[key] for key in ("running", "pending", "paused")):
        raise ValueError("quant_activity_not_drained")
    receipts = OPERATION / "preview"
    receipts.mkdir(mode=0o700, exist_ok=True)
    os.chown(receipts, 10001, 10001)
    output = receipts / (args.label + ".json")
    if output.exists():
        raise ValueError("existing_receipt_requires_reconciliation")
    allowed = {"/state/auth", "/state/backup-auth", "/state/jobs", "/run/secrets/model_runtime_token"}
    runtime_mounts = runtime["Mounts"]
    if {row["Destination"] for row in runtime_mounts} != allowed:
        raise ValueError("unexpected_runtime_mounts")
    allowed_client = {"/etc/quant-company/roles.json", "/run/secrets/database_password",
                      "/run/secrets/model_runtime_token", "/run/secrets/temporal_api_key"}
    if not {row["Destination"] for row in client["Mounts"]} <= allowed_client:
        raise ValueError("unexpected_client_mounts")
    if any(runtime_env.get(key) or client_env.get(key) for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_ACCESS_TOKEN")):
        raise ValueError("api_credentials_forbidden")
    runtime_networks = list(runtime["NetworkSettings"]["Networks"])
    client_networks = list(client["NetworkSettings"]["Networks"])
    # Remove monitoring healthcheck dependencies: these are disposable private containers.
    create(RUNTIME, runtime, source, runtime_env, runtime_mounts, runtime_networks,
           ["uvicorn", "quant_company.providers.codex_runtime:app", "--host", "0.0.0.0", "--port", "8080"], "1g")
    run(["docker", "start", RUNTIME])
    for _ in range(30):
        try:
            run(["docker", "exec", RUNTIME, "python", "-c",
                 "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=2)"])
            break
        except RuntimeError:
            time.sleep(1)
    else:
        raise ValueError("preview_runtime_unhealthy")
    client_env["MODEL_RUNTIME_URL"] = f"http://{RUNTIME}:8080"
    client_mounts = [*client["Mounts"], {"Type": "bind", "Source": str(receipts),
                                      "Destination": "/qualification/receipts", "RW": True}]
    create(CLIENT, client, source, client_env, client_mounts, client_networks,
           ["python", "/qualification/source/scripts/qualify_quant_editorial.py", "--negative", NEGATIVE,
            "--positive", args.positive, "--output", "/qualification/receipts/" + output.name, "--live"], "384m")
    run(["docker", "start", CLIENT])
    print(json.dumps({"state": "running", "label": args.label, "publication_enabled": False,
                      "new_image_builds": 0, "shared_quant_lane_lock": True}), flush=True)
    # A timeout leaves both containers and their durable receipts for reconciliation.
    code = int(run(["docker", "wait", CLIENT], timeout=3600))
    receipt = json.loads(output.read_text()) if output.exists() else {"state": "missing"}
    after = inventory()
    summary = {"label": args.label, "source": source.name, "source_digest": source_digest,
               "runtime_base_image": runtime["Image"], "client_base_image": client["Image"],
               "receipt_state": receipt["state"], "returncode": code,
               "case_results": receipt.get("case_results"), "call_count": len(receipt.get("calls", [])),
               "services_preserved": before == after, "current_release_preserved": CURRENT.resolve().name == args.base,
               "publication_count_preserved": activity()["publications"] == pending["publications"],
               "publication_enabled": env(inspect("quant-company-quant-feed-worker-1")).get("QUANT_FEED_PUBLISH_ENABLED") != "false"}
    (receipts / (args.label + "-operation.json")).write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)
    # Retain a potentially active model process after lost transport; never kill/reissue it.
    if receipt["state"] == "blocked" and receipt.get("fault") in {"uncertain", "timeout"}:
        raise SystemExit(2)
    run(["docker", "stop", "--time", "30", RUNTIME])
    run(["docker", "rm", CLIENT, RUNTIME])
    if code or not all(summary[key] for key in ("services_preserved", "current_release_preserved", "publication_count_preserved")):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

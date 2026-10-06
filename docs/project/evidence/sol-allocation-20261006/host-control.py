"""Run this approved operator action apart from production service memory limits.

No model inference, Slack ingress or Slack credentials. Only public configuration
and receipts leave the host. Mounted operator/database secrets stay in the service
boundary, using the deployed immutable API image and official entrypoint.
"""

import hashlib
import importlib.util
import json
import pathlib
import subprocess
import sys
import urllib.request
from datetime import UTC, datetime

ROOT = pathlib.Path("/opt/quant-company/operator-releases/sol-allocation-20261006")
HELPER = pathlib.Path("/opt/quant-company/operator-releases/codex-upgrade-20261006/production-cutover.py")
HELPER_SHA256 = "bc61547f1d79a3b6c21fa62c973fbbd5547aced9aaca005d4ec4cec7bb6985a3"


def save(name, value):
    path = ROOT / name
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.chmod(0o600)
    temporary.replace(path)


def helpers():
    if hashlib.sha256(HELPER.read_bytes()).hexdigest() != HELPER_SHA256:
        raise ValueError("Reviewed read-only helper changed")
    spec = importlib.util.spec_from_file_location("read_only_helpers", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def catalog(h):
    account = h.sql("SELECT row_to_json(s) FROM (SELECT profile,revision FROM model_account_policy WHERE id=1)s")
    row = h.inspect(["codex-runtime"])["codex-runtime"]
    token_path = next(m["Source"] for m in row["Mounts"] if m["Destination"] == "/run/secrets/model_runtime_token")
    token = pathlib.Path(token_path).read_text().strip()
    ip = next(network["IPAddress"] for network in row["NetworkSettings"]["Networks"].values() if network["IPAddress"])
    request = urllib.request.Request(f"http://{ip}:8080/v1/models/{account['profile']}",
                                     headers={"Authorization": "Bearer " + token})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=180) as response:
        data = json.load(response)
    result = {"account": account, "cli_version": data["cli_version"],
              "checked_at": data["checked_at"], "models": data["models"]}
    save("catalog.json", result)
    return result


def invoke(h, mode):
    row = h.inspect(["api"])["api"]
    payload = {"plan": json.loads((ROOT / "plan.json").read_text()),
               "catalog": json.loads((ROOT / "catalog.json").read_text())}
    if mode == "apply":
        preview = json.loads((ROOT / "preview.json").read_text())
        if preview["state"] != "preview" or preview["before_revision"] != payload["plan"]["expected_revision"]:
            raise ValueError("Validated preview required before apply")
    ip = row["NetworkSettings"]["Networks"]["quant-company_core"]["IPAddress"]
    args = ["docker", "run", "--rm", "-i", "--name=sol-allocation-20261006-operator", "--read-only",
            "--network=quant-company_core", "--memory=256m", "--cpus=.5", "--pids-limit=64", "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true", "--tmpfs=/tmp:size=32m,mode=1777"]
    exclude = {"SLACK_CREDENTIALS_FILE", "RESEARCH_WORKER_TOKEN_FILE", "MODEL_RUNTIME_TOKEN_FILE", "TEMPORAL_API_KEY_FILE"}
    for entry in row["Config"]["Env"]:
        if entry.split("=", 1)[0] not in exclude:
            args.extend(["--env", entry])
    args.extend(["--env", "SOL_ALLOCATION_API_URL=http://" + ip + ":8000"])
    for mount in row["Mounts"]:
        target = mount["Destination"]
        if target.startswith("/etc/quant-company/") or target in {"/run/secrets/database_password", "/run/secrets/operator_token"}:
            if mount["Type"] != "bind":
                raise ValueError("Unexpected configuration mount type")
            args.extend(["--mount", f"type=bind,source={mount['Source']},target={target},readonly"])
    args.extend(["--entrypoint=/app/.venv/bin/python", row["Image"], "/app/entrypoint.py", "/app/.venv/bin/python",
                 "-c", (ROOT / "operator-allocation.py").read_text(), mode])
    result = subprocess.run(args, input=json.dumps(payload), text=True, capture_output=True, timeout=120)
    if result.returncode:
        error = ROOT / (mode + "-isolated-error.log")
        error.write_text(result.stderr)
        error.chmod(0o600)
        raise RuntimeError(f"isolated_operator_{mode}_failed:{result.returncode}; error retained privately")
    proof = json.loads(result.stdout)
    save(mode + ".json", proof)
    return proof


def api_readback(h):
    row = h.inspect(["api"])["api"]
    token_path = next(m["Source"] for m in row["Mounts"] if m["Destination"] == "/run/secrets/operator_token")
    token = pathlib.Path(token_path).read_text().strip()
    ip = row["NetworkSettings"]["Networks"]["quant-company_core"]["IPAddress"]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    result = {"at": datetime.now(UTC).isoformat()}
    for path in ("/healthz", "/v1/model-assignments", "/v1/agents"):
        request = urllib.request.Request(f"http://{ip}:8000{path}", headers={"Authorization": "Bearer " + token})
        with opener.open(request, timeout=30) as response:
            result[path] = json.load(response)
    save("api-readback.json", result)
    return result


if __name__ == "__main__":
    mode = sys.argv[1]
    h = helpers()
    if mode == "catalog":
        result = catalog(h)
    elif mode == "readback":
        result = api_readback(h)
    elif mode in {"preview", "apply", "receipt"}:
        result = invoke(h, mode)
    else:
        raise ValueError("Unknown host operator mode")
    print(json.dumps(result, ensure_ascii=False, indent=2))

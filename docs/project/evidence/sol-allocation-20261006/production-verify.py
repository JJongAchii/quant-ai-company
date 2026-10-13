"""Read back policy and deployed producer selection without inference or Slack.

Producer checks use ephemeral containers of the actual immutable images and live
configuration. They do not exec additional application processes inside workers.
"""

import hashlib
import importlib.util
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

HOST = "/opt/quant-company/operator-releases/sol-allocation-20261006/host-control.py"
EXTERNAL_RELEASE = Path("/var/lib/quant-company/releases/analyst-preview-0ea670b7b0637d91a46714b73ef8df96d979cc6f.json")
PRODUCERS = {"worker": ["director", "financial_strategist", "researcher_kr", "researcher_global",
                        "researcher_crypto", "data", "engineer", "validator", "risk", "operations", "market_brief"],
             "news-worker": ["reporter", "news_screening", "news_search"],
             "quant-feed-worker": ["quant_scout"], "maintenance": ["maintainer"]}


def producer_probe(h, name, row, targets):
    code = '''import json, inspect
from quant_company.company import Company
from quant_company.config import Settings
from quant_company.model_policy import selection
company = Company(Settings())
with company.db.transaction() as conn:
    conn.execute("SET TRANSACTION READ ONLY")
    current = conn.execute("SELECT * FROM model_assignment_policy WHERE id=1").fetchone()
    chosen = {target: selection(company, conn, target, current=current) for target in TARGETS}
print(json.dumps({"source": inspect.getfile(Company), "enabled": company.settings.model_assignments_enabled,
                  "effective_assignments": chosen}))
'''.replace("TARGETS", repr(targets))
    args = ["docker", "run", "--rm", "-i", "--name=sol-allocation-verify-" + name, "--read-only",
            "--network=quant-company_core", "--memory=256m", "--cpus=.5", "--pids-limit=64", "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true", "--tmpfs=/tmp:size=32m,mode=1777"]
    excluded = {"SLACK_CREDENTIALS_FILE", "RESEARCH_WORKER_TOKEN_FILE", "MODEL_RUNTIME_TOKEN_FILE",
                "TEMPORAL_API_KEY_FILE", "OPERATOR_TOKEN_FILE"}
    for entry in row["Config"]["Env"]:
        if entry.split("=", 1)[0] not in excluded:
            args.extend(["--env", entry])
    for mount in row["Mounts"]:
        target = mount["Destination"]
        if target.startswith("/etc/quant-company/") or target == "/run/secrets/database_password":
            if mount["Type"] != "bind":
                raise ValueError("Unexpected configuration mount type")
            args.extend(["--mount", f"type=bind,source={mount['Source']},target={target},readonly"])
    args.extend(["--entrypoint=/app/.venv/bin/python", row["Image"], "/app/entrypoint.py", "/app/.venv/bin/python", "-c", code])
    result = subprocess.run(args, text=True, capture_output=True, timeout=90)
    if result.returncode:
        path = h.ROOT / ("verify-" + name + "-error.log")
        path.write_text(result.stderr)
        path.chmod(0o600)
        raise RuntimeError("Producer read-only check failed: " + name)
    return {"image": row["Image"], "production_container_id": row["Id"], "inference_calls": 0,
            "scope": "read-only selection in deployed immutable image with live configuration", **json.loads(result.stdout)}


def verify():
    spec = importlib.util.spec_from_file_location("allocation_host", HOST)
    h = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(h)
    helpers = h.helpers()
    before = json.loads((h.ROOT / "before.json").read_text())
    plan = json.loads((h.ROOT / "plan.json").read_text())
    applied = json.loads((h.ROOT / "apply.json").read_text())
    receipt = json.loads((h.ROOT / "receipt.json").read_text())
    if not receipt["cached"] or receipt["revision"] != applied["revision"] or receipt["plan_sha256"] != applied["plan_sha256"]:
        raise ValueError("Durable operation receipt did not match")
    api = h.api_readback(helpers)
    status = api["/v1/model-assignments"]
    if not api["/healthz"]["ok"] or status["revision"] != 1 or not status["enabled"]:
        raise ValueError("Live API or policy not ready")
    for target, wanted in plan["assignments"].items():
        found = status["assignments"][target]
        if any(found[key] != wanted[key] for key in wanted) or found["source"] != "pinned" or found["revision"] != 1:
            raise ValueError("Actual policy differed: " + target)
    active = sorted(agent["id"] for agent in api["/v1/agents"] if agent["active"])
    if active != applied["active_roles"]:
        raise ValueError("Employee activation changed")
    account = helpers.sql("SELECT row_to_json(s) FROM (SELECT profile,revision FROM model_account_policy WHERE id=1)s")
    if account != before["account"] or account != plan["expected_account"]:
        raise ValueError("Selected account changed")
    rows = helpers.inspect(sorted(before["containers"]))
    # A separately receipted Analyst preview replaced three services during this
    # readback window. Accept only those exact recorded IDs; keep every model and
    # frozen-request invariant and qualify the currently deployed producer code.
    external_bytes = EXTERNAL_RELEASE.read_bytes()
    external = json.loads(external_bytes)
    if external["phase"] != "preview_active" or external["commit"] != "0ea670b7b0637d91a46714b73ef8df96d979cc6f":
        raise ValueError("Concurrent release state changed; reconcile before readback")
    replacements = {}
    for name, original in before["containers"].items():
        row = rows[name]
        digest = hashlib.sha256(json.dumps(helpers.signature(row), sort_keys=True).encode()).hexdigest()
        if not row["State"]["Running"] or row["State"]["OOMKilled"]:
            raise ValueError("Production container configuration or health changed: " + name)
        if row["Id"] != original["id"] or row["Image"] != original["image"] or digest != original["configuration_sha256"]:
            approved = external["selected_services"].get(name)
            if (name not in {"dispatch", "news-worker", "briefing-data-worker"} or not approved
                    or approved["id"] != row["Id"] or approved["image"] != row["Config"]["Image"]):
                raise ValueError("Unreceipted production service replacement: " + name)
            replacements[name] = {"before": original, "after": {"id": row["Id"], "image": row["Image"],
                                                                    "configuration_sha256": digest}}
    requests = helpers.requests()
    preserved = 0
    for table, originals in before["frozen_requests"].items():
        for key, digest in originals.items():
            if requests.get(table, {}).get(key) != digest:
                raise ValueError("An existing frozen request changed")
            preserved += 1
    producers = {}
    for name, targets in PRODUCERS.items():
        proof = producer_probe(h, name, rows[name], targets)
        for target, chosen in proof["effective_assignments"].items():
            if not proof["enabled"] or chosen != status["assignments"][target]:
                raise ValueError("Producer selection differed: " + name + ":" + target)
        producers[name] = proof
        print(json.dumps({"verified_producer": name, "targets": targets}), flush=True)
    ledger = helpers.sql("""SELECT row_to_json(s) FROM (SELECT
        (SELECT count(*) FROM model_assignment_revisions WHERE revision=1) AS revision_rows,
        (SELECT command_id IS NULL FROM model_assignment_revisions WHERE revision=1) AS operator_revision,
        (SELECT count(*) FROM events WHERE kind='operator_model_assignment'
            AND detail->>'operation_id'='sol-allocation-20261006-owner-chat-v1') AS operation_events,
        (SELECT count(*) FROM model_assignment_commands WHERE state='requested') AS pending_commands)s""")
    if ledger["revision_rows"] != 1 or not ledger["operator_revision"] or ledger["operation_events"] != 1:
        raise ValueError("Revision or operation receipt was duplicated")
    result = {"state": "verified", "at": datetime.now(UTC).isoformat(), "operation_id": plan["operation_id"],
              "account": account, "policy_revision": status["revision"], "effective_assignments": status["assignments"],
              "agents": [{k: agent[k] for k in ("id", "name", "active", "model", "reasoning_effort")}
                         for agent in api["/v1/agents"]], "active_roles": active, "health": api["/healthz"],
              "containers": {n: {"id": r["Id"], "image": r["Image"], "running": r["State"]["Running"],
                                  "restart_count": r["RestartCount"]} for n, r in rows.items()},
              "container_ids_images_configuration_preserved": not replacements,
              "unchanged_service_count": len(rows) - len(replacements),
              "external_deployment": {"receipt": str(EXTERNAL_RELEASE), "receipt_sha256_at_readback": hashlib.sha256(external_bytes).hexdigest(),
                                      "commit": external["commit"], "phase": external["phase"],
                                      "activated_at": external["activated_at"], "replacements": replacements},
              "frozen_requests_preserved": preserved,
              "frozen_request_manifest_sha256": hashlib.sha256(json.dumps(before["frozen_requests"], sort_keys=True).encode()).hexdigest(),
              "receipt_readback_cached": receipt["cached"], "ledger": ledger, "producers": producers,
              "slack_messages_sent": 0, "model_inference_calls": 0,
              "preview_failure": json.loads((h.ROOT / "preview-failure.json").read_text())}
    h.save("verify.json", result)
    return result


if __name__ == "__main__":
    result = verify()
    print(json.dumps({key: result[key] for key in ("state", "at", "policy_revision", "active_roles", "ledger",
                                                 "frozen_requests_preserved", "receipt_readback_cached")}, indent=2))

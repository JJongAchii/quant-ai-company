"""Read back the owner-corrected Astra/Sol allocation without inference or Slack."""

import hashlib
import importlib.util
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path("/opt/quant-company/operator-releases/mixed-allocation-20261006")
PROBE_SOURCE = Path("/opt/quant-company/operator-releases/sol-allocation-20261006/production-verify.py")
PROBE_SHA256 = "1ed2100ebfcc2e19750891ee7e2063142e51e6df9d520a70b885a8bc311d9823"


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def verify():
    h = module("mixed_host", ROOT / "host-control.py")
    if hashlib.sha256(PROBE_SOURCE.read_bytes()).hexdigest() != PROBE_SHA256:
        raise ValueError("Reviewed read-only producer probe changed")
    probes = module("reviewed_producer_probes", PROBE_SOURCE)
    helpers = h.helpers()
    before = json.loads((ROOT / "before.json").read_text())
    plan = json.loads((ROOT / "plan.json").read_text())
    applied = json.loads((ROOT / "apply.json").read_text())
    receipt = json.loads((ROOT / "receipt.json").read_text())
    revision = plan["expected_revision"] + 1
    if not receipt["cached"] or receipt["revision"] != revision or receipt["plan_sha256"] != applied["plan_sha256"]:
        raise ValueError("Durable receipt did not match the correction")
    api = h.api_readback(helpers)
    status = api["/v1/model-assignments"]
    if not api["/healthz"]["ok"] or not status["enabled"] or status["revision"] != revision:
        raise ValueError("Live API or assignment revision did not match")
    for target, wanted in plan["assignments"].items():
        found = status["assignments"][target]
        model = "gpt-6-astra" if plan["original_models"][target] == "gpt-6-astra" else "gpt-6.1-sol"
        if (any(found[key] != wanted[key] for key in wanted) or found["model"] != model
                or found["source"] != "pinned" or found["revision"] != revision):
            raise ValueError("Corrected allocation differed: " + target)
    active = sorted(agent["id"] for agent in api["/v1/agents"] if agent["active"])
    if active != before["active_roles"] or active != applied["active_roles"]:
        raise ValueError("Employee activation changed")
    account = helpers.sql("SELECT row_to_json(s) FROM (SELECT profile,revision FROM model_account_policy WHERE id=1)s")
    if account != before["account"] or account != plan["expected_account"]:
        raise ValueError("Selected account changed")
    rows = helpers.inspect(sorted(before["containers"]))
    for name, original in before["containers"].items():
        row = rows[name]
        digest = hashlib.sha256(json.dumps(helpers.signature(row), sort_keys=True).encode()).hexdigest()
        if (row["Id"] != original["id"] or row["Image"] != original["image"] or digest != original["configuration_sha256"]
                or not row["State"]["Running"] or row["State"]["OOMKilled"] or row["RestartCount"] != original["restart_count"]):
            raise ValueError("Production service changed during correction: " + name)
    requests = helpers.requests()
    preserved = 0
    for table, originals in before["frozen_requests"].items():
        for key, digest in originals.items():
            if requests.get(table, {}).get(key) != digest:
                raise ValueError("An existing frozen request changed")
            preserved += 1
    producers = {}
    for name, targets in probes.PRODUCERS.items():
        proof = probes.producer_probe(h, name, rows[name], targets)
        if not proof["enabled"] or any(chosen != status["assignments"][target]
                                       for target, chosen in proof["effective_assignments"].items()):
            raise ValueError("Producer selection differed: " + name)
        producers[name] = proof
        print(json.dumps({"verified_producer": name, "targets": targets}), flush=True)
    ledger = helpers.sql("""SELECT row_to_json(s) FROM (SELECT
        (SELECT count(*) FROM model_assignment_revisions WHERE revision=2) AS revision_rows,
        (SELECT command_id IS NULL FROM model_assignment_revisions WHERE revision=2) AS operator_revision,
        (SELECT count(*) FROM events WHERE kind='operator_model_assignment'
            AND detail->>'operation_id'='mixed-allocation-20261006-owner-correction-v1') AS operation_events,
        (SELECT count(*) FROM model_assignment_revisions WHERE revision IN (0,1)) AS prior_revisions_preserved,
        (SELECT count(*) FROM model_assignment_commands WHERE state='requested') AS pending_commands)s""")
    if (ledger["revision_rows"] != 1 or not ledger["operator_revision"] or ledger["operation_events"] != 1
            or ledger["prior_revisions_preserved"] != 2):
        raise ValueError("Correction receipt duplicated or previous history changed")
    result = {"state": "verified", "at": datetime.now(UTC).isoformat(), "operation_id": plan["operation_id"],
              "account": account, "policy_revision": status["revision"], "effective_assignments": status["assignments"],
              "model_counts": dict(Counter(choice["model"] for choice in plan["assignments"].values())),
              "agents": [{k: agent[k] for k in ("id", "name", "active", "model", "reasoning_effort")}
                         for agent in api["/v1/agents"]], "active_roles": active, "health": api["/healthz"],
              "containers": {n: {"id": row["Id"], "image": row["Image"], "running": row["State"]["Running"],
                                   "restart_count": row["RestartCount"]} for n, row in rows.items()},
              "container_ids_images_configuration_restart_counts_preserved": True, "frozen_requests_preserved": preserved,
              "frozen_request_manifest_sha256": hashlib.sha256(json.dumps(before["frozen_requests"], sort_keys=True).encode()).hexdigest(),
              "receipt_readback_cached": receipt["cached"], "ledger": ledger, "producers": producers,
              "producer_probe_source_sha256": PROBE_SHA256, "slack_messages_sent": 0, "model_inference_calls": 0}
    h.save("verify.json", result)
    return result


if __name__ == "__main__":
    result = verify()
    print(json.dumps({key: result[key] for key in ("state", "at", "policy_revision", "model_counts", "ledger",
                                                 "frozen_requests_preserved", "receipt_readback_cached")}, indent=2))

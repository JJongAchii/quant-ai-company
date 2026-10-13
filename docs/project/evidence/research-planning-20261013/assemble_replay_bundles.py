"""Assemble the P1 replay bundle from committed evidence only. No database, network or model access.

Each task gets the packet facts that were operating when it was proposed. The operating
registry is traced through attachment receipts whose before/after hashes must match the
committed registry files. Facts the evidence does not record stay unset, so the replay
reports them as not_declared.
"""

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent
ENVELOPE = "etf_strategy"


def load(name):
    return json.loads((ROOT / name).read_text())


def sha(name):
    return hashlib.sha256((ROOT / name).read_bytes()).hexdigest()


def at(value):
    return datetime.fromisoformat(value)


def registry_facts(name, program_digest):
    packets = [p for p in load(name)["packets"] if p["program_digest"] == program_digest and p["envelope"] == ENVELOPE]
    assert len(packets) == 1, (name, len(packets))
    return packets[0]["blocking_gaps"]


def timeline(program_digest):
    cutover = load("research-programs-20260929/data-evidence-worker-cutover.json")
    provenance = load("research-programs-20260930/etf-provenance-attached.json")
    lineage = load("etf-raw-lineage-20260930/raw-lineage-attached.json")
    activation = load("etf-exploration-20260930/activation-server.json")
    first = "research-programs-20260929/etf-data-evidence-registry.json"
    second = "research-programs-20260930/etf-provenance-evidence-registry.json"
    third = "etf-raw-lineage-20260930/raw-lineage-evidence-registry.json"
    # Each attachment receipt names the registry it replaced; the committed files must be those bytes.
    assert provenance["old_registry_sha256"] == sha(first)
    assert provenance["new_registry_sha256"] == lineage["old_registry_sha256"] == sha(second)
    assert lineage["new_registry_sha256"] == activation["registry_before_sha256"] == sha(third)
    return [
        (None, at(cutover["completed_at"]), False, None,
         "No verified packet before the data-evidence worker cutover completed at "
         f"{cutover['completed_at']} (research-programs-20260929/data-evidence-worker-cutover.json; "
         "FIRST-TASK-DATA-GAP-20260929.md)"),
        (at(cutover["completed_at"]), at(provenance["completed_at"]), True, registry_facts(first, program_digest),
         f"{first} (sha256 {sha(first)[:12]}), operating until research-programs-20260930/etf-provenance-attached.json"),
        (at(provenance["completed_at"]), at(lineage["completed_at"]), True, registry_facts(second, program_digest),
         f"{second} (sha256 {sha(second)[:12]}), operating until etf-raw-lineage-20260930/raw-lineage-attached.json"),
        (at(lineage["completed_at"]), at(activation["started_at"]), True, registry_facts(third, program_digest),
         f"{third} (sha256 {sha(third)[:12]}), operating until the activation that started at "
         f"{activation['started_at']} (etf-exploration-20260930/activation-server.json registry_before)"),
    ]


def bundle_e06537d3():
    base = load("conditional-program-20261006/server-baseline-before.json")["database"]
    program = base["programs"][0]
    assert program["id"].startswith("e06537d3"), program["id"]
    windows = timeline(program["manifest_digest"])
    second = load("research-programs-20260929/second-task-data-assessment.json")["id"]
    tasks = []
    for row in sorted(base["tasks"], key=lambda t: t["created_at"]):
        assert row["proposal"]["envelope"] == ENVELOPE, row["id"]
        task = {"id": row["id"], "state": row["state"], "proposal": row["proposal"],
                "data_decision": (row.get("data_assessment") or {}).get("decision"),
                "selection_decision": (row.get("decision") or {}).get("decision"), "created_at": row["created_at"]}
        created = at(row["created_at"])
        for start, end, present, gaps, provenance in windows:
            if (start is None or start <= created) and created < end:
                task |= {"packet_present": present, "blocking_gaps": gaps, "packet_provenance": provenance}
        if row["id"] == second:
            task |= {"declared_execution_timing": "same_day_close_fill",
                     "declaration_provenance": "FIRST-TASK-DATA-GAP-20260929.md names the second task's 08:30 decision "
                                               "with same-day close fill; research-programs-20260929/"
                                               "second-task-data-assessment.json records that task id"}
        tasks.append(task)
    return {"program_id": program["id"], "program_manifest_digest": program["manifest_digest"],
            "program": program["spec"], "packets": [], "tasks": tasks,
            "provenance": ["conditional-program-20261006/server-baseline-before.json (program spec and tasks)",
                           "registry attachment receipts listed per task"]}


if __name__ == "__main__":
    value = bundle_e06537d3()
    (OUT / "replay-bundle-e06537d3.json").write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    known = sum("packet_present" in task for task in value["tasks"])
    print(f"{len(value['tasks'])} tasks, {known} with recorded packet facts", file=sys.stderr)

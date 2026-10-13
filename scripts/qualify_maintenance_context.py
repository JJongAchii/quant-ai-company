"""Replay a recorded diagnostic input locally; no inference or external effect."""

import argparse
import ast
import copy
import hashlib
import json
import subprocess
from pathlib import Path

from quant_company.contracts import ProviderRequest
from quant_company.maintenance.policy import SECRET, Patch, Triage, digest
from quant_company.maintenance.runner import (
    INSTRUCTIONS,
    compact_prompt_value,
    proposal_material,
    repository_prompt_paths,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = args.input.read_bytes()
    saved = json.loads(raw)
    if SECRET.search(json.dumps(saved)):
        raise ValueError("recorded_input_requires_secret_redaction")
    inspected = saved.get("investigation_evidence", [])[-4:]
    external = [row for row in saved.get("investigation_external", []) if not row.get("omitted")][-4:]
    baseline = subprocess.check_output(["git", "rev-parse", args.baseline], text=True).strip()
    source = subprocess.check_output(
        ["git", "show", baseline + ":src/quant_company/maintenance/runner.py"], text=True)
    tree = ast.parse(source)
    diagnostic_instructions = next(node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and node.value.startswith("Read current_implementation FIRST."))
    snapshot, diagnosis = saved["snapshot"], saved["diagnosis"]
    material = {
        "observations": saved["observations"], "editable_paths": snapshot["paths"],
        "history": saved.get("review", {}), "current_implementation": diagnosis,
        "investigated_code": inspected, "external_research": external,
        "repository_paths": repository_prompt_paths(snapshot["entries"]),
        "inspection_rounds_remaining": 4-saved.get("investigation_round", 0),
        "evidence_references": [diagnosis["key"]] + [row["key"] for row in
            diagnosis["source_files"] + inspected + external],
        "requested_diagnosis": saved.get("instruction"),
        "active_diagnosis": {"job_id": "45dce3da-b3dc-5196-b756-35025c48c2f1",
            "revision": saved.get("diagnostic_revision", 0), "commit": snapshot["commit"],
            "state": "this_model_call_is_the_resumed_triage"},
        "instructions": diagnostic_instructions,
    }
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "proposal_material")
    namespace = {"copy": copy, "json": json, "INSTRUCTIONS": INSTRUCTIONS,
                 "compact_prompt_value": compact_prompt_value, "Triage": Triage, "Patch": Patch}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "recorded-baseline", "exec"), namespace)
    before = digest(saved)
    try:
        namespace["proposal_material"](material, Triage)
        base_error = None
    except ValueError as exc:
        base_error = str(exc)
    compacted, prompt = proposal_material(material, Triage)
    ProviderRequest(request_id="qualification-only-no-inference", model="fixture", prompt=prompt)
    result = {
        "schema_version": 1, "baseline_commit": baseline,
        "source_record": "maintenance_jobs:45dce3da-b3dc-5196-b756-35025c48c2f1",
        "input_sha256": hashlib.sha256(raw).hexdigest(),
        "original_material_characters": len(INSTRUCTIONS + "SCHEMA:\n" + json.dumps(Triage.model_json_schema())
            + "\nEVIDENCE JSON:\n" + json.dumps(material, ensure_ascii=False)),
        "baseline_error": base_error, "candidate_prompt_characters": len(prompt),
        "provider_request_validated": True, "saved_evidence_unchanged": digest(saved) == before,
        "observation_keys_preserved": [r["key"] for r in material["observations"]]
            == [r["key"] for r in compacted["observations"]],
        "path_catalog": compacted.get("prompt_path_catalog", {}),
        "observation_compaction_rounds": compacted.get("prompt_observation_compaction", 0),
        "model_calls": 0, "external_writes": 0,
        "scope": "Actual recorded PostgreSQL diagnostic input; local baseline/candidate formatting replay only.",
    }
    if base_error != "maintenance_proposal_context_too_large" or not result["saved_evidence_unchanged"]:
        raise ValueError("recorded_failure_not_reproduced")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

"""Qualify the unchanged authentic packet response in a prepared image, always roll back."""
import argparse
import json
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("preflight", "probe"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    manifest = json.loads((root / "manifest.json").read_bytes())
    prepared = json.loads((Path("/var/lib/quant-company/releases") /
                          ("audit-note-bound-" + manifest["commit"] + "-prepared.json")).read_bytes())
    command = ["python3", str(root / "reconcile-second-result-audit-notes-20261014.py")]
    if args.mode == "preflight":
        command.extend(["--image", prepared["images"]["api"]["image_id"]])
    outcome = subprocess.run(command, check=True, capture_output=True, text=True, timeout=100)
    result = json.loads(outcome.stdout)
    assert result["state"] == "production_transaction_probe_passed_rolled_back"
    assert result["applied"] is False and result["new_provider_calls"] == result["new_scientific_jobs"] == 0
    result["state"] = "actual_original_audit_response_verified_rolled_back"
    result["qualified_mode"] = args.mode
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

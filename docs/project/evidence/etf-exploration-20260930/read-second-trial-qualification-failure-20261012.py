"""Inspect a failed warmup sandbox without exposing raw logs, inputs or metrics."""

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

JOB = "69a52b87-3b37-56c1-9ac5-97c5819c4746"


def main():
    config = json.loads(Path("/home/achii/.config/quant-company/research-worker.json").read_bytes())
    root = Path(config["state_dir"]) / "jobs" / JOB
    assignment = json.loads((root / "state.json").read_bytes())["assignment"]
    manifest = assignment["manifest"]
    assert manifest["trial_id"] == "5868e1af-475c-5b7b-ad4a-da55fc3966e3"
    assert not (root / "sandbox-evaluation.json").exists()
    records = []
    for name in ("qualify/stderr.log", "qualify/stdout.log", "executor.log"):
        path = root / name
        if not path.is_file():
            records.append({"name": name, "exists": False})
            continue
        raw = path.read_bytes()
        content = raw.decode("utf-8", errors="replace")
        exceptions = re.findall(r"(?m)^([A-Za-z][A-Za-z0-9_.]*):", content)
        last = content.strip().splitlines()[-1] if content.strip() else ""
        safe_last = last if re.fullmatch(r"[A-Za-z][A-Za-z0-9_. :()=-]{0,250}", last) else None
        frames = [{"path": path, "line": int(line), "function": function}
                  for path, line, function in re.findall(
                      r'File "(/code/[^"\n]+)", line (\d+), in ([a-zA-Z0-9_<>]+)', content)]
        literals = sorted(set(re.findall(r"(?:KeyError|NameError|ModuleNotFoundError): ['\"]([a-zA-Z0-9_.-]+)['\"]", content)))
        records.append({"name": name, "exists": True, "sha256": hashlib.sha256(raw).hexdigest(),
                        "bytes": len(raw), "exception_classes": exceptions,
                        "bounded_ascii_diagnostic": safe_last,
                        "code_frames": frames, "missing_identifiers": literals,
                        "syntax_error": "SyntaxError:" in content,
                        "sandbox_mount_error": "bwrap:" in content})
    print(json.dumps({"observed_at": datetime.now(UTC).isoformat(), "job_id": JOB,
                      "code_commit": manifest["plan"]["code_commit"],
                      "code_files": manifest["code_files"], "records": records,
                      "worker_state_mutated": False, "raw_logs_returned": False,
                      "performance_metrics_read_or_returned": False}, indent=2))


if __name__ == "__main__":
    main()

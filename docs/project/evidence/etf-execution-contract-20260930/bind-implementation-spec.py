"""Preserve the initial package and bind the review's concrete implementation supplement."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from quant_company.company import fingerprint

EVIDENCE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    with path.open("x") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n")


def main():
    current = EVIDENCE / "review-package.json"
    initial = EVIDENCE / "review-package-initial.json"
    validation = EVIDENCE / "validation.json"
    initial_validation = EVIDENCE / "validation-initial.json"
    review = json.loads(current.read_text())
    if fingerprint(review["package"]) != review["review_digest"]:
        raise ValueError("Initial review package changed")
    if initial.exists() or initial_validation.exists():
        raise ValueError("Amendment already recorded; do not repeat")
    prior_sha = sha(current)
    current.rename(initial)
    validation.rename(initial_validation)
    package = review["package"]
    for name in ("implementation-spec.md", "digest-encoding.json"):
        package["files"][name] = sha(EVIDENCE / name)
    write(current, {"package": package, "review_digest": fingerprint(package)})
    write(EVIDENCE / "package-amendment.json", {
        "schema_version": 1, "observed_at": datetime.now(UTC).isoformat(),
        "reason": "Independent admission review requested explicit fields, gates, result propagation and cross-program lineage.",
        "script_sha256": sha(Path(__file__)), "initial_package_file_sha256": prior_sha,
        "initial_review_digest": review["review_digest"], "final_review_digest": fingerprint(package),
        "final_package_file_sha256": sha(current), "unchanged_input_files": package["input_files"],
        "production_mutated": False, "readiness_granted": False, "scientific_trials_added": 0,
    })
    print(json.dumps({"review_digest": fingerprint(package), "initial_package_preserved": True}))


if __name__ == "__main__":
    main()

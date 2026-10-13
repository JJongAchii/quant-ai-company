"""Preserve review history and bind the final admission clarifications."""

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
    path = EVIDENCE / "review-package.json"
    prior = json.loads(path.read_text())
    package = prior["package"]
    if fingerprint(package) != prior["review_digest"]:
        raise ValueError("Prior package digest mismatch")
    for name, digest in package["files"].items():
        source = "implementation-spec-before-admission-clarification.md" if name == "implementation-spec.md" else name
        if sha(EVIDENCE / source) != digest:
            raise ValueError("Unexpected change outside the documented clarification: " + name)
    backup = EVIDENCE / "review-package-before-admission-clarification.json"
    if backup.exists():
        raise ValueError("Finalization already recorded; do not repeat")
    prior_sha = sha(path)
    archived_reviews = {}
    for name in ("data-draft-review.json", "admission-draft-review.json"):
        source = EVIDENCE / name
        if source.exists():
            archived = EVIDENCE / (source.stem + "-before-clarification.json")
            with archived.open("xb") as stream:
                stream.write(source.read_bytes())
            archived_reviews[archived.name] = sha(archived)
    path.rename(backup)
    (EVIDENCE / "validation.json").rename(EVIDENCE / "validation-before-admission-clarification.json")
    package["files"]["implementation-spec.md"] = sha(EVIDENCE / "implementation-spec.md")
    write(path, {"package": package, "review_digest": fingerprint(package)})
    write(EVIDENCE / "final-package-receipt.json", {
        "schema_version": 1, "observed_at": datetime.now(UTC).isoformat(), "script_sha256": sha(Path(__file__)),
        "prior_package_sha256": prior_sha, "prior_review_digest": prior["review_digest"],
        "final_package_sha256": sha(path), "final_review_digest": fingerprint(package),
        "archived_reviews": archived_reviews,
        "reason": "Bind trusted wrapper scope propagation, first-cycle two-result ceiling and signed lineage cap.",
        "current_runtime_admissible": False, "production_mutated": False, "scientific_trials_added": 0,
    })
    print(json.dumps({"final_review_digest": fingerprint(package), "prior_package_preserved": True}))


if __name__ == "__main__":
    main()

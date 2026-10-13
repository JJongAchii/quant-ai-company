"""Verify actual independent local draft-review files against the frozen package."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from quant_company.company import fingerprint

ROOT = Path(__file__).resolve().parents[4]
EVIDENCE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    review = json.loads((EVIDENCE / "review-package.json").read_text())
    if fingerprint(review["package"]) != review["review_digest"]:
        raise ValueError("Review package changed")
    verified = []
    for name in ("data-draft-review.json", "admission-draft-review.json"):
        path = EVIDENCE / name
        row = json.loads(path.read_text())
        if row["review_digest"] != review["review_digest"] or row["verdict"] != "reviewable_draft":
            raise ValueError("Stale or rejected review: " + name)
        if row["blocking_findings"] or row["scientific_trials_added"] != 0:
            raise ValueError("Unexpected blocker or scientific trial: " + name)
        for field in ("current_runtime_admissible", "readiness_granted", "production_assessment"):
            if row[field] is not False:
                raise ValueError("Local review cannot grant readiness: " + field)
        for field in ("owner_approval_granted", "historical_point_in_time_verified"):
            if field in row and row[field] is not False:
                raise ValueError("Local review cannot manufacture authority or historical evidence")
        checks = dict(row["reviewed_file_sha256"])
        checks.update(row.get("reviewed_runtime_source_sha256", {}))
        if not checks or any(sha(ROOT / source) != expected for source, expected in checks.items()):
            raise ValueError("Reviewed file changed: " + name)
        verified.append({"path": str(path.relative_to(ROOT)), "sha256": sha(path),
                         "reviewer": row["reviewer"], "files_verified": len(checks), "verdict": row["verdict"]})
    result = {
        "schema_version": 1, "observed_at": datetime.now(UTC).isoformat(), "script_sha256": sha(Path(__file__)),
        "review_digest": review["review_digest"], "reviews": verified, "all_passed": True,
        "scope": "actual_local_independent_draft_review_files_not_production_staff_assessments",
        "production_assessment": False, "runtime_admissible": False, "readiness_granted": False,
        "scientific_trials_added": 0,
    }
    with (EVIDENCE / "review-validation.json").open("x") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"reviews_verified": len(verified), "review_digest": review["review_digest"]}))


if __name__ == "__main__":
    main()

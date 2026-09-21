"""Import hashes from an already independently audited frozen pilot, without running research.

Run with the frozen lab environment (qlab + PyYAML) and explicit source/output paths.
This script cannot create an audit verdict or select another strategy.
"""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def main():
    from qlab.audits.receipt import check_receipt
    from qlab.audits.record import parse_audit, validate_audit

    parser = argparse.ArgumentParser()
    parser.add_argument("--lab", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    lab = args.lab.resolve()
    evidence_commit = "b1c813e1e99bcc6fb9f1329562c17ea14e483c7f"
    if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=lab, text=True).strip() != evidence_commit:
        raise ValueError("reference_commit_mismatch")
    audit_path = "labs/company-kr-etf-pilot/audits/AUDIT-leak-auditor-20260919-company-kr-etf-pilot-p11-report-path.md"
    record = parse_audit(lab / audit_path)
    if (record.verdict != "pass" or record.objective_digest != "03d58ee449e6"
            or record.scope_digest != "e1c51cf90a17"):
        raise ValueError("reference_audit_mismatch")
    if hashlib.sha256((lab.parent / "docs/objective.md").read_bytes()).hexdigest()[:12] != record.objective_digest:
        raise ValueError("reference_objective_mismatch")
    violations = validate_audit(record, lab)
    receipt_violations = check_receipt(record)
    if violations or receipt_violations:
        raise ValueError("reference_audit_invalid", violations, receipt_violations)

    def digest(path):
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    base = "labs/company-kr-etf-pilot"
    scope = json.loads((lab / "docs/research/company-kr-etf-pilot/P11-AUDIT-REQUEST.json").read_text())
    reference = {
        "id": "kr-etf-p11-replay-v1", "title": "국내 ETF P11 고정 후보 3개 재현",
        "kind": "equivalent_replay", "worker_id": "worker", "hostname": "DESKTOP-5T00NAF",
        "gpu": "NVIDIA GeForce RTX 3070", "code_commit": "02649715bd3661826253e1ce84f8002d2a74c822",
        "evidence_commit": evidence_commit,
        "lake_id": "krx-etf-9c4fb62c8f19a158aee1dff36af37706-meta-2d8776e0c3fbe4e020dc383718239ee5",
        "config_files": {p: digest(lab / p) for p in [
            *[f"{base}/configs/{method}-liquid-11.json" for method in ("m1", "m2", "m3")],
            "docs/research/company-kr-etf-pilot/DISCOVERY-ARGS-v4-execution.json",
        ]},
        "input_files": {p: digest(lab / p) for p in [
            f"{base}/data/snapshot-20260918/{period}/{name}" for period in ("warmup", "dev")
            for name in ("prices.parquet", "meta.parquet", "snapshot.json")
        ]},
        "expected_outputs": {}, "audit_path": audit_path, "audit_sha256": digest(lab / audit_path),
        "audit_receipt_sha256": digest((lab / audit_path).with_suffix(".receipt.json")),
        "objective_digest": record.objective_digest, "scope_digest": record.scope_digest,
        "scope_files": {p: digest(lab / p) for p in scope["scopeRepoRelative"]},
        "description": "개발기간 2014-01-01~2026-08-31, 준비기간 2012~2013. 월별 M1/M2/M3, "
                       "편도 비용 10/30bp. 고정된 코드·입력·결과와 기존 독립 감사의 재현 검증. "
                       "새 과학 시행 0회, 기준선·초과성과 미측정, 확증·투자 승인 아님.",
    }
    names = ["objective.json", *[f"{stem}-{cost}.{ext}" for cost in ("base", "stress")
             for stem, ext in (("daily", "csv"), ("monthly", "csv"), ("orders", "json"),
                               ("memberships", "json"), ("terminal-events", "json"))]]
    for method in ("m1", "m2", "m3"):
        for name in names:
            reference["expected_outputs"][f"{method}/{name}"] = digest(lab / base / "output" / f"eval-{method}-liquid-11" / name)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "p11.json").write_text(json.dumps(reference, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    for source, name in [(lab / audit_path, "audit.md"),
                         ((lab / audit_path).with_suffix(".receipt.json"), "audit.receipt.json")]:
        (args.output / name).write_bytes(source.read_bytes())
    print(json.dumps({"original_scope_files": len(reference["scope_files"]),
                      "economic_output_files": len(reference["expected_outputs"]),
                      "new_scientific_trials": 0}))


if __name__ == "__main__":
    main()

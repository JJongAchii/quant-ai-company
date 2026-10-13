"""Persist the trusted read-only reconciliation as a new, scoped operator verification."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
proof = json.loads((ROOT / "reconciliation.json").read_text())
reviews = proof["reviews"]
assert len(reviews) == 11
assert all(row["runtime_request_matches"] and row["runtime_state"] == "failed"
           and row["runtime_fault"] == "uncertain" and not row["response_available"]
           and not row["raw_cli_output_retained"] for row in reviews)
material = {
    "scope": "Read-only original independent-review request/receipt reconciliation",
    "review_ids": [row["id"] for row in reviews],
    "database_row_digests": {row["id"]: row["database_row_digest"] for row in reviews},
    "receipt_sha256": {row["id"]: row["receipt_sha256"] for row in reviews},
    "request_match_count": len(reviews),
    "outcome": "indeterminate_model_completion",
    "reason": "Original failed uncertain receipts match frozen requests but retain no result or raw CLI output.",
    "model_calls": 0,
    "boundary": "Do not replay uncertain reviews or change employee grades. This verifies receipt metadata only."
}
identity = "operator-review-reconciliation-" + hashlib.sha256(
    json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:32]
probe = r'''
import hashlib,json,os,sys
from pathlib import Path
from urllib.parse import quote
from quant_company.company import Company
from quant_company.config import Settings
from quant_company.system_state import record_verification
password=Path(os.environ["DATABASE_PASSWORD_FILE"]).read_text().strip()
os.environ["DATABASE_URL"]="postgresql://"+quote(os.environ.get("DATABASE_USER","company"),safe="")+":"+quote(password,safe="")+"@postgres:5432/"+quote(os.environ.get("DATABASE_NAME","quant_company"),safe="")
entry=json.load(sys.stdin)
company=Company(Settings())
with company.db.transaction() as conn:
    before=conn.execute("SELECT jsonb_object_agg(id,md5(to_jsonb(v)::text)) AS hashes FROM staff_independent_reviews v WHERE id=ANY(%s)",
        (entry["evidence"]["review_ids"],)).fetchone()["hashes"]
    assert before==entry["evidence"]["database_row_digests"],"original_review_changed"
    record_verification(conn,company,identity=entry["identity"],feature="staff_review_receipt_reconciliation",
        scope="Original 11 blocked uncertain Claude independent explanation reviews; metadata only",
        evidence=entry["evidence"])
    row=conn.execute("SELECT id,feature,scope,evidence FROM system_verifications WHERE id=%s",(entry["identity"],)).fetchone()
    assert row["evidence"]==entry["evidence"],"verification_identity_conflict"
    assert conn.execute("SELECT jsonb_object_agg(id,md5(to_jsonb(v)::text)) AS hashes FROM staff_independent_reviews v WHERE id=ANY(%s)",
        (entry["evidence"]["review_ids"],)).fetchone()["hashes"]==before
print(json.dumps({"verification_id":row["id"],"feature":row["feature"],"original_reviews_unchanged":True,
    "model_calls":0,"database_writes":"one new scoped operator verification; idempotent identity",
    "limits":entry["evidence"]["reason"]}))
'''
os.umask(0o077)
result = subprocess.run(["docker", "exec", "-i", "quant-company-maintenance-1", "python", "-c", probe],
    input=json.dumps({"identity": identity, "evidence": material}), text=True, capture_output=True, timeout=45)
if result.returncode:
    raise RuntimeError("operator_verification_failed; original reviews must not be replayed")
receipt = json.loads(result.stdout)
(ROOT / "verification.json").write_text(json.dumps(receipt, indent=2) + "\n")
print(json.dumps(receipt))

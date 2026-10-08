"""Operator read-only reconciliation. Never invokes a model or emits private prompts."""
import json
import re
import subprocess
from datetime import UTC, datetime


def command(args, *, stdin=None):
    result = subprocess.run(args, input=stdin, text=True, capture_output=True, check=False)
    if result.returncode:
        errors = re.findall(r"\b([A-Za-z]+Error):", result.stderr)
        raise RuntimeError("operator_read_failed_" + (errors[-1] if errors else "unknown"))
    return result.stdout


sql = """BEGIN READ ONLY;
SELECT jsonb_agg(jsonb_build_object('id',id,'request',request,'input_digest',input_digest,
'row_digest',md5(to_jsonb(v)::text))) FROM staff_independent_reviews v
WHERE state='blocked' AND error='uncertain';
COMMIT;"""
raw = command(["docker", "exec", "-u", "postgres", "quant-company-postgres-1",
               "psql", "-XAt", "-v", "ON_ERROR_STOP=1", "-d", "quant_company", "-c", sql])
rows = json.loads(next(line for line in raw.splitlines() if line.startswith("[")))
probe = """
import hashlib, json, os, re, sys
from pathlib import Path
from quant_company.contracts import ProviderRequest
from pydantic import ValidationError
from quant_company.providers.codex_runner import request_digest
root=Path(os.environ["CLAUDE_JOBS_DIR"])
output=[]
for row in json.load(sys.stdin):
    identity=row["id"]
    if not re.fullmatch(r"review-[a-f0-9-]{36}",identity):
        raise ValueError("invalid_review_identity")
    path=root/(identity+".json")
    raw=path.read_bytes()
    if len(raw)>2097152:
        raise ValueError("receipt_too_large")
    receipt=json.loads(raw)
    fields=dict(row["request"])
    ignored_defaults=[]
    for key, default in (("session",None),("output_contract","agent_decision")):
        if key not in ProviderRequest.model_fields and fields.get(key,object()) == default:
            fields.pop(key)
            ignored_defaults.append(key)
    try:
        request=ProviderRequest.model_validate(fields)
    except ValidationError as exc:
        output.append({"id":identity,"runtime_state":receipt["state"],"runtime_fault":receipt.get("fault"),
          "request_validation_errors":[{"loc":list(item["loc"]),"type":item["type"]}
                                       for item in exc.errors(include_input=False,include_url=False)]})
        continue
    expected=request_digest(request)
    output.append({"id":identity,"db_input_digest":row["input_digest"],
      "database_row_digest":row["row_digest"],"runtime_expected_digest":expected,
      "legacy_default_fields_omitted":ignored_defaults,
      "runtime_stored_digest":receipt["input_digest"],
      "runtime_request_matches":receipt["request_id"]==identity and receipt["input_digest"]==expected
                               and receipt["provider"]=="claude",
      "runtime_state":receipt["state"],"runtime_fault":receipt.get("fault"),
      "response_available":receipt.get("result") is not None,
      "receipt_sha256":hashlib.sha256(raw).hexdigest(),
      "raw_cli_output_retained":any(key in receipt for key in ("stdout","stderr","events","raw_output"))})
print(json.dumps(output))
"""
matched = json.loads(command(["docker", "exec", "-i", "quant-company-claude-runtime-1",
    "/opt/company/.venv/bin/python", "-c", probe], stdin=json.dumps(rows)))
print(json.dumps({"captured_at":datetime.now(UTC).isoformat(), "reviews":matched,
    "model_calls":0,"database_writes":0,
    "limits":["DB input fingerprints and runtime normalized request digests are separate formats.",
              "Failed uncertain receipts without raw CLI output cannot establish model completion or token usage.",
              "Original review rows and runtime receipts are not changed or replayed."]}))

"""Trusted host qualification; no model calls, all PostgreSQL changes roll back."""

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
MANIFEST = json.loads((ROOT / "manifest.json").read_text())
SERVICE = "quant-company-maintenance-1"


def run(args, timeout=60):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        frames = re.findall(r'File "([^"]+)", line ([0-9]+)', result.stderr)
        errors = re.findall(r"^([A-Za-z.]+Error|AssertionError):", result.stderr, re.M)
        raise RuntimeError("operator_command_failed_" + args[0] + " " + json.dumps({
            "error_classes": errors[-2:], "frames": [(Path(path).name, line) for path, line in frames[-4:]]}))
    return result.stdout.strip()


PROBE = r'''
import copy,hashlib,json,os
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote
import psycopg
from psycopg.rows import dict_row
from quant_company.company import Company
from quant_company.config import Settings
from quant_company.db import Database
from quant_company.maintenance.github import GitHub
from quant_company.maintenance.policy import MaintenanceConfig,Triage,digest
from quant_company.maintenance.runner import proposal_material,repository_prompt_paths
import quant_company.maintenance.runner as runner
from quant_company.system_state import diagnosis_context,record_repository

password=Path(os.environ["DATABASE_PASSWORD_FILE"]).read_text().strip()
os.environ["DATABASE_URL"]="postgresql://"+quote(os.environ.get("DATABASE_USER","company"),safe="")+":"+quote(password,safe="")+"@postgres:5432/"+quote(os.environ.get("DATABASE_NAME","quant_company"),safe="")
class RollbackDatabase(Database):
    @contextmanager
    def transaction(self):
        with psycopg.connect(self.url,row_factory=dict_row,connect_timeout=5) as conn:
            try:
                yield conn
            finally:
                conn.rollback()

company=Company(Settings())
company.db=RollbackDatabase(company.settings.database_url)
config=MaintenanceConfig.model_validate_json(Path("/etc/quant-company/maintenance.json").read_text())
github=GitHub(config)
with company.db.transaction() as conn:
    repo=conn.execute("SELECT * FROM repository_evidence ORDER BY checked_at DESC LIMIT 1").fetchone()
    assert repo,"repository_evidence_required"
    snapshot=repo["snapshot"]
    fingerprint=conn.execute("SELECT jsonb_object_agg(id,md5(to_jsonb(v)::text)) AS hashes FROM staff_independent_reviews v WHERE state='blocked' AND error='uncertain'").fetchone()["hashes"]
    jobs=conn.execute("SELECT * FROM maintenance_jobs WHERE id=ANY(%s::uuid[]) ORDER BY id",
        (json.loads(os.environ["CASE_IDS"]),)).fetchall()
    calls=conn.execute("SELECT count(*) AS n FROM maintenance_calls").fetchone()["n"]
files,coverage=github.read_repository(snapshot,repo["files"])
records=[]
with company.db.transaction() as conn:
    record_repository(conn,snapshot,files,repo["metadata"])
    for job in jobs:
        payload=job["payload"]
        original=conn.execute("SELECT request FROM maintenance_calls WHERE job_id=%s AND id LIKE '%%triage%%' ORDER BY created_at DESC LIMIT 1",(job["id"],)).fetchone()
        assert original,"original_triage_request_required"
        material=json.loads(original["request"]["prompt"].split("EVIDENCE JSON:\n",1)[1])
        # A fresh construction must not inherit the prior call's completed budget passes.
        for name in tuple(material):
            if name.startswith("prompt_"):
                material.pop(name)
        old_digest=digest(payload)
        candidate_mode=hasattr(runner,"diagnostic_history")
        kwargs={"observations":payload["observations"]} if candidate_mode else {}
        diagnosis=diagnosis_context(conn,company,payload["owners"],snapshot,payload.get("instruction",""),**kwargs)
        material["current_implementation"]=diagnosis
        material["history"]=runner.diagnostic_history(payload) if candidate_mode else payload.get("review",{})
        material["editable_paths"]=snapshot["paths"]
        material["repository_paths"]=repository_prompt_paths(snapshot["entries"])
        inspected=payload.get("investigation_evidence",[])[-4:]
        external=[row for row in payload.get("investigation_external",[]) if not row.get("omitted")][-4:]
        material["investigated_code"]=inspected
        material["external_research"]=external
        material["evidence_references"]=[diagnosis["key"]]+[r["key"] for r in diagnosis["source_files"]+inspected+external]
        shown,prompt=proposal_material(material,Triage)
        assert digest(payload)==old_digest,"stored_original_payload_changed"
        from quant_company.model_policy import effective_role
        role=effective_role(company,conn,"maintainer")
        records.append({"job_id":str(job["id"]),"state":job["state"],"prompt_characters":len(prompt),
            "source_characters":sum(len(r["content"]) for r in shown["current_implementation"]["source_files"]),
            "source_paths":[r["path"] for r in shown["current_implementation"]["source_files"]],
            "history_evidence_count":len(shown.get("history",{}).get("evidence",[])),
            "bounded_system_characters":len(json.dumps(shown["current_implementation"]["system"],ensure_ascii=False)),
            "model":role.model,"reasoning_effort":role.reasoning_effort,
            "source_observation_keys":[r["key"] for r in shown["observations"]],
            "request_preparation_only":True})
with company.db.transaction() as conn:
    assert conn.execute("SELECT count(*) AS n FROM maintenance_calls").fetchone()["n"]==calls,"unexpected_model_call"
    assert conn.execute("SELECT jsonb_object_agg(id,md5(to_jsonb(v)::text)) AS hashes FROM staff_independent_reviews v WHERE state='blocked' AND error='uncertain'").fetchone()["hashes"]==fingerprint,"original_uncertain_review_changed"
print(json.dumps({"repository_commit":snapshot["commit"],"coverage":{k:v for k,v in coverage.items() if k!="omitted_paths"},
    "omitted_paths":len(coverage["omitted_paths"]),"required_review_code_available":
    all(p in files for p in ("src/quant_company/staff/independent_review.py","src/quant_company/providers/claude_runner.py")),
    "cases":records,"model_calls":0,"database_writes":"rolled_back","original_uncertain_reviews_unchanged":True}))
'''


def probe(image, service):
    env = dict(pair.split("=", 1) for pair in service["Config"]["Env"])
    command = ["docker", "create", "--pull", "never", "--read-only", "--memory", "512m",
               "--cpus", "0.5", "--pids-limit", "128", "--cap-drop", "ALL",
               "--security-opt", "no-new-privileges:true", "--user", service["Config"]["User"],
               "--tmpfs", "/tmp:size=64m,mode=1777", "--entrypoint", "python"]
    network = next(name for name in service["NetworkSettings"]["Networks"] if name.endswith("_core"))
    command += ["--network", network]
    for pair in service["Config"]["Env"]:
        command += ["--env", pair]
    command += ["--env", "CASE_IDS=" + json.dumps(MANIFEST["case_ids"])]
    targets = {env["ROLES_FILE"], env["DATABASE_PASSWORD_FILE"]}
    for mount in service["Mounts"]:
        if (mount["Destination"] in targets or mount["Destination"].startswith("/etc/quant-company/")
                or mount["Destination"] == "/run/secrets/maintenance_github_key"):
            assert mount["Type"] == "bind"
            command += ["--mount", "type=bind,src=" + mount["Source"] + ",dst=" + mount["Destination"] + ",readonly"]
    container_id = run(command + [image, "-c", PROBE])
    try:
        for name in service["NetworkSettings"]["Networks"]:
            if name != network:
                run(["docker", "network", "connect", name, container_id])
        return json.loads(run(["docker", "start", "-a", container_id], timeout=300))
    finally:
        run(["docker", "rm", "-f", container_id])


os.umask(0o077)
service = json.loads(run(["docker", "inspect", SERVICE]))[0]
assert service["Image"] == MANIFEST["base_image_id"], "production_image_changed"
assert json.loads(run(["docker", "image", "inspect", service["Config"]["Image"]]))[0]["Id"] == service["Image"]
installed = run(["docker", "exec", SERVICE, "python", "-c",
    "import hashlib,json;from pathlib import Path;root=Path(" + repr(MANIFEST["module_root"]) + ");"
    "print(json.dumps({p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in "
    + repr(list(MANIFEST["base_module_sha256"])) + "}))"])
assert json.loads(installed) == MANIFEST["base_module_sha256"], "production_source_changed"
for name, expected in MANIFEST["candidate_module_sha256"].items():
    assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected
run(["docker", "build", "--network", "none", "--build-arg", "BASE_IMAGE=" + service["Config"]["Image"],
     "--build-arg", "MODULE_ROOT=" + MANIFEST["module_root"], "--build-arg", "PATCH_COMMIT=" + MANIFEST["patch_commit"],
     "-t", MANIFEST["target_image"], str(ROOT)], timeout=120)
image = json.loads(run(["docker", "image", "inspect", MANIFEST["target_image"]]))[0]
baseline, candidate = probe(service["Image"], service), probe(image["Id"], service)
attempt = {"baseline": baseline, "candidate": candidate}
(ROOT / "comparison-attempt.json").write_text(json.dumps(attempt, indent=2) + "\n")
print(json.dumps(attempt), flush=True)
assert baseline["repository_commit"] == candidate["repository_commit"], "repository_scope_changed"
assert candidate["required_review_code_available"], "required_review_code_not_loaded"
for before, after in zip(baseline["cases"], candidate["cases"], strict=True):
    for key in ("job_id", "state", "model", "reasoning_effort", "source_observation_keys"):
        assert before[key] == after[key], "authority_changed_" + key
    assert after["prompt_characters"] < before["prompt_characters"] <= 88000
receipt = {"captured_at": datetime.now(UTC).isoformat(),
    "scope": "Actual production configuration and PostgreSQL; rolled-back preparation; zero model calls",
    "patch_commit": MANIFEST["patch_commit"], "base_image_id": service["Image"], "previous_container_id": service["Id"],
    "target_image": MANIFEST["target_image"], "target_image_id": image["Id"],
    "baseline": baseline, "candidate": candidate, "candidate_module_sha256": MANIFEST["candidate_module_sha256"],
    "compose_files": service["Config"]["Labels"]["com.docker.compose.project.config_files"].split(","),
    "project": service["Config"]["Labels"]["com.docker.compose.project"],
    "working_dir": service["Config"]["Labels"]["com.docker.compose.project.working_dir"],
    "interpretation": "Characters measure prepared input size, not actual token savings. Old cases remain terminal; no request replay."}
(ROOT / "qualification.json").write_text(json.dumps(receipt, indent=2) + "\n")
print(json.dumps(receipt))

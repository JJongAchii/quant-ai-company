"""Verify only existing code, receipts and output hashes on the approved worker."""
import hashlib
import json
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path

BASE=Path("/home/achii/quant-company-research/worker-state/jobs")
EXPECTED={"first":"1ac851a9-f5d0-55a3-9bf8-7d98193e42c8","failed":"69a52b87-3b37-56c1-9ac5-97c5819c4746"}
def sha(data):
    return hashlib.sha256(data).hexdigest()
records={}
for label,identity in EXPECTED.items():
    root=BASE/identity
    manifest=json.loads((root/"manifest.json").read_bytes())
    code=root/"workspace"/"code"
    head=subprocess.check_output(["git","-C",str(code),"rev-parse","HEAD"],text=True).strip()
    assert head==manifest["plan"]["code_commit"]
    status=subprocess.check_output(["git","-C",str(code),"status","--porcelain","--untracked-files=no"],text=True)
    assert not status
    files={}
    for name,digest in manifest["code_files"].items():
        actual=sha((code/name).read_bytes())
        assert actual==digest
        files[name]={"sha256":actual,"verified":True}
    row={"job_id":identity,"code_commit":head,"tracked_checkout_clean":True,
         "manifest_sha256":sha((root/"manifest.json").read_bytes()),"code_files":files}
    if label=="first":
        archive=root/"artifact.zip"
        archive_sha=sha(archive.read_bytes())
        assert archive_sha=="9c5c2a8eb504556ce3c2d42996cd6e0cb4ba005af0d76ed0b33761ee537b28cb"
        receipt_raw=(root/"receipt.json").read_bytes()
        receipt=json.loads(receipt_raw)
        assert receipt["code_commit"]==head and receipt["execution_count"]==1 and not receipt["sealed_read"]
        with zipfile.ZipFile(archive) as bundle:
            names=bundle.namelist()
            assert bundle.read("receipt.json")==receipt_raw
            assert bundle.read("manifest.json")==(root/"manifest.json").read_bytes()
            for name,digest in manifest["code_files"].items():
                assert sha(bundle.read("code/"+name))==digest
            result_raw=bundle.read("result.json")
            result=json.loads(result_raw)
            assert receipt["result_sha256"]==sha(result_raw)
            output_files={}
            for name,digest in result["output_files"].items():
                data=bundle.read("outputs/"+name)
                assert sha(data)==digest==receipt["output_files"][name]
                assert data==(root/"evaluate"/name).read_bytes()
                output_files[name]={"sha256":digest,"bytes":len(data),"verified":True}
            row.update(archive_sha256=archive_sha,archive_members=names,
                       receipt_sha256=sha(receipt_raw),result_sha256=sha(result_raw),
                       output_files=output_files,
                       archive_contains_event_or_target_trace=any(
                           any(word in name.lower() for word in ("trace","event","target"))
                           for name in names),
                       receipt={"qualification_passed":receipt["qualification_passed"],
                                "execution_count":receipt["execution_count"],
                                "sealed_read":receipt["sealed_read"],
                                "research_scope":receipt["research_scope"]})
    else:
        row["sandbox_qualification_sha256"]=sha((root/"sandbox-qualification.json").read_bytes())
        q=json.loads((root/"sandbox-qualification.json").read_bytes())
        row["qualification"]={k:q[k] for k in ("action","exit_code","timed_out","fixture_only")}
        row["economic_evaluation_receipt_present"]=(root/"sandbox-evaluation.json").exists()
    records[label]=row
print(json.dumps({"schema_version":1,"observed_at":datetime.now(UTC).isoformat(),
                  "read_only":True,"economic_evaluation_executed":False,
                  "performance_metrics_read_or_reported":False,"records":records},indent=2))


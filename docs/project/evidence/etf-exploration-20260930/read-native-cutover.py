"""Read only public phase/identity fields from the concurrent cutover journal."""

import json
from pathlib import Path

root = Path("/var/lib/quant-company/releases")
names = ["native-output-5c44ad07273adc780a56a47d7d35114fd0dc32c8-01.json",
         "native-output-5c44ad07273adc780a56a47d7d35114fd0dc32c8-02.json"]
public = []
allowed = {"phase", "state", "candidate", "candidate_commit", "company_commit", "source_commit", "previous",
           "selected_services", "started_at", "completed_at", "failed_at", "error", "backup", "backup_sha256",
           "worker", "worker_receipt", "steps", "images", "activation", "validation", "approval", "error_type"}
for name in names:
    path = root / name
    if not path.exists():
        continue
    value = json.loads(path.read_bytes())
    # Show field names first; nested journal content may contain private configs.
    item = {"name": name, "keys": sorted(value), "phase": value.get("phase")}
    for key in allowed.intersection(value):
        if isinstance(value[key], (str, int, float, bool)) or value[key] is None:
            item[key] = value[key]
    item["nested_fields"] = {key: sorted(value[key]) for key in allowed.intersection(value)
                             if isinstance(value[key], dict)}
    item["gates"] = {key: val for key, val in value.get("gates", {}).items()
                     if isinstance(val, bool)}
    public.append(item)
print(json.dumps(public, ensure_ascii=False))

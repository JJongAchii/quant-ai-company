"""Read back immutable staff evidence, actual model turns and science counts."""

import hashlib
import json
import subprocess
from pathlib import Path

PROGRAM = "e06537d3-fac3-5c8c-bf25-ddabb3c7e282"
REPORT = "raw-lineage-f8a0595989be.json"
REPORT_SHA = "f8a0595989be3f371938737c057a1ec29031e7a5615917e2172c5f944c398e3a"
SQL = f"""BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SELECT json_build_object('observed_at',now(),
 'program',(SELECT json_build_object('state',state,'digest',manifest_digest,'approval',approval_event_id)
   FROM research_programs WHERE id='{PROGRAM}'),
 'stages',(SELECT coalesce(json_agg(json_build_object('id',id,'stage',stage,'actor',actor,
   'state',state,'attempt',attempt,'error',error,'task_id',task_id,'updated_at',updated_at,'context',context)
   ORDER BY created_at,id),'[]'::json) FROM research_mission_stages
   WHERE program_id='{PROGRAM}' AND context::text LIKE '%{REPORT}%'),
 'reads',(SELECT coalesce(json_agg(json_build_object('stage_id',stage_id,'attempt',attempt,'path',path,
   'sha256',sha256,'character_offset',character_offset,'next_offset',next_offset,
   'content_chars',length(content::text)) ORDER BY created_at,id),'[]'::json) FROM research_stage_reads
   WHERE path LIKE '%{REPORT}%' AND stage_id IN
     (SELECT id FROM research_mission_stages WHERE program_id='{PROGRAM}')),
 'turns',(SELECT coalesce(json_agg(json_build_object('id',id,'task_id',task_id,'status',status,
   'error',error,'provider',response->>'provider','prompt_chars',length(request->>'prompt'),
   'updated_at',updated_at) ORDER BY created_at,id),'[]'::json) FROM turns
   WHERE task_id IN (SELECT task_id FROM research_mission_stages
     WHERE program_id='{PROGRAM}' AND context::text LIKE '%{REPORT}%')),
 'tasks',(SELECT coalesce(json_agg(json_build_object('id',id,'state',state,'mission_id',mission_id,
   'created_at',created_at,'data_assessment',data_assessment,'selection_decision',decision)
   ORDER BY created_at,id),'[]'::json) FROM research_program_tasks WHERE program_id='{PROGRAM}'),
 'mission_count',(SELECT count(*) FROM research_missions WHERE program_id='{PROGRAM}'),
 'reservation_count',(SELECT count(*) FROM research_program_reservations WHERE program_id='{PROGRAM}'),
 'scientific_trials',(SELECT count(*) FROM research_program_reservations
   WHERE program_id='{PROGRAM}' AND scientific_trial));
ROLLBACK;"""
result = subprocess.run(
    ["docker", "exec", "-i", "-u", "postgres", "quant-company-postgres-1", "psql",
     "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-d", "quant_company"],
    input=SQL, text=True, capture_output=True, check=True,
)
receipt = next(json.loads(line) for line in result.stdout.splitlines() if line.startswith("{"))
files = []
for stage in receipt["stages"]:
    context = stage.pop("context")
    stage["packet_index"] = context.get("data_evidence_packets")
    stage["required_data_reads"] = context.get("required_data_reads")
    for name, entry in context.get("_private_files", {}).items():
        if not name.endswith("/" + REPORT):
            continue
        path = Path("/var/lib/quant-company") / Path(entry["path"]).relative_to("/state")
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != entry["sha256"] or digest != REPORT_SHA:
            raise RuntimeError("immutable_staff_report_differs_from_provisioned_receipt")
        files.append({"stage_id": stage["id"], "path": name, "sha256": digest,
                      "size_bytes": len(data), "exact_provisioned_bytes": True})
receipt["immutable_report_files"] = files
receipt["report_sha256"] = REPORT_SHA
receipt["registry_sha256"] = hashlib.sha256(
    Path("/var/lib/quant-company/research/provisioned/data-evidence/registry.json").read_bytes()
).hexdigest()
receipt["staff_completed_report_reads"] = [
    {"stage_id": item["stage_id"], "attempt": item["attempt"], "path": item["path"]}
    for item in receipt["reads"] if item["next_offset"] is None and item["sha256"] == REPORT_SHA
]
print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))

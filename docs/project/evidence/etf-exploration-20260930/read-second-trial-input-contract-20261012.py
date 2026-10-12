"""Read selected obligations and frozen authority; no DB or runtime mutation."""
import json
import subprocess
from datetime import UTC, datetime

QUERY = """
BEGIN READ ONLY;
SET LOCAL statement_timeout='10s';
SELECT json_build_object(
 'mission',(SELECT json_build_object('id',id,'manifest_digest',manifest_digest,'state',state,'spec',spec)
  FROM research_missions WHERE id='2ce40574-6368-5cef-b706-b4e67441b3de'),
 'proposal',(SELECT json_build_object('id',id,'payload',payload,'digest',digest,'created_at',created_at)
  FROM research_mission_proposals WHERE id='fa1c5f89-7341-47a6-905d-9ae1853c4397'),
 'challenges',(SELECT json_agg(json_build_object('id',id,'payload',payload,'digest',digest,'created_at',created_at))
  FROM research_mission_challenges WHERE proposal_id='fa1c5f89-7341-47a6-905d-9ae1853c4397'),
 'responses',(SELECT json_agg(json_build_object('challenge_id',challenge_id,'payload',payload,'created_at',created_at))
  FROM research_challenge_responses WHERE proposal_id='fa1c5f89-7341-47a6-905d-9ae1853c4397'),
 'held_stage',(SELECT json_build_object('id',id,'state',state,'attempt',attempt,'error',error,
    'retry_at',retry_at,'hold',context->'_repair_hold','updated_at',updated_at)
  FROM research_mission_stages WHERE id='2d0ec857-8601-5cca-927d-f7f51877696d'));
COMMIT;
"""
raw=json.loads(subprocess.check_output([
 "docker","exec","quant-company-postgres-1","psql","-U","postgres","-d","quant_company",
 "-qAt","-v","ON_ERROR_STOP=1","-c",QUERY],text=True,timeout=30))
assert raw['mission']['manifest_digest']=='4d67e615bfdd78e686389735a66d0644259b771081cc4707019e4c7d972da737'
assert raw['proposal']['id']=='fa1c5f89-7341-47a6-905d-9ae1853c4397'
assert raw['held_stage']['error']=='repair_requires_evidence'
print(json.dumps({"schema_version":1,"observed_at":datetime.now(UTC).isoformat(),
 "database_mutated":False,"performance_metrics_selected":False,**raw},ensure_ascii=False,indent=2))

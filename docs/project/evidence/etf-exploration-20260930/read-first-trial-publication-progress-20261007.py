"""Read fixed trial audit/read/report/Slack receipt metadata, without performance values.

All SQL executes in a read-only transaction. No model, mutation or Slack send.
"""

import json
import subprocess
from datetime import UTC, datetime

QUERY = """BEGIN TRANSACTION READ ONLY;
SELECT json_build_object(
 'audit',(SELECT json_build_object('id',id,'state',state,'error',error,
   'root',context#>>'{_audit,root}',
   'validator_request_id',context#>>'{_audit,binding,validator_request_id}',
   'qlab_commit',context#>>'{_audit,qlab_commit}')
   FROM research_mission_stages WHERE id='5e5e2013-373c-5868-9fc9-242985e085b3'),
 'meaning',(SELECT row_to_json(x) FROM (
   SELECT s.id,s.state,s.error,s.attempt,s.updated_at,
     (SELECT coalesce(json_agg(row_to_json(t)),'[]'::json) FROM (
       SELECT id,sequence,status,error,response#>>'{decision,status}' AS decision_status,
         response#>>'{decision,tools,0,arguments,action}' AS tool_action,
         response#>>'{decision,tools,0,arguments,path}' AS tool_path,
         response#>>'{decision,tools,0,arguments,offset}' AS tool_offset,
         jsonb_array_length(response#>'{decision,artifacts}') AS artifact_count
       FROM turns WHERE task_id=s.task_id ORDER BY sequence DESC LIMIT 3)t) AS recent_turns,
     (SELECT count(*) FROM research_stage_reads r WHERE r.stage_id=s.id AND r.attempt=s.attempt) AS read_chunks,
     (SELECT coalesce(sum(length(content)),0) FROM research_stage_reads r WHERE r.stage_id=s.id AND r.attempt=s.attempt) AS read_characters,
     (SELECT coalesce(json_agg(row_to_json(b)),'[]'::json) FROM (
       SELECT r.path,count(*) AS chunks,sum(length(content)) AS characters,
         bool_or(next_offset IS NULL) AS complete,max(character_offset) AS latest_character_offset
       FROM research_stage_reads r WHERE r.stage_id=s.id AND r.attempt=s.attempt GROUP BY r.path ORDER BY r.path)b) AS reads_by_path,
     (SELECT coalesce(json_agg(row_to_json(f)),'[]'::json) FROM (
       SELECT path,
         EXISTS(SELECT 1 FROM research_stage_reads r WHERE r.stage_id=s.id AND r.attempt=s.attempt
           AND r.path=paths.path AND r.next_offset IS NULL) AS complete,
         (SELECT max(r.character_offset) FROM research_stage_reads r WHERE r.stage_id=s.id
           AND r.attempt=s.attempt AND r.path=paths.path) AS latest_character_offset
       FROM jsonb_array_elements_text(s.context->'required_meaning_reads') AS paths(path))f) AS files
   FROM research_mission_stages s WHERE s.id='8987ff99-b738-5927-8ba2-0545cdab6884')x),
 'meaning_record',(SELECT json_build_object('trial_id',trial_id,'digest',digest,'created_at',created_at,
   'conclusion',payload->>'conclusion') FROM research_meaning_reviews
   WHERE trial_id='de3597b0-e358-5a58-af5e-9f3c79df53e9'),
 'job',(SELECT json_build_object('id',id,'state',state,'error',error,
   'director_task_id',report->>'director_task_id','source_id',report->>'source_id')
   FROM research_jobs WHERE id='1ac851a9-f5d0-55a3-9bf8-7d98193e42c8'),
 'source',(SELECT json_build_object('id',id,'uri',uri,'approved',approved,'synthetic',synthetic,
   'html_sha256',content::jsonb#>>'{report,html_sha256}',
   'archive_sha256',content::jsonb#>>'{report,report_archive_sha256}')
   FROM sources WHERE id='mission-report:de3597b0-e358-5a58-af5e-9f3c79df53e9'),
 'director_task',(SELECT json_build_object('id',k.id,'status',k.status,'error',k.error,
   'priority',k.priority,'turn_count',k.turn_count,
   'turns',(SELECT coalesce(json_agg(row_to_json(t)),'[]'::json) FROM (
     SELECT id,sequence,status,error,updated_at FROM turns WHERE task_id=k.id ORDER BY sequence DESC LIMIT 3)t))
   FROM tasks k JOIN research_jobs j ON k.id::text=j.report->>'director_task_id'
   WHERE j.id='1ac851a9-f5d0-55a3-9bf8-7d98193e42c8'),
 'slack',(SELECT coalesce(json_agg(row_to_json(o)),'[]'::json) FROM (
   SELECT o.id,o.status,o.attempts,o.sent_ts,o.error,o.channel,o.thread_ts,o.created_at,
     length(o.text) AS characters,md5(o.text) AS text_md5,
     strpos(o.text,(SELECT content::jsonb#>>'{report,view_url}' FROM sources
       WHERE id='mission-report:de3597b0-e358-5a58-af5e-9f3c79df53e9'))>0 AS exact_report_link_present,
     strpos(o.text,(SELECT '<@' || owner_user || '>' FROM projects
       WHERE id='9aac0de4-2b97-5195-a720-287d324234f3'))>0 AS owner_tag_present
   FROM outbox o JOIN messages m ON m.id=o.id JOIN research_jobs j ON m.task_id::text=j.report->>'director_task_id'
   WHERE j.id='1ac851a9-f5d0-55a3-9bf8-7d98193e42c8' ORDER BY o.created_at)o));
ROLLBACK;"""

raw = subprocess.check_output([
    "docker", "exec", "quant-company-postgres-1", "psql", "-U", "postgres", "-d", "quant_company",
    "-qAt", "-v", "ON_ERROR_STOP=1", "-c", QUERY,
], text=True, timeout=30)
value = json.loads(raw)
audit = value["audit"]
if audit and audit.get("root") and audit.get("validator_request_id"):
    code = """import hashlib,json,sys
from pathlib import Path
entry=json.loads(sys.stdin.read())
root=Path(entry['root'])
path=root/'audits'/(entry['validator_request_id']+'.verification.json')
if not path.is_file():
    print(json.dumps({'present':False}))
else:
    content=path.read_bytes()
    record=json.loads(content)
    files=record.get('audit_files',{})
    hashes={name:hashlib.sha256((root/name).read_bytes()).hexdigest()==digest
        for name,digest in files.items()}
    print(json.dumps({'present':True,'path':str(path),'sha256':hashlib.sha256(content).hexdigest(),
        'verdict':record.get('verdict'),'scope_digest':record.get('scope_digest'),
        'objective_digest':record.get('objective_digest'),'qlab_commit':record.get('qlab_commit'),
        'violation_count':len(record.get('violations',[])),
        'receipt_violation_count':len(record.get('receipt_violations',[])),
        'audit_file_sha_matches':hashes}))
"""
    result = subprocess.check_output([
        "docker", "exec", "-i", "quant-company-api-1", "python", "-c", code,
    ], input=json.dumps(audit), text=True, timeout=30)
    value["audit_verification"] = json.loads(result)
print(json.dumps({"observed_at": datetime.now(UTC).isoformat(), **value}, indent=2, sort_keys=True))

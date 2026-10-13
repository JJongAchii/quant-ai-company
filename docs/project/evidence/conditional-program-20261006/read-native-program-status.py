"""Read current genuine owner authority, independent employee stages and cumulative usage."""

import hashlib
import json
import sys
from datetime import UTC, datetime

from quant_company.company import Company, as_json, fingerprint
from quant_company.config import Settings
from quant_company.research.program_contracts import ResearchProgram

company = Company(Settings())
assert company.settings.company_code_commit == '5c44ad07273adc780a56a47d7d35114fd0dc32c8'
spec = ResearchProgram.model_validate_json(sys.argv[1])
digest = fingerprint(spec.model_dump(mode='json'))
assert digest == 'c3ba5268d71c87bd2c6226160bc720498137bd291396a054970a1f1e7ebcf2db'
project_id = '9aac0de4-2b97-5195-a720-287d324234f3'
with company.db.transaction() as conn:
    conn.execute('SET TRANSACTION READ ONLY')
    programs = conn.execute("""SELECT id,state,manifest_digest,approval_event_id,approved_at,updated_at
        FROM research_programs WHERE project_id=%s ORDER BY created_at,id""", (project_id,)).fetchall()
    row = next(p for p in programs if p['manifest_digest'] == digest)
    bindings = conn.execute("""SELECT b.id,b.message_id,b.revision,b.manifest_digest,o.status,o.sent_ts,o.attempts
        FROM research_approval_bindings b JOIN outbox o ON o.id=b.message_id
        WHERE b.target_kind='program' AND b.target_id=%s ORDER BY b.created_at,b.id""", (row['id'],)).fetchall()
    notice = conn.execute('SELECT text FROM messages WHERE id=%s', (bindings[-1]['message_id'],)).fetchone()['text']
    notice_spec = ResearchProgram.model_validate_json(notice[notice.index('{'):])
    assert fingerprint(notice_spec.model_dump(mode='json')) == digest
    notice_proof = {'message_sha256': hashlib.sha256(notice.encode()).hexdigest(),
        'exact_canonical_spec_verified': True,
        'slack_display_boundaries': [notice[offset-35:offset+35] for offset in range(2900, len(notice), 2900)]}
    owner_events = conn.execute("""SELECT e.kind,e.detail->>'program_id' AS program_id,
        e.detail->>'manifest_digest' AS manifest_digest,e.detail->>'owner_event_id' AS owner_event_id,e.created_at,
        i.event_key IS NOT NULL AS actual_inbound_exists FROM events e LEFT JOIN inbound i
        ON i.event_key=e.detail->>'owner_event_id' AND i.project_id=e.project_id
        WHERE e.project_id=%s AND e.kind IN ('research_program_approve','research_program_cancel')
        ORDER BY e.created_at""", (project_id,)).fetchall()
    tasks = conn.execute("""SELECT id,program_id,state,digest,proposal,data_assessment,decision,mission_id,created_at
        FROM research_program_tasks WHERE program_id=%s ORDER BY created_at,id""", (row['id'],)).fetchall()
    stages = conn.execute("""SELECT s.id,s.stage,s.state,s.error,s.retry_at,s.task_id,t.agent,t.status AS task_state,
        t.error AS task_error,s.context->'_program_hold' AS technical_hold,
        s.context->'_data_output_failures' AS native_output_failures,s.created_at,s.updated_at FROM research_mission_stages s JOIN tasks t ON t.id=s.task_id
        WHERE s.program_id=%s ORDER BY s.created_at,s.id""", (row['id'],)).fetchall()
    progress = conn.execute("""SELECT s.id AS stage_id,a.attempt,t.sequence,t.status,
        t.request->>'model' AS requested_model,t.request->>'output_contract' AS output_contract,
        t.response->>'provider' AS response_provider,t.updated_at,
        strpos(t.request->>'prompt','"response_serialization":')>0 AS format_guidance_delivered,
        strpos(t.request->>'prompt','"data_artifact_serialization":')>0 AS schema_feedback_delivered
        FROM research_mission_stages s JOIN research_stage_attempts a ON a.stage_id=s.id
        JOIN turns t ON t.task_id=a.task_id WHERE s.program_id=%s
        ORDER BY s.created_at,s.id,a.attempt,t.sequence""", (row['id'],)).fetchall()
    reads = conn.execute("""SELECT r.stage_id,r.attempt,r.path,r.character_offset,r.next_offset,r.sha256,
        length(r.content) AS characters,r.created_at FROM research_stage_reads r
        JOIN research_mission_stages s ON s.id=r.stage_id WHERE s.program_id=%s
        ORDER BY r.created_at,r.id""", (row['id'],)).fetchall()
    artifacts = conn.execute("""SELECT s.id AS stage_id,s.stage,s.state,s.attempt,s.result,
        a.task_id,a.response->>'provider' AS response_provider,
        a.response->>'request_id' AS response_request_id,
        a.response->'decision'->'artifacts' AS response_artifacts,a.error AS attempt_error
        FROM research_mission_stages s JOIN research_stage_attempts a
        ON a.stage_id=s.id AND a.attempt=s.attempt WHERE s.program_id=%s
        ORDER BY s.created_at,s.id""", (row['id'],)).fetchall()
    turns = conn.execute("""SELECT u.id,u.status,u.error,u.attempts,u.updated_at,k.id AS task_id,k.agent,k.kind
        FROM turns u JOIN tasks k ON k.id=u.task_id WHERE k.project_id=%s AND u.status<>'completed'
        ORDER BY u.updated_at DESC LIMIT 20""", (project_id,)).fetchall()
    trials = conn.execute("""SELECT t.id,t.state,t.plan_digest,j.state AS job_state FROM research_mission_trials t
        JOIN research_missions m ON m.id=t.mission_id LEFT JOIN research_jobs j ON j.id=t.job_id
        WHERE m.program_id=%s ORDER BY t.created_at,t.id""", (row['id'],)).fetchall()
    lineages = conn.execute('SELECT id,trial_limit FROM research_scientific_lineages WHERE project_id=%s', (project_id,)).fetchall()
    origins = conn.execute('SELECT task_id,lineage_id,task_digest FROM research_scientific_lineage_origins WHERE lineage_id=%s ORDER BY task_id',
        (spec.envelopes[0].template.scientific_lineage.id,)).fetchall()
    activity = conn.execute("""SELECT (SELECT count(*) FROM research_jobs WHERE state IN
        ('queued','claimed','running','uncertain','cancel_requested')) AS active_jobs,
        (SELECT count(*) FROM research_program_reservations WHERE scientific_trial) AS scientific_trials,
        (SELECT count(*) FROM model_account_calls WHERE state IN ('dispatching','uncertain')) AS unresolved_model_calls,
        (SELECT count(*) FROM outbox WHERE status='sending') AS sending_outbox""").fetchone()
    pause = conn.execute('SELECT paused_until,reason FROM runtime_control WHERE id=1').fetchone()
    worker = conn.execute("SELECT id,last_seen,last_seen>now()-interval '60 seconds' AS fresh FROM research_workers WHERE id='worker'").fetchone()
    feedback = conn.execute("""SELECT s.id AS stage_id,s.task_id,s.state,p.value->>'id' AS prior_task_id,
        p.value->'decision'->>'decision' AS prior_decision,t.id AS first_turn_id,t.status AS first_turn_state,
        t.response->>'provider' AS first_response_provider,
        strpos(t.request->>'prompt',concat('prior-tasks/',p.value->>'id','.json'))>0 AS evidence_navigation_delivered,
        s.context->'_private_files'->concat('prior-tasks/',p.value->>'id','.json')->>'sha256' AS feedback_file_sha256
        FROM research_mission_stages s CROSS JOIN LATERAL jsonb_array_elements(s.context->'prior_tasks') AS p(value)
        JOIN turns t ON t.task_id=s.task_id AND t.sequence=1 WHERE s.program_id=%s
        AND s.stage='program_proposal' AND p.value->'decision'->>'decision'='revise'
        ORDER BY s.created_at,s.id""", (row['id'],)).fetchall()
print(json.dumps(as_json({'schema_version': 1, 'observed_at': datetime.now(UTC),
    'program_digest': digest, 'programs': programs, 'approval_bindings': bindings,
    'canonical_approval_notice': notice_proof, 'actual_owner_events': owner_events,
    'actual_tasks': tasks, 'actual_stages': stages, 'stage_progress': progress, 'stage_reads': reads,
    'pending_turns': turns, 'actual_trials': trials, 'stage_artifacts': artifacts,
    'followup_feedback': feedback, 'worker_authenticated_polling': worker,
    'scientific_lineages': lineages, 'scientific_origins': origins, 'activity': activity, 'runtime_pause': pause,
    'owner_approval_fabricated': False, 'staff_decisions_fabricated': False}), ensure_ascii=False))

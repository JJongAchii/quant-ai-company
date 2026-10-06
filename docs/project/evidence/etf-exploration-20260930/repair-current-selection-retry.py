"""Reconcile only navigation/urgency for the observed selection retry two."""

import json
import pathlib
import subprocess

CODE = r'''
import json
from datetime import UTC,datetime
from psycopg.types.json import Jsonb
from quant_company.company import Company
from quant_company.config import Settings
company=Company(Settings())
operation='first-mission-selection-contract-navigation:6e125dec:attempt2:20261006'
with company.db.transaction() as conn:
    project=company._project(conn,'9aac0de4-2b97-5195-a720-287d324234f3')
    assert project['revision']==5 and project['status']=='active'
    program=conn.execute('SELECT * FROM research_programs WHERE id=%s FOR UPDATE',
        ('f7deaf96-e677-5afe-93d4-18ac387043bb',)).fetchone()
    assert program['state']=='active' and program['manifest_digest']=='c3ba5268d71c87bd2c6226160bc720498137bd291396a054970a1f1e7ebcf2db'
    previous=conn.execute("SELECT id,detail FROM events WHERE project_id=%s AND kind=%s "
        "AND detail->>'operation_id'=%s ORDER BY id LIMIT 1",
        (project['id'],'research_selection_retry_navigation_reconciled',operation)).fetchone()
    if previous:
        result={**previous['detail'],'event_id':previous['id'],'already_committed':True}
    else:
        mission=conn.execute('SELECT * FROM research_missions WHERE id=%s FOR UPDATE',
            ('2ce40574-6368-5cef-b706-b4e67441b3de',)).fetchone()
        assert mission['program_id']==program['id'] and mission['state']=='active' and mission['revision']==5
        assert mission['approval_event_id']==program['approval_event_id']
        assert not conn.execute('SELECT id FROM research_jobs WHERE mission_id=%s LIMIT 1',(mission['id'],)).fetchone()
        latest=conn.execute('SELECT id FROM research_mission_stages WHERE mission_id=%s '
            'ORDER BY created_at DESC,id DESC LIMIT 1',(mission['id'],)).fetchone()
        assert str(latest['id'])=='6e125dec-1624-5602-a3e1-28f47366d3d6'
        stage=conn.execute('SELECT * FROM research_mission_stages WHERE id=%s FOR UPDATE',(latest['id'],)).fetchone()
        assert stage['state']=='running' and stage['attempt']==2 and stage['actor']=='director' and stage['stage']=='selection'
        assert str(stage['task_id'])=='07d30ebc-b00f-5aaf-8c20-cff002fd72d3'
        assert 'source_completion_navigation' not in stage['context']
        prior=conn.execute('SELECT error FROM research_stage_attempts WHERE stage_id=%s AND attempt=1',(stage['id'],)).fetchone()
        assert prior['error']=='stage_contract_rejected: Every independent challenge needs exactly one disposition'
        task=conn.execute('SELECT * FROM tasks WHERE id=%s FOR UPDATE',(stage['task_id'],)).fetchone()
        assert task['kind']=='research_stage' and task['status'] in {'pending','running'}
        assert task['revision']==5 and task['project_id']==project['id'] and task['agent']=='director' and task['priority'] in {0,100}
        proposal='a3e207ea-d615-4b40-96c0-1b92d1ae5124'
        assert stage['context']['mission']['stage']['proposal_id']==proposal
        challenges=conn.execute('SELECT id FROM research_mission_challenges WHERE mission_id=%s AND proposal_id=%s ORDER BY created_at,id',
            (mission['id'],proposal)).fetchall()
        ids=[str(c['id']) for c in challenges]
        assert ids==['581631b4-9d2d-4fe9-864a-75a9a27eec10']
        navigation={
            'operation_id':operation,'current_proposal_id':proposal,'current_challenge_ids':ids,
            'prior_operational_failure':prior['error'],
            'selection_contract':
                'Return the supplied ReviewDecision schema. Its responses list must contain exactly one entry for each '
                'current_challenge_ids UUID above, with no duplicate or historical challenge IDs. Read the actual current '
                'challenge evidence file. Determine disposition, rationale, sources and test plan independently. '
                'This is identification and existing format guidance; it supplies no execute/revise decision.',
            'existing_service_requirement':
                'Completely read every cited original evidence_sources file and every required_lineage_reads file in '
                'this attempt before the final artifact. Absent file_progress means unread; non-null next_offset means '
                'incomplete; null means complete. Follow exact next_offset without drafting an artifact between chunks. '
                'Earlier attempt read receipts do not count in this provider thread. Choose needed sources independently.',
        }
        previous_priority=task['priority']
        conn.execute('UPDATE tasks SET priority=0 WHERE id=%s',(task['id'],))
        conn.execute('UPDATE research_mission_stages SET context=%s,updated_at=now() WHERE id=%s',
            (Jsonb({**stage['context'],'source_completion_navigation':navigation}),stage['id']))
        result={'schema_version':1,'operation_id':operation,'state':'selection_retry_navigation_reconciled',
            'observed_at':datetime.now(UTC).isoformat(),'program_id':str(program['id']),'program_digest':program['manifest_digest'],
            'mission_id':str(mission['id']),'stage_id':str(stage['id']),'task_id':str(task['id']),'attempt':2,
            'previous_priority':previous_priority,'priority':0,'navigation':navigation,
            'source_bytes_or_read_receipts_changed':False,'frozen_model_requests_changed':False,
            'scientific_judgment_or_authority_or_budget_changed':False,'signed_resource_priority_changed':False,
            'new_call_or_scientific_job_enqueued':False,'exact_sha_human_approval_claimed':False}
        event=conn.execute('INSERT INTO events(project_id,kind,detail) VALUES(%s,%s,%s) RETURNING id',
            (project['id'],'research_selection_retry_navigation_reconciled',Jsonb(result))).fetchone()
        result.update(event_id=event['id'],already_committed=False)
print(json.dumps(result))
'''

value = subprocess.run([
    'docker', 'exec', '-i', 'quant-company-api-1', 'python', '/app/entrypoint.py', 'python', '-',
], input=CODE, capture_output=True, text=True, timeout=60)
if value.returncode:
    private = pathlib.Path('/var/lib/quant-company/releases/first-mission-selection-retry.private-error.log')
    private.write_text(value.stderr)
    private.chmod(0o600)
    raise RuntimeError('selection_retry_navigation_failed_check_private_host_receipt')
print(json.dumps(json.loads(value.stdout), indent=2))

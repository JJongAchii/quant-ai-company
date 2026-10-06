"""Reviewable operator for an observed next preparation stage of the first mission.

Only existing task urgency and factual completion navigation can change. This
operator refuses once a scientific job exists and never enqueues any work.
"""

import json
import pathlib
import subprocess
import sys
from uuid import UUID

stage_id, task_id = (str(UUID(value)) for value in sys.argv[1:3])
stage_type = sys.argv[3]
assert stage_type in {'proposal', 'challenge', 'selection', 'implementation'}

CODE = r'''
import json,sys
from datetime import UTC,datetime
from uuid import UUID
from psycopg.types.json import Jsonb
from quant_company.company import Company
from quant_company.config import Settings
stage_id,task_id=(str(UUID(value)) for value in sys.argv[1:3])
stage_type=sys.argv[3]
actors={'proposal':'researcher_kr','challenge':'financial_strategist','selection':'director','implementation':'engineer'}
actor=actors[stage_type]
company=Company(Settings())
operation='first-mission-preparation-navigation:'+stage_id+':20261006'
with company.db.transaction() as conn:
    project=company._project(conn,'9aac0de4-2b97-5195-a720-287d324234f3')
    assert project['revision']==5 and project['status']=='active'
    program=conn.execute('SELECT * FROM research_programs WHERE id=%s FOR UPDATE',
        ('f7deaf96-e677-5afe-93d4-18ac387043bb',)).fetchone()
    assert program['state']=='active' and program['manifest_digest']=='c3ba5268d71c87bd2c6226160bc720498137bd291396a054970a1f1e7ebcf2db'
    previous=conn.execute("SELECT id,detail FROM events WHERE project_id=%s AND kind=%s "
        "AND detail->>'operation_id'=%s ORDER BY id LIMIT 1",
        (project['id'],'research_first_stage_preparation_reconciled',operation)).fetchone()
    if previous:
        result={**previous['detail'],'event_id':previous['id'],'already_committed':True}
    else:
        mission=conn.execute('SELECT * FROM research_missions WHERE id=%s FOR UPDATE',
            ('2ce40574-6368-5cef-b706-b4e67441b3de',)).fetchone()
        assert mission['program_id']==program['id'] and mission['state']=='active' and mission['revision']==5
        assert mission['approval_event_id']==program['approval_event_id']
        assert not conn.execute('SELECT id FROM research_jobs WHERE mission_id=%s LIMIT 1',
            (mission['id'],)).fetchone()
        latest=conn.execute('SELECT id FROM research_mission_stages WHERE mission_id=%s '
            'ORDER BY created_at DESC,id DESC LIMIT 1',(mission['id'],)).fetchone()
        assert str(latest['id'])==stage_id
        stage=conn.execute('SELECT * FROM research_mission_stages WHERE id=%s FOR UPDATE',
            (stage_id,)).fetchone()
        assert stage['mission_id']==mission['id'] and stage['stage']==stage_type
        assert stage['state']=='running' and stage['attempt']==1 and stage['actor']==actor
        assert str(stage['task_id'])==task_id and 'source_completion_navigation' not in stage['context']
        task=conn.execute('SELECT * FROM tasks WHERE id=%s FOR UPDATE',(task_id,)).fetchone()
        assert task['kind']=='research_stage' and task['status'] in {'pending','running'}
        assert task['revision']==5 and task['project_id']==project['id'] and task['agent']==actor
        assert task['priority'] in {0,100}
        navigation={
            'operation_id':operation,
            'existing_service_sequence':
                'Proposal, challenge and selection can read immutable evidence; they have no code execution tool. '
                'An execute selection enables engineer implementation, not immediate development evaluation. '
                'The trusted backend builds that implementation and the worker must pass profile qualification '
                'before evaluating development inputs. ReviewDecision responses with disposition test carry '
                'prospective executable test_plan obligations; these are not completed test evidence and are '
                'not automatically discharged. A required revise disposition prevents implementation. '
                'Distinguish actual static source evidence from planned implementation checks and never claim '
                'unperformed checks passed. This describes existing capabilities and supplies no disposition '
                'or execute/revise recommendation; scientific judgment remains yours.',
            'existing_service_requirement':
                'Every source_id referenced in a final artifact, including selection.responses source_ids, '
                'requires a complete read of its corresponding evidence_sources file in this attempt. '
                'In file_progress, absent means unread; non-null next_offset means incomplete; null means '
                'complete. Also complete each required_lineage_reads file.',
            'preparation_navigation':
                'Choose sources using your own scientific judgment within the approved library. Read '
                'each intended cited original to its end using the exact next_offset before drafting '
                'the final artifact. A single successful chunk does not finish a longer source. '
                'Do not regenerate a complete artifact between remaining source chunks. Read only '
                'the evidence needed and the explicitly required files. This explains existing service '
                'gates and supplies no hypothesis, objection, selection, patch or scientific judgment. '
                'For a revised proposal, read relevant_evidence.rejections and the actual independent '
                'criticism before drafting; respond to that recorded decision within the selected study.',
        }
        if stage_type=='selection':
            proposal=str(UUID(stage['context']['mission']['stage']['proposal_id']))
            challenges=conn.execute('SELECT id FROM research_mission_challenges WHERE mission_id=%s '
                'AND proposal_id=%s ORDER BY created_at,id',(mission['id'],proposal)).fetchall()
            navigation['current_proposal_id']=proposal
            navigation['current_challenge_ids']=[str(c['id']) for c in challenges]
            navigation['selection_response_identity_rule']='Exactly one responses entry per current_challenge_ids UUID; no historical or duplicate IDs.'
        previous_priority=task['priority']
        context={**stage['context'],'source_completion_navigation':navigation}
        conn.execute('UPDATE tasks SET priority=0 WHERE id=%s',(task_id,))
        conn.execute('UPDATE research_mission_stages SET context=%s,updated_at=now() WHERE id=%s',
            (Jsonb(context),stage_id))
        result={'schema_version':1,'operation_id':operation,'state':'current_stage_preparation_reconciled',
            'observed_at':datetime.now(UTC).isoformat(),'program_id':str(program['id']),
            'program_digest':program['manifest_digest'],'mission_id':str(mission['id']),
            'stage_id':stage_id,'task_id':task_id,'stage':stage_type,'actor':actor,'attempt':1,
            'previous_priority':previous_priority,'priority':0,'navigation':navigation,
            'source_bytes_or_read_receipts_changed':False,'frozen_model_requests_changed':False,
            'scientific_judgment_applied':False,'scientific_authority_or_budget_changed':False,
            'signed_resource_priority_changed':False,'new_model_call_or_scientific_job_enqueued':False,
            'authority':'Existing owner urgent first-experiment and continuation requests; root-reviewed concrete current-stage operation in PR105'}
        event=conn.execute('INSERT INTO events(project_id,kind,detail) VALUES(%s,%s,%s) RETURNING id',
            (project['id'],'research_first_stage_preparation_reconciled',Jsonb(result))).fetchone()
        result.update(event_id=event['id'],already_committed=False)
print(json.dumps(result))
'''

value = subprocess.run([
    'docker', 'exec', '-i', 'quant-company-api-1', 'python', '/app/entrypoint.py',
    'python', '-', stage_id, task_id, stage_type,
], input=CODE, capture_output=True, text=True, timeout=60)
if value.returncode:
    private = pathlib.Path('/var/lib/quant-company/releases/first-mission-stage-preparation.private-error.log')
    private.write_text(value.stderr)
    private.chmod(0o600)
    raise RuntimeError('current_stage_preparation_failed_check_private_host_receipt')
print(json.dumps(json.loads(value.stdout), indent=2))

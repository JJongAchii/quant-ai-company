"""Expose existing source-completion requirements to one current preparation task.

This is a bounded operator metadata repair. It changes no evidence, read receipt,
model request, proposal, selection, scientific authority or execution record.
"""

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
operation='first-mission-source-completion-navigation:c3050fd0:20261006'
with company.db.transaction() as conn:
    project=company._project(conn,'9aac0de4-2b97-5195-a720-287d324234f3')
    assert project['revision']==5 and project['status']=='active'
    program=conn.execute('SELECT * FROM research_programs WHERE id=%s FOR UPDATE',
        ('f7deaf96-e677-5afe-93d4-18ac387043bb',)).fetchone()
    assert program['state']=='active' and program['manifest_digest']=='c3ba5268d71c87bd2c6226160bc720498137bd291396a054970a1f1e7ebcf2db'
    previous=conn.execute("SELECT id,detail FROM events WHERE project_id=%s AND kind=%s "
        "AND detail->>'operation_id'=%s ORDER BY id LIMIT 1",
        (project['id'],'research_source_navigation_reconciled',operation)).fetchone()
    if previous:
        result={**previous['detail'],'event_id':previous['id'],'already_committed':True}
    else:
        mission=conn.execute('SELECT * FROM research_missions WHERE id=%s FOR UPDATE',
            ('2ce40574-6368-5cef-b706-b4e67441b3de',)).fetchone()
        assert mission['program_id']==program['id'] and mission['state']=='active' and mission['revision']==5
        assert mission['approval_event_id']==program['approval_event_id']
        stage=conn.execute('SELECT * FROM research_mission_stages WHERE id=%s FOR UPDATE',
            ('c3050fd0-0139-53ec-8669-56dff53ab630',)).fetchone()
        assert stage['mission_id']==mission['id'] and stage['stage']=='proposal'
        assert stage['state']=='running' and stage['attempt']==1
        assert str(stage['task_id'])=='d7df0261-af8f-5c89-b8ef-a59e3bb9b6f1' and stage['actor']=='researcher_kr'
        task=conn.execute('SELECT * FROM tasks WHERE id=%s FOR UPDATE',(stage['task_id'],)).fetchone()
        assert task['kind']=='research_stage' and task['status']=='running' and task['revision']==5
        assert task['project_id']==project['id'] and task['agent']==stage['actor']
        rejected=conn.execute('SELECT response FROM turns WHERE id=%s AND task_id=%s AND status=%s',
            ('8ffc746c-d213-5fae-af1b-25d463107376',task['id'],'completed')).fetchone()
        decision=rejected['response']['decision']
        assert decision['status']=='complete' and len(decision['artifacts'])==1 and not decision['tools']
        source_ids=json.loads(decision['artifacts'][0]['content'])['source_ids']
        mappings={item['source_id']:item['file'] for item in stage['context']['evidence_sources']}
        assert source_ids and all(source in mappings for source in source_ids)
        incomplete=[]
        for source in source_ids:
            path=mappings[source]
            progress=conn.execute('SELECT next_offset FROM research_stage_reads WHERE stage_id=%s '
                'AND attempt=%s AND path=%s ORDER BY character_offset DESC LIMIT 1',
                (stage['id'],stage['attempt'],path)).fetchone()
            if progress is None or progress['next_offset'] is not None:
                incomplete.append({'source_id':source,'file':path})
        assert incomplete and 'source_completion_navigation' not in stage['context']
        navigation={
            'operation_id':operation,
            'existing_service_requirement':
                'Every source_id cited by a final artifact requires a complete read of its corresponding '
                'evidence_sources file in this attempt. In file_progress, absent means unread; non-null '
                'next_offset means incomplete; null means complete.',
            'current_recovery':
                'The preceding complete artifacts were not accepted because a cited original was incomplete. '
                'Read each intended cited original to its end using the exact next_offset before drafting '
                'another complete artifact. A single successful chunk does not finish a longer source. '
                'Do not regenerate the full artifact between remaining chunks. This requirement changes '
                'no scientific scope and supplies no hypothesis or decision.',
            'incomplete_previously_cited_sources':incomplete,
        }
        context={**stage['context'],'source_completion_navigation':navigation}
        conn.execute('UPDATE research_mission_stages SET context=%s,updated_at=now() WHERE id=%s',
            (Jsonb(context),stage['id']))
        result={'schema_version':1,'operation_id':operation,'state':'source_completion_navigation_visible',
            'observed_at':datetime.now(UTC).isoformat(),'program_id':str(program['id']),
            'program_digest':program['manifest_digest'],'mission_id':str(mission['id']),
            'stage_id':str(stage['id']),'task_id':str(task['id']),'attempt':stage['attempt'],
            'navigation':navigation,'only_current_stage_navigation_changed':True,
            'source_bytes_or_read_receipts_changed':False,'frozen_model_requests_changed':False,
            'proposal_selection_or_completion_applied':False,'scientific_authority_or_budget_changed':False,
            'new_model_call_or_scientific_job_enqueued':False,
            'authority':'Existing owner urgent first-experiment and continuation instructions; root-reviewed PR105 metadata repair'}
        event=conn.execute('INSERT INTO events(project_id,kind,detail) VALUES(%s,%s,%s) RETURNING id',
            (project['id'],'research_source_navigation_reconciled',Jsonb(result))).fetchone()
        result.update(event_id=event['id'],already_committed=False)
print(json.dumps(result))
'''

value = subprocess.run([
    "docker", "exec", "-i", "quant-company-api-1", "python", "/app/entrypoint.py", "python", "-",
], input=CODE, capture_output=True, text=True, timeout=60)
if value.returncode:
    private = pathlib.Path('/var/lib/quant-company/releases/first-mission-navigation.private-error.log')
    private.write_text(value.stderr)
    private.chmod(0o600)
    raise RuntimeError('source_navigation_repair_failed_check_private_host_receipt')
print(json.dumps(json.loads(value.stdout), indent=2))

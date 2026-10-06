"""Apply the reviewed current planning-task urgency without changing science."""

import json
import pathlib
import subprocess

code = r'''
import json
from psycopg.types.json import Jsonb
from quant_company.company import Company
from quant_company.config import Settings
company=Company(Settings())
operation='research-current-mission-preparation-urgency:c3050fd0:20261006'
with company.db.transaction() as conn:
    project=company._project(conn,'9aac0de4-2b97-5195-a720-287d324234f3')
    assert project['revision']==5 and project['status']=='active'
    program=conn.execute('SELECT * FROM research_programs WHERE id=%s FOR UPDATE',
        ('f7deaf96-e677-5afe-93d4-18ac387043bb',)).fetchone()
    assert program['state']=='active' and program['manifest_digest']=='c3ba5268d71c87bd2c6226160bc720498137bd291396a054970a1f1e7ebcf2db'
    previous=conn.execute("SELECT id,detail FROM events WHERE project_id=%s AND kind=%s "
        "AND detail->>'operation_id'=%s ORDER BY id LIMIT 1",
        (project['id'],'research_preparation_priority_reconciled',operation)).fetchone()
    if previous:
        result={**previous['detail'],'event_id':previous['id'],'already_committed':True}
    else:
        task=conn.execute('SELECT * FROM tasks WHERE id=%s FOR UPDATE',
            ('d7df0261-af8f-5c89-b8ef-a59e3bb9b6f1',)).fetchone()
        stage=conn.execute('SELECT * FROM research_mission_stages WHERE id=%s FOR UPDATE',
            ('c3050fd0-0139-53ec-8669-56dff53ab630',)).fetchone()
        assert task['project_id']==project['id'] and task['revision']==5 and task['kind']=='research_stage'
        assert task['agent']=='researcher_kr' and task['priority']==100 and task['status'] in {'pending','running'}
        mission=conn.execute('SELECT * FROM research_missions WHERE id=%s FOR UPDATE', ('2ce40574-6368-5cef-b706-b4e67441b3de',)).fetchone()
        assert mission['program_id']==program['id'] and mission['state']=='active' and mission['revision']==5
        assert mission['approval_event_id']==program['approval_event_id']
        assert stage['mission_id']==mission['id'] and stage['task_id']==task['id']
        assert stage['state']=='running' and stage['attempt']==1 and stage['stage']=='proposal'
        result={'schema_version':1,'operation_id':operation,'state':'current_preparation_priority_reconciled',
            'program_id':str(program['id']),'program_digest':program['manifest_digest'],
            'stage_id':str(stage['id']),'task_id':str(task['id']),'previous_priority':100,'priority':0,
            'signed_programme_resource_priority_changed':False,'scientific_authority_changed':False,
            'source_completion_or_selection_bypassed':False,'new_model_call_or_scientific_job_enqueued':False,
            'authority':'Existing owner continuation requests and urgent first-experiment instruction; root-reviewed PR105 operation'}
        conn.execute('UPDATE tasks SET priority=0 WHERE id=%s',(task['id'],))
        event=conn.execute('INSERT INTO events(project_id,kind,detail) VALUES(%s,%s,%s) RETURNING id',
            (project['id'],'research_preparation_priority_reconciled',Jsonb(result))).fetchone()
        result.update(event_id=event['id'],already_committed=False)
print(json.dumps(result))
'''
completed = subprocess.run(["docker", "exec", "-i", "quant-company-api-1", "python", "/app/entrypoint.py", "python", "-"],
    input=code, capture_output=True, text=True, timeout=60)
if completed.returncode:
    private = pathlib.Path('/var/lib/quant-company/releases/research-priority.private-error.log')
    private.write_text(completed.stderr)
    private.chmod(0o600)
    raise RuntimeError('priority_operation_failed_check_private_host_receipt')
print(json.dumps(json.loads(completed.stdout), indent=2))

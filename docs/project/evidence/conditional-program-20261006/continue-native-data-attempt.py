"""Continue one definite event-format failure with a fresh turn and the same genuine read history."""

import json
import sys
from datetime import UTC, datetime

from psycopg.types.json import Jsonb

from quant_company.company import Company, as_json, fingerprint
from quant_company.config import Settings
from quant_company.research.program_contracts import ResearchProgram

company = Company(Settings())
assert company.settings.company_code_commit == '5c44ad07273adc780a56a47d7d35114fd0dc32c8'
spec = ResearchProgram.model_validate_json(sys.argv[1])
digest = fingerprint(spec.model_dump(mode='json'))
assert digest == 'c3ba5268d71c87bd2c6226160bc720498137bd291396a054970a1f1e7ebcf2db'
failure = json.loads(sys.argv[2])
assert failure['receipt']['request_id'] == '86e01053-793c-5784-b944-dea0ba1d5349'
assert failure['receipt']['state'] == 'failed'
assert failure['receipt']['fault']['code'] == 'invalid_output'
assert failure['receipt']['fault']['message'].endswith('(events:invalid_shape).')
stage_id = 'ce867fc3-a194-5327-b312-864b93538d34'
with company.db.transaction() as conn:
    program = conn.execute('SELECT * FROM research_programs WHERE manifest_digest=%s FOR UPDATE', (digest,)).fetchone()
    assert program['state'] == 'active' and program['approval_event_id']
    stage = conn.execute('SELECT * FROM research_mission_stages WHERE id=%s FOR UPDATE', (stage_id,)).fetchone()
    assert stage['program_id'] == program['id'] and stage['stage'] == 'program_data'
    assert stage['state'] == 'waiting' and stage['attempt'] == 6 and stage['error'] == 'invalid_output'
    assert stage['context']['_data_output_failures'] == 2 and not stage['context'].get('_program_hold')
    task = conn.execute('SELECT * FROM tasks WHERE id=%s FOR UPDATE', (stage['task_id'],)).fetchone()
    assert str(task['id']) == 'a6c52e6a-91bc-5d55-980a-141cdfb15477'
    assert task['status'] == 'blocked' and task['turn_count'] == 44 and task['kind'] == 'research_stage'
    turns = conn.execute('SELECT id,sequence,status,request,response FROM turns WHERE task_id=%s ORDER BY sequence',
                         (task['id'],)).fetchall()
    assert len(turns) == 44 and all(turn['status'] == 'completed' for turn in turns[:-1])
    assert str(turns[-1]['id']) == failure['receipt']['request_id'] and turns[-1]['status'] == 'blocked'
    assert not conn.execute("SELECT 1 FROM turns WHERE task_id=%s AND status IN ('queued','running','waiting')",
                            (task['id'],)).fetchone()
    before = fingerprint(as_json(turns))
    read_count = conn.execute('SELECT count(*) AS n FROM research_stage_reads WHERE stage_id=%s AND attempt=6',
                             (stage_id,)).fetchone()['n']
    assert read_count == 43 and 'data_artifact_serialization' in stage['context']
    context = dict(stage['context'])
    assert '_native_event_continuation' not in context, 'already_continued'
    repair = {'failed_request_id': failure['receipt']['request_id'], 'receipt_sha256': failure['receipt']['receipt_sha256'],
              'failed_phase': 'events', 'prior_turns_digest': before, 'failure_count_preserved': 2,
              'same_attempt_read_count': read_count, 'scope': 'One fresh technical continuation; no artifact is applied.'}
    context['_native_event_continuation'] = repair
    conn.execute("UPDATE research_mission_stages SET state='running',context=%s,result=NULL,retry_at=NULL,updated_at=now() WHERE id=%s",
                 (Jsonb(context), stage_id))
    fresh_turn_id = company._new_turn(conn, task)
    after = conn.execute('SELECT id,sequence,status,request,response FROM turns WHERE task_id=%s AND sequence<=44 ORDER BY sequence',
                        (task['id'],)).fetchall()
    assert fingerprint(as_json(after)) == before, 'original_turns_modified'
    company._event(conn, 'research_program_native_event_continued', {**repair, 'stage_id': stage_id,
                   'fresh_turn_id': fresh_turn_id}, program['project_id'])
print(json.dumps(as_json({'schema_version': 1, 'observed_at': datetime.now(UTC), 'program_digest': digest,
    'stage_id': stage_id, 'attempt': 6, 'fresh_turn_id': fresh_turn_id, 'fresh_sequence': 45, **repair,
    'genuine_read_history_preserved': True, 'original_frozen_turns_preserved': True,
    'employee_decision_applied': False, 'scientific_budget_reset': False, 'model_requests_replayed': 0}), ensure_ascii=False))

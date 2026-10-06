"""Remove one technical format hold after compatible intake; leave every scientific decision to employees."""

import hashlib
import json
import sys
from datetime import UTC, datetime

from psycopg.types.json import Jsonb

from quant_company.company import Company, as_json, fingerprint
from quant_company.config import Settings
from quant_company.contracts import ProviderRequest
from quant_company.providers.codex_runner import request_digest
from quant_company.research.program_contracts import ResearchProgram

company = Company(Settings())
source = '5c44ad07273adc780a56a47d7d35114fd0dc32c8'
assert company.settings.company_code_commit == source
spec = ResearchProgram.model_validate_json(sys.argv[1])
digest = fingerprint(spec.model_dump(mode='json'))
assert digest == 'c3ba5268d71c87bd2c6226160bc720498137bd291396a054970a1f1e7ebcf2db'
activation, verification, registration = (json.loads(value) for value in sys.argv[2:5])
receipts = json.loads(sys.argv[5])
assert activation['source_commit'] == verification['source_commit'] == registration['source_commit'] == source
assert activation['phase'] == 'active_held_for_data_review_reconciliation'
assert activation['authority_and_receipts_preserved'] and activation['backup']['sha256']
assert verification['actual_temporal_rpc'] and verification['original_signed_approval_and_inbound_verified']
assert registration['exact_release_resolvable_by_active_supervisor']
assert registration['phase'] == 'qualified_registered_for_exact_jobs'
stage_id = 'ce867fc3-a194-5327-b312-864b93538d34'
with company.db.transaction() as conn:
    program = conn.execute('SELECT * FROM research_programs WHERE manifest_digest=%s FOR UPDATE', (digest,)).fetchone()
    assert program['state'] == 'active' and program['approval_event_id'] == verification['program']['approval_event_id']
    stage = conn.execute('SELECT * FROM research_mission_stages WHERE id=%s FOR UPDATE', (stage_id,)).fetchone()
    assert stage['program_id'] == program['id'] and stage['stage'] == 'program_data'
    assert stage['state'] == 'waiting' and stage['attempt'] == 4 and stage['retry_at'] is None
    hold = stage['context']['_program_hold']
    assert hold['reason'] == 'repeated_data_output_contract_failure' and hold['failure_count'] == 4
    assert stage['context'].get('_data_output_failures') is None
    attempts = conn.execute('SELECT task_id,error FROM research_stage_attempts WHERE stage_id=%s ORDER BY attempt',
                            (stage_id,)).fetchall()
    assert len(attempts) == 4 and all(a['error'] == 'stage_response_rejected' for a in attempts)
    assert len(receipts) == len(hold['receipts']) == 4
    for raw, prior, attempt in zip(receipts, hold['receipts'], attempts, strict=True):
        assert hashlib.sha256(raw.encode()).hexdigest() == prior['receipt_sha256']
        receipt = json.loads(raw)
        assert receipt['request_id'] == prior['request_id'] and receipt['state'] == 'failed'
        assert receipt['fault']['code'] == 'invalid_output'
        turn = conn.execute('SELECT task_id,status,request FROM turns WHERE id=%s', (receipt['request_id'],)).fetchone()
        assert turn['task_id'] == attempt['task_id'] and turn['status'] == 'blocked'
        assert request_digest(ProviderRequest.model_validate(turn['request'])) == receipt['input_digest']
    assert not conn.execute("""SELECT 1 FROM turns t JOIN research_stage_attempts a ON a.task_id=t.task_id
        WHERE a.stage_id=%s AND t.status IN ('queued','running','waiting')""", (stage_id,)).fetchone()
    task = conn.execute('SELECT id,state,data_assessment,decision,mission_id FROM research_program_tasks WHERE program_id=%s',
                        (program['id'],)).fetchone()
    assert task['state'] == 'proposed' and task['data_assessment'] is None and task['decision'] is None and task['mission_id'] is None
    pause = conn.execute('SELECT * FROM runtime_control WHERE id=1 FOR UPDATE').fetchone()
    assert pause['reason'] == activation['pause_reason']
    assert pause['paused_until'].isoformat() == activation['pause_until']
    repair = {'stage_id': stage_id, 'source_commit': source, 'output_contract': 'research_stage_v1',
        'prior_hold': hold, 'ci_run_id': activation['gates']['ci']['run_id'],
        'backup_sha256': activation['backup']['sha256'], 'prior_attempts_preserved': 4,
        'native_failure_limit': 3, 'scope': 'Technical response contract repair only.'}
    context = {key: value for key, value in stage['context'].items() if key != '_program_hold'}
    context['_output_contract_repair'] = repair
    conn.execute('UPDATE research_mission_stages SET context=%s,updated_at=now() WHERE id=%s', (Jsonb(context), stage_id))
    company._event(conn, 'research_program_output_contract_repaired', repair, program['project_id'])
    # Scheduling and the next independent model turn remain the normal Temporal/controller path.
    conn.execute('UPDATE runtime_control SET paused_until=NULL,reason=NULL WHERE id=1')
print(json.dumps(as_json({'schema_version': 1, 'observed_at': datetime.now(UTC),
    'phase': 'technical_hold_released_for_normal_controller', 'program_id': program['id'],
    'program_digest': digest, **repair, 'original_signed_approval_preserved': True,
    'employee_decision_applied': False, 'scientific_budget_reset': False,
    'model_requests_replayed': 0, 'frozen_failed_requests_modified': False}), ensure_ascii=False))

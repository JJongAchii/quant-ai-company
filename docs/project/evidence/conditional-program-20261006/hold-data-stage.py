"""Hold repeated definite format failures without changing owner authority or an employee judgment."""

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
assert company.settings.company_code_commit == '231d6ba0755f658f167636a8ea2c3ef65b6af562'
spec = ResearchProgram.model_validate_json(sys.argv[1])
digest = fingerprint(spec.model_dump(mode='json'))
assert digest == 'c3ba5268d71c87bd2c6226160bc720498137bd291396a054970a1f1e7ebcf2db'
receipts = json.loads(sys.argv[3])
assert len(receipts) == 4
with company.db.transaction() as conn:
    program = conn.execute('SELECT * FROM research_programs WHERE manifest_digest=%s FOR UPDATE', (digest,)).fetchone()
    assert program['state'] == 'active'
    stage = conn.execute('SELECT * FROM research_mission_stages WHERE id=%s FOR UPDATE',
                         ('ce867fc3-a194-5327-b312-864b93538d34',)).fetchone()
    assert stage['program_id'] == program['id'] and stage['stage'] == 'program_data'
    assert stage['state'] == 'waiting' and stage['error'] == 'invalid_output' and stage['attempt'] == 4
    attempts = conn.execute('SELECT * FROM research_stage_attempts WHERE stage_id=%s ORDER BY attempt',
                            (stage['id'],)).fetchall()
    assert len(attempts) == 4 and all(a['error'] == 'stage_response_rejected' for a in attempts)
    assert not conn.execute("""SELECT 1 FROM turns t JOIN research_stage_attempts a ON a.task_id=t.task_id
        WHERE a.stage_id=%s AND t.status IN ('queued','running','waiting')""", (stage['id'],)).fetchone()
    verified = []
    for raw, attempt in zip(receipts, attempts, strict=True):
        receipt = json.loads(raw)
        assert receipt['state'] == 'failed' and receipt['fault']['code'] == 'invalid_output'
        assert receipt['fault']['message'].endswith('(decision_contract:invalid_shape).')
        turn = conn.execute('SELECT * FROM turns WHERE id=%s', (receipt['request_id'],)).fetchone()
        assert turn['task_id'] == attempt['task_id'] and turn['status'] == 'blocked'
        request = ProviderRequest.model_validate(turn['request'])
        assert request_digest(request) == receipt['input_digest']
        verified.append({'request_id': receipt['request_id'], 'receipt_sha256': hashlib.sha256(raw.encode()).hexdigest()})
    context = {**stage['context'], '_program_hold': {
        'reason': 'repeated_data_output_contract_failure', 'failure_count': 4, 'receipts': verified,
        'operator_scope': 'Technical output repair; no data admission, selection or scientific judgment.'}}
    conn.execute("""UPDATE research_mission_stages SET context=%s,retry_at=NULL,updated_at=now()
        WHERE id=%s""", (Jsonb(context), stage['id']))
    company._event(conn, 'research_program_stage_held', {'stage_id': str(stage['id']),
        'reason': 'repeated_data_output_contract_failure', 'failure_count': 4, 'receipts': verified}, program['project_id'])
print(json.dumps(as_json({'schema_version': 1, 'observed_at': datetime.now(UTC),
    'program_id': program['id'], 'program_digest': digest, 'stage_id': stage['id'],
    'reason': 'repeated_data_output_contract_failure', 'failure_count': 4,
    'retry_at': None, 'owner_program_state_preserved': 'active', 'failure_receipts': verified,
    'employee_decision_applied': False, 'scientific_budget_reset': False, 'model_calls_replayed': 0}), ensure_ascii=False))

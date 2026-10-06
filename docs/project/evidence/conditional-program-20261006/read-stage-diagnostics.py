"""Read failed employee artifacts and check their contract without applying a decision."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from quant_company.company import Company, PolicyError, as_json, fingerprint
from quant_company.config import Settings
from quant_company.research.program_contracts import DataAssessment, ResearchProgram
from quant_company.research.programs import ProgramStore

company = Company(Settings(research_data_evidence_file=Path('/state/research/provisioned/data-evidence/registry.json')))
assert company.settings.company_code_commit == '231d6ba0755f658f167636a8ea2c3ef65b6af562'
spec = ResearchProgram.model_validate_json(sys.argv[1])
digest = fingerprint(spec.model_dump(mode='json'))
assert digest == 'c3ba5268d71c87bd2c6226160bc720498137bd291396a054970a1f1e7ebcf2db'
diagnostics = []
with company.db.transaction() as conn:
    conn.execute('SET TRANSACTION READ ONLY')
    program = conn.execute('SELECT * FROM research_programs WHERE manifest_digest=%s', (digest,)).fetchone()
    task = conn.execute('SELECT * FROM research_program_tasks WHERE program_id=%s ORDER BY created_at LIMIT 1',
                        (program['id'],)).fetchone()
    stages = conn.execute("""SELECT s.id,s.state,s.error,s.result,s.context->'required_data_reads' AS required_data_reads,
        a.attempt,a.error AS attempt_error,a.task_id,a.response FROM research_mission_stages s
        JOIN research_stage_attempts a ON a.stage_id=s.id
        WHERE s.program_id=%s AND s.stage='program_data' ORDER BY s.created_at,a.attempt""", (program['id'],)).fetchall()
    for stage in stages:
        item = {key: stage[key] for key in ('id', 'state', 'error', 'attempt', 'attempt_error', 'task_id')}
        item['response_present'] = stage['response'] is not None
        if stage['response']:
            artifacts = stage['response'].get('decision', {}).get('artifacts', [])
            try:
                value = json.loads(artifacts[0]['content'], strict=False)
                item['artifact_fields'] = sorted(value)
                item['proposed_decision'] = value.get('decision')
                typed = DataAssessment.model_validate(value)
                company._check_sources(conn, program['project_id'], typed.source_ids)
                envelope = next(e for e in spec.envelopes if e.name == task['proposal']['envelope'])
                ProgramStore(company)._assessment(conn, program, envelope, task, typed, allow_blocked=True)
                item['read_only_contract_check'] = 'accepted'
            except ValidationError as exc:
                item['read_only_contract_check'] = 'validation_rejected'
                item['contract_errors'] = [{'location': list(e['loc']), 'message': e['msg']}
                                           for e in exc.errors(include_input=False)]
            except (PolicyError, ValueError, KeyError, IndexError, TypeError) as exc:
                item['read_only_contract_check'] = 'rejected'
                item['contract_error'] = str(exc)[:1200]
        diagnostics.append(item)
print(json.dumps(as_json({'schema_version': 1, 'observed_at': datetime.now(UTC),
    'program_digest': digest, 'data_attempts': diagnostics, 'transaction_read_only': True,
    'employee_decision_applied': False, 'model_call_replayed': False}), ensure_ascii=False))

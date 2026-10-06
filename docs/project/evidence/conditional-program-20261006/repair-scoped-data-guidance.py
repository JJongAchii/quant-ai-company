"""Return a rejected artifact's schema error to future employee turns without editing their judgment."""

import json
import sys
from datetime import UTC, datetime

from psycopg.types.json import Jsonb

from quant_company.company import Company, as_json, fingerprint
from quant_company.config import Settings
from quant_company.research.program_contracts import ResearchProgram

company = Company(Settings())
source = '5c44ad07273adc780a56a47d7d35114fd0dc32c8'
assert company.settings.company_code_commit == source
spec = ResearchProgram.model_validate_json(sys.argv[1])
digest = fingerprint(spec.model_dump(mode='json'))
assert digest == 'c3ba5268d71c87bd2c6226160bc720498137bd291396a054970a1f1e7ebcf2db'
stage_id = 'ce867fc3-a194-5327-b312-864b93538d34'
with company.db.transaction() as conn:
    program = conn.execute('SELECT * FROM research_programs WHERE manifest_digest=%s FOR UPDATE', (digest,)).fetchone()
    assert program['state'] == 'active' and program['approval_event_id']
    stage = conn.execute('SELECT * FROM research_mission_stages WHERE id=%s FOR UPDATE', (stage_id,)).fetchone()
    assert stage['program_id'] == program['id'] and stage['stage'] == 'program_data'
    assert stage['state'] == 'running' and stage['attempt'] == 6
    assert str(stage['task_id']) == 'a6c52e6a-91bc-5d55-980a-141cdfb15477'
    assert stage['context']['_data_output_failures'] == 1 and not stage['context'].get('_program_hold')
    assert 'data_artifact_serialization' not in stage['context'], 'guidance_already_recorded'
    prior = conn.execute('SELECT * FROM research_stage_attempts WHERE stage_id=%s AND attempt=5', (stage_id,)).fetchone()
    assert prior['completed_at'] and prior['response']['provider'] == 'codex'
    rejected = json.loads(prior['response']['decision']['artifacts'][0]['content'])
    assert rejected['schema_version'] == 2
    assert rejected.get('data_policy_digest') is not None or rejected.get('evaluation_prices') is not None or rejected['decision'] == 'exploratory_only'
    frozen = conn.execute('SELECT id,request,response,status FROM turns WHERE task_id=%s ORDER BY sequence',
                          (stage['task_id'],)).fetchall()
    frozen_identity = fingerprint(as_json(frozen))
    guidance = {
        'rejected_attempt': 5,
        'validation_error': 'Scoped assessments cannot carry a legacy exploratory decision or policy fields',
        'schema_version': 2,
        'canonical_research_scope': spec.envelopes[0].template.research_scope.model_dump(mode='json'),
        'field_constraints': {
            'data_policy_digest': 'Omit or set null. The policy digest belongs inside canonical research_scope.',
            'evaluation_prices': 'Omit or set null. For scoped assessments use evaluation_price_contract_verified.',
            'decision': 'Use the version 2 contract. exploratory_only is a legacy decision and is invalid here.',
        },
        'rejected_artifact': rejected,
        'decision_scope': ('Schema feedback only. Independently re-evaluate the unchanged evidence and return your own '
                           'DataAssessment. This feedback grants no readiness, acceptance, execution or scope promotion.'),
    }
    context = {**stage['context'], 'data_artifact_serialization': guidance}
    conn.execute('UPDATE research_mission_stages SET context=%s,updated_at=now() WHERE id=%s',
                 (Jsonb(context), stage_id))
    after = conn.execute('SELECT id,request,response,status FROM turns WHERE task_id=%s ORDER BY sequence',
                        (stage['task_id'],)).fetchall()
    assert fingerprint(as_json(after)) == frozen_identity, 'existing_turns_modified'
    event = {'stage_id': stage_id, 'attempt': 6, 'rejected_attempt': 5,
             'guidance_digest': fingerprint(guidance), 'rejected_artifact_digest': fingerprint(rejected),
             'scope': 'Artifact serialization feedback for future unfrozen employee turns only.'}
    company._event(conn, 'research_program_data_schema_feedback', event, program['project_id'])
print(json.dumps(as_json({'schema_version': 1, 'observed_at': datetime.now(UTC),
    'program_id': program['id'], 'program_digest': digest, 'source_commit': source,
    **event, 'guidance': guidance, 'existing_turns_digest': frozen_identity,
    'frozen_requests_and_responses_preserved': True, 'employee_decision_applied': False,
    'failure_count_preserved': 1, 'scientific_budget_reset': False, 'model_requests_replayed': 0}), ensure_ascii=False))

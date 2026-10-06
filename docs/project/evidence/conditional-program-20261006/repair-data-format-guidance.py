"""Clarify the existing output serialization for future turns, without changing any research decision."""

import json
import sys
from datetime import UTC, datetime

from psycopg.types.json import Jsonb

from quant_company.company import Company, as_json, fingerprint
from quant_company.config import Settings
from quant_company.contracts import AgentDecision
from quant_company.research.program_contracts import ResearchProgram

company = Company(Settings())
assert company.settings.company_code_commit == '231d6ba0755f658f167636a8ea2c3ef65b6af562'
spec = ResearchProgram.model_validate_json(sys.argv[1])
digest = fingerprint(spec.model_dump(mode='json'))
assert digest == 'c3ba5268d71c87bd2c6226160bc720498137bd291396a054970a1f1e7ebcf2db'
examples = {
    'read': {'say': '', 'status': 'continue', 'tools': [{'name': 'research_control',
        'arguments': {'action': 'read_stage_file', 'path': '<exact available path>', 'offset': 0}}]},
    'complete': {'say': '', 'status': 'complete', 'artifacts': [{'title': 'program_data',
        'content': '<JSON string encoding the independent DataAssessment object>'}]}}
for example in examples.values():
    AgentDecision.model_validate(example)
guidance = {'artifact_content_type': 'string',
    'artifact_content_encoding': 'Serialize the output_schema JSON object into a string. Do not put an object in artifact.content.',
    'agent_decision_examples': examples,
    'decision_scope': 'Serialization only. Independently choose the data decision using the unchanged evidence and canonical scope.'}
with company.db.transaction() as conn:
    program = conn.execute('SELECT * FROM research_programs WHERE manifest_digest=%s FOR UPDATE', (digest,)).fetchone()
    assert program['state'] == 'active'
    stage = conn.execute('SELECT * FROM research_mission_stages WHERE id=%s FOR UPDATE',
                         ('ce867fc3-a194-5327-b312-864b93538d34',)).fetchone()
    assert stage['program_id'] == program['id'] and stage['stage'] == 'program_data' and stage['actor'] == 'data'
    assert stage['state'] == 'running' and stage['attempt'] == 4
    assert stage['context']['task']['id'] == 'bc915792-d608-5710-a271-f4aa592e9ee1'
    requests_before = conn.execute('SELECT id,request FROM turns WHERE task_id=%s ORDER BY sequence',
                                   (stage['task_id'],)).fetchall()
    context = {**stage['context'], 'response_serialization': guidance}
    conn.execute('UPDATE research_mission_stages SET context=%s,updated_at=now() WHERE id=%s',
                 (Jsonb(context), stage['id']))
    requests_after = conn.execute('SELECT id,request FROM turns WHERE task_id=%s ORDER BY sequence',
                                  (stage['task_id'],)).fetchall()
    assert requests_after == requests_before
    company._event(conn, 'research_stage_output_format_guidance', {'stage_id': str(stage['id']),
        'attempt': 4, 'guidance_digest': fingerprint(guidance), 'research_decision_changed': False}, program['project_id'])
print(json.dumps(as_json({'schema_version': 1, 'observed_at': datetime.now(UTC),
    'program_id': program['id'], 'program_digest': digest, 'stage_id': stage['id'], 'attempt': 4,
    'guidance': guidance, 'guidance_digest': fingerprint(guidance),
    'frozen_requests_preserved': True, 'employee_decision_applied': False,
    'program_inputs_policy_and_budget_preserved': True, 'model_calls_replayed': 0}), ensure_ascii=False))

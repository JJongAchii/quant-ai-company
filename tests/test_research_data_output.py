"""Real PostgreSQL with scripted provider events; no real Codex, Slack or financial execution."""

import json
from pathlib import Path

from psycopg.types.json import Jsonb

from quant_company.contracts import ProviderRequest
from quant_company.providers.codex_runner import ProcessResult, parse_result
from quant_company.research.mission_backend import MissionBackend
from quant_company.research.program_controller import ProgramController

from .test_research_conditional import conditional  # noqa: F401
from .test_research_programs import program, provision_data_packet, task_proposal  # noqa: F401


def pending(company):
    with company.db.transaction() as conn:
        stage = conn.execute("SELECT * FROM research_mission_stages WHERE stage='program_data'").fetchone()
        turn = conn.execute("SELECT id FROM turns WHERE task_id=%s AND status='queued'",
                            (stage['task_id'],)).fetchone()
    return stage, str(turn['id'])


def native_response(prepared, payload):
    events = [{'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(payload)}},
              {'type': 'turn.completed', 'usage': {}}]
    output = '\n'.join(json.dumps(e) for e in events).encode()
    return parse_result(ProviderRequest.model_validate(prepared['request']), ProcessResult(0, output, b''), 900)


def begin_data(harness):
    with harness.company.db.transaction() as conn:
        harness.program_store.propose(conn, harness.program_id, task_proposal(), actor='researcher_kr')
    controller = ProgramController(harness.company)
    assert controller.tick()['state'] == 'running'
    return controller


def test_native_private_data_output_still_requires_original_reads_and_independent_decision(program):  # noqa: F811
    company = program.company
    controller = begin_data(program)
    stage, turn_id = pending(company)
    prepared = company.prepare_turn(turn_id)
    assert prepared['request']['output_contract'] == 'research_stage_v1'
    path = stage['context']['evidence_sources'][0]['file']
    response = native_response(prepared, {'action': 'read', 'read_path': path, 'read_offset': 0, 'artifact_json': None})
    company.commit_turn(turn_id, response)
    _, final_id = pending(company)
    final = company.prepare_turn(final_id)
    blocked = {'decision': 'blocked', 'rationale': 'Synthetic fixture cannot establish execution evidence',
               'source_ids': ['fixture:baseline'], 'point_in_time': False, 'coverage': True,
               'executable_prices': False, 'original_conditions': False}
    response = native_response(final, {'action': 'complete', 'read_path': None, 'read_offset': None,
                                      'artifact_json': json.dumps(blocked)})
    company.commit_turn(final_id, response)
    assert controller.tick()['state'] == 'completed'
    with company.db.transaction() as conn:
        task = conn.execute('SELECT * FROM research_program_tasks WHERE program_id=%s', (program.program_id,)).fetchone()
        assert task['data_assessment'] == blocked and task['state'] == 'assessed'
        assert task['decision'] is None and task['mission_id'] is None
        assert not conn.execute('SELECT 1 FROM research_program_reservations WHERE scientific_trial').fetchone()


def test_repeated_data_output_faults_preserve_each_request_and_hold_after_three_attempts(program):  # noqa: F811
    company = program.company
    controller = begin_data(program)
    ids = []
    for attempt in range(1, 4):
        stage, turn_id = pending(company)
        assert stage['attempt'] == attempt
        request = company.prepare_turn(turn_id)['request']
        ids.append(turn_id)
        company.block_turn(turn_id, 'invalid_output')
        company.block_turn(turn_id, 'invalid_output')  # One committed fault per turn.
        with company.db.transaction() as conn:
            actual = conn.execute('SELECT * FROM research_mission_stages WHERE id=%s', (stage['id'],)).fetchone()
            assert actual['context']['_data_output_failures'] == attempt
            saved = conn.execute('SELECT request FROM turns WHERE id=%s', (turn_id,)).fetchone()
            assert saved['request'] == request
            if attempt < 3:
                conn.execute("UPDATE research_mission_stages SET retry_at=now()-interval '1 second' WHERE id=%s",
                             (stage['id'],))
        state = controller.tick()
        if attempt < 3:
            assert state['state'] == 'running'
        else:
            assert state == {'state': 'waiting', 'reason': 'repeated_data_output_contract_failure'}
            assert actual['retry_at'] is None
    with company.db.transaction() as conn:
        assert conn.execute('SELECT count(*) AS n FROM research_stage_attempts').fetchone()['n'] == 3
        assert conn.execute("SELECT count(*) AS n FROM turns WHERE status='blocked' AND id=ANY(%s::uuid[])",
                            (ids,)).fetchone()['n'] == 3
        assert not conn.execute('SELECT 1 FROM research_program_reservations WHERE scientific_trial').fetchone()


def test_frozen_data_engine_stays_in_the_prompt_after_long_report_reads(program):  # noqa: F811
    company = program.company
    provision_data_packet(program)
    begin_data(program)
    stage, _ = pending(company)
    engine = stage['context']['data_evidence_packets'][0]['engine_file']
    engine_content = Path(stage['context']['_private_files'][engine]['path']).read_text()
    backend = MissionBackend(company)
    extra = backend._entry(backend._file(company.settings.research_artifact_dir / 'long-fixture.txt',
                                        ('Synthetic nonfinancial report.\n' * 3000).encode()))
    with company.db.transaction() as conn:
        context = stage['context']
        context['_private_files']['long-report.txt'] = extra
        context['available_files'].append({'name': 'long-report.txt', 'size': extra['size'], 'sha256': extra['sha256']})
        conn.execute('UPDATE research_mission_stages SET context=%s WHERE id=%s', (Jsonb(context), stage['id']))
    for path, offsets in ((engine, range(0, max(1, len(engine_content)), 12000)),
                          ('long-report.txt', range(0, 84000, 12000))):
        for offset in offsets:
            _, turn_id = pending(company)
            prepared = company.prepare_turn(turn_id)
            company.commit_turn(turn_id, native_response(prepared, {
                'action': 'read', 'read_path': path, 'read_offset': offset, 'artifact_json': None}))
    _, turn_id = pending(company)
    prompt = company.prepare_turn(turn_id)['request']['prompt']
    context = json.loads(prompt.split('MISSION DATA JSON:\n', 1)[1])
    retained = [c for c in context['read_chunks'] if c['path'] == engine]
    assert ''.join(c['content'] for c in retained) == engine_content
    assert context['file_progress'][engine]['next_offset'] is None and len(prompt) <= 90000


def test_invalid_data_assessment_cannot_retry_forever_or_grant_admission(program):  # noqa: F811
    company = program.company
    controller = begin_data(program)
    for attempt in range(1, 4):
        stage, turn_id = pending(company)
        prepared = company.prepare_turn(turn_id)
        invalid = {'decision': 'ready', 'rationale': 'Synthetic unsupported readiness claim', 'source_ids': [],
                   'point_in_time': False, 'coverage': False, 'executable_prices': False, 'original_conditions': False}
        company.commit_turn(turn_id, native_response(prepared, {
            'action': 'complete', 'read_path': None, 'read_offset': None, 'artifact_json': json.dumps(invalid)}))
        assert controller.tick()['state'] == 'waiting'
        with company.db.transaction() as conn:
            actual = conn.execute('SELECT * FROM research_mission_stages WHERE id=%s', (stage['id'],)).fetchone()
            task = conn.execute('SELECT * FROM research_program_tasks WHERE program_id=%s', (program.program_id,)).fetchone()
            assert actual['context']['_data_output_failures'] == attempt
            assert task['data_assessment'] is None and task['mission_id'] is None
            if attempt < 3:
                conn.execute("UPDATE research_mission_stages SET retry_at=now()-interval '1 second' WHERE id=%s",
                             (stage['id'],))
        if attempt < 3:
            assert controller.tick()['state'] == 'running'
    assert controller.tick() == {'state': 'waiting', 'reason': 'repeated_data_output_contract_failure'}
    assert actual['retry_at'] is None


def test_scoped_employee_prompt_excludes_the_legacy_fields_that_failed_in_production(conditional):  # noqa: F811
    company = conditional.company
    with company.db.transaction() as conn:
        conditional.program_store.propose(conn, conditional.program_id,
            task_proposal(mode='novel_hypothesis'), actor='researcher_kr')
    assert ProgramController(company).tick()['state'] == 'running'
    _, turn_id = pending(company)
    prepared = company.prepare_turn(turn_id)
    context = json.loads(prepared['request']['prompt'].split('MISSION DATA JSON:\n', 1)[1])
    schema = context['output_schema']
    assert prepared['request']['output_contract'] == 'research_stage_v1'
    assert schema['properties']['schema_version'] == {'type': 'integer', 'const': 2}
    assert {'schema_version', 'research_scope'} <= set(schema['required'])
    assert 'data_policy_digest' not in schema['properties'] and 'evaluation_prices' not in schema['properties']
    assert 'packet_digest' in schema['properties'] and 'evaluation_price_contract_verified' in schema['properties']
    assert schema['properties']['decision']['enum'] == ['ready', 'conditional_ready', 'blocked']
    assert context['research_scopes']['etf'] == conditional.program_spec.envelopes[0].template.research_scope.model_dump(
        mode='json')
    with company.db.transaction() as conn:
        task = conn.execute('SELECT * FROM research_program_tasks WHERE program_id=%s', (conditional.program_id,)).fetchone()
        assert task['data_assessment'] is None and task['decision'] is None and task['mission_id'] is None


def test_legacy_employee_prompt_retains_its_signed_assessment_contract(program):  # noqa: F811
    begin_data(program)
    _, turn_id = pending(program.company)
    context = json.loads(program.company.prepare_turn(turn_id)['request']['prompt'].split('MISSION DATA JSON:\n', 1)[1])
    schema = context['output_schema']
    assert schema['properties']['schema_version'] == {'type': 'integer', 'const': 1}
    assert 'schema_version' in schema['required']
    assert {'data_policy_digest', 'evaluation_prices'} <= set(schema['properties'])
    assert not {'research_scope', 'packet_digest', 'evaluation_price_contract_verified'} & set(schema['properties'])
    assert schema['properties']['decision']['enum'] == ['ready', 'exploratory_only', 'blocked']

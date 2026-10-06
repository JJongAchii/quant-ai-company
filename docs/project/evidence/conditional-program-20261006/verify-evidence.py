"""Verify public operating receipts without credentials, model calls or market execution."""

import ast
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from quant_company.company import fingerprint
from quant_company.research.data_evidence import DataEvidencePacket, require_admissible_gaps
from quant_company.research.policy_contracts import require_scope
from quant_company.research.program_contracts import (
    DataAssessment,
    ResearchProgram,
    ResearchTaskProposal,
    TaskDecision,
)

base = Path(__file__).resolve().parent
repo = base.parents[3]


def read(name):
    return json.loads((base / name).read_text())


for path in base.glob('*.json'):
    json.loads(path.read_text())
for path in base.glob('*.py'):
    ast.parse(path.read_text(), filename=str(path))

preparation = read('program-preparation.json')
program = ResearchProgram.model_validate(read('candidate-program.json'))
digest = fingerprint(program.model_dump(mode='json'))
assert digest == preparation['program_digest'] == 'c3ba5268d71c87bd2c6226160bc720498137bd291396a054970a1f1e7ebcf2db'
assert program.schema_version == 2
assert (program.max_total_trials, program.max_compute_seconds, program.max_missions,
        program.max_parallel_missions) == (4, 7200, 1, 1)
template = program.envelopes[0].template
assert template.schema_version == 3
packet = DataEvidencePacket.model_validate(preparation['packet'])
require_scope(template, packet)
require_admissible_gaps(packet, template)
assert packet.program_digest == digest
assert packet.execution_profile_digest == template.execution_profile_digest
assert {name: entry.sha256 for name, entry in packet.input_files.items()} == template.data.input_files
assert packet.engine.sha256 == '1984bf6b7fa5997a8b7ba446061ff663bdf78c683776be945da9130a067480cc'
history = preparation['history']
assert fingerprint(history) == preparation['history_digest'] == template.scientific_lineage.history_digest
assert history['origins'] == [ref.model_dump(mode='json') for ref in template.scientific_lineage.originating_task_refs]
assert len(history['origins']) == 12 and history['trials'] == []
assert preparation['actual_company_tree_verified']
assert preparation['profiles_inputs_engine_policy_budget_preserved']

owner = read('owner-approval-state.json')
current = next(p for p in owner['programs'] if p['manifest_digest'] == digest)
assert current['state'] == 'active'
binding = owner['approval_bindings'][-1]
assert binding['manifest_digest'] == digest and binding['status'] == 'delivered'
assert binding['sent_ts'] == '1791241501.260409'
assert current['approval_event_id'].endswith(f":action:{binding['id']}:approve")
approval = next(e for e in owner['actual_owner_events'] if e['owner_event_id'] == current['approval_event_id'])
assert approval['kind'] == 'research_program_approve' and approval['actual_inbound_exists']
assert approval['program_id'] == current['id'] and approval['manifest_digest'] == digest
previous = next(p for p in owner['programs'] if p['id'] == 'e06537d3-fac3-5c8c-bf25-ddabb3c7e282')
assert previous['state'] == 'cancelled'
cancellation = next(e for e in owner['actual_owner_events']
                    if e['owner_event_id'] == preparation['prior_signed_cancellation'])
assert cancellation['kind'] == 'research_program_cancel' and cancellation['actual_inbound_exists']
assert cancellation['program_id'] == previous['id']
assert cancellation['manifest_digest'] == previous['manifest_digest']
lineage_id = str(template.scientific_lineage.id)
assert next(lineage for lineage in owner['scientific_lineages'] if lineage['id'] == lineage_id)['trial_limit'] == 4
assert {(o['task_id'], o['task_digest']) for o in owner['scientific_origins']} == {
    (str(ref.task_id), ref.task_digest) for ref in template.scientific_lineage.originating_task_refs}
ui = read('owner-ui-review.json')
assert ui['program_digest'] == digest and ui['program_id'] == current['id']
assert ui['exact_canonical_server_notice_verified'] and not ui['manual_human_click_claimed']
assert ui['user_authorization'] == '너가 보내고 진행해줘'

before, recovery = read('server-baseline-before.json'), read('runtime-recovery.json')
for name, identity in before['containers'].items():
    actual = recovery['containers'][name]
    assert actual['id'] == identity['id'] and actual['image_id'] == identity['image_id']
assert len(recovery['containers']) == 15
assert all(c['state'] == 'running' for c in recovery['containers'].values())
assert recovery['disk']['free_bytes'] > 30_000_000_000
worker = read('worker-baseline-current.json')
old_worker = json.loads((repo / 'docs/project/evidence/conditional-runtime-20261002/worker-baseline-active.json').read_text())
assert worker['checkout_commit'] == worker['company_commit'] == preparation['company_commit']
assert worker['checkout_changes'] == '' and worker['config_sha256'] == old_worker['config_sha256']
assert worker['units']['research-worker.service']['ActiveState'] == 'active'
assert worker['units']['research-worker-tunnel.service']['ActiveState'] == 'active'
reconciliation = read('completed-turn-reconciliation.json')
assert reconciliation['actual_official_codex_receipt'] and reconciliation['request_and_response_binding_verified']
assert reconciliation['previous_account_call_state'] == reconciliation['account_call_state'] == 'completed'
assert reconciliation['model_requests_replayed'] == 0 and not reconciliation['agent_decision_applied']

native_source = '5c44ad07273adc780a56a47d7d35114fd0dc32c8'
ci, manifest = read('source-ci.json'), read('native-source-manifest.json')
qualification, registration = read('native-worker-qualification.json'), read('worker-registration.json')
images = read('image-preparation.json')
assert ci['source_commit'] == manifest['source_commit'] == qualification['source_commit'] == registration['source_commit'] == images['source_commit'] == native_source
assert ci['conclusion'] == 'success' and ci['passed'] == 1752 and ci['skipped'] == 47 and ci['deselected'] == 1
assert manifest['dependencies_entrypoint_and_qdata_pin_preserved'] and manifest['numeric_inputs_engine_and_profile_preserved']
assert qualification['program_digest'] == digest and qualification['actual_bubblewrap']
assert qualification['qualified_for_frozen_input_transport'] and qualification['warmup_rows'] == 1040
assert not qualification['performance_computed'] and not qualification['model_training']
assert not qualification['development_price_rows_mounted'] and not qualification['sealed_price_rows_read']
assert qualification['profile_digest'] == packet.execution_profile_digest
assert registration['phase'] == 'qualified_registered_for_exact_jobs' and registration['exact_release_resolvable_by_active_supervisor']
assert registration['active_config_sha256'] == worker['config_sha256'] and registration['previous_releases_preserved']
assert images['archive_sha256'] == manifest['archive_sha256'] and not images['services_replaced']
assert set(images['images']) == {'app', 'worker', 'gateway', 'codex'}
assert all(image['import_qualification']['native_schema_verified'] and image['import_qualification']['effective_uid'] == 10001
           and not image['import_qualification']['model_called'] for image in images['images'].values())
held = read('data-stage-hold.json')
assert held['failure_count'] == 4 and held['retry_at'] is None and not held['employee_decision_applied']
assert not held['scientific_budget_reset'] and held['model_calls_replayed'] == 0

activated = (base / 'server-native-activation.json').exists()
if activated:
    activation = read('server-native-activation.json')
    installed = read('native-installed-verification.json')
    resumed = read('native-data-resume.json')
    assert activation['source_commit'] == installed['source_commit'] == resumed['source_commit'] == native_source
    assert activation['phase'] == 'active_data_review_resumed' and activation['backup']['sha256']
    assert activation['authority_and_receipts_preserved'] and activation['model_calls_replayed'] == 0
    assert installed['actual_temporal_rpc'] and installed['actual_postgresql'] and installed['authorized_limit_change_only']
    assert installed['signed_history_digest'] == template.scientific_lineage.history_digest
    assert installed['original_signed_approval_and_inbound_verified'] and installed['worker_authenticated_polling']['fresh']
    assert resumed['original_signed_approval_preserved'] and resumed['prior_attempts_preserved'] == 4
    assert not resumed['employee_decision_applied'] and not resumed['scientific_budget_reset']
    assert len(activation['actual_imported_sources']) == 6
    assert hashlib.sha256((base / 'apply-native-runtime.py').read_bytes()).hexdigest() == activation['operator_helper_sha256']
    assert hashlib.sha256((base / 'finalize-native-runtime.py').read_bytes()).hexdigest() == activation['operator_probe_repair']['operator_helper_sha256']
    selected = {'quant-company-' + service + '-1' for service in activation['selected_services']}
    for name, old in activation['before'].items():
        if name not in selected:
            assert activation['after'][name]['id'] == old['id'] and activation['after'][name]['image_id'] == old['image_id']

staff_name = 'staff-review-state.json'
staff = read(staff_name)
assert staff['program_digest'] == digest
assert not staff['staff_decisions_fabricated'] and not staff['owner_approval_fabricated']
assert staff['stage_progress'] and any(t['response_provider'] == 'codex' for t in staff['stage_progress'])
assert not any(t['response_provider'] == 'fixture' for t in staff['stage_progress'])
assert staff['actual_trials'] == [] and staff['activity']['scientific_trials'] == 0
assert staff['activity']['active_jobs'] == 0
assert staff['worker_authenticated_polling']['id'] == 'worker' and staff['worker_authenticated_polling']['fresh']
presence = read('worker-presence-current.json')
assert presence['exit_code'] == 0 and all(state == 'active' for state in presence['units'].values())
assert not presence['worker_reconfigured_or_restarted'] and not presence['model_or_scientific_execution_invoked']
actual_program = next(p for p in staff['programs'] if p['id'] == current['id'])
assert actual_program['manifest_digest'] == digest and actual_program['approval_event_id'] == current['approval_event_id']
assert actual_program['state'] == 'active'
for task in staff['actual_tasks']:
    proposal = ResearchTaskProposal.model_validate(task['proposal'])
    assert proposal.envelope == program.envelopes[0].name and proposal.mode == 'novel_hypothesis'
    for stage_name, field, model in (('program_data', 'data_assessment', DataAssessment),
                                   ('program_selection', 'decision', TaskDecision)):
        if task[field] is None:
            continue
        value = model.model_validate(task[field])
        stage = next(s for s in staff['actual_stages'] if s['stage'] == stage_name)
        assert stage['state'] == 'completed'
        artifact = next(a for a in staff['stage_artifacts'] if a['stage_id'] == stage['id'])
        assert artifact['response_provider'] == 'codex' and artifact['state'] == 'completed'
        assert model.model_validate(artifact['result']).model_dump(mode='json') == value.model_dump(mode='json')
        payload = json.loads(artifact['response_artifacts'][0]['content'])
        assert fingerprint(payload) == fingerprint(artifact['result'])
        assert model.model_validate(payload).model_dump(mode='json') == value.model_dump(mode='json')
        if field == 'data_assessment':
            require_scope(template, value)
            assert value.packet_digest == fingerprint(packet.model_dump(mode='json'))
            assert not value.point_in_time and not value.executable_prices and not value.original_conditions
        elif value.decision == 'accept':
            assert task['mission_id'] is not None
        elif value.decision == 'revise':
            assert task['mission_id'] is None and task['state'] == 'rejected'
            feedback_delivery = next(item for item in staff['followup_feedback'] if item['prior_task_id'] == task['id'])
            assert feedback_delivery['prior_decision'] == 'revise' and feedback_delivery['evidence_navigation_delivered']
            assert feedback_delivery['first_response_provider'] == 'codex' and feedback_delivery['first_turn_state'] == 'completed'
            assert len(feedback_delivery['feedback_file_sha256']) == 64

feedback = read('artifact-schema-guidance.json')
assert feedback['source_commit'] == native_source and feedback['program_digest'] == digest
assert feedback['frozen_requests_and_responses_preserved'] and not feedback['employee_decision_applied']
assert feedback['failure_count_preserved'] == 1 and not feedback['scientific_budget_reset']
assert feedback['model_requests_replayed'] == 0
assert fingerprint(feedback['guidance']) == feedback['guidance_digest']
assert feedback['guidance']['canonical_research_scope'] == template.research_scope.model_dump(mode='json')
assert any(t.get('schema_feedback_delivered') for t in staff['stage_progress'])
continuation = read('native-data-continuation.json')
failed = read('native-failed-model-receipt.json')
assert continuation['failed_request_id'] == failed['receipt']['request_id']
assert continuation['receipt_sha256'] == failed['receipt']['receipt_sha256']
assert failed['receipt']['state'] == 'failed' and failed['receipt']['fault']['code'] == 'invalid_output'
assert continuation['genuine_read_history_preserved'] and continuation['original_frozen_turns_preserved']
assert continuation['failure_count_preserved'] == 2 and continuation['same_attempt_read_count'] == 43
assert not continuation['employee_decision_applied'] and not continuation['scientific_budget_reset']
assert continuation['model_requests_replayed'] == 0
followup_tests, followup_ci = read('schema-followup-tests.json'), read('schema-followup-ci.json')
assert followup_tests['source_commit'] == followup_ci['source_commit'] == '86aea8cdf03b5543666813c97689acb1e8158a6d'
assert followup_tests['passed'] == 78 and followup_tests['actual_postgresql']
assert followup_ci['conclusion'] == 'success' and followup_ci['passed'] == 1754
assert followup_ci['actual_postgresql'] and followup_ci['actual_temporal'] and followup_ci['lint_passed']
assert not followup_tests['deployed'] and not followup_ci['deployed']

names = ['server-baseline-before.json', 'program-preparation.json', 'candidate-program.json',
         'packet-binding.json', 'program-publication.json', 'owner-approval-state.json',
         'owner-ui-review.json', 'completed-turn-reconciliation.json', 'runtime-recovery.json',
         'worker-baseline-current.json', 'operational-repairs.json', staff_name]
names += ['source-ci.json', 'native-source-manifest.json', 'image-preparation.json',
          'native-worker-qualification.json', 'worker-registration.json', 'data-stage-hold.json',
          'failed-model-receipts.json', 'format-guidance.json', 'server-baseline-pre-native.json', 'runtime-locks-pre-native.json']
if activated:
    names += ['server-native-activation.json', 'native-installed-verification.json', 'native-data-resume.json']
names += ['artifact-schema-guidance.json', 'native-failed-model-receipt.json', 'native-data-continuation.json']
names += ['schema-followup-tests.json', 'schema-followup-ci.json']
names += ['worker-presence-current.json']
print(json.dumps({
    'schema_version': 1, 'verified_at': datetime.now(UTC).isoformat(), 'verification_kind': 'public_receipt_consistency',
    'company_code_commit': native_source if activated else preparation['company_commit'],
    'qualified_native_source': native_source, 'native_runtime_activated': activated,
    'actual_native_output_observed': any(t.get('output_contract') == 'research_stage_v1' and t['response_provider'] == 'codex'
                                        for t in staff['stage_progress']),
    'scoped_artifact_schema_feedback_delivered': True,
    'same_attempt_event_failure_continuation_recorded': True,
    'qualified_schema_followup_source': followup_ci['source_commit'],
    'schema_followup_deployed': False,
    'program_id': current['id'], 'program_digest': digest,
    'actual_owner_approval_event_id': current['approval_event_id'], 'actual_prior_cancellation_event_id': cancellation['owner_event_id'],
    'actual_owner_gate_recorded': True, 'native_owner_ui_action_delegated_by_user': True,
    'manual_human_click_claimed': False, 'inherited_origins': 12, 'cumulative_scientific_trial_limit': 4,
    'actual_employee_stage_states': [{'stage': s['stage'], 'actor': s['agent'], 'state': s['state']} for s in staff['actual_stages']],
    'actual_employee_review_finished': all(any(s['stage'] == stage and s['state'] == 'completed'
        for s in staff['actual_stages']) for stage in ('program_proposal', 'program_data', 'program_selection')),
    'staff_review_scope': 'Initial proposed task data assessment and independent task selection.',
    'actual_task_decisions': [{'id': t['id'], 'data_decision': (t['data_assessment'] or {}).get('decision'),
        'selection_decision': (t['decision'] or {}).get('decision'), 'mission_id': t['mission_id']} for t in staff['actual_tasks']],
    'actual_revision_feedback_delivered': staff['followup_feedback'],
    'followup_research_in_progress': any(s['state'] == 'running' for s in staff['actual_stages']),
    'fresh_worker_authenticated_polling': staff['worker_authenticated_polling'],
    'operator_granted_staff_data_admission': False, 'scientific_trials_started': 0,
    'fresh_full_backup_success_claimed': activated, 'secrets_read_or_printed': False,
    'evidence_sha256': {name: hashlib.sha256((base / name).read_bytes()).hexdigest() for name in names},
    'operator_helper_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(base.glob('*.py'))},
}, ensure_ascii=False, indent=2))

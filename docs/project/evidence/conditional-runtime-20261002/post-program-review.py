"""Publish a fresh canonical draft only after the prior program's signed cancellation."""

import json
import sys
from datetime import UTC, datetime

from quant_company.company import Company, fingerprint, stable
from quant_company.config import Settings
from quant_company.research.program_contracts import ResearchProgram
from quant_company.research.program_controller import program_tool
from quant_company.research.programs import ProgramStore
from quant_company.research.scientific_lineages import history

company = Company(Settings())
assert company.settings.company_code_commit == '231d6ba0755f658f167636a8ea2c3ef65b6af562'
spec = ResearchProgram.model_validate_json(sys.argv[1])
digest = fingerprint(spec.model_dump(mode='json'))
assert digest == 'be0d940bf5904c417d213898617b3f6c0fed19d2bfddd78460ec315e92b216af'
project_id = '9aac0de4-2b97-5195-a720-287d324234f3'
old_id = 'e06537d3-fac3-5c8c-bf25-ddabb3c7e282'
task_id = stable('operator-program-approval-request:' + project_id + ':' + digest)
instruction = 'INTENT-v8 operator-authorized exact canonical draft; a separate signed Slack approval grants research authority.'
with company.db.transaction() as connection:
    project = company._project(connection, project_id)
    assert project['revision'] == 5 and project['status'] == 'active'
    assert project['owner_user'] == 'U0C250E23NW' and project['channel'] == 'C0C2B9EUEGM'
    assert project['thread_ts'] == '1789633942.673909'
    old = ProgramStore(company).snapshot(connection, old_id)
    assert old['state'] == 'cancelled', 'signed_prior_cancellation_required'
    cancellation = connection.execute("""SELECT e.detail->>'owner_event_id' AS event_key FROM events e
        JOIN inbound i ON i.event_key=e.detail->>'owner_event_id' AND i.project_id=e.project_id
        WHERE e.project_id=%s AND e.kind='research_program_cancel' AND e.detail->>'program_id'=%s
        ORDER BY e.created_at DESC LIMIT 1""", (project_id, old_id)).fetchone()
    assert cancellation and cancellation['event_key'].startswith('slack:T0C1YRDRPNF:C0C2B9EUEGM:')
    assert not connection.execute("SELECT 1 FROM research_programs WHERE project_id=%s AND state='active'",
        (project_id,)).fetchone(), 'competing_active_program'
    for envelope in spec.envelopes:
        preimage, preimage_digest = history(connection, project['id'], envelope.template.scientific_lineage)
        assert preimage_digest == envelope.template.scientific_lineage.history_digest
    connection.execute("""INSERT INTO tasks(id,project_id,agent,instruction,revision,kind,status)
        VALUES(%s,%s,'director',%s,5,'operator_program_draft','pending') ON CONFLICT DO NOTHING""",
        (task_id, project_id, instruction))
    task = connection.execute('SELECT * FROM tasks WHERE id=%s FOR UPDATE', (task_id,)).fetchone()
    assert task['agent'] == 'director' and task['kind'] == 'operator_program_draft'
    assert task['instruction'] == instruction and task['revision'] == project['revision']
    snapshot = program_tool(company, connection, project, task,
        {'action': 'program_draft', 'spec': spec.model_dump(mode='json')})
    assert snapshot['manifest_digest'] == digest and snapshot['state'] == 'draft'
    assert snapshot['approval_event_id'] is None
    connection.execute("UPDATE tasks SET status='completed',result=%s WHERE id=%s",
        ('Canonical draft queued for a fresh signed Slack owner decision.', task_id))
    company._event(connection, 'operator_conditional_program_draft_requested', {
        'program_id': snapshot['id'], 'program_digest': digest,
        'prior_signed_cancellation': cancellation['event_key'], 'owner_approval_required': True}, project_id)
    binding = connection.execute("""SELECT b.id,b.message_id,o.status,o.sent_ts FROM research_approval_bindings b
        JOIN outbox o ON o.id=b.message_id WHERE b.target_kind='program' AND b.target_id=%s
        AND b.manifest_digest=%s ORDER BY b.created_at DESC LIMIT 1""", (snapshot['id'], digest)).fetchone()
    assert binding
print(json.dumps({'schema_version': 1, 'observed_at': datetime.now(UTC).isoformat(),
    'program_id': snapshot['id'], 'program_digest': digest, 'program_state': 'draft',
    'binding_id': str(binding['id']), 'message_id': str(binding['message_id']),
    'outbox_status': binding['status'], 'sent_ts': binding['sent_ts'], 'owner_program_approved': False,
    'actual_staff_decisions_observed': False, 'scientific_trials_started': 0}))

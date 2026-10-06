"""Read genuine current origins and prepare a revised signature preimage; grant no authority."""

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import quant_company
from quant_company.company import Company, as_json, fingerprint
from quant_company.config import Settings
from quant_company.research.data_evidence import load_packets
from quant_company.research.program_contracts import ResearchProgram
from quant_company.research.scientific_lineages import history

source = json.loads(sys.argv[2])
root = Path(quant_company.__file__).parent
actual = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
          for path in root.rglob('*') if path.is_file() and '__pycache__' not in path.parts}
assert actual == source['company_files'], 'actual_imported_source_changed'
company = Company(Settings(research_data_evidence_file=Path('/state/research/provisioned/data-evidence/registry.json')))
assert company.settings.company_code_commit == source['source_commit'] == '231d6ba0755f658f167636a8ea2c3ef65b6af562'
old = ResearchProgram.model_validate_json(sys.argv[1])
old_digest = fingerprint(old.model_dump(mode='json'))
assert old_digest == 'be0d940bf5904c417d213898617b3f6c0fed19d2bfddd78460ec315e92b216af'
assert len(old.envelopes) == 1
project_id = '9aac0de4-2b97-5195-a720-287d324234f3'
prior_id = 'e06537d3-fac3-5c8c-bf25-ddabb3c7e282'
with company.db.transaction() as conn:
    conn.execute('SET TRANSACTION READ ONLY')
    project = conn.execute('SELECT * FROM projects WHERE id=%s', (project_id,)).fetchone()
    assert project['revision'] == 5 and project['status'] == 'active'
    assert project['owner_user'] == 'U0C250E23NW' and project['channel'] == 'C0C2B9EUEGM'
    assert project['thread_ts'] == '1789633942.673909'
    prior = conn.execute('SELECT * FROM research_programs WHERE id=%s', (prior_id,)).fetchone()
    assert prior['state'] == 'cancelled'
    cancel = conn.execute("""SELECT e.detail->>'owner_event_id' AS event_key FROM events e
        JOIN inbound i ON i.event_key=e.detail->>'owner_event_id' AND i.project_id=e.project_id
        WHERE e.project_id=%s AND e.kind='research_program_cancel' AND e.detail->>'program_id'=%s
        ORDER BY e.created_at DESC LIMIT 1""", (project_id, prior_id)).fetchone()
    assert cancel and cancel['event_key'].startswith('slack:T0C1YRDRPNF:C0C2B9EUEGM:')
    assert not conn.execute("SELECT 1 FROM research_programs WHERE project_id=%s AND state='active'", (project_id,)).fetchone()
    origins = conn.execute("""SELECT t.id AS task_id,t.program_id,t.digest AS task_digest FROM research_program_tasks t
        JOIN research_programs p ON p.id=t.program_id WHERE p.project_id=%s ORDER BY t.id""", (project_id,)).fetchall()
    assert 10 <= len(origins) <= 20
    refs = as_json(origins)
    previous_refs = old.envelopes[0].template.scientific_lineage.originating_task_refs
    assert {str(ref.task_id) for ref in previous_refs} <= {ref['task_id'] for ref in refs}
    candidate = old.model_dump(mode='json')
    candidate['title'] = f'고정 빈티지 ETF 조건부 개발 연구 — 기존 {len(refs)}개 과제 이력 포함'
    lineage = candidate['envelopes'][0]['template']['scientific_lineage']
    lineage['originating_task_refs'] = refs
    provisional = ResearchProgram.model_validate(candidate)
    preimage, preimage_digest = history(conn, project['id'], provisional.envelopes[0].template.scientific_lineage)
    lineage['history_digest'] = preimage_digest
    new = ResearchProgram.model_validate(candidate)
    _, check = history(conn, project['id'], new.envelopes[0].template.scientific_lineage)
    assert check == preimage_digest
    company._check_sources(conn, project_id, new.source_ids)
packets = load_packets(company, {'manifest_digest': old_digest}, {e.name: e for e in old.envelopes})
assert set(packets) == {'etf_strategy'}
digest = fingerprint(new.model_dump(mode='json'))
packet = packets['etf_strategy'][0].model_dump(mode='json')
packet['program_digest'] = digest
without_history = new.model_dump(mode='json')
without_history['title'] = old.title
without_history['envelopes'][0]['template']['scientific_lineage'] = old.envelopes[0].template.scientific_lineage.model_dump(mode='json')
assert without_history == old.model_dump(mode='json'), 'policy_data_budget_or_permissions_changed'
print(json.dumps({'schema_version': 1, 'observed_at': datetime.now(UTC).isoformat(),
    'company_commit': source['source_commit'], 'actual_company_tree_verified': True,
    'prior_signed_cancellation': cancel['event_key'], 'previous_program_digest': old_digest,
    'program_digest': digest, 'spec': new.model_dump(mode='json'), 'packet': packet,
    'history': preimage, 'history_digest': preimage_digest, 'origin_count': len(refs),
    'only_scientific_history_revised': True, 'profiles_inputs_engine_policy_budget_preserved': True,
    'owner_program_approved': False, 'scientific_trials_started': 0, 'performance_computed': False}, ensure_ascii=False))

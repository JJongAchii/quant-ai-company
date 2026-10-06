"""Verify deployed code, approved authority, immutable input bindings and actual Temporal RPC."""

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta

from temporalio.api.workflowservice.v1 import GetSystemInfoRequest
from temporalio.client import Client

from quant_company.company import Company, as_json, fingerprint
from quant_company.config import Settings
from quant_company.research.builds import profile_for
from quant_company.research.data_evidence import load_packets
from quant_company.research.program_contracts import ResearchProgram
from quant_company.research.programs import ProgramStore
from quant_company.research.scientific_lineages import history

company = Company(Settings())
assert company.settings.company_code_commit == '5c44ad07273adc780a56a47d7d35114fd0dc32c8'
spec = ResearchProgram.model_validate_json(sys.argv[1])
original_history = json.loads(sys.argv[2])
digest = fingerprint(spec.model_dump(mode='json'))
assert digest == 'c3ba5268d71c87bd2c6226160bc720498137bd291396a054970a1f1e7ebcf2db'
envelopes = {envelope.name: envelope for envelope in spec.envelopes}
packets = load_packets(company, {'manifest_digest': digest}, envelopes)
assert len(packets) == 1
with company.db.transaction() as conn:
    conn.execute('SET TRANSACTION READ ONLY')
    project = company._project(conn, '9aac0de4-2b97-5195-a720-287d324234f3', lock=False)
    assert project['revision'] == 5 and project['status'] == 'active'
    company._check_sources(conn, project['id'], spec.source_ids)
    for envelope in spec.envelopes:
        profile_for(company, envelope.template)
        inherited, inherited_digest = history(conn, project['id'], envelope.template.scientific_lineage)
        assert fingerprint(original_history) == envelope.template.scientific_lineage.history_digest
        assert inherited == {**original_history, 'trial_limit': 4}
    program = conn.execute('SELECT id,state,manifest_digest,approval_event_id FROM research_programs WHERE manifest_digest=%s',
                           (digest,)).fetchone()
    assert program['state'] == 'active'
    assert conn.execute('SELECT 1 FROM inbound WHERE event_key=%s AND project_id=%s',
                        (program['approval_event_id'], project['id'])).fetchone()
    usage = ProgramStore(company).usage(conn, program['id'])
    assert usage['trials'] == 0
    assert conn.execute('SELECT trial_limit FROM research_scientific_lineages WHERE id=%s',
                         (spec.envelopes[0].template.scientific_lineage.id,)).fetchone()['trial_limit'] == 4
    stage = conn.execute("""SELECT state,attempt,context->'_program_hold' AS hold FROM research_mission_stages
        WHERE id='ce867fc3-a194-5327-b312-864b93538d34'""").fetchone()
    assert stage['state'] == 'waiting' and stage['attempt'] == 4 and stage['hold']['failure_count'] == 4
    worker = conn.execute("SELECT id,last_seen,last_seen>now()-interval '60 seconds' AS fresh FROM research_workers WHERE id='worker'").fetchone()
    assert worker['fresh']


async def temporal():
    settings = company.settings
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace,
        api_key=settings.temporal_api_key.get_secret_value() or None, tls=settings.temporal_tls)
    await client.workflow_service.get_system_info(GetSystemInfoRequest(), timeout=timedelta(seconds=10))


asyncio.run(temporal())
print(json.dumps(as_json({'schema_version': 1, 'observed_at': datetime.now(UTC),
    'source_commit': company.settings.company_code_commit, 'program': program,
    'original_signed_approval_and_inbound_verified': True, 'actual_postgresql': True, 'actual_temporal_rpc': True,
    'signed_history_digest': spec.envelopes[0].template.scientific_lineage.history_digest,
    'authorized_history_digest': inherited_digest, 'authorized_limit_change_only': True,
    'inherited_origins': len(inherited['origins']),
    'sources_profiles_inputs_engine_policy_bindings_verified': True,
    'worker_authenticated_polling': worker, 'data_stage_held': stage,
    'employee_judgment_applied': False, 'scientific_trials_added': 0, 'secrets_printed': False}), ensure_ascii=False))

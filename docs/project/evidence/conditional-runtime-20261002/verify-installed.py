"""Read installed files, genuine PG history and Temporal RPC; grant no research authority."""

import asyncio
import json
import os
import sys
from datetime import timedelta

from temporalio.api.workflowservice.v1 import GetSystemInfoRequest
from temporalio.client import Client

from quant_company.company import Company, fingerprint
from quant_company.config import Settings
from quant_company.research.builds import profile_for
from quant_company.research.data_evidence import load_packets
from quant_company.research.program_contracts import ResearchProgram
from quant_company.research.programs import ProgramStore
from quant_company.research.scientific_lineages import history

settings = Settings()
company = Company(settings)
spec = ResearchProgram.model_validate_json(sys.argv[1])
digest = fingerprint(spec.model_dump(mode='json'))
envelopes = {envelope.name: envelope for envelope in spec.envelopes}
packets = load_packets(company, {'manifest_digest': digest}, envelopes)
assert len(packets) == 1 and len(envelopes) == 1
with company.db.transaction() as connection:
    project = company._project(connection, '9aac0de4-2b97-5195-a720-287d324234f3')
    assert project['revision'] == 5 and project['status'] == 'active'
    company._check_sources(connection, project['id'], spec.source_ids)
    for envelope in spec.envelopes:
        profile_for(company, envelope.template)
        preimage, preimage_digest = history(connection, project['id'], envelope.template.scientific_lineage)
        assert preimage_digest == envelope.template.scientific_lineage.history_digest
    old = ProgramStore(company).snapshot(connection, 'e06537d3-fac3-5c8c-bf25-ddabb3c7e282')
    assert old['usage']['trials'] == 0
    assert not connection.execute("SELECT 1 FROM research_programs WHERE spec->>'schema_version'='2'").fetchone()
    assert not connection.execute('SELECT 1 FROM research_scientific_lineages').fetchone()


async def temporal():
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace,
        api_key=settings.temporal_api_key.get_secret_value() or None, tls=settings.temporal_tls)
    await client.workflow_service.get_system_info(GetSystemInfoRequest(), timeout=timedelta(seconds=10))


asyncio.run(temporal())
print(json.dumps({'program_digest': digest, 'company_commit': os.environ['COMPANY_CODE_COMMIT'],
    'packet_count': len(packets), 'old_program_readable': True, 'old_program_state': old['state'],
    'exact_sources_profiles_reports_inputs_engine_verified': True, 'history': preimage,
    'history_digest': preimage_digest, 'genuine_postgresql_history': True, 'actual_temporal_rpc': True,
    'scientific_trials_added': 0, 'staff_data_admission_granted': False, 'owner_program_approved': False}))

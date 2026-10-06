"""Append the verified unchanged packet under the refreshed program digest; preserve every prior packet."""

import fcntl
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

base = Path(__file__).resolve().parent
prepared = json.loads((base / 'program-preparation.json').read_bytes())
assert prepared['program_digest'] == 'c3ba5268d71c87bd2c6226160bc720498137bd291396a054970a1f1e7ebcf2db'
registry = Path('/var/lib/quant-company/research/provisioned/data-evidence/registry.json')
with Path('/var/lib/quant-company/.backup.lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    raw = registry.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == '13810e6a6f613d84ed67f2941617471a79e48887bba88b9cb06996364ce31860'
    guard = json.loads(subprocess.check_output(['docker', 'exec', 'quant-company-postgres-1', 'psql', '-U',
        'postgres', '-d', 'quant_company', '-qAt', '-v', 'ON_ERROR_STOP=1', '-c', """SELECT json_build_object(
        'prior_cancelled', EXISTS(SELECT 1 FROM research_programs WHERE id='e06537d3-fac3-5c8c-bf25-ddabb3c7e282' AND state='cancelled'),
        'active_program', EXISTS(SELECT 1 FROM research_programs WHERE project_id='9aac0de4-2b97-5195-a720-287d324234f3' AND state='active'),
        'active_jobs', EXISTS(SELECT 1 FROM research_jobs WHERE state IN ('queued','claimed','running','uncertain','cancel_requested')))"""], timeout=30))
    assert guard == {'prior_cancelled': True, 'active_program': False, 'active_jobs': False}
    old = json.loads(raw)
    assert len(old['packets']) == 4
    previous = next(p for p in old['packets'] if p['program_digest'] == prepared['previous_program_digest'])
    packet = prepared['packet']
    rebound = dict(previous, program_digest=prepared['program_digest'])
    assert rebound == packet, 'input_engine_reports_profile_policy_or_scope_changed'
    assert not any(p['program_digest'] == prepared['program_digest'] for p in old['packets'])
    backup = base / 'registry-before.json'
    with backup.open('xb') as output:
        output.write(raw)
    backup.chmod(0o444)
    value = dict(old, packets=[*old['packets'], packet])
    encoded = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()
    stat = registry.stat()
    temporary = registry.with_name('.registry-conditional-20261006.json')
    with temporary.open('xb') as output:
        os.fchown(output.fileno(), stat.st_uid, stat.st_gid)
        os.fchmod(output.fileno(), stat.st_mode & 0o777)
        output.write(encoded)
        output.flush()
        os.fsync(output.fileno())
    assert registry.read_bytes() == raw
    os.replace(temporary, registry)
    directory_fd = os.open(registry.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    current = json.loads(registry.read_bytes())
    assert current['packets'][:-1] == old['packets'] and current['packets'][-1] == packet
print(json.dumps({'schema_version': 1, 'observed_at': datetime.now(UTC).isoformat(),
    'program_digest': prepared['program_digest'], 'packet_count': len(current['packets']),
    'registry_before_sha256': hashlib.sha256(raw).hexdigest(),
    'registry_after_sha256': hashlib.sha256(registry.read_bytes()).hexdigest(),
    'all_prior_packets_preserved': True, 'only_program_binding_added': True,
    'inputs_engine_reports_profiles_policy_preserved': True, 'owner_program_approved': False}))

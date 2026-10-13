"""Verify the retained runtime and actual authenticated worker polling, then release our pause."""

import argparse
import fcntl
import hashlib
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

STATE = Path('/var/lib/quant-company')


def sql(query):
    return json.loads(subprocess.check_output(['docker', 'exec', 'quant-company-postgres-1', 'psql',
        '-U', 'postgres', '-d', 'quant_company', '-qAt', '-v', 'ON_ERROR_STOP=1', '-c', query], timeout=30))


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker-receipt', type=Path, required=True)
    args = parser.parse_args()
    worker = json.loads(args.worker_receipt.read_bytes())
    assert worker['phase'] == 'active' and worker['source_commit'] == '231d6ba0755f658f167636a8ea2c3ef65b6af562'
    assert worker['config_sha256'] == '4d0b0c82e567c6a269b17610abaa282a01b1f18b261272600c076e28ee496c57'
    commit = worker['source_commit']
    journal = STATE / 'releases' / ('conditional-cutover-' + commit + '-03.json')
    with (STATE / '.backup.lock').open('a') as lock:
        wait_deadline = time.monotonic() + 600
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                assert time.monotonic() < wait_deadline, 'shared_lock_wait_timeout'
                print(json.dumps({'phase': 'waiting_for_shared_lock'}), flush=True)
                time.sleep(10)
        record = json.loads(journal.read_bytes())
        assert record['phase'] == 'active_awaiting_3070_verification'
        assert Path('/opt/quant-company/current').resolve().name == commit
        deadline = time.monotonic() + 60
        while True:
            heartbeat = sql("SELECT json_build_object('last_seen',last_seen,'after_activation',last_seen>="
                + literal(worker['started_at']) + "::timestamptz,'fresh',last_seen>now()-interval '60 seconds') "
                "FROM research_workers WHERE id='worker'")
            if heartbeat and heartbeat['after_activation'] and heartbeat['fresh']:
                break
            assert time.monotonic() < deadline, 'actual_worker_polling_not_observed'
            time.sleep(2)
        for name, expected in record['after'].items():
            row = json.loads(subprocess.check_output(['docker', 'inspect', name], timeout=30))[0]
            assert row['Id'] == expected['id'] and row['Image'] == expected['image_id']
            if name in {'quant-company-' + service + '-1' for service in record['selected_services']}:
                assert row['State']['Running']
            if name == 'quant-company-api-1':
                assert row['State']['Health']['Status'] == 'healthy'
        assert hashlib.sha256((STATE / 'research/provisioned/data-evidence/registry.json').read_bytes()).hexdigest() == record['registry_after_sha256']
        assert hashlib.sha256((STATE / 'config/research-profiles.json').read_bytes()).hexdigest() == record['profiles_after_sha256']
        result = sql("WITH c AS (UPDATE runtime_control SET paused_until=NULL,reason=NULL WHERE id=1 AND reason="
            + literal(record['pause_reason']) + ' AND paused_until=' + literal(record['pause_until'])
            + "::timestamptz RETURNING id) SELECT json_build_object('released',(SELECT count(*) FROM c))")
        assert result['released'] == 1, 'pause_ownership_changed'
        record.update(phase='active_verified', worker_activation=worker, authenticated_worker_polling=heartbeat,
            pause_released_at=datetime.now(UTC).isoformat(), scientific_authority_granted=False)
        journal.write_text(json.dumps(record, sort_keys=True, indent=2) + '\n')
        print(json.dumps(record), flush=True)


if __name__ == '__main__':
    main()

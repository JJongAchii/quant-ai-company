import fcntl
import hashlib
import importlib.util
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location('storage_cleanup',
                                             Path(__file__).parents[1] / 'deploy/storage_cleanup.py')
storage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(storage)
NOW = datetime(2026, 10, 6, tzinfo=UTC)


def image(number, age, repository='quant-company'):
    return {'id': f'sha256:{number:064x}', 'created': (NOW - timedelta(days=age)).isoformat(),
            'tags': [f'{repository}:{number:040x}']}


def archive(root, age, number):
    stamp = NOW - timedelta(days=age)
    path = root / f'company-{stamp:%Y%m%dT%H%M%SZ}-{number:08x}.tar.gz'
    path.write_bytes(f'backup-{number}'.encode())
    os.utime(path, (stamp.timestamp(), stamp.timestamp()))
    checksum = path.with_name(path.name + '.sha256')
    checksum.write_text(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name + '\n')
    return path


def test_image_selection_preserves_live_pinned_current_and_full_rollback():
    rows = [image(n, n + 6) for n in range(1, 9)]
    cfg = {'PINNED_CODEX_RUNTIME_IMAGE': rows[4]['tags'][0], 'RELEASE_COMMIT': f'{6:040x}'}
    eligible = storage.image_candidates(rows, {rows[6]['id']}, cfg, NOW, [f'{8:040x}'])
    assert {row['id'] for row in eligible} == {rows[3]['id']}


def test_image_selection_keeps_three_distinct_images_and_recent_stages():
    rows = [image(n, n + 5) for n in range(1, 6)]
    rows[0]['tags'].append(f'quant-company:{99:040x}')
    rows.append(image(9, 2, 'quant-company-codex'))
    eligible = storage.image_candidates(rows, set(), {}, NOW)
    assert {row['id'] for row in eligible} == {rows[3]['id'], rows[4]['id']}


def test_image_selection_excludes_unknown_repositories_and_mixed_aliases():
    rows = [image(n, n + 7) for n in range(1, 5)]
    rows[3]['tags'].append('third-party:latest')
    rows.extend([image(8, 20, 'quant-company-audit-base'), image(9, 20, 'postgres')])
    assert storage.image_candidates(rows, set(), {}, NOW) == []


def test_missing_configured_pin_aborts_before_any_deletion():
    with pytest.raises(ValueError, match='configured_image_pin_missing'):
        storage.image_candidates([image(1, 20)], set(), {'PINNED_COMPANY_WORKER_IMAGE': 'missing:pin'}, NOW)


def test_backup_selection_keeps_minimum_three_and_seven_days(tmp_path):
    paths = [archive(tmp_path, age, n) for n, age in enumerate((2, 8, 9, 10, 11))]
    assert {row['name'] for row in storage.backup_candidates(tmp_path, NOW)} == {
        paths[3].name, paths[4].name,
    }


def test_backup_selection_preserves_all_recent_snapshots_and_manual_files(tmp_path):
    for n in range(10):
        archive(tmp_path, 1 + n / 24, n)
    (tmp_path / 'manual-cutover.tar.gz').write_bytes(b'manual')
    (tmp_path / 'research-cutover').mkdir()
    assert storage.backup_candidates(tmp_path, NOW) == []


def test_backup_selection_rejects_linked_root_and_does_not_follow_archives(tmp_path):
    root = tmp_path / 'backups'
    root.mkdir()
    outside = archive(tmp_path, 20, 1)
    (root / outside.name).symlink_to(outside)
    assert storage.backup_candidates(root, NOW) == []
    link = tmp_path / 'linked-root'
    link.symlink_to(root)
    with pytest.raises(ValueError, match='unsafe_backup_directory'):
        storage.backup_candidates(link, NOW)


def test_local_backup_corruption_is_never_sent_to_remote_verification(tmp_path, monkeypatch):
    path = archive(tmp_path, 20, 1)
    path.write_bytes(b'corrupted')
    monkeypatch.setattr(storage, 'remote_digest', lambda *args: pytest.fail('Unexpected S3 read'))
    with pytest.raises(ValueError, match='local_backup_checksum_mismatch'):
        storage.verified_backup(path, storage.fingerprint(path), {'BACKUP_S3_URI': 's3://backups/company/'})
    assert path.exists()


def test_remote_backup_mismatch_preserves_the_local_archive(tmp_path, monkeypatch):
    path = archive(tmp_path, 20, 1)
    monkeypatch.setattr(storage, 'remote_digest', lambda *args: '0' * 64)
    with pytest.raises(ValueError, match='remote_backup_checksum_mismatch'):
        storage.verified_backup(path, storage.fingerprint(path), {'BACKUP_S3_URI': 's3://backups/company/'})
    assert path.exists()


def test_backup_change_during_remote_read_is_detected(tmp_path, monkeypatch):
    path = archive(tmp_path, 20, 1)
    expected = storage.digest(path)

    def changed_remote(*args):
        path.write_bytes(b'changed while downloading')
        return expected

    monkeypatch.setattr(storage, 'remote_digest', changed_remote)
    with pytest.raises(ValueError, match='backup_changed_during_verification'):
        storage.verified_backup(path, storage.fingerprint(path), {'BACKUP_S3_URI': 's3://backups/company/'})
    assert path.exists()


def test_verified_backup_records_exact_s3_hash_and_size(tmp_path, monkeypatch):
    path = archive(tmp_path, 20, 1)
    expected = storage.digest(path)
    reads = []

    def matching_remote(uri, cfg):
        reads.append(uri)
        return expected

    monkeypatch.setattr(storage, 'remote_digest', matching_remote)
    result = storage.verified_backup(path, storage.fingerprint(path), {'BACKUP_S3_URI': 's3://backups/company/'})
    assert result['sha256'] == expected
    assert result['bytes'] == path.stat().st_size
    assert reads == ['s3://backups/company/' + path.name]


def test_image_newly_referenced_by_container_is_preserved(monkeypatch):
    row = image(1, 20)
    monkeypatch.setattr(storage, 'guard_config', lambda report: None)
    monkeypatch.setattr(storage, 'containers', lambda: [{'image': row['id']}])
    monkeypatch.setattr(storage, 'run', lambda *args: pytest.fail('Unexpected image removal'))
    assert storage.remove_image(row, {}) is False


def test_configuration_drift_aborts_cleanup(tmp_path, monkeypatch):
    (tmp_path / 'config').mkdir()
    config = tmp_path / 'config/runtime.env'
    config.write_text('RELEASE_COMMIT=old\n')
    original = storage.digest(config)
    config.write_text('RELEASE_COMMIT=new\n')
    monkeypatch.setattr(storage, 'STATE', tmp_path)
    with pytest.raises(ValueError, match='runtime_configuration_changed'):
        storage.guard_config({'runtime_sha256': original})


def test_locked_backup_defers_without_docker_or_s3_calls(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(storage, 'STATE', tmp_path)
    monkeypatch.setattr(storage.sys, 'argv', ['storage_cleanup.py', '--apply'])
    monkeypatch.setattr(storage, 'make_plan', lambda *args: pytest.fail('Unexpected plan while locked'))
    with (tmp_path / '.backup.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        storage.main()
    assert json.loads(capsys.readouterr().out)['status'] == 'deferred'


def test_configuration_change_during_plan_is_rejected_before_image_removal(tmp_path, monkeypatch):
    (tmp_path / 'config').mkdir()
    (tmp_path / 'backups').mkdir()
    config = tmp_path / 'config/runtime.env'
    config.write_text(f'RELEASE_COMMIT={1:040x}\n')
    (tmp_path / 'config/roles.json').write_text('[]')
    original_reader = storage.configuration

    def changing_config(path):
        old = original_reader(path)
        config.write_text(f'RELEASE_COMMIT={1:040x}\nPINNED_COMPANY_WORKER_IMAGE=quant-company:{4:040x}\n')
        return old

    monkeypatch.setattr(storage, 'STATE', tmp_path)
    monkeypatch.setattr(storage, 'configuration', changing_config)
    monkeypatch.setattr(storage, 'images', lambda: [image(n, n + 6) for n in range(1, 5)])
    monkeypatch.setattr(storage, 'containers', lambda: [{'id': 'container', 'image': image(1, 7)['id']}])
    plan = storage.make_plan([])
    with pytest.raises(ValueError, match='runtime_configuration_changed'):
        storage.guard_config(plan)


def test_apply_removes_only_verified_old_backups_and_persists_receipt(tmp_path, monkeypatch):
    (tmp_path / 'config').mkdir()
    (tmp_path / 'backups').mkdir()
    (tmp_path / 'config/runtime.env').write_text(f'RELEASE_COMMIT={1:040x}\n')
    (tmp_path / 'config/roles.json').write_text('[]')
    (tmp_path / 'config/backup.env').write_text('BACKUP_S3_URI=s3://backups/company/\n')
    paths = [archive(tmp_path / 'backups', age, n) for n, age in enumerate((2, 8, 9, 10, 11))]
    good, bad = paths[3:]
    stable = [{'id': 'live', 'image': image(1, 1)['id']}]
    monkeypatch.setattr(storage, 'STATE', tmp_path)
    monkeypatch.setattr(storage, 'containers', lambda: stable)
    monkeypatch.setattr(storage, 'images', lambda: [image(1, 1)])
    monkeypatch.setattr(storage, 'run', lambda *args, **kwargs: 'Total: 0B')
    monkeypatch.setattr(storage, 'remote_digest',
                        lambda uri, cfg: storage.digest(good) if uri.endswith(good.name) else '0' * 64)
    report = storage.make_plan([])
    report['backups'] = storage.backup_candidates(tmp_path / 'backups', NOW)
    receipt = tmp_path / 'receipt.json'
    storage.apply_plan(report, receipt)
    assert not good.exists()
    assert not good.with_name(good.name + '.sha256').exists()
    assert all(path.exists() for path in paths[:3] + [bad])
    saved = json.loads(receipt.read_text())
    assert [row['name'] for row in saved['deleted_backups']] == [good.name]
    assert saved['skipped'] == [{'backup': bad.name, 'reason': 'remote_backup_checksum_mismatch'}]
    assert saved['runtime_preserved'] and saved['roles_preserved'] and saved['containers_preserved']

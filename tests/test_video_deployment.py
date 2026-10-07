"""Read-only Compose expansion; Docker engine and cloud deployment are not required."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


def test_video_profile_defaults_off_and_isolates_media_credentials():
    if not shutil.which('docker'):
        pytest.skip('Docker Compose CLI required for configuration expansion')
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['docker', 'compose', '--env-file', str(root/'deploy/.env.example'),
        '-f', str(root/'deploy/compose.yaml'), '-f', str(root/'deploy/video.compose.yaml'), '--profile', 'video',
        'config', '--format', 'json'], check=True, capture_output=True, text=True)
    services = json.loads(result.stdout)['services']
    for name in ('api', 'worker', 'dispatch', 'slack-socket', 'news-worker', 'video-worker'):
        for flag in ('VIDEO_ENABLED', 'VIDEO_UPLOAD_ENABLED', 'VIDEO_PUBLISH_ENABLED'):
            assert services[name]['environment'][flag] == 'false'
    media = services['video-worker']
    mounts = {m['target'] for m in media['volumes']}
    assert '/state/media-auth' in mounts and '/state/video' in mounts
    assert not any('slack' in str(s) or 'lake' in str(s) for s in media['secrets'])
    assert media['read_only'] and media['pids_limit'] == 256
    for name, service in services.items():
        if name != 'video-worker':
            assert '/state/media-auth' not in {m['target'] for m in service.get('volumes', [])}
    model = services['codex-runtime']
    assert not any(name.startswith('VIDEO_') for name in model['environment'])

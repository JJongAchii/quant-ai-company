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
        '-f', str(root/'deploy/compose.yaml'), '-f', str(root/'deploy/video.compose.yaml'), '--profile', 'video', '--profile', 'claude',
        'config', '--format', 'json'], check=True, capture_output=True, text=True)
    services = json.loads(result.stdout)['services']
    for name in ('api', 'worker', 'dispatch', 'slack-socket', 'news-worker', 'video-worker'):
        for flag in ('VIDEO_ENABLED', 'VIDEO_UPLOAD_ENABLED', 'VIDEO_PUBLISH_ENABLED', 'VIDEO_ANALYTICS_ENABLED'):
            assert services[name]['environment'][flag] == 'false'
    media = services['video-worker']
    mounts = {m['target'] for m in media['volumes']}
    assert '/state/media-auth' in mounts and '/state/video' in mounts and '/state/video-assets' in mounts
    assert next(m for m in media['volumes'] if m['target'] == '/state/video-assets')['read_only']
    assert media['environment']['VIDEO_TEMPLATE'] == 'motion-v2'
    assert not any('slack' in str(s) or 'lake' in str(s) for s in media['secrets'])
    assert media['read_only'] and media['pids_limit'] == 256
    for name, service in services.items():
        if name != 'video-worker':
            assert '/state/media-auth' not in {m['target'] for m in service.get('volumes', [])}
    assert media['environment']['VIDEO_MODEL'] == 'claude-opus-5-5' and media['environment']['VIDEO_VOICE'] == 'Vincent'
    assert media['environment']['VIDEO_REASONING_EFFORT'] == 'xhigh' and media['environment']['VIDEO_REVIEW_EFFORT'] == 'max'
    assert media['environment']['VIDEO_MODEL_RUNTIME_URL'] == 'http://claude-runtime:8080'
    # The dispatcher gates analytics posts and the API/socket accept lesson clicks with the same channel setting.
    assert {services[n]['environment']['VIDEO_ANALYTICS_CHANNEL_ID'] for n in ('dispatch', 'slack-socket', 'video-worker')} == {''}
    assert set(media['depends_on']) == {'postgres', 'claude-runtime'}
    assert not any(k.startswith('MODEL_PROVIDER') for k in media['environment'])
    # The Slack dispatcher delivers the owner's files: it reads the video volume and nothing else of the media worker.
    dispatch = services['dispatch']
    video = next(m for m in dispatch['volumes'] if m['target'] == '/state/video')
    assert video['read_only'] and dispatch['environment']['VIDEO_ARTIFACT_DIR'] == '/state/video'
    assert 'media-auth' not in str(dispatch['volumes']) and 'model_runtime_token' not in str(dispatch.get('secrets'))
    for name in ('codex-runtime', 'claude-runtime'):
        assert not any(key.startswith('VIDEO_') for key in services[name]['environment'])


def test_render_overlay_gives_only_the_media_worker_the_fargate_settings_and_credentials(tmp_path):
    if not shutil.which('docker'):
        pytest.skip('Docker Compose CLI required for configuration expansion')
    root = Path(__file__).resolve().parents[1]
    command = ['docker', 'compose', '--env-file', str(root/'deploy/.env.example'), '-f', str(root/'deploy/compose.yaml'),
               '-f', str(root/'deploy/video.compose.yaml'), '-f', str(root/'deploy/video-render.compose.yaml'),
               '--profile', 'video', '--profile', 'claude']
    missing = subprocess.run(command + ['config', '--format', 'json'], capture_output=True, text=True)
    # Never a half-configured remote backend (Compose names whichever required variable it checks first).
    assert missing.returncode != 0 and 'required variable VIDEO_RENDER_' in missing.stderr
    env = tmp_path / 'render.env'
    env.write_text('VIDEO_RENDER_BUCKET=render-bucket\nVIDEO_RENDER_CLUSTER=render\nVIDEO_RENDER_TASK_DEFINITION=render:2\n'
                   'VIDEO_RENDER_SUBNETS=["subnet-a","subnet-b"]\nVIDEO_RENDER_SECURITY_GROUP=sg-render\n')
    result = subprocess.run(command[:4] + ['--env-file', str(env)] + command[4:] + ['config', '--format', 'json'],
                            check=True, capture_output=True, text=True)
    services = json.loads(result.stdout)['services']
    media = services['video-worker']
    assert media['environment']['VIDEO_RENDER_BACKEND'] == 'remote'
    assert media['environment']['VIDEO_RENDER_SUBNETS'] == '["subnet-a","subnet-b"]'
    assert media['environment']['AWS_SHARED_CREDENTIALS_FILE'] == '/run/secrets/render_aws_credentials'
    assert {s['source'] for s in media['secrets']} == {'database_password', 'temporal_api_key', 'model_runtime_token',
                                                        'render_aws_credentials'}
    for name, service in services.items():
        if name != 'video-worker':
            assert 'render_aws_credentials' not in str(service.get('secrets', []))
            assert not any(k.startswith('VIDEO_RENDER_') and k != 'VIDEO_RENDER_WORKERS' for k in service.get('environment', {}))

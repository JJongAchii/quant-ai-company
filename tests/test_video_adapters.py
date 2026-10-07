"""Protocol simulations only: no real Runway charge, OAuth grant or YouTube write."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from quant_company.config import Settings
from quant_company.video.auth import RunwayTokenStorage, write_secret
from quant_company.video.render import verify_artifacts
from quant_company.video.runway import RunwaySpeech
from quant_company.video.youtube import UPLOAD, YouTube, session_url

from .test_video import artifact_fixture, video_plan


@pytest.fixture
def upload_job(tmp_path):
    artifacts = artifact_fixture(tmp_path / 'job')
    return {'artifacts': artifacts, 'plan': video_plan().model_dump(), 'youtube_id': 'fixture1234',
            'policy': {'youtube_channel': 'UCfixture'}, 'publish_deadline': datetime.now(UTC)+timedelta(hours=1),
            'upload_session': UPLOAD+'/videos?upload_id=fixture'}


def test_private_credentials_reject_links_or_world_readable_directory(tmp_path):
    root = tmp_path/'private'
    root.mkdir(mode=0o700)
    path = root/'token.json'
    write_secret(path, {'fixture': 'synthetic-token'})
    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(path.read_text()) == {'fixture': 'synthetic-token'}
    alias = root/'alias.json'
    alias.symlink_to(path)
    with pytest.raises(ValueError):
        write_secret(alias, {})
    root.chmod(0o755)
    with pytest.raises(ValueError):
        write_secret(path, {})


def test_token_reader_rejects_overly_broad_permissions(tmp_path):
    path = tmp_path/'runway-tokens.json'
    path.write_text('{}')
    path.chmod(0o644)
    with pytest.raises(ValueError):
        RunwayTokenStorage(tmp_path).read(path.name, object)


async def test_runway_web_workspace_credit_and_task_contract():
    runway = RunwaySpeech('unused-private-directory')
    calls = []
    async def fake(name, args):
        calls.append((name, args))
        if name == 'whoami':
            return {'authenticated': True, 'team': {'id': 123}, 'credits': {'total': 1500}}
        if name == 'generate_speech':
            return {'taskId': 'known-task'}
        return {'status': 'SUCCEEDED', 'url': 'https://example.com/audio.mp3'}
    runway.call = fake
    assert (await runway.account(123))['credits']['total'] == 1500
    with pytest.raises(ValueError):
        await runway.account(456)
    assert (await runway.submit('합성 음성 테스트', 'Leslie', 'eleven_multilingual_v2'))['taskId'] == 'known-task'
    assert await runway.completed('known-task') == 'https://example.com/audio.mp3'
    assert calls[-1] == ('get_task', {'id': 'known-task'})
    assert calls[-2][1]['languageCode'] == 'ko'
    with pytest.raises(ValueError):
        await runway.submit('fixture', 'Leslie', 'paid-dev-model')


@pytest.mark.parametrize('url', ['http://www.googleapis.com/upload/youtube/v3/videos',
    'https://evil.example/upload/youtube/v3/videos', 'https://www.googleapis.com@evil.example/upload/youtube/v3/videos',
    'https://www.googleapis.com/upload/youtube/v3/captions', 'https://www.googleapis.com:444/upload/youtube/v3/videos'])
def test_upload_session_cannot_redirect_credentials(url):
    with pytest.raises(ValueError):
        session_url(url)


async def test_youtube_always_initiates_private_and_resumes_without_reinsert(upload_job, tmp_path):
    calls = []
    def fake(request):
        calls.append(request)
        if request.url.path == '/youtube/v3/channels':
            return httpx.Response(200, json={'items': [{'id': 'UCfixture'}]})
        if request.method == 'POST':
            assert json.loads(request.content)['status']['privacyStatus'] == 'private'
            return httpx.Response(200, headers={'Location': upload_job['upload_session']})
        if request.headers['content-range'].startswith('bytes */'):
            return httpx.Response(308, headers={'Range': 'bytes=0-3'})
        assert request.headers['content-range'].startswith('bytes 4-')
        assert request.content == b'lated-video'
        return httpx.Response(200, json={'id': 'fixture1234'})
    youtube = YouTube(Settings(video_artifact_dir=tmp_path), token='synthetic', transport=httpx.MockTransport(fake))
    assert await youtube.initiate(upload_job) == upload_job['upload_session']
    assert await youtube.upload(upload_job) == 'fixture1234'
    assert len([r for r in calls if r.method == 'POST']) == 1
    assert all(r.headers['authorization'] == 'Bearer synthetic' for r in calls)


async def test_expired_upload_session_never_creates_replacement(upload_job, tmp_path):
    calls = []
    def fake(request):
        calls.append(request)
        return httpx.Response(404)
    youtube = YouTube(Settings(video_artifact_dir=tmp_path), token='synthetic', transport=httpx.MockTransport(fake))
    with pytest.raises(httpx.HTTPStatusError):
        await youtube.upload(upload_job)
    assert len(calls) == 1 and calls[0].method == 'PUT'


async def test_unverified_project_cannot_claim_successful_public_release(upload_job, tmp_path):
    calls = []
    def fake(request):
        calls.append(request)
        if request.url.path == '/youtube/v3/channels':
            return httpx.Response(200, json={'items': [{'id': 'UCfixture'}]})
        if request.method == 'GET':
            return httpx.Response(200, json={'items': [{'snippet': {'channelId': 'UCfixture'},
                'status': {'privacyStatus': 'private', 'selfDeclaredMadeForKids': False},
                'processingDetails': {'processingStatus': 'succeeded'}}]})
        return httpx.Response(200, json={'status': {'privacyStatus': 'private'}})
    youtube = YouTube(Settings(video_artifact_dir=tmp_path), token='synthetic', transport=httpx.MockTransport(fake))
    with pytest.raises(ValueError, match='did not confirm'):
        await youtube.publish(upload_job)
    assert json.loads(calls[-1].content)['status']['privacyStatus'] == 'public'


async def test_deadline_checked_immediately_before_public_write(upload_job, tmp_path):
    calls = []
    def fake(request):
        calls.append(request)
        if request.url.path == '/youtube/v3/channels':
            return httpx.Response(200, json={'items': [{'id': 'UCfixture'}]})
        upload_job['publish_deadline'] = datetime.now(UTC)-timedelta(seconds=1)
        return httpx.Response(200, json={'items': [{'snippet': {'channelId': 'UCfixture'},
            'status': {'privacyStatus': 'private'}, 'processingDetails': {'processingStatus': 'succeeded'}}]})
    youtube = YouTube(Settings(video_artifact_dir=tmp_path), token='synthetic', transport=httpx.MockTransport(fake))
    with pytest.raises(ValueError, match='deadline'):
        await youtube.publish(upload_job)
    assert all(request.method == 'GET' for request in calls)


def test_artifact_hash_change_detects_modified_file_and_manifest(upload_job, tmp_path):
    manifest = upload_job['artifacts']
    root = verify_artifacts(manifest, tmp_path)
    manifest['duration'] += 1
    with pytest.raises(ValueError, match='manifest'):
        verify_artifacts(manifest, tmp_path)
    manifest['duration'] -= 1
    (root/'video.mp4').write_bytes(b'changed')
    with pytest.raises(ValueError, match='artifact changed'):
        verify_artifacts(manifest, tmp_path)

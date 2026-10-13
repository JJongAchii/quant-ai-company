"""Automatic server path: delivered body → episode → files in the brief's Slack thread.

Real PostgreSQL. Model, Runway, the renderer and Slack's HTTP API are explicitly simulated fixtures; nothing here
calls Slack, Runway or YouTube."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from quant_company.config import Settings
from quant_company.slack import SlackOutbox
from quant_company.video.contracts import digest
from quant_company.video.episode import fresh_library
from quant_company.video.render import checksum
from quant_company.video.runner import VideoRunner
from quant_company.video.store import VideoStore

from .test_briefing import brief  # noqa: F401
from .test_video import (  # noqa: F401
    SimulatedProvider,
    SimulatedSpeech,
    SimulatedYouTube,
    deliver_brief_root,
    new_job,
    video,
)


@pytest.fixture
def slack_video(video):  # noqa: F811
    store, clock = video
    store.settings.video_upload_enabled = False  # default: owner delivery in Slack, YouTube API off
    store.settings.video_publish_enabled = False
    return store, clock


def final_files(root):
    root.mkdir(parents=True, exist_ok=True)
    files = {'video.mp4': b'simulated-video' * 1000, 'thumbnail.png': b'simulated-thumbnail',
             'subtitles.srt': b'1\n00:00:00,000 --> 00:00:01,000\nSynthetic caption\n',
             'upload.txt': '[제목]\nS&P 500 1.92% 상승 | 9월 22일 아침 · 오늘의 증시story\n'.encode()}
    for name, content in files.items():
        (root/name).write_bytes(content)
    manifest = {'directory': str(root), 'qa_passed': True, 'human_watched': False, 'duration': 272.,
                'title': 'S&P 500 1.92% 상승 | 9월 22일 아침 · 오늘의 증시story',
                'files': {n: checksum(root/n) for n in files}}
    manifest['digest'] = digest(manifest)
    return manifest


class FileRenderer:
    def render(self, job, plan, directory, paths):
        return final_files(directory)


class FakeSlack:
    """Slack external upload API simulation with per-step failure switches."""

    def __init__(self, complete='ok', url_status=200, rate_limit=0):
        self.calls, self.complete, self.url_status, self.rate_limit, self.uploaded = [], complete, url_status, rate_limit, {}

    def __call__(self, request):
        path = request.url.path
        self.calls.append(path)
        if path == '/api/files.getUploadURLExternal':
            if self.rate_limit:
                self.rate_limit -= 1
                return httpx.Response(429, headers={'retry-after': '1'})
            form = dict(httpx.QueryParams(request.content.decode()))
            identity = 'F' + str(len(self.uploaded) + 1)
            self.uploaded[identity] = {'name': form['filename'], 'length': int(form['length'])}
            return httpx.Response(200, json={'ok': True, 'file_id': identity,
                                             'upload_url': 'https://files.slack.com/upload/v1/' + identity})
        if path.startswith('/upload/v1/'):
            identity = path.rsplit('/', 1)[-1]
            assert len(request.content) == self.uploaded[identity]['length']
            return httpx.Response(self.url_status, text='OK')
        if path == '/api/files.completeUploadExternal':
            form = dict(httpx.QueryParams(request.content.decode()))
            self.completed = form
            if self.complete == 'timeout':
                raise httpx.ReadTimeout('simulated lost completion')
            if self.complete == 'rejected':
                return httpx.Response(200, json={'ok': False, 'error': 'not_in_channel'})
            return httpx.Response(200, json={'ok': True, 'files': [{'id': f['id']} for f in json.loads(form['files'])]})
        if path == '/api/chat.postMessage':
            return httpx.Response(200, json={'ok': True, 'ts': f'100.{len(self.calls):03}', 'channel': 'CQUANT'})
        raise AssertionError(path)


async def produce(store, provider=None, speech=None, youtube=None, ticks=9):
    provider, speech, youtube = provider or SimulatedProvider(), speech or SimulatedSpeech(), youtube or SimulatedYouTube()
    for _ in range(ticks):
        await VideoRunner(store.company, provider, speech, FileRenderer(), youtube).tick()
    return provider, speech, youtube


def quiet_other_outbox(store, keep):
    with store.db.transaction() as conn:
        conn.execute("UPDATE outbox SET status='delivered',sent_ts='100.000' WHERE status='pending' AND id<>%s", (keep,))


async def send(store, transport):
    outbox = SlackOutbox(store.company, {'market_brief': {'bot_token': 'fake'}}, httpx.MockTransport(transport))
    return await outbox.send_one()


def test_upload_to_youtube_is_off_by_default():
    settings = Settings()
    assert settings.video_upload_enabled is False and settings.video_publish_enabled is False
    assert settings.video_voice == 'Vincent'
    assert (settings.video_monthly_credit_limit, settings.video_episode_credit_limit) == (1500, 100)


def test_job_source_is_the_delivered_body_only(brief, slack_video):  # noqa: F811
    store, _ = slack_video
    job = new_job(brief, slack_video)
    assert set(job['source']) == {'id', 'day', 'cutoff', 'project_id', 'owner_user', 'channel', 'policy_digest', 'body',
                                  'quality', 'edition_kind', 'format'}
    with store.db.transaction() as conn:
        rendered = conn.execute('SELECT rendered FROM brief_editions WHERE id=%s', (job['edition_id'],)).fetchone()['rendered']
    assert job['source']['body'] == rendered and job['policy']['delivery'] == 'slack'
    assert job['policy']['voice'] == 'Vincent'


@pytest.mark.parametrize('quality', [{'reduced': True}, {'rejected': {'x': 'semantic_review'}, 'reduced': True}])
def test_reduced_but_substantive_body_still_makes_a_video(brief, slack_video, quality):  # noqa: F811
    store, clock = slack_video
    job = new_job(brief, slack_video)
    with store.db.transaction() as conn:
        conn.execute('DELETE FROM video_jobs')
        edition = conn.execute('SELECT * FROM brief_editions WHERE id=%s', (job['edition_id'],)).fetchone()
        edition['state'] = 'ready'
        edition['quality'].update(quality)
        assert store.enqueue(conn, edition, clock['at'])


async def test_fallback_notice_makes_no_video_and_one_thread_alert(brief, slack_video):  # noqa: F811
    store, clock = slack_video
    job = new_job(brief, slack_video)
    with store.db.transaction() as conn:
        conn.execute('DELETE FROM video_jobs')
        edition = conn.execute('SELECT * FROM brief_editions WHERE id=%s', (job['edition_id'],)).fetchone()
        edition.update(state='writing', proposal=None)
        edition['quality'] = {**edition['quality'], 'fallback': 'deadline', 'substantive': False}
        assert store.enqueue(conn, edition, clock['at']) is None
        conn.execute("UPDATE projects SET thread_ts=NULL WHERE id=%s", (job['source']['project_id'],))
    assert store.skip_notices() == 0  # the brief itself is not in Slack yet
    deliver_brief_root(store, job['edition_id'])
    provider = SimulatedProvider()
    await VideoRunner(store.company, provider=provider).tick()
    await VideoRunner(store.company, provider=provider).tick()
    with store.db.transaction() as conn:
        alerts = conn.execute("SELECT text,project_id FROM messages WHERE kind='video_status' AND text NOT LIKE '영상 제작 현황%' ORDER BY created_at DESC").fetchall()
        outbox = conn.execute("SELECT thread_ts FROM outbox o JOIN messages m ON m.id=o.id WHERE m.kind='video_status'").fetchone()
        assert conn.execute('SELECT count(*) AS n FROM video_jobs').fetchone()['n'] == 0
    assert len(alerts) == 1 and '영상 제작 안 함' in alerts[0]['text'] and 'deadline' in alerts[0]['text']
    assert outbox['thread_ts'] == '100.000' and not provider.calls


async def test_episode_waits_for_the_brief_receipt_before_any_spend(brief, slack_video):  # noqa: F811
    store, _ = slack_video
    job = new_job(brief, slack_video, deliver=False)
    provider = SimulatedProvider()
    assert (await VideoRunner(store.company, provider=provider).tick())['state'] == 'idle'
    assert not provider.calls and store.get(job['id'])['state'] == 'queued'
    deliver_brief_root(store, job['edition_id'])
    assert (await VideoRunner(store.company, provider=provider).tick())['state'] == 'reviewing'


async def test_files_are_delivered_into_the_brief_thread_with_receipts(brief, slack_video, monkeypatch):  # noqa: F811
    store, _ = slack_video
    job = new_job(brief, slack_video)
    monkeypatch.setattr('quant_company.video.runner.download_audio', lambda url, path: path.write_bytes(b'simulated-speech'))
    _, speech, youtube = await produce(store)
    current = store.get(job['id'])
    assert current['state'] == 'delivering', current['error']
    assert len(speech.calls) == 3 and all(c['voice'] == 'Vincent' for c in speech.calls)
    assert not youtube.calls  # VIDEO_UPLOAD_ENABLED=false never touches the YouTube API
    quiet_other_outbox(store, current['delivery_message_id'])
    slack = FakeSlack()
    assert await send(store, slack)
    assert store.get(job['id'])['state'] == 'delivered'
    names = [f['name'] for f in slack.uploaded.values()]
    assert names == ['아침브리핑_20260922.mp4', '아침브리핑_20260922_썸네일.png', '아침브리핑_20260922.srt',
                     '아침브리핑_20260922_업로드문안.txt']
    assert slack.completed['channel_id'] == 'CQUANT' and slack.completed['thread_ts'] == '100.000'
    assert '영상 준비 완료' in slack.completed['initial_comment'] and 'upload.txt' in slack.completed['initial_comment']
    with store.db.transaction() as conn:
        delivery = conn.execute('SELECT * FROM video_deliveries WHERE job_id=%s', (job['id'],)).fetchone()
        status = conn.execute('SELECT status FROM outbox WHERE id=%s', (current['delivery_message_id'],)).fetchone()['status']
    assert delivery['state'] == 'delivered' and status == 'delivered'
    assert [f['id'] for f in delivery['receipt']['files']] == ['F1', 'F2', 'F3', 'F4']
    assert delivery['receipt']['completed']['file_ids'] == ['F1', 'F2', 'F3', 'F4']
    calls = len(slack.calls)
    assert not await send(store, slack) and len(slack.calls) == calls  # nothing is sent twice


async def test_rejected_upload_posts_the_server_path_and_can_be_redelivered(brief, slack_video, monkeypatch):  # noqa: F811
    from quant_company.video.recovery import retry

    store, _ = slack_video
    job = new_job(brief, slack_video)
    monkeypatch.setattr('quant_company.video.runner.download_audio', lambda url, path: path.write_bytes(b'simulated-speech'))
    await produce(store)
    current = store.get(job['id'])
    quiet_other_outbox(store, current['delivery_message_id'])
    assert await send(store, FakeSlack(complete='rejected'))
    failed = store.get(job['id'])
    assert failed['state'] == 'delivery_failed' and failed['error'] == 'not_in_channel'
    with store.db.transaction() as conn:
        notice = conn.execute("SELECT o.text,o.thread_ts FROM outbox o JOIN messages m ON m.id=o.id "
                              "WHERE m.kind='video_status' AND o.text NOT LIKE '영상 제작 현황%'").fetchone()
    assert '영상 파일 전달 실패' in notice['text'] and failed['artifacts']['directory'] in notice['text']
    assert notice['thread_ts'] == '100.000'
    assert retry(store, str(job['id']), '채널 초대 후 같은 파일을 새 시도로 다시 전달함')['state'] == 'delivering'
    again = store.get(job['id'])
    assert again['delivery_message_id'] != current['delivery_message_id']
    quiet_other_outbox(store, again['delivery_message_id'])
    assert await send(store, FakeSlack())
    assert store.get(job['id'])['state'] == 'delivered'


async def test_lost_completion_is_uncertain_and_never_replayed(brief, slack_video, monkeypatch):  # noqa: F811
    store, _ = slack_video
    job = new_job(brief, slack_video)
    monkeypatch.setattr('quant_company.video.runner.download_audio', lambda url, path: path.write_bytes(b'simulated-speech'))
    await produce(store)
    current = store.get(job['id'])
    quiet_other_outbox(store, current['delivery_message_id'])
    slack = FakeSlack(complete='timeout')
    assert await send(store, slack)
    assert store.get(job['id'])['state'] == 'delivery_uncertain'
    with store.db.transaction() as conn:
        status = conn.execute('SELECT status FROM outbox WHERE id=%s', (current['delivery_message_id'],)).fetchone()['status']
        notice = conn.execute("SELECT text FROM messages WHERE kind='video_status' AND text NOT LIKE '영상 제작 현황%' ORDER BY created_at DESC").fetchone()['text']
    assert status == 'uncertain' and '자동으로 다시 올리지 않습니다' in notice
    calls = slack.calls.count('/api/files.completeUploadExternal')
    await send(store, slack)
    assert slack.calls.count('/api/files.completeUploadExternal') == calls


async def test_rate_limit_before_sharing_retries_later(brief, slack_video, monkeypatch):  # noqa: F811
    store, _ = slack_video
    job = new_job(brief, slack_video)
    monkeypatch.setattr('quant_company.video.runner.download_audio', lambda url, path: path.write_bytes(b'simulated-speech'))
    await produce(store)
    current = store.get(job['id'])
    quiet_other_outbox(store, current['delivery_message_id'])
    slack = FakeSlack(rate_limit=1)
    assert await send(store, slack)
    assert store.get(job['id'])['state'] == 'delivering'
    with store.db.transaction() as conn:
        conn.execute("UPDATE outbox SET next_at=now() WHERE id=%s", (current['delivery_message_id'],))
    assert await send(store, slack)
    assert store.get(job['id'])['state'] == 'delivered'


async def test_runway_unavailable_alerts_without_any_charge_or_fallback(brief, slack_video):  # noqa: F811
    from quant_company.video.recovery import retry
    from quant_company.video.runner import VideoRunner as Runner

    store, _ = slack_video
    job = new_job(brief, slack_video)
    await Runner(store.company, SimulatedProvider()).tick()
    await Runner(store.company, SimulatedProvider()).tick()
    assert store.get(job['id'])['state'] == 'synthesizing'
    speech = SimulatedSpeech()

    async def offline(workspace):
        raise httpx.ConnectError('simulated Runway outage')
    speech.account = offline
    assert (await Runner(store.company, speech=speech).tick())['state'] == 'blocked'
    current = store.get(job['id'])
    assert current['error'] == 'runway_unavailable' and not speech.calls
    with store.db.transaction() as conn:
        alert = conn.execute("SELECT text FROM messages WHERE kind='video_status' AND text NOT LIKE '영상 제작 현황%' ORDER BY created_at DESC").fetchone()['text']
        assert conn.execute("SELECT count(*) AS n FROM video_effects WHERE kind='speech'").fetchone()['n'] == 0
    assert 'Runway' in alert and '유료 API로 전환하지 않았습니다' in alert and 'video-auth runway' in alert
    with store.db.transaction() as conn:
        conn.execute("UPDATE video_jobs SET publish_deadline=now()+interval '1 hour' WHERE id=%s", (job['id'],))
    assert retry(store, str(job['id']), 'Runway 재로그인 후 무료 계정 확인부터 다시 진행')['state'] == 'synthesizing'


@pytest.mark.parametrize('limit,code', [('monthly', 'credit_cap'), ('balance', 'runway_balance')])
async def test_credit_checks_run_before_the_first_take(brief, slack_video, limit, code):  # noqa: F811
    store, _ = slack_video
    job = new_job(brief, slack_video)
    await VideoRunner(store.company, SimulatedProvider()).tick()
    await VideoRunner(store.company, SimulatedProvider()).tick()
    speech = SimulatedSpeech()
    if limit == 'monthly':
        store.settings.video_monthly_credit_limit = 2
    else:
        async def poor(workspace):
            return {'credits': {'total': 1}}
        speech.account = poor
    assert (await VideoRunner(store.company, speech=speech).tick())['state'] == 'blocked'
    assert store.get(job['id'])['error'] == code and not speech.calls
    with store.db.transaction() as conn:
        assert '크레딧' in conn.execute("SELECT text FROM messages WHERE kind='video_status' AND text NOT LIKE '영상 제작 현황%' ORDER BY created_at DESC").fetchone()['text']


def test_image_rotation_skips_last_seven_days_and_falls_back_to_oldest():
    now = datetime(2026, 10, 10, 8, tzinfo=UTC)
    library = {'wafer': {'tags': ['반도체·메모리']}, 'fab': {'tags': ['반도체·메모리']}, 'oil': {'tags': ['원유·해운']}}
    assert fresh_library(library, {}, now) == library
    usage = {'wafer': now - timedelta(days=2), 'fab': now - timedelta(days=9)}
    assert set(fresh_library(library, usage, now)) == {'fab', 'oil'}
    usage = {'wafer': now - timedelta(days=2), 'fab': now - timedelta(days=5), 'oil': now - timedelta(days=1)}
    assert set(fresh_library(library, usage, now)) == {'fab', 'oil'}  # oldest of each fully recent tag


def test_usage_log_records_thumbnail_and_scene_slots(brief, slack_video):  # noqa: F811
    from types import SimpleNamespace

    store, clock = slack_video
    job = new_job(brief, slack_video)
    plan = SimpleNamespace(thumbnail_image='wafer', scenes=[SimpleNamespace(data={'image': {'asset': 'fab'}})])
    library = {'wafer': {'file': 'img/semiconductor-memory.jpg'}, 'fab': {'file': 'img/fab.jpg'}, 'oil': {'file': 'img/oil.jpg'}}
    store.record_assets(job, plan, library)
    store.record_assets(job, plan, library)
    with store.db.transaction() as conn:
        rows = conn.execute('SELECT asset_id,file,slot FROM video_asset_usage ORDER BY slot').fetchall()
    assert [(r['asset_id'], r['slot']) for r in rows] == [('fab', 'scene'), ('wafer', 'thumbnail')]
    assert rows[1]['file'] == 'img/semiconductor-memory.jpg'
    usage = store.asset_usage()
    assert set(usage) == {'wafer', 'fab'}
    assert set(fresh_library({k: {'tags': ['x']} for k in library}, usage, clock['at'] + timedelta(days=1))) == {'oil'}


def test_status_lists_deliveries_and_skips(brief, slack_video):  # noqa: F811
    store, _ = slack_video
    new_job(brief, slack_video)
    status = VideoStore(store.company).status()
    assert status['upload_enabled'] is False and status['deliveries'] == [] and status['skips'] == []


async def test_dispatcher_crash_mid_upload_becomes_uncertain_with_notice(brief, slack_video, monkeypatch):  # noqa: F811
    store, _ = slack_video
    job = new_job(brief, slack_video)
    monkeypatch.setattr('quant_company.video.runner.download_audio', lambda url, path: path.write_bytes(b'simulated-speech'))
    await produce(store)
    current = store.get(job['id'])
    with store.db.transaction() as conn:
        # Sender lease expiry after a crash (SlackOutbox.recover_uncertain).
        conn.execute("UPDATE outbox SET status='uncertain',error='sender_lease_expired' WHERE id=%s",
                     (current['delivery_message_id'],))
    assert store.sync_deliveries() == 1 and store.sync_deliveries() == 0
    assert store.get(job['id'])['state'] == 'delivery_uncertain'
    with store.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM messages WHERE kind='video_status' AND text NOT LIKE '영상 제작 현황%'").fetchone()['n'] == 1


async def test_script_writer_is_not_offered_images_used_in_the_last_week(brief, slack_video, tmp_path):  # noqa: F811
    from types import SimpleNamespace

    from .test_video_motion import EpisodeProvider

    store, clock = slack_video
    store.settings.video_template = 'motion-v2'
    store.settings.video_asset_dir = tmp_path / 'assets'
    (tmp_path / 'assets').mkdir()
    shelf = {'wafer': {'tags': ['반도체·메모리'], 'kind': '일러스트', 'file': 'img/semiconductor-memory.jpg'},
             'fab': {'tags': ['반도체·메모리'], 'kind': '일러스트', 'file': 'img/fab.jpg'}}
    (tmp_path / 'assets' / 'manifest.json').write_text(json.dumps(shelf, ensure_ascii=False))
    job = new_job(brief, slack_video)
    store.record_assets(job, SimpleNamespace(thumbnail_image='wafer', scenes=[]), shelf, at=clock['at'] - timedelta(days=1))
    provider = EpisodeProvider()
    await VideoRunner(store.company, provider=provider).tick()
    prompt = provider.calls[0].prompt
    assert '"fab"' in prompt and '"wafer"' not in prompt


@pytest.mark.parametrize('quality,expected', [
    ({'fallback': None, 'issue_count': 1}, 'full'),                                   # ordinary body
    ({'fallback': 'deadline', 'original_facts': [{'id': 'f1'}], 'issue_count': 2}, 'full'),  # reduced, facts kept
    ({'fallback': 'editorial_review_withheld', 'original_facts': [{'id': 'f1'}], 'issue_count': 0}, 'facts'),
    ({'fallback': 'deadline', 'issue_count': 0}, None),                               # notice only: skip + alert
    # PR #132 salvage shapes (briefing/store.py _salvage): the deadline draft ships with review_incomplete,
    ({'fallback': None, 'review_incomplete': True, 'salvage_reason': 'timeout', 'issue_count': 2}, 'full'),
    # the fact-list body carries the original-fact count from render(facts=...),
    ({'fallback': 'timeout', 'original_facts': 7, 'review_incomplete': True, 'issue_count': 0}, 'facts'),
    # and an edition where nothing was collected stays a notice.
    ({'fallback': 'timeout', 'review_incomplete': True, 'issue_count': 0}, None),
])
def test_trigger_rule_for_always_body_editions(brief, slack_video, quality, expected):  # noqa: F811
    store, clock = slack_video
    job = new_job(brief, slack_video)
    with store.db.transaction() as conn:
        conn.execute('DELETE FROM video_jobs')
        edition = conn.execute('SELECT * FROM brief_editions WHERE id=%s', (job['edition_id'],)).fetchone()
        edition.update(state='committed', proposal=None, quality={**edition['quality'], 'original_facts': None, **quality})
        identity = store.enqueue(conn, edition, clock['at'])
        skipped = conn.execute('SELECT reason FROM video_skips WHERE edition_id=%s', (edition['id'],)).fetchone()
    if expected is None:
        assert identity is None and skipped['reason'] == quality['fallback']
    else:
        assert identity and not skipped and store.get(identity)['source']['format'] == expected

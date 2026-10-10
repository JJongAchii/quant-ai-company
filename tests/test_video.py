"""Real PostgreSQL; model, speech, YouTube and Slack calls are explicitly simulated."""

import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient

from quant_company.api import create_app
from quant_company.company import PolicyError
from quant_company.config import Settings
from quant_company.contracts import AgentDecision, ProviderFault, ProviderRequest, ProviderResponse
from quant_company.slack import SlackIngress, SlackOutbox
from quant_company.video.contracts import VideoAction, VideoPlan, VideoReview, digest, validate_plan
from quant_company.video.render import checksum
from quant_company.video.runner import VideoRunner
from quant_company.video.store import UncertainEffect, VideoStore

from .test_briefing import brief, bundle, complete, proposal  # noqa: F401
from .test_slack import signed


def body_source(**extra):
    """The delivered brief body of the fixture edition: the only source a video may use."""
    from quant_company.briefing.editor import render

    parts, _ = render(proposal(), bundle())
    return {'body': parts, 'day': '2026-09-22', 'cutoff': '2026-09-22T07:30:00+09:00', 'edition_kind': 'am', **extra}


def video_plan():
    return VideoPlan(title='반도체 강세, 시장 전체로 이어질까', thumbnail='상승의 확산을 확인할 때',
        introduction='반도체 강세와 국채 수익률 하락의 관계, 아직 남아 있는 확인 조건을 살펴봅니다.',
        scenes=[{'heading': '오늘의 핵심', 'lines': ['반도체 주도 상승', '기술주 밖의 흐름은 혼조'],
                 'narration': '미국 증시는 반도체가 주도했지만 업종별 흐름은 엇갈렸습니다.', 'claim_ids': ['b001']},
                {'heading': '가능한 연결과 다른 설명', 'lines': ['금리 하락은 할인 부담을 낮출 수 있음', '반도체 자체 재료도 확인'],
                 'narration': '금리 하락은 성장주의 할인 부담을 낮출 수 있습니다. 반도체 자체 재료의 영향도 가능합니다.',
                 'claim_ids': ['b009', 'b010']},
                {'heading': '다음 확인 조건', 'lines': ['비기술 업종으로 강세가 이어지는지 확인'],
                 'narration': '시장의 강세가 비기술 업종으로 확산되는지 추가로 확인합니다.', 'claim_ids': ['b011']}],
        pinned_comment='어떤 업종에서 강세의 확산을 확인하고 계신가요?')


def good_review():
    return VideoReview(facts=True, numbers=True, conditions=True, coverage=True, readability=True, concerns=[])


@pytest.fixture
def video(brief, tmp_path, monkeypatch):  # noqa: F811
    upstream, clock = brief
    settings = upstream.company.settings
    settings.video_enabled = True
    settings.video_upload_enabled = True
    settings.video_publish_enabled = True
    settings.video_runway_workspace_id = 123
    settings.video_youtube_channel_id = 'UCfixture'
    settings.video_template = 'text-v1'  # text renderer contract; motion-v2 is covered in test_video_motion
    settings.video_artifact_dir = tmp_path / 'video'
    monkeypatch.setattr('quant_company.video.store.utcnow', lambda: clock['at'])
    return VideoStore(upstream.company), clock


def deliver_brief_root(store, edition_id, ts='100.000'):
    """Simulated Slack receipt of the brief's root post (the outbox records it as delivered)."""
    with store.db.transaction() as conn:
        root = conn.execute('SELECT id FROM brief_messages WHERE edition_id=%s AND part=0', (edition_id,)).fetchone()
        conn.execute("UPDATE outbox SET status='delivered',sent_ts=%s WHERE id=%s", (ts, root['id']))
        conn.execute('UPDATE projects SET thread_ts=%s WHERE id=(SELECT project_id FROM brief_editions WHERE id=%s)',
                     (ts, edition_id))


def new_job(upstream, video, deliver=True):
    complete(upstream)
    store, _ = video
    with store.db.transaction() as conn:
        job = conn.execute('SELECT * FROM video_jobs').fetchone()
        assert job, 'Delivered AM body must enqueue in the same flush transaction'
    if deliver:
        deliver_brief_root(store, job['edition_id'])
    return job


def artifact_fixture(root):
    root.mkdir(parents=True, exist_ok=True)
    files = {'video.mp4': b'simulated-video', 'thumbnail.png': b'simulated-thumbnail',
             'subtitles.srt': b'1\n00:00:00,000 --> 00:00:01,000\nSynthetic caption\n'}
    for name, content in files.items():
        (root/name).write_bytes(content)
    manifest = {'directory': str(root), 'qa_passed': True, 'human_watched': False, 'duration': 180.,
                'description': 'Synthetic fixture only', 'files': {n: checksum(root/n) for n in files}}
    manifest['digest'] = digest(manifest)
    return manifest


def prepare_approval(store, job):
    artifacts = artifact_fixture(store.settings.video_artifact_dir / str(job['id']))
    store.save(job, 'awaiting_approval', plan=video_plan().model_dump(mode='json'),
               review=good_review().model_dump(), artifacts=artifacts, artifact_digest=artifacts['digest'],
               youtube_id='fixture1234')
    with store.db.transaction() as conn:
        conn.execute('UPDATE projects SET thread_ts=%s WHERE id=%s', ('100.000', job['source']['project_id']))
    store.notice(job)
    current = store.get(job['id'])
    with store.db.transaction() as conn:
        conn.execute("UPDATE outbox SET status='delivered',sent_ts='100.001' WHERE id=%s", (current['review_message_id'],))
    return current


def action(job, selected='approve', **updates):
    return VideoAction(action=selected, version=job['version'], artifact_digest=job['artifact_digest'], **updates)


def interaction(job, credential, selected='approve'):
    value = json.dumps({'id': str(job['id']), 'version': job['version'], 'digest': job['artifact_digest']})
    return {'type': 'block_actions', 'team': {'id': 'TTEST'}, 'api_app_id': credential['app_id'],
        'user': {'id': 'UHUMAN'}, 'channel': {'id': 'CQUANT'},
        'message': {'user': credential['bot_user_id'], 'app_id': credential['app_id'], 'ts': '100.001'},
        'container': {'type': 'message', 'message_ts': '100.001', 'channel_id': 'CQUANT'},
        'actions': [{'action_id': 'video_'+selected, 'type': 'button', 'block_id': 'video-review:'+str(job['id']),
                     'text': {'type': 'plain_text', 'text': {'approve': '공개', 'revise': '수정', 'hold': '보류'}[selected]},
                     'value': value, 'action_ts': '100.002'}]}


def test_source_plan_numeric_binding_and_typed_scope():
    plan = video_plan()
    source = body_source()
    validate_plan(plan, source)
    computed = plan.model_copy(deep=True)
    computed.scenes[0].lines = ['S&P500 +1.92%']
    computed.scenes[0].narration = 'S&P500 지수는 전 거래일보다 +1.92% 상승했습니다.'
    computed.scenes[0].claim_ids = ['b003']
    validate_plan(computed, source)
    changed = plan.model_copy(deep=True)
    changed.scenes[0].narration += ' 수익은 999퍼센트 증가했습니다.'
    with pytest.raises(ValueError, match='Unbound number'):
        validate_plan(changed, source)
    changed = plan.model_copy(deep=True)
    changed.scenes[0].claim_ids = ['invented']
    with pytest.raises(ValueError, match='Unknown source'):
        validate_plan(changed, source)
    with pytest.raises(ValueError):
        ProviderRequest(request_id='company-other', model='fixture', prompt='fixture', output_contract='video_plan_v1')
    with pytest.raises(ValueError):
        ProviderRequest(request_id='video-test', model='fixture', prompt='fixture', output_contract='video_plan_v1', web_search=True)


def test_video_flags_fail_closed():
    assert not Settings().video_enabled
    with pytest.raises(ValueError):
        Settings(video_enabled=True)
    with pytest.raises(ValueError):
        Settings(video_publish_enabled=True)


def test_flush_transaction_freezes_source_and_enqueues_once(brief, video):  # noqa: F811
    store, clock = video
    job = new_job(brief, video)
    assert job['source_digest'] == digest(job['source'])
    assert job['state'] == 'queued'
    assert job['policy']['workspace_id'] == 123
    brief[0].flush()
    with store.db.transaction() as conn:
        assert conn.execute('SELECT count(*) AS n FROM video_jobs').fetchone()['n'] == 1
        assert conn.execute('SELECT state FROM brief_editions WHERE id=%s', (job['edition_id'],)).fetchone()['state'] == 'committed'
    assert clock['at'] < job['publish_deadline']


def test_video_enqueue_failure_never_holds_back_the_brief(brief, video, monkeypatch):  # noqa: F811
    store, _ = video

    def broken(self, conn, edition, at):
        conn.execute('SELECT 1 FROM no_such_video_table')

    monkeypatch.setattr(VideoStore, 'enqueue', broken)
    edition, _, _ = complete(brief)
    with store.db.transaction() as conn:
        row = conn.execute('SELECT state FROM brief_editions WHERE id=%s', (edition.id,)).fetchone()
        assert row['state'] == 'committed'
        assert conn.execute('SELECT count(*) AS n FROM video_jobs').fetchone()['n'] == 0
        skip = conn.execute('SELECT reason FROM video_skips WHERE edition_id=%s', (edition.id,)).fetchone()
    assert skip['reason'] == 'video_enqueue_failed'
    deliver_brief_root(store, edition.id)
    assert store.skip_notices() == 1
    with store.db.transaction() as conn:
        text = conn.execute("SELECT text FROM messages WHERE kind='video_status'").fetchone()['text']
    assert '서버 오류' in text and '본문은 그대로 발송' in text and '대체 공지' not in text


@pytest.mark.parametrize('change', ['preview', 'unknown_kind', 'unreviewed', 'unknown_quality', 'fallback', 'draft', 'late', 'disabled'])
def test_ineligible_briefs_never_enqueue(brief, video, change):  # noqa: F811
    store, clock = video
    job = new_job(brief, video)
    with store.db.transaction() as conn:
        conn.execute('DELETE FROM video_jobs')
        edition = conn.execute('SELECT * FROM brief_editions WHERE id=%s', (job['edition_id'],)).fetchone()
        edition['state'] = 'ready'
        if change == 'preview':
            edition['publish'] = False
        elif change == 'unknown_kind':
            edition['kind'] = 'weekly'  # am and pm both produce videos; nothing else does
        elif change == 'unreviewed':
            edition['review'] = None
            edition['quality'] = {}
        elif change == 'unknown_quality':
            edition['quality'] = {}
        elif change == 'fallback':
            edition['quality'] = {**edition['quality'], 'fallback': 'deadline', 'substantive': False}
        elif change == 'draft':
            edition['quality']['unreviewed_draft_preserved'] = True
        elif change == 'late':
            clock['at'] = job['publish_deadline']
        elif change == 'disabled':
            store.settings.video_enabled = False
        assert store.enqueue(conn, edition, clock['at']) is None


def test_receipts_concurrency_cost_reservation_and_no_uncertain_replay(brief, video):  # noqa: F811
    store, _ = video
    job = new_job(brief, video)
    def reserve():
        try:
            store.begin_effect(job, 'speech-0', {'text': 'fixture'}, 'speech', 60)
            return 'started'
        except UncertainEffect:
            return 'uncertain'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: reserve(), range(2))) == ['started', 'uncertain']
    with pytest.raises(PolicyError, match='budget'):
        store.begin_effect(job, 'speech-1', {}, 'speech', 41)
    store.finish_effect(job, 'speech-0', {'taskId': 'already-created'})
    assert store.begin_effect(job, 'speech-0', {'text': 'fixture'}, 'speech', 60) == {'taskId': 'already-created'}
    with pytest.raises(PolicyError, match='input changed'):
        store.begin_effect(job, 'speech-0', {'text': 'changed'}, 'speech', 60)


def test_monthly_and_company_model_limits_are_shared(brief, video):  # noqa: F811
    store, _ = video
    job = new_job(brief, video)
    store.settings.video_monthly_credit_limit = 2
    with pytest.raises(PolicyError, match='budget'):
        store.begin_effect(job, 'speech-0', {}, 'speech', 3)
    store.settings.company_max_daily_turns = 1
    with store.db.transaction() as conn:
        conn.execute('INSERT INTO daily_usage(day,reserved) VALUES(CURRENT_DATE,1) ON CONFLICT(day) DO UPDATE SET reserved=1')
    with pytest.raises(PolicyError, match='allowance'):
        store.begin_effect(job, 'plan', {}, 'model')


@pytest.mark.parametrize('mutation', ['owner', 'channel', 'version', 'digest', 'receipt', 'expired', 'disabled', 'policy', 'revision'])
def test_approval_is_bound_to_owner_message_version_digest_and_deadline(brief, video, mutation):  # noqa: F811
    store, clock = video
    job = prepare_approval(store, new_job(brief, video))
    value, owner, channel, timestamp, at = action(job), 'UHUMAN', 'CQUANT', '100.001', clock['at']
    if mutation == 'owner':
        owner = 'UOTHER'
    elif mutation == 'channel':
        channel = 'COTHER'
    elif mutation == 'version':
        value.version += 1
    elif mutation == 'digest':
        value.artifact_digest = 'f'*64
    elif mutation == 'receipt':
        timestamp = '100.999'
    elif mutation == 'expired':
        at = job['publish_deadline']
    elif mutation == 'disabled':
        store.settings.video_publish_enabled = False
    elif mutation == 'policy':
        store.settings.video_youtube_channel_id = 'another-channel'
    elif mutation == 'revision':
        with store.db.transaction() as conn:
            conn.execute('UPDATE projects SET revision=2 WHERE id=%s', (job['source']['project_id'],))
    with pytest.raises(PolicyError):
        store.action(str(job['id']), value, owner, channel, 'event-1', message_ts=timestamp, at=at)
    assert store.get(job['id'])['state'] == 'awaiting_approval'


def test_revision_invalidate_previous_approval_and_credit_cap_follows_edition(brief, video):  # noqa: F811
    store, clock = video
    job = prepare_approval(store, new_job(brief, video))
    store.begin_effect(job, 'speech-0', {}, 'speech', 60)
    store.finish_effect(job, 'speech-0', {'taskId': 'old'})
    store.action(str(job['id']), action(job, 'revise'), 'UHUMAN', 'CQUANT', 'revise', at=clock['at'])
    assert store.get(job['id'])['state'] == 'superseded'
    with store.db.transaction() as conn:
        newer = conn.execute('SELECT * FROM video_jobs WHERE version=2').fetchone()
    assert newer['source_digest'] == job['source_digest']
    with pytest.raises(PolicyError, match='budget'):
        store.begin_effect(newer, 'speech-0', {}, 'speech', 41)
    with pytest.raises(PolicyError):
        store.action(str(job['id']), action(job), 'UHUMAN', 'CQUANT', 'old-approve', at=clock['at'])


def test_signed_slack_action_and_duplicate_delivery(brief, video):  # noqa: F811
    store, _ = video
    job = prepare_approval(store, new_job(brief, video))
    credential = {'app_id': 'AVIDEO', 'bot_user_id': 'UBRIEF', 'signing_secret': 'test-signing', 'bot_token': 'fake'}
    payload = interaction(job, credential, 'hold')
    client = TestClient(create_app(store.settings, store.company, {'market_brief': credential}))
    raw, headers = signed(payload, credential)
    response = client.post('/slack/events/market_brief', content=raw, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()['state'] == 'held'
    assert client.post('/slack/events/market_brief', content=raw, headers=headers).json()['duplicate']
    headers['x-slack-signature'] = 'v0=invalid'
    assert client.post('/slack/events/market_brief', content=raw, headers=headers).status_code == 401


@pytest.mark.parametrize('field', ['team', 'api_app_id', 'user', 'message', 'container', 'actions'])
def test_slack_forgery_is_rejected(brief, video, field):  # noqa: F811
    store, _ = video
    job = prepare_approval(store, new_job(brief, video))
    credential = {'app_id': 'AVIDEO', 'bot_user_id': 'UBRIEF'}
    payload = interaction(job, credential)
    if field == 'team':
        payload[field]['id'] = 'TOTHER'
    elif field == 'api_app_id':
        payload[field] = 'AOTHER'
    elif field == 'user':
        payload[field]['id'] = 'UOTHER'
    elif field == 'message':
        payload[field]['user'] = 'UFORGED'
    elif field == 'container':
        payload[field]['message_ts'] = '123.999'
    else:
        payload[field][0]['action_id'] = 'approve'
    with pytest.raises(PolicyError):
        SlackIngress(store.settings, store.company, {'market_brief': credential}).accept('market_brief', payload, credential)


async def test_slack_review_receipt_and_expiry_gate(brief, video):  # noqa: F811
    store, clock = video
    job = prepare_approval(store, new_job(brief, video))
    with store.db.transaction() as conn:
        conn.execute("UPDATE outbox SET status='pending',sent_ts=NULL WHERE id=%s", (job['review_message_id'],))
        # Simulate the existing briefing already delivered.
        conn.execute("UPDATE outbox SET status='delivered',sent_ts='100.000' WHERE id<>%s", (job['review_message_id'],))
    sent = []
    def fake(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={'ok': True})  # missing ts must remain uncertain
    outbox = SlackOutbox(store.company, {'market_brief': {'bot_token': 'fake'}}, httpx.MockTransport(fake))
    assert await outbox.send_one()
    assert sent[0]['blocks'][1]['elements'][0]['action_id'] == 'video_approve'
    with store.db.transaction() as conn:
        assert conn.execute('SELECT status FROM outbox WHERE id=%s', (job['review_message_id'],)).fetchone()['status'] == 'uncertain'
        conn.execute("UPDATE outbox SET status='pending' WHERE id=%s", (job['review_message_id'],))
    clock['at'] = job['publish_deadline']
    assert not await outbox.send_one()
    assert len(sent) == 1


def test_deadline_stops_spending_and_preserves_unknown_charge(brief, video):  # noqa: F811
    store, clock = video
    job = new_job(brief, video)
    store.begin_effect(job, 'speech-0', {}, 'speech', 1)
    clock['at'] = job['publish_deadline']
    assert store.claim() is None
    assert store.get(job['id'])['state'] == 'uncertain'


class SimulatedProvider:
    def __init__(self):
        self.calls = []

    async def run(self, request):
        self.calls.append(request)
        value = video_plan() if request.output_contract == 'video_plan_v1' else good_review()
        return ProviderResponse(request_id=request.request_id, provider='claude',
            usage={'actual_model': request.model}, decision=AgentDecision(
            status='complete', say='', artifacts=[{'title': 'Simulated video', 'content': value.model_dump_json()}]))


class SimulatedSpeech:
    def __init__(self):
        self.calls = []

    async def account(self, workspace):
        assert workspace == 123
        return {'credits': {'total': 1500}}

    async def submit(self, **request):
        self.calls.append(request)
        return {'taskId': 'fixture-task-'+str(len(self.calls))}

    async def completed(self, identity):
        return 'https://example.com/'+identity+'.mp3'


class SimulatedYouTube:
    def __init__(self):
        self.calls = []

    async def initiate(self, job):
        self.calls.append('initiate-private')
        return 'https://www.googleapis.com/upload/youtube/v3/videos?upload_id=fixture'

    async def upload(self, job):
        assert job['upload_session']
        self.calls.append('resume-existing')
        return 'fixture1234'

    async def thumbnail(self, job):
        self.calls.append('thumbnail')
        return {'thumbnail_set': True}

    async def captions(self, job):
        self.calls.append('captions')
        return {'caption_id': 'fixture'}

    async def video(self, identity):
        return {'status': {'privacyStatus': 'private'}, 'snippet': {'channelId': 'UCfixture'},
                'processingDetails': {'processingStatus': 'succeeded'}}

    async def publish(self, job):
        self.calls.append('publish')
        return {'privacy': 'public'}


async def test_pipeline_restart_stays_private_until_bound_approval(brief, video, monkeypatch):  # noqa: F811
    store, clock = video
    job = new_job(brief, video)
    provider, speech, youtube = SimulatedProvider(), SimulatedSpeech(), SimulatedYouTube()
    class Renderer:
        def render(self, job, plan, directory, paths):
            return artifact_fixture(directory)
    monkeypatch.setattr('quant_company.video.runner.download_audio', lambda url, path: path.write_bytes(b'simulated-speech'))
    for _ in range(9):
        # New process object for every stage; committed DB receipts survive.
        runner = VideoRunner(store.company, provider, speech, Renderer(), youtube)
        await runner.tick()
    current = store.get(job['id'])
    assert current['state'] == 'awaiting_approval', current['error']
    assert len(provider.calls) == 2 and len(speech.calls) == 3
    assert 'publish' not in youtube.calls
    with store.db.transaction() as conn:
        # Runner's final deadline guard uses the real clock, not this historical brief fixture.
        conn.execute("UPDATE video_jobs SET publish_deadline=now()+interval '1 hour' WHERE id=%s", (job['id'],))
    current = store.get(job['id'])
    store.action(str(job['id']), action(current), 'UHUMAN', 'CQUANT', 'human-approved', at=clock['at'])
    await VideoRunner(store.company, provider, speech, Renderer(), youtube).tick()
    assert store.get(job['id'])['state'] == 'published'
    assert youtube.calls.count('publish') == 1
    await runner.tick()
    assert youtube.calls.count('publish') == 1


async def test_ambiguous_speech_response_never_resubmits(brief, video):  # noqa: F811
    store, _ = video
    job = new_job(brief, video)
    store.save(job, 'synthesizing', plan=video_plan().model_dump(mode='json'), review=good_review().model_dump())
    speech = SimulatedSpeech()
    async def uncertain(**request):
        speech.calls.append(request)
        raise httpx.ReadTimeout('simulated lost receipt')
    speech.submit = uncertain
    runner = VideoRunner(store.company, speech=speech)
    assert (await runner.tick())['state'] == 'uncertain'
    assert (await runner.tick())['state'] == 'idle'
    assert len(speech.calls) == 1


async def test_artifact_change_after_approval_never_publishes(brief, video):  # noqa: F811
    store, clock = video
    job = prepare_approval(store, new_job(brief, video))
    with store.db.transaction() as conn:
        conn.execute("UPDATE video_jobs SET publish_deadline=now()+interval '1 hour' WHERE id=%s", (job['id'],))
    store.action(str(job['id']), action(job), 'UHUMAN', 'CQUANT', 'approve', at=clock['at'])
    (store.settings.video_artifact_dir / str(job['id']) / 'video.mp4').write_bytes(b'changed')
    youtube = SimulatedYouTube()
    assert (await VideoRunner(store.company, youtube=youtube).tick())['state'] == 'blocked'
    assert not youtube.calls


def test_status_never_exposes_tokens_upload_session_or_signed_urls(brief, video):  # noqa: F811
    store, _ = video
    job = new_job(brief, video)
    store.checkpoint(job, upload_session='https://example.com?secret=never-show')
    client = TestClient(create_app(store.settings, store.company, {}))
    assert client.get('/v1/videos').status_code == 401
    response = client.get('/v1/videos', headers={'Authorization': 'Bearer test-token-with-more-than-24-characters'})
    assert response.status_code == 200
    assert 'never-show' not in response.text
    assert 'upload_session' not in response.text


async def test_busy_before_execution_refunds_allowance_and_defers_same_request(brief, video):  # noqa: F811
    store, _ = video
    job = new_job(brief, video)
    provider = SimulatedProvider()
    async def busy(request):
        raise ProviderFault('busy', 'Pre-execution lane is occupied', 10)
    provider.run = busy
    result = await VideoRunner(store.company, provider=provider).tick()
    assert result['state'] == 'queued'
    with store.db.transaction() as conn:
        effect = conn.execute('SELECT * FROM video_effects WHERE job_id=%s', (job['id'],)).fetchone()
        assert effect['state'] == 'rejected'
        assert conn.execute('SELECT reserved FROM daily_usage WHERE day=CURRENT_DATE').fetchone()['reserved'] == 2
        conn.execute('UPDATE video_jobs SET lease_until=NULL WHERE id=%s', (job['id'],))
    provider = SimulatedProvider()
    assert (await VideoRunner(store.company, provider=provider).tick())['state'] == 'reviewing'
    assert provider.calls[0].request_id == f"video-{job['id']}-plan"
    assert provider.calls[0].model == 'claude-opus-5' and provider.calls[0].output_contract == 'video_plan_v1'


async def test_claude_quota_before_execution_defers_without_spending_allowance(brief, video):  # noqa: F811
    store, _ = video
    job = new_job(brief, video)
    provider = SimulatedProvider()
    async def quota(request):
        raise ProviderFault('quota', 'Subscription window is exhausted', 900)
    provider.run = quota
    assert (await VideoRunner(store.company, provider=provider).tick())['state'] == 'queued'
    with store.db.transaction() as conn:
        effect = conn.execute('SELECT * FROM video_effects WHERE job_id=%s', (job['id'],)).fetchone()
        assert effect['state'] == 'rejected' and effect['receipt']['code'] == 'quota'
        row = conn.execute('SELECT error,lease_until FROM video_jobs WHERE id=%s', (job['id'],)).fetchone()
        assert row['error'] == 'model_quota' and row['lease_until'] is not None


async def test_non_claude_or_other_model_response_is_rejected(brief, video):  # noqa: F811
    store, _ = video
    job = new_job(brief, video)
    provider = SimulatedProvider()
    original = provider.run
    async def codex(request):
        return (await original(request)).model_copy(update={'provider': 'codex'})
    provider.run = codex
    assert (await VideoRunner(store.company, provider=provider).tick())['state'] in {'blocked', 'uncertain'}
    assert store.get(job['id'])['plan'] is None


async def test_entire_speech_budget_is_checked_before_first_charge(brief, video):  # noqa: F811
    store, _ = video
    job = new_job(brief, video)
    store.save(job, 'synthesizing', plan=video_plan().model_dump(mode='json'), review=good_review().model_dump())
    store.settings.video_monthly_credit_limit = 1
    speech = SimulatedSpeech()
    assert (await VideoRunner(store.company, speech=speech).tick())['state'] == 'blocked'
    assert not speech.calls


def test_reconcile_original_task_receipt_does_not_create_another_charge(brief, video):  # noqa: F811
    from quant_company.video.recovery import reconcile

    store, _ = video
    job = new_job(brief, video)
    store.begin_effect(job, 'speech-0', {'text': 'known-original'}, 'speech', 10)
    store.save(job, 'uncertain', error='external_effect_requires_reconciliation')
    with pytest.raises(ValueError):
        reconcile(store, str(job['id']), 'speech-0', {'taskId': ''}, '원래 요청의 생성 이력과 영수증을 확인한 기록입니다.')
    assert reconcile(store, str(job['id']), 'speech-0', {'taskId': 'original-task'},
                     '원래 요청의 생성 이력과 영수증을 확인한 기록입니다.')['state'] == 'synthesizing'
    assert store.begin_effect(job, 'speech-0', {'text': 'known-original'}, 'speech', 10) == {'taskId': 'original-task'}
    with store.db.transaction() as conn:
        assert conn.execute('SELECT sum(credits) AS n FROM video_effects').fetchone()['n'] == 10
        assert conn.execute('SELECT count(*) AS n FROM video_reconciliations').fetchone()['n'] == 1


def test_reconcile_public_receipt_requires_prior_human_approval(brief, video):  # noqa: F811
    from quant_company.video.recovery import reconcile

    store, clock = video
    job = prepare_approval(store, new_job(brief, video))
    store.action(str(job['id']), action(job), 'UHUMAN', 'CQUANT', 'human', at=clock['at'])
    job = store.get(job['id'])
    store.begin_effect(job, 'publish', {'video': job['youtube_id']}, 'youtube-write')
    store.save(job, 'uncertain', error='external_effect_requires_reconciliation')
    assert reconcile(store, str(job['id']), 'publish', {'video_id': job['youtube_id'], 'privacy': 'public'},
                     '시뮬레이션에서 기존 영상 공개 상태와 이전 승인 영수증을 대조함')['state'] == 'published'
    assert store.get(job['id'])['approved_by'] == 'UHUMAN'


def test_safe_retry_cannot_repeat_uncertain_charge_or_expired_work(brief, video):  # noqa: F811
    from quant_company.video.recovery import retry

    store, clock = video
    job = new_job(brief, video)
    store.begin_effect(job, 'speech-0', {}, 'speech', 1)
    store.save(job, 'blocked', error='video_stage_failed:rendering')
    with pytest.raises(PolicyError):
        retry(store, str(job['id']), '기존 음성 생성 task와 로컬 렌더 환경을 확인한 복구 기록')
    store.finish_effect(job, 'speech-0', {'taskId': 'original-task'})
    assert retry(store, str(job['id']), '기존 음성 생성 task와 로컬 렌더 환경을 확인한 복구 기록')['state'] == 'rendering'
    job = store.get(job['id'])
    store.save(job, 'blocked', error='video_stage_failed:rendering')
    clock['at'] = job['publish_deadline']
    with pytest.raises(PolicyError):
        retry(store, str(job['id']), '기존 음성 생성 task와 로컬 렌더 환경을 확인한 복구 기록')


def test_0830_delay_status_is_once_per_edition(brief, video):  # noqa: F811
    from datetime import timedelta

    store, clock = video
    job = new_job(brief, video)
    with store.db.transaction() as conn:
        conn.execute("UPDATE projects SET thread_ts='100.000' WHERE id=%s", (job['source']['project_id'],))
    clock['at'] = job['publish_deadline']-timedelta(minutes=30)
    store.late_notices()
    store.late_notices()
    with store.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM messages WHERE kind='video_status'").fetchone()['n'] == 1


def test_edition_times_follow_the_brief_schedule():
    from datetime import date, datetime, timedelta

    from quant_company.video.store import KST, edition_times

    day = date(2026, 10, 7)
    am = edition_times({'kind': 'am', 'day': day, 'due_at': datetime(2026, 10, 7, 7, 45, tzinfo=KST)})
    assert [t.strftime('%H:%M') for t in am] == ['06:50', '09:00']            # NY daylight time: publish 07:00
    winter = edition_times({'kind': 'am', 'day': date(2026, 11, 2), 'due_at': datetime(2026, 11, 2, 7, 55, tzinfo=KST)})
    assert [t.strftime('%H:%M') for t in winter] == ['07:40', '09:00']        # from 11/1: publish 07:50
    pm = edition_times({'kind': 'pm', 'day': day, 'due_at': datetime(2026, 10, 7, 17, 25, tzinfo=KST)})
    assert [t.strftime('%H:%M') for t in pm] == ['17:50', '20:20']            # publish 18:00
    assert pm[0] - pm[1] == timedelta(minutes=-150)


def test_close_edition_gets_its_own_job_label_times_and_shared_monthly_budget(brief, video):  # noqa: F811
    from datetime import datetime
    from uuid import uuid4

    from quant_company.video.store import KST

    store, _ = video
    morning = new_job(brief, video)
    pm_id = uuid4()
    with store.db.transaction() as conn:
        edition = conn.execute('SELECT * FROM brief_editions WHERE id=%s', (morning['edition_id'],)).fetchone()
        due = datetime.combine(edition['day'], datetime.min.time().replace(hour=17, minute=45), KST)
        conn.execute("""INSERT INTO brief_editions SELECT (jsonb_populate_record(NULL::brief_editions,
            to_jsonb(e) || jsonb_build_object('id', %s::text, 'kind', 'pm', 'due_at', %s::text, 'state', 'ready'))).* FROM brief_editions e WHERE id=%s""",
                     (str(pm_id), due.isoformat(), edition['id']))
        closing = conn.execute('SELECT * FROM brief_editions WHERE id=%s', (pm_id,)).fetchone()
        identity = store.enqueue(conn, closing, edition['cutoff'])
    assert identity and identity != str(morning['id'])
    job = store.get(identity)
    assert job['source']['edition_kind'] == 'close' and morning['source'].get('edition_kind', 'am') == 'am'
    assert job['policy']['edition'] == 'close'
    assert job['publish_deadline'].astimezone(KST).strftime('%H:%M') == '20:20'
    assert datetime.fromisoformat(job['policy']['review_target']).astimezone(KST).strftime('%H:%M') == '17:50'
    assert datetime.fromisoformat(job['policy']['publish_at']).astimezone(KST).strftime('%H:%M') == '18:00'
    store.settings.video_monthly_credit_limit, store.settings.video_episode_credit_limit = 100, 100
    store.begin_effect(morning, 'speech-0', {'scene': 0}, 'speech', 60)
    assert store.budget_available(job, 40) and not store.budget_available(job, 41)
    with pytest.raises(PolicyError, match='budget'):
        store.begin_effect(job, 'speech-0', {'scene': 0}, 'speech', 41)


def test_speech_credit_estimate_matches_observed_takes():
    from quant_company.video.runway import speech_credits

    assert [speech_credits('가' * n) for n in (55, 210, 500, 501, 600)] == [1, 1, 1, 2, 2]


def test_publish_times_are_configurable_but_morning_generation_still_ends_at_nine():
    from datetime import date, datetime

    from quant_company.video.store import KST, edition_times, publish_time

    am = {'kind': 'am', 'day': date(2026, 10, 7), 'due_at': datetime(2026, 10, 7, 7, 45, tzinfo=KST)}
    settings = Settings(video_am_publish_dst='07:20', video_publish_lead_minutes=15)
    target, deadline = edition_times(am, settings)
    assert (publish_time(am, settings).strftime('%H:%M'), target.strftime('%H:%M'), deadline.strftime('%H:%M')) == ('07:20', '07:05', '09:00')
    with pytest.raises(ValueError):
        Settings(video_am_publish_dst='10:15')


def test_retention_prunes_only_ended_jobs_after_the_period(brief, video):  # noqa: F811
    store, _ = video
    job = new_job(brief, video)
    folder = store.settings.video_artifact_dir / str(job['id']) / 'attempt'
    folder.mkdir(parents=True)
    (folder / 'video.mp4').write_bytes(b'x')
    assert store.prune() == 0
    with store.db.transaction() as conn:
        conn.execute("UPDATE video_jobs SET state='published', updated_at=now()-interval '15 days' WHERE id=%s", (job['id'],))
    assert store.prune() == 1 and not folder.exists()


async def test_low_disk_blocks_new_episodes_without_charges(brief, video, monkeypatch):  # noqa: F811
    store, _ = video
    job = new_job(brief, video)
    monkeypatch.setattr('quant_company.video.runner.free_gb', lambda path: 2.0)
    result = await VideoRunner(store.company, provider=SimulatedProvider()).tick()
    assert result['state'] == 'disk_low'
    assert store.get(job['id'])['state'] == 'blocked' and store.get(job['id'])['error'] == 'insufficient_disk'
    with store.db.transaction() as conn:
        assert conn.execute('SELECT count(*) AS n FROM video_effects').fetchone()['n'] == 0


def test_delivery_text_states_publish_time_and_a_late_finish():
    from datetime import datetime, timedelta

    from quant_company.video.store import KST, delivery_text

    target = datetime(2026, 10, 12, 17, 50, tzinfo=KST)
    job = {'source': {'edition_kind': 'close'},
           'policy': {'review_target': target.isoformat(), 'publish_at': (target + timedelta(minutes=10)).isoformat()}}
    on_time = delivery_text(job, {'duration': 283, 'title': 't'}, target - timedelta(minutes=5))
    assert '공개 목표 18:00 KST' in on_time and '늦게' not in on_time
    late = delivery_text(job, {'duration': 283, 'title': 't'}, target + timedelta(minutes=12))
    assert '파일 준비 목표보다 12분 늦게 완성됐습니다' in late

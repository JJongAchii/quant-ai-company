"""Daily Shorts: the cut's contract and checks, a real Chromium/FFmpeg vertical render, and the production flow on real
PostgreSQL. Model, speech, alignment and Slack's HTTP API are simulated fixtures; nothing here calls them."""

import importlib.util
import json

import pytest

from quant_company.config import Settings
from quant_company.video import runner as runner_module
from quant_company.video.contracts import digest
from quant_company.video.render import checksum, run, verify_artifacts
from quant_company.video.runner import VideoRunner
from quant_company.video.shorts import (
    CHECKS,
    NARRATION_LIMIT,
    ShortsPlan,
    ShortsRenderer,
    closes_of,
    direction,
    settle,
    shorts_prompt,
    shorts_violations,
)
from quant_company.video.shorts_store import ShortsStore

from .test_briefing import brief  # noqa: F401
from .test_video import body_source, video  # noqa: F401
from .test_video_delivery import FakeSlack, quiet_other_outbox, send
from .test_video_motion import InspectedRenderer, InspectingProvider, WordAligner, episode, produce_inspected
from .test_video_render import ffmpeg_binary


def shorts(**changes):
    value = dict(
        title='S&P 500 1.92% 상승, 반도체가 이끌었다', description='반도체가 이끈 미국 증시와 확산 여부를 짧게 정리합니다.',
        scenes=[dict(id='hook', type='hook', narration='에스앤피 500이 정규장 종가 기준 1.92% 올랐습니다. 반도체가 이끌었습니다.',
                     claim_ids=['b013', 'b007'], data=dict(question='반도체가 이끈 상승, 확산될까?', stat='S&P 500 1.92%',
                                                           stat_dir='up')),
                dict(id='index', type='point', narration='에스앤피 500은 5,300에 마감했습니다. 정규장 종가 기준입니다.',
                     claim_ids=['b013'], data=dict(label='S&P 500', big='1.92%', dir='up', text='정규장 종가 5,300으로 마감')),
                dict(id='nasdaq', type='point', narration='나스닥 종합은 0.59% 올라 17,100에 마감했습니다.',
                     claim_ids=['b014'], data=dict(label='나스닥 종합', big='0.59%', dir='up', text='종가 17,100, 상승폭은 더 작았습니다')),
                dict(id='signal', type='point', narration='다음은 비기술 업종으로 상승이 확산되는지 확인합니다.',
                     claim_ids=['b011', 'b012'], data=dict(label='확인할 신호', big='확산', text='비기술 업종과 한국 반도체로 이어질까'))])
    value.update(changes)
    return ShortsPlan.model_validate(value)


def test_a_cut_is_one_hook_and_three_points_without_the_series_name():
    plan = shorts().model_dump(mode='json')
    with pytest.raises(ValueError, match='one hook and three points'):
        ShortsPlan.model_validate({**plan, 'scenes': [plan['scenes'][1], plan['scenes'][0], *plan['scenes'][2:]]})
    with pytest.raises(ValueError, match='series name'):
        ShortsPlan.model_validate({**plan, 'title': '반도체가 이끌었다 | 9월 22일 아침 · 오늘의 증시story'})
    long = [{**s, 'narration': '가' * 100} for s in plan['scenes']]
    assert 4 * 100 > NARRATION_LIMIT
    with pytest.raises(ValueError, match='Shorts length'):
        ShortsPlan.model_validate({**plan, 'scenes': long})
    with pytest.raises(ValueError, match='lacks'):
        ShortsPlan.model_validate({**plan, 'scenes': [plan['scenes'][0], {**plan['scenes'][1], 'data': {'label': 'x'}},
                                                     *plan['scenes'][2:]]})


@pytest.mark.parametrize('scene,field,value,error', [
    (1, 'big', '5,400', 'Unbound number'),
    (1, 'dir', 'dn', 'contradicts its line'),  # ▼ for the 1.92% rise
    (2, 'narration', '나스닥 종합은 0.59% 급락해 17,100에 마감했습니다.', 'contradicts its line'),
    (2, 'text', '17,100으로 마감', '종가'),  # a close value without its label
    (2, 'narration', '보도에 따르면 나스닥 종합은 0.59% 올랐습니다.', '보도에 따르면'),
    (3, 'narration', '이 영상은 AI가 만든 요약입니다. 확산을 확인합니다.', 'tool wording'),
    (3, 'narration', '반도체는 지금 사세요. 확산을 확인합니다.', 'Buy/sell'),
])
def test_the_cut_follows_the_episode_binding_rules(scene, field, value, error):
    assert shorts_violations(shorts(), body_source()) == []
    raw = shorts().model_dump(mode='json')
    target = raw['scenes'][scene]
    if field == 'narration':
        target['narration'] = value
    else:
        target['data'][field] = value
    assert any(error in f for f in shorts_violations(ShortsPlan.model_validate(raw), body_source()))


def test_a_list_shares_its_verb_and_a_date_is_no_close():
    # 10/20 AM: all three cuts were given up on these lines.
    line = ('19일 정규장에서 에너지저장장치(ESS) 수요 기대에 삼성SDI는 11.70%, LG에너지솔루션은 7.20%, 포스코퓨처엠은 '
            '10.90% 상승 마감했다.')
    assert [direction(line, v) for v in ('11.70%', '7.20%', '10.90%')] == ['up', 'up', 'up']
    assert direction('삼성SDI는 11.70%, 반면 LG에너지솔루션은 3.10% 하락했다.', '11.70%') is None
    assert closes_of('나스닥 종합 26,430.89 pt · +1.11% · 정규장 종가 10/20 05:00 KST · Yahoo chart') == {'26430.89', '1.11'}


def test_an_arrow_its_line_does_not_state_is_dropped_not_rejected():
    raw = shorts().model_dump(mode='json')
    raw['scenes'][1]['data']['big'] = '5,300'  # a level: its line states no direction for it
    plan, dropped = settle(ShortsPlan.model_validate(raw), body_source())
    assert dropped == ['index: 5,300'] and 'dir' not in plan.scenes[1].data and shorts_violations(plan, body_source()) == []
    raw['scenes'][1]['data'].update(big='1.92%', dir='dn')  # against its line: kept, and the checks fail
    plan, dropped = settle(ShortsPlan.model_validate(raw), body_source())
    assert dropped == [] and any('contradicts its line' in f for f in shorts_violations(plan, body_source()))


def test_the_prompt_cuts_the_delivered_episode_and_leaves_flagged_claims_out():
    prompt = shorts_prompt(body_source(), episode().model_dump(mode='json'), ['나스닥 수치 해석이 과장됨'], '- Unbound number')
    assert episode().upload.title in prompt and 'no greeting' in prompt and "Never say '보도에 따르면'" in prompt
    assert '나스닥 수치 해석이 과장됨' in prompt and 'Unbound number' in prompt and 'b013' in prompt


def test_a_vertical_cut_renders_inside_the_shorts_safe_area(tmp_path):
    if not importlib.util.find_spec('playwright'):
        pytest.skip('Install optional video dependencies and Chromium')
    ffmpeg = ffmpeg_binary()
    settings = Settings(video_artifact_dir=tmp_path, video_ffmpeg=ffmpeg, video_ffprobe='')
    plan = shorts()
    paths = []
    for i in range(5):
        path = tmp_path / f's{i}.wav'
        run([ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i', f'sine=frequency={330 + i * 60}:sample_rate=48000:duration=8', str(path)])
        paths.append(path)
    job = {'id': 'fixture', 'source': {**body_source(), 'edition_kind': 'am'}, 'policy': {}}
    manifest = ShortsRenderer(settings, WordAligner()).render(job, plan, tmp_path / 'out', paths[:-1], paths[-1])
    assert manifest['size'] == [1080, 1920] and 40 < manifest['duration'] < 50
    assert not any('safe area' in f for f in manifest['findings']), manifest['findings']
    assert [s['type'] for s in manifest['scenes']] == ['hook', 'point', 'point', 'point', 'outro']
    upload = (tmp_path / 'out' / 'shorts-upload.txt').read_text()
    assert plan.title in upload and '#Shorts' in upload and '관련 동영상' in upload and '|' not in upload.split('\n')[1]
    srt = (tmp_path / 'out' / 'shorts.srt').read_text()
    assert '본편에서 확인하세요.' in srt
    verify_artifacts(manifest, tmp_path)


class ShortsFileRenderer:
    def __init__(self):
        self.calls = []

    def render(self, job, plan, directory, paths, outro, stop=None):
        self.calls.append((len(paths), outro))
        files = {'shorts.mp4': b'simulated-shorts', 'shorts.srt': b'1\n00:00:00,000 --> 00:00:01,000\nx\n',
                 'shorts-upload.txt': '[제목]\nS&P 500 1.92% 상승\n'.encode(), 'shorts-plan.json': plan.model_dump_json().encode()}
        for name, content in files.items():
            (directory / name).write_bytes(content)
        manifest = {'title': plan.title, 'duration': 46.0, 'qa_passed': True, 'findings': [], 'directory': str(directory),
                    'files': {n: checksum(directory / n) for n in files}}
        manifest['digest'] = digest(manifest)
        return manifest


async def delivered_episode(store, tmp_path, monkeypatch, brief, clock=None):  # noqa: F811
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from quant_company.video.contracts import VisualReview

    store.settings.video_upload_enabled = store.settings.video_publish_enabled = False
    provider = InspectingProvider(visual=[VisualReview(passed=True, issues=[])])
    provider.queues['video_shorts_v1'] = [shorts()]
    job, speech = await produce_inspected(store, tmp_path, monkeypatch, provider, InspectedRenderer(), brief)
    assert job['state'] == 'delivering', job['error']
    with store.db.transaction() as conn:  # the dispatcher's receipt of the episode files (simulated)
        conn.execute("UPDATE video_jobs SET state='delivered' WHERE id=%s", (job['id'],))
    if clock is not None:  # midday: no next episode is due (shorts_store.QUIET)
        clock['at'] = datetime(2026, 9, 22, 12, 0, tzinfo=ZoneInfo('Asia/Seoul'))
    return job, provider, speech


async def shorts_ticks(store, provider, speech, renderer, job, limit=12):
    shorts_store = ShortsStore(store.company)
    for _ in range(limit):
        runner = VideoRunner(store.company, provider, speech)
        runner.shorts_renderer = renderer
        await runner.tick()
        with store.db.transaction() as conn:
            conn.execute('UPDATE video_shorts SET lease_until=NULL WHERE job_id=%s', (job['id'],))
        row = shorts_store.get(job['id'])
        if row and row['state'] not in {'writing', 'synthesizing', 'rendering'}:
            return row
    return shorts_store.get(job['id'])


async def test_a_delivered_episode_gets_its_shorts_in_the_same_thread(brief, video, tmp_path, monkeypatch):  # noqa: F811
    store, clock = video
    job, provider, speech = await delivered_episode(store, tmp_path, monkeypatch, brief, clock)
    renderer = ShortsFileRenderer()
    assert (await VideoRunner(store.company, provider, speech).tick())['state'] == 'idle'  # off by default
    store.settings.video_shorts_enabled = True
    takes = len(speech.calls)
    row = await shorts_ticks(store, provider, speech, renderer, job)
    assert row['state'] == 'delivering', row['error']
    request = next(c for c in provider.calls if c.output_contract == 'video_shorts_v1')
    assert request.request_id == f"video-{job['id']}-shorts-1" and episode().upload.title in request.prompt
    assert len(speech.calls) - takes == 5 and speech.calls[-1]['text'] == '왜 그런지, 앞으로 볼 신호는 본편에서 확인하세요.'
    assert renderer.calls == [(4, renderer.calls[0][1])]
    with store.db.transaction() as conn:
        message = conn.execute("SELECT o.text,o.thread_ts FROM outbox o WHERE o.id=%s", (row['delivery_message_id'],)).fetchone()
        keys = [r['id'].split(':', 1)[1] for r in conn.execute(
            "SELECT id FROM video_effects WHERE job_id=%s AND id LIKE %s", (job['id'], '%:shorts-speech-%')).fetchall()]
    assert message['text'].startswith('<@UHUMAN>\n쇼츠 준비 완료') and len(keys) == 5
    quiet_other_outbox(store, row['delivery_message_id'])
    slack = FakeSlack()
    assert await send(store, slack)
    assert [f['name'] for f in slack.uploaded.values()] == ['아침브리핑_20260922_쇼츠.mp4', '아침브리핑_20260922_쇼츠.srt',
                                                             '아침브리핑_20260922_쇼츠_업로드문안.txt']
    assert ShortsStore(store.company).get(job['id'])['state'] == 'delivered'
    # One cut per episode: nothing is made again.
    calls = len(provider.calls)
    await VideoRunner(store.company, provider, speech).tick()
    assert len(provider.calls) == calls


async def test_the_episode_goes_first_and_a_late_cut_is_given_up(brief, video, tmp_path, monkeypatch):  # noqa: F811
    store, clock = video
    job, provider, speech = await delivered_episode(store, tmp_path, monkeypatch, brief, clock)
    store.settings.video_shorts_enabled = True
    shorts_store = ShortsStore(store.company)
    with store.db.transaction() as conn:
        conn.execute("UPDATE video_jobs SET state='rendering' WHERE id=%s", (job['id'],))
    assert shorts_store.claim() is None and shorts_store.get(job['id']) is None  # an episode is in production
    with store.db.transaction() as conn:
        conn.execute("UPDATE video_jobs SET state='delivered' WHERE id=%s", (job['id'],))
    assert shorts_store.claim()['state'] == 'writing'
    with store.db.transaction() as conn:
        conn.execute("UPDATE video_shorts SET lease_until=NULL")
        newer = dict(job, id='00000000-0000-0000-0000-000000000001', version=2, state='superseded')
        conn.execute("""INSERT INTO video_jobs(id,edition_id,version,source_digest,source,policy,state,publish_deadline,
            created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,now()+interval '1 minute')""",
                     (newer['id'], job['edition_id'], 2, job['source_digest'], json.dumps(job['source']),
                      json.dumps(job['policy']), 'superseded', job['publish_deadline']))
    assert shorts_store.claim() is None
    assert shorts_store.get(job['id'])['state'] == 'skipped' and shorts_store.get(job['id'])['error'] == 'superseded'
    with store.db.transaction() as conn:
        note = conn.execute("SELECT text FROM messages WHERE kind='video_status' AND text LIKE '쇼츠는 다음 회차%'").fetchone()
    assert note and not note['text'].startswith('<@')


async def test_shorts_leave_the_credits_the_next_episodes_need(brief, video, tmp_path, monkeypatch):  # noqa: F811
    store, clock = video
    job, provider, speech = await delivered_episode(store, tmp_path, monkeypatch, brief, clock)
    store.settings.video_shorts_enabled = True
    with store.db.transaction() as conn:
        spent = conn.execute('SELECT COALESCE(sum(credits),0) AS n FROM video_effects').fetchone()['n']
    store.settings.video_monthly_credit_limit = spent + 50  # less than the reserve kept for the next episodes
    takes = len(speech.calls)
    row = await shorts_ticks(store, provider, speech, ShortsFileRenderer(), job)
    assert row['state'] == 'skipped' and row['error'] == 'credit_reserve' and len(speech.calls) == takes


async def test_the_reserve_is_kept_in_the_month_not_in_the_episode_cap(brief, video, tmp_path, monkeypatch):  # noqa: F811
    store, clock = video
    job, provider, speech = await delivered_episode(store, tmp_path, monkeypatch, brief, clock)
    store.settings.video_shorts_enabled = True
    with store.db.transaction() as conn:
        spent = conn.execute('SELECT COALESCE(sum(credits),0) AS n FROM video_effects').fetchone()['n']
    store.settings.video_episode_credit_limit = spent + 5  # room for the cut's five takes, not for another 80
    row = await shorts_ticks(store, provider, speech, ShortsFileRenderer(), job)
    assert row['state'] == 'delivering', row['error']


async def test_no_cut_starts_while_the_next_episode_is_due_or_after_a_stuck_one(brief, video, tmp_path, monkeypatch):  # noqa: F811
    from datetime import datetime
    from zoneinfo import ZoneInfo

    store, clock = video
    job, provider, speech = await delivered_episode(store, tmp_path, monkeypatch, brief, clock)
    store.settings.video_shorts_enabled = True
    shorts_store = ShortsStore(store.company)
    clock['at'] = datetime(2026, 9, 22, 16, 30, tzinfo=ZoneInfo('Asia/Seoul'))  # the close brief's episode is due
    assert shorts_store.claim() is None and shorts_store.get(job['id']) is None
    clock['at'] = datetime(2026, 9, 22, 12, 0, tzinfo=ZoneInfo('Asia/Seoul'))
    with store.db.transaction() as conn:  # an older episode stuck in production for days no longer holds the cuts
        conn.execute("""INSERT INTO video_jobs(id,edition_id,version,source_digest,source,policy,state,publish_deadline,
            created_at) VALUES(%s,%s,3,%s,%s,%s,'queued',%s,now()-interval '3 days')""",
                     ('00000000-0000-0000-0000-000000000003', job['edition_id'], job['source_digest'],
                      json.dumps(job['source']), json.dumps(job['policy']), job['publish_deadline']))
    assert shorts_store.claim()['state'] == 'writing'


async def test_a_lost_completion_keeps_the_file_receipt(brief, video, tmp_path, monkeypatch):  # noqa: F811
    store, clock = video
    job, provider, speech = await delivered_episode(store, tmp_path, monkeypatch, brief, clock)
    store.settings.video_shorts_enabled = True
    row = await shorts_ticks(store, provider, speech, ShortsFileRenderer(), job)
    shorts_store = ShortsStore(store.company)
    shorts_store.delivery_receipt(row['delivery_message_id'], {'files': [{'id': 'F1', 'uploaded': True}], 'complete_requested': True})
    with store.db.transaction() as conn:  # the dispatcher died after asking Slack to share the files
        conn.execute("UPDATE outbox SET status='uncertain' WHERE id=%s", (row['delivery_message_id'],))
    assert shorts_store.sync_deliveries() == 1
    current = shorts_store.get(job['id'])
    assert current['state'] == 'delivery_uncertain' and current['qa']['delivery']['files'][0]['id'] == 'F1'
    with store.db.transaction() as conn:
        note = conn.execute("SELECT o.text FROM outbox o JOIN messages m ON m.id=o.id WHERE m.text LIKE %s",
                            ('%쇼츠 파일 전달 결과 확인 필요%',)).fetchone()
    assert note['text'].startswith('<@UHUMAN>\n') and '서버 보관 경로' in note['text']


async def test_a_lost_turn_is_asked_again_as_a_new_attempt(brief, video, tmp_path, monkeypatch):  # noqa: F811
    from quant_company.contracts import ProviderFault

    store, clock = video
    job, provider, speech = await delivered_episode(store, tmp_path, monkeypatch, brief, clock)
    store.settings.video_shorts_enabled = True
    provider.queues['video_shorts_v1'] = [ProviderFault('uncertain', 'the CLI exited'), shorts()]
    row = await shorts_ticks(store, provider, speech, ShortsFileRenderer(), job)
    assert row['state'] == 'delivering', row['error']
    assert [a['outcome'] for a in row['qa']['attempts']] == ['uncertain', 'passed']
    keys = [c.request_id.rsplit('-', 1)[-1] for c in provider.calls if c.output_contract == 'video_shorts_v1']
    assert keys == ['1', '2']


async def test_a_cut_given_up_on_older_checks_is_checked_again_without_a_new_turn(brief, video, tmp_path, monkeypatch):  # noqa: F811
    store, clock = video
    job, provider, speech = await delivered_episode(store, tmp_path, monkeypatch, brief, clock)
    store.settings.video_shorts_enabled = True
    provider.queues['video_shorts_v1'] = [shorts(), shorts(), shorts()]
    checks = runner_module.shorts_violations
    monkeypatch.setattr(runner_module, 'shorts_violations', lambda plan, source: ['an older check'])
    row = await shorts_ticks(store, provider, speech, ShortsFileRenderer(), job)
    assert row['state'] == 'skipped' and row['error'] == 'check_failed' and row['qa']['checks'] == CHECKS
    shorts_store = ShortsStore(store.company)
    assert shorts_store.claim() is None  # given up on the current checks: not looked at again
    monkeypatch.setattr(runner_module, 'shorts_violations', checks)
    with store.db.transaction() as conn:  # as if given up before this version of the checks
        conn.execute("UPDATE video_shorts SET qa=qa-'checks' WHERE job_id=%s", (job['id'],))
    calls = len(provider.calls)
    row = await shorts_ticks(store, provider, speech, ShortsFileRenderer(), job)
    assert row['state'] == 'delivering', row['error']
    assert len(provider.calls) == calls  # the written versions, no new turn
    assert row['qa']['attempts'][-1]['recheck'] == {'checks': CHECKS, 'findings': []}
    assert 'recheck' not in row['qa'] and row['plan'] == shorts().model_dump(mode='json')

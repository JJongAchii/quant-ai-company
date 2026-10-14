"""Motion template: episode contract, number binding and a real Chromium/FFmpeg render.
Model, speech and alignment are simulated fixtures; the frames, encoding and layout checks are real."""

import importlib.util
import json

import pytest

from quant_company.config import Settings
from quant_company.contracts import AgentDecision, ProviderResponse
from quant_company.video.episode import EpisodePlan, validate_episode
from quant_company.video.motion import MotionRenderer, captions, word_times
from quant_company.video.render import run, spoken_numbers_heard, verify_artifacts
from quant_company.video.runner import VideoRunner

from .test_briefing import brief, complete  # noqa: F401
from .test_video import body_source, new_job, video  # noqa: F401
from .test_video_render import ffmpeg_binary


def source():
    return body_source()


def episode(**changes):
    # Line IDs are the numbered lines of the delivered fixture body (video/body.py): b001 summary, b003/b004 index lines,
    # b005 overview, b007 issue fact, b009 mechanism, b010 counterpoint, b011 signal, b012 next check, b013/b014 closes.
    scenes = [
        dict(id='hook', chapter='', type='cold_open', claim_ids=['b001', 'b005', 'b013', 'b014'],
             narration='보도에 따르면 미국 증시는 반도체가 주도했습니다. 그런데 업종별 흐름은 엇갈렸습니다.',
             data=dict(kicker='9월 22일 화요일 · 아침 시장 브리핑', question=[[['반도체가', 1], ['이끌었다', 0]]],
                       stats=[dict(label='S&P 500 · 정규장 종가 기준', value='5,300', sub='▲ 1.92%', sub_dir='up'),
                              dict(label='나스닥 종합 · 정규장 종가 기준', value='17,100', sub='▲ 0.59%', sub_dir='up'),
                              dict(label='업종 흐름', value='혼조', sub='기술주 밖 참여는 혼조')])),
        dict(id='summary', chapter='요약', type='summary3', claim_ids=['b001', 'b009', 'b011'],
             narration='보도에 따르면 오늘 꼭 알아야 할 세 가지는 반도체 주도, 금리 하락의 해석, 그리고 상승의 확산 여부입니다.',
             data=dict(title='오늘 꼭 알아야 할 <span style="color:var(--gold)">세 가지</span>',
                       cards=[dict(n='01', items=[['chip', 112, '#5EA0FF', '반도체', 'up', '▲'], ['chartUp', 112, '#FDC749', '주도', '', '']],
                                   title='반도체가<br>상승 주도', stats=[['업종 흐름', '엇갈림', ''], ['기술주 밖', '혼조', '']]),
                              dict(n='02', items=[['percent', 112, '#FDC749', '국채 수익률', 'dn', '▼'], ['balance', 112, '#5EA0FF', '할인 부담', '', '']],
                                   title='수익률 하락,<br>할인 부담 완화', stats=[['해석', '가능성', ''], ['단정', '하지 않음', '']]),
                              dict(n='03', items=[['basket', 112, '#5EA0FF', '비기술 업종', '', ''], ['check', 112, '#FDC749', '확산', '', '']],
                                   title='상승이<br>넓어지는지', stats=[['확인', '비기술 업종', ''], ['다음', '한국 반도체', '']])])),
        dict(id='board', chapter='시장', type='market_board', claim_ids=['b013', 'b014'],
             narration='에스앤피 500과 나스닥 종합 지수가 모두 올랐습니다.',
             data=dict(title='시장 한눈에', tag='정규장 종가 기준', tiles=[
                 dict(name='S&P 500', value='5,300', dir='up', change='1.92%', sub='정규장 종가 기준', badge=''),
                 dict(name='나스닥 종합', value='17,100', dir='up', change='0.59%', sub='정규장 종가 기준', badge='')])),
        dict(id='head', chapter='핵심 이슈', type='headline', claim_ids=['b007', 'b013'],
             narration='보도에 따르면 미국 지수는 상승했고 반도체가 상승을 주도했습니다.',
             data=dict(chips=['핵심 이슈 1', '당일'], title='반도체 주도와 제한된 확산', image=dict(asset='fixture-illustration'),
                       panel_title='미국 지수', source='Synthetic fixture',
                       stats=[dict(label='S&P 500', value='5,300', dir='up', change='1.92%', sub='정규장 종가 기준'),
                              dict(label='업종 흐름', value='혼조', dir='', change='', sub='기술주 밖 참여')])),
        dict(id='flow', chapter='핵심 이슈', type='flow', claim_ids=['b005', 'b009', 'b007'],
             narration='보도에 따르면 수익률 하락은 성장주의 할인 부담을 낮출 수 있다는 해석입니다.',
             data=dict(chips=['핵심 이슈 1'], title='수익률 하락 → 할인 부담 완화', nodes=[
                 dict(chips=['원인'], icon='percent', head='국채 수익률', text='수익률 하락'),
                 dict(chips=['보도 해석'], icon='balance', head='해석', text='성장주 할인 부담<br>완화 가능'),
                 dict(chips=['결과'], icon='chartUp', head='반도체', text='상승을 주도')])),
        dict(id='photo', chapter='핵심 이슈', type='photo', claim_ids=['b010', 'b011'],
             narration='금리보다 반도체 자체의 재료가 영향을 줬을 수도 있습니다.',
             data=dict(chips=['핵심 이슈 1'], title='다르게 볼 점', image=dict(asset='fixture-photo'), rows=[
                 dict(chip='다르게 볼 점', kind='counter', text='반도체 자체 재료 가능성', sub='금리만의 효과로 보지 않음'),
                 dict(chip='확인할 신호', kind='signal', text='비기술 업종 확산', sub='상승이 넓어지는지 확인'),
                 dict(chip='사실', text='반도체가 상승 주도', sub='업종별 흐름은 엇갈림')])),
        dict(id='counter', chapter='핵심 이슈', type='counter', claim_ids=['b010'],
             narration='다르게 보면 금리보다 반도체 자체의 재료가 영향을 줬을 수도 있습니다.',
             data=dict(chips=['핵심 이슈 1'], title='금리만의 효과일까', icon='balance', topic='다르게 볼 점',
                       blocks=[dict(x=0, label='금리', value='할인 부담', gold=1, sub='수익률 하락의 효과'),
                               dict(x=520, label='반도체', value='자체 재료', sub='업종 고유의 재료')],
                       rows=[dict(icon='check', text='금리만의 효과로 단정하지 않는다'),
                             dict(icon='chartUp', text='비기술 업종으로 상승이 확산되는지 확인')])),
        dict(id='calendar', chapter='일정', type='calendar', claim_ids=['b012'],
             narration='다음에는 한국 반도체 업종으로 강세가 이어지는지 확인합니다.',
             data=dict(title='다음 확인할 것', rows=[['9/22', '화', '한국', 'chip', '한국 반도체 업종 강세 지속 여부', '개장 후']])),
        dict(id='closing', chapter='일정', type='signals', claim_ids=['b011', 'b012'],
             narration='오늘 브리핑은 여기까지입니다. 이 영상은 투자 권유가 아닙니다.',
             data=dict(title='오늘 확인할 신호', rows=[dict(icon='chartUp', topic='확산', conds=['비기술 업종 상승', '한국 반도체 강세'],
                                                         then='상승 확산 판단')], disclaimer='투자 권유가 아닙니다')),
    ]
    value = dict(title='반도체가 이끈 미국 증시, 확산은 아직', thumbnail='반도체가 이끌었다', thumbnail_stat='S&P 500 ▲ 1.92%',
                 thumbnail_image='fixture-illustration', introduction='반도체가 주도한 상승과 확산 여부를 확인 조건과 함께 봅니다.',
                 pinned_comment='어떤 업종의 확산을 확인하고 계신가요?',
                 upload=dict(title='S&P 500 1.92% 상승, 반도체가 이끈 미국 증시 확산은 아직',
                             lead='미국 증시는 반도체가 주도했지만 업종별 흐름은 엇갈렸습니다.',
                             intro='반도체가 주도한 상승과 비기술 업종으로의 확산 여부를 확인 조건과 함께 짚어봅니다.', market='미국',
                             stories=['반도체가 이끈 상승', '수익률 하락과 성장주 부담', '비기술 업종 확산 여부'], hashtags=['#반도체'],
                             tags=['반도체', 'S&P 500', '나스닥 종합', '국채 수익률', '성장주', '업종 흐름', '오늘의 증시', '증시 브리핑',
                                   '주식 시황', '미국 증시', '주식 뉴스', '경제 뉴스', '시황']),
                 ticker=dict(label='정규장 종가', claim_ids=['b013', 'b014'], items=[
                     dict(name='S&P 500', value='5,300', change='1.92%', dir='up'),
                     dict(name='나스닥', value='17,100', change='0.59%', dir='up'),
                     dict(name='S&P 500', value='5,300', change='1.92%', dir='up')]), scenes=scenes)
    value.update(changes)
    return EpisodePlan.model_validate(value)


def library(root, ffmpeg):
    root.mkdir(parents=True, exist_ok=True)
    for name, colour in (('photo.png', 'navy'), ('illu.png', 'gold')):
        run([ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i', f'color=c={colour}:s=1280x720', '-frames:v', '1', str(root / name)])
    shelf = {'fixture-photo': dict(file='photo.png', kind='자료사진', subject='Fixture building', author='Fixture Author',
                                   license='CC BY 4.0', license_url='https://creativecommons.org/licenses/by/4.0/',
                                   source='https://example.invalid/fixture', changes='잘라 냄', tags=['미국증시']),
             'fixture-illustration': dict(file='illu.png', kind='일러스트', subject='fixture', author='뭐든story', license='자체 제작',
                                          source='library', tags=['반도체·메모리'])}
    (root / 'manifest.json').write_text(json.dumps(shelf, ensure_ascii=False))
    return shelf


def test_episode_binds_every_number_type_icon_and_asset(tmp_path):
    shelf = {'fixture-photo': {}, 'fixture-illustration': {}}
    validate_episode(episode(), source(), shelf)
    bad = episode().model_dump(mode='json')
    bad['scenes'][2]['data']['tiles'][0]['change'] = '2.5%'
    with pytest.raises(ValueError, match='Unbound number'):
        validate_episode(EpisodePlan.model_validate(bad), source(), shelf)
    bad = episode().model_dump(mode='json')
    bad['ticker']['items'][0]['value'] = '5,400'
    with pytest.raises(ValueError, match='Ticker'):
        validate_episode(EpisodePlan.model_validate(bad), source(), shelf)
    with pytest.raises(ValueError, match='reviewed asset library'):
        validate_episode(episode(), source(), {'fixture-photo': {}})
    raw = episode().model_dump(mode='json')
    raw['scenes'][2]['type'] = 'sparkline'
    with pytest.raises(ValueError):
        EpisodePlan.model_validate(raw)
    raw = episode().model_dump(mode='json')
    raw['scenes'][2]['narration'] = '에스앤피 500은 +1.92% 올랐습니다.'
    with pytest.raises(ValueError, match='signs as words'):
        EpisodePlan.model_validate(raw)
    raw = episode().model_dump(mode='json')
    raw['scenes'][4]['data']['nodes'][0]['icon'] = 'rocket'
    with pytest.raises(ValueError, match='pictogram'):
        validate_episode(EpisodePlan.model_validate(raw), source(), shelf)
    raw = episode().model_dump(mode='json')
    raw['scenes'][2]['data']['tiles'][0]['sub'] = '정규장 종가 · 05:00 KST'
    with pytest.raises(ValueError, match='Clock time'):
        validate_episode(EpisodePlan.model_validate(raw), source(), shelf)
    raw = episode().model_dump(mode='json')
    raw['scenes'][1], raw['scenes'][2] = raw['scenes'][2], raw['scenes'][1]
    with pytest.raises(ValueError, match='fixed episode order'):
        EpisodePlan.model_validate(raw)


def test_number_check_tolerates_number_words_but_not_missing_amounts():
    assert spoken_numbers_heard('세 가지, 8일', '3가지 8일')
    assert spoken_numbers_heard('1,100억달러', '천백억 달러')
    assert not spoken_numbers_heard('30편', '서른 편')
    assert not spoken_numbers_heard('7일 8일', '8일 7일')


def test_captions_show_the_script_on_real_word_times():
    script = '미국 증시는 반도체가 주도했습니다. 그런데 업종별 흐름은 엇갈렸습니다.'
    words = [{'word': w, 'start': i * 0.5, 'end': i * 0.5 + 0.4} for i, w in enumerate('미국 증시는 반도체가 주도 했습니다 그런데 업종별 흐름은 엇갈렸습니다'.split())]
    cues = captions(word_times(script, words, 5.0), 0.5, 7.0)
    assert [c[2] for c in cues] == ['미국 증시는 반도체가 주도했습니다.', '그런데 업종별 흐름은 엇갈렸습니다.']
    assert cues[0][0] == 0.5 and cues[1][0] >= cues[0][1]


class WordAligner:
    def align(self, audio, expected):
        tokens = expected.split()
        return {'transcript': expected, 'method': 'simulated-even-word-timestamps', 'cues': [],
                'words': [{'word': w, 'start': 0.1 + i * 1.6 / len(tokens), 'end': 0.1 + (i + 1) * 1.6 / len(tokens)} for i, w in enumerate(tokens)]}


def test_motion_render_frames_layout_credits_and_artifact_binding(tmp_path):
    if not importlib.util.find_spec('playwright'):
        pytest.skip('Install optional video dependencies and Chromium')
    ffmpeg = ffmpeg_binary()
    settings = Settings(video_artifact_dir=tmp_path, video_ffmpeg=ffmpeg, video_ffprobe='', video_asset_dir=tmp_path / 'assets',
                        video_render_workers=2)
    library(tmp_path / 'assets', ffmpeg)
    plan = episode()
    paths = []
    for i in range(len(plan.scenes)):
        path = tmp_path / f'audio-{i}.wav'
        run([ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i', f'sine=frequency={330 + i * 60}:sample_rate=48000:duration=9', str(path)])
        paths.append(path)
    job = {'id': 'fixture', 'source': source(), 'policy': {'template': 'motion-v2'}}
    manifest = MotionRenderer(settings, WordAligner()).render(job, plan, tmp_path / 'out', paths)
    assert manifest['template'] == 'motion-v2' and manifest['qa_passed'] and manifest['fps'] == 30
    assert 85 < manifest['duration'] < 100 and manifest['audio_quality']['true_peak_dbfs'] <= -1.5
    upload = (tmp_path / 'out' / 'upload.txt').read_text()
    assert 'Fixture Author · CC BY 4.0' in upload and '변경: 잘라 냄' in upload and 'AI' not in upload
    assert manifest['title'].endswith(' | 9월 22일 아침 · 오늘의 증시story') and manifest['tags'][-2:] == ['증시story', '뭐든story']
    assert '검토용 샘플' not in (tmp_path / 'out' / 'upload.txt').read_text()
    assert '📅 2026.09.22 | 미국 증시' in upload and '자료 기준: 2026.09.22 07:30 (한국시간) · 장전' in upload
    assert '00:00 반도체가 이끌었다' in upload and '#증시story #뭐든story #반도체' in upload and '▶ 증시story 모아보기\nhttps://www.youtube.com/playlist?list=PLbCkACCer37U' in upload
    assert 'https://www.cnbc.com/fixture-market-report.html' in upload
    assert not (tmp_path / 'out' / 'page').exists() and not list((tmp_path / 'out').glob('part-*.mp4'))
    assert not (tmp_path / 'out' / 'narration.wav').exists() and (tmp_path / 'out' / 'video.mp4').exists()
    assert [c['title'] for c in manifest['chapters']][:3] == ['오프닝', '오늘 꼭 알아야 할 세 가지', '시장 한눈에']
    assert [s['id'] for s in manifest['scenes']] == [s.id for s in plan.scenes]
    assert manifest['scenes'][0]['t0'] == 0 and manifest['scenes'][-1]['t1'] == pytest.approx(manifest['duration'], abs=.5)
    assert '• Synthetic fixture / 2026.09.22' in upload
    verify_artifacts(manifest, tmp_path)
    (tmp_path / 'out' / 'subtitles.srt').write_text('changed')
    with pytest.raises(ValueError):
        verify_artifacts(manifest, tmp_path)


def test_overflowing_text_is_reported_and_the_episode_still_renders(tmp_path):
    if not importlib.util.find_spec('playwright'):
        pytest.skip('Install optional video dependencies and Chromium')
    ffmpeg = ffmpeg_binary()
    settings = Settings(video_artifact_dir=tmp_path, video_ffmpeg=ffmpeg, video_ffprobe='', video_asset_dir=tmp_path / 'assets')
    library(tmp_path / 'assets', ffmpeg)
    raw = episode().model_dump(mode='json')
    raw['scenes'][4]['data']['nodes'][1]['text'] = '성장주 할인 부담 완화 가능 ' * 12
    plan = EpisodePlan.model_validate(raw)
    paths = []
    for i in range(len(plan.scenes)):
        path = tmp_path / f'a{i}.wav'
        run([ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=2', str(path)])
        paths.append(path)
    class MisheardAligner(WordAligner):
        def align(self, audio, expected):
            return {**super().align(audio, expected), 'findings': ['Spoken numbers could not be verified in the transcription']}

    manifest = MotionRenderer(settings, MisheardAligner()).render({'id': 'x', 'source': source(), 'policy': {}}, plan,
                                                                 tmp_path / 'out', paths)
    assert any('safe screen area' in finding for finding in manifest['findings'])
    assert any(f.startswith(f"Scene {plan.scenes[0].id}: Spoken numbers") for f in manifest['findings'])
    assert (tmp_path / 'out' / 'layout-qa.json').exists()
    verify_artifacts(manifest, tmp_path)


class EpisodeProvider:
    def __init__(self):
        self.calls = []

    async def run(self, request):
        self.calls.append(request)
        from .test_video import good_review
        value = episode() if request.output_contract == 'video_episode_v1' else good_review()
        return ProviderResponse(request_id=request.request_id, provider='claude', usage={'actual_model': request.model},
                                decision=AgentDecision(status='complete', say='', artifacts=[{'title': 'Simulated', 'content': value.model_dump_json()}]))


async def test_motion_jobs_request_the_episode_contract_with_the_reviewed_library(brief, video, tmp_path):  # noqa: F811
    store, _ = video
    store.settings.video_template = 'motion-v2'
    store.settings.video_asset_dir = tmp_path / 'assets'
    (tmp_path / 'assets').mkdir()
    (tmp_path / 'assets' / 'manifest.json').write_text(json.dumps({'fixture-photo': {'tags': [], 'kind': '자료사진'},
                                                                   'fixture-illustration': {'tags': [], 'kind': '일러스트'}}))
    job = new_job(brief, video)
    assert job['policy']['template'] == 'motion-v2'
    job_source = {**job['source']}
    provider = EpisodeProvider()
    runner = VideoRunner(store.company, provider=provider)
    state = (await runner.tick())['state']
    request = provider.calls[0]
    assert request.output_contract == 'video_episode_v1' and 'fixture-photo' in request.prompt
    assert 'cold_open' in request.prompt and job_source['day']
    from .test_briefing import CONTENT
    # Only the delivered body: neither the raw article text nor its title reach the script writer.
    assert CONTENT[:40] not in request.prompt and 'Synthetic closing report' not in request.prompt
    assert '"b001"' in request.prompt and 'report_label_required' in request.prompt
    assert state == 'reviewing'
    assert EpisodePlan.model_validate(store.get(job['id'])['plan']).ticker.label == '정규장 종가'


def motion_job(brief, video, tmp_path):  # noqa: F811
    store, _ = video
    store.settings.video_template = 'motion-v2'
    store.settings.video_asset_dir = tmp_path / 'assets'
    (tmp_path / 'assets').mkdir()
    (tmp_path / 'assets' / 'manifest.json').write_text(json.dumps({'fixture-photo': {'tags': [], 'kind': '자료사진'},
                                                                   'fixture-illustration': {'tags': [], 'kind': '일러스트'}}))
    return store, new_job(brief, video)


def sent_episodes(provider):
    # The episode prompt ends with its data as one JSON line.
    return [json.loads(c.prompt.rsplit('\n', 1)[1]) for c in provider.calls if c.output_contract == 'video_episode_v1']


async def test_a_rejected_script_is_revised_from_its_last_version_with_the_review_concerns(brief, video, tmp_path):  # noqa: F811
    """10/14 AM: every retry rewrote the episode from scratch (scene IDs changed each time), so a finding fixed in one
    scene came back in another, and a failed check dropped the reviewer's eleven concerns from the next request."""
    from quant_company.video.contracts import VideoReview

    from .test_video import ScriptedProvider, good_review, release

    store, job = motion_job(brief, video, tmp_path)
    concern = VideoReview(facts=True, numbers=True, conditions=True, coverage=True, readability=False, concerns=['결론이 늦게 나옴'])
    unbound = episode().model_dump(mode='json')
    unbound['scenes'][2]['data']['tiles'][0]['change'] = '2.5%'
    provider = ScriptedProvider([episode(), concern, EpisodePlan.model_validate(unbound), episode(), good_review()])
    for _ in range(5):
        release(store, job)
        await VideoRunner(store.company, provider=provider).tick()
    assert store.get(job['id'])['state'] == 'synthesizing'
    first, revised, again = sent_episodes(provider)
    assert 'previous_version' not in first
    assert revised['previous_version'] == episode().model_dump(mode='json') and '결론이 늦게 나옴' in revised['revision_feedback']
    assert again['previous_version']['scenes'][2]['data']['tiles'][0]['change'] == '2.5%'
    assert 'Unbound number' in again['revision_feedback'] and '결론이 늦게 나옴' in again['revision_feedback']


async def test_a_previous_version_that_does_not_fit_the_prompt_is_left_out(brief, video, tmp_path, monkeypatch):  # noqa: F811
    from quant_company.video import runner as runner_module

    from .test_video import ScriptedProvider, release

    store, job = motion_job(brief, video, tmp_path)
    unbound = episode().model_dump(mode='json')
    unbound['scenes'][2]['data']['tiles'][0]['change'] = '2.5%'
    provider = ScriptedProvider([EpisodePlan.model_validate(unbound), episode()])
    await VideoRunner(store.company, provider=provider).tick()
    monkeypatch.setattr(runner_module, 'PROMPT_LIMIT', len(provider.calls[0].prompt) + 2000)
    release(store, job)
    await VideoRunner(store.company, provider=provider).tick()
    assert store.get(job['id'])['state'] == 'reviewing'
    assert 'previous_version' not in sent_episodes(provider)[1] and 'Unbound number' in provider.calls[1].prompt


def test_upload_copy_template_and_checks():
    from datetime import date, datetime

    from quant_company.video.upload import check, compose

    copy = episode().upload
    chapters = [(0, '반도체가 이끌었다'), (10, '오늘의 세 가지'), (35, '시장 한눈에')]
    refs = ['• Synthetic fixture — Synthetic closing report / 2026.09.22', 'https://www.cnbc.com/fixture-market-report.html']
    result = compose(copy, date(2026, 9, 22), datetime(2026, 9, 22, 7, 30), 'am', chapters, refs, 'https://www.youtube.com/playlist?list=fixture')
    text = result['description']
    assert text.startswith(copy.lead + '\n' + copy.intro + '\n\n📅 2026.09.22 | 미국 증시')
    assert '▶ 증시story 모아보기\nhttps://www.youtube.com/playlist?list=fixture' in text
    assert text.endswith('세상의 뭐든, 이야기로 만나다. 뭐든story.\n\n#증시story #뭐든story #반도체')
    for broken in (dict(result, title=copy.title), dict(result, description=text.replace('00:10', '00:03')),
                   dict(result, description=text + '\nhttps://example.invalid/guess'), dict(result, tags=result['tags'][:5])):
        with pytest.raises(ValueError):
            check(broken, refs_allowed={refs[1]}, playlist_url='https://www.youtube.com/playlist?list=fixture')
    raw = episode().model_dump(mode='json')
    raw['upload']['tags'][0] = '비트코인'
    with pytest.raises(ValueError, match='Tag'):
        validate_episode(EpisodePlan.model_validate(raw), source(), {'fixture-photo': {}, 'fixture-illustration': {}})


def test_title_puts_the_series_name_last_and_fits_100_characters():
    from datetime import date, datetime

    from quant_company.video import upload

    copy = episode().upload
    chapters = [(0, '반도체가 이끌었다'), (10, '오늘의 세 가지'), (35, '시장 한눈에')]
    am = upload.compose(copy, date(2026, 9, 22), datetime(2026, 9, 22, 7, 30), 'am', chapters, [])
    assert am['title'] == copy.title + ' | 9월 22일 아침 · 오늘의 증시story'
    pm = upload.compose(copy, date(2026, 10, 8), datetime(2026, 10, 8, 16, 30), 'close', chapters, [])
    assert pm['title'].endswith(' | 10월 8일 마감 · 오늘의 증시story') and len(pm['title']) <= 100
    assert upload.UploadCopy.model_fields['title'].metadata[-1].max_length == 100 - upload.SUFFIX_MAX
    for broken in ('[오늘의 증시story] 반도체가 이끌었다', '반도체가 이끌었다 | 9월 22일 아침 브리핑'):
        with pytest.raises(ValueError, match='오늘의 증시story'):
            upload.check({'title': broken, 'description': '', 'tags': []})
    raw = episode().model_dump(mode='json')
    raw['upload']['title'] = '반도체가 이끈 하루 | 9월 22일 아침'
    with pytest.raises(ValueError, match='hook only'):
        EpisodePlan.model_validate(raw)


@pytest.mark.parametrize('title,error', [
    ('반도체가 이끈 미국 증시, 업종 흐름은 엇갈려 확산 여부를 확인', '20 characters'),
    ('S&P 500 1.92% 급락? 반도체가 이끈 하루', '급락'),
    ('S&P 500 1.92% 상승, 지금 사야 할까 반도체', 'instruction'),
])
def test_title_rules_from_the_brief_body(title, error):
    raw = episode().model_dump(mode='json')
    raw['upload']['title'] = title
    with pytest.raises(ValueError, match=error):
        validate_episode(EpisodePlan.model_validate(raw), source(), {'fixture-photo': {}, 'fixture-illustration': {}})
    raw['upload']['title'] = '나스닥 종합과 S&P, 반도체가 이끈 상승 확산은 아직'  # instrument names from the body
    validate_episode(EpisodePlan.model_validate(raw), source(), {'fixture-photo': {}, 'fixture-illustration': {}})


def test_drop_thresholds_and_intraday_label():
    from quant_company.video.episode import check_title

    plan = episode()
    lines = {'b1': {'text': '코스피는 2.62% 내린 6,625.93으로 장을 마쳤다'}, 'b2': {'text': 'SK스퀘어 -8.06% 105만원'},
             'b3': {'text': 'SK하이닉스는 장중 2.09% 오른 175만 9천원까지'}}
    ok = plan.model_copy(deep=True)
    ok.upload.title = '코스피 2.62% 급락, 삼성전자 실적에도'
    check_title(ok, lines)
    weak = {'b1': {'text': '코스피는 1.98% 내린 6,803.90'}, 'b2': {'text': '삼성전자 -2.42% 26만2천원'}}
    ok.upload.title = '코스피 1.98% 급락, 반도체 매수는 어디로'
    with pytest.raises(ValueError, match='급락'):
        check_title(ok, weak)
    ok.upload.title = 'SK하이닉스 2.09% 상승 뒤 하락 전환'
    with pytest.raises(ValueError, match='장중'):
        check_title(ok, lines)
    ok.upload.title = 'SK하이닉스 장중 2.09% 상승 뒤 하락 전환'
    check_title(ok, lines)


def test_a_close_sharing_a_line_with_an_intraday_low_needs_no_intraday_label():
    """2026-10-14 AM: five script versions were discarded because the close (5.46%) shared its body line with the
    intraday low; '장중' belongs only to the sentence that states the intraday value."""
    from quant_company.video.episode import check_title

    lines = {'b1': {'text': '13일 코스피는 전날보다 5.46% 내린 6,035.35에 마감했다. 장중 5,911.11(-7.41%)까지 밀리며 '
                            '매도 사이드카가 발동됐지만 장 후반 6천선을 회복했다.'}}
    ok = episode().model_copy(deep=True)
    ok.upload.title, ok.thumbnail, ok.thumbnail_stat = '코스피 5.46% 급락, 성장률 상향과 왜 엇갈렸나', '코스피 ▼5.46% 왜', '코스피 ▼5.46% · 정규장 종가 기준'
    check_title(ok, lines)
    ok.upload.title = '코스피 7.41% 밀린 뒤 6천선 회복'
    with pytest.raises(ValueError, match='장중'):
        check_title(ok, lines)
    ok.upload.title = '코스피 장중 7.41% 밀린 뒤 6천선 회복'
    check_title(ok, lines)


@pytest.mark.parametrize('line,shown,error', [
    # Yields share the line with a futures flow (10/14 AM, four versions discarded): only the flow amount needs the label.
    ('국고채 10년물 금리는 연 4.306%에서 4.229%로 내렸다. 외국인은 10년 국채선물 2,996계약을 순매수했다.', '국고채 10년물 4.229%', None),
    ('국고채 10년물 금리는 연 4.306%에서 4.229%로 내렸다. 외국인은 10년 국채선물 2,996계약을 순매수했다.', '외국인 국채선물 2,996계약', '장 마감 기준'),
    # The line says the close was not provided (10/14 AM, UnitedHealth): the pre-market move is not a close value.
    ('주가는 장전 약 3% 상승했으며 정규장 종가 반응은 제공되지 않았다.', '장전 약 3% 상승', None),
    ('주가는 정규장 종가 기준 3% 상승했다.', '주가 3% 상승', '정규장 종가 기준'),
])
def test_labels_follow_the_sentence_that_states_the_value(line, shown, error):
    from quant_company.video.episode import episode_violations

    src = source()
    src['body'] = [src['body'][0].replace('• 한국 반도체 업종으로 강세가 이어지는지 확인합니다.', f'• {line}')] + src['body'][1:]
    raw = episode().model_dump(mode='json')
    raw['scenes'][7]['data']['rows'][0][4] = shown
    raw['scenes'][7]['narration'] = '다음에는 이 숫자가 이어지는지 확인합니다.'
    found = [f for f in episode_violations(EpisodePlan.model_validate(raw), src, {'fixture-photo': {}, 'fixture-illustration': {}})
             if 'scene calendar' in f]
    assert found == ([] if error is None else [f'{"Investor flows" if error == "장 마감 기준" else "Regular-session close values"} '
                                                f'need {error} in scene calendar'])


def test_tags_ignore_spaces_and_an_unlisted_tag_is_dropped_not_fatal():
    """10/14 AM: '미국증시' failed where '미국 증시' passes, and one tag discarded a whole script."""
    from quant_company.video.episode import CARD_GUIDE, GENERAL_TAGS, episode_violations, tidy_tags

    shelf = {'fixture-photo': {}, 'fixture-illustration': {}}
    raw = episode().model_dump(mode='json')
    raw['upload']['tags'][-4:] = ['미국증시', '경제뉴스', '주식시장', '주식뉴스']
    assert not [f for f in episode_violations(EpisodePlan.model_validate(raw), source(), shelf) if f.startswith('Tag')]
    raw['upload']['tags'] = ['반도체', 'S&P 500', '아침 브리핑', '외국인 순매도'] + raw['upload']['tags'][4:]
    raw['upload']['hashtags'] = ['#순매도']
    tidy = tidy_tags(EpisodePlan.model_validate(raw))
    assert '아침 브리핑' not in tidy.upload.tags and '외국인 순매도' not in tidy.upload.tags and len(tidy.upload.tags) >= 13
    assert tidy.upload.tags[:2] == ['반도체', 'S&P 500'] and tidy.upload.hashtags == ['#증시']
    assert not [f for f in episode_violations(tidy, source(), shelf) if f.startswith('Tag')]
    assert tidy_tags(episode()) == episode()  # a clean plan is left as it is
    assert all(tag in CARD_GUIDE for tag in GENERAL_TAGS)


@pytest.mark.parametrize('mutation,error', [
    ('tool', 'tool wording'), ('report', '보도에 따르면'), ('close', '정규장 종가 기준'), ('flow', '장 마감 기준'),
    ('few', 'scenes'),
])
def test_body_attribution_labels_and_no_tool_wording(mutation, error):
    src = source()
    shelf = {'fixture-photo': {}, 'fixture-illustration': {}}
    raw = episode().model_dump(mode='json')
    if mutation == 'tool':
        raw['scenes'][5]['data']['rows'][0]['sub'] = 'AI 분석 결과'
    elif mutation == 'report':
        raw['scenes'][0]['narration'] = '미국 증시는 반도체가 주도했습니다. 그런데 업종별 흐름은 엇갈렸습니다.'
    elif mutation == 'close':
        for tile in raw['scenes'][2]['data']['tiles']:
            tile['sub'] = '미국 지수'
        raw['scenes'][2]['data']['tag'] = '미국 지수'
    elif mutation == 'flow':
        src['body'] = [src['body'][0].replace('• 한국 반도체 업종으로 강세가 이어지는지 확인합니다.',
                                              '• 외국인은 1조9천921억원 순매도했습니다.')] + src['body'][1:]
        raw['scenes'][7]['data']['rows'][0][4] = '외국인 1조9천921억원 순매도'
        raw['scenes'][7]['narration'] = '외국인은 1조9천921억원 순매도했습니다.'
    else:
        raw['scenes'] = raw['scenes'][:3] + raw['scenes'][-1:]
    with pytest.raises(ValueError, match=error):
        validate_episode(EpisodePlan.model_validate(raw), src, shelf)


def test_fact_list_edition_uses_the_short_episode():
    shelf = {'fixture-photo': {}, 'fixture-illustration': {}}
    full = episode().model_dump(mode='json')
    keep = ['hook', 'board', 'head', 'photo', 'calendar', 'closing']
    raw = dict(full, scenes=[s for s in full['scenes'] if s['id'] in keep])
    raw['upload']['tags'] = ['반도체', 'S&P 500', '나스닥 종합', '업종 흐름', '오늘의 증시', '증시 브리핑', '주식 시황', '미국 증시',
                             '주식 뉴스', '경제 뉴스', '시황', '주식', '증시']
    validate_episode(EpisodePlan.model_validate(raw), source() | {'format': 'facts'}, shelf)
    with pytest.raises(ValueError, match='scenes'):
        validate_episode(EpisodePlan.model_validate(raw), source(), shelf)  # too short for a full edition
    with pytest.raises(ValueError, match='Fact-list'):
        validate_episode(episode(), source() | {'format': 'facts'}, shelf)  # full episode for a fact list



async def test_a_still_running_episode_turn_is_resent_identically_after_jsonb_round_trip(brief, video, tmp_path):  # noqa: F811
    from quant_company.contracts import ProviderFault

    from .test_video import new_job

    store, _ = video
    store.settings.video_template = 'motion-v2'
    store.settings.video_asset_dir = tmp_path / 'assets'
    (tmp_path / 'assets').mkdir()
    # JSONB stores shorter keys first: this insertion order does not survive a round trip.
    (tmp_path / 'assets' / 'manifest.json').write_text(json.dumps({
        'fixture-photo-long-name': {'tags': [], 'kind': '자료사진'}, 'fixture-photo': {'tags': [], 'kind': '자료사진'},
        'fixture-illustration': {'tags': [], 'kind': '일러스트'}}))
    job = new_job(brief, video)
    provider = EpisodeProvider()
    pending = ProviderFault('pending', 'still running', 20)
    original = provider.run

    async def first_pending(request):
        if not provider.calls:
            provider.calls.append(request)
            raise pending
        return await original(request)

    provider.run = first_pending
    await VideoRunner(store.company, provider=provider).tick()
    assert store.get(job['id'])['error'] == 'model_pending'
    with store.db.transaction() as conn:
        conn.execute('UPDATE video_jobs SET lease_until=NULL WHERE id=%s', (job['id'],))
    await VideoRunner(store.company, provider=provider).tick()
    current = store.get(job['id'])
    assert current['state'] != 'blocked', current['error']
    assert provider.calls[0].prompt == provider.calls[1].prompt


class InspectingProvider:
    """Answers by contract from per-contract queues (plan, review, visual review, screen fix)."""

    def __init__(self, visual, fixes=()):
        from .test_video import good_review

        self.queues = {'video_episode_v1': [episode()], 'video_review_v1': [good_review()],
                       'video_visual_review_v1': list(visual), 'video_screen_fix_v1': list(fixes)}
        self.calls = []

    async def run(self, request):
        self.calls.append(request)
        queue = self.queues[request.output_contract]
        value = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(value, Exception):
            raise value
        return ProviderResponse(request_id=request.request_id, provider='claude', usage={'actual_model': request.model},
                                decision=AgentDecision(status='complete', say='', artifacts=[{'title': 'Simulated', 'content': value.model_dump_json()}]))


class InspectedRenderer:
    """Writes review frames like MotionRenderer; the first render can report a misheard scene."""

    def __init__(self, misheard=None, renderer_code=None):
        self.renders, self.misheard, self.texts, self.renderer_code = 0, misheard or {}, [], renderer_code

    def render(self, job, plan, directory, paths, texts=None):
        from quant_company.video.contracts import digest
        from quant_company.video.render import checksum

        self.renders += 1
        self.texts.append(texts)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'review').mkdir(exist_ok=True)
        files = {'video.mp4': b'simulated-video', 'thumbnail.png': b'simulated-thumbnail', 'plan.json': plan.model_dump_json().encode()}
        for name, content in files.items():
            (directory / name).write_bytes(content)
        frames = []
        for n, scene in enumerate(plan.scenes):
            (directory / f'review/{n:02}-{scene.id}.jpg').write_bytes(b'\xff\xd8simulated-frame')
            frames.append({'scene': scene.id, 'file': f'review/{n:02}-{scene.id}.jpg'})
        manifest = {'directory': str(directory), 'qa_passed': True, 'human_watched': False, 'duration': 60., 'findings': [],
                    'review': {'frames': frames + [{'scene': 'thumbnail', 'file': 'thumbnail.png'}],
                               'audio': self.misheard if self.renders == 1 else {}},
                    'files': {n: checksum(directory / n) for n in files}}
        if self.renderer_code:  # what a render image built from other code reports
            manifest['renderer'] = self.renderer_code
        else:
            from quant_company.video.motion import RENDERER_DIGEST

            manifest['renderer'] = RENDERER_DIGEST
        manifest['digest'] = digest(manifest)
        return manifest


async def produce_inspected(store, tmp_path, monkeypatch, provider, renderer, brief):  # noqa: F811
    from .test_video import SimulatedSpeech, new_job, release

    store.settings.video_template = 'motion-v2'
    store.settings.video_asset_dir = tmp_path / 'assets'
    (tmp_path / 'assets').mkdir()
    (tmp_path / 'assets' / 'manifest.json').write_text(json.dumps({'fixture-photo': {'tags': [], 'kind': '자료사진'},
                                                                   'fixture-illustration': {'tags': [], 'kind': '일러스트'}}))
    monkeypatch.setattr('quant_company.video.runner.download_audio', lambda url, path: path.write_bytes(b'simulated-speech'))
    job = new_job(brief, (store, None))
    speech = SimulatedSpeech()
    for _ in range(60):
        release(store, job)  # a pause the runner asked for (model_retry, render waits) has passed
        await VideoRunner(store.company, provider, speech, renderer).tick()
        if store.get(job['id'])['state'] not in {'queued', 'reviewing', 'synthesizing', 'rendering', 'inspecting'}:
            break
    return store.get(job['id']), speech


async def test_final_visual_review_fixes_cards_rerenders_and_delivers(brief, video, tmp_path, monkeypatch):  # noqa: F811
    from quant_company.video.contracts import VisualReview
    from quant_company.video.episode import ScreenFix

    store, _ = video
    shorter = {**episode().scenes[3].data, 'title': '반도체 주도'}
    provider = InspectingProvider(
        visual=[VisualReview(passed=False, issues=[{'scene': 'head', 'kind': 'clipped_text', 'detail': 'title clipped',
                                                    'fix': 'shorten the title'}]), VisualReview(passed=True, issues=[])],
        fixes=[ScreenFix(scenes=[{'id': 'head', 'data': shorter}])])
    renderer = InspectedRenderer()
    job, speech = await produce_inspected(store, tmp_path, monkeypatch, provider, renderer, brief)
    assert job['state'] == 'uploading', job['error']
    contracts = [c.output_contract for c in provider.calls]
    assert contracts.count('video_visual_review_v1') == 2 and contracts.count('video_screen_fix_v1') == 1
    visual = next(c for c in provider.calls if c.output_contract == 'video_visual_review_v1')
    assert len(visual.attachments) == len(episode().scenes) + 1 and visual.attachments[0].media_type == 'image/jpeg'
    assert renderer.renders == 2 and len(speech.calls) == len(episode().scenes)  # narration unchanged: no new takes
    assert next(s for s in job['plan']['scenes'] if s['id'] == 'head')['data']['title'] == '반도체 주도'
    assert len(job['qa']['inspections']) == 2 and job['qa']['inspection_findings'] == []


async def test_misread_scene_is_rerecorded_in_number_words(brief, video, tmp_path, monkeypatch):  # noqa: F811
    from quant_company.video.contracts import VisualReview
    from quant_company.video.render import korean_numbers

    store, _ = video
    provider = InspectingProvider(visual=[
        VisualReview(passed=False, issues=[{'scene': 'board', 'kind': 'audio_text', 'detail': '500 misread',
                                            'fix': 're-record'}]), VisualReview(passed=True, issues=[])])
    renderer = InspectedRenderer(misheard={'board': ['Spoken numbers could not be verified in the transcription']})
    job, speech = await produce_inspected(store, tmp_path, monkeypatch, provider, renderer, brief)
    assert job['state'] == 'uploading', job['error']
    board = next(s for s in episode().scenes if s.id == 'board')
    assert len(speech.calls) == len(episode().scenes) + 1 and speech.calls[-1]['text'] == korean_numbers(board.narration)
    assert '오백' in speech.calls[-1]['text'] and renderer.renders == 2
    assert renderer.texts[1][[s.id for s in episode().scenes].index('board')] == korean_numbers(board.narration)
    with store.db.transaction() as conn:
        keys = {r['id'].split(':')[1] for r in conn.execute("SELECT id FROM video_effects WHERE kind='speech'").fetchall()}
    index = [s.id for s in episode().scenes].index('board')
    assert f'speech-{index}' in keys and any(k.startswith(f'speech-{index}-') for k in keys)


async def test_unfixable_visual_findings_never_block_delivery(brief, video, tmp_path, monkeypatch):  # noqa: F811
    from quant_company.contracts import ProviderFault
    from quant_company.video.contracts import VisualReview

    store, _ = video
    stuck = VisualReview(passed=False, issues=[{'scene': 'flow', 'kind': 'overlap', 'detail': 'arrow overlaps text',
                                                'fix': 'move the arrow'}])
    provider = InspectingProvider(visual=[stuck], fixes=[ProviderFault('invalid_output', 'Video output failed its contract: scenes.0.data')])
    job, _ = await produce_inspected(store, tmp_path, monkeypatch, provider, InspectedRenderer(), brief)
    assert job['state'] == 'uploading', job['error']
    assert job['qa']['inspection_findings'] == ['flow: arrow overlaps text']


def test_interrupted_render_resumes_scene_by_scene_without_realigning(tmp_path, monkeypatch):
    if not importlib.util.find_spec('playwright'):
        pytest.skip('Install optional video dependencies and Chromium')
    from quant_company.video import motion

    ffmpeg = ffmpeg_binary()
    settings = Settings(video_artifact_dir=tmp_path, video_ffmpeg=ffmpeg, video_ffprobe='', video_asset_dir=tmp_path / 'assets')
    library(tmp_path / 'assets', ffmpeg)
    plan = episode()
    paths = []
    for i in range(len(plan.scenes)):
        path = tmp_path / f'a{i}.wav'
        run([ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=2', str(path)])
        paths.append(path)

    class CountingAligner(WordAligner):
        calls = 0

        def align(self, audio, expected):
            CountingAligner.calls += 1
            return super().align(audio, expected)

    rendered, original = [], motion.render_segments
    monkeypatch.setattr(motion, 'render_segments', lambda page, items, ffmpeg, digest: (
        rendered.append([n for n, *_ in items]), original(page, items, ffmpeg, digest))[1])
    job, out = {'id': 'x', 'source': source(), 'policy': {}}, tmp_path / 'out'
    first = MotionRenderer(settings, CountingAligner()).render(job, plan, out, paths)
    assert CountingAligner.calls == len(plan.scenes) and sorted(sum(rendered, [])) == [f'{n:02}-00' for n in range(len(plan.scenes))]
    # An interruption after scene 3 was encoded but before scene 4 finished: only scene 4 is rendered again.
    (out / 'seg-04-00.mp4').unlink()
    (out / 'manifest.json').unlink()
    rendered.clear()
    second = MotionRenderer(settings, CountingAligner()).render(job, plan, out, paths)
    assert CountingAligner.calls == len(plan.scenes)  # alignment reused from the attempt directory
    assert rendered == [['04-00']] and second['frames'] == first['frames']
    verify_artifacts(second, tmp_path)


def test_a_render_still_running_in_the_directory_is_not_started_twice(tmp_path):
    import fcntl
    import os

    from quant_company.video.render import RenderInProgress

    out = tmp_path / 'out'
    out.mkdir()
    held = os.open(out / '.render.lock', os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(held, fcntl.LOCK_EX)
    try:
        with pytest.raises(RenderInProgress):
            MotionRenderer(Settings(video_artifact_dir=tmp_path), WordAligner()).render(
                {'id': 'x', 'source': source(), 'policy': {}}, episode(), out, [])
    finally:
        os.close(held)



def test_episodes_are_capped_near_five_minutes():
    from quant_company.video.episode import NARRATION_LIMIT

    raw = episode().model_dump(mode='json')
    raw['scenes'][2]['narration'] = '가' * 600
    while sum(len(s['narration']) for s in raw['scenes']) <= NARRATION_LIMIT:
        raw['scenes'].insert(3, {**raw['scenes'][2], 'id': f"pad{len(raw['scenes'])}"})
    with pytest.raises(ValueError, match='daily production limit'):
        EpisodePlan.model_validate(raw)


def test_the_screen_shows_one_arrow_where_the_template_draws_it_from_the_direction():
    from quant_company.video.motion import single_arrows

    # Values written as the 2026-10-12 morning script did ("▲0.59%" next to dir='up').
    cold = {'stats': [{'label': 'S&P500', 'value': '▲0.59%', 'big_dir': 'up', 'sub': '▼2.62% · 종가', 'sub_dir': 'dn'},
                      {'label': '코스피', 'value': '6,625.93', 'big_dir': 'dn', 'sub': '종가'}]}
    board = {'tiles': [{'name': '코스피', 'value': '6,625.93', 'change': '▼ 2.62%', 'dir': 'dn'},
                       {'name': 'WTI', 'value': '91.85달러', 'change': '0.39%', 'dir': 'up'}]}
    flow = {'nodes': [{'rows': [['외국인', '▼6조4692억원', 'dn'], ['개인', '6조3596억원', 'up']]}]}
    bars = {'image': {'asset': 'x', 'labels': [{'text': '유가 ▲', 'dir': 'up'}]}}
    # Since 2026-10-14 the summary tiles and counter blocks draw the arrow too (10/13 PM showed '5.46%' with no ▼).
    summary = {'cards': [{'stats': [['코스피', '▼2.62%', 'dn'], ['코스닥', '1.43%', 'dn']]}]}
    counter = {'blocks': [{'label': '미국 경유 재고', 'value': '▼14%', 'dir': 'dn'}, {'label': '하락 종목', 'value': '약 820개'}]}
    calendar = {'side': [{'label': '환율', 'value': '▲1,420원', 'dir': 'up'}]}  # colour only: kept
    ticker = {'items': [{'name': '나스닥', 'value': '27,366.17', 'change': '▲0.64%', 'dir': 'up'}]}
    assert single_arrows('cold_open', cold) == [] and single_arrows('market_board', board) == []
    assert [s['value'] for s in cold['stats']] == ['0.59%', '6,625.93'] and cold['stats'][0]['sub'] == '▼2.62% · 종가'
    assert [t['change'] for t in board['tiles']] == ['2.62%', '0.39%']
    assert single_arrows('flow', flow) == [] and flow['nodes'][0]['rows'][0][1] == '6조4692억원'
    assert single_arrows('bars', bars) == [] and bars['image']['labels'][0]['text'] == '유가'
    assert single_arrows('summary3', summary) == [] and [x[1] for x in summary['cards'][0]['stats']] == ['2.62%', '1.43%']
    assert single_arrows('counter', counter) == [] and [b['value'] for b in counter['blocks']] == ['14%', '약 820개']
    assert single_arrows('calendar', calendar) == [] and calendar['side'][0]['value'] == '▲1,420원'
    assert single_arrows('', {}, ticker) == [] and ticker['items'][0]['change'] == '0.64%'
    # A written sign that contradicts the direction is reported; the screen follows the direction field.
    wrong = {'stats': [{'label': '미 10년물', 'value': '2bp', 'change': '▼2bp', 'dir': 'up'}]}
    assert single_arrows('headline', wrong) == ['headline ▼2bp'] and wrong['stats'][0]['change'] == '2bp'


def test_the_thumbnail_names_its_edition_and_carries_the_watched_progress_strip():
    from importlib.resources import files

    html = files('quant_company.video').joinpath('design', 'thumbnail.html').read_text()
    assert '아침 시장 브리핑' not in html and '{{brand}}' in html  # the close edition reads 마감 브리핑
    # Every thumbnail carries the red strip at half width along its bottom edge (owner request 2026-10-12).
    assert '<div class="resume"><i></i></div>' in html and 'width:50%;background:#FF0000' in html


async def test_an_uncertain_model_turn_is_a_failed_attempt_and_the_episode_is_still_delivered(brief, video, tmp_path,  # noqa: F811
                                                                                                monkeypatch):
    """2026-10-12 PM: a plan turn ended uncertain and the whole episode stopped for an operator."""
    from quant_company.contracts import ProviderFault
    from quant_company.video.contracts import VisualReview

    store, _ = video
    provider = InspectingProvider(visual=[VisualReview(passed=True, issues=[])])
    provider.queues['video_episode_v1'] = [ProviderFault('uncertain', 'Claude did not confirm a complete review.'), episode()]
    job, _ = await produce_inspected(store, tmp_path, monkeypatch, provider, InspectedRenderer(), brief)
    assert job['state'] == 'uploading', job['error']
    attempts = job['qa']['plan_attempts']
    assert [a['key'] for a in attempts] == ['plan', 'plan-2'] and attempts[0]['outcome'] == 'uncertain'


def test_a_screen_fix_naming_an_image_outside_the_library_keeps_that_scene():
    """2026-10-12 PM: a screen fix invented the image 'bank-of-korea'; every render then stopped on the missing file."""
    from quant_company.video.episode import ScreenFix, apply_screen_fix

    plan = episode()
    photo = next(s for s in plan.scenes if s.id == 'photo')
    head = next(s for s in plan.scenes if s.id == 'head')
    invented = {**photo.data, 'image': {'asset': 'bank-of-korea'}}
    shorter = {**head.data, 'title': '반도체 주도'}
    fix = ScreenFix(scenes=[{'id': 'photo', 'data': invented}, {'id': 'head', 'data': shorter}])
    reverted = []
    fixed = apply_screen_fix(plan, fix, {'fixture-photo': {}, 'fixture-illustration': {}}, reverted)
    assert reverted == ['photo'] and next(s for s in fixed.scenes if s.id == 'photo').data == photo.data
    assert next(s for s in fixed.scenes if s.id == 'head').data['title'] == '반도체 주도'
    assert next(s for s in apply_screen_fix(plan, fix).scenes if s.id == 'photo').data['image'] == {'asset': 'bank-of-korea'}


def test_an_issue_first_episode_needs_no_market_board_but_opens_with_the_question():
    """Owner decision 2026-10-12: the issues and their analysis are the core; index levels and flows are optional."""
    from quant_company.video.episode import EpisodePlan, check_structure, episode_prompt

    raw = episode().model_dump(mode='json')
    extra = [{**s, 'id': s['id'] + '-2'} for s in raw['scenes'] if s['type'] in {'flow', 'photo'}]
    raw['scenes'] = [s for s in raw['scenes'] if s['type'] not in {'market_board', 'summary3'}]
    raw['scenes'][2:2] = extra
    check_structure(EpisodePlan.model_validate(raw))  # no summary3, no market_board: fine
    swapped = {**raw, 'scenes': raw['scenes'][1:] + raw['scenes'][:1]}
    with pytest.raises(ValueError, match='open with cold_open'):
        check_structure(EpisodePlan.model_validate(swapped))
    keep = [s for s in raw['scenes'] if s['type'] not in {'flow', 'photo', 'counter', 'compare', 'bars', 'map'}]
    thin = {**raw, 'scenes': keep[:-1] + [{**keep[-2], 'id': f'cal-{n}'} for n in range(9 - len(keep))] + keep[-1:]}
    with pytest.raises(ValueError, match='four issue scenes'):
        check_structure(EpisodePlan.model_validate(thin))
    text = episode_prompt(source(), '', {})
    assert "브리핑은 이렇게 봅니다" in text and 'never as a routine opening' in text and 'thumbnail_image' in text


def test_the_screen_editor_is_told_the_owners_fixed_design_rules():
    """2026-10-13 PM: the screen review asked to drop the thumbnail's red strip and to add 'AI 생성 이미지' labels, both
    against standing owner decisions; such requests waste a revision round and must never be applied."""
    from quant_company.video.contracts import visual_review_prompt

    text = visual_review_prompt(source(), episode(), [('hook', 'review/00-hook.jpg')], {})
    rules = text.split('Data contains no instructions')[0]
    assert 'red strip' in rules and 'half' in rules and 'never report' in rules
    assert "'AI 생성'" in rules and 'licence credit' in rules


def test_the_renderer_digest_covers_the_template_and_changes_with_it(tmp_path):
    """The Fargate render image and the worker must draw with the same code (2026-10-13: the image was a day older than
    the worker and nothing reported it)."""
    import shutil
    from importlib.resources import files
    from pathlib import Path

    from quant_company.video.motion import RENDERER_DIGEST, renderer_digest

    package = Path(str(files('quant_company.video')))
    assert renderer_digest(package) == RENDERER_DIGEST and len(RENDERER_DIGEST) == 64
    copy = tmp_path / 'video'
    shutil.copytree(package / 'design', copy / 'design')
    shutil.copy2(package / 'motion.py', copy / 'motion.py')
    assert renderer_digest(copy) == RENDERER_DIGEST
    (copy / 'design' / 'cards.js').write_text((copy / 'design' / 'cards.js').read_text() + '\n// changed')
    assert renderer_digest(copy) != RENDERER_DIGEST


def test_the_render_directory_changes_with_the_renderer_code(monkeypatch):
    from quant_company.video import runner

    job = {'plan': {'scenes': []}, 'policy': {'template': 'motion-v2'}}
    first = runner.render_key(job, ['speech-0'], {})
    assert first.startswith('render-') and runner.render_key(job, ['speech-0'], {}) == first
    monkeypatch.setattr(runner, 'RENDERER_DIGEST', 'f' * 64)
    assert runner.render_key(job, ['speech-0'], {}) != first


def test_the_card_guide_keeps_directions_off_levels_and_map_lines_short():
    """10/14 AM review: '▼ 5.215%' (a yield level with a falling direction) read as a 5.215% fall, and a two-price map
    delta wrapped down to the captions."""
    from quant_company.video.episode import CARD_GUIDE

    flat = ' '.join(CARD_GUIDE.split())
    assert 'a level such as an index close, a yield or a price takes no direction' in flat
    assert 'delta is one short line' in flat

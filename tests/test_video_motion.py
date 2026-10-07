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

from .test_briefing import brief, bundle, complete, proposal  # noqa: F401
from .test_video import new_job, video  # noqa: F401
from .test_video_render import ffmpeg_binary


def source():
    return {'proposal': proposal().model_dump(mode='json'), 'bundle': bundle(), 'day': '2026-09-22',
            'cutoff': '2026-09-22 07:30 KST', 'rendered': {}}


def episode(**changes):
    scenes = [
        dict(id='hook', chapter='', type='cold_open', claim_ids=['summary', 'overview', 'sp500', 'nasdaq'],
             narration='미국 증시는 반도체가 주도했습니다. 그런데 업종별 흐름은 엇갈렸습니다.',
             data=dict(kicker='9월 22일 화요일 · 아침 시장 브리핑', question=[[['반도체가', 1], ['이끌었다', 0]]],
                       stats=[dict(label='S&P 500', value='5,300', sub='▲ 1.92%', sub_dir='up'),
                              dict(label='나스닥 종합', value='17,100', sub='▲ 0.59%', sub_dir='up'),
                              dict(label='업종 흐름', value='혼조', sub='기술주 밖 참여는 혼조')])),
        dict(id='board', chapter='시장', type='market_board', claim_ids=['sp500', 'nasdaq'],
             narration='에스앤피 500과 나스닥 종합 지수가 모두 올랐습니다.',
             data=dict(title='시장 한눈에', tag='정규장 종가', tiles=[
                 dict(name='S&P 500', value='5,300', dir='up', change='1.92%', sub='정규장 종가', badge=''),
                 dict(name='나스닥 종합', value='17,100', dir='up', change='0.59%', sub='정규장 종가', badge='')])),
        dict(id='flow', chapter='핵심 이슈', type='flow', claim_ids=['overview', 'mechanism', 'fact'],
             narration='수익률 하락은 성장주의 할인 부담을 낮출 수 있다는 해석입니다.',
             data=dict(chips=['핵심 이슈 1'], title='수익률 하락 → 할인 부담 완화', nodes=[
                 dict(chips=['원인'], icon='percent', head='국채 수익률', text='수익률 하락'),
                 dict(chips=['보도 해석'], icon='balance', head='해석', text='성장주 할인 부담<br>완화 가능'),
                 dict(chips=['결과'], icon='chartUp', head='반도체', text='상승을 주도')])),
        dict(id='photo', chapter='핵심 이슈', type='photo', claim_ids=['alternative', 'condition'],
             narration='금리보다 반도체 자체의 재료가 영향을 줬을 수도 있습니다.',
             data=dict(chips=['핵심 이슈 1'], title='다르게 볼 점', image=dict(asset='fixture-photo'), rows=[
                 dict(chip='다르게 볼 점', kind='counter', text='반도체 자체 재료 가능성', sub='금리만의 효과로 보지 않음'),
                 dict(chip='확인할 신호', kind='signal', text='비기술 업종 확산', sub='상승이 넓어지는지 확인'),
                 dict(chip='사실', text='반도체가 상승 주도', sub='업종별 흐름은 엇갈림')])),
        dict(id='closing', chapter='일정', type='signals', claim_ids=['condition', 'watch'],
             narration='오늘 브리핑은 여기까지입니다. 이 영상은 투자 권유가 아닙니다.',
             data=dict(title='오늘 확인할 신호', rows=[dict(icon='chartUp', topic='확산', conds=['비기술 업종 상승', '한국 반도체 강세'],
                                                         then='상승 확산 판단')], disclaimer='투자 권유가 아닙니다')),
    ]
    value = dict(title='반도체가 이끈 미국 증시, 확산은 아직', thumbnail='반도체가 이끌었다', thumbnail_stat='S&P 500 ▲ 1.92%',
                 thumbnail_image='fixture-illustration', introduction='반도체가 주도한 상승과 확산 여부를 확인 조건과 함께 봅니다.',
                 pinned_comment='어떤 업종의 확산을 확인하고 계신가요?',
                 ticker=dict(label='정규장 종가', claim_ids=['sp500', 'nasdaq'], items=[
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
    bad['scenes'][1]['data']['tiles'][0]['change'] = '2.5%'
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
    raw['scenes'][1]['narration'] = '에스앤피 500은 +1.92% 올랐습니다.'
    with pytest.raises(ValueError, match='signs as words'):
        EpisodePlan.model_validate(raw)
    raw = episode().model_dump(mode='json')
    raw['scenes'][2]['data']['nodes'][0]['icon'] = 'rocket'
    with pytest.raises(ValueError, match='pictogram'):
        validate_episode(EpisodePlan.model_validate(raw), source(), shelf)
    raw = episode().model_dump(mode='json')
    raw['scenes'][1]['data']['tiles'][0]['sub'] = '정규장 종가 · 05:00 KST'
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
        run([ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i', f'sine=frequency={330 + i * 60}:sample_rate=48000:duration=5', str(path)])
        paths.append(path)
    job = {'id': 'fixture', 'source': source(), 'policy': {'template': 'motion-v2'}}
    manifest = MotionRenderer(settings, WordAligner()).render(job, plan, tmp_path / 'out', paths)
    assert manifest['template'] == 'motion-v2' and manifest['qa_passed'] and manifest['fps'] == 30
    assert 30 < manifest['duration'] < 40 and manifest['audio_quality']['true_peak_dbfs'] <= -1.5
    upload = (tmp_path / 'out' / 'upload.txt').read_text()
    assert 'Fixture Author · CC BY 4.0' in upload and '변경: 잘라 냄' in upload and 'AI' not in upload
    page = (tmp_path / 'out' / 'page' / 'episode.js').read_text()
    assert '자료사진 · Fixture Author · CC BY 4.0' in page and '일러스트' not in page
    assert '자료 기준' not in page and '07:30' not in page
    assert [c['title'] for c in manifest['chapters']][:2] == ['오프닝', '시장 한눈에']
    verify_artifacts(manifest, tmp_path)
    (tmp_path / 'out' / 'subtitles.srt').write_text('changed')
    with pytest.raises(ValueError):
        verify_artifacts(manifest, tmp_path)


def test_overflowing_text_is_rejected_before_any_frame(tmp_path):
    if not importlib.util.find_spec('playwright'):
        pytest.skip('Install optional video dependencies and Chromium')
    ffmpeg = ffmpeg_binary()
    settings = Settings(video_artifact_dir=tmp_path, video_ffmpeg=ffmpeg, video_ffprobe='', video_asset_dir=tmp_path / 'assets')
    library(tmp_path / 'assets', ffmpeg)
    raw = episode().model_dump(mode='json')
    raw['scenes'][2]['data']['nodes'][1]['text'] = '성장주 할인 부담 완화 가능 ' * 12
    plan = EpisodePlan.model_validate(raw)
    paths = []
    for i in range(len(plan.scenes)):
        path = tmp_path / f'a{i}.wav'
        run([ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=2', str(path)])
        paths.append(path)
    with pytest.raises(ValueError, match='safe screen area'):
        MotionRenderer(settings, WordAligner()).render({'id': 'x', 'source': source(), 'policy': {}}, plan, tmp_path / 'out', paths)
    assert not list((tmp_path / 'out').glob('part-*.mp4'))


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
    complete(brief)
    with store.db.transaction() as conn:
        job = conn.execute('SELECT * FROM video_jobs').fetchone()
    assert job['policy']['template'] == 'motion-v2'
    job_source = {**job['source']}
    provider = EpisodeProvider()
    runner = VideoRunner(store.company, provider=provider)
    state = (await runner.tick())['state']
    request = provider.calls[0]
    assert request.output_contract == 'video_episode_v1' and 'fixture-photo' in request.prompt
    assert 'cold_open' in request.prompt and job_source['day']
    assert state == 'reviewing'
    assert EpisodePlan.model_validate(store.get(job['id'])['plan']).ticker.label == '정규장 종가'

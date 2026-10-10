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
    assert '• Synthetic fixture / 2026.09.22' in upload
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
    raw['scenes'][4]['data']['nodes'][1]['text'] = '성장주 할인 부담 완화 가능 ' * 12
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

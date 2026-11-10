"""Motion template: episode contract, number binding and a real Chromium/FFmpeg render.
Model, speech and alignment are simulated fixtures; the frames, encoding and layout checks are real."""

import importlib.util
import json
import re
import subprocess

import pytest

from quant_company.config import Settings
from quant_company.contracts import AgentDecision, ProviderResponse
from quant_company.video.episode import EpisodePlan, validate_episode
from quant_company.video.motion import BRAND, MotionRenderer, captions, word_times
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
             narration='미국 증시는 반도체가 주도했습니다. 그런데 업종별 흐름은 엇갈렸습니다.',
             data=dict(kicker='9월 22일 화요일 · 아침 시장 브리핑', question=[[['반도체가', 1], ['이끌었다', 0]]],
                       stats=[dict(label='S&P 500 · 정규장 종가 기준', value='5,300', sub='▲ 1.92%', sub_dir='up'),
                              dict(label='나스닥 종합 · 정규장 종가 기준', value='17,100', sub='▲ 0.59%', sub_dir='up'),
                              dict(label='업종 흐름', value='혼조', sub='기술주 밖 참여는 혼조')])),
        dict(id='summary', chapter='요약', type='summary3', claim_ids=['b001', 'b009', 'b011'],
             narration='오늘 꼭 알아야 할 세 가지는 반도체 주도, 금리 하락의 해석, 그리고 상승의 확산 여부입니다.',
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
             narration='미국 지수는 상승했고 반도체가 상승을 주도했습니다.',
             data=dict(chips=['핵심 이슈 1', '당일'], title='반도체 주도와 제한된 확산', image=dict(asset='fixture-illustration'),
                       panel_title='미국 지수', source='Synthetic fixture',
                       stats=[dict(label='S&P 500', value='5,300', dir='up', change='1.92%', sub='정규장 종가 기준'),
                              dict(label='업종 흐름', value='혼조', dir='', change='', sub='기술주 밖 참여')])),
        dict(id='flow', chapter='핵심 이슈', type='flow', claim_ids=['b005', 'b009', 'b007'],
             narration='수익률 하락은 성장주의 할인 부담을 낮출 수 있다는 해석입니다.',
             data=dict(chips=['핵심 이슈 1'], title='수익률 하락 → 할인 부담 완화', nodes=[
                 dict(chips=['원인'], source='Synthetic fixture', icon='percent', head='국채 수익률', text='수익률 하락'),
                 dict(chips=['보도 해석'], icon='balance', head='해석', text='성장주 할인 부담<br>완화 가능'),
                 dict(chips=['결과'], icon='chartUp', head='반도체', text='상승을 주도')])),
        dict(id='photo', chapter='핵심 이슈', type='photo', claim_ids=['b010', 'b011'],
             narration='금리보다 반도체 자체의 재료가 영향을 줬을 수도 있습니다.',
             data=dict(chips=['핵심 이슈 1'], title='다르게 볼 점', image=dict(asset='fixture-photo'), rows=[
                 dict(chip='다르게 볼 점', kind='counter', text='반도체 자체 재료 가능성', sub='금리만의 효과로 보지 않음'),
                 dict(chip='확인할 신호', kind='signal', text='비기술 업종 확산', sub='상승이 넓어지는지 확인'),
                 dict(chip='사실', text='반도체가 상승 주도', sub='업종별 흐름은 엇갈림')])),
        dict(id='counter', chapter='핵심 이슈', type='counter', claim_ids=['b010', 'b014'],
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
    # Back on 뭐든story (owner, 2026-11-10): its 증시story playlist, never the 그래서, 오늘장 channel's PLAMiTJ2pkjHY.
    assert '00:00 반도체가 이끌었다' in upload and '#증시story #뭐든story #반도체' in upload
    assert '▶ 증시story 모아보기\nhttps://www.youtube.com/playlist?list=PLbCkACCer37U' in upload and 'PLAMiTJ2pkjHY' not in upload
    assert '세상의 뭐든, 이야기로 만나다. 뭐든story.' in upload and '오늘장' not in upload
    assert 'https://www.cnbc.com/fixture-market-report.html' in upload
    assert not (tmp_path / 'out' / 'page').exists() and not list((tmp_path / 'out').glob('part-*.mp4'))
    assert not (tmp_path / 'out' / 'narration.wav').exists() and (tmp_path / 'out' / 'video.mp4').exists()
    assert [c['title'] for c in manifest['chapters']][:3] == ['오프닝', '오늘 꼭 알아야 할 세 가지', '시장 한눈에']
    assert [s['id'] for s in manifest['scenes']] == [s.id for s in plan.scenes]
    assert manifest['scenes'][0]['t0'] == 0 and manifest['scenes'][-1]['t1'] == pytest.approx(manifest['duration'], abs=.5)
    assert '• Synthetic fixture / 2026.09.22' in upload
    # The mail signup (owner, 2026-10-20): on top of the description, also the one YouTube is given, and under the
    # pinned comment. It goes on after the copy checks, whose reference rule would reject its link.
    signup = '📩 구독자 전용 · 매일 아침·마감 증시 리포트를 메일로 받아보세요\n👉 https://forms.gle/JffyKEoKmSCNFKsJ8\n\n'
    assert f'[설명]\n{signup}미국 증시는 반도체가 주도했지만' in upload and manifest['description'].startswith(signup)
    assert ('[고정 댓글]\n어떤 업종의 확산을 확인하고 계신가요?\n\n'
            '📩 구독자 전용 매일 증시 리포트 메일 신청: https://forms.gle/JffyKEoKmSCNFKsJ8\n\n[자료사진 출처]') in upload
    assert not any(f.startswith('Upload copy') for f in manifest['findings'])
    assert manifest['music'] is None  # no music folder: the episode is made without a bed, and that is no finding
    assert not any('Music' in f for f in manifest['findings'])
    verify_artifacts(manifest, tmp_path)
    (tmp_path / 'out' / 'subtitles.srt').write_text('changed')
    with pytest.raises(ValueError):
        verify_artifacts(manifest, tmp_path)


def test_the_fixed_opening_and_closing_frame_the_episode_without_moving_its_chapters(tmp_path):
    """Owner, 2026-10-19: every episode opens with the channel's line and closes with the subscribe scene (since
    2026-11-04 the briefing channel's '그래서, 오늘장입니다.', and from 2026-11-10 뭐든story's again)."""
    if not importlib.util.find_spec('playwright'):
        pytest.skip('Install optional video dependencies and Chromium')
    ffmpeg = ffmpeg_binary()
    settings = Settings(video_artifact_dir=tmp_path, video_ffmpeg=ffmpeg, video_ffprobe='', video_asset_dir=tmp_path / 'assets',
                        video_render_workers=2)
    library(tmp_path / 'assets', ffmpeg)
    plan = episode()
    paths, brand = [], {}
    for i in range(len(plan.scenes)):
        path = tmp_path / f'a{i}.wav'
        run([ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i', f'sine=frequency={330 + i * 60}:sample_rate=48000:duration=3', str(path)])
        paths.append(path)
    for name, seconds in (('sting', 2.1), ('endcard', 4.6)):
        brand[name] = tmp_path / f'{name}.wav'
        run([ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i', f'sine=frequency=520:sample_rate=48000:duration={seconds}', str(brand[name])])
    job = {'id': 'fixture', 'source': source(), 'policy': {'template': 'motion-v2'}}
    plain = MotionRenderer(settings, WordAligner()).render(job, plan, tmp_path / 'plain', paths)
    manifest = MotionRenderer(settings, WordAligner()).render(job, plan, tmp_path / 'out', paths, brand=brand)
    scenes = manifest['scenes']
    assert manifest['brand'] == ['sting', 'endcard'] and plain['brand'] == []
    assert [s['type'] for s in scenes][0] == 'sting' and scenes[-1]['type'] == 'endcard'
    assert [s['id'] for s in scenes[1:-1]] == [s.id for s in plan.scenes]
    assert scenes[0]['t0'] == 0 and 2.5 <= scenes[0]['t1'] <= 3.0 and scenes[-1]['t1'] - scenes[-1]['t0'] == pytest.approx(5.9, abs=.01)
    assert manifest['duration'] == pytest.approx(plain['duration'] + scenes[0]['t1'] + 5.9, abs=.5)
    # Same chapters and titles; the opening belongs to the first one and the closing opens none.
    assert [c['title'] for c in manifest['chapters']] == [c['title'] for c in plain['chapters']]
    assert manifest['chapters'][0]['start'] == 0 and manifest['chapters'][1]['start'] == pytest.approx(
        plain['chapters'][1]['start'] + scenes[0]['t1'], abs=.01)
    srt = (tmp_path / 'out' / 'subtitles.srt').read_text()
    assert srt.split('\n')[2] == '안녕하세요, 뭐든story입니다.' and '구독과 좋아요, 알림 설정 부탁드려요.' in srt
    from quant_company.video.motion import BRAND
    assert BRAND['sting']['say'] == '안녕하세요, 뭐든 스토리입니다.'  # 'story' in Korean letters for the voice
    # Checked on screen like every card, but never sent to the visual review.
    assert not (tmp_path / 'out' / 'layout-qa.json').exists()
    assert not any(f['scene'].startswith('brand-') for f in manifest['review']['frames'])
    assert (tmp_path / 'out' / 'review' / '00-brand-sting.jpg').is_file()
    verify_artifacts(manifest, tmp_path)


def test_the_episode_plays_the_shorts_music_bed_and_renders_without_one(tmp_path):
    """Owner, 2026-10-20: the episode gets the Shorts' bed too. Without a track, or with one FFmpeg cannot read, it
    renders as before. Each render after the first resumes its frames, so only the sound is made again."""
    if not importlib.util.find_spec('playwright'):
        pytest.skip('Install optional video dependencies and Chromium')
    ffmpeg = ffmpeg_binary()
    settings = Settings(video_artifact_dir=tmp_path, video_ffmpeg=ffmpeg, video_ffprobe='', video_asset_dir=tmp_path / 'assets',
                        video_render_workers=2)
    library(tmp_path / 'assets', ffmpeg)
    plan = episode()
    paths = []
    for i in range(len(plan.scenes)):
        path = tmp_path / f'a{i}.wav'
        run([ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i', f'sine=frequency={330 + i * 60}:sample_rate=48000:duration=2', str(path)])
        paths.append(path)
    folder = tmp_path / 'assets' / 'music'
    folder.mkdir()
    run([ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=220:sample_rate=44100:duration=3', str(folder / 'bed.mp3')])
    job = {'id': 'fixture', 'source': source(), 'policy': {'template': 'motion-v2'}}

    def render(**music):
        manifest = MotionRenderer(settings, WordAligner()).render(job, plan, tmp_path / 'out', paths, **music)
        # The closing card holds past its 2-s line: no narration is left there, only the bed (the 3-s track loops).
        closing = manifest['scenes'][-1]
        report = subprocess.run([ffmpeg, '-hide_banner', '-ss', f"{closing['t1'] - 1.8:.2f}", '-t', '0.6', '-i',
                                 str(tmp_path / 'out' / 'video.mp4'), '-af', 'volumedetect', '-f', 'null', '-'],
                                capture_output=True, text=True, check=True).stderr
        return manifest, float(re.search(r'mean_volume: (-?[\d.]+) dB', report).group(1))

    manifest, tail = render()
    assert manifest['music'] == 'bed.mp3' and tail > -60 and not any('Music' in f for f in manifest['findings'])
    assert manifest['audio_quality']['true_peak_dbfs'] <= -1.5 and not (tmp_path / 'out' / 'mixed.wav').exists()
    verify_artifacts(manifest, tmp_path)
    # A remote render is given the track from its inputs, whatever the folder holds.
    given = tmp_path / 'given.mp3'
    given.write_bytes((folder / 'bed.mp3').read_bytes())
    assert render(music=given)[0]['music'] == 'given.mp3'
    (folder / 'bed.mp3').write_bytes(b'not an mp3 at all' * 100)
    manifest, tail = render()
    assert manifest['music'] is None and tail < -70
    assert 'Music bed bed.mp3 could not be mixed; the cut has no music' in manifest['findings']
    verify_artifacts(manifest, tmp_path)
    (folder / 'bed.mp3').unlink()
    manifest, tail = render()
    assert manifest['music'] is None and tail < -70 and not any('Music' in f for f in manifest['findings'])


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
    assert '"b001"' in request.prompt and 'outlet_on_card' in request.prompt
    assert "never say '보도에 따르면'" in request.prompt  # owner, 2026-10-19: sources are shown on screen only
    # 11/01-11/06: most review concerns were card lines cut from a briefing sentence that dropped its condition.
    assert "keeps that sentence's condition or caveat" in request.prompt
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
    from quant_company.video.episode import ScriptFix

    from .test_video import ScriptedProvider, good_review, release

    store, job = motion_job(brief, video, tmp_path)
    concern = VideoReview(facts=True, numbers=True, conditions=True, coverage=True, readability=False, concerns=['결론이 늦게 나옴'])
    unbound = episode().model_dump(mode='json')
    unbound['scenes'][2]['data']['tiles'][0]['change'] = '2.5%'
    # The revision in place fails the checks too (an unbound number), so the whole script is rewritten.
    fix = ScriptFix(scenes=[EpisodePlan.model_validate(unbound).scenes[2]])
    provider = ScriptedProvider([episode(), concern, fix, EpisodePlan.model_validate(unbound), episode(), good_review()])
    for _ in range(6):
        release(store, job)
        await VideoRunner(store.company, provider=provider).tick()
    assert store.get(job['id'])['state'] == 'synthesizing'
    assert [a['outcome'] for a in store.get(job['id'])['qa']['plan_attempts']] == ['passed', 'revise_failed', 'check_failed', 'passed']
    first, revised, again = sent_episodes(provider)
    assert 'previous_version' not in first
    assert revised['previous_version'] == episode().model_dump(mode='json') and '결론이 늦게 나옴' in revised['revision_feedback']
    assert again['previous_version']['scenes'][2]['data']['tiles'][0]['change'] == '2.5%'
    assert 'Unbound number' in again['revision_feedback'] and '결론이 늦게 나옴' in again['revision_feedback']


async def test_review_concerns_are_fixed_in_place_not_by_rewriting_the_script(brief, video, tmp_path):  # noqa: F811
    """11/01-11/06: every episode's first review had 10-15 concerns, and each was answered by rewriting the whole script
    (7-16 minutes at xhigh) before the second review. A revision in place changes only the scenes a concern names."""
    from quant_company.video.contracts import VideoReview
    from quant_company.video.episode import ScriptFix

    from .test_video import ScriptedProvider, good_review, release

    store, job = motion_job(brief, video, tmp_path)
    concern = VideoReview(facts=True, numbers=True, conditions=False, coverage=True, readability=True,
                          concerns=['[flow] 조건이 빠져 실제보다 확정적으로 읽힘'])
    scene = episode().scenes[4].model_copy(update={'narration': episode().scenes[4].narration + ' 다만 조건이 남아 있어요.'})
    provider = ScriptedProvider([episode(), concern, ScriptFix(scenes=[scene]), good_review()])
    for _ in range(4):
        release(store, job)
        await VideoRunner(store.company, provider=provider).tick()
    current = store.get(job['id'])
    assert current['state'] == 'synthesizing'
    assert [a['outcome'] for a in current['qa']['plan_attempts']] == ['passed', 'revised']
    assert [c.output_contract for c in provider.calls] == ['video_episode_v1', 'video_review_v1', 'video_script_fix_v1',
                                                          'video_review_v1']
    revision = provider.calls[2]
    assert revision.request_id.endswith('-revise') and '조건이 빠져' in revision.prompt
    plan = EpisodePlan.model_validate(current['plan'])
    assert plan.scenes[4].narration.endswith('다만 조건이 남아 있어요.')
    assert [s for i, s in enumerate(plan.scenes) if i != 4] == [s for i, s in enumerate(episode().scenes) if i != 4]
    # The second review reads the revised script.
    assert '다만 조건이 남아 있어요.' in provider.calls[3].prompt


async def test_a_revision_that_changes_nothing_leaves_the_concerns_to_a_rewrite(brief, video, tmp_path):  # noqa: F811
    """A concern a revision cannot express (a new or reordered scene) came back as an empty fix: the second review would
    read the same script and the episode would ship with the concerns."""
    from quant_company.video.contracts import VideoReview
    from quant_company.video.episode import ScriptFix

    from .test_video import ScriptedProvider, good_review, release

    store, job = motion_job(brief, video, tmp_path)
    concern = VideoReview(facts=True, numbers=True, conditions=True, coverage=False, readability=True, concerns=['장면 순서를 바꿔야 함'])
    provider = ScriptedProvider([episode(), concern, ScriptFix(), episode(), good_review()])
    for _ in range(5):
        release(store, job)
        await VideoRunner(store.company, provider=provider).tick()
    current = store.get(job['id'])
    assert current['state'] == 'synthesizing'
    attempts = current['qa']['plan_attempts']
    assert [a['outcome'] for a in attempts] == ['passed', 'revise_failed', 'passed']
    assert attempts[1]['findings'] == ['The revision changed nothing']
    assert '장면 순서를 바꿔야 함' in sent_episodes(provider)[1]['revision_feedback']


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
    assert '증시story에서 그 앞뒤 이야기를 함께 짚어봅니다.' in text
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
    assert upload.UploadCopy.model_fields['title'].metadata[-1].max_length == 100 - upload.SUFFIX_MAX == 73
    for hook in ('증시story가 본 반도체', '그래서, 오늘장 반도체'):  # no series name, old or new, in the hook
        with pytest.raises(ValueError, match='hook only'):
            upload.UploadCopy.model_validate({**copy.model_dump(mode='json'), 'title': hook})
    for broken in ('[오늘의 증시story] 반도체가 이끌었다', '반도체가 이끌었다 | 9월 22일 아침 브리핑',
                   '반도체가 이끌었다 | 9월 22일 아침 · 그래서, 오늘장'):  # the 2026-11-04..10 series
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
    # One sentence joins the index close and the flow (10/14 PM, '…마감했고 … 순매도해'): the index drop is not a flow
    # value, so the revision put '장 마감 기준' under the index card. Only the investor's clause holds flow values.
    ('13일 코스피는 전날 대비 5.46% 급락한 6,035로 마감했고 삼성전자와 SK하이닉스는 각각 7%, 10% 넘게 하락했으며, '
     '외국인은 4조5천억원 넘게 순매도해 6거래일 연속 매도했다.', '13일 코스피 5.46%', None),
    ('13일 코스피는 전날 대비 5.46% 급락한 6,035로 마감했고 삼성전자와 SK하이닉스는 각각 7%, 10% 넘게 하락했으며, '
     '외국인은 4조5천억원 넘게 순매도해 6거래일 연속 매도했다.', '외국인 4조5천억원 순매도', '장 마감 기준'),
    # Each investor names its own amount before the shared flow word: both amounts are flow values.
    ('외국인은 4,000억원, 기관은 2,000억원을 순매수했다.', '외국인 4,000억원', '장 마감 기준'),
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
    ('tool', 'tool wording'), ('report', 'outlet'), ('close', '정규장 종가 기준'), ('flow', '장 마감 기준'),
    ('few', 'scenes'),
])
def test_body_attribution_labels_and_no_tool_wording(mutation, error):
    src = source()
    shelf = {'fixture-photo': {}, 'fixture-illustration': {}}
    raw = episode().model_dump(mode='json')
    if mutation == 'tool':
        raw['scenes'][5]['data']['rows'][0]['sub'] = 'AI 분석 결과'
    elif mutation == 'report':
        raw['scenes'][3]['data']['source'] = '다른 매체'  # a one-outlet line on a card that names another outlet
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

    def render(self, job, plan, directory, paths, texts=None, brand=None):
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
                    'files': {n: checksum(directory / n) for n in files}, 'brand': sorted(brand or {})}
        if self.renderer_code:  # what a render image built from other code reports
            manifest['renderer'] = self.renderer_code
        else:
            from quant_company.video.motion import RENDERER_DIGEST

            manifest['renderer'] = RENDERER_DIGEST
        manifest['digest'] = digest(manifest)
        return manifest


def scene_takes(speech):
    """Narration takes of the written scenes, without the fixed opening and closing lines (motion.BRAND)."""
    return [c for c in speech.calls if c['text'] not in {line['say'] for line in BRAND.values()}]


async def produce_inspected(store, tmp_path, monkeypatch, provider, renderer, brief, audio=None, speech=None):  # noqa: F811
    from .test_video import SimulatedSpeech, new_job, release

    store.settings.video_template = 'motion-v2'
    store.settings.video_asset_dir = tmp_path / 'assets'
    (tmp_path / 'assets').mkdir()
    (tmp_path / 'assets' / 'manifest.json').write_text(json.dumps({'fixture-photo': {'tags': [], 'kind': '자료사진'},
                                                                   'fixture-illustration': {'tags': [], 'kind': '일러스트'}}))
    monkeypatch.setattr('quant_company.video.runner.download_audio', audio or (lambda url, path: path.write_bytes(b'simulated-speech')))
    job = new_job(brief, (store, None))
    speech = speech or SimulatedSpeech()
    for _ in range(60):
        release(store, job)  # a pause the runner asked for (model_retry, render waits) has passed
        await VideoRunner(store.company, provider, speech, renderer).tick()
        if store.get(job['id'])['state'] not in {'queued', 'reviewing', 'synthesizing', 'rendering', 'inspecting'}:
            break
    return store.get(job['id']), speech


async def test_a_new_job_carries_the_owner_craft_rules_in_its_script_and_review_prompts(brief, video, tmp_path, monkeypatch):  # noqa: F811
    # Owner, 2026-10-22: hook, one metaphor, short thumbnail text, a closing question (video/craft.py), frozen per job.
    from quant_company.video.contracts import VisualReview
    from quant_company.video.craft import (
        CRAFT_GUIDE,
        CRAFT_VERSION,
        OUTLOOK,
        VERSIONS,
        recent_block,
        with_craft,
    )

    store, _ = video
    store.settings.video_upload_enabled = store.settings.video_publish_enabled = False
    provider = InspectingProvider(visual=[VisualReview(passed=True, issues=[])])
    job, _speech = await produce_inspected(store, tmp_path, monkeypatch, provider, InspectedRenderer(), brief)
    assert job['state'] == 'delivering', job['error']
    assert job['policy']['craft'] == CRAFT_VERSION == 4
    script = next(c for c in provider.calls if c.output_contract == 'video_episode_v1').prompt
    rules, data = script.rsplit('\n', 1)
    # Version 3 (owner, 11/06) ends with the recent hooks it must not echo; the data stays the last line.
    assert rules.endswith(VERSIONS[CRAFT_VERSION][0] + recent_block([])) and json.loads(data)['lines']
    # Version 2 (owner, 10/22): the close states the outlook, conditions and direction only; version 3 keeps it.
    assert OUTLOOK in VERSIONS[3][0] and 'never a level, size or timed move' in OUTLOOK
    assert 'Leave the base case out when the lines give no basis' in OUTLOOK and "line's own strength" in OUTLOOK
    review = next(c for c in provider.calls if c.output_contract == 'video_review_v1').prompt
    plan = episode()  # the lengths are measured by the service, not counted by the reviewer
    assert f'the title hook is {len(plan.upload.title)} characters, the thumbnail text {len(plan.thumbnail)})' in review
    assert 'or repeats the opening, the ending or the shape of a recent hook: none.' in review
    # Version 4 (owner, 11/09): plain words, one point, no comma-joined stories, no date on the thumbnail.
    assert review.endswith('carries a date or weekday or only recaps figures.')
    assert "Never join two stories with a comma" in VERSIONS[4][0] and VERSIONS[4][0].startswith(VERSIONS[3][0])
    # Jobs frozen with versions 1 to 3 keep their text after the change.
    head = rules[:-len(VERSIONS[CRAFT_VERSION][0] + recent_block([])) - 1]
    plain = head + '\n' + data
    assert with_craft(plain, {'craft': 3}) == head + '\n' + VERSIONS[3][0] + recent_block([]) + '\n' + data
    assert with_craft(plain, {'craft': 2}) == head + '\n' + CRAFT_GUIDE + OUTLOOK + '\n' + data
    assert with_craft(plain, {'craft': 1}) == head + '\n' + CRAFT_GUIDE + '\n' + data


async def test_a_job_created_before_the_craft_rules_keeps_its_prompts(brief, video, tmp_path, monkeypatch):  # noqa: F811
    from quant_company.video.contracts import VisualReview

    monkeypatch.setattr('quant_company.video.store.CRAFT_VERSION', None)  # what an older job's policy holds
    store, _ = video
    store.settings.video_upload_enabled = store.settings.video_publish_enabled = False
    provider = InspectingProvider(visual=[VisualReview(passed=True, issues=[])])
    job, _speech = await produce_inspected(store, tmp_path, monkeypatch, provider, InspectedRenderer(), brief)
    assert job['state'] == 'delivering', job['error']
    prompts = [c.prompt for c in provider.calls if c.output_contract in {'video_episode_v1', 'video_review_v1'}]
    assert len(prompts) >= 2 and not any('Craft rules' in p or 'Craft (owner' in p for p in prompts)


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
    assert renderer.renders == 2 and len(scene_takes(speech)) == len(episode().scenes)  # narration unchanged: no new takes
    assert next(s for s in job['plan']['scenes'] if s['id'] == 'head')['data']['title'] == '반도체 주도'
    assert len(job['qa']['inspections']) == 2 and job['qa']['inspection_findings'] == []


async def test_the_episode_is_looked_at_twice_and_goes_out_with_what_the_second_look_left(brief, video, tmp_path, monkeypatch):  # noqa: F811
    """11/01-11/06: all eleven episodes took three looks and two re-renders (about 13 minutes each round) and none passed;
    like the Shorts since 10/30, one fix and re-render, then the files go out with the second look's findings."""
    from quant_company.video.contracts import VisualReview
    from quant_company.video.episode import ScreenFix

    store, _ = video
    issue = {'scene': 'head', 'kind': 'layout', 'detail': 'title wraps', 'fix': 'shorten the title'}
    fixes = [ScreenFix(scenes=[{'id': 'head', 'data': {**episode().scenes[3].data, 'title': title}}]) for title in ('반도체 주도', '반도체')]
    provider = InspectingProvider(visual=[VisualReview(passed=False, issues=[issue])] * 3, fixes=fixes)
    renderer = InspectedRenderer()
    job, _speech = await produce_inspected(store, tmp_path, monkeypatch, provider, renderer, brief)
    assert job['state'] == 'uploading', job['error']
    contracts = [c.output_contract for c in provider.calls]
    assert contracts.count('video_visual_review_v1') == 2 and contracts.count('video_screen_fix_v1') == 1
    assert renderer.renders == 2 and job['qa']['inspection_findings'] == ['head: title wraps']


class BrandRenderer(InspectedRenderer):
    def __init__(self):
        super().__init__()
        self.brand = []

    def render(self, job, plan, directory, paths, texts=None, brand=None):
        self.brand.append(brand)
        return super().render(job, plan, directory, paths, texts, brand)


def real_audio(ffmpeg):
    def write(url, path):
        run([ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=1.5', '-f', 'wav', str(path)])
    return write


async def test_the_fixed_lines_are_recorded_once_and_framed_into_the_render(brief, video, tmp_path, monkeypatch):  # noqa: F811
    from quant_company.video.contracts import VisualReview
    from quant_company.video.runner import VideoRunner, brand_file, brand_requests
    from quant_company.video.store import delivery_text

    store, _ = video
    store.settings.video_ffmpeg, store.settings.video_ffprobe = ffmpeg_binary(), ''
    renderer = BrandRenderer()
    job, speech = await produce_inspected(store, tmp_path, monkeypatch, InspectingProvider(visual=[VisualReview(passed=True, issues=[])]),
                                          renderer, brief, audio=real_audio(ffmpeg_binary()))
    assert job['state'] == 'uploading', job['error']
    said = [c['text'] for c in speech.calls]
    assert said.count(BRAND['sting']['say']) == 1 and said.count(BRAND['endcard']['say']) == 1
    assert sorted(renderer.brand[-1]) == ['endcard', 'sting'] and all(p.is_file() for p in renderer.brand[-1].values())
    assert job['qa']['brand_new'] == ['endcard', 'sting']
    assert '시작·마무리 음성을 이번 회차에서 처음 만들었습니다' in delivery_text(job, job['artifacts'])
    # Kept for every later episode: nothing is recorded again for the same voice.
    assert await VideoRunner(store.company, speech=speech).record_brand({**job, 'qa': {}}) and len(speech.calls) == len(said)
    # Another voice or speech model is another recording.
    requests = brand_requests(job)
    other = brand_requests({**job, 'policy': {**job['policy'], 'voice': 'Other'}})
    assert brand_file(store.settings, 'sting', other['sting']) != brand_file(store.settings, 'sting', requests['sting'])
    # A library voice is another recording too; a preset-voice job's requests carry no voice_id, so their keys hold.
    library = brand_requests({**job, 'policy': {**job['policy'], 'voice_id': 'lib123'}})
    assert 'voice_id' not in requests['sting'] and library['sting']['voice_id'] == 'lib123'
    assert brand_file(store.settings, 'sting', library['sting']) != brand_file(store.settings, 'sting', requests['sting'])


async def test_an_episode_never_waits_for_the_fixed_lines(brief, video, tmp_path, monkeypatch):  # noqa: F811
    import httpx

    from quant_company.video.contracts import VisualReview
    from quant_company.video.runner import VideoRunner
    from quant_company.video.store import delivery_text

    from .test_video import SimulatedSpeech

    store, _ = video
    speech, renderer = SimulatedSpeech(), BrandRenderer()
    submit = speech.submit

    async def lost(**request):
        if request['text'] == BRAND['sting']['say']:
            speech.calls.append(request)
            raise httpx.ReadTimeout('simulated lost receipt')
        return await submit(**request)

    speech.submit = lost
    job, _ = await produce_inspected(store, tmp_path, monkeypatch, InspectingProvider(visual=[VisualReview(passed=True, issues=[])]),
                                     renderer, brief, speech=speech)
    assert job['state'] == 'uploading', job['error']
    # The uncertain recording is never sent again and the episode renders without the fixed scenes.
    assert [c['text'] for c in speech.calls].count(BRAND['sting']['say']) == 1 and renderer.brand == [None]
    assert job['qa']['brand_skipped'].startswith('ReadTimeout') and job['qa']['brand'] is None
    assert '시작·마무리 장면 없이 만들었습니다(고정 음성 준비 실패' in delivery_text(job, job['artifacts'])
    # The lost recording stays a receipt of its own kind: the episode is not uncertain and no later episode sends it again.
    assert not store.has_uncertain_effect(job)
    with store.db.transaction() as conn:
        assert conn.execute("SELECT kind,state FROM video_effects WHERE id LIKE '%%:speech-brand-sting-%%'").fetchone() == \
            {'kind': 'brand', 'state': 'running'}
    later = VideoRunner(store.company, speech=speech)
    assert await later.record_brand({**job, 'qa': {}})
    assert [c['text'] for c in speech.calls].count(BRAND['sting']['say']) == 1


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
    assert len(scene_takes(speech)) == len(episode().scenes) + 1 and speech.calls[-1]['text'] == korean_numbers(board.narration)
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
    # Since 2026-10-15 photo rows draw the arrow of a big number's direction (10/15 PM: 'CoreWeave 16%' had no ▼).
    photo = {'rows': [{'chip': '코어위브 주가', 'big': '▼16%', 'dir': 'dn'}, {'chip': '해석', 'text': '자금 가격 상승'}]}
    assert single_arrows('photo', photo) == [] and photo['rows'][0]['big'] == '16%'
    assert single_arrows('photo', {'rows': [{'big': '▲16%', 'dir': 'dn'}]}) == ['photo ▲16%']
    assert single_arrows('', {}, ticker) == [] and ticker['items'][0]['change'] == '0.64%'
    # A written sign that contradicts the direction is reported; the screen follows the direction field.
    wrong = {'stats': [{'label': '미 10년물', 'value': '2bp', 'change': '▼2bp', 'dir': 'up'}]}
    assert single_arrows('headline', wrong) == ['headline ▼2bp'] and wrong['stats'][0]['change'] == '2bp'


def test_the_thumbnail_names_its_edition_and_carries_the_watched_progress_strip():
    from importlib.resources import files

    html = files('quant_company.video').joinpath('design', 'thumbnail.html').read_text()
    assert '아침 시장 브리핑' not in html and '{{brand}}' in html  # the close edition reads 마감 브리핑
    # Every thumbnail carries the red strip along its bottom edge (owner request 2026-10-12): half its width until the
    # owner asked for a tenth on 2026-11-05.
    assert '<div class="resume"><i></i></div>' in html and 'width:10%;background:#FF0000' in html
    # 뭐든story's logo and name on the bar again (owner, 2026-11-10), never the 그래서, 오늘장 mark.
    assert '<img src="logo_mark.png">뭐든story<span>{{brand}}</span>' in html and '오늘장' not in html


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


def test_title_and_thumbnail_text_are_written_to_grab_attention_within_the_lines():
    """Owner, 2026-10-15: '영상 제목이나 썸네일 문구 등은 시청자가 이목을 끌만한 글로 어그로 잘 끌어주면 좋겠어.'
    The hook is the owner's rule; the facts, the size words and the no-buy/sell rule still bind it."""
    from quant_company.video.contracts import review_prompt
    from quant_company.video.episode import TITLE_GUIDE, episode_prompt

    for phrase in ('Owner rule (2026-10-15)', 'stop a scrolling viewer', 'curiosity gap', 'thumbnail text',
                   'every fact and number comes from the lines', 'no buy/sell'):
        assert phrase in TITLE_GUIDE, phrase
    assert TITLE_GUIDE in episode_prompt(source())
    review = review_prompt(source(), episode())
    assert 'do not ask to tone down' in review and 'exaggerates a number' in review
    assert 'flat or generic' in review


def test_a_card_names_its_outlet_with_or_without_the_word_news():
    from quant_company.video.episode import shows_outlet

    assert shows_outlet(['SBS 보도'], ['SBS 뉴스']) and shows_outlet(['CNBC · 연합뉴스'], ['연합뉴스'])
    assert shows_outlet(['연합뉴스·이투데이'], ['이투데이']) and not shows_outlet(['연합뉴스'], ['SBS 뉴스'])
    assert not shows_outlet(['브리핑 분석'], ['SBS 뉴스'])


def test_ticker_prices_share_one_number_format():
    """11/04 PM, 11/05 PM, 11/06 AM: '23만1,000' and '29만3500원' sat beside '1,393,000', and '금 4113.5' had no comma."""
    from quant_company.video.motion import ticker_number

    assert ticker_number('29만3500원') == '293,500원' and ticker_number('23만1,000') == '231,000'
    assert ticker_number('10만원') == '100,000원' and ticker_number('4113.5') == '4,113.5'
    for kept in ('1,393,000', '24,315', '1,334.3원', '3.95%', '90.52', '', '678.41', '1조6168억원', '1억2000만', '2026년',
                 '29만 3500원', '10/30'):
        assert ticker_number(kept) == kept


def test_a_photo_credit_drops_the_link_words_of_its_source_page():
    """11/06 AM: '자료사진 · mattbuck (category) · CC BY-SA 4.0' showed the Commons author field's category link."""
    from quant_company.video.motion import credit_author

    assert credit_author('mattbuck (category)') == 'mattbuck'
    assert credit_author('Jane Doe (talk | contribs)') == 'Jane Doe' and credit_author('Reuters') == 'Reuters'
    assert credit_author('Jane Doe (talk · contribs)') == 'Jane Doe' and credit_author(None) == ''


# Scenes as the 11/04-11/06 scripts wrote them; each drew a problem the final editor reported in every round.
LAYOUT_SCENES = [
    ('flow', {'chips': ['AI 투자'], 'title': '주민투표에서 메모리 발주까지', 'nodes': [
        {'big': '41건', 'sub': '노스다코타 하우드 1건 빼고 모두 반대', 'head': '데이터센터 제한 주민투표', 'chips': ['화요일 · 10개 주'], 'source': 'CNBC'},
        {'big': '공약 단계', 'sub': '주민 보호 법률 마련 전까지 · 시행된 중단은 아님', 'head': '주지사 당선인, 신규 승인 중단 공약', 'chips': ['텍사스'], 'source': '이투데이'},
        {'big': 'HBM·D램·낸드', 'sub': '삼성전자·SK하이닉스 · 건설·가동 지연 시', 'head': '메모리 발주 지연 가능성', 'chips': ['한국 반도체'], 'source': '이투데이'}],
        'foot': {'chip': '전력망 변수', 'text': '전력망 비용 부담 확대는 투자를 위축시킬 수 있다'}}),
    ('counter', {'chips': ['방산'], 'title': '라인메탈 전망 하향, 수요가 사라졌나', 'kicker': '다르게 볼 점', 'topic': '계약 지연과 현금 유입 시점',
                 'source': 'CNBC', 'blocks': [
        {'sub': '기존 137억~142억유로 · 8월 이후 두 번째 하향', 'label': '2026년 매출 전망', 'value': '약 132억유로'},
        {'sub': '3분기 매출 32%·영업이익 62% 증가 · 전년 동기 대비', 'gold': True, 'label': '분기 말 수주잔고', 'value': '780억유로'},
        {'dir': 'dn', 'sub': '장 초반 하락 · 원인: 계약 지연·포탄 인수시험 차질', 'label': '주가 · 장중', 'value': '17% 넘게'}],
                 'rows': [{'text': '계약 체결·선수금 유입 시 수요 소멸보다 시점 이연에 가까울 수 있음', 'source': 'CNBC'}]}),
    ('signals', {'chips': ['일정'], 'title': '다음 장, 이 네 가지를 보세요', 'disclaimer': '투자 권유가 아닌 정보 제공 목적입니다.',
                 'note': '브리핑의 시각 · 자동차 상승만으로는 시장 회복을 판단하기 어렵습니다', 'rows': [
        {'topic': '반도체·외국인', 'conds': ['반도체 낙폭 축소', '외국인 매도 완화'],
         'then': '추가 하락 위험 축소 가능 · 다만 일시적 반등에 그치면 다시 판단이 어렵습니다'},
        {'topic': '데이터센터 승인', 'conds': ['투자 유지 합의', '승인 재개 구체화'],
         'then': '메모리 발주 지연 우려 약화 가능 · 다만 일시적 반등에 그치면 다시 판단이 어렵습니다'},
        {'topic': '호르무즈 해협', 'conds': ['해협 개방 공식 합의', '통항 정상화·유가 상승분 반납'],
         'then': '공급 위험 재부각 해석 약화 가능 · 다만 일시적 반등에 그치면 다시 판단이 어렵습니다'},
        {'topic': '물가·신용', 'conds': ['에너지 외 품목·임금 확산 진정', '소기업 대출 기준·위험 가산금리 완화'],
         'then': '긴축·신용 동반 부담 판단 약화 가능 · 다만 일시적 반등에 그치면 다시 판단이 어렵습니다'}]}),
    ('bars', {'chips': ['실적'], 'title': '이익은 두 회사에 몰렸다', 'unit_note': '직전 분기 대비', 'rows': [
        {'name': '전체 99곳', 'n': 37.6, 'label': '▲37.6%'}, {'name': '두 회사 제외 97곳', 'n': 8.82, 'label': '▼8.82%'}],
              'side': {'title': '실적 발표 99곳', 'rows': [['영업이익 전년 대비', '▲326.6%'], ['코스닥', '▼3.2%'], ['하회·적자 전환', '50곳(50.5%)']],
                       'signal': '두 회사를 뺀 이익이 다시 늘어나는지'}}),
    ('compare', {'chips': ['반도체'], 'title': '같은 반도체, 엇갈린 외국인 수급', 'chart_title': '등락률', 'chart_source': 'KRX',
                 'tiles': [{'label': 'SK하이닉스', 'value': '4,482억원', 'sub': '순매수'}, {'label': '삼성전자', 'value': '1,382억원', 'sub': '순매도'}],
                 'bars': [{'n': 2.22, 'label': '▲2.22%', 'name': 'SK하이닉스'}, {'n': 4.13, 'label': '▼4.13%', 'name': '삼성전자'}],
                 'arrow_text': '같은 업종, 다른 방향', 'extra': [['코스피 외국인', '757억원 순매수']]}),
]

LAYOUT_JS = r"""() => {
 const scn=[...document.querySelectorAll('.scene')].find(s=>s.style.display==='block'); const out=[];
 const w=document.createTreeWalker(scn,NodeFilter.SHOW_TEXT); let n; const rg=document.createRange();
 while((n=w.nextNode())){ const s=n.textContent; if(!s.trim()||n.parentElement.closest('svg')) continue;
  if(/undefined|NaN/.test(s)) out.push('undefined: '+s.trim());
  const starts=[]; let top=null;
  for(let i=0;i<s.length;i++){ if(/\s/.test(s[i])) continue; rg.setStart(n,i); rg.setEnd(n,i+1); const r=rg.getBoundingClientRect();
   if(r.width<0.5) continue; if(top===null||r.top>top+r.height*0.5){starts.push(i); top=r.top;} }
  for(const i of starts.slice(1)) if(s[i]==='·') out.push('line starts with a dot: '+s.slice(i,i+12));
  if(starts.length>1 && s.slice(starts[starts.length-1]).replace(/\s/g,'').length<=2) out.push('lone last word: '+s.trim());}
 for(const row of scn.querySelectorAll('.sig')){ const b=row.getBoundingClientRect();
  for(const e of row.querySelectorAll('.sthen,.sconds')){ const r=e.getBoundingClientRect(); if(r.bottom>b.bottom+1||r.top<b.top-1) out.push('out of its row: '+e.textContent.slice(0,16));}}
 const bars=[...scn.querySelectorAll('.bar')].map(b=>({left:b.getBoundingClientRect().left, colour:getComputedStyle(b).backgroundColor}));
 const values=[...scn.querySelectorAll('.num')].map(e=>[e.textContent.trim(), e.className]);
 return {problems: out, bars, values, rising: scn.querySelectorAll('rect[data-vbar]').length};}"""


def laid_out(tmp_path, scenes, design=None):
    """Each scene's settled frame measured in Chromium, from the template alone (no audio, no images)."""
    import shutil
    from importlib.resources import files

    from playwright.sync_api import sync_playwright

    from quant_company.video.motion import CHROMIUM_ARGS

    page = tmp_path / 'page'
    shutil.copytree(design or files('quant_company.video').joinpath('design'), page)
    rows = [{'id': f's{i}', 'chapter': '핵심 이슈', 'type': kind, 'narration': '', 'data': {**data, 'id': f's{i}'},
             't0': i * 10.0, 't1': i * 10.0 + 10} for i, (kind, data) in enumerate(scenes)]
    ep = {'date_label': '11.06 금', 'asof': '', 'sample_mark': '', 'brand_name': '뭐든story', 'logo': '', 'brand_label': '아침',
          'chapter_names': ['핵심 이슈'], 'ticker': {'label': '종가', 'items': [{'name': '금', 'value': '4,113.5', 'change': '1.0%', 'dir': 'up'}]},
          'scenes': rows, 'total': len(rows) * 10.0, 'changes': [], 'captions': []}
    (page / 'episode.js').write_text('window.EP=' + json.dumps(ep, ensure_ascii=False) + ';')
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=CHROMIUM_ARGS)
        tab = browser.new_page(viewport={'width': 1920, 'height': 1080})
        tab.goto((page / 'index.html').as_uri())
        tab.evaluate('document.fonts.ready')
        found = []
        for row in rows:
            tab.evaluate(f"seek({row['t1'] - 0.6})")
            found.append(tab.evaluate(LAYOUT_JS))
        browser.close()
    return found


def test_card_text_wraps_whole_and_stays_in_its_row_and_bars_follow_their_labels(tmp_path):
    """The final editor's most frequent findings (11/01-11/06: text out of its row, a lone last word, a line opening with
    '·', a fall drawn as a red rise, 'undefined' rows) are the template's to prevent, not a round of fixes each."""
    if not importlib.util.find_spec('playwright'):
        pytest.skip('Install optional video dependencies and Chromium')
    flow, counter, signals, bars, compare = laid_out(tmp_path, LAYOUT_SCENES)
    for scene in (flow, counter, signals, bars, compare):
        assert scene['problems'] == []
    red, blue = 'rgb(255, 92, 102)', 'rgb(94, 155, 255)'
    rise, fall = bars['bars'][:2]
    assert rise['colour'] == red and fall['colour'] == blue and fall['left'] < rise['left']
    # A side value written as a pair without n takes the colour of its own arrow.
    side = dict(bars['values'])
    assert 'dnn' in side['▼3.2%'] and 'upn' in side['▲326.6%'] and 'upn' not in side['50곳(50.5%)']
    # A rise and a fall are never two bars standing up from one baseline.
    assert compare['rising'] == 0 and sorted(b['colour'] for b in compare['bars']) == sorted([red, blue])

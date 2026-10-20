"""Daily Shorts: the cut's contract and checks, a real Chromium/FFmpeg vertical render, and the production flow on real
PostgreSQL. Model, speech, alignment and Slack's HTTP API are simulated fixtures; nothing here calls them."""

import importlib.util
import json
import re
import subprocess
from datetime import date

import pytest

from quant_company.config import Settings
from quant_company.contracts import ProviderFault
from quant_company.video import runner as runner_module
from quant_company.video.contracts import digest
from quant_company.video.render import checksum, run, verify_artifacts
from quant_company.video.runner import VideoRunner
from quant_company.video.shorts import (
    CHARS_PER_SECOND,
    CHECKS,
    HOOK_NARRATION,
    KO_COUNT,
    OUTRO,
    QA_JS,
    SAFE,
    ShortsPlan,
    ShortsRenderer,
    agenda_line,
    closes_of,
    cut_pictures,
    date_line,
    direction,
    estimated_seconds,
    issue_scenes,
    settle,
    shorts_prompt,
    shorts_violations,
)
from quant_company.video.shorts_store import ShortsStore

from .test_briefing import brief  # noqa: F401
from .test_video import body_source, video  # noqa: F401
from .test_video_delivery import FakeSlack, quiet_other_outbox, send
from .test_video_motion import (
    InspectedRenderer,
    InspectingProvider,
    WordAligner,
    episode,
    library,
    produce_inspected,
    source,
)
from .test_video_render import ffmpeg_binary


def shorts(**changes):
    value = dict(
        title='S&P 500 1.92% 상승, 반도체가 이끌었다', description='반도체가 이끈 미국 증시와 확산 여부를 짧게 정리합니다.',
        hook=dict(headline='반도체가 이끈 미국 증시, 확산은 아직', question='확산될까?',
                  narration='반도체가 주도했지만 업종별 흐름은 엇갈렸습니다.', claim_ids=['b001', 'b013']),
        issues=[dict(id='semis', scene_ids=['head', 'flow'], label='반도체 주도', agenda='반도체가 이끈 상승', big='1.92%',
                     big_label='S&P 500', dir='up', body='정규장 종가 기준 S&P 500이 올랐고 반도체가 상승을 주도했습니다.',
                     note_kind='의미', note='수익률 하락은 할인 부담을 낮출 수 있음',
                     narration='에스앤피 500이 정규장 종가 기준 1.92% 올랐고 반도체가 상승을 주도했습니다. '
                               '수익률 하락은 성장주의 할인 부담을 낮출 수 있습니다.',
                     claim_ids=['b013', 'b007', 'b009']),
                dict(id='check', scene_ids=['photo', 'counter'], label='다르게 볼 점', agenda='금리만의 효과일까', big='0.59%',
                     big_label='나스닥 종합', dir='up', body='나스닥 종합도 정규장 종가 기준 0.59% 올랐지만 기술주 밖의 참여는 혼조였습니다.',
                     note_kind='확인할 점', note='비기술 업종으로 확산되는지',
                     narration='나스닥 종합은 0.59% 올랐지만 금리보다 반도체 자체의 재료가 영향을 줬을 수도 있습니다.',
                     claim_ids=['b014', 'b010', 'b011'])],
        signals=['비기술 업종 상승 확산', '한국 반도체 강세 지속'],
        signals_narration='비기술 업종으로 상승이 확산되는지, 한국 반도체 강세가 이어지는지 확인합니다.',
        signals_claim_ids=['b011', 'b012'])
    value.update(changes)
    return ShortsPlan.model_validate(value)


def cut(*, issue=None, **changes):
    """The fixture plan as a dict with one issue's fields (or the plan's own) changed."""
    raw = shorts().model_dump(mode='json')
    if issue is not None:
        raw['issues'][issue].update(changes)
    else:
        raw.update(changes)
    return raw


def violations(raw):
    return shorts_violations(ShortsPlan.model_validate(raw), body_source(), episode().model_dump(mode='json'))


def test_a_cut_is_a_hook_two_to_five_issues_and_signals():
    plan = shorts().model_dump(mode='json')
    for issues in (plan['issues'][:1], [{**plan['issues'][0], 'id': f'issue-{n}'} for n in range(6)]):
        with pytest.raises(ValueError, match='issues'):
            ShortsPlan.model_validate({**plan, 'issues': issues})
    with pytest.raises(ValueError, match='series name'):
        ShortsPlan.model_validate({**plan, 'title': '반도체가 이끌었다 | 9월 22일 아침 · 오늘의 증시story'})
    long = [{**plan['issues'][0], 'id': f'long-{n}', 'narration': '가' * 90} for n in range(5)]
    with pytest.raises(ValueError, match='Shorts length'):  # 5 x 90 + the hook and the signals
        ShortsPlan.model_validate({**plan, 'issues': long})
    with pytest.raises(ValueError, match='unique'):
        ShortsPlan.model_validate({**plan, 'issues': [plan['issues'][0], plan['issues'][0]]})
    with pytest.raises(ValueError, match='fixed scenes'):  # the cut's own scene IDs
        ShortsPlan.model_validate({**plan, 'issues': [plan['issues'][0], {**plan['issues'][1], 'id': 'outro'}]})
    with pytest.raises(ValueError, match='delivery tags'):
        ShortsPlan.model_validate({**plan, 'signals_narration': '[calm] 비기술 업종으로 확산되는지 확인합니다.'})
    with pytest.raises(ValueError, match='signs as words'):
        ShortsPlan.model_validate({**plan, 'signals_narration': '비기술 업종이 +2% 오르는지 확인합니다.'})


# A hook written under #194's 50-character limit, before the one-breath budget.
OLD_HOOK = '미국 증시는 반도체가 주도했지만 업종별 흐름은 엇갈렸고 확산 여부는 아직 모릅니다'


def test_the_hook_is_one_breath_after_the_date_line():
    # Owner, 10/20: the first preview's opener ran 11.5 s (the spec asks ~3-5 s); the on-screen headline carries the detail.
    plan = shorts().model_dump(mode='json')
    assert HOOK_NARRATION == 32 and len(plan['hook']['narration']) <= HOOK_NARRATION and len(OLD_HOOK) == 45
    assert not any('Hook narration' in f for f in violations(cut(hook={**plan['hook'], 'narration': '가' * 32})))
    # A longer hook is a finding the model rewrites before any speech is paid, never a schema error: a plan stored under
    # the old limits still validates after a release.
    old = ShortsPlan.model_validate({**plan, 'hook': {**plan['hook'], 'narration': OLD_HOOK}})
    assert 'Hook narration is 45 characters, over 32: one sentence in one breath after the date line' in ' '.join(
        shorts_violations(old, body_source(), episode().model_dump(mode='json')))
    # So is a total over the 400-character budget that still fits the schema's 420.
    raw = cut(signals_narration='가' * (405 - 5 * 66 - len(plan['hook']['narration'])))
    raw['issues'] = [{**plan['issues'][0], 'id': f'long-{n}', 'narration': '가' * 66} for n in range(5)]
    assert 'Narration is 405 characters in all, over 400: shorten the narrations' in violations(raw)
    # The length estimate counts the hook with the date line at the voice's pace.
    short, long = (ShortsPlan.model_validate({**plan, 'hook': {**plan['hook'], 'narration': '가' * n}}) for n in (10, 32))
    assert estimated_seconds(long) - estimated_seconds(short) == pytest.approx(22 / CHARS_PER_SECOND)
    prompt = shorts_prompt(body_source(), episode().model_dump(mode='json'))
    assert f'narration (8-{HOOK_NARRATION} characters' in prompt and 'one breath' in prompt


def test_fixed_lines():
    assert date_line({'source': {'day': '2026-10-20', 'edition_kind': 'am'}}) == '10월 20일 아침 브리핑입니다.'
    assert date_line({'source': {'day': '2026-10-20', 'edition_kind': 'close'}}).endswith('마감 브리핑입니다.')
    assert agenda_line(5) == '오늘 볼 이슈는 다섯 가지입니다.' and agenda_line(2) == '오늘 볼 이슈는 두 가지입니다.'
    assert [KO_COUNT[n] for n in (3, 4)] == ['세', '네']


def test_issues_cut_the_episode_issue_scenes_in_order():
    assert [s['id'] for s in issue_scenes(episode().model_dump(mode='json'))] == ['head', 'flow', 'photo', 'counter']
    assert any('Issue scene board is not an issue scene' in f for f in violations(cut(issue=0, scene_ids=['board'])))
    raw = cut()
    raw['issues'][0]['scene_ids'], raw['issues'][1]['scene_ids'] = ['photo', 'counter'], ['head', 'flow']
    assert any('out of episode order' in f for f in violations(raw))
    raw = cut()  # within an issue any order is fine; only the first scene of each issue follows the episode
    raw['issues'][0]['scene_ids'], raw['issues'][1]['scene_ids'] = ['head', 'counter'], ['flow']
    assert not any('episode order' in f for f in violations(raw))
    assert any('cited twice' in f for f in violations(cut(issue=1, scene_ids=['flow', 'counter'])))
    assert any('does not have' in f for f in violations(cut(issue=1, scene_ids=['nowhere'])))


def test_numbers_must_be_in_the_episode():
    # '21' is in the cited line b002 ('2026-09-21 종가') but in no scene of the delivered episode.
    raw = cut(issue=1, big='21', claim_ids=['b014', 'b002'], dir=None)
    assert any('not in the delivered episode' in f for f in violations(raw))
    assert not any('Unbound' in f for f in violations(raw))


def test_only_visible_episode_text_counts_as_in_the_episode():
    # '5' (from '05:00' in b013) and '21' (b002) are in cited lines but in no visible text of the episode; card numbers,
    # icon sizes and colours (112, '01', #5EA0FF) are not text.
    for big, ids in (('5', ['b013']), ('21', ['b014', 'b002']), ('112', ['b014'])):
        raw = cut(issue=1, big=big, claim_ids=ids, dir=None)
        assert any('not in the delivered episode' in f for f in violations(raw)), big
    assert 'not in the delivered episode' not in ' '.join(violations(cut()))


def test_an_over_long_plan_is_found_before_any_speech_is_paid(monkeypatch):
    from quant_company.video import shorts as shorts_module

    assert estimated_seconds(shorts()) < 60
    assert not any('Estimated length' in f for f in violations(cut()))
    # The plan's own character limit keeps real plans under the cap; a looser limit shows the estimate as the second guard.
    monkeypatch.setattr(shorts_module, 'NARRATION_LIMIT', 2000)
    raw = cut(signals_narration='가' * 10, hook=dict(cut()['hook'], narration='가' * 10))
    raw['issues'] = [{**cut()['issues'][0], 'id': f'long-{n}', 'narration': '가' * 95} for n in range(5)]
    plan = ShortsPlan.model_validate(raw)
    assert estimated_seconds(plan) > 90
    assert any('Estimated length' in f and 'over the 90 s cap' in f for f in violations(raw))


def test_claim_ids_are_bound_to_the_episode_scenes():
    # b010 is used by the episode (photo), but not by the scenes this issue cites (head, flow).
    assert any('issue semis' in f and 'cited scenes' in f for f in violations(cut(issue=0, claim_ids=['b013', 'b010'])))
    # b002 is a briefing line no scene of the episode carries.
    assert any('the hook' in f and 'anywhere in the episode' in f for f in violations(cut(hook=dict(cut()['hook'], claim_ids=['b002']))))
    assert any('the signals' in f and 'anywhere in the episode' in f for f in violations(cut(signals_claim_ids=['b002'])))


def test_title_and_description_numbers_must_be_in_the_delivered_episode():
    # '21' is in the cited line b002 (also cited by the issue) but in no visible text of the episode.
    raw = cut(title='S&P 500 21일 상승, 반도체가 이끌었다')
    raw['issues'][0]['claim_ids'] = ['b013', 'b007', 'b009', 'b002']
    found = violations(raw)
    assert any('title or description is not in the delivered episode' in f for f in found), found


def test_two_issues_is_enough():
    assert len(shorts().issues) == 2
    assert shorts_violations(shorts(), body_source(), episode().model_dump(mode='json')) == []


@pytest.mark.parametrize('change,error', [
    (lambda r: r['issues'][0].update(big='5,400'), 'Unbound number'),
    (lambda r: r['issues'][0].update(dir='dn'), 'contradicts its line'),  # ▼ for the 1.92% rise
    (lambda r: r['issues'][1].update(narration='나스닥 종합은 0.59% 급락해 금리보다 반도체 재료가 영향을 줬을 수도 있습니다.'),
     'contradicts its line'),
    (lambda r: r['issues'][1].update(body='나스닥 종합은 0.59% 올랐지만 기술주 밖의 참여는 혼조였습니다.'), '종가'),  # no label
    (lambda r: r['issues'][1].update(narration='보도에 따르면 나스닥 종합은 0.59% 올랐습니다. 반도체 재료도 있습니다.'), '보도에 따르면'),
    (lambda r: r.update(signals_narration='이 영상은 AI가 만든 요약입니다. 확산을 확인합니다.'), 'tool wording'),
    (lambda r: r.update(signals_narration='반도체는 지금 사세요. 확산을 확인합니다.'), 'Buy/sell'),
    (lambda r: r['hook'].update(headline='S&P 500이 3.1% 올랐다'), 'Unbound number'),
])
def test_the_cut_follows_the_episode_binding_rules(change, error):
    raw = cut()
    change(raw)
    assert any(error in f for f in violations(raw)), violations(raw)


def test_a_list_shares_its_verb_and_a_date_is_no_close():
    # 10/20 AM: all three cuts were given up on these lines.
    line = ('19일 정규장에서 에너지저장장치(ESS) 수요 기대에 삼성SDI는 11.70%, LG에너지솔루션은 7.20%, 포스코퓨처엠은 '
            '10.90% 상승 마감했다.')
    assert [direction(line, v) for v in ('11.70%', '7.20%', '10.90%')] == ['up', 'up', 'up']
    assert direction('삼성SDI는 11.70%, 반면 LG에너지솔루션은 3.10% 하락했다.', '11.70%') is None
    assert closes_of('나스닥 종합 26,430.89 pt · +1.11% · 정규장 종가 10/20 05:00 KST · Yahoo chart') == {'26430.89', '1.11'}


def test_an_arrow_its_line_does_not_state_is_dropped_not_rejected():
    raw = cut(issue=0, big='5,300', claim_ids=['b013'])  # a level: its line states no direction for it
    plan, dropped = settle(ShortsPlan.model_validate(raw), body_source())
    assert dropped == ['semis: 5,300'] and plan.issues[0].dir is None
    assert shorts_violations(plan, body_source(), episode().model_dump(mode='json')) == []
    raw = cut(issue=0, big='1.92%', dir='dn')  # against its line: kept, and the checks fail
    plan, dropped = settle(ShortsPlan.model_validate(raw), body_source())
    assert dropped == [] and any('contradicts its line' in f for f in violations(raw))


def test_the_prompt_cuts_the_delivered_episode_and_leaves_flagged_claims_out():
    prompt = shorts_prompt(body_source(), episode().model_dump(mode='json'), ['나스닥 수치 해석이 과장됨'], '- Unbound number')
    assert episode().upload.title in prompt and 'no greeting' in prompt and "Never say '보도에 따르면'" in prompt
    assert '나스닥 수치 해석이 과장됨' in prompt and 'Unbound number' in prompt and 'b013' in prompt


def tones(folder, seconds, ffmpeg):
    """One synthetic take per scene (opener, agenda, issues, outro), as long as given."""
    paths = []
    for i, length in enumerate(seconds):
        path = folder / f's{i}.wav'
        run([ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i', f'sine=frequency={330 + i * 60}:sample_rate=48000:duration={length}',
             str(path)])
        paths.append(path)
    return paths


def render_fixture(tmp_path, assets=True):
    """Settings, job and the episode's pictures (the motion fixture library) for a real Shorts render."""
    if not importlib.util.find_spec('playwright'):
        pytest.skip('Install optional video dependencies and Chromium')
    ffmpeg = ffmpeg_binary()
    settings = Settings(video_artifact_dir=tmp_path, video_ffmpeg=ffmpeg, video_ffprobe='', video_asset_dir=tmp_path / 'assets')
    if assets:
        library(tmp_path / 'assets', ffmpeg)
    job = {'id': 'fixture', 'source': {**body_source(), 'edition_kind': 'am'}, 'policy': {'template': 'motion-v2'}}
    return ffmpeg, settings, job


# A short agenda take is held for the list; the outro is held for its button.
TAKES = [4, 1, 6, 6, 3]


def test_a_vertical_cut_renders_inside_the_shorts_safe_area(tmp_path):
    ffmpeg, settings, job = render_fixture(tmp_path)
    plan = shorts()
    paths = tones(tmp_path, TAKES, ffmpeg)
    manifest = ShortsRenderer(settings, WordAligner()).render(job, plan, episode().model_dump(mode='json'), tmp_path / 'out', paths)
    assert manifest['size'] == [1080, 1920] and manifest['fps'] == 30
    scenes = manifest['scenes']
    assert [s['id'] for s in scenes] == ['opener', 'agenda', 'semis', 'check', 'outro']
    assert [s['type'] for s in scenes] == ['opener', 'agenda', 'issue', 'issue', 'outro']
    assert not any(k in f for f in manifest['findings'] for k in ('outside-safe-area', 'overlap', 'covered')), manifest['findings']
    assert scenes[1]['t1'] - scenes[1]['t0'] >= 1.0 + 0.9 * len(plan.issues) - 1e-6
    assert scenes[-1]['t1'] - scenes[-1]['t0'] >= 6.0 - 1e-6
    assert scenes[0]['t0'] == 0 and manifest['duration'] == pytest.approx(scenes[-1]['t1'], abs=.5)
    assert manifest['music'] is None
    pictures = {p['scene']: p for p in manifest['pictures']}
    assert set(pictures) == {s['id'] for s in scenes}  # every scene shows a picture
    assert pictures['check']['credit'] == '자료사진 · Fixture Author · CC BY 4.0' and pictures['semis']['credit'] == ''
    assert not any('without a picture' in f for f in manifest['findings'])
    upload = (tmp_path / 'out' / 'shorts-upload.txt').read_text()
    assert plan.title in upload and '#Shorts' in upload and '관련 동영상' in upload and '|' not in upload.split('\n')[1]
    assert '[사진 출처]\n자료사진 · Fixture Author · CC BY 4.0\n' in upload
    srt = (tmp_path / 'out' / 'shorts.srt').read_text()
    assert '9월 22일 아침 브리핑입니다.' in srt and '오늘 볼 이슈는 두 가지입니다.' in srt and '본편에서 확인하세요.' in srt
    assert [f['scene'] for f in manifest['review']['frames']] == [s['id'] for s in scenes]
    assert all((tmp_path / 'out' / f['file']).stat().st_size for f in manifest['review']['frames'])
    assert not (tmp_path / 'out' / 'page').exists() and not list((tmp_path / 'out').glob('aud-*.wav'))
    verify_artifacts(manifest, tmp_path)


def on_page(page, scripts, t=0.0, style=''):
    """Each script's value on the built page at time t (a QA function gets the safe area), optionally with a style
    added first."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            tab = browser.new_page(viewport={'width': 1080, 'height': 1920})
            tab.goto((page / 'index.html').as_uri())
            tab.evaluate('document.fonts.ready')
            if style:
                tab.add_style_tag(content=style)
            tab.evaluate(f'seek({t})')
            return [tab.evaluate(s, SAFE) if s.startswith('(safe)') else tab.evaluate(s) for s in scripts]
        finally:
            browser.close()


def scene_classes(page):
    """The page's scenes as built: {id: (EP bg, blur, the scene element's classes)}."""
    [scenes] = on_page(page, ["EP.scenes.map(s => ({id: s.id, bg: s.bg, blur: s.blur,"
                              " cls: [...document.querySelector(`.scene[data-id=\"${s.id}\"]`).classList]}))"])
    return {s['id']: (s['bg'], s['blur'], s['cls']) for s in scenes}


def test_layout_qa_finds_text_covered_by_the_background(tmp_path):
    ffmpeg, settings, job = render_fixture(tmp_path)
    built = ShortsRenderer(settings, WordAligner()).build(job, shorts(), episode().model_dump(mode='json'), tmp_path / 'out',
                                                          tones(tmp_path, TAKES, ffmpeg))
    issue = built['scenes'][2]
    [found] = on_page(built['page'], [QA_JS], issue['t1'] - 0.3)
    assert found == []
    # The pictures drawn over the text (the first draft's bug): every box is still in place, but nothing can be read.
    [found] = on_page(built['page'], [QA_JS], issue['t1'] - 0.3, style='#bgs{z-index:5}')
    assert {f[0] for f in found} == {'covered'} and any('1.92%' in f[1] for f in found), found


def test_build_keeps_a_minus_without_an_arrow(tmp_path):
    ffmpeg, settings, job = render_fixture(tmp_path)
    raw = shorts().model_dump(mode='json')
    raw['issues'][0].update(big='-0.2%', dir=None)  # a signed level: the sign is the fact
    raw['issues'][1].update(big='-0.59%', dir='dn')  # the arrow replaces the sign
    built = ShortsRenderer(settings, WordAligner()).build(job, ShortsPlan.model_validate(raw), episode().model_dump(mode='json'),
                                                          tmp_path / 'out', tones(tmp_path, TAKES, ffmpeg))
    [shown] = on_page(built['page'], ["[...document.querySelectorAll('.scene.issue .num .v')].map(e => e.textContent)"])
    assert shown == ['-0.2%', '▼0.59%']


def test_a_long_total_is_a_finding_not_a_raise(tmp_path):
    ffmpeg, settings, job = render_fixture(tmp_path)
    takes = tones(tmp_path, [20, 20, 20, 20, 20], ffmpeg)  # ~100 s of paid speech
    built = ShortsRenderer(settings, WordAligner()).build(job, shorts(), episode().model_dump(mode='json'), tmp_path / 'out', takes)
    assert 90 < built['total'] < 180
    assert any('at most 90 s' in f for f in built['findings'])


def test_a_sparse_library_entry_cannot_raise_the_credit(tmp_path):
    ffmpeg = ffmpeg_binary()
    settings = Settings(video_artifact_dir=tmp_path, video_ffmpeg=ffmpeg, video_asset_dir=tmp_path / 'assets')
    shelf = library(tmp_path / 'assets', ffmpeg)
    for entry in shelf.values():
        entry.pop('author', None)
        entry.pop('license', None)
    job = {'id': 'fixture', 'source': source(), 'policy': {'template': 'motion-v2'}}
    pictures = cut_pictures(job, episode().model_dump(mode='json'), shorts(), settings, shelf)
    assert pictures['issues'][1]['credit'] == '자료사진 ·  · '


def test_every_scene_has_a_picture(tmp_path):
    ffmpeg, settings, job = render_fixture(tmp_path)
    paths = tones(tmp_path, TAKES, ffmpeg)
    raw = shorts().model_dump(mode='json')
    raw['issues'][0]['scene_ids'], raw['issues'][1]['scene_ids'] = ['flow'], ['counter']  # cards without a picture
    plan = ShortsPlan.model_validate(raw)
    bare = episode(thumbnail_image='').model_dump(mode='json')
    built = ShortsRenderer(settings, WordAligner()).build(job, plan, bare, tmp_path / 'out', paths)
    shown = scene_classes(built['page'])
    # The cover is the first picture the episode shows (the headline card's), sharp behind the opener and blurred
    # behind the agenda and the outro. The issues without their own picture show their chapter's other pictures blurred,
    # each one the scene before does not show.
    assert {i: (bg, blur) for i, (bg, blur, _) in shown.items()} == {
        'opener': ('img/illu.png', False), 'agenda': ('img/illu.png', True), 'semis': ('img/photo.png', True),
        'check': ('img/illu.png', True), 'outro': ('img/illu.png', True)}
    assert (built['page'] / 'img' / 'illu.png').is_file() and (built['page'] / 'img' / 'photo.png').is_file()
    assert not any('nophoto' in cls for *_, cls in shown.values()) and not any('picture' in f for f in built['findings'])
    # No picture anywhere (no reviewed library, no episode picture): a gradient, never a bare frame, and a finding.
    settings.video_asset_dir = tmp_path / 'empty'
    built = ShortsRenderer(settings, WordAligner()).build(job, plan, bare, tmp_path / 'none', paths)
    shown = scene_classes(built['page'])
    assert all(bg is None and 'nophoto' in cls for bg, _, cls in shown.values()), shown
    assert [f for f in built['findings'] if 'picture' in f] == [
        f'Shorts scene without a picture: {i}' for i in ('opener', 'agenda', 'semis', 'check', 'outro')]


def test_an_issue_without_a_picture_takes_one_from_its_chapter_before_the_cover(tmp_path):
    ffmpeg, settings, job = render_fixture(tmp_path)
    raw = episode().model_dump(mode='json')  # the thumbnail picture (the cover) is the headline card's illustration
    raw['scenes'][6]['chapter'] = '함께 볼 이슈'  # counter: alone in its chapter, with no picture
    assert [(s['id'], s['chapter']) for s in issue_scenes(raw)] == [
        ('head', '핵심 이슈'), ('flow', '핵심 이슈'), ('photo', '핵심 이슈'), ('counter', '함께 볼 이슈')]
    plan = shorts().model_dump(mode='json')
    plan['issues'][0]['scene_ids'], plan['issues'][1]['scene_ids'] = ['flow'], ['counter']  # cards without a picture
    plan = ShortsPlan.model_validate(plan)
    shelf = json.loads((tmp_path / 'assets' / 'manifest.json').read_text())
    nearby = cut_pictures(job, raw, plan, settings, shelf)['nearby']
    assert [[p['path'].rsplit('/', 1)[1] for p in n] for n in nearby] == [['illu.png', 'photo.png'], []]
    built = ShortsRenderer(settings, WordAligner()).build(job, plan, raw, tmp_path / 'out', tones(tmp_path, TAKES, ffmpeg))
    shown = scene_classes(built['page'])
    # flow: its chapter's nearest picture is the cover the agenda shows just before, so the photo two scenes on, with
    # its own credit. counter: nothing else in its chapter, so the cover.
    assert {i: (bg, blur) for i, (bg, blur, _) in shown.items()} == {
        'opener': ('img/illu.png', False), 'agenda': ('img/illu.png', True), 'semis': ('img/photo.png', True),
        'check': ('img/illu.png', True), 'outro': ('img/illu.png', True)}
    credits = {p['scene']: p['credit'] for p in built['pictures']}
    assert credits['semis'] == '자료사진 · Fixture Author · CC BY 4.0' and credits['check'] == '' and credits['agenda'] == ''
    [drawn] = on_page(built['page'], ["[...document.querySelectorAll('.scene')].map(e => e.querySelector('.credit')?.textContent || '')"])
    assert drawn == ['', '', '자료사진 · Fixture Author · CC BY 4.0', '', '']
    # The chapter's picture is taken even when the scene before shows it: the cover comes only after the chapter.
    raw['scenes'][5]['chapter'] = '함께 볼 이슈'  # photo and counter
    plan = shorts().model_dump(mode='json')
    plan['issues'][0]['scene_ids'], plan['issues'][1]['scene_ids'] = ['photo'], ['counter']
    built = ShortsRenderer(settings, WordAligner()).build(job, ShortsPlan.model_validate(plan), raw, tmp_path / 'again',
                                                          tones(tmp_path, TAKES, ffmpeg))
    shown = scene_classes(built['page'])
    assert (shown['semis'][:2], shown['check'][:2]) == (('img/photo.png', False), ('img/photo.png', True))
    assert {p['scene']: p['credit'] for p in built['pictures']}['check'] == '자료사진 · Fixture Author · CC BY 4.0'


def test_the_headline_breaks_after_its_first_comma(tmp_path):
    ffmpeg, settings, job = render_fixture(tmp_path)
    takes = tones(tmp_path, TAKES, ffmpeg)
    raw = shorts().model_dump(mode='json')
    # The headline's text, its breaks, whether each part is drawn on one line, and its size against the CSS size (92px).
    lines = ("[...document.querySelectorAll('.opener .h1')].map(e => [e.innerText, e.querySelectorAll('br').length,"
             " [...e.querySelectorAll('.part')].every(p => new Set([...p.getClientRects()].map(r => Math.round(r.top))).size === 1),"
             " parseFloat(getComputedStyle(e).fontSize) / 92])")
    shown = {}
    # The owner's 10/20 headline broke mid-phrase ('나스닥 1.11% 반등, AI 칩 / 살 돈엔 금리 10% 이상').
    for name, headline in (('comma', '나스닥 1.11% 반등, AI 칩 살 돈엔 금리 10% 이상'), ('fixture', raw['hook']['headline']),
                           ('wide', '반도체, 미국 증시를 이끌었지만 비기술 업종 확산은 아직 혼조였다'), ('plain', '반도체가 이끈 미국 증시')):
        raw['hook']['headline'] = headline
        built = ShortsRenderer(settings, WordAligner()).build(job, ShortsPlan.model_validate(raw), episode().model_dump(mode='json'),
                                                              tmp_path / name, takes)
        [[shown[name]], fullwidth] = on_page(built['page'], [lines, "headline('반도체 주도，확산은 아직')"])
    # Its second part needs the headline ~13% smaller to fit one line; the fixture's fits at the headline size.
    assert shown['comma'][:3] == ['나스닥 1.11% 반등,\nAI 칩 살 돈엔 금리 10% 이상', 1, True] and 0.85 <= shown['comma'][3] < 0.9
    assert shown['fixture'] == ['반도체가 이끈 미국 증시,\n확산은 아직', 1, True, 1]
    # A second part that cannot fit one line even 15% smaller: the headline wraps as written, at its size.
    assert shown['wide'] == ['반도체, 미국 증시를 이끌었지만 비기술 업종 확산은 아직 혼조였다', 0, True, 1]
    assert shown['plain'] == ['반도체가 이끈 미국 증시', 0, True, 1]
    assert '<br class="comma">' in fullwidth and '반도체 주도，</span>' in fullwidth


def test_music_is_optional(tmp_path):
    av = pytest.importorskip('av')  # the cut is read back with PyAV (the video extra)

    from quant_company.video.shorts import music_track

    ffmpeg, settings, job = render_fixture(tmp_path)
    assert music_track(settings, job) is None  # no folder
    (tmp_path / 'assets' / 'music').mkdir()
    assert music_track(settings, job) is None  # an empty folder
    run([ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=220:sample_rate=44100:duration=3',
         str(tmp_path / 'assets' / 'music' / 'a.mp3')])
    assert music_track(settings, job) == tmp_path / 'assets' / 'music' / 'a.mp3'
    paths = tones(tmp_path, TAKES, ffmpeg)
    manifest = ShortsRenderer(settings, WordAligner()).render(job, shorts(), episode().model_dump(mode='json'),
                                                             tmp_path / 'out', paths)
    assert manifest['music'] == 'a.mp3'
    total = manifest['scenes'][-1]['t1']
    with av.open(str(tmp_path / 'out' / 'shorts.mp4')) as media:
        stream = media.streams.audio[0]
        assert float(stream.duration * stream.time_base) == pytest.approx(total, abs=.25)
    # The bed plays on under the outro's held silence, where no narration is left (the 3-s track loops).
    outro = manifest['scenes'][-1]
    report = subprocess.run([ffmpeg, '-hide_banner', '-ss', f"{outro['t1'] - 1.8:.2f}", '-t', '0.6', '-i',
                             str(tmp_path / 'out' / 'shorts.mp4'), '-af', 'volumedetect', '-f', 'null', '-'],
                            capture_output=True, text=True, check=True).stderr
    assert float(re.search(r'mean_volume: (-?[\d.]+) dB', report).group(1)) > -60
    # Several tracks take turns by the episode's date.
    (tmp_path / 'assets' / 'music' / 'b.mp3').write_bytes((tmp_path / 'assets' / 'music' / 'a.mp3').read_bytes())
    day = date.fromisoformat(job['source']['day'])
    assert music_track(settings, job).name == ['a.mp3', 'b.mp3'][day.toordinal() % 2]


def test_music_is_never_fatal_and_never_lifts_the_peak(tmp_path, monkeypatch):
    from quant_company.video import shorts as shorts_module
    from quant_company.video.shorts import mix_music

    ffmpeg = ffmpeg_binary()
    settings = Settings(video_artifact_dir=tmp_path, video_ffmpeg=ffmpeg, video_ffprobe='')
    # Clicks at the narration chain's 0.79 limit over a steady loud bed: too short for the ducking to catch, so without
    # a limiter after the mix the two add up past -2 dBFS (about -1.6 dB).
    narration, bed = tmp_path / 'narration.wav', tmp_path / 'bed.wav'
    click = '0.79*eq(mod(n\\,12000)\\,0)*gte(t\\,1)'
    run([ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i', f'aevalsrc={click}|{click}:s=48000:d=4', str(narration)])
    run([ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i', 'aevalsrc=0.9|0.9:s=48000:d=4', str(bed)])
    sound, music, found = mix_music(settings, tmp_path, narration, 4.0, bed)
    assert (sound, music, found) == (tmp_path / 'mixed.wav', 'bed.wav', [])
    report = subprocess.run([ffmpeg, '-hide_banner', '-i', str(sound), '-af', 'volumedetect', '-f', 'null', '-'],
                            capture_output=True, text=True, check=True).stderr
    assert float(re.search(r'max_volume: (-?[\d.]+) dB', report).group(1)) <= -2.0
    # A track FFmpeg cannot read, or a mix that hangs: the cut goes on with the narration alone.
    broken = tmp_path / 'broken.mp3'
    broken.write_bytes(b'not an mp3 at all' * 100)
    assert mix_music(settings, tmp_path, narration, 4.0, broken) == (
        narration, None, ['Music bed broken.mp3 could not be mixed; the cut has no music'])

    def hung(argv, timeout=600):
        raise subprocess.TimeoutExpired(argv, timeout)

    monkeypatch.setattr(shorts_module, 'run', hung)
    assert mix_music(settings, tmp_path, narration, 4.0, bed)[:2] == (narration, None)


class ShortsFileRenderer:
    def __init__(self):
        self.calls = []

    def render(self, job, plan, episode, directory, paths, stop=None):
        self.calls.append((len(paths), paths[-1]))
        files = {'shorts.mp4': b'simulated-shorts', 'shorts.srt': b'1\n00:00:00,000 --> 00:00:01,000\nx\n',
                 'shorts-upload.txt': '[제목]\nS&P 500 1.92% 상승\n'.encode(), 'shorts-plan.json': plan.model_dump_json().encode()}
        for name, content in files.items():
            (directory / name).write_bytes(content)
        manifest = {'title': plan.title, 'duration': 46.0, 'qa_passed': True, 'findings': [], 'directory': str(directory),
                    'scenes': [{'id': i.id, 'type': 'issue'} for i in plan.issues],
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
    count = 2 + len(shorts().issues) + 1
    assert len(speech.calls) - takes == count == 5
    assert speech.calls[-count]['text'].startswith('9월 22일 아침 브리핑입니다.') and speech.calls[-1]['text'].endswith(OUTRO['say'])
    assert renderer.calls == [(count, renderer.calls[0][1])]
    with store.db.transaction() as conn:
        message = conn.execute("SELECT o.text,o.thread_ts FROM outbox o WHERE o.id=%s", (row['delivery_message_id'],)).fetchone()
        keys = [r['id'].split(':', 1)[1] for r in conn.execute(
            "SELECT id FROM video_effects WHERE job_id=%s AND id LIKE %s", (job['id'], '%:shorts-speech-%')).fetchall()]
    assert message['text'].startswith('<@UHUMAN>\n쇼츠 준비 완료') and '이슈 2개' in message['text'] and len(keys) == count
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


async def test_a_plan_stored_under_the_old_limits_is_still_voiced_and_rendered(brief, video, tmp_path, monkeypatch):  # noqa: F811
    # A cut written under #194 (a 45-character hook) waits between ticks with no lease while a release swaps the worker.
    store, clock = video
    job, provider, speech = await delivered_episode(store, tmp_path, monkeypatch, brief, clock)
    store.settings.video_shorts_enabled = True
    row = await shorts_ticks(store, provider, speech, ShortsFileRenderer(), job, limit=1)
    assert row['state'] == 'synthesizing', row['error']
    stored = {**row['plan'], 'hook': {**row['plan']['hook'], 'narration': OLD_HOOK}}
    with store.db.transaction() as conn:
        conn.execute('UPDATE video_shorts SET plan=%s::jsonb WHERE job_id=%s', (json.dumps(stored), job['id']))
    renderer, takes = ShortsFileRenderer(), len(speech.calls)
    row = await shorts_ticks(store, provider, speech, renderer, job)
    assert row['state'] == 'delivering', row['error']
    assert speech.calls[takes]['text'] == '9월 22일 아침 브리핑입니다. ' + OLD_HOOK and len(renderer.calls) == 1


async def test_an_episode_with_under_two_issue_scenes_spends_no_turn(brief, video, tmp_path, monkeypatch):  # noqa: F811
    store, clock = video
    job, provider, speech = await delivered_episode(store, tmp_path, monkeypatch, brief, clock)
    store.settings.video_shorts_enabled = True
    plan = store.get(job['id'])['plan']
    for scene in plan['scenes']:
        if scene.get('chapter') == '함께 볼 이슈' or scene['id'] in ('flow', 'photo', 'counter'):
            scene['chapter'] = '시장'
    with store.db.transaction() as conn:
        conn.execute('UPDATE video_jobs SET plan=%s::jsonb WHERE id=%s', (json.dumps(plan), job['id']))
    turns = len([c for c in provider.calls if c.output_contract == 'video_shorts_v1'])
    row = await shorts_ticks(store, provider, speech, ShortsFileRenderer(), job)
    assert row['state'] == 'skipped' and row['error'] == 'too_few_issues'
    assert len([c for c in provider.calls if c.output_contract == 'video_shorts_v1']) == turns


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
    store, clock = video
    job, provider, speech = await delivered_episode(store, tmp_path, monkeypatch, brief, clock)
    store.settings.video_shorts_enabled = True
    provider.queues['video_shorts_v1'] = [ProviderFault('uncertain', 'the CLI exited'), shorts()]
    row = await shorts_ticks(store, provider, speech, ShortsFileRenderer(), job)
    assert row['state'] == 'delivering', row['error']
    assert [a['outcome'] for a in row['qa']['attempts']] == ['uncertain', 'passed']
    keys = [c.request_id.rsplit('-', 1)[-1] for c in provider.calls if c.output_contract == 'video_shorts_v1']
    assert keys == ['1', '2']


def given_up(store, job, checks):
    """The cut as a worker before CHECKS 4 left it: skipped on its checks, at that version of them."""
    with store.db.transaction() as conn:
        conn.execute("UPDATE video_shorts SET state='skipped',error='check_failed',lease_until=NULL,"
                     "qa=qa||jsonb_build_object('checks',%s::int) WHERE job_id=%s", (checks, job['id']))


def shorts_keys(provider):
    return [c.request_id.rsplit('-', 1)[-1] for c in provider.calls if c.output_contract == 'video_shorts_v1']


async def test_a_cut_given_up_on_older_checks_is_checked_again_without_a_new_turn(brief, video, tmp_path, monkeypatch):  # noqa: F811
    store, clock = video
    job, provider, speech = await delivered_episode(store, tmp_path, monkeypatch, brief, clock)
    store.settings.video_shorts_enabled = True
    provider.queues['video_shorts_v1'] = [shorts(), shorts(), shorts()]
    checks = runner_module.shorts_violations
    monkeypatch.setattr(runner_module, 'shorts_violations', lambda plan, source, episode: ['an older check'])
    row = await shorts_ticks(store, provider, speech, ShortsFileRenderer(), job, limit=3)
    assert row['state'] == 'writing' and [a['outcome'] for a in row['qa']['attempts']] == ['check_failed'] * 3
    given_up(store, job, CHECKS)
    shorts_store = ShortsStore(store.company)
    assert shorts_store.claim() is None  # given up on the current checks: not looked at again
    monkeypatch.setattr(runner_module, 'shorts_violations', checks)
    given_up(store, job, CHECKS - 1)  # as if given up before this version of the checks
    calls = len(provider.calls)
    row = await shorts_ticks(store, provider, speech, ShortsFileRenderer(), job)
    assert row['state'] == 'delivering', row['error']
    assert len(provider.calls) == calls  # the written versions, no new turn
    assert row['qa']['attempts'][-1]['recheck'] == {'checks': CHECKS, 'findings': []}
    assert 'recheck' not in row['qa'] and row['plan'] == shorts().model_dump(mode='json')


async def test_the_cut_given_up_by_the_old_worker_in_the_release_is_written_again(brief, video, tmp_path, monkeypatch):  # noqa: F811
    # 10/20 PM: the v1 worker claimed the cut at 19:00 while claude-runtime already wrote the v2 plan, so all three turns
    # were rejected and the cut was skipped on checks 3 with no version written.
    store, clock = video
    job, provider, speech = await delivered_episode(store, tmp_path, monkeypatch, brief, clock)
    store.settings.video_shorts_enabled = True
    provider.queues['video_shorts_v1'] = [ProviderFault('invalid_output', 'hook: Extra inputs are not permitted; scenes: Field required')]
    row = await shorts_ticks(store, provider, speech, ShortsFileRenderer(), job, limit=3)
    assert [a['outcome'] for a in row['qa']['attempts']] == ['rejected'] * 3
    assert CHECKS == 4
    given_up(store, job, 3)
    provider.queues['video_shorts_v1'] = [shorts()]
    row = await shorts_ticks(store, provider, speech, ShortsFileRenderer(), job)
    assert row['state'] == 'delivering', row['error']
    assert [a['outcome'] for a in row['qa']['attempts']] == ['rejected'] * 3 + ['passed'] and 'recheck' not in row['qa']
    assert shorts_keys(provider) == ['1', '2', '3', '4']


async def test_a_cut_is_produced_with_its_fewest_findings_after_the_last_version(brief, video, tmp_path, monkeypatch):  # noqa: F811
    store, clock = video
    job, provider, speech = await delivered_episode(store, tmp_path, monkeypatch, brief, clock)
    store.settings.video_shorts_enabled = True
    titles = [f'반도체가 이끈 미국 증시 {name}판' for name in ('첫', '둘째', '셋째', '넷째', '다섯째')]
    found = {t: [f'{t} 지적 {"가나다라"[k]}' for k in range(n)] for t, n in zip(titles, (3, 1, 2, 1, 4), strict=True)}
    provider.queues['video_shorts_v1'] = [shorts(title=t) for t in titles]
    monkeypatch.setattr(runner_module, 'shorts_violations', lambda plan, source, episode: found[plan.title])
    row = await shorts_ticks(store, provider, speech, ShortsFileRenderer(), job, limit=16)  # 5 versions, 5 takes, render
    assert row['state'] == 'delivering', row['error']
    assert [a['outcome'] for a in row['qa']['attempts']] == ['check_failed'] * 5 and shorts_keys(provider) == ['1', '2', '3', '4', '5']
    # One finding each in the second and the fourth version: the latest is produced, its finding in the thread.
    assert row['plan']['title'] == titles[3] and row['qa']['shipped_attempt'] == 4
    assert row['qa']['shipped_with_findings'] == ['반도체가 이끈 미국 증시 넷째판 지적 가']
    with store.db.transaction() as conn:
        text = conn.execute('SELECT text FROM outbox WHERE id=%s', (row['delivery_message_id'],)).fetchone()['text']
    assert '자동 검사 지적 1건(아래, 제작은 계속함)' in text and '• 대본: 반도체가 이끈 미국 증시 넷째판 지적 가\n' in text


async def test_a_cut_with_no_valid_version_waits_and_writes_again(brief, video, tmp_path, monkeypatch):  # noqa: F811
    from datetime import UTC, datetime, timedelta

    store, clock = video
    job, provider, speech = await delivered_episode(store, tmp_path, monkeypatch, brief, clock)
    store.settings.video_shorts_enabled = True
    provider.queues['video_shorts_v1'] = [ProviderFault('invalid_output', 'issues: Field required')]
    row = await shorts_ticks(store, provider, speech, ShortsFileRenderer(), job, limit=5)
    assert row['state'] == 'writing' and [a['outcome'] for a in row['qa']['attempts']] == ['rejected'] * 5
    runner = VideoRunner(store.company, provider, speech)
    runner.shorts_renderer = ShortsFileRenderer()
    await runner.tick()
    row = ShortsStore(store.company).get(job['id'])
    # Nothing to produce: never skipped, asked again in 30 minutes (until the next episode starts).
    assert row['state'] == 'writing' and row['error'] == 'no_valid_version' and len(row['qa']['attempts']) == 5
    assert row['lease_until'] > datetime.now(UTC) + timedelta(minutes=29)
    await runner.tick()  # still waiting
    assert len(shorts_keys(provider)) == 5
    provider.queues['video_shorts_v1'] = [shorts()]
    row = await shorts_ticks(store, provider, speech, ShortsFileRenderer(), job)
    assert row['state'] == 'delivering', row['error']
    assert shorts_keys(provider) == ['1', '2', '3', '4', '5', '6'] and row['qa']['attempts'][-1]['outcome'] == 'passed'


def test_cut_pictures_follow_the_cited_scenes(tmp_path):
    ffmpeg = ffmpeg_binary()
    settings = Settings(video_artifact_dir=tmp_path, video_ffmpeg=ffmpeg, video_asset_dir=tmp_path / 'assets')
    shelf = library(tmp_path / 'assets', ffmpeg)
    job = {'id': 'fixture', 'source': source(), 'policy': {'template': 'motion-v2'}}
    raw = episode().model_dump(mode='json')
    plan = shorts()
    pictures = cut_pictures(job, raw, plan, settings, shelf)
    assert pictures['issues'][0] == {'path': str(tmp_path / 'assets' / 'illu.png'), 'credit': ''}
    assert pictures['issues'][1]['path'] == str(tmp_path / 'assets' / 'photo.png')
    assert pictures['issues'][1]['credit'] == '자료사진 · Fixture Author · CC BY 4.0'
    assert pictures['hook'] == pictures['cover'] == pictures['issues'][0]  # the episode thumbnail is fixture-illustration
    bare = shorts(issues=[{**plan.model_dump(mode='json')['issues'][0], 'scene_ids': ['flow']},
                          {**plan.model_dump(mode='json')['issues'][1], 'scene_ids': ['photo', 'counter']}])
    raw['thumbnail_image'] = ''
    pictures = cut_pictures(job, raw, bare, settings, shelf)
    assert pictures['issues'][0] is None and pictures['hook'] == pictures['issues'][1]


def test_takes_open_with_the_date_and_close_with_the_outro():
    job = {'id': 'fixture', 'source': source(), 'policy': {'voice': 'v', 'speech_model': 'm'}}
    plan = shorts()
    takes = VideoRunner.shorts_takes(None, job, plan)
    texts = [request['text'] for _, request in takes]
    assert len(takes) == 2 + len(plan.issues) + 1
    assert texts[0].startswith('9월 22일 아침 브리핑입니다. ') and texts[1] == '오늘 볼 이슈는 두 가지입니다.'
    assert texts[-1].endswith(OUTRO['say'])
    assert all(key.startswith(f'shorts-speech-{i}-') for i, (key, _) in enumerate(takes))


async def test_recheck_skips_versions_of_the_old_shape(brief, video, tmp_path, monkeypatch):  # noqa: F811
    store, clock = video
    job, provider, speech = await delivered_episode(store, tmp_path, monkeypatch, brief, clock)
    store.settings.video_shorts_enabled = True
    provider.queues['video_shorts_v1'] = [shorts(), shorts(), shorts()]
    checks = runner_module.shorts_violations
    monkeypatch.setattr(runner_module, 'shorts_violations', lambda plan, source, episode: ['a check'])
    row = await shorts_ticks(store, provider, speech, ShortsFileRenderer(), job, limit=3)
    assert [a['outcome'] for a in row['qa']['attempts']] == ['check_failed'] * 3
    monkeypatch.setattr(runner_module, 'shorts_violations', checks)
    old = {'title': 'old', 'description': 'old', 'hook': '옛 훅', 'points': [], 'closing': 'x'}
    given_up(store, job, CHECKS - 1)
    with store.db.transaction() as conn:
        conn.execute("UPDATE video_effects SET receipt=jsonb_set(receipt,'{output}',%s::jsonb) WHERE job_id=%s AND id LIKE %s",
                     (json.dumps(old), job['id'], '%:shorts-_'))
    row = await shorts_ticks(store, provider, speech, ShortsFileRenderer(), job)
    # The versions of the old shape are passed over and the cut writes on.
    assert row['state'] == 'delivering', row['error']
    assert [a.get('recheck') for a in row['qa']['attempts']] == [{'checks': CHECKS, 'findings': ['older plan shape']}] * 3 + [None]
    assert row['qa']['attempts'][-1]['outcome'] == 'passed' and shorts_keys(provider) == ['1', '2', '3', '4']

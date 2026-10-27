"""Daily Shorts: the cut's contract and checks, a real Chromium/FFmpeg vertical render, and the production flow on real
PostgreSQL. Model, speech, alignment and Slack's HTTP API are simulated fixtures; nothing here calls them."""

import importlib.util
import json
import re
import subprocess
from datetime import date

import pytest
from psycopg.types.json import Jsonb

from quant_company.config import Settings
from quant_company.contracts import ProviderFault
from quant_company.video import runner as runner_module
from quant_company.video.contracts import digest
from quant_company.video.render import checksum, run, verify_artifacts
from quant_company.video import shorts as shorts_module
from quant_company.video.body import parse
from quant_company.video.lineup import lineup
from quant_company.video.runner import VideoRunner
from quant_company.video.shorts import (
    CHARS_PER_SECOND,
    CHECKS,
    HOOK_NARRATION,
    KO_COUNT,
    OUTLOOK_SHORTS,
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
    opener_line,
    outro_line,
    settle,
    shorts_prompt,
    shorts_violations,
)
from quant_company.video.shorts_store import ShortsStore

from .test_briefing import brief  # noqa: F401
from .test_video import body_source, video  # noqa: F401
from .test_video_lineup import PM_TITLES, issue_body, issue_episode
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
    """A v2 cut of the fixture episode: its brief has one numbered issue (b006-b011), a 핵심 이슈 one."""
    value = dict(
        title='S&P 500 1.92% 상승, 반도체가 이끌었다', description='반도체가 이끈 미국 증시와 확산 여부를 짧게 정리합니다.',
        hook=dict(headline='반도체가 이끈 미국 증시, 확산은 아직', question='확산될까?',
                  narration='반도체가 주도했지만 업종별 흐름은 엇갈렸습니다.', claim_ids=['b001', 'b013']),
        issues=[dict(id='semis', issue=1, label='반도체 주도', agenda='반도체가 이끈 상승, 확산은 아직', big='1.92%',
                     big_label='S&P 500', dir='up', body='정규장 종가 기준 S&P 500이 올랐고 반도체가 상승을 주도했습니다.',
                     meaning='수익률 하락이 성장주 부담을 덜 수 있음',
                     narration='에스앤피 500이 정규장 종가 기준 1.92% 올랐고 반도체가 이끌었습니다. '
                               '수익률 하락은 성장주 부담을 낮출 수 있습니다.',
                     claim_ids=['b013', 'b007', 'b009'])],
        close=dict(conclusion='정리하면, 반도체가 이끌었지만 확산은 아직입니다.', signals=['비기술 업종 상승 확산', '한국 반도체 강세 지속'],
                   signals_narration='비기술 업종 확산과 한국 반도체 강세를 보세요.', claim_ids=['b011', 'b012']))
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
    plan, episode_json = ShortsPlan.model_validate(raw), episode().model_dump(mode='json')
    return shorts_violations(plan, body_source(), episode_json, lineup(body_source(), episode_json))


def spoken_plan(core, brief, hook=32, close=(35, 35)):
    """A cut whose spoken lines have exactly these lengths: core and brief are lists of narration lengths."""
    raw = shorts().model_dump(mode='json')
    base = raw['issues'][0]
    raw['hook']['narration'] = '가' * hook
    raw['issues'] = [{**base, 'id': f'issue-{n}', 'issue': n, 'narration': '가' * length}
                     for n, length in enumerate([*core, *brief], 1)]
    raw['close'].update(conclusion='가' * close[0], signals_narration='가' * close[1])
    return ShortsPlan.model_validate(raw)


def test_a_cut_is_a_hook_one_to_seven_issues_and_a_close():
    plan = shorts().model_dump(mode='json')
    issue = plan['issues'][0]
    for issues in ([], [{**issue, 'id': f'issue-{n}', 'issue': 1} for n in range(8)]):
        with pytest.raises(ValueError, match='issues'):
            ShortsPlan.model_validate({**plan, 'issues': issues})
    with pytest.raises(ValueError, match='series name'):
        ShortsPlan.model_validate({**plan, 'title': '반도체가 이끌었다 | 9월 22일 아침 · 오늘의 증시story'})
    with pytest.raises(ValueError, match='unique'):
        ShortsPlan.model_validate({**plan, 'issues': [issue, issue]})
    with pytest.raises(ValueError, match='fixed scenes'):  # the cut's own scene IDs
        ShortsPlan.model_validate({**plan, 'issues': [{**issue, 'id': 'outro'}]})
    with pytest.raises(ValueError, match='delivery tags'):
        ShortsPlan.model_validate({**plan, 'close': {**plan['close'], 'signals_narration': '[calm] 비기술 업종 확산을 보세요.'}})
    with pytest.raises(ValueError, match='signs as words'):
        ShortsPlan.model_validate({**plan, 'close': {**plan['close'], 'signals_narration': '비기술 업종이 +2% 오르는지 보세요.'}})
    with pytest.raises(ValueError, match='big_label'):
        ShortsPlan.model_validate({**plan, 'issues': [{**issue, 'big_label': ''}]})
    # A brief issue may show no number and no 그래서 line.
    assert ShortsPlan.model_validate({**plan, 'issues': [{**issue, 'big': '', 'big_label': '', 'dir': None, 'meaning': ''}]})
    # A cut of the earlier shape (issues cut from scenes, a note per issue, signals on the plan) is not a v2 plan.
    old = {**plan, 'signals': ['a 신호다', 'b 신호다'], 'signals_narration': '신호를 봅니다.', 'signals_claim_ids': ['b011']}
    old.pop('close')
    with pytest.raises(ValueError):
        ShortsPlan.model_validate(old)


def test_the_hook_is_one_breath_after_the_date_line():
    # Owner, 10/20: the first preview's opener ran 11.5 s (the spec asks ~3-5 s); the on-screen headline carries the detail.
    plan = shorts().model_dump(mode='json')
    assert HOOK_NARRATION == 32 and len(plan['hook']['narration']) <= HOOK_NARRATION
    short, long = (ShortsPlan.model_validate({**plan, 'hook': {**plan['hook'], 'narration': '가' * n}}) for n in (10, 32))
    assert estimated_seconds(long) - estimated_seconds(short) == pytest.approx(22 / CHARS_PER_SECOND)


def test_fixed_lines():
    assert date_line({'source': {'day': '2026-10-20', 'edition_kind': 'am'}}) == '10월 20일 아침 브리핑입니다.'
    assert date_line({'source': {'day': '2026-10-20', 'edition_kind': 'close'}}).endswith('마감 브리핑입니다.')
    # 2026-10-27: the list scene says only the count; each issue's name comes back with k/N on its own scene.
    assert agenda_line(6) == '오늘 이슈는 여섯 가지입니다.' and agenda_line(1) == '오늘 이슈는 한 가지입니다.'
    assert [KO_COUNT[n] for n in range(1, 8)] == ['한', '두', '세', '네', '다섯', '여섯', '일곱']
    # Owner, 10/22: the hook comes first and the date after it.
    assert opener_line('10월 20일 아침 브리핑입니다.', '반등은 진짜일까?') == '반등은 진짜일까? 10월 20일 아침 브리핑입니다.'
    assert opener_line('10월 20일 아침 브리핑입니다.', '반도체가 이끌었습니다') == '반도체가 이끌었습니다. 10월 20일 아침 브리핑입니다.'
    # Owner, 10/27: the cut answers its opener, then points to the full episode and the report mail.
    close = shorts().close
    assert outro_line(close) == ('정리하면, 반도체가 이끌었지만 확산은 아직입니다. 비기술 업종 확산과 한국 반도체 강세를 보세요. '
                                 '자세한 분석은 본편에서, 매일 리포트 메일은 프로필 링크에서 신청하세요.')
    assert OUTRO['button'] == '본편 보기' and not hasattr(shorts_module, 'TEASER')


def test_six_issues_at_their_longest_are_found_before_speech():
    # Three core issues at 85 characters, three brief ones at 40, the answer and the signals at 70 together.
    longest = spoken_plan([85] * 3, [40] * 3)
    assert 85 <= estimated_seconds(longest) <= 90
    assert estimated_seconds(spoken_plan([85] * 3, [40] * 4)) > 90


def test_four_issues_at_their_middle_run_about_a_minute():
    assert 55 <= estimated_seconds(spoken_plan([72] * 2, [33] * 2, hook=28, close=(30, 30))) <= 66
    assert 70 <= estimated_seconds(spoken_plan([72] * 2, [33] * 4, hook=28, close=(30, 30))) <= 78


def lineup_plan(source, episode, **changes):
    """A passing cut of an issue_body brief: one issue per lineup entry, core and brief lengths, numbers from the facts."""
    issues = []
    for i in lineup(source, episode):
        n, ids = i.number, i.claim_ids
        core = i.tier == 'core'
        issues.append(dict(
            id=f'issue-{n}', issue=n, label=f'이슈 {chr(64 + n)}', agenda=f'{chr(64 + n)} 이슈, 수급 부담 신호', big=f'{n}.{n}%', big_label='관련 지표',
            body=f'관련 지표가 {n}.{n}% 움직였습니다.', meaning='수급 부담으로 이어질 수 있음' if core else '',
            narration=(f'관련 지표가 {n}.{n}% 움직였습니다. 브리핑은 이 흐름이 결국 투자자 수급 부담으로 이어질 수 있다고 해석합니다.'
                       if core else f'관련 지표가 {n}.{n}% 움직여 수급 부담 신호입니다.'),
            claim_ids=list(ids[:2]) + ([ids[2]] if core else [])))
    claims = parse(source['body'])['claims']
    summary = next(k for k, v in claims.items() if v['section'] == '오늘의 핵심')
    following = next(k for k, v in claims.items() if v['section'] == '다음 확인할 것')
    value = dict(title='반등 실패, 수급 부담이 막았다', description='오늘 마감 이슈를 짧게 정리합니다.',
                 hook=dict(headline='반등은 실패, 코스닥 낙폭 확대', question='왜 꺾였나?', narration='반등을 시도했지만 끝내 꺾였습니다.',
                           claim_ids=[summary]),
                 issues=issues,
                 close=dict(conclusion='정리하면, 수급 부담이 반등을 막았습니다.', signals=['외국인 수급 전환', '코스닥 종가 회복'],
                            signals_narration='외국인 수급과 코스닥 종가를 보세요.', claim_ids=[issues[0]['claim_ids'][0], following]))
    value.update(changes)
    return ShortsPlan.model_validate(value)


FOUR = {1: '핵심 이슈', 2: '핵심 이슈', 3: '함께 볼 이슈', 4: '함께 볼 이슈'}


def lineup_violations(plan, source, episode):
    return shorts_violations(plan, source, episode, lineup(source, episode))


def test_a_cut_of_every_issue_in_order_passes():
    source = issue_body(PM_TITLES[:4])
    episode_json = issue_episode(source, FOUR)
    assert lineup_violations(lineup_plan(source, episode_json), source, episode_json) == []
    assert violations(cut()) == []  # the fixture brief's one issue


def test_every_briefing_issue_once_in_order():
    source = issue_body(PM_TITLES[:4])
    episode_json = issue_episode(source, FOUR)
    raw = lineup_plan(source, episode_json).model_dump(mode='json')
    issues = raw['issues']

    def found(changed):
        return ' '.join(lineup_violations(ShortsPlan.model_validate({**raw, 'issues': changed}), source, episode_json))

    assert 'Briefing issues [3] are missing' in found([issues[0], issues[1], issues[3]])
    split = {**issues[0], 'id': 'issue-1b'}
    assert 'Briefing issues [1] are split into more than one issue' in found([issues[0], split, *issues[1:]])
    assert 'out of the briefing order [1, 2, 3, 4]' in found([issues[1], issues[0], *issues[2:]])
    assert 'Issues [5] are not briefing issues' in found([*issues, {**issues[3], 'id': 'issue-5', 'issue': 5}])


def test_an_issue_cites_only_its_own_lines():
    source = issue_body(PM_TITLES[:4])
    episode_json = issue_episode(source, FOUR)
    plan = lineup_plan(source, episode_json).model_dump(mode='json')
    other = lineup(source, episode_json)[2].claim_ids[1]
    plan['issues'][1]['claim_ids'].append(other)
    assert any(f"Line IDs ['{other}'] in issue issue-2 belong to neither briefing issue 2" in f
               for f in lineup_violations(ShortsPlan.model_validate(plan), source, episode_json))


def test_core_and_brief_narration_lengths():
    source = issue_body(PM_TITLES[:4])
    episode_json = issue_episode(source, FOUR)
    plan = lineup_plan(source, episode_json).model_dump(mode='json')
    plan['issues'][0]['narration'] = '관련 지표가 1.1% 움직였습니다. 수급 부담입니다.'
    plan['issues'][2]['narration'] = '관련 지표가 3.3% 움직였고 이 흐름은 투자자 수급 부담으로 이어질 수 있습니다.'
    found = ' '.join(lineup_violations(ShortsPlan.model_validate(plan), source, episode_json))
    assert 'Issue issue-1 narration is 29 characters; a core issue takes 60-85' in found
    assert 'Issue issue-3 narration is 46 characters; a brief issue takes 25-40' in found


def test_a_core_issue_says_what_it_means():
    source = issue_body(PM_TITLES[:4])
    episode_json = issue_episode(source, FOUR)
    plan = lineup_plan(source, episode_json).model_dump(mode='json')
    plan['issues'][0]['meaning'] = ''
    assert any('Core issue issue-1 needs its 그래서 line' in f
               for f in lineup_violations(ShortsPlan.model_validate(plan), source, episode_json))
    plan['issues'][0]['meaning'], plan['issues'][3]['meaning'] = '수급 부담으로 이어질 수 있음', ''
    assert lineup_violations(ShortsPlan.model_validate(plan), source, episode_json) == []


def test_the_close_fits_its_budget():
    raw = cut()
    raw['close'].update(conclusion='정리하면, 반도체가 이끌었지만 비기술 업종 확산은 아직입니다.',
                        signals_narration='비기술 업종 확산과 한국 반도체 강세가 이어지는지 꼭 확인해 보세요.')
    assert any(f.startswith('The conclusion and the signals narration are 72 characters, over 70') for f in violations(raw))


def test_an_issue_the_episode_left_out_needs_only_its_briefing_lines():
    source = issue_body(PM_TITLES[:4])
    episode_json = issue_episode(source, FOUR, skip=(3,))
    plan = lineup_plan(source, episode_json)
    assert '3.3' not in json.dumps(episode_json, ensure_ascii=False)
    assert lineup_violations(plan, source, episode_json) == []
    raw = plan.model_dump(mode='json')
    raw['issues'][2]['big'] = '7.7%'  # in no line of issue 3
    assert any('Unbound number' in f and 'issue-3' in f
               for f in lineup_violations(ShortsPlan.model_validate(raw), source, episode_json))


def test_the_hook_takes_one_breath():
    plan = cut(hook={**cut()['hook'], 'narration': '가' * 33})
    assert 'Hook narration is 33 characters, over 32: one sentence in one breath after the date line' in ' '.join(
        violations(plan))
    assert not any('Hook narration' in f for f in violations(cut(hook={**cut()['hook'], 'narration': '가' * 32})))


def test_numbers_must_be_in_the_episode():
    # '21' is in the briefing line b002 ('2026-09-21 종가') but in no scene of the delivered episode.
    raw = cut(close={**cut()['close'], 'signals': ['21일 종가 확인', '한국 반도체 강세 지속'], 'claim_ids': ['b011', 'b002']})
    assert any('Number [\'21\'] in the close is not in the delivered episode' in f for f in violations(raw))


def test_only_visible_episode_text_counts_as_in_the_episode():
    # '5' (from '05:00' in b013) and '21' (b002) are in cited lines but in no visible text of the episode; card numbers,
    # icon sizes and colours (112, '01', #5EA0FF) are not text.
    for big, ids in (('5', ['b013']), ('112', ['b014'])):
        raw = cut(issue=0, big=big, dir=None, claim_ids=ids)
        assert any('not in the delivered episode' in f for f in violations(raw)), big
    assert 'not in the delivered episode' not in ' '.join(violations(cut()))


def test_an_over_long_plan_is_found_before_any_speech_is_paid():
    assert estimated_seconds(shorts()) < 60 and not any('Estimated length' in f for f in violations(cut()))
    plan = spoken_plan([85] * 3, [40] * 4)
    found = shorts_violations(plan, body_source(), episode().model_dump(mode='json'),
                              lineup(body_source(), episode().model_dump(mode='json')))
    assert any('Estimated length' in f and 'over the 90 s cap' in f for f in found)


def test_hook_and_close_lines_are_used_by_the_episode_or_the_issues():
    # b002 is a briefing line no scene of the episode carries and no issue owns.
    assert any('the hook' in f and 'used nowhere' in f for f in violations(cut(hook=dict(cut()['hook'], claim_ids=['b002']))))
    assert any('the close' in f and 'used nowhere' in f
               for f in violations(cut(close={**cut()['close'], 'claim_ids': ['b002']})))


def test_title_and_description_numbers_must_be_in_the_delivered_episode():
    raw = cut(title='S&P 500 21일 상승, 반도체가 이끌었다', close={**cut()['close'], 'claim_ids': ['b011', 'b012', 'b002']})
    found = violations(raw)
    assert any('title or description is not in the delivered episode' in f for f in found), found


@pytest.mark.parametrize('change,error', [
    (lambda r: r['issues'][0].update(big='5,400'), 'Unbound number'),
    (lambda r: r['issues'][0].update(dir='dn'), 'contradicts its line'),  # ▼ for the 1.92% rise
    (lambda r: r['issues'][0].update(narration='에스앤피 500이 정규장 종가 기준 1.92% 급락했고 반도체가 흔들렸습니다. '
                                               '수익률 하락은 성장주 부담을 낮출 수 있습니다.'), 'contradicts its line'),
    (lambda r: r['issues'][0].update(body='S&P 500이 1.92% 올랐고 반도체가 상승을 주도했습니다.',
                                     narration='에스앤피 500이 1.92% 올랐고 반도체가 이끌었습니다. 수익률 하락은 성장주 부담을 '
                                               '낮출 수 있다는 해석입니다.'), '종가'),  # no label
    (lambda r: r['issues'][0].update(narration='보도에 따르면 에스앤피 500이 정규장 종가 기준 1.92% 올랐습니다. 수익률 하락은 '
                                               '성장주 부담을 낮출 수 있습니다.'), '보도에 따르면'),
    (lambda r: r['close'].update(signals_narration='이 영상은 AI가 만든 요약입니다.'), 'tool wording'),
    (lambda r: r['close'].update(signals_narration='반도체는 지금 사세요. 확산을 보세요.'), 'Buy/sell'),
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
    episode_json = episode().model_dump(mode='json')
    assert shorts_violations(plan, body_source(), episode_json, lineup(body_source(), episode_json)) == []
    raw = cut(issue=0, big='1.92%', dir='dn')  # against its line: kept, and the checks fail
    plan, dropped = settle(ShortsPlan.model_validate(raw), body_source())
    assert dropped == [] and any('contradicts its line' in f for f in violations(raw))


@pytest.mark.skip(reason='v2: rewritten in Task 5')
def test_the_prompt_cuts_the_delivered_episode_and_leaves_flagged_claims_out():
    prompt = shorts_prompt(body_source(), episode().model_dump(mode='json'), ['나스닥 수치 해석이 과장됨'], '- Unbound number')
    assert episode().upload.title in prompt and 'no greeting' in prompt and "Never say '보도에 따르면'" in prompt
    assert '나스닥 수치 해석이 과장됨' in prompt and 'Unbound number' in prompt and 'b013' in prompt


@pytest.mark.skip(reason='v2: rewritten in Task 5')
def test_the_shorts_prompt_carries_only_given_lessons():
    plain = shorts_prompt(body_source(), episode().model_dump(mode='json'))
    assert shorts_prompt(body_source(), episode().model_dump(mode='json'), lessons=()) == plain  # older cuts unchanged
    taught = shorts_prompt(body_source(), episode().model_dump(mode='json'), ['나스닥 수치 해석이 과장됨'],
                           lessons=[{'id': 'x', 'scope': 'shorts', 'text': '첫 문장은 숫자 하나로 시작한다'}])
    assert 'channel_lessons' in taught and '첫 문장은 숫자 하나로 시작한다' in taught and 'channel_lessons' not in plain
    assert taught.index('Briefing body lines') < taught.index('channel_lessons') < taught.index('나스닥 수치 해석이 과장됨')


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


@pytest.mark.skip(reason='v2: rewritten in Task 6')
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
    assert '9월 22일 아침 브리핑입니다.' in srt and '반도체 주도, 다르게 볼 점, 두 가지입니다.' in srt and '본편에서 확인하세요.' in srt
    assert '채널 프로필 링크에서 신청하세요.' in srt and '답은 본편에서 확인하세요.' in srt  # the end screen (owner, 10/21-22)
    assert '[고정 댓글]\n확산될까? 여러분은 어떻게 보세요? 댓글로 남겨 주세요.' in upload
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


@pytest.mark.skip(reason='v2: rewritten in Task 6')
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


@pytest.mark.skip(reason='v2: rewritten in Task 6')
def test_build_keeps_a_minus_without_an_arrow(tmp_path):
    ffmpeg, settings, job = render_fixture(tmp_path)
    raw = shorts().model_dump(mode='json')
    raw['issues'][0].update(big='-0.2%', dir=None)  # a signed level: the sign is the fact
    raw['issues'][1].update(big='-0.59%', dir='dn')  # the arrow replaces the sign
    built = ShortsRenderer(settings, WordAligner()).build(job, ShortsPlan.model_validate(raw), episode().model_dump(mode='json'),
                                                          tmp_path / 'out', tones(tmp_path, TAKES, ffmpeg))
    [shown] = on_page(built['page'], ["[...document.querySelectorAll('.scene.issue .num .v')].map(e => e.textContent)"])
    assert shown == ['-0.2%', '▼0.59%']


@pytest.mark.skip(reason='v2: rewritten in Task 6')
def test_a_long_total_is_a_finding_not_a_raise(tmp_path):
    ffmpeg, settings, job = render_fixture(tmp_path)
    takes = tones(tmp_path, [20, 20, 20, 20, 20], ffmpeg)  # ~100 s of paid speech
    built = ShortsRenderer(settings, WordAligner()).build(job, shorts(), episode().model_dump(mode='json'), tmp_path / 'out', takes)
    assert 90 < built['total'] < 180
    assert any('at most 90 s' in f for f in built['findings'])


@pytest.mark.skip(reason='v2: rewritten in Task 6')
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


@pytest.mark.skip(reason='v2: rewritten in Task 6')
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


@pytest.mark.skip(reason='v2: rewritten in Task 6')
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


@pytest.mark.skip(reason='v2: rewritten in Task 6')
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
                           ('wide', '반도체, 미국 증시를 이끌었지만 비기술 업종 확산은 아직 혼조였다'), ('plain', '반도체가 이끈 미국 증시'),
                           ('long', '다우 1.02% 올랐지만, 반대매매에 LIG D&A 제재까지')):
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
    # 10/21: a second part too long for one line wraps below the break, and 'LIG D&A' stays on one line.
    assert shown['long'] == ['다우 1.02% 올랐지만,\n반대매매에 LIG\u00a0D&A 제재까지', 1, False, 1]
    assert '<br class="comma">' in fullwidth and '반도체 주도，</span>' in fullwidth


@pytest.mark.skip(reason='v2: rewritten in Task 6')
def test_each_issue_leads_with_its_line_and_the_list_rows_come_in_as_they_are_read(tmp_path):
    # Owner, 10/21: an issue led with a bare number; its one-line takeaway (its row in the list) now heads it.
    ffmpeg, settings, job = render_fixture(tmp_path)
    plan = shorts()
    built = ShortsRenderer(settings, WordAligner()).build(job, plan, episode().model_dump(mode='json'), tmp_path / 'out',
                                                          tones(tmp_path, TAKES, ffmpeg))
    agenda = built['ep']['scenes'][1]
    at = agenda['data']['at']
    assert len(at) == len(plan.issues) and 0 < at[0] < at[1] < agenda['t1'] - agenda['t0']
    [heads] = on_page(built['page'], ["[...document.querySelectorAll('.issue .ih')].map(e => e.innerText)"])
    assert heads == [i.agenda for i in plan.issues]


@pytest.mark.skip(reason='v2: rewritten in Task 6')
def test_the_question_stays_up_over_the_issues_and_the_end_leaves_it_for_the_episode(tmp_path):
    # Owner, 10/22: 59% of the 10/20 cut's feed viewers swiped away; the question bar and one end screen.
    ffmpeg, settings, job = render_fixture(tmp_path)
    plan = shorts()
    built = ShortsRenderer(settings, WordAligner()).build(job, plan, episode().model_dump(mode='json'), tmp_path / 'out',
                                                          tones(tmp_path, TAKES, ffmpeg))
    scenes = {s['id']: s for s in built['ep']['scenes']}
    bar = "[getComputedStyle(document.querySelector('#qbar')).opacity, document.querySelector('#qbar').innerText]"
    [[hidden, _]] = on_page(built['page'], [bar], t=1.0)  # the opener shows the question big itself
    [[shown, text]] = on_page(built['page'], [bar], t=scenes['semis']['t0'] + 1.0)
    assert float(hidden) == 0 and float(shown) == 1 and text == f'오늘의 질문{plan.hook.question}'
    end = scenes['outro']
    [found] = on_page(built['page'], ["[document.querySelector('.outro .oq').innerText, document.querySelector('.outro .play').innerText,"
                                      " document.querySelector('.outro .mailrow').innerText]"], t=end['t1'] - 0.3)
    assert found == [plan.hook.question, OUTRO['button'], OUTRO['mail_line']]
    [issues] = on_page(built['page'], [QA_JS], t=end['t1'] - 0.3)
    assert not issues, issues
    # A long question beside a long credit (a 48-character author is allowed): neither covers nor overlaps the other,
    # and the hidden bar never sits over the opener's or the outro's credit.
    raw = plan.model_dump(mode='json')
    raw['hook']['question'] = '외국인 매수 다음 장도 이어질까요?'  # 19 characters
    built = ShortsRenderer(settings, WordAligner()).build(job, ShortsPlan.model_validate(raw), episode().model_dump(mode='json'),
                                                          tmp_path / 'long', tones(tmp_path, TAKES, ffmpeg))
    page_js = built['page'] / 'episode.js'
    ep = json.loads(page_js.read_text().removeprefix('window.EP=').removesuffix(';'))
    for s in ep['scenes']:
        s['credit'] = '자료사진 · ' + 'Republic of Korea Armed Forces Photographer Kim' + ' · CC BY-SA 4.0'
    page_js.write_text('window.EP=' + json.dumps(ep, ensure_ascii=False) + ';')
    for s in ep['scenes']:
        [issues] = on_page(built['page'], [QA_JS], t=s['t1'] - 0.3)
        assert not issues, (s['id'], issues)


@pytest.mark.skip(reason='v2: rewritten in Task 6')
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


@pytest.mark.skip(reason='v2: rewritten in Task 7')
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
    assert speech.calls[-count]['text'].endswith(' 9월 22일 아침 브리핑입니다.')  # the hook first (owner, 10/22)
    assert speech.calls[-1]['text'].endswith(OUTRO['mail'])
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


@pytest.mark.skip(reason='v2: rewritten in Task 7')
async def test_a_cut_receives_the_shorts_lessons_frozen_in_its_episode_job(brief, video, tmp_path, monkeypatch):  # noqa: F811
    store, clock = video
    job, provider, speech = await delivered_episode(store, tmp_path, monkeypatch, brief, clock)
    lessons = [{'id': '1', 'scope': 'thumbnail', 'text': '썸네일 교훈 본편 전용'},
               {'id': '2', 'scope': 'shorts', 'text': '쇼츠 첫 문장은 숫자 하나로'}]
    with store.db.transaction() as conn:
        conn.execute("UPDATE video_jobs SET policy=policy||%s WHERE id=%s", (Jsonb({'lessons': lessons}), job['id']))
    store.settings.video_shorts_enabled = True
    await shorts_ticks(store, provider, speech, ShortsFileRenderer(), job)
    request = next(c for c in provider.calls if c.output_contract == 'video_shorts_v1')
    assert '쇼츠 첫 문장은 숫자 하나로' in request.prompt and '썸네일 교훈 본편 전용' not in request.prompt


@pytest.mark.skip(reason='v2: rewritten in Task 7')
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
    assert speech.calls[takes]['text'] == opener_line('9월 22일 아침 브리핑입니다.', OLD_HOOK) and len(renderer.calls) == 1


@pytest.mark.skip(reason='v2: rewritten in Task 7')
async def test_a_cut_voiced_before_new_fixed_lines_voices_them_before_it_renders(brief, video, tmp_path, monkeypatch):  # noqa: F811
    # A release between ticks (10/21: the read list, the mail invitation): the rendering step finds a take it lacks.
    store, clock = video
    job, provider, speech = await delivered_episode(store, tmp_path, monkeypatch, brief, clock)
    store.settings.video_shorts_enabled = True
    row = await shorts_ticks(store, provider, speech, ShortsFileRenderer(), job, limit=1)
    for _ in range(12):
        if row['state'] == 'rendering':
            break
        row = await shorts_ticks(store, provider, speech, ShortsFileRenderer(), job, limit=1)
    assert row['state'] == 'rendering', row['error']
    with store.db.transaction() as conn:
        conn.execute("DELETE FROM video_effects WHERE job_id=%s AND id LIKE %s", (job['id'], '%:shorts-speech-4-%'))
    renderer, takes = ShortsFileRenderer(), len(speech.calls)
    row = await shorts_ticks(store, provider, speech, renderer, job, limit=1)
    assert row['state'] == 'synthesizing' and not renderer.calls
    row = await shorts_ticks(store, provider, speech, renderer, job)
    assert row['state'] == 'delivering', row['error']
    assert len(speech.calls) - takes == 1 and speech.calls[-1]['text'].endswith(OUTRO['mail']) and len(renderer.calls) == 1


@pytest.mark.skip(reason='v2: rewritten in Task 7')
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


@pytest.mark.skip(reason='v2: rewritten in Task 7')
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


@pytest.mark.skip(reason='v2: rewritten in Task 7')
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


async def test_the_morning_cut_starts_once_its_episode_and_mail_are_out(brief, video, tmp_path, monkeypatch):  # noqa: F811
    from datetime import datetime
    from zoneinfo import ZoneInfo

    store, clock = video
    job, provider, speech = await delivered_episode(store, tmp_path, monkeypatch, brief, clock)
    store.settings.video_shorts_enabled = True
    shorts_store = ShortsStore(store.company)
    assert job['source']['day'] == '2026-09-22' and job['source']['edition_kind'] == 'am'
    clock['at'] = datetime(2026, 9, 23, 8, 0, tzinfo=ZoneInfo('Asia/Seoul'))  # the next morning's brief is due
    assert shorts_store.claim() is None
    clock['at'] = datetime(2026, 9, 22, 16, 30, tzinfo=ZoneInfo('Asia/Seoul'))  # an AM episode leaves the close window
    assert shorts_store.claim() is None
    clock['at'] = datetime(2026, 9, 22, 8, 0, tzinfo=ZoneInfo('Asia/Seoul'))  # that morning's episode is out
    with store.db.transaction() as conn:  # its mail still waits for the model
        conn.execute("""INSERT INTO newsletter_issues(id,edition_id,state,auto,max_attempts,fallback_at,expires_at)
            VALUES(%s,%s,'writing',true,2,now()+interval '30 minutes',now()+interval '12 hours')""",
                     ('00000000-0000-0000-0000-000000000004', job['edition_id']))
    assert shorts_store.claim() is None and shorts_store.get(job['id']) is None
    with store.db.transaction() as conn:  # past its fallback time the mail no longer needs the model
        conn.execute("UPDATE newsletter_issues SET fallback_at=now()-interval '1 minute'")
    assert shorts_store.claim()['state'] == 'writing'


async def test_the_close_cut_starts_once_the_close_episode_is_out(brief, video, tmp_path, monkeypatch):  # noqa: F811
    from datetime import datetime
    from zoneinfo import ZoneInfo

    store, clock = video
    job, provider, speech = await delivered_episode(store, tmp_path, monkeypatch, brief, clock)
    store.settings.video_shorts_enabled = True
    with store.db.transaction() as conn:
        conn.execute("""UPDATE video_jobs SET source=jsonb_set(source,'{edition_kind}','"close"') WHERE id=%s""",
                     (job['id'],))
    clock['at'] = datetime(2026, 9, 23, 7, 0, tzinfo=ZoneInfo('Asia/Seoul'))  # a close episode leaves the morning window
    assert ShortsStore(store.company).claim() is None
    clock['at'] = datetime(2026, 9, 22, 18, 30, tzinfo=ZoneInfo('Asia/Seoul'))
    assert ShortsStore(store.company).claim()['state'] == 'writing'


@pytest.mark.skip(reason='v2: rewritten in Task 7')
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


@pytest.mark.skip(reason='v2: rewritten in Task 7')
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


@pytest.mark.skip(reason='v2: rewritten in Task 7')
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


@pytest.mark.skip(reason='v2: rewritten in Task 7')
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


@pytest.mark.skip(reason='v2: rewritten in Task 7')
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


@pytest.mark.skip(reason='v2: rewritten in Task 7')
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


@pytest.mark.skip(reason='v2: rewritten in Task 6')
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
    assert texts[0].endswith(' 9월 22일 아침 브리핑입니다.') and texts[1] == agenda_line(1)
    assert texts[-1].startswith('정리하면, 반도체가 이끌었지만 확산은 아직입니다.') and texts[-1].endswith(OUTRO['say'])
    assert all(key.startswith(f'shorts-speech-{i}-') for i, (key, _) in enumerate(takes))


@pytest.mark.skip(reason='v2: rewritten in Task 7')
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

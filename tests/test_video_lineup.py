"""The Shorts issue lineup: the briefing's numbered issues, the episode scenes that covered each and its depth. The
multi-issue briefing and episode below are also the fixtures of the Shorts tests."""

import copy
import json

from quant_company.video.body import parse
from quant_company.video.lineup import CORE_MAX, briefing_issues, coverage_findings, lineup, uncovered

from .test_briefing import brief  # noqa: F401
from .test_video import video  # noqa: F401

PM_TITLES = ['장 초반 반등 실패, 코스닥 낙폭 확대', '유가 조정에도 공급 위험은 남았다', 'AI 이익 성장과 투자재원',
             '중소기업 수출 최대, 자동차는 역행', 'LIG 급락·루닛 상한가', '반도체 호조가 청년 채용으로 안 이어져']


def issue_body(titles, lines_per_issue=4, detail=False):
    """A delivered brief body in the rendered Slack form with one numbered issue per title. Issue n's fact line carries
    '{n}.{n}%' and its signal line '{n}0억원', so number checks have something to bind; with detail, a thread part
    after the issues holds more lines that belong to no issue."""
    kinds = [lambda n, t: f'{t}: 관련 지표가 {n}.{n}% 움직였습니다.',
             lambda n, t: f'*Analyst 해석* · {t}은 투자자 수급 부담으로 이어질 수 있습니다.',
             lambda n, t: f'*다르게 볼 점* · {t}은 일시적 요인일 수 있습니다.',
             lambda n, t: f'*확인할 신호* · 다음 장에서 {n}0억원 규모의 순매수가 나오면 우려가 약해집니다.']
    lines = ['*10/26 마감 브리핑 · 오늘 시장*', '자료 기준 10/26 16:10 KST', '', '*오늘의 핵심* · 30초 요약', '',
             '• 코스피는 1.46% 내린 5,469.47로 마감했습니다.', '', '*주요 이슈*', '']
    for n, title in enumerate(titles, 1):
        lines += [f'*{n}. {title}* · 수일~수주', '']
        for kind in kinds[:lines_per_issue]:
            lines += [kind(n, title), '']
    lines += ['*다음 확인할 것*', '', '• 다음 한국장에서 외국인 현물 수급을 확인합니다.']
    parts = ['\n'.join(lines)]
    if detail:
        parts.append('*상세 계속*\n• 상세 줄: 거래대금은 18조원이었습니다.\n• 상세 줄: 신용잔고는 2.1% 늘었습니다.')
    return {'body': parts, 'day': '2026-10-26', 'cutoff': '2026-10-26T16:10:00+09:00', 'edition_kind': 'close'}


def issue_episode(source, chapters, skip=()):
    """The delivered EpisodePlan (as JSON) of an issue_body brief: a cold open and summary citing issue 1, one headline
    scene per issue in its chapter (plus a photo scene with its interpretation for a 핵심 이슈 one), the calendar and
    the closing signals. skip leaves those issue numbers out of the issue chapters."""
    from .test_video_motion import episode

    claims = parse(source['body'])['claims']
    base = {s['id']: s for s in episode().model_dump(mode='json')['scenes']}
    issues = briefing_issues(source)
    summary = next(k for k, v in claims.items() if v['section'] == '오늘의 핵심')
    following = next(k for k, v in claims.items() if v['section'] == '다음 확인할 것')
    scenes = [{**copy.deepcopy(base['hook']), 'claim_ids': [summary, issues[0][2][0]]},
              {**copy.deepcopy(base['summary']), 'claim_ids': [summary, issues[0][2][1]]}]
    for number, title, ids in issues:
        if number in skip:
            continue
        chapter = chapters[number]
        head = copy.deepcopy(base['head'])
        head.update(id=f'issue-{number}', chapter=chapter, claim_ids=list(ids[:2]), narration=claims[ids[1]]['text'])
        head['data'].update(chips=[f'{chapter} {number}', '수일~수주'], title=title[:30])
        scenes.append(head)
        if chapter == '핵심 이슈' and len(ids) > 3:
            view = copy.deepcopy(base['photo'])
            view.update(id=f'issue-{number}-view', chapter=chapter, claim_ids=list(ids[2:4]),
                        narration=claims[ids[2]]['text'].split(' · ', 1)[-1])
            view['data'].update(chips=[f'{chapter} {number}'], title='다르게 볼 점')
            scenes.append(view)
    scenes += [{**copy.deepcopy(base['calendar']), 'claim_ids': [following]},
               {**copy.deepcopy(base['closing']), 'claim_ids': [issues[0][2][-1], following]}]
    return episode(scenes=scenes).model_dump(mode='json')


def test_the_lineup_is_the_briefings_numbered_issues_in_order():
    issues = briefing_issues(issue_body(PM_TITLES))
    assert [n for n, _, _ in issues] == [1, 2, 3, 4, 5, 6]
    assert issues[0][1] == '장 초반 반등 실패, 코스닥 낙폭 확대'
    assert all(len(ids) == 5 for _, _, ids in issues)  # the heading line and its four lines
    claims = parse(issue_body(PM_TITLES)['body'])['claims']
    assert claims[issues[2][2][0]]['text'] == 'AI 이익 성장과 투자재원 · 수일~수주'


def test_scenes_and_tiers_follow_the_episode_chapters():
    source = issue_body(PM_TITLES[:4])
    found = lineup(source, issue_episode(source, {1: '핵심 이슈', 2: '핵심 이슈', 3: '함께 볼 이슈', 4: '함께 볼 이슈'}))
    assert [i.tier for i in found] == ['core', 'core', 'brief', 'brief']
    assert [i.scene_ids for i in found] == [('issue-1', 'issue-1-view'), ('issue-2', 'issue-2-view'), ('issue-3',),
                                            ('issue-4',)]


def test_no_more_than_three_core_issues():
    source = issue_body(PM_TITLES[:5])
    found = lineup(source, issue_episode(source, {n: '핵심 이슈' for n in range(1, 6)}))
    assert CORE_MAX == 3 and [i.tier for i in found] == ['core', 'core', 'core', 'brief', 'brief']


def test_an_uncovered_issue_is_reported_and_brief():
    source = issue_body(PM_TITLES[:3])
    found = lineup(source, issue_episode(source, {1: '핵심 이슈', 2: '함께 볼 이슈', 3: '함께 볼 이슈'}, skip=(2,)))
    assert [i.number for i in uncovered(found)] == [2]
    assert found[1].tier == 'brief' and found[1].scene_ids == ()


def test_only_issue_chapter_scenes_cover_an_issue():
    source = issue_body(PM_TITLES[:2])
    episode = issue_episode(source, {1: '함께 볼 이슈', 2: '함께 볼 이슈'}, skip=(1,))
    # The cold open, the summary and the closing all cite issue 1 lines; none of them covers it.
    assert lineup(source, episode)[0].scene_ids == ()


def test_a_scene_citing_two_issues_covers_both():
    source = issue_body(PM_TITLES[:2])
    episode = issue_episode(source, {1: '핵심 이슈', 2: '함께 볼 이슈'})
    issues = briefing_issues(source)
    episode['scenes'][2]['claim_ids'].append(issues[1][2][1])  # issue-1's headline also cites issue 2's fact
    found = lineup(source, episode)
    assert found[1].scene_ids == ('issue-1', 'issue-2') and found[1].tier == 'core'


def test_lines_outside_numbered_issues_belong_to_no_issue():
    source = issue_body(PM_TITLES[:2], detail=True)
    claims = parse(source['body'])['claims']
    owned = {c for _, _, ids in briefing_issues(source) for c in ids}
    outside = {k for k, v in claims.items() if v['section'] in {'오늘의 핵심', '다음 확인할 것', '상세 계속'}}
    assert outside and not owned & outside
    assert {claims[c]['section'] for c in owned} == {'주요 이슈'}


def test_a_briefing_without_numbered_issues_has_no_lineup():
    source = issue_body([])
    assert briefing_issues(source) == [] and lineup(source, {'scenes': []}) == []


def test_the_fixture_brief_has_one_issue():
    from .test_video import body_source
    from .test_video_motion import episode

    found = lineup(body_source(), episode().model_dump(mode='json'))
    assert [(i.number, i.tier) for i in found] == [(1, 'core')]
    assert found[0].title == '반도체 주도와 제한된 확산'
    assert found[0].scene_ids == ('head', 'flow', 'photo', 'counter')


def test_coverage_findings_name_each_uncovered_issue():
    source = issue_body(PM_TITLES[:3])
    episode = issue_episode(source, {1: '핵심 이슈', 2: '함께 볼 이슈', 3: '함께 볼 이슈'}, skip=(2, 3))
    assert coverage_findings(source, episode) == [
        f"Briefing issue {n} ({PM_TITLES[n - 1]}) has no scene in 핵심 이슈 or 함께 볼 이슈: give it at least one issue scene"
        for n in (2, 3)]
    assert coverage_findings(source, issue_episode(source, {1: '핵심 이슈', 2: '함께 볼 이슈', 3: '함께 볼 이슈'})) == []


def test_a_facts_edition_has_no_coverage_finding():
    source = {**issue_body(PM_TITLES[:2]), 'format': 'facts'}
    assert coverage_findings(source, {'scenes': []}) == []
    assert coverage_findings(issue_body([]), {'scenes': []}) == []


async def test_an_episode_that_skips_a_briefing_issue_is_asked_to_cover_it(brief, video, tmp_path, monkeypatch):  # noqa: F811
    from quant_company.video.contracts import VisualReview

    from .test_video_motion import InspectedRenderer, InspectingProvider, episode, produce_inspected

    store, _ = video
    provider = InspectingProvider(visual=[VisualReview(passed=True, issues=[])])
    # The fixture brief's one issue (lines b006-b011) is cited by no issue-chapter scene of the first version.
    skipping = episode().model_dump(mode='json')
    for scene in skipping['scenes']:
        scene['claim_ids'] = {'head': ['b013'], 'flow': ['b005'], 'photo': ['b012'], 'counter': ['b014']}.get(
            scene['id'], scene['claim_ids'])
    provider.queues['video_episode_v1'] = [type(episode()).model_validate(skipping), episode()]
    job, _ = await produce_inspected(store, tmp_path, monkeypatch, provider, InspectedRenderer(), brief)
    attempts = job['qa']['plan_attempts']
    finding = ('Briefing issue 1 (반도체 주도와 제한된 확산) has no scene in 핵심 이슈 or 함께 볼 이슈: '
               'give it at least one issue scene')
    assert attempts[0]['outcome'] == 'check_failed' and finding in attempts[0]['findings']
    assert attempts[1]['outcome'] == 'passed', attempts[1]
    second = [c for c in provider.calls if c.output_contract == 'video_episode_v1'][1].prompt
    assert finding in json.loads(second.rsplit('\n', 1)[1])['revision_feedback']
    assert job['state'] == 'uploading', job['error']

"""The Shorts review, visual inspection and screen-fix prompts (video/shorts_review.py): what the max-effort editors
are asked, and the rule that a screen fix never changes what is said."""

import json

from quant_company.video.contracts import OWNER_DESIGN_RULES
from quant_company.video.lineup import lineup
from quant_company.video.shorts import ShortsPlan
from quant_company.video.shorts_review import fix_prompt, keep_spoken, review_prompt, visual_prompt

from .test_video import body_source
from .test_video_motion import episode
from .test_video_shorts import shorts


def inputs():
    source, episode_json = body_source(), episode().model_dump(mode='json')
    return source, episode_json, lineup(source, episode_json)


def test_review_prompt_names_the_five_checks_and_the_episode_concerns():
    source, episode_json, issues = inputs()
    prompt = review_prompt(source, episode_json, issues, shorts(), ['코스닥 기록을 코스피에 붙임'], 61.4)
    for check in ('facts', 'numbers', 'conditions', 'coverage', 'readability'):
        assert f'{check}:' in prompt, check
    assert '코스닥 기록을 코스피에 붙임' in prompt and '61 s' in prompt and 'Korean' in prompt
    assert 'answered inside the Shorts' in prompt and 'hedge' in prompt and 'soften' in prompt
    data = json.loads(prompt.rsplit('\n', 1)[1])
    assert data['shorts'] == shorts().model_dump(mode='json') and data['lineup'][0]['issue'] == 1
    assert 'b013' in json.dumps(data['lines'])
    # The reviewer sees the episode as the writer did: its closing signals carry the outlook the Shorts repeats.
    assert data['episode']['closing_signals'][0]['narration'] == episode().scenes[-1].narration
    assert data['episode']['thumbnail_stat'] == episode().thumbnail_stat


def test_visual_prompt_lists_the_frames_and_keeps_the_owner_rules():
    prompt = visual_prompt(body_source(), shorts(), [('opener', 'image 1'), ('semis', 'image 2')],
                           {'semis': 'heard 1.92 as 192'})
    assert '1=opener, 2=semis' in prompt.replace('image ', '') and OWNER_DESIGN_RULES in prompt
    assert 'heard 1.92 as 192' in prompt and 'audio_text' in prompt and 'top ~220 px' in prompt


def test_fix_prompt_lists_the_screen_issues():
    source, episode_json, issues = inputs()
    screen = [{'scene': 'semis', 'kind': 'clipped_text', 'detail': '본문이 잘림', 'fix': '본문을 줄임'}]
    prompt = fix_prompt(source, episode_json, issues, shorts(), screen)
    assert '본문이 잘림' in prompt and 'Keep every spoken field' in prompt and 'narration' in prompt


def test_keep_spoken_restores_every_spoken_field():
    original = shorts()
    raw = original.model_dump(mode='json')
    raw['hook'].update(narration='다르게 말한 훅입니다.', headline='화면 제목만 바뀜')
    raw['issues'][0].update(narration='다르게 말한 이슈 내레이션입니다. 길이는 상관없습니다.', body='화면 본문만 바뀝니다.',
                            issue=2, claim_ids=['b001'])
    raw['close'].update(conclusion='정리하면, 다른 결론입니다.', signals_narration='다른 신호 내레이션입니다.',
                        signals=['화면 신호 하나', '화면 신호 둘'])
    kept = keep_spoken(original, ShortsPlan.model_validate(raw))
    assert kept.hook.narration == original.hook.narration and kept.hook.headline == '화면 제목만 바뀜'
    assert kept.issues[0].narration == original.issues[0].narration and kept.issues[0].body == '화면 본문만 바뀝니다.'
    assert (kept.issues[0].issue, kept.issues[0].id, kept.issues[0].claim_ids) == (1, 'semis', original.issues[0].claim_ids)
    assert kept.close.conclusion == original.close.conclusion
    assert kept.close.signals_narration == original.close.signals_narration and kept.close.signals == ['화면 신호 하나', '화면 신호 둘']
    assert kept.narrations() == original.narrations()


def test_keep_spoken_matches_issues_by_id():
    from .test_video_lineup import PM_TITLES, issue_body, issue_episode
    from .test_video_shorts import lineup_plan

    source = issue_body(PM_TITLES[:2])
    original = lineup_plan(source, issue_episode(source, {1: '핵심 이슈', 2: '함께 볼 이슈'}))
    raw = original.model_dump(mode='json')
    raw['issues'].reverse()  # a reordered "fix" would pair one issue's screen with the other's narration
    assert keep_spoken(original, ShortsPlan.model_validate(raw)) == original

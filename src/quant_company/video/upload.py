"""증시story upload copy (docs/DAILY_BRIEF_DESIGN_SPEC.md §15): fixed title prefix, description template,
chapters from the rendered timeline, references from the delivered brief body's source list, bounded tags."""

import re
from typing import Literal

from pydantic import Field, field_validator

from ..contracts import StrictModel

# Owner decision 2026-10-10: the series name moves to the end of the title.
#   PM: '<key number/stock + question or next-session point> | <M월 D일> 마감 · 오늘의 증시story'
#   AM: '<one overnight number + one of today's events> | <M월 D일> 아침 · 오늘의 증시story'
SERIES = '오늘의 증시story'
EDITION_WORD = {'am': '아침', 'close': '마감'}
SUFFIX_MAX = len(' | 12월 31일 마감 · ' + SERIES)
SUFFIX = re.compile(r' \| (\d{1,2})월 (\d{1,2})일 (아침|마감) · ' + re.escape(SERIES) + '$')
# No buy/sell instruction anywhere in the copy.
INSTRUCTION = re.compile(r'매수하세요|매도하세요|사세요|파세요|사야 (?:할|합)|팔아야|매수 추천|매도 추천|지금 사|담으세요|담아야|손절하|강력 매수|강력 매도')
CLOSING = ('──────────\n\n'
           '오늘 시장에 무슨 일이 있었는지,\n'
           '어떤 소식이 주가를 움직였는지.\n'
           '증시story에서 그 앞뒤 이야기를 함께 짚어봅니다.\n\n'
           '궁금한 시장 이야기나 다음에 다뤘으면 하는 주제가 있다면 댓글로 남겨주세요.')
SIGNATURE = '세상의 뭐든, 이야기로 만나다. 뭐든story.'
BASE_TAGS = ('#증시story', '#뭐든story')
FIXED_TAGS = ('증시story', '뭐든story')
TIMING = {'am': '장전', 'intraday': '장중', 'close': '마감'}
# Production/tool wording never appears on screen, in narration or in the upload copy (owner rule). Market topics
# such as "AI 반도체" stay allowed; only phrases about how the episode was made are rejected.
TOOL_WORDING = re.compile(r'AI\s*(?:시장분석|분석|해설|앵커|음성|내레이션|생성|요약|가 만든|로 만든|가 작성|가 정리)'
                          r'|인공지능이 (?:만든|작성|정리)|Analyst|Claude|Codex|Runway|ElevenLabs|클로드|런웨이|일레븐랩스'
                          r'|자동 생성|자동으로 생성|생성형|검토용 샘플')


class UploadCopy(StrictModel):
    """Model-written parts only; layout, fixed lines, chapters and references are filled by the service."""
    title: str = Field(min_length=5, max_length=100-SUFFIX_MAX)
    lead: str = Field(min_length=10, max_length=160)
    intro: str = Field(min_length=10, max_length=320)
    market: Literal['국내', '미국', '글로벌']
    stories: list[str] = Field(min_length=3, max_length=3)
    hashtags: list[str] = Field(min_length=1, max_length=2)
    tags: list[str] = Field(min_length=13, max_length=23)

    @field_validator('title')
    @classmethod
    def hook(cls, value):
        if '|' in value or SERIES in value or '증시story' in value:
            raise ValueError('Title is the hook only; the service appends the date, edition and series name')
        return value

    @field_validator('hashtags')
    @classmethod
    def hashtag(cls, value):
        if any(not re.fullmatch(r'#[0-9A-Za-z가-힣]{1,20}', h) or h in BASE_TAGS for h in value):
            raise ValueError('Hashtags are #word and exclude the fixed channel tags')
        return value

    @field_validator('tags')
    @classmethod
    def tag(cls, value):
        if any(not 1 <= len(t) <= 30 or ',' in t or t.startswith('#') or t in FIXED_TAGS for t in value):
            raise ValueError('Tags are plain keywords; the channel tags are appended by the service')
        return value


def stamp(seconds):
    return f'{int(seconds) // 60:02}:{int(seconds) % 60:02}'


def references(source, used_ids):
    """Outlets named by the cited lines of the delivered body, with the URL the body itself links."""
    from .body import references as body_references

    return body_references(source, used_ids)[0]


def compose(copy, day, asof, edition, chapters, refs, playlist_url=''):
    """Fill the owner's template exactly; only the {…} slots vary."""
    lines = [copy.lead, copy.intro, '', f'📅 {day:%Y.%m.%d} | {copy.market} 증시',
             f'자료 기준: {asof:%Y.%m.%d %H:%M} (한국시간) · {TIMING[edition]}', '', '📌 오늘 짚어볼 이야기']
    lines += [f'• {s}' for s in copy.stories]
    lines += ['', '⏱ 영상 순서'] + [f'{stamp(start)} {title}' for start, title in chapters]
    if refs:
        lines += ['', '📚 참고 자료'] + refs
    lines += ['', CLOSING, '']
    if playlist_url:
        lines += ['▶ 증시story 모아보기', playlist_url, '']
    lines += [SIGNATURE, '', ' '.join(BASE_TAGS + tuple(copy.hashtags))]
    title = f'{copy.title} | {day.month}월 {day.day}일 {EDITION_WORD.get(edition, "아침")} · {SERIES}'
    tags = list(dict.fromkeys(copy.tags)) + list(FIXED_TAGS)
    result = {'title': title, 'description': '\n'.join(lines), 'tags': tags}
    check(result, refs_allowed={r for r in refs if r.startswith('http')}, playlist_url=playlist_url)
    return result


def check(result, refs_allowed=frozenset(), playlist_url=''):
    title, text, tags = result['title'], result['description'], result['tags']
    if not SUFFIX.search(title) or title.startswith('[') or len(title) > 100:
        raise ValueError(f"Title ends with ' | M월 D일 아침|마감 · {SERIES}' and has at most 100 characters")
    if INSTRUCTION.search(title) or INSTRUCTION.search(text):
        raise ValueError('Buy/sell instruction wording in the upload copy')
    if TOOL_WORDING.search(title) or TOOL_WORDING.search(text) or any(TOOL_WORDING.search(t) for t in tags):
        raise ValueError('Production or tool wording in the upload copy')
    if len(text) > 5000 or len(text.encode()) > 5000:
        raise ValueError('Description exceeds the YouTube limit')
    order = ['📅 ', '자료 기준: ', '📌 오늘 짚어볼 이야기', '⏱ 영상 순서', CLOSING, SIGNATURE, ' '.join(BASE_TAGS)]
    positions = [text.find(x) for x in order]
    if -1 in positions or positions != sorted(positions):
        raise ValueError('Description does not follow the fixed template order')
    times = re.findall(r'^(\d{2}):(\d{2}) \S', text.split('⏱ 영상 순서\n', 1)[1].split('\n\n', 1)[0], flags=re.M)
    seconds = [int(m) * 60 + int(s) for m, s in times]
    if len(seconds) < 3 or seconds[0] != 0 or any(b - a < 10 for a, b in zip(seconds, seconds[1:], strict=False)):
        raise ValueError('Chapters start at 00:00, list at least three and are at least 10 seconds apart')
    if not re.search(r'^#증시story #뭐든story( #[0-9A-Za-z가-힣]{1,20}){1,2}$', text, flags=re.M):
        raise ValueError('Hashtag line must be #증시story #뭐든story plus one or two topic tags')
    urls = set(re.findall(r'https?://\S+', text)) - ({playlist_url} if playlist_url else set())
    if not urls <= set(refs_allowed):
        raise ValueError('Reference URL is not from the delivered brief body')
    if not 15 <= len(tags) <= 25 or len(', '.join(tags)) > 500 or tags[-2:] != list(FIXED_TAGS):
        raise ValueError('Tags: 15-25 keywords, 500 characters, channel tags last')
    return True

"""증시story upload copy (docs/DAILY_BRIEF_DESIGN_SPEC.md §15): fixed title prefix, description template,
chapters from the rendered timeline, references from the frozen source bundle, bounded tags."""

import re
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from ..contracts import StrictModel

PREFIX = '[증시story] '
CLOSING = ('──────────\n\n'
           '오늘 시장에 무슨 일이 있었는지,\n'
           '어떤 소식이 주가를 움직였는지.\n'
           '증시story에서 그 앞뒤 이야기를 함께 짚어봅니다.\n\n'
           '궁금한 시장 이야기나 다음에 다뤘으면 하는 주제가 있다면 댓글로 남겨주세요.')
SIGNATURE = '세상의 뭐든, 이야기로 만나다. 뭐든story.'
BASE_TAGS = ('#증시story', '#뭐든story')
FIXED_TAGS = ('증시story', '뭐든story')
TIMING = {'am': '장전', 'intraday': '장중', 'close': '마감'}


class UploadCopy(StrictModel):
    """Model-written parts only; layout, fixed lines, chapters and references are filled by the service."""
    title: str = Field(min_length=5, max_length=88)
    lead: str = Field(min_length=10, max_length=160)
    intro: str = Field(min_length=10, max_length=320)
    market: Literal['국내', '미국', '글로벌']
    stories: list[str] = Field(min_length=3, max_length=3)
    hashtags: list[str] = Field(min_length=1, max_length=2)
    tags: list[str] = Field(min_length=13, max_length=23)

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
    """Only documents the cited claims rely on; a URL appears only when the frozen bundle holds it."""
    rows = []
    for doc in source['bundle']['documents']:
        if doc['id'] not in used_ids:
            continue
        when = doc.get('published_at')
        day = datetime.fromisoformat(when).strftime('%Y.%m.%d') if isinstance(when, str) and when else ''
        rows.append(f"• {doc['publisher']} — {doc['title']}" + (f' / {day}' if day else ''))
        if doc.get('url'):
            rows.append(doc['url'])
    return rows


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
    title = PREFIX + copy.title
    tags = list(dict.fromkeys(copy.tags)) + list(FIXED_TAGS)
    result = {'title': title, 'description': '\n'.join(lines), 'tags': tags}
    check(result, refs_allowed={r for r in refs if r.startswith('http')}, playlist_url=playlist_url)
    return result


def check(result, refs_allowed=frozenset(), playlist_url=''):
    title, text, tags = result['title'], result['description'], result['tags']
    if not title.startswith(PREFIX) or len(title) > 100:
        raise ValueError('Title needs the [증시story] prefix and at most 100 characters')
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
        raise ValueError('Reference URL is not from the frozen source bundle')
    if not 15 <= len(tags) <= 25 or len(', '.join(tags)) > 500 or tags[-2:] != list(FIXED_TAGS):
        raise ValueError('Tags: 15-25 keywords, 500 characters, channel tags last')
    return True

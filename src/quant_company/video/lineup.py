"""The day's issue lineup, shared by the episode and its Shorts (owner, 2026-10-26: "왜 본편에서의 이슈와 개수가
다르니? 같아야지"; docs/superpowers/specs/2026-10-27-shorts-episode-summary-design.md).

The briefing's numbered issues ('*1. 제목* · 기간' in its 주요 이슈 section) are the lineup, in their own order. An
episode scene in an issue chapter covers an issue when it cites any of that issue's lines, the heading line included;
the chapter of the first such scene sets how deep the Shorts goes (핵심 이슈: what happened, why, what it means; 함께
볼 이슈: one number and one line). Nothing here is a render input: video/remote.py and its imports never import it."""

import html
import re
from typing import NamedTuple

from .body import parse, plain

ISSUE_CHAPTERS = ('핵심 이슈', '함께 볼 이슈')
CORE_MAX = 3
HEADING = re.compile(r'^\*(\d+)\. ([^*]+)\*(?: · (.*))?$')


class LineupIssue(NamedTuple):
    number: int
    title: str
    claim_ids: tuple[str, ...]
    scene_ids: tuple[str, ...]
    tier: str


def briefing_issues(source):
    """[(number, title, claim IDs of the heading line and every line under it)], in body order."""
    parts = source['body'] if isinstance(source['body'], list) else [source['body']]
    headings = []
    for part in parts:
        for raw in part.split('\n'):
            found = HEADING.match(html.unescape(raw.strip()))
            if found:
                text = found[2] + (f' · {found[3]}' if found[3] else '')
                headings.append((int(found[1]), plain(found[2]), plain(text)))
    issues, current, pending = [], None, list(headings)
    for claim_id, claim in parse(source['body'])['claims'].items():
        if claim['section'] != '주요 이슈':
            current = None
            continue
        if pending and claim['text'] == pending[0][2]:
            number, title, _ = pending.pop(0)
            current = (number, title, [claim_id])
            issues.append(current)
        elif current is not None:
            current[2].append(claim_id)
    return [(number, title, tuple(ids)) for number, title, ids in issues]


def lineup(source, episode):
    """The issues with the episode scenes that covered each (episode order) and their depth. episode: the delivered
    EpisodePlan as JSON."""
    scenes = [s for s in episode.get('scenes', []) if s.get('chapter') in ISSUE_CHAPTERS]
    found, core = [], 0
    for number, title, ids in briefing_issues(source):
        own = set(ids)
        covering = [s for s in scenes if own & set(s.get('claim_ids', []))]
        tier = 'brief'
        if covering and covering[0]['chapter'] == ISSUE_CHAPTERS[0] and core < CORE_MAX:
            tier, core = 'core', core + 1
        found.append(LineupIssue(number, title, ids, tuple(s['id'] for s in covering), tier))
    return found


def uncovered(issues):
    """The issues no episode scene covered."""
    return [i for i in issues if not i.scene_ids]

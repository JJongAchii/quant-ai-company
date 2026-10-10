"""The delivered brief body is the only source of a video (owner rule, 2026-10-10).

The committed edition's rendered Slack parts are parsed into numbered lines. Scenes cite those line IDs, and
every displayed or spoken number must appear in the cited lines. Raw articles, the proposal and the collection
bundle are never given to the script writer or the checks. Source attribution (publisher, URL) is read from the
body's own source list, so a single-outlet line can be labelled as a report."""

import html
import re

# Lines that describe the production or delivery, not the market. They never become claims.
SKIP = ('자료 기준', 'Analyst · ', '지수별 숫자 출처', '이용 안내', '수집 데이터는', '자료 수집·작성·검토가 완료되지',
        '내용 확인 중', '확인 부족:', '작성 과정에서 확인이 부족한', '검증에서 근거·시점 확인이', '일부 원문/일정 조회가',
        '출처 간 수치 차이가', '수집 데이터에 지연·결측', '*품질 검사 미통과')
HEADINGS = {'오늘의 핵심', '주요 숫자', '시장 전체 흐름', '주요 이슈', '업종·수급에서 볼 점', '아침에 짚었던 내용은', '다음 확인할 것',
            '상세 근거·추가 지표', '상세 계속', '확인 한계', '과거 데이터 비교', '출처', '거래대금 상위 종목'}
# Sections whose single-outlet lines are reported explanations; numbers boards and calendars are exempt.
REPORTED = {'오늘의 핵심', '시장 전체 흐름', '주요 이슈', '업종·수급에서 볼 점', '아침에 짚었던 내용은'}
SOURCE = re.compile(r'^\[(\d+)\] <([^|<>]+)\|([^<>]+)> · 발행 ([^·]+?)(?: · 조회 .*)?$')
LINK = re.compile(r'<[^<>|]+\|\[(\d+)\]>')
REF = re.compile(r'\[(\d+)\]')
# Brief labels that name the writer; the video keeps the meaning without the tool name.
LABELS = (('Analyst 해석', '해석'),)
# The brief's own analysis and conditions are not reports, even when they cite one outlet.
ANALYSIS = ('해석 · ', '다르게 볼 점 · ', '확인할 신호 · ', '원인 판단 유보 · ', '해석 ·')
CLOSE = '정규장 종가'
FLOW = re.compile(r'순매수|순매도|매도 우위|매수 우위|장 마감 기준')


def plain(text):
    text = html.unescape(text)
    for old, new in LABELS:
        text = text.replace(old, new)
    return re.sub(r'\s+', ' ', text.replace('*', '')).strip()


def parse(parts):
    """{'claims': {id: {text, section, refs, publishers, single_outlet}}, 'sources': {n: {publisher, url, published}}}"""
    if isinstance(parts, str):
        parts = [parts]
    sources, rows, section = {}, [], ''
    for index, part in enumerate(parts):
        lines = part.split('\n')
        if index == 0:
            lines = lines[1:]  # the edition title line
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            found = SOURCE.match(html.unescape(line))
            if found:
                sources[found[1]] = {'publisher': found[3].strip(), 'url': found[2].strip(), 'published': found[4].strip()}
                continue
            if any(line.lstrip('• ').startswith(s) for s in SKIP):
                continue
            heading = re.fullmatch(r'\*([^*]+)\*(?: · (.*))?', line)
            if heading and (heading[1] in HEADINGS or heading[1].startswith('상세')):
                section = heading[1]
                if heading[2] and section == '주요 숫자':
                    rows.append((section, heading[1] + ' · ' + heading[2], []))
                continue
            refs = LINK.findall(line)
            body = LINK.sub('', line)
            refs += REF.findall(body)
            body = REF.sub('', body)
            body = re.sub(r'^•\s*', '', body)
            numbered = re.fullmatch(r'\*\d+\. ([^*]+)\*(?: · (.*))?', body.strip())
            if numbered:
                body = numbered[1] + (f' · {numbered[2]}' if numbered[2] else '')
            text = plain(body)
            if len(text) < 4:
                continue
            rows.append((section, text, list(dict.fromkeys(refs))))
    claims = {}
    for i, (section, text, refs) in enumerate(rows, 1):
        publishers = sorted({sources[r]['publisher'] for r in refs if r in sources})
        claims[f'b{i:03}'] = {'text': text, 'section': section, 'refs': refs, 'publishers': publishers,
                              'single_outlet': (len(publishers) == 1 and section in REPORTED
                                                and not text.startswith(ANALYSIS))}
    return {'claims': claims, 'sources': sources}


def catalog(source):
    return parse(source['body'])['claims']


def prompt_lines(source):
    return {k: {'text': v['text'], 'section': v['section'], 'outlets': v['publishers'],
                'report_label_required': v['single_outlet']} for k, v in catalog(source).items()}


def references(source, claim_ids):
    """Reference rows for the description: only outlets the cited body lines name, with the body's own URL."""
    parsed = parse(source['body'])
    used = list(dict.fromkeys(r for c in claim_ids if c in parsed['claims'] for r in parsed['claims'][c]['refs']))
    rows, urls = [], set()
    for ref in sorted(used, key=int):
        doc = parsed['sources'].get(ref)
        if not doc:
            continue
        day = re.match(r'(\d{2})/(\d{2})', doc['published'])
        year = str(source['day'])[:4]
        rows.append(f"• {doc['publisher']}" + (f' / {year}.{day[1]}.{day[2]}' if day else ''))
        rows.append(doc['url'])
        urls.add(doc['url'])
    return rows, urls

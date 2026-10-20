"""Real Chromium layout of the card template with the cards that broke in the 2026-10-13 PM episode: counter blocks
pushed off screen by script x values, summary icons drawn at full size from size 'sm', a four-row signals card under
the captions and a compare arrow drawn over its own label. The card data below is that episode's final plan."""

import importlib.util
import json
import shutil

import pytest

from quant_company.config import Settings
from quant_company.video.episode import EpisodePlan
from quant_company.video.motion import CHAPTER_TITLES, CHROMIUM_ARGS, QA_JS, MotionRenderer

from .test_video_motion import episode

SUMMARY = {'title': '오늘 볼 세 가지 · 지수는 정규장 종가 기준', 'cards': [
    {'n': 1, 'title': '코스피 급락, 반도체 밖까지',
     'items': [['chip', 'sm', 'gold', '반도체 약세', 'dn', '▼'], ['basket', 'sm', 'gold', '하락 종목 우세', 'dn', '▼']],
     'stats': [['코스피', '5.46%', 'dn'], ['코스닥', '1.43%', 'dn']]},
    {'n': 2, 'title': '국채 강세, 현물 유입은 미확인',
     'items': [['percent', 'sm', 'gold', '국채 강세', 'up', '▲'], ['doc', 'sm', 'gold', '현물 미확인', '', '·']],
     'stats': [['3년물 금리 · 전일', '6.2bp', 'dn'], ['10년물 금리 · 전일', '8.9bp', 'dn']]},
    {'n': 3, 'title': '유가 반락에도 경유는 빠듯',
     'items': [['drop', 'sm', 'gold', '유가 반락', 'dn', '▼'], ['ship', 'sm', 'gold', '경유 수출 감소', 'dn', '▼']],
     'stats': [['브렌트유 · 장중', '1.48%', 'dn'], ['경유 수출 · 전년 대비', '20%', 'dn']]}]}
BREADTH = {'icon': 'balance', 'chips': ['시장 폭', '장중'], 'title': '매도는 반도체 밖으로도 번졌다',
           'topic': '장중 집계 · 유가증권시장 종목 수', 'source': 'SBS 뉴스',
           'blocks': [{'x': 590, 'sub': '상승 약 70개 · 장중', 'gold': True, 'label': '하락 종목', 'value': '약 820개'},
                      {'x': 1150, 'dir': 'dn', 'sub': '28만3천원 · 정규장 종가 기준', 'label': '에이피알', 'value': '19.6%'}],
           'rows': [{'icon': 'basket', 'text': '화장품주 동반 하락 · 미국·유럽 매출 성장에도 물류비가 수익성 압박', 'source': '연합뉴스'},
                    {'icon': 'shield', 'text': '외국인 매도 2조원 넘어(장중) · 장 마감 기준 수급은 미확인', 'source': 'SBS 뉴스'}]}
DIESEL = {'icon': 'drop', 'chips': ['경유', '보도 해석'], 'title': '공급이 늘어도 경유값 하락은 제한적',
          'topic': '보도 해석 · 골드만삭스 추정', 'source': 'CNBC',
          'blocks': [{'x': 590, 'sub': '러시아산 약 600만배럴 늘어도', 'gold': True, 'label': '도매 경유 가격 하락',
                      'value': '배럴당 1~2달러'},
                     {'x': 1150, 'dir': 'dn', 'sub': '전년 대비', 'label': '미국 경유 재고', 'value': '14%'}],
          'rows': [{'icon': 'clock', 'text': '러시아 정유시설 가동 중단 하루 300만배럴 증가(전년 대비) · 증산 여력 제약',
                    'source': 'CNBC'},
                   {'icon': 'globe', 'text': '다르게 볼 점: 정유시설 복구·외교 접촉이 실제 증산·공격 중단으로 이어지면 유가 하락 지속 가능',
                    'source': 'CNBC · SBS 뉴스'}]}
BOND = {'chips': ['국채', '수일~수주'], 'title': '외국인, 3년 국채선물 역대 두 번째 규모 순매수',
        'bars': [{'n': 6.2, 'name': '3년물', 'label': '6.2bp'}, {'n': 8.9, 'name': '10년물', 'label': '8.9bp'}],
        'extra': [['외국인 10년 선물', '1만2,126계약'], ['금리 하락', '채권값 상승']],
        'tiles': [{'dir': 'dn', 'sub': '전일 최종호가 3.921%', 'label': '3년물 금리', 'value': '6.2bp', 'source': 'SBS 뉴스'},
                  {'sub': '전일 순매수 · 장 마감 기준', 'label': '외국인 3년 국채선물', 'value': '3만4,789계약', 'source': 'SBS 뉴스'}],
        'arrow_text': '10년물 하락폭 더 커', 'chart_title': '만기별 전일 금리 하락폭', 'chart_source': 'SBS 뉴스'}
SIGNALS = {'chips': ['다음 장', '확인할 신호'], 'title': '다음 장에서 확인할 신호',
           'disclaimer': '투자 권유가 아닌 정보 제공 목적이며, 투자 판단과 책임은 투자자 본인에게 있습니다.',
           'rows': [{'icon': 'chip', 'topic': '시장 폭', 'then': '광범위한 매도 지속 판단 약화',
                     'conds': ['반도체 하락 멈춤·하락 종목 감소', '외국인 매도 감소(장 마감 기준)']},
                    {'icon': 'bank', 'topic': '미국 은행 실적', 'then': '반도체 약세 확산 해석 약화', 'conds': ['은행 이익 견조', '영업 전망 견조']},
                    {'icon': 'percent', 'topic': '국채', 'then': '상대적 수요 우위 해석 약화',
                     'conds': ['현물 매수 없이 선물 매수 되돌림', '금리 상승']},
                    {'icon': 'drop', 'topic': '경유', 'then': '공급 부족의 유가 지지 해석 약화',
                     'conds': ['세계 경유 해상 수출 반등', '미국 경유 재고 반등']}]}
CAPTION = '다만 정유시설 복구와 외교 접촉이 실제 증산과 공격 중단으로 이어지면 유가 하락이 이어질 수 있습니다.'

RECTS = r"""(sels) => {
 const scn=[...document.querySelectorAll('.scene')].find(s=>s.style.display==='block');
 const R=e=>{const r=e.getBoundingClientRect();return {l:r.left,t:r.top,r:r.right,b:r.bottom,w:r.width,h:r.height,
   text:e.textContent.trim(),sw:e.scrollWidth,cw:e.clientWidth};};
 const out={stage:R(document.querySelector('#stage'))};
 for(const [k,s] of Object.entries(sels)) out[k]=[...scn.querySelectorAll(s)].map(R);
 return out;}"""


def scene(id_, chapter, type_, data):
    return {'id': id_, 'chapter': chapter, 'type': type_, 'claim_ids': [], 'data': data,
            'narration': '화면 배치를 확인하는 장면입니다. 숫자는 화면에만 있습니다.'}


def plan_with(*cards):
    raw = episode().model_dump(mode='json')
    hook = raw['scenes'][0]
    calendar = next(s for s in raw['scenes'] if s['type'] == 'calendar')
    pads = [{**calendar, 'id': f'cal-{n}'} for n in range(max(0, 3 - len(cards)))]
    raw['scenes'] = [hook, *cards, *pads, scene('closing', '일정', 'signals', json.loads(json.dumps(SIGNALS)))]
    return EpisodePlan.model_validate(raw)


def measure(tmp_path, plan, sels, *, captions=(), probe=None, library=None):
    """Builds the episode page like a render does and returns, per scene, the layout check and the element boxes."""
    if not importlib.util.find_spec('playwright'):
        pytest.skip('Install optional video dependencies and Chromium')
    from playwright.sync_api import sync_playwright

    # Most cards carry no pictures: no asset library, speech or FFmpeg is needed to lay them out. A photo card gets a
    # one-entry library whose file the caller wrote under tmp_path.
    renderer = MotionRenderer(Settings(video_artifact_dir=tmp_path, video_asset_dir=tmp_path), aligner=object(),
                              library=library or {})
    renderer.sign_conflicts, renderer.generated_used, renderer.episode_assets = [], set(), {}
    page, scenes, _ = renderer._page({'id': 'layout', 'policy': {}}, plan, tmp_path / 'out')
    for number, item in enumerate(scenes):
        item.update(t0=number * 8.0, t1=number * 8.0 + 8)
    ep = {'date_label': '10.13 화', 'asof': '', 'sample_mark': '', 'brand_label': '마감 브리핑',
          'chapter_names': [c for c in CHAPTER_TITLES if c], 'ticker': plan.ticker.model_dump(mode='json'), 'scenes': scenes,
          'total': len(scenes) * 8.0, 'changes': [], 'captions': [list(c) for c in captions]}
    (page / 'episode.js').write_text('window.EP=' + json.dumps(ep, ensure_ascii=False) + ';')
    found = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=CHROMIUM_ARGS)
        try:
            tab = browser.new_page(viewport={'width': 1920, 'height': 1080})
            tab.goto((page / 'index.html').as_uri())
            tab.evaluate('document.fonts.ready')
            for item in scenes:
                tab.evaluate(f"seek({item['t1'] - 0.6})")
                found[item['id']] = {'qa': tab.evaluate(QA_JS), **tab.evaluate(RECTS, sels)}
                if probe:
                    found[item['id']]['probe'] = tab.evaluate(probe)
            if captions:
                tab.evaluate(f'seek({(captions[0][0] + captions[0][1]) / 2})')
                # Width of each rendered caption line (text rects grouped by their top edge).
                found['caption'] = tab.evaluate(r"""() => {const box=document.querySelector('#capbox');
                  const rg=document.createRange(); rg.selectNodeContents(box); const lines=new Map();
                  for(const r of rg.getClientRects()){if(r.width<1) continue; const k=Math.round(r.top/10);
                    const [l,rt]=lines.get(k)||[Infinity,-Infinity]; lines.set(k,[Math.min(l,r.left),Math.max(rt,r.right)]);}
                  return {lines:[...lines.values()].map(([l,r])=>r-l), box:box.getBoundingClientRect().width};}""")
        finally:
            browser.close()
    return found


def inside(box, outer, slack=1):
    return (box['l'] >= outer['l'] - slack and box['r'] <= outer['r'] + slack and box['t'] >= outer['t'] - slack
            and box['b'] <= outer['b'] + slack)


def test_counter_blocks_share_the_panel_whatever_x_the_script_wrote(tmp_path):
    plan = plan_with(scene('breadth', '핵심 이슈', 'counter', BREADTH), scene('diesel', '핵심 이슈', 'counter', DIESEL))
    found = measure(tmp_path, plan, {'panel': '.glass', 'block': '[data-block]', 'fit': '[data-fit]'})
    for name in ('breadth', 'diesel'):
        boxes = found[name]
        assert not [q for q in boxes['qa'] if q[0] == 'outside-stage'], boxes['qa']
        assert len(boxes['block']) == 2 and all(inside(b, boxes['panel'][0]) for b in boxes['block']), boxes['block']
        assert all(f['sw'] <= f['cw'] + 1 for f in boxes['fit']) and boxes['fit']
    # The direction the script gave is drawn once in front of the value, as on the other cards.
    assert [b['text'] for b in found['diesel']['fit']][-1] == '▼ 14%'


def test_counter_names_its_own_panel_heading(tmp_path):
    plan = plan_with(scene('breadth', '핵심 이슈', 'counter', {**BREADTH, 'kicker': '장중 시장 폭'}),
                     scene('diesel', '핵심 이슈', 'counter', DIESEL))
    found = measure(tmp_path, plan, {'kicker': '[data-kicker]'})
    assert [k['text'] for k in found['breadth']['kicker']] == ['◆ 장중 시장 폭']
    assert [k['text'] for k in found['diesel']['kicker']] == ['◆ 다르게 볼 점']


def test_summary_icons_take_named_sizes_and_stats_show_their_direction(tmp_path):
    plan = plan_with(scene('summary', '요약', 'summary3', SUMMARY))
    covered = r"""() => {const scn=[...document.querySelectorAll('.scene')].find(s=>s.style.display==='block');
      return [...scn.querySelectorAll('.spot .num')].map(n=>{const r=n.getBoundingClientRect();
        const top=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2); return n.contains(top);});}"""
    found = measure(tmp_path, plan, {'icon': '.spot svg', 'cap': '.spot-item .cap', 'badge': '.badge', 'card': '.paper',
                                     'value': '.stat .v'}, probe=covered)['summary']
    assert len(found['icon']) == 6 and all(i['h'] <= 120 for i in found['icon']), [i['h'] for i in found['icon']]
    assert all(cap['t'] >= icon['b'] - 1 for icon, cap in zip(found['icon'], found['cap'], strict=True))
    assert all(found['probe']) and len(found['probe']) == 3
    assert all(any(inside(b, card) for card in found['card']) for b in found['badge'])
    assert [v['text'] for v in found['value']][:2] == ['▼ 5.46%', '▼ 1.43%']


def test_four_signal_rows_and_the_disclaimer_stay_above_the_captions(tmp_path):
    plan = plan_with(scene('breadth', '핵심 이슈', 'counter', BREADTH))
    found = measure(tmp_path, plan, {'row': '.glass', 'note': '[data-disclaimer]'})['closing']
    assert not [q for q in found['qa'] if q[0] == 'outside-stage'], found['qa']
    assert len(found['row']) == 4 and all(r['b'] <= found['stage']['b'] for r in found['row'])
    assert found['note'] and found['note'][0]['t'] >= found['row'][-1]['b'] and found['note'][0]['b'] <= found['stage']['b']


def test_compare_arrow_label_clears_the_arrow_and_the_extra_tiles_stay_in_their_panel(tmp_path):
    plan = plan_with(scene('bond', '핵심 이슈', 'compare', BOND))
    found = measure(tmp_path, plan, {'arrow': '[data-role=arrow]', 'label': '[data-role=arrow-text]',
                                     'panel': '.glass', 'extra': '[data-extra]'})['bond']
    arrow, label = found['arrow'][0], found['label'][0]
    assert label['b'] <= arrow['t'] or label['t'] >= arrow['b'] or label['r'] <= arrow['l'] or label['l'] >= arrow['r']
    assert len(found['extra']) == 2 and all(inside(e, found['panel'][0]) for e in found['extra'])
    assert not [q for q in found['qa'] if q[0] in {'outside-stage', 'overlap'}], found['qa']


def test_long_values_shrink_to_fit_their_block(tmp_path):
    crowded = {**DIESEL, 'blocks': [*DIESEL['blocks'], {'x': 900, 'label': '정유 마진', 'value': '배럴당 35달러 안팎까지',
                                                        'sub': '전년 대비 크게 확대'}]}
    plan = plan_with(scene('diesel', '핵심 이슈', 'counter', crowded))
    found = measure(tmp_path, plan, {'panel': '.glass', 'block': '[data-block]', 'fit': '[data-fit]'})['diesel']
    assert not [q for q in found['qa'] if q[0] in {'outside-stage', 'overlap'}], found['qa']
    assert len(found['fit']) == 3 and all(f['sw'] <= f['cw'] + 1 for f in found['fit'])
    assert all(inside(b, found['panel'][0]) for b in found['block'])


def test_captions_break_into_balanced_lines(tmp_path):
    plan = plan_with(scene('breadth', '핵심 이슈', 'counter', BREADTH))
    found = measure(tmp_path, plan, {}, captions=[(0.5, 7.5, CAPTION)])
    widths = sorted(found['caption']['lines'])
    assert len(widths) == 2 and widths[0] >= 0.6 * widths[1], widths
    # The dark caption box hugs its balanced lines (28px padding each side) instead of staying 1500px wide.
    assert found['caption']['box'] <= widths[1] + 56 + 8, found['caption']


def test_only_chapters_the_episode_uses_get_a_pill():
    """2026-10-13 PM: the '시장' pill stayed dark for the whole episode because no scene used that chapter."""
    from quant_company.video.motion import chapter_names

    scenes = [{'chapter': ''}, {'chapter': '요약'}, {'chapter': '핵심 이슈'}, {'chapter': '일정'}]
    assert chapter_names(scenes) == ['요약', '핵심 이슈', '일정']


THUMB_RECTS = r"""() => {const R = s => {const r = document.querySelector(s).getBoundingClientRect();
  return {t: r.top, b: r.bottom, l: r.left, r: r.right};};
  const q = document.querySelector('.q');
  return {q: R('.q'), n: R('.n'), bar: R('.bar'), size: parseFloat(getComputedStyle(q).fontSize),
          lines: Math.round(q.getBoundingClientRect().height / parseFloat(getComputedStyle(q).lineHeight))};}"""


def thumbnail(tmp_path, question, stat, template=None, probe=None):
    """Renders the thumbnail like a render does; returns the renderer's finding and the text boxes."""
    if not importlib.util.find_spec('playwright'):
        pytest.skip('Install optional video dependencies and Chromium')
    from importlib.resources import files

    from playwright.sync_api import sync_playwright

    renderer = MotionRenderer(Settings(video_artifact_dir=tmp_path, video_asset_dir=tmp_path), aligner=object(), library={})
    renderer.episode_assets = {}
    page = tmp_path / 'page'
    shutil.copytree(files('quant_company.video').joinpath('design'), page)
    if template:
        (page / 'thumbnail.html').write_text(template((page / 'thumbnail.html').read_text()))
    plan = episode(thumbnail=question, thumbnail_stat=stat, thumbnail_image='')
    ep = {'date_label': '10.14 수', 'sample_mark': '', 'brand_label': '아침 시장 브리핑'}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=CHROMIUM_ARGS)
        try:
            found = renderer._thumbnail(browser, plan, page, ep, tmp_path)
            tab = browser.new_page(viewport={'width': 1280, 'height': 720})
            tab.goto((page / 'thumbnail.render.html').as_uri())
            tab.evaluate('document.fonts.ready.then(() => window.fit && fit())')
            rects = tab.evaluate(THUMB_RECTS)
            if probe:
                rects['probe'] = tab.evaluate(probe)
        finally:
            browser.close()
    return found, rects


@pytest.mark.parametrize('question', ['반도체 급락에도 IMF는 성장률 상향?', '반도체 급락에도 IMF는 한국 성장률 상향?'])
def test_a_long_thumbnail_question_shrinks_and_never_covers_the_stat(tmp_path, question):
    """10/14 AM: the question took three lines at the fixed 104px and its last line '상향?' covered the stat
    'SK하이닉스 10.09% 하락', which sat at a fixed height; the renderer's check only compared the question with the bar."""
    found, box = thumbnail(tmp_path, question, 'SK하이닉스 10.09% 하락')
    assert box['q']['b'] <= box['n']['t'] and box['n']['b'] <= box['bar']['t'], box
    assert box['lines'] <= 2 and box['size'] >= 64, box
    assert found == []


def test_a_short_thumbnail_question_keeps_the_full_size(tmp_path):
    found, box = thumbnail(tmp_path, '코스피 5.46% 급락', '코스피 ▼5.46% · 정규장 종가 기준')
    assert box['size'] == 104 and box['q']['b'] <= box['n']['t'] and found == []


def test_the_thumbnail_check_reports_text_covering_the_stat(tmp_path):
    def broken(html):  # the stat pulled up under the question, as on 10/14
        return html.replace('</style>', '.n{position:absolute;left:60px;top:200px}</style>')

    found, _ = thumbnail(tmp_path, '반도체 급락에도 IMF는 성장률 상향?', 'SK하이닉스 10.09% 하락', template=broken)
    assert found == ['Thumbnail text exceeds safe screen area']


# 2026-10-14 AM cards (first render) that broke: the map's delta wrapped into six lines down to the captions, its alt
# line landed on the captions and two preset markers claimed facts the brief never had; a long calendar date pushed its
# chip onto the next row; a two-line extra value pushed the compare tiles out of their panel; a topic broke in two.
OIL = {'alt': '이란 해군, 위치추적 끈 유조선 적발 · 선체 파손과 화재', 'region': 'hormuz', 'chips': ['유가·해상 통항', '수일~수주'],
       'bars': [{'w': 100, 'gold': True, 'name': '10·11월 원유', 'label': '전년도 평균 대비 100% 넘게'},
                {'w': 100, 'name': '10월 나프타', 'label': '100% 초과 · 비교 기준 미제시'}, {'w': 100, 'name': '11월 나프타', 'label': '거의 100%'}],
       'cost': '골드만삭스: 러시아 경유 80만톤 추가 공급(기간 미제시)도 경유 도매가를 배럴당 1~2달러 낮추는 데 그칠 것',
       'delta': '브렌트 105.69달러 ▼96센트 · WTI 94.14달러 ▲40센트', 'title': '유가는 엇갈리고, 호르무즈 위험은 남았다',
       'cost_head': '다르게 볼 점', 'panel_title': '정부가 밝힌 확보 물량', 'delta_source': '화요일 정규장 종가 기준 · CNBC·연합뉴스'}
CALENDAR14 = {'title': '다음 확인할 일정', 'rows': [
    ['오늘', '수', '물가', 'percent', '미국 9월 소비자물가 · 시장 예상 3.7%', '시각 미확인'],
    ['오늘', '수', '공개', 'doc', '앤트로픽 3분기 재무정보 공개 계획 · 관계자 보도', '시각 미확인'],
    ['내일', '목', '지표', 'basket', '미국 9월 생산자물가·소매판매', '시각 미확인'],
    ['10월 27~28일', '화·수', 'FOMC', 'bank', '미 연준 통화정책회의', '시각 미확인'],
    ['10월 29일', '목', 'GDP', 'chartUp', '미국 3분기 GDP 속보·9월 개인소득 및 지출', '시각 미확인']]}
RATES = {'bars': [{'n': 4.306, 'name': '전 거래일', 'label': '4.306%'}, {'n': 4.229, 'name': '13일', 'label': '4.229%'}],
         'chips': ['장기금리와 한국은행', '당일'], 'title': '같은 금리 하락, 다른 이유', 'arrow_text': '안전자산 수요',
         'extra': [['미국 금리 하락 배경', '에너지 공급 기대'], ['한국 금리 하락 배경', '주가 급락·외국인 선물 매수']],
         'tiles': [{'dir': 'dn', 'sub': '장중 · 2bp 넘게 하락 · 정규장 종가 기준 미확인', 'label': '미 10년물', 'value': '5.215%', 'source': 'CNBC'},
                   {'sub': '장중 · 보합', 'label': '미 2년물', 'value': '4.791%', 'source': 'CNBC'},
                   {'dir': 'dn', 'sub': '전 거래일 3.921%에서 하락', 'label': '국고채 3년물', 'value': '3.857%', 'source': '연합뉴스'}],
         'chart_title': '국고채 10년물', 'chart_source': '연합뉴스 · 13일 장 마감 기준'}
WATCH = {**SIGNALS, 'rows': [{**SIGNALS['rows'][0], 'topic': '반도체·수급'}, {**SIGNALS['rows'][1], 'topic': '미국 물가·금리'},
                             {**SIGNALS['rows'][2], 'topic': '유가·통항'}, {**SIGNALS['rows'][3], 'topic': '성장 전망'}]}
BROKEN = ('overlap', 'outside-stage', 'card-overflow')


def test_the_map_panel_keeps_its_text_inside_and_draws_no_preset_claims(tmp_path):
    found = measure(tmp_path, plan_with(scene('oil', '함께 볼 이슈', 'map', OIL)),
                    {'panel': '.glass', 'svg': 'svg text', 'delta': '[data-delta]'})['oil']
    assert not [x for x in found['qa'] if x[0] in BROKEN], found['qa']
    assert not [t for t in found['svg'] if '미군' in t['text'] or '환적' in t['text']]
    assert len(found['delta']) == 1 and found['delta'][0]['h'] < 70 and inside(found['delta'][0], found['panel'][1])


def test_a_long_calendar_date_stays_on_one_line_above_its_chip(tmp_path):
    found = measure(tmp_path, plan_with(scene('week', '일정', 'calendar', CALENDAR14)), {'date': '[data-date]'})['week']
    assert not [x for x in found['qa'] if x[0] in BROKEN], found['qa']
    assert len(found['date']) == 5 and all(d['h'] < 50 and d['sw'] <= d['cw'] + 1 for d in found['date']), found['date']


def test_compare_extra_values_keep_one_line_inside_their_panel(tmp_path):
    found = measure(tmp_path, plan_with(scene('rates', '핵심 이슈', 'compare', RATES)), {'panel': '.glass', 'extra': '[data-extra]'})['rates']
    assert not [x for x in found['qa'] if x[0] in BROKEN], found['qa']
    assert all(inside(e, found['panel'][0]) for e in found['extra']), (found['extra'], found['panel'])


def test_a_two_word_signal_topic_keeps_one_line(tmp_path):
    found = measure(tmp_path, plan_with(scene('watch', '일정', 'signals', WATCH)), {'topic': '[data-topic]'})['watch']
    assert len(found['topic']) == 4 and all(t['h'] < 50 and t['sw'] <= t['cw'] + 1 for t in found['topic']), found['topic']


# 2026-10-14 PM cards that misled after three visual reviews: each counter value shrank on its own, so the shortest
# number ('4000명', the smallest change) was drawn the largest; two stats of one summary card came out in different
# sizes; the map delta line is always the falling colour, so '…상승' was drawn blue.
JOBS = {'icon': 'balance', 'chips': ['고용', '수개월'], 'title': '성장의 온기, 고용엔 고르지 않다',
        'topic': '9월 고용보험 가입자 (전년 동월 대비)', 'source': '이투데이', 'kicker': '고용 근거',
        'blocks': [{'dir': 'up', 'sub': '증가분 대부분 · 전체 28만8000명 증가', 'label': '서비스업', 'value': '28만3000명'},
                   {'dir': 'dn', 'sub': '외국인 당연가입분 제외 · 건설업도 5900명 감소, 38개월 연속', 'label': '내국인 제조업',
                    'value': '4000명'},
                   {'dir': 'dn', 'gold': True, 'sub': '인구 감소 등 영향', 'label': '29세 이하', 'value': '5만5000명'}],
        'rows': [{'icon': 'chip', 'text': '해석: 성장 전망 개선의 중심은 반도체 수출'}]}
SUMMARY14 = json.loads(json.dumps(SUMMARY))
SUMMARY14['cards'][1]['stats'] = [['미국 빅테크', '투자 실행의 제약', ''], ['시장이 볼 것', '수요 전망·실제 자금조달', '']]
FIT_SIZES = """() => {const scn=[...document.querySelectorAll('.scene')].find(s=>s.style.display==='block');
  return [...scn.querySelectorAll('[data-fit-group]')].map(e=>[e.dataset.fitGroup, parseFloat(getComputedStyle(e).fontSize),
    e.scrollWidth<=e.clientWidth+1]);}"""
DELTA = """() => {const scn=[...document.querySelectorAll('.scene')].find(s=>s.style.display==='block');
  const e=scn.querySelector('[data-delta]'); return e && {cls:e.className, color:getComputedStyle(e).color};}"""


def test_counter_values_side_by_side_share_one_size(tmp_path):
    found = measure(tmp_path, plan_with(scene('jobs', '핵심 이슈', 'counter', JOBS)), {}, probe=FIT_SIZES)['jobs']
    sizes = [size for group, size, _ in found['probe'] if group == 'blocks']
    assert len(sizes) == 3 and len(set(sizes)) == 1, found['probe']
    assert all(fits for _, _, fits in found['probe']), found['probe']


def test_the_stats_of_one_summary_card_share_one_size(tmp_path):
    found = measure(tmp_path, plan_with(scene('summary', '요약', 'summary3', SUMMARY14)), {}, probe=FIT_SIZES)['summary']
    by_card: dict[str, set[float]] = {}
    for group, size, fits in found['probe']:
        by_card.setdefault(group, set()).add(size)
        assert fits, found['probe']
    assert len(by_card) == 3 and all(len(sizes) == 1 for sizes in by_card.values()), found['probe']


@pytest.mark.parametrize('direction,cls', [('up', 'upn'), ('dn', 'dnn'), (None, '')])
def test_the_map_delta_takes_the_colour_of_its_direction(tmp_path, direction, cls):
    data = {**OIL, 'delta': '브렌트 1.24%·WTI 1.18% 상승 · 수요일 장중'}
    if direction:
        data['delta_dir'] = direction
    found = measure(tmp_path, plan_with(scene('oil', '함께 볼 이슈', 'map', data)), {}, probe=DELTA)['oil']
    assert found['probe']['cls'] == cls, found['probe']



# 2026-10-15 PM cards left after three visual reviews: the photo card drew 'CoreWeave 16%' (direction dn) as a bare white
# number; the map's bars took w as pixels, so '약 40%'·'약 20%' became 40px and 20px squares with no scale; summary card
# titles left one word on the second line ('…외국인 매도 속 / 급락'); and the cost line ignored the break the script wrote.
PIXEL = bytes.fromhex('89504e470d0a1a0a0000000d4948445200000001000000010806000000'
                      '1f15c4890000000d49444154789c6360f8cfc0f01f0005fe02fea3586b9b0000000049454e44ae426082')
AI_COST = {'chips': ['AI 조달 부담', '수개월'], 'title': 'TSMC는 호실적, AI 투자 자금은 비싸졌다', 'image': {'asset': 'fixture-photo'},
           'rows': [{'chip': '코어위브 주가', 'big': '16%', 'dir': 'dn', 'sub': '급락 · 수요일 보도 시점', 'source': 'CNBC'},
                    {'chip': '코어위브 채권', 'big': '14.8%', 'sub': '2032년 만기채 만기수익률 · 수요일 보도 시점', 'source': 'CNBC'},
                    {'chip': '해석', 'kind': 'interp', 'text': '다음 투자에 필요한 자금의 가격이 높아진다는 신호', 'sub': '기존 채권의 표면금리가 바뀐 것은 아님'}]}
HORMUZ = {**OIL, 'bars': [{'w': 40, 'gold': True, 'name': '해협 원유 수출', 'label': '약 40% 적음'},
                          {'w': 20, 'name': '우회 송유관 포함 지역 전체', 'label': '약 20% 적음'}],
          'cost': '완충은 비축유 방출\n변수는 네타냐후 ‘군사행동 현재 부적절’ 발언과 기만전술 가능성이 겹친다는 점'}
SUMMARY15 = json.loads(json.dumps(SUMMARY))
SUMMARY15['cards'][0]['title'] = '반도체 충격과 외국인 매도 속 급락'
ROW = """() => {const scn=[...document.querySelectorAll('.scene')].find(s=>s.style.display==='block');
  const big=[...scn.querySelectorAll('.glass .tnum')].find(e=>e.textContent.includes('16%'));
  return big && {text:big.textContent.trim(), arrows:[...big.querySelectorAll('span')].map(s=>[s.className,s.textContent.trim()])};}"""
BARS = """() => {const scn=[...document.querySelectorAll('.scene')].find(s=>s.style.display==='block');
  const bars=[...scn.querySelectorAll('[data-track]')];
  return bars.length ? bars.map(t=>({track:t.getBoundingClientRect().width,
    fill:t.querySelector('.bar').getBoundingClientRect().width})) : null;}"""
LINES = r"""() => {const scn=[...document.querySelectorAll('.scene')].find(s=>s.style.display==='block');
  const lines=e=>{const rg=document.createRange(); rg.selectNodeContents(e); const m=new Map();
    for(const r of rg.getClientRects()){if(r.width<1) continue; const k=Math.round(r.top/8);
      const [l,rt]=m.get(k)||[Infinity,-Infinity]; m.set(k,[Math.min(l,r.left),Math.max(rt,r.right)]);}
    return {lines:[...m.values()].map(([l,r])=>r-l), box:e.getBoundingClientRect().width};};
  return {titles:[...scn.querySelectorAll('.paper .h2')].map(lines), cost:[...scn.querySelectorAll('[data-cost]')].map(lines)};}"""


def test_a_photo_row_draws_the_direction_of_its_number(tmp_path):
    (tmp_path / 'p.png').write_bytes(PIXEL)
    library = {'fixture-photo': {'file': 'p.png', 'kind': '자료사진', 'author': 'Peellden', 'license': 'CC BY-SA 3.0'}}
    found = measure(tmp_path, plan_with(scene('ai-cost', '핵심 이슈', 'photo', AI_COST)), {}, probe=ROW,
                    library=library)['ai-cost']
    assert found['probe']['text'].startswith('▼') and found['probe']['text'].count('▼') == 1, found['probe']
    assert ['dnn', '▼'] in found['probe']['arrows'], found['probe']


def test_map_bars_fill_one_track_in_proportion(tmp_path):
    found = measure(tmp_path, plan_with(scene('hormuz', '함께 볼 이슈', 'map', HORMUZ)), {}, probe=BARS)['hormuz']
    bars = found['probe']
    assert bars and len(bars) == 2 and bars[0]['track'] == bars[1]['track'] >= 240, bars
    assert abs(bars[0]['fill'] / bars[1]['fill'] - 2) < 0.05 and bars[0]['fill'] < bars[0]['track'], bars
    assert not [x for x in found['qa'] if x[0] in BROKEN], found['qa']


def test_card_titles_wrap_without_a_lone_last_word(tmp_path):
    found = measure(tmp_path, plan_with(scene('summary', '요약', 'summary3', SUMMARY15)), {}, probe=LINES)['summary']
    first = found['probe']['titles'][0]['lines']
    assert len(first) == 2 and first[1] >= 0.4 * first[0], first


def test_the_cost_line_keeps_the_break_the_script_wrote(tmp_path):
    found = measure(tmp_path, plan_with(scene('hormuz', '함께 볼 이슈', 'map', HORMUZ)), {}, probe=LINES)['hormuz']
    (cost,) = found['probe']['cost']
    assert len(cost['lines']) >= 2 and cost['lines'][0] < 0.5 * cost['box'], cost


# 2026-10-16 AM cards left after three visual reviews: every summary card kept a 140px empty band between its title and
# its stats (the stats sat at the card's bottom edge); the compare arrow and its label were always blue, so the KOFIX
# '0.25%p 상승' arrow read as a fall, and the label rose to 4px under the chart's source chip; the five-row signals card
# drew 84px icon tiles in 66px rows, so the tiles stuck out of their rows and touched each other.
SUMMARY16 = {'title': '한국만 더 흔들린 이유와 오늘의 세 가지', 'cards': [
    {'n': 1, 'title': '메모리 쏠림과 수급 압력',
     'items': [['memory', 'lg', 'red', '삼성전자·SK하이닉스 쏠림', 'dn', '▼'], ['globe', 'md', 'sky', '외국인 매도·원화 약세', 'dn', '▼']],
     'stats': [['시총 감소분 중 두 종목', '약 65%', ''], ['원/달러 기준가', '1,344.6원', '']]},
    {'n': 2, 'title': '반등 뒤 뜨거운 물가',
     'items': [['gauge', 'lg', 'red', '생산자물가 예상 상회', 'up', '▲'], ['basket', 'md', 'sky', '통제그룹 판매 부진', 'dn', '▼']],
     'stats': [['PPI 전월 대비', '1.3%', 'up'], ['통제그룹 · 전망 0.3% 하회', '0.1%', '']]},
    {'n': 3, 'title': '채권 강세, 대출 기준지표 상승',
     'items': [['percent', 'md', 'green', '국고채 금리 하락', 'dn', '▼'], ['bank', 'lg', 'red', '코픽스 상승', 'up', '▲']],
     'stats': [['국고채 10년', '6.8bp', 'dn'], ['코픽스 신규취급액', '0.25%p', 'up']]}]}
KOFIX = {'bars': [{'n': 3.18, 'name': '전월', 'label': '3.18%'}, {'n': 3.43, 'name': '9월', 'label': '3.43%'}],
         'chips': ['대출 금리', '수개월'], 'title': '국고채는 내렸는데, 주담대 기준지표는 올랐다', 'arrow_text': '0.25%p 상승',
         'extra': [['잔액 기준', '3.05% → 3.15%'], ['한은 기준금리', '2.50% → 3.00%']],
         'tiles': [{'sub': '15일 마감 · 6.8bp 하락', 'label': '국고채 10년', 'value': '4.193%', 'source': '연합뉴스'},
                   {'sub': '15일 마감 · 1.6bp 하락', 'label': '국고채 3년', 'value': '3.867%', 'source': '연합뉴스'}],
         'chart_title': '코픽스 신규취급액 기준', 'chart_source': '이투데이'}
RETAIL = {'bars': [{'n': 6.4, 'name': '주유소 매출', 'label': '6.4%'}, {'n': 0.2, 'name': '주유소 제외 판매', 'label': '0.2%'}],
          'chips': ['미국 물가', '왜 중요한가'], 'title': '숫자는 강했지만, 휘발유를 빼면 약했다', 'arrow_text': '주유소를 빼면',
          'extra': [['전체 소매판매', '0.8% · 전망 0.4%'], ['통제그룹 (GDP 반영)', '0.1% · 전망 0.3% 하회']],
          'tiles': [{'dir': 'up', 'sub': '전망 0.6% 상회 · 근원 0.7%', 'label': 'PPI 전월 대비', 'value': '1.3%', 'source': '연합뉴스'},
                    {'dir': 'up', 'sub': '상품 상승분의 80% 이상', 'label': 'PPI 에너지 가격', 'value': '10.8%', 'source': '연합뉴스'}],
          'chart_title': '9월 소매판매 전월 대비 (명목)', 'chart_source': '연합뉴스'}
CONDITIONS = {'tag': '확인할 신호', 'title': '판단이 바뀌는 조건',
              'note': '석유화학: EIA 원유 재고 감소 둔화, 에틸렌·나프타 가격 차 확대 시 마진 압박 판단 약화 · 한국장 수급은 장 마감 기준 집계로 확인',
              'disclaimer': '투자 권유가 아닌 정보 제공 목적이며, 해석은 브리핑의 판단입니다. 투자 결정과 책임은 본인에게 있습니다.',
              'rows': [{'icon': 'balance', 'then': '수급 악순환 우려 약화', 'conds': ['외국인 순매도·미수 거래·반대매매 감소', '원화 안정'],
                        'topic': '한국장 회복 판단'},
                       {'icon': 'memory', 'then': '공급 충격 해석 약화', 'conds': ['CXMT 양산 확대 지연', '메모리 계약가격 유지'],
                        'topic': '메모리 공급 충격'},
                       {'icon': 'gauge', 'then': '비용 압박·수요 둔화 해석 약화', 'conds': ['근원 PPI 상승률 둔화', '통제그룹 판매 회복'],
                        'topic': '미국 물가·소비'},
                       {'icon': 'bank', 'then': '가계 부담 확대 전망 약화', 'conds': ['은행 신규 조달금리 하락', '변동형 주담대 적용금리 안정'],
                        'topic': '가계 대출 금리'},
                       {'icon': 'shield', 'then': '방어 공백 위험 평가 하향', 'conds': ['추가 요격탄 실제 인도', '조기경보·군 통신 정상화'],
                        'topic': '우크라이나 방공'}]}
ARROW = """() => {const scn=[...document.querySelectorAll('.scene')].find(s=>s.style.display==='block');
  const a=scn.querySelector('[data-role=arrow]'), t=scn.querySelector('[data-role=arrow-text]');
  const head=scn.querySelector('marker path'); if(!a) return null;
  return {arrow:a.getAttribute('stroke'), text:t.getAttribute('fill'), head:head.getAttribute('stroke')};}"""


def test_summary_stats_follow_their_title_without_an_empty_band(tmp_path):
    found = measure(tmp_path, plan_with(scene('summary', '요약', 'summary3', SUMMARY16)),
                    {'card': '.paper', 'title': '.paper .h2', 'stat': '.paper .stat'})['summary']
    assert not [x for x in found['qa'] if x[0] in BROKEN], found['qa']
    assert len(found['card']) == 3 and len(found['stat']) == 6
    for title, stats in zip(found['title'], (found['stat'][0:2], found['stat'][2:4], found['stat'][4:6]), strict=True):
        assert stats[0]['t'] - title['b'] <= 40, (title, stats[0])
    assert all(inside(card, found['stage']) for card in found['card'])
    # '통제그룹 · 전망 0.3% 하회' takes two lines; every tile still has the same height, as the 10/16 inspection asked.
    assert len({round(s['h']) for s in found['stat']}) == 1, [s['h'] for s in found['stat']]


def test_summary_stats_line_up_across_cards_when_one_title_takes_two_lines(tmp_path):
    found = measure(tmp_path, plan_with(scene('summary', '요약', 'summary3', SUMMARY15)),
                    {'card': '.paper', 'title': '.paper .h2', 'stat': '.paper .stat'})['summary']
    tops = {round(s['t']) for s in found['stat']}
    assert max(tops) - min(tops) <= 2, tops
    assert min(s['t'] for s in found['stat'][0:2]) - found['title'][0]['b'] <= 40, found['title'][0]
    assert len({round(c['h']) for c in found['card']}) == 1, [c['h'] for c in found['card']]


@pytest.mark.parametrize('direction,colour', [('up', '#FF5C6C'), ('dn', '#5EA0FF'), (None, '#FDC749')])
def test_the_compare_arrow_takes_the_colour_of_its_direction(tmp_path, direction, colour):
    data = {**KOFIX, 'arrow_dir': direction} if direction else KOFIX
    found = measure(tmp_path, plan_with(scene('kofix', '함께 볼 이슈', 'compare', data)), {}, probe=ARROW)['kofix']
    assert found['probe'] == {'arrow': colour, 'text': colour, 'head': colour}, found['probe']


@pytest.mark.parametrize('data', [KOFIX, RETAIL], ids=['kofix', 'retail'])
def test_the_compare_arrow_label_keeps_clear_of_the_chart_title(tmp_path, data):
    found = measure(tmp_path, plan_with(scene('chart', '핵심 이슈', 'compare', data)),
                    {'title': '.glass .lab', 'chip': '.glass .src', 'arrow': '[data-role=arrow]',
                     'label': '[data-role=arrow-text]'})['chart']
    label, arrow = found['label'][0], found['arrow'][0]
    assert label['t'] - max(found['title'][0]['b'], found['chip'][0]['b']) >= 16, (label, found['title'], found['chip'])
    assert label['b'] <= arrow['t'] or label['t'] >= arrow['b'] or label['r'] <= arrow['l'] or label['l'] >= arrow['r']
    assert not [x for x in found['qa'] if x[0] in BROKEN], found['qa']


def test_signal_icon_tiles_stay_inside_short_rows(tmp_path):
    found = measure(tmp_path, plan_with(scene('watch', '일정', 'signals', CONDITIONS)),
                    {'row': '.glass:has(> .tile)', 'tile': '.glass > .tile'})['watch']
    assert len(found['row']) == 5 and len(found['tile']) == 5
    for row, tile in zip(found['row'], found['tile'], strict=True):
        assert inside(tile, row), (tile, row)
    assert not [x for x in found['qa'] if x[0] in BROKEN], found['qa']


# 2026-10-16 PM, the first episode on render :11: two of the three 'jobs' flow notes took three lines and ran out of the
# bottom of their fixed 330px cards ('둔화' and '늦어질 수 있다' printed over the card edge). The layout check passed it,
# because it skipped every card holding an svg with a viewBox, and every icon is one.
JOBS16 = {'foot': {'chip': '확인할 신호',
                   'text': "청년·숙박음식·건설 감소폭이 줄고 제조업 증가가 이어지면 '수출 개선이 내수로 확산되지 못한다'는 판단 약화"},
          'chips': ['국내 고용', '수개월'], 'title': '제조업 고용은 회복, 청년·내수는 부진',
          'nodes': [{'big': '1만6천명', 'dir': 'up', 'head': '9월 제조업 취업자', 'icon': 'chartUp', 'chips': ['무슨 일'],
                     'sub': '27개월 만에 증가 전환 · 전체 취업자 11만3천명 증가, 8월 18만4천명보다 둔화', 'source': '이투데이 보도'},
                    {'big': '14만7천명', 'dir': 'dn', 'head': '15∼29세 청년 취업자', 'icon': 'gauge', 'chips': ['약한 고리'],
                     'sub': '46개월째 감소 · 실업률 5.4% · 약세가 이어지면 소비 관련 기업 매출 회복이 늦어질 수 있다',
                     'source': '이투데이 보도'},
                    {'big': '25만3천명', 'dir': 'up', 'head': '상용근로자', 'icon': 'check', 'chips': ['다르게 볼 점'],
                     'sub': "비중 57.8%, 9월 기준 역대 최고 · 청년 '쉬었음' 4만4천명 감소", 'source': '이투데이 보도'}]}
FLOW = r"""() => {const scn=[...document.querySelectorAll('.scene')].find(s=>s.style.display==='block');
  const R=e=>{const r=e.getBoundingClientRect(); return {t:r.top,b:r.bottom,l:r.left,r:r.right};};
  const text=n=>{const w=document.createTreeWalker(n,NodeFilter.SHOW_TEXT); let b=0,x;
    while((x=w.nextNode())){if(!x.textContent.trim()) continue; const rg=document.createRange(); rg.selectNodeContents(x);
      b=Math.max(b,rg.getBoundingClientRect().bottom);} return b;};
  return {nodes:[...scn.querySelectorAll('.paper')].map(n=>({...R(n),text:text(n)})),
          arrows:[...scn.querySelectorAll('path[marker-end]')].map(R), foot:R(scn.querySelector('.glass'))};}"""


def test_flow_cards_grow_to_hold_a_three_line_note(tmp_path):
    found = measure(tmp_path, plan_with(scene('jobs', '함께 볼 이슈', 'flow', JOBS16)), {}, probe=FLOW)['jobs']
    nodes, arrows, foot = found['probe']['nodes'], found['probe']['arrows'], found['probe']['foot']
    assert not [x for x in found['qa'] if x[0] in BROKEN], found['qa']
    assert len(nodes) == 3 and len(arrows) == 2
    for node in nodes:
        assert node['text'] <= node['b'] - 16, node
    assert len({round(n['b'] - n['t']) for n in nodes}) == 1, nodes
    middle = (nodes[0]['t'] + nodes[0]['b']) / 2
    for arrow, left, right in zip(arrows, nodes, nodes[1:], strict=False):
        assert abs((arrow['t'] + arrow['b']) / 2 - middle) <= 3, (arrow, middle)
        assert left['r'] <= arrow['l'] and arrow['r'] <= right['l'], (left, arrow, right)
    assert foot['t'] - nodes[0]['b'] >= 30 and foot['b'] <= found['stage']['b'], (nodes[0], foot)


ICON_CARD = """<div id="stage" style="position:absolute;left:80px;top:120px;width:1760px;height:730px">
 <div class="scene" style="display:block;position:absolute;inset:0">
  <div class="paper" style="position:absolute;left:0;top:0;width:520px;height:120px;padding:26px 32px;font-size:28px">
   <span class="ico"><svg width="56" height="56" viewBox="0 0 64 64"><circle cx="32" cy="32" r="20"/></svg></span>
   <div>넘치는 설명 · 세 줄을 넘는 긴 문장이 카드 아래 가장자리 밖으로 흘러내려 시청자에게 잘린 글자로 보입니다</div></div>
  <div class="glass" style="position:absolute;left:600px;top:0;width:600px;height:300px;overflow:hidden">
   <svg width="600" height="300" viewBox="0 0 60 30"><text x="5" y="15" font-size="4">지도</text></svg></div>
 </div></div>"""


def test_the_layout_check_reports_text_running_out_of_a_card_with_an_icon():
    if not importlib.util.find_spec('playwright'):
        pytest.skip('Install optional video dependencies and Chromium')
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=CHROMIUM_ARGS)
        try:
            tab = browser.new_page(viewport={'width': 1920, 'height': 1080})
            tab.set_content(ICON_CARD)
            qa = tab.evaluate(QA_JS)
        finally:
            browser.close()
    # A map or chart svg fills its card and may sit a few pixels over it; a 56px icon never excuses clipped text.
    assert [x[1][:6] for x in qa if x[0] == 'card-overflow'] == ['넘치는 설명'], qa


# 2026-10-16 PM thumbnail: the script wrote '외국인 사고 기관 던졌다\n바닥 신호일까?', but the page joined the two lines and
# balanced them as '외국인 사고 기관 / 던졌다 바닥 신호일까?', which first reads as '외국인 사고 기관' (a foreigners' accident office).
WORD_TOPS = r"""(words) => {const q=document.querySelector('.q'); const node=q.firstChild;
  return words.map(w=>{const i=node.textContent.indexOf(w); const rg=document.createRange(); rg.setStart(node,i);
    rg.setEnd(node,i+w.length); return Math.round(rg.getBoundingClientRect().top);});}"""


def test_the_thumbnail_keeps_the_line_break_the_script_wrote(tmp_path):
    words = "(['외국인', '던졌다', '바닥'])"
    found, box = thumbnail(tmp_path, '외국인 사고 기관 던졌다\n바닥 신호일까?', '장중 ▲4.60% → 종가 ▲2.05%',
                           probe=f'(() => ({WORD_TOPS})({words}))')
    first, thrown, second = box['probe']
    assert first == thrown < second, box['probe']
    assert box['lines'] == 2 and box['q']['b'] <= box['n']['t'] and found == [], box

"""Real Chromium layout of the card template with the cards that broke in the 2026-10-13 PM episode: counter blocks
pushed off screen by script x values, summary icons drawn at full size from size 'sm', a four-row signals card under
the captions and a compare arrow drawn over its own label. The card data below is that episode's final plan."""

import importlib.util
import json

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


def measure(tmp_path, plan, sels, *, captions=(), probe=None):
    """Builds the episode page like a render does and returns, per scene, the layout check and the element boxes."""
    if not importlib.util.find_spec('playwright'):
        pytest.skip('Install optional video dependencies and Chromium')
    from playwright.sync_api import sync_playwright

    # These cards carry no pictures: no asset library, speech or FFmpeg is needed to lay them out.
    renderer = MotionRenderer(Settings(video_artifact_dir=tmp_path, video_asset_dir=tmp_path), aligner=object(), library={})
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

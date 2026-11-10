"""The episode thumbnail designed for each episode (owner, 2026-11-06; video/thumbnail.py) and the version-3 title rule
(video/craft.py)."""

import importlib.util
import json
from pathlib import Path

import pytest

from quant_company.contracts import AgentDecision, ProviderResponse
from quant_company.video import thumbnail as thumbs
from quant_company.video.contracts import VisualReview
from quant_company.video.thumbnail import ThumbnailDesign

from .test_briefing import brief  # noqa: F401
from .test_video import body_source, good_review, video  # noqa: F401
from .test_video_motion import InspectedRenderer, episode, produce_inspected

PICTURE = [{'id': 'gen-1', 'name': 'pic-1.jpg', 'file': '/nonexistent/pic.jpg', 'kind': '자료사진', 'subject': '반도체',
            'width': 1600, 'height': 900}]
CSS = ('.bg{position:absolute;inset:0;background:url(pic-1.jpg) center/cover}'
       '.h{position:absolute;left:56px;top:140px;font:800 96px P;color:#fff}'
       '.n{position:absolute;left:56px;top:300px;font:800 88px B;color:#FF4D4D}')
HTML = '<div class="bg"></div><div class="h">반도체가 끌었다</div><div class="n">▲1.92%</div>'
# The episode fixture offers no picture file: its designs draw on a gradient.
PLAIN = ('.bg{position:absolute;inset:0;background:linear-gradient(120deg,#08142f,#203a7a)}'
         + CSS[CSS.index('.h{'):])


def design(html=HTML, css=CSS, **extra):
    return ThumbnailDesign(idea='반도체 주도 상승을 숫자 하나로', html=html, css=css, **extra)


def needs_browser():
    if not (importlib.util.find_spec('playwright') and importlib.util.find_spec('PIL')):
        pytest.skip('drawing needs the video extra (Playwright and Pillow)')


@pytest.mark.parametrize('html, css, refused', [
    ('<div>코스피</div><script>alert(1)</script>', CSS, '<script>'),
    ('<img src="pic-1.jpg" onerror="x()">', CSS, 'onerror'),
    ('<img src="https://example.com/a.jpg">', CSS, 'src='),
    ('<img src="../secret.png">', CSS, 'src='),
    ('<div style="background:url(http://x/y.png)">a</div>', CSS, 'load'),
    (HTML, CSS + "@font-face{font-family:X;src:url(x.woff2)}", 'At-rules'),
    (HTML, CSS + ".h::after{content:'매수하세요'}", 'Generated text'),
    (HTML, CSS + "</style><script>x()</script><style>", 'Markup'),
    (HTML, CSS + ".x{background:url(other.jpg)}", 'url(other.jpg)'),
    (HTML, CSS + ".x{background:u\\72l(pic-1.jpg)}", 'load or run'),
    ('<a href="https://x">a</a>', CSS, '<a>'),
    ('<svg xmlns="http://evil.example/ns"><path d="M0 0"/></svg>', CSS, 'xmlns'),
    # Read differently by Chromium and by the parser, or drawing words no text node holds (review of PR #226).
    ('<!--><img src="/etc/host.png"><!-- -->', CSS, 'Comments and declarations'),
    ('<![CDATA[ > <img src="/x.png"> ]]>', CSS, 'Comments and declarations'),
    ('<div class="h">반도체</div></div><div class="x">폭락 99%</div>', CSS, 'Unbalanced markup'),
    ('<div class="h">반도체', CSS, 'is never closed'),
    ('<img src="pic-1.jpg" alt="지금 매수">', CSS, 'Attribute not allowed in the thumbnail: alt'),
    (HTML, CSS + '.h::after{content/**/:" 지금 매수 99%"}', 'Comments are not allowed'),
    (HTML, CSS + '.h{display:list-item;list-style-type:"지금 매수 ";list-style-position:inside}', 'List markers'),
])
def test_a_design_that_could_load_run_or_hide_anything_is_refused(html, css, refused):
    _, problems = thumbs.markup(design(html, css), PICTURE)
    assert any(refused in p for p in problems), problems


def test_a_clean_design_lists_its_words_and_may_draw_shapes():
    html = HTML + ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10" class="a"><path d="M0 0L10 10" fill="url(#g)"/>'
                   '<text x="1" y="9">S&amp;P</text></svg>')
    texts, problems = thumbs.markup(design(html, CSS + '.a{align-content:center}'), PICTURE)
    assert problems == [] and texts == ['반도체가 끌었다', '▲1.92%', 'S&P']
    _, problems = thumbs.markup(design(picture='gen-2'), PICTURE)
    assert problems == ['Picture gen-2 was not offered']


def test_the_words_follow_the_episodes_number_size_and_advice_rules():
    source = body_source()
    assert thumbs.fact_problems(['반도체가 끌었다', 'S&P 500 ▲1.92%'], source) == []
    found = thumbs.fact_problems(['S&P 500 2% 폭등', '지금 사세요'], source)
    assert any('Number the lines do not contain: 2 ' in f for f in found)  # 1.92% rounded is not in the lines
    assert "'폭등' only when the lines say so" in found and 'Buy/sell instruction wording' in found
    assert thumbs.fact_problems(['반도체 급락'], source) == [
        "'급락' needs an index fall of at least 2% or a stock fall of at least 3% in the lines"]
    assert thumbs.fact_problems(['AI 생성 화면'], source) == ['Production or AI wording on the thumbnail']


def test_measurements_flag_unreadable_cut_off_and_bar_covered_words():
    drawn = [{'text': '반도체가 끌었다', 'hidden': False, 'rects': [[56, 140, 900, 280]]},
             {'text': '작은 글씨', 'hidden': False, 'rects': [[56, 400, 200, 420]]},
             {'text': '모서리', 'hidden': False, 'rects': [[1150, 650, 1270, 700]]},
             {'text': '밖으로', 'hidden': False, 'rects': [[1200, 100, 1400, 200]]},
             {'text': '숨김', 'hidden': True, 'rects': []}]
    texts, problems = thumbs.measure_problems(drawn)
    assert texts == ['반도체가 끌었다', '작은 글씨', '모서리', '밖으로']
    assert 'Hidden text in the thumbnail' in problems
    assert "Words too small to read in the feed (20 px tall): '작은 글씨'" in problems
    # YouTube's duration badge sits on the channel bar (2026-11-10), so words there are under the bar.
    assert "Words run off the thumbnail or under the channel bar: '모서리'" in problems
    assert "Words run off the thumbnail or under the channel bar: '밖으로'" in problems
    _, problems = thumbs.measure_problems([{'text': '작다', 'hidden': False, 'rects': [[0, 0, 100, 60]]}])
    assert problems == ['No line big enough to read at feed size (largest 60 px; needs 90)']
    _, problems = thumbs.measure_problems([{'text': '가' * 27, 'hidden': False, 'rects': [[0, 0, 1000, 110]]}])
    assert problems == ['Too many words for a thumbnail (27 characters; at most 26)']


def test_measurements_flag_shouting_words_and_a_second_number():
    # Owner, 2026-11-09, on the first designed thumbnails (150-240 px numbers, two numbers joined by an arrow):
    # "글자랑 숫자가 너무 크고 … 구식 같아 보여".
    _, problems = thumbs.measure_problems([{'text': '서킷브레이커', 'hidden': False, 'rects': [[56, 100, 830, 300]]},
                                           {'text': '▼6.02%', 'hidden': False, 'rects': [[300, 320, 980, 600]]}])
    assert problems == ["Words too big: 200 px tall, at most 120 (about a 100 px font): '서킷브레이커'",
                        "Words too big: 280 px tall, at most 120 (about a 100 px font): '▼6.02%'"]
    _, problems = thumbs.measure_problems([{'text': '나스닥 ▼5.5%', 'hidden': False, 'rects': [[56, 60, 700, 170]]},
                                           {'text': '▲1.09%', 'hidden': False, 'rects': [[760, 60, 1100, 170]]}])
    assert problems == ['More than one number on the thumbnail (5.5, 1.09): keep the one that is the story']
    texts, problems = thumbs.measure_problems([{'text': '반등은 했는데', 'hidden': False, 'rects': [[56, 380, 700, 494]]},
                                               {'text': '코스피 ▼6.02%', 'hidden': False, 'rects': [[56, 520, 500, 566]]}])
    assert problems == [] and texts == ['반등은 했는데', '코스피 ▼6.02%']


def test_a_design_is_drawn_with_the_service_fonts_its_picture_and_the_owners_red_strip(tmp_path):
    needs_browser()
    from PIL import Image

    picture = tmp_path / 'source.jpg'
    Image.new('RGB', (1600, 900), (20, 60, 120)).save(picture)
    offered = [{**PICTURE[0], 'file': str(picture)}]
    drawn = thumbs.draw(design(), offered, tmp_path / 'out', '1')
    assert drawn['problems'] == [] and drawn['texts'] == ['반도체가 끌었다', '▲1.92%']
    with Image.open(drawn['file']) as image:
        assert image.size == (1280, 720)
        assert image.getpixel((40, 715))[:3] == (255, 0, 0)  # the red part of the strip, a tenth of the width
        assert image.getpixel((40, 700))[:3] != (255, 0, 0) and image.getpixel((140, 715))[:3] != (255, 0, 0)
        assert image.getpixel((640, 715))[0] > 100 and image.getpixel((640, 715))[1] > 100  # the grey rest
        assert max(abs(a - b) for a, b in zip(image.getpixel((1200, 60))[:3], (20, 60, 120), strict=True)) <= 3  # the picture loaded
    small = design(html='<div class="h">작다</div>', css='.h{position:absolute;left:10px;top:10px;font:700 20px P}')
    assert any('too small' in p for p in thumbs.draw(small, offered, tmp_path / 'out', '2')['problems'])
    # The strip is painted on the picture: no style of the design can move or hide it.
    moved = thumbs.draw(design(css=CSS + 'div{top:0!important}#strip{display:none}'), offered, tmp_path / 'out', '3')
    with Image.open(moved['file']) as image:
        assert image.getpixel((40, 715))[:3] == (255, 0, 0)
    # Words drawn without a text node are found when the page is drawn as well.
    marker = thumbs.draw(design(css=CSS + '.n::before{content:"▲"}'.replace('content:', 'content :')), offered,
                         tmp_path / 'out', '4')
    assert 'Generated text (CSS content or list markers): every word must be in the markup' in marker['problems']
    plain = thumbs.draw(design(css=CSS + '.bg::after{content:"";position:absolute;inset:0}'), offered, tmp_path / 'out', '5')
    assert plain['problems'] == []


def test_every_design_carries_the_anything_story_bar_along_the_bottom(tmp_path):
    # Owner, 2026-11-10: "지금 제작 구조는 모두 유지하되 화면 하단에 옛날에 뭐든 story 로고 및 고정 표시줄이 있었는데
    # 그거만 다시 추가". The service paints the bar like the strip, so no style of the design can move or hide it.
    needs_browser()
    from PIL import Image

    picture = tmp_path / 'source.jpg'
    Image.new('RGB', (1600, 900), (200, 200, 200)).save(picture)
    offered = [{**PICTURE[0], 'file': str(picture)}]
    drawn = thumbs.draw(design(css=CSS + '#bar{display:none}div{z-index:99}'), offered, tmp_path / 'out', '1',
                        '아침 시장 브리핑')
    assert drawn['problems'] == [] and drawn['texts'] == ['반도체가 끌었다', '▲1.92%']  # the bar's words are the service's

    def near(pixel, colour):
        return max(abs(a - b) for a, b in zip(pixel[:3], colour, strict=True)) <= 12

    with Image.open(drawn['file']) as image:
        assert near(image.getpixel((640, 621)), (0xFD, 0xC7, 0x49))  # the yellow rule on top of the bar
        assert near(image.getpixel((1000, 650)), (0x00, 0x11, 0x2E)) and near(image.getpixel((1000, 690)), (0x00, 0x11, 0x2E))
        logo = [image.getpixel((x, y)) for x in range(56, 122) for y in range(636, 704)]
        assert sum(r > 200 and 150 < g < 230 and b < 140 for r, g, b in logo) > 300  # the yellow book of 뭐든story
        words = [image.getpixel((x, y)) for x in range(140, 420) for y in range(645, 695)]
        assert sum(min(p) > 200 for p in words) > 150  # '뭐든story' in white
        assert image.getpixel((40, 715))[:3] == (255, 0, 0)  # the red strip still runs over it
        assert near(image.getpixel((640, 300)), (200, 200, 200))  # the design above it is untouched
    low = design(html='<div class="bg"></div><div class="h">반도체가 끌었다</div>',
                 css=CSS.replace('top:140px', 'top:600px'))
    assert any('under the channel bar' in p for p in thumbs.draw(low, offered, tmp_path / 'out', '2')['problems'])


def test_the_design_leaves_the_bottom_to_the_anything_story_bar():
    for rule in ('뭐든story', 'bottom 100 px', 'y 620'):
        assert rule in thumbs.DESIGN_RULES, rule
    assert '오늘장' not in thumbs.DESIGN_RULES and '뭐든story bar' in thumbs.CHECK_RULES
    assert thumbs.bar_label({'source': {'edition_kind': 'am'}, 'policy': {}}) == '아침 시장 브리핑'
    assert thumbs.bar_label({'source': {'edition_kind': 'close'}, 'policy': {}}) == '마감 브리핑'
    # The logo is the bar's: a design may not draw it again (2026-11-10).
    for html, css in (('<img src="mark.png">', CSS), ('<div class="m"></div>', CSS + '.m{background:url(mark.png)}')):
        _, problems = thumbs.markup(design(HTML + html, css), PICTURE)
        assert any('may be loaded' in p for p in problems), problems
    _, problems = thumbs.measure_problems([{'text': '반도체가 끌었다', 'hidden': False, 'rects': [[56, 560, 700, 660]]}])
    assert problems == ["Words run off the thumbnail or under the channel bar: '반도체가 끌었다'"]


def test_best_keeps_the_design_with_the_fewest_findings_or_the_template():
    attempts = [{'key': 'thumb', 'file': 'a.png', 'sha256': 'a' * 64, 'problems': ['x', 'y'], 'done': True},
                {'key': 'thumb-2', 'problems': ['design unavailable: timeout'], 'done': True},
                {'key': 'thumb-3', 'file': 'c.png', 'sha256': 'c' * 64, 'problems': [], 'issues': ['face small → crop'],
                 'texts': ['코스피'], 'design': {'person': '파월'}, 'done': True}]
    assert thumbs.best(attempts) == {'key': 'thumb-3', 'file': 'c.png', 'sha256': 'c' * 64, 'texts': ['코스피'],
                                     'person': '파월', 'findings': ['thumbnail: face small → crop']}
    assert thumbs.best([attempts[1]])['template'] is True
    # A design whose words broke a rule of the lines never goes out, however few its other findings.
    wrong = {'key': 'thumb', 'file': 'a.png', 'sha256': 'a' * 64, 'facts': ['Number the lines do not contain: 2.5'],
             'problems': ['Number the lines do not contain: 2.5'], 'done': True}
    assert thumbs.best([wrong])['template'] is True
    assert thumbs.final(attempts[2], ['look skipped'])['findings'][-1] == 'thumbnail: look skipped'


def test_install_swaps_the_template_thumbnail_and_keeps_the_manifest_verifiable(tmp_path):
    from quant_company.video.contracts import digest
    from quant_company.video.render import checksum, verify_artifacts

    directory = tmp_path / 'render-1'
    directory.mkdir()
    (directory / 'thumbnail.png').write_bytes(b'template')
    (directory / 'video.mp4').write_bytes(b'video')
    manifest = {'directory': str(directory), 'qa_passed': True, 'findings': [thumbs.TEMPLATE_FINDING, 'other'],
                'files': {n: checksum(directory / n) for n in ('thumbnail.png', 'video.mp4')}}
    manifest['digest'] = digest(manifest)
    designed = tmp_path / 'designed.png'
    designed.write_bytes(b'designed')
    chosen = {'key': 'thumb-2', 'file': str(designed), 'sha256': checksum(designed), 'texts': ['코스피'], 'person': ''}
    installed = thumbs.install(manifest, chosen)
    assert (directory / 'thumbnail.png').read_bytes() == b'designed' and installed['findings'] == ['other']
    assert installed['thumbnail'] == {'designed': 'thumb-2', 'texts': ['코스피'], 'person': ''}
    assert verify_artifacts(installed, tmp_path) == directory
    assert json.loads((directory / 'manifest.json').read_text())['digest'] == installed['digest']
    # A file that is not the one that was checked never goes out.
    designed.write_bytes(b'changed')
    assert thumbs.install(installed, chosen) is installed
    assert thumbs.install(installed, {'template': True, 'findings': []}) is installed


class ThumbnailProvider:
    """The episode's turns (script, review, inspection) plus the thumbnail's design and look."""

    def __init__(self, designs, looks):
        self.designs, self.looks, self.calls = list(designs), list(looks), []

    async def run(self, request):
        self.calls.append(request)
        if request.output_contract == 'video_thumbnail_v1':
            value = self.designs.pop(0) if len(self.designs) > 1 else self.designs[0]
            if isinstance(value, Exception):
                raise value
        elif '-visual-thumb' in request.request_id:
            value = self.looks.pop(0) if len(self.looks) > 1 else self.looks[0]
        else:
            value = {'video_episode_v1': episode(), 'video_review_v1': good_review(),
                     'video_visual_review_v1': VisualReview(passed=True, issues=[])}[request.output_contract]
        return ProviderResponse(request_id=request.request_id, provider='claude', usage={'actual_model': request.model},
                                decision=AgentDecision(status='complete', say='',
                                                       artifacts=[{'title': 'Simulated', 'content': value.model_dump_json()}]))


def fake_draw(problems=()):
    def draw(self, design, offered, folder, name, label=''):
        from PIL import Image

        assert label in ('아침 시장 브리핑', '마감 브리핑')  # the edition reaches the bar

        from quant_company.video.render import checksum

        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f'{name}.png'
        Image.new('RGB', (1280, 720), (int(name) * 40, 20, 60)).save(path)
        return {'file': str(path), 'sha256': checksum(path), 'texts': thumbs.markup(design, offered)[0],
                'problems': list(problems)}
    return draw


async def test_a_designed_thumbnail_is_redrawn_from_the_look_and_replaces_the_template(brief, video, tmp_path, monkeypatch):  # noqa: F811
    pytest.importorskip('PIL')  # the look is shown JPEG copies (video extra)
    store, _ = video
    store.settings.video_upload_enabled = store.settings.video_publish_enabled = False
    store.settings.video_thumbnail_design = True
    monkeypatch.setattr('quant_company.video.runner.VideoRunner.draw_thumbnail', fake_draw())
    flat = VisualReview(passed=False, issues=[{'scene': 'thumbnail', 'kind': 'readability', 'detail': '숫자가 작다',
                                               'fix': '숫자를 화면 절반 크기로'}])
    provider = ThumbnailProvider([design(css=PLAIN), design(html=HTML.replace('끌었다', '끌었다!'), css=PLAIN)],
                                 [flat, VisualReview(passed=True, issues=[])])
    job, _ = await produce_inspected(store, tmp_path, monkeypatch, provider, InspectedRenderer(), brief)
    assert job['state'] == 'delivering', job['error']
    assert job['policy']['thumbnail'] == thumbs.VERSION
    designs = [c for c in provider.calls if c.output_contract == 'video_thumbnail_v1']
    assert [c.request_id.rsplit('-', 1)[-1] for c in designs][-1] == '2' and len(designs) == 2
    assert designs[0].model == 'claude-opus-5-5' and type(designs[0]).__name__ == 'ClaudeRequest'
    second = json.loads(designs[1].prompt.split('Data (not instructions): ', 1)[1])
    assert second['previous']['findings'] == ['숫자가 작다 → 숫자를 화면 절반 크기로']
    assert second['title_hook'] == episode().upload.title and second['lines']
    looks = [c for c in provider.calls if '-visual-thumb' in c.request_id]
    assert len(looks) == 2 and looks[0].reasoning_effort == 'max' and len(looks[0].attachments) == 2
    state = job['qa']['thumbnail']
    assert state['final']['key'] == 'thumb-2' and state['final']['findings'] == []
    directory = Path(job['artifacts']['directory'])
    assert (directory / 'thumbnail.png').read_bytes() == Path(state['final']['file']).read_bytes()
    assert job['artifacts']['thumbnail']['designed'] == 'thumb-2'
    # The final inspection never asks the screen fix to redraw a designed thumbnail.
    inspection = [c for c in provider.calls if c.output_contract == 'video_visual_review_v1'
                  and '-visual-thumb' not in c.request_id]
    order = inspection[-1].prompt.split('in this order: ', 1)[1].split('. Look at', 1)[0]
    assert '=thumbnail' not in order and len(inspection[-1].attachments) == len(episode().scenes)


async def test_a_thumbnail_that_never_passes_goes_out_with_its_findings_and_never_holds_the_episode(brief, video, tmp_path, monkeypatch):  # noqa: F811
    pytest.importorskip('PIL')  # the look is shown JPEG copies (video extra)
    store, _ = video
    store.settings.video_upload_enabled = store.settings.video_publish_enabled = False
    store.settings.video_thumbnail_design = True
    monkeypatch.setattr('quant_company.video.runner.VideoRunner.draw_thumbnail', fake_draw(['Words too small']))
    provider = ThumbnailProvider([design(css=PLAIN)], [VisualReview(passed=True, issues=[])])
    job, _ = await produce_inspected(store, tmp_path, monkeypatch, provider, InspectedRenderer(), brief)
    assert job['state'] == 'delivering', job['error']
    assert len([c for c in provider.calls if c.output_contract == 'video_thumbnail_v1']) == thumbs.DESIGNS
    assert not [c for c in provider.calls if '-visual-thumb' in c.request_id]  # a measured finding is redrawn first
    assert job['qa']['thumbnail']['final']['findings'] == ['thumbnail: Words too small']
    assert 'thumbnail: Words too small' in job['qa']['inspection_findings']


async def test_a_job_without_the_design_keeps_the_template_thumbnail(brief, video, tmp_path, monkeypatch):  # noqa: F811
    store, _ = video
    store.settings.video_upload_enabled = store.settings.video_publish_enabled = False
    provider = ThumbnailProvider([design()], [VisualReview(passed=True, issues=[])])
    job, _ = await produce_inspected(store, tmp_path, monkeypatch, provider, InspectedRenderer(), brief)
    assert job['state'] == 'delivering' and 'thumbnail' not in job['policy']
    assert not [c for c in provider.calls if c.output_contract == 'video_thumbnail_v1']
    assert 'thumbnail' not in job['artifacts']


async def test_the_script_sees_the_recent_hooks_it_must_not_echo(brief, video, tmp_path, monkeypatch):  # noqa: F811
    from quant_company.video.craft import CRAFT_V3, TITLE_V3

    from .test_video import new_job

    store, _ = video
    earlier = new_job(brief, video)
    with store.db.transaction() as conn:
        conn.execute("UPDATE video_jobs SET plan=%s, created_at=now()-interval '1 hour' WHERE id=%s",
                     (json.dumps({'upload': {'title': '코스피 5.28% 급락, 미국은 올랐는데 왜?'}}), earlier['id']))
    assert store.recent_hooks({'id': 'ffffffff-ffff-ffff-ffff-ffffffffffff'}) == ['코스피 5.28% 급락, 미국은 올랐는데 왜?']
    # The 10/22 example hooks are no longer in the rules the script follows.
    assert '코스피 3.29% 반등, 믿어도 될까' not in CRAFT_V3 and TITLE_V3 in CRAFT_V3
    assert 'Open with an index and its percent change only when that move is itself the story' in TITLE_V3


def test_claude_runtime_serves_the_thumbnail_contract_with_pictures():
    from pathlib import Path

    from pydantic import ValidationError

    from quant_company.contracts import ProviderAttachment
    from quant_company.providers.claude_runner import ClaudeConfig, ClaudeRequest, output_schema

    picture = ProviderAttachment(name='image-01.jpg', media_type='image/jpeg', data='AAAA')
    request = ClaudeRequest(request_id='video-00000000-0000-0000-0000-000000000000-thumb', model='claude-opus-5-5',
                            prompt='design', output_contract='video_thumbnail_v1', attachments=[picture])
    assert output_schema(request)[0] is ThumbnailDesign
    config = ClaudeConfig(auth_dir=Path('/auth'), jobs_dir=Path('/jobs'))
    assert config.process_limit(request) == config.video_timeout_seconds  # not the short email limit
    with pytest.raises(ValidationError):
        ClaudeRequest(request_id='newsletter-x', model='claude-opus-5-5', prompt='design',
                      output_contract='video_thumbnail_v1')


def test_analytics_reads_the_words_a_designed_thumbnail_showed():
    from quant_company.video.analytics import plan_summary

    plan = {'thumbnail': '반도체가 끌었다', 'thumbnail_stat': 'S&P 500 ▲ 1.92%', 'scenes': []}
    assert plan_summary({'plan': plan})['thumbnail_text'] == '반도체가 끌었다 / S&P 500 ▲ 1.92%'
    designed = {'plan': plan, 'artifacts': {'thumbnail': {'designed': 'thumb', 'texts': ['이 사람 당선에', '−5.28%']}}}
    assert plan_summary(designed)['thumbnail_text'] == '이 사람 당선에 / −5.28%'


async def test_a_design_turn_asked_again_sends_the_same_request_and_the_stage_keeps_to_its_time(brief, video, tmp_path, monkeypatch):  # noqa: F811
    pytest.importorskip('PIL')
    from quant_company.contracts import ProviderFault

    store, _ = video
    store.settings.video_upload_enabled = store.settings.video_publish_enabled = False
    store.settings.video_thumbnail_design = True
    monkeypatch.setattr('quant_company.video.runner.VideoRunner.draw_thumbnail', fake_draw())
    pending = ProviderFault('pending', 'The model turn is still running.', 30)
    provider = ThumbnailProvider([pending, design(css=PLAIN)], [VisualReview(passed=True, issues=[])])
    job, _ = await produce_inspected(store, tmp_path, monkeypatch, provider, InspectedRenderer(), brief)
    assert job['state'] == 'delivering', job['error']
    first, again = [c for c in provider.calls if c.output_contract == 'video_thumbnail_v1']
    assert first.request_id == again.request_id and first.model_dump() == again.model_dump()
    assert job['qa']['thumbnail']['final']['key'] == 'thumb' and 'started' in job['qa']['thumbnail']


async def test_a_stage_out_of_time_sends_the_template_and_never_holds_the_episode(brief, video, tmp_path, monkeypatch):  # noqa: F811
    store, _ = video
    store.settings.video_upload_enabled = store.settings.video_publish_enabled = False
    store.settings.video_thumbnail_design = True
    monkeypatch.setattr(thumbs, 'DESIGN_MINUTES', -1)
    provider = ThumbnailProvider([design(css=PLAIN)], [VisualReview(passed=True, issues=[])])
    job, _ = await produce_inspected(store, tmp_path, monkeypatch, provider, InspectedRenderer(), brief)
    assert job['state'] == 'delivering', job['error']
    assert not [c for c in provider.calls if c.output_contract == 'video_thumbnail_v1']
    assert job['qa']['thumbnail']['final']['template'] is True and 'thumbnail' not in job['artifacts']


def test_the_design_and_the_look_ask_for_a_current_editorial_thumbnail():
    # Owner, 2026-11-09: "글자랑 숫자가 너무 크고, 제목과 문구도 이상해 … 보고 싶게끔 … 구식 같아 보여".
    for rule in ('76 to 100 px', 'At most one number', 'no highlighter box', 'everyday Korean',
                 'not a recap of figures', 'weekday', '구식 같아'):
        assert rule in thumbs.DESIGN_RULES, rule
    for issue in ('shout', 'more than one number', 'dated', 'jargon', 'reason to watch'):
        assert issue in thumbs.CHECK_RULES, issue
    assert "a number's size" not in thumbs.CHECK_RULES  # the look may now ask for smaller numbers

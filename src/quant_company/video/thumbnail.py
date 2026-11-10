"""The episode thumbnail, designed for each episode (owner, 2026-11-06).

Briefing thumbnails ran at about a 2.1% click-through rate, every one in the same navy frame with a faded photo, and 30
of 40 titles opened with an index and its percent change. The owner asked for the opposite of a rotation of frames:
"썸네일과 문구와 제목만으로 현재 시장에 아주 적합한 것을 노출함으로써 … 무조건 클릭하고 싶게끔 … 핵심에 딱 맞아야",
and "인물이 중요 이슈였던 날은 얼굴이 크게 나와야".

So Claude composes each thumbnail as a small HTML/CSS page from the final title, the brief's lines and the episode's
pictures. The service checks the page (only its fonts, the brand mark and the offered pictures; no script, link or
outside file; every number from the lines; the '급락', '폭락', '장중' and no-advice rules), draws it with Chromium on this
host, measures the text (readable in the 168-pixel suggestions column, inside the frame, clear of YouTube's duration
badge) and has Claude look at it beside the latest thumbnails. A finding is fixed by another design; after the last one
the design with the fewest findings goes out with them, and when none could be drawn the template thumbnail does. The
drawn file replaces the template thumbnail in the render's artifacts (install), so the render task never changes:
nothing the render imports imports this module (deploy/release.py render_changes)."""

import base64
import io
import json
import os
import re
import shutil
from html.parser import HTMLParser
from importlib.resources import files
from pathlib import Path

from pydantic import Field

from ..contracts import ProviderAttachment, StrictModel
from .upload import SERIES

VERSION = 1  # policy['thumbnail'] of a job whose thumbnail is designed (VideoStore.enqueue)
CONTRACT = 'video_thumbnail_v1'
DESIGNS = 3  # designs per episode; the fewest findings go out after the last
WIDTH, HEIGHT = 1280, 720
# The owner's red watched-progress strip (2026-10-12) along the bottom edge, its red part a tenth of the width since
# 2026-11-05 (design/thumbnail.html). The service draws it over every design.
STRIP, STRIP_SHARE = 12, 0.10
# 뭐든story's fixed bar along the bottom (owner, 2026-11-10: "화면 하단에 옛날에 뭐든 story 로고 및 고정 표시줄이 있었는데
# 그거만 다시 추가"), as design/thumbnail.html draws it: the logo, the name and the edition on navy under a yellow rule.
# The service draws it over every design, so the design fills the 1280x620 above it. YouTube's duration badge sits on it.
BAR = 100
FEED = (168, 94)  # the suggested-videos column
MIN_LINE, MIN_HEADLINE, MAX_CHARS = 30, 90, 26  # drawn line heights in pixels; visible characters
# Owner, 2026-11-09, on the first designs (150-240 px numbers): "글자랑 숫자가 너무 크고". A line is drawn about 1.2
# times its font size tall, so 120 px is about a 100 px font.
MAX_LINE = 120
NUMBER = re.compile(r'\d[\d,]*(?:\.\d+)?')
TEMPLATE_FINDING = 'Thumbnail text exceeds safe screen area'  # the template thumbnail's own check (motion._thumbnail)
FONTS = {'P': {500: 'Pretendard-Medium', 600: 'Pretendard-SemiBold', 700: 'Pretendard-Bold', 800: 'Pretendard-ExtraBold'},
         'B': {600: 'BarlowCondensed-SemiBold', 700: 'BarlowCondensed-Bold', 800: 'BarlowCondensed-ExtraBold'}}
MARK = 'mark.png'  # the channel logo (design/logo_mark.png) on the bar; a design may not load it
VOID = frozenset({'br', 'img'})
DESIGN_MINUTES = 25  # after this, no new design or look starts: the fewest findings go out (production never waits)


class ThumbnailDesign(StrictModel):
    """One episode's thumbnail as a page: html is the content of a 1280x720 canvas and css styles it."""
    idea: str = Field(min_length=3, max_length=300)
    person: str = Field(default='', max_length=60)
    picture: str = Field(default='', max_length=40)
    html: str = Field(min_length=10, max_length=8000)
    css: str = Field(min_length=10, max_length=12000)


TAGS = frozenset({'div', 'span', 'p', 'b', 'strong', 'em', 'i', 'u', 's', 'small', 'sup', 'sub', 'br', 'img', 'svg', 'g',
                  'path', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon', 'text', 'tspan', 'defs',
                  'lineargradient', 'radialgradient', 'stop', 'clippath', 'mask', 'filter', 'fegaussianblur',
                  'feoffset', 'feflood', 'fecomposite', 'femerge', 'femergenode', 'fedropshadow', 'fecolormatrix'})
ATTRS = frozenset({'class', 'id', 'style', 'src', 'viewbox', 'xmlns', 'd', 'fill', 'stroke', 'stroke-width',
                   'stroke-linecap', 'stroke-linejoin', 'stroke-dasharray', 'fill-opacity', 'stroke-opacity', 'opacity',
                   'x', 'y', 'x1', 'y1', 'x2', 'y2', 'cx', 'cy', 'r', 'rx', 'ry', 'fx', 'fy', 'width', 'height',
                   'points', 'transform', 'offset', 'stop-color', 'stop-opacity', 'gradientunits', 'gradienttransform',
                   'spreadmethod', 'preserveaspectratio', 'clip-path', 'mask', 'filter', 'font-size', 'font-weight',
                   'font-family', 'text-anchor', 'dominant-baseline', 'letter-spacing', 'dx', 'dy', 'stddeviation',
                   'flood-color', 'flood-opacity', 'in', 'in2', 'result', 'operator', 'values', 'type',
                   'clippathunits', 'maskunits', 'filterunits', 'fill-rule', 'clip-rule', 'paint-order'})
# Anything that could load, run or hide something the checks do not see.
UNSAFE = re.compile(r'javascript:|vbscript:|data:|https?:|//|expression\(|-moz-binding|behavior\s*:|image-set|'
                    r'element\(|cross-fade|\\', re.I)
URL = re.compile(r'url\(\s*([\'"]?)(.*?)\1\s*\)', re.I | re.S)
CONTENT = re.compile(r'(?<![\w-])content\s*:\s*(?!(?:""|\'\'|none|normal)\s*(?:;|}|!|$))', re.I)
SVG_NAMESPACE = 'http://www.w3.org/2000/svg'


class Page(HTMLParser):
    """The design's markup as the checks see it: refused tags and attributes, the files it loads and its text."""

    def __init__(self, allowed):
        super().__init__(convert_charrefs=True)
        self.allowed, self.problems, self.texts, self.open = allowed, [], [], []

    def handle_starttag(self, tag, attrs):
        self.check(tag, attrs)
        if tag not in VOID:
            self.open.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.check(tag, attrs)

    def handle_endtag(self, tag):
        # One closing tag too many would close the service's canvas and leave the rest outside every check.
        if self.open and self.open[-1] == tag:
            self.open.pop()
        else:
            self.problems.append(f'Unbalanced markup: </{tag}> does not close the open <{self.open[-1] if self.open else "canvas"}>')

    def check(self, tag, attrs):
        if tag not in TAGS:
            self.problems.append(f'Tag not allowed in the thumbnail: <{tag}>')
            return
        for name, value in attrs:
            value = value or ''
            if name not in ATTRS:
                self.problems.append(f'Attribute not allowed in the thumbnail: {name}')
            elif name == 'src':
                if tag != 'img' or value not in self.allowed:
                    self.problems.append(f'Only the offered pictures may be loaded (src={value[:40]})')
            elif name == 'style':
                self.problems += css_problems(value, self.allowed)
            elif name == 'xmlns':
                if value != SVG_NAMESPACE:
                    self.problems.append(f'Value not allowed in the thumbnail (xmlns={value[:40]})')
            elif UNSAFE.search(value) or ('url(' in value.lower() and not re.fullmatch(r'\s*url\(#[\w-]+\)\s*', value)):
                self.problems.append(f'Value not allowed in the thumbnail ({name}={value[:40]})')

    def handle_data(self, data):
        text = re.sub(r'\s+', ' ', data).strip()
        if text:
            self.texts.append(text)


def css_problems(css, allowed):
    """Styles may use the service's fonts, the offered pictures and the page's own ids, and nothing else."""
    found = []
    if '<' in css:
        found.append('Markup is not allowed in the thumbnail styles')
    if '/*' in css:
        found.append('Comments are not allowed in the thumbnail styles')
    if re.search(r'list-style|list-item|counter', css, re.I):
        found.append('List markers and counters are not allowed: every word must be in the markup')
    if '@' in css:
        found.append('At-rules are not allowed in the thumbnail styles (the fonts are provided)')
    if UNSAFE.search(URL.sub('', css)):
        found.append('Styles may not load or run anything')
    for _, target in URL.findall(css):
        if target not in allowed and not re.fullmatch(r'#[\w-]+', target):
            found.append(f'Only the offered pictures may be loaded (url({target[:40]}))')
    if CONTENT.search(css):
        found.append('Generated text (CSS content) is not allowed: every word must be in the markup')
    return found


def markup(design, offered):
    """(texts, problems) of a design's page before it is drawn; a design with any problem here is never drawn."""
    allowed = {p['name'] for p in offered}  # the logo (MARK) is the bar's alone
    parser = Page(allowed)
    parser.feed(design.html)
    parser.close()
    problems = list(parser.problems)
    if re.search(r'<[!?]', design.html):
        # Comments, declarations and CDATA are read differently by Chromium and by this parser.
        problems.append('Comments and declarations are not allowed in the thumbnail markup')
    if parser.open:
        problems.append(f'Unbalanced markup: <{parser.open[-1]}> is never closed')
    problems += css_problems(design.css, allowed)
    if design.picture and design.picture not in {p['id'] for p in offered}:
        problems.append(f'Picture {design.picture} was not offered')
    return parser.texts, list(dict.fromkeys(problems))


def fact_problems(texts, source):
    """The episode's own rules for the words on its thumbnail: numbers from the lines, size words, intraday values and
    no advice or production wording (video/episode.py check_title and validate_episode)."""
    from .contracts import claim_catalog
    from .episode import INDEX_WORDS, drops, frame, nums, sentences
    from .upload import INSTRUCTION, TOOL_WORDING

    lines = [c['text'] for c in claim_catalog(source).values()]
    joined = ' '.join(texts)
    found = []
    stray = nums(joined) - (nums(' '.join(lines)) | nums(frame(source)))
    if stray:
        found.append(f'Number the lines do not contain: {", ".join(sorted(stray))} (write numbers exactly as the lines do)')
    if '급락' in joined and not any(any(d >= (2 if INDEX_WORDS.search(line) else 3) for d in drops(line)) for line in lines):
        found.append("'급락' needs an index fall of at least 2% or a stock fall of at least 3% in the lines")
    for word in ('폭락', '폭등'):
        if word in joined and not any(word in line for line in lines):
            found.append(f"'{word}' only when the lines say so")
    for number in nums(joined):
        holders = [s for line in lines for s in sentences(line) if number in nums(s)]
        if holders and all('장중' in s for s in holders) and '장중' not in joined:
            found.append(f"An intraday value needs '장중' ({number})")
    if INSTRUCTION.search(joined):
        found.append('Buy/sell instruction wording')
    if TOOL_WORDING.search(joined) or 'AI 생성' in joined:
        found.append('Production or AI wording on the thumbnail')
    return found


def pictures(job, plan, settings, library):
    """The pictures a design may use, each with its file on this host: the plan's thumbnail picture first, then the
    episode's other story pictures (found or made for it, so never a recent thumbnail's)."""
    from PIL import Image

    from .illustrations import library_entries

    entries = library_entries(job, plan, settings, library)
    ids = [plan.thumbnail_image] if getattr(plan, 'thumbnail_image', '') else []
    ids += [i.id for i in getattr(plan, 'illustrations', []) if i.id not in ids and i.id in entries
            and Path(entries[i.id]['file']).is_absolute()]
    out = []
    for asset in ids:
        entry = entries.get(asset) or library.get(asset)
        if not entry or not entry.get('file') or len(out) >= 3:
            continue
        path = Path(entry['file'])
        path = path if path.is_absolute() else Path(settings.video_asset_dir) / path
        if not path.is_file():
            continue
        with Image.open(path) as image:
            width, height = image.size
        subject = entry.get('subject') or next((i.subject for i in getattr(plan, 'illustrations', []) if i.id == asset),
                                               asset)
        out.append({'id': asset, 'name': f'pic-{len(out) + 1}{path.suffix.lower() or ".jpg"}', 'file': str(path),
                    'kind': entry.get('kind', ''), 'subject': subject, 'width': width, 'height': height})
    return out


def jpeg(path_or_image, edge, quality=86):
    """A picture for Claude to look at: JPEG, at most `edge` pixels wide, base64."""
    from PIL import Image

    if isinstance(path_or_image, Image.Image):
        image = path_or_image.convert('RGB')
    else:
        with Image.open(path_or_image) as opened:
            image = opened.convert('RGB')
    if image.width > edge:
        image = image.resize((edge, round(image.height * edge / image.width)), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format='JPEG', quality=quality)
    return base64.b64encode(buffer.getvalue()).decode()


def feed_view(path):
    """The thumbnail as the suggestions column shows it (168 px wide), enlarged twice so the look sees the pixels."""
    from PIL import Image

    with Image.open(path) as image:
        small = image.convert('RGB').resize(FEED, Image.LANCZOS)
    return small.resize((FEED[0] * 2, FEED[1] * 2), Image.NEAREST)


def attach(name, data):
    return ProviderAttachment(name=name, media_type='image/jpeg', data=data)


def recent_attachments(recent, start):
    out = []
    for n, path in enumerate(recent, start):
        if Path(path).is_file():
            out.append((f'recent-{n - start + 1}', attach(f'image-{n:02}.jpg', jpeg(path, 480, 80))))
    return out


def lines_of(source):
    from .contracts import claim_catalog

    return [c['text'] for c in claim_catalog(source).values()]


def edition_words(job):
    kind = job['source'].get('edition_kind') or job['policy'].get('edition') or ''
    return {'am': '아침', 'pm': '마감', 'close': '마감'}.get(kind, kind)


def bar_label(job):
    """The edition on the bar, as the episode's top bar and the template thumbnail name it."""
    from .motion import EDITIONS

    return EDITIONS['am'] if edition_words(job) == '아침' else EDITIONS['close']


DESIGN_RULES = """You design the YouTube thumbnail of one episode of 「오늘의 증시story」, the Korean stock-market
briefing of the channel 뭐든story, for this episode alone. The owner's words (2026-11-06): "정해진 제한된 수의 틀 안에서 돌려쓰는 것보다도 썸네일과
문구와 제목만으로 현재 시장에 아주 적합한 것을 노출함으로써 … 이목과 어그로를 끌면서 무조건 클릭하고 싶게끔 … 핵심에 딱
맞아야" and "인물이 중요 이슈였던 날은 얼굴이 크게 나와야". After the first designed thumbnails (2026-11-09): "글자랑 숫자가
너무 크고, 제목과 문구도 이상해 … 유튜브 시청자들이 우리 영상을 보고 싶게끔 … 디자인도 별로고 내용도 별로야. 뭔가 구식 같아
보여". Those shouted: 150-240 px numbers, two numbers joined by an arrow, words in yellow and black highlighter boxes, a
generic product shot, and words that recapped figures ('나스닥 · 목요일 ▼5.5% → 금요일 ▲1.09%') instead of giving a
reason to watch. Before them, every thumbnail sat in the same navy frame with a faded photo and was rarely clicked.

The look: a current, editorial thumbnail, as a premium economy channel makes it today, not a 2010s clickbait one. One
full-bleed picture of the story's real subject (a face when a person is the story; otherwise the company, product, place
or scene the story is about, never a generic stock image when a story picture is offered), in natural colour, sharp,
cropped so the subject is large, with the words on calm space beside it. A soft dark gradient only where the words sit;
no frame, no panel, no sticker, no highlighter box behind words, no clip-art arrow, no bevel, glow or heavy outline (a
soft shadow is enough). White words and at most one accent colour. Margins of at least 56 px.

The words: one headline of one or two short lines, in everyday Korean a casual investor gets at a glance, that names
the stake or the open question this video answers (why should I care today?). It is not a recap of figures, not a date
or weekday, not the title again, and no jargon (잉여현금흐름, 경상수지, 담보비율 …) unless the story is that word, then
said in plain words. Optionally one short kicker line above or below it: a person's name and role, or the one number.
At most one number on the thumbnail. Sizes: the headline 76 to 100 px (Pretendard 800), the number no bigger than the
headline, a kicker 30 to 40 px; nothing bigger than 100 px and nothing under 26 px. Keep the main message to about 14
characters, and at most 26 visible characters in all. The thumbnail and the title say different things: one shows who
or what is at stake, the other what happened. Make it clearly unlike the recent thumbnails attached (picture, colour,
layout). Korean convention: red for a rise and blue for a fall, for the number only. No date badge and no channel name
or logo of your own: the service draws 뭐든story's fixed bar (its logo, its name and the edition) across the bottom 100 px
of every thumbnail, from y 620 down. Design the 1280x620 above it: every word, the face and the key object stay above
y 620 (words below it are refused), and the picture may run under the bar.

When a person is the day's key issue and their photo is offered, the face fills a large part of the frame (at least 40%
of its height), sharp and never covered by words; caption the person with name and role from the lines when Korean
viewers may not know them. Crop the picture with CSS (background-size and background-position, or object-fit and
object-position on an img) using its pixel size given below.

Facts bind as in the script: every number exactly as the lines write it (a rounded or computed value is a number the
lines do not contain and is refused); '급락' only for an index fall of at least 2% or a stock fall of at least 3% in the
lines; '폭락' or '폭등' only when the lines say so; '장중' for an intraday value; never buy, sell, target-price or
guaranteed-return words; no 'AI' or production wording. A sharp hook is welcome; a claim the lines do not support is not.
A picture made for the episode carries no 'AI 생성' label, and a real photo needs no credit on the thumbnail (the
description carries it).

The page: html is the content of a 1280x720 canvas (position: relative, overflow hidden, black background) and css
styles it. Fonts: 'P' (Pretendard 500/600/700/800, for Korean) and 'B' (Barlow Condensed 600/700/800, for numerals).
Pictures: the file names below, by url(<name>) in css or <img src="<name>">. The channel logo is the bar's alone. Inline SVG may
draw simple shapes. Not allowed and refused: scripts, event handlers, links, any other file or URL, at-rules
(@font-face, @import, @media), backslashes, and CSS content text (every word must be in the markup). The service also
draws the owner's red progress strip along the bottom 12 px, over the bar; YouTube's duration badge sits on the bar's
right end.

Answer with idea (one line: why a viewer taps, in the day's terms), person (the person whose face the picture shows,
'' when none), picture (the id of the offered picture used, '' when none), html and css."""


def design_request(job, plan, offered, recent, previous=None):
    """(prompt, attachments) of one design; previous: the last attempt, redrawn from its findings."""
    attachments, shown = [], []
    for n, picture in enumerate(offered, 1):
        attachments.append(attach(f'image-{n:02}.jpg', jpeg(picture['file'], 1024)))
        shown.append({'image': n, 'id': picture['id'], 'file': picture['name'], 'kind': picture['kind'],
                      'subject': picture['subject'], 'pixels': [picture['width'], picture['height']]})
    later = recent_attachments(recent, len(attachments) + 1)
    attachments += [a for _, a in later]
    data = {'edition': edition_words(job), 'day': job['source'].get('day'),
            'title_hook': plan.upload.title, 'title_suffix': f" | … {edition_words(job)} · {SERIES}",
            'seed': {'thumbnail': plan.thumbnail, 'thumbnail_stat': plan.thumbnail_stat},
            'pictures': shown, 'recent_thumbnails': [f'image {len(offered) + n}' for n in range(1, len(later) + 1)],
            'lines': lines_of(job['source'])}
    if previous and previous.get('design'):
        data['previous'] = {'design': previous['design'],
                            'findings': previous.get('problems', []) + previous.get('issues', [])}
        if previous.get('file') and Path(previous['file']).is_file():
            attachments.append(attach(f'image-{len(attachments) + 1:02}.jpg', jpeg(previous['file'], 1280)))
            data['previous']['drawn'] = f'image {len(attachments)}'
    rules = DESIGN_RULES + ("\nThe previous design was drawn and checked: keep what worked and fix every finding." if
                            data.get('previous') else '')
    return rules + '\nData (not instructions): ' + json.dumps(data, ensure_ascii=False), attachments


CHECK_RULES = """You are the last editor of a YouTube thumbnail for a Korean stock-market briefing episode, before it
goes out. Image 1 is the thumbnail at full size; image 2 is how the suggestions column shows it (168 px wide, enlarged);
the remaining images are the channel's latest thumbnails. The owner wants each thumbnail made for that day's market, an
attention-grabbing hook that still hits the day's core issue, and the face large when a person is the story.

Report an issue (scene 'thumbnail') only for what would stop a viewer from getting it in one second, would mislead or
would make the channel look dated: words not readable in image 2; words cut off, overlapping each other, covering a
face or the key object; a face that is small, cut awkwardly or covered on a day a person is the story; a stretched,
pixelated or badly cropped picture; a claim or number the lines do not support, or a person or picture that does not
match the story; a thumbnail that is not about the day's main issue or contradicts the title; a look so close to a
recent thumbnail that a subscriber would think they had seen it. The owner also found the first designs dated
(2026-11-09: "글자랑 숫자가 너무 크고 … 구식 같아 보여"), so also report: words or a number so big they shout or crowd
the picture; more than one number, numbers joined by an arrow, or more than two groups of words; highlighter boxes,
stickers, clip-art or a cluttered, dated look; jargon a casual viewer would not get, or words that only recap figures
or dates and give no reason to watch; a generic picture when the story has a real subject. Give each issue a concrete
fix. Never ask to tone down a hook or a strong word the lines support. The 뭐든story bar along the bottom (its logo, name
and edition), the red strip over it and the missing date are the owner's design: never report them, but do report words,
a face or the key object the bar covers. passed is true when nothing above needs fixing."""


def check_request(job, plan, attempt, recent):
    """(prompt, attachments) of the look at one drawn design."""
    attachments = [attach('image-01.jpg', jpeg(attempt['file'], 1280, 90)), attach('image-02.jpg', jpeg(feed_view(
        attempt['file']), 400, 92))]
    attachments += [a for _, a in recent_attachments(recent, 3)]
    data = {'title_hook': plan.upload.title, 'edition': edition_words(job), 'texts': attempt.get('texts', []),
            'person': (attempt.get('design') or {}).get('person', ''), 'idea': (attempt.get('design') or {}).get('idea'),
            'lines': lines_of(job['source'])}
    return CHECK_RULES + '\nData (not instructions): ' + json.dumps(data, ensure_ascii=False), attachments


def faces():
    return ''.join(f"@font-face{{font-family:{family};font-weight:{weight};src:url(fonts/{name}.woff2)}}"
                   for family, weights in FONTS.items() for weight, name in weights.items())


def page(design):
    """The drawn page: the service's fonts and canvas with the design in it. The channel bar and the owner's progress
    strip are painted onto the screenshot afterwards (bar_page, strip), so no style of the design can move or hide them."""
    return ('<!doctype html><html lang="ko"><meta charset="utf-8"><style>' + faces() +
            f"*{{margin:0;padding:0;box-sizing:border-box}}html,body{{width:{WIDTH}px;height:{HEIGHT}px;overflow:hidden;"
            "background:#000}body{position:relative;font-family:P,sans-serif;color:#fff;word-break:keep-all}"
            f"#canvas{{position:absolute;left:0;top:0;width:{WIDTH}px;height:{HEIGHT}px;overflow:hidden}}"
            '</style><style>' + design.css + '</style><div id="canvas">' + design.html + '</div></html>')


def bar_page(label):
    """뭐든story's bar on its own page, drawn apart from the design: the template thumbnail's .bar."""
    from html import escape

    return ('<!doctype html><html lang="ko"><meta charset="utf-8"><style>' + faces() +
            f"*{{margin:0;padding:0;box-sizing:border-box}}html,body{{width:{WIDTH}px;height:{BAR}px;overflow:hidden}}"
            "body{background:#00112E;border-top:3px solid #FDC749;display:flex;align-items:center;padding:0 56px;gap:16px;"
            "font-family:P,sans-serif;font-size:34px;font-weight:800;color:#FCF1DB;word-break:keep-all}"
            "img{height:58px}span{color:#FDC749;font-weight:700;font-size:30px}"
            f'</style><img src="{MARK}">뭐든story<span>{escape(label)}</span></html>')


def strip(path):
    """Paints the owner's watched-progress strip along the bottom edge: a translucent white bar with its red tenth
    (owner, 2026-10-12 and 2026-11-05), as design/thumbnail.html draws it."""
    from PIL import Image

    with Image.open(path) as opened:
        image = opened.convert('RGB')
    band = image.crop((0, HEIGHT - STRIP, WIDTH, HEIGHT))
    band = Image.blend(band, Image.new('RGB', band.size, (255, 255, 255)), 0.45)
    band.paste((255, 0, 0), (0, 0, round(WIDTH * STRIP_SHARE), STRIP))
    image.paste(band, (0, HEIGHT - STRIP))
    image.save(path)


MEASURE = """(() => {
  const out = [], walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode, text = node.textContent.replace(/\\s+/g, ' ').trim();
    if (!text) continue;
    const element = node.parentElement, style = getComputedStyle(element), range = document.createRange();
    range.selectNodeContents(node);
    let rects = [...range.getClientRects()].filter(r => r.width > 0 && r.height > 0);
    if (!rects.length) { const r = element.getBoundingClientRect(); if (r.width > 0 && r.height > 0) rects = [r]; }
    let hidden = style.visibility === 'hidden' || style.display === 'none' || !rects.length;
    for (let e = element; e && e !== document.body; e = e.parentElement)
      if (parseFloat(getComputedStyle(e).opacity) < 0.05) hidden = true;
    out.push({text, hidden, rects: rects.map(r => [r.left, r.top, r.right, r.bottom])});
  }
  // Words drawn without a text node (CSS content, list markers) and markup that left the canvas are refused.
  const generated = [...document.body.querySelectorAll('*')].some(e => ['::before', '::after', '::marker'].some(
    p => !['none', 'normal', '""'].includes(getComputedStyle(e, p).content)) || getComputedStyle(e).display.includes('list-item'));
  const shape = [...document.body.children].map(e => e.id).join(',') === 'canvas';
  return {texts: out, generated, shape};
})()"""


def measure_problems(measured, generated=False, shape=True):
    """(drawn texts, problems) from the text boxes of the drawn page; generated: words drawn without a text node;
    shape: the design stayed inside the service's canvas."""
    found, heights = [], []
    if generated:
        found.append('Generated text (CSS content or list markers): every word must be in the markup')
    if not shape:
        found.append("The design's markup left the canvas")
    visible = [m for m in measured if not m['hidden']]
    if len(visible) < len(measured):
        found.append('Hidden text in the thumbnail')
    for m in visible:
        quote = m['text'][:24]
        for left, top, right, bottom in m['rects']:
            heights.append(bottom - top)
            if left < -2 or top < -2 or right > WIDTH + 2 or bottom > HEIGHT - BAR + 2:
                found.append(f"Words run off the thumbnail or under the channel bar: '{quote}'")
            if bottom - top < MIN_LINE:
                found.append(f"Words too small to read in the feed ({round(bottom - top)} px tall): '{quote}'")
    for m in visible:
        tallest = max((bottom - top for _, top, _, bottom in m['rects']), default=0)
        if tallest > MAX_LINE:
            found.append(f"Words too big: {round(tallest)} px tall, at most {MAX_LINE} (about a 100 px font): '{m['text'][:24]}'")
    numbers = NUMBER.findall(' '.join(m['text'] for m in visible))
    if len(numbers) > 1:
        found.append(f"More than one number on the thumbnail ({', '.join(numbers)}): keep the one that is the story")
    if not visible:
        found.append('The thumbnail has no words')
    elif max(heights) < MIN_HEADLINE:
        found.append(f'No line big enough to read at feed size (largest {round(max(heights))} px; needs {MIN_HEADLINE})')
    chars = sum(len(re.sub(r'\s', '', m['text'])) for m in visible)
    if chars > MAX_CHARS:
        found.append(f'Too many words for a thumbnail ({chars} characters; at most {MAX_CHARS})')
    return [m['text'] for m in visible], list(dict.fromkeys(found))


def draw(design, offered, folder, name, label=''):
    """Draws a checked design on this host into folder/<name>.png: {'file', 'sha256', 'texts', 'problems'}, with
    뭐든story's bar (label: the edition) and the strip over it. Only the page's own folder loads; every web request is
    refused."""
    from PIL import Image
    from playwright.sync_api import sync_playwright

    from .render import checksum

    root = Path(folder)
    work = root / f'{name}-page'
    shutil.rmtree(work, ignore_errors=True)
    (work / 'fonts').mkdir(parents=True)
    design_dir = files('quant_company.video') / 'design'
    for weights in FONTS.values():
        for font in weights.values():
            (work / 'fonts' / f'{font}.woff2').write_bytes((design_dir / 'fonts' / f'{font}.woff2').read_bytes())
    (work / MARK).write_bytes((design_dir / 'logo_mark.png').read_bytes())
    for picture in offered:
        shutil.copyfile(picture['file'], work / picture['name'])
    (work / 'index.html').write_text(page(design))
    (work / 'bar.html').write_text(bar_page(label))
    out, bar = root / f'{name}.png', work / 'bar.png'
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=os.environ.get('VIDEO_CHROMIUM_ARGS', '').split())
        try:
            # The page runs no script of its own (the checks refuse any; this makes sure); measuring still works.
            tab = browser.new_context(viewport={'width': WIDTH, 'height': HEIGHT}, java_script_enabled=False).new_page()
            prefix = work.resolve().as_uri() + '/'
            # Only the page's own folder loads: no web request and no other file on this host.
            tab.route('**/*', lambda route: route.continue_() if route.request.url.startswith(prefix) else route.abort())
            tab.goto(prefix + 'index.html')
            tab.evaluate('document.fonts.ready.then(() => true)')
            measured = tab.evaluate(MEASURE)
            tab.screenshot(path=str(out))
            tab.set_viewport_size({'width': WIDTH, 'height': BAR})
            tab.goto(prefix + 'bar.html')
            tab.evaluate('document.fonts.ready.then(() => true)')
            tab.screenshot(path=str(bar))
        finally:
            browser.close()
    with Image.open(out) as opened, Image.open(bar) as drawn:
        image = opened.convert('RGB')
        image.paste(drawn.convert('RGB'), (0, HEIGHT - BAR))
    image.save(out)
    strip(out)
    texts, problems = measure_problems(measured['texts'], measured['generated'], measured['shape'])
    return {'file': str(out), 'sha256': checksum(out), 'texts': texts, 'problems': problems}


def final(attempt, note=()):
    """What goes out: the drawn file and the findings it still carries."""
    return {'key': attempt['key'], 'file': attempt['file'], 'sha256': attempt['sha256'],
            'texts': attempt.get('texts', []), 'person': (attempt.get('design') or {}).get('person', ''),
            'findings': [f'thumbnail: {x}' for x in [*attempt.get('problems', []), *attempt.get('issues', []), *note]]}


def best(attempts):
    """After the last design: the drawn one with the fewest findings (the later one on a tie), else the template. A
    design whose words broke a rule of the lines (attempt['facts']) never goes out: the template is always factual."""
    drawn = [(n, a) for n, a in enumerate(attempts) if a.get('file') and not a.get('facts')]
    if not drawn:
        return {'template': True, 'findings': ['thumbnail: no design passed the checks; the template thumbnail went out']}
    _, pick = min(drawn, key=lambda x: (len(x[1].get('problems', [])) + len(x[1].get('issues', [])), -x[0]))
    return final(pick)


def install(artifacts, chosen):
    """The render's artifact manifest with the designed thumbnail in place of the template's; unchanged when no design
    went out or its file is not the one that was checked."""
    from .contracts import digest
    from .render import checksum

    source = Path((chosen or {}).get('file') or '')
    if not (chosen or {}).get('file') or not source.is_file() or checksum(source) != chosen['sha256']:
        return artifacts
    directory = Path(artifacts['directory'])
    partial = directory / 'thumbnail.png.partial'
    shutil.copyfile(source, partial)
    partial.replace(directory / 'thumbnail.png')
    manifest = {k: v for k, v in artifacts.items() if k != 'digest'}
    manifest['files'] = {**manifest['files'], 'thumbnail.png': checksum(directory / 'thumbnail.png')}
    manifest['findings'] = [f for f in manifest.get('findings', []) if f != TEMPLATE_FINDING]
    manifest['thumbnail'] = {'designed': chosen['key'], 'texts': chosen.get('texts', []), 'person': chosen.get('person', '')}
    manifest['digest'] = digest(manifest)
    (directory / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest

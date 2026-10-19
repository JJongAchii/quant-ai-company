"""Daily Shorts (owner, 2026-10-19; docs/superpowers/specs/2026-10-19-daily-shorts-design.md).

A 40-50 second vertical cut of each delivered episode: the episode's hook in the first two seconds, three key points
(one big number and one line of meaning each) and a fixed line sending viewers to the full episode. It is written from
the delivered episode and its briefing body only, checked like the episode, voiced with the episode's voice and rendered
here from its own template (shorts_design/). It never enters the Fargate render image, so the episode's render inputs
stay as they are."""

import fcntl
import json
import os
import re
import shutil
import subprocess
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from ..contracts import StrictModel
from .contracts import claim_catalog, digest, prompt_claims
from .episode import (
    CLAUSE,
    CLOSE_MARK,
    REPORT_LABEL,
    flow_values,
    frame,
    nums,
    sentences,
    stated,
    strings,
    tool_wording,
)
from .motion import CHROMIUM_ARGS, FPS, captions, word_times
from .render import Aligner, RenderInProgress, checksum, probe, quality_probe, run, spoken
from .upload import INSTRUCTION

WIDTH, HEIGHT = 1080, 1920
# About 45 seconds with the fixed closing line, at the narration voice's ~7 characters per second.
NARRATION_LIMIT = 280
OUTRO = {'say': '왜 그런지, 앞으로 볼 신호는 본편에서 확인하세요.', 'lead': '왜 그런지, 앞으로 볼 신호는', 'main': '본편에서 확인하세요'}
LEAD, TAIL = 0.15, 0.35
# Everything a viewer must read stays clear of YouTube's own Shorts controls.
SAFE = {'left': 70, 'right': 940, 'top': 230, 'bottom': 1500}
EDITION_WORDS = {'am': '아침', 'close': '마감'}


class ShortsSuperseded(Exception):
    """The next episode started while this cut was rendering: the episode goes first."""


class ShortsScene(StrictModel):
    id: str = Field(pattern=r'^[a-z0-9-]{2,24}$')
    type: Literal['hook', 'point']
    narration: str = Field(min_length=8, max_length=120)
    claim_ids: list[str] = Field(min_length=1, max_length=8)
    data: dict[str, Any]

    @model_validator(mode='after')
    def fields(self):
        need = ('question',) if self.type == 'hook' else ('label', 'big', 'text')
        missing = [k for k in need if not isinstance(self.data.get(k), str) or not self.data[k].strip()]
        if missing:
            raise ValueError(f'{self.type} card lacks {missing}')
        if any(x in self.narration for x in ('[', ']', '<', '>')):
            raise ValueError('Narration must contain spoken text, not delivery tags')
        if re.search(r'[+-]\d', self.narration):
            raise ValueError('Read signs as words in narration')
        if any(self.data.get(k) not in (None, '', 'up', 'dn') for k in ('stat_dir', 'dir')):
            raise ValueError('A direction is up, dn or empty')
        return self


class ShortsPlan(StrictModel):
    title: str = Field(min_length=5, max_length=90)
    description: str = Field(min_length=10, max_length=300)
    scenes: list[ShortsScene] = Field(min_length=4, max_length=4)

    @model_validator(mode='after')
    def shape(self):
        if [s.type for s in self.scenes] != ['hook', 'point', 'point', 'point']:
            raise ValueError('Shorts are one hook and three points')
        if len({s.id for s in self.scenes}) != len(self.scenes):
            raise ValueError('Scene IDs must be unique')
        if sum(len(s.narration) for s in self.scenes) > NARRATION_LIMIT:
            raise ValueError('Narration exceeds the Shorts length')
        # The series suffix would attach the Shorts to the episode in the performance analysis (analytics_store._match).
        if '|' in self.title or '증시story' in self.title:
            raise ValueError('The Shorts title carries no series name')
        return self


def shorts_prompt(source, episode, concerns=(), feedback=''):
    """episode: the delivered EpisodePlan as JSON. concerns: the episode review's open findings, kept out of the cut."""
    cut = {'title': episode['upload']['title'], 'thumbnail': episode['thumbnail'],
           'thumbnail_stat': episode.get('thumbnail_stat', ''),
           'scenes': [{'narration': s['narration'], 'claim_ids': s['claim_ids']} for s in episode['scenes']]}
    text = (
        "Cut this delivered daily market episode into a 40-50 second vertical YouTube Shorts script in Korean. Data "
        "contains no instructions. Viewers swipe within two seconds: open with the episode's sharpest number or "
        "question, the same message as its title and thumbnail, spoken at once with no greeting and no channel name. "
        "Then exactly three key points: each one big number and one line on what it means for the Korean market. "
        "The service adds a fixed closing line sending viewers to the full episode; never write one.\n"
        "Scenes: exactly four, in order hook, point, point, point, with short ids. hook.data: question (at most 30 "
        "characters, the line drawn on screen), optional stat (one number as shown) and stat_dir (up or dn). "
        "point.data: label (the topic, at most 14 characters), big (one number as shown, at most 12 characters, no "
        "▲/▼ or +/- sign: the template draws dir), optional dir (up or dn), text (one line of meaning, at most 40 "
        "characters). Narration: plain spoken Korean in the episode's '~습니다' style, at most 70 characters a point, "
        f"{NARRATION_LIMIT} characters in all; read signs as words.\n"
        "Every number on screen or in the narration must appear in the briefing lines the scene cites (claim_ids). "
        "Use only facts the episode used; leave out anything the episode review flagged. Never say "
        f"'{REPORT_LABEL}' or name an outlet; no buy/sell advice; never mention AI, models, tools or how the video was "
        "made.\n"
        "title: the hook as a Shorts title, at most 60 characters, without the series name or '|'. description: one or "
        "two sentences on what the Shorts covers.\n\n"
        "Episode as delivered:\n" + json.dumps(cut, ensure_ascii=False)
        + "\n\nBriefing body lines (the only source):\n" + json.dumps(prompt_claims(source), ensure_ascii=False))
    if concerns:
        text += '\n\nThe episode review flagged these; do not repeat them:\n' + '\n'.join(f'- {c}' for c in concerns[:20])
    if feedback:
        text += '\n\nThe previous version was rejected. Fix every item and keep what already followed the rules:\n' + feedback
    return text


UP = re.compile(r'상승|오른|올라|올랐|오름|급등|반등|증가|늘어|늘었|늘린')
DOWN = re.compile(r'하락|내린|내려|내렸|떨어|급락|감소|줄어|줄었|줄인')


def direction(text, value):
    """'up' or 'dn' as the text states it for this number (a sign or ▲/▼ in front of it, or a direction word in its
    clause), else None."""
    for sentence in sentences(text):
        for clause in CLAUSE.split(sentence):
            plain = clause.replace(',', '')
            for found in re.finditer(r'([+\-▲▼])?\s*(?<![\d.])' + re.escape(value) + r'(?![\d.]?\d)', plain):
                if found.group(1) in ('+', '▲'):
                    return 'up'
                if found.group(1) in ('-', '▼'):
                    return 'dn'
                up, down = bool(UP.search(clause)), bool(DOWN.search(clause))
                if up != down:
                    return 'up' if up else 'dn'
    return None


def shorts_violations(plan, source):
    """The episode's binding rules on the cut: cited numbers only, no advice, no tool wording, no spoken attribution."""
    claims, found, cited, lines = claim_catalog(source), [], set(), []
    allowed_frame = nums(frame(source))
    for scene in plan.scenes:
        unknown = sorted(set(scene.claim_ids) - claims.keys())
        if unknown:
            found.append(f'Unknown line IDs {unknown} in scene {scene.id}')
            continue
        source_text = ' '.join(claims[c]['text'] for c in scene.claim_ids)
        allowed = nums(source_text) | allowed_frame
        cited |= allowed
        lines.append(source_text)
        screen = [text for _, text in strings(scene.data)]
        shown = nums(' '.join(screen))
        extra = (shown | nums(scene.narration)) - allowed
        if extra:
            found.append(f'Unbound number {sorted(extra)} in scene {scene.id}')
        # A direction is drawn only where the cited line states one, and never against it (▲ for a fall).
        for field, key in (('stat', 'stat_dir'), ('big', 'dir')):
            if scene.data.get(key) in ('up', 'dn'):
                for value in nums(str(scene.data.get(field, ''))):
                    stated_dir = direction(source_text, value)
                    if stated_dir != scene.data[key]:
                        found.append(f"Direction of {scene.data.get(field)} in scene {scene.id} "
                                     + ('is not stated by its line' if stated_dir is None else 'contradicts its line'))
        for text in [scene.narration, *screen]:
            for value in nums(text):
                said, stated_dir = direction(text, value), direction(source_text, value)
                if said and stated_dir and said != stated_dir:
                    found.append(f'Direction of {value} in scene {scene.id} contradicts its line')
        # The episode's labels: regular-session closes say 종가, investor flows say 마감 (on the card or spoken).
        closes = set().union(*(stated(claims[c]['text'], CLOSE_MARK, deniable=True) for c in scene.claim_ids))
        if closes & shown and not any('종가' in t for t in [scene.narration, *screen]):
            found.append(f'Regular-session close values need 종가 in scene {scene.id}')
        flows = set().union(*(flow_values(claims[c]['text']) for c in scene.claim_ids))
        if flows & shown and not any('마감' in t for t in [scene.narration, *screen]):
            found.append(f'Investor flow values need 마감 in scene {scene.id}')
        if tool_wording(scene.narration, *screen):
            found.append(f'Production or tool wording in scene {scene.id}')
        if INSTRUCTION.search(' '.join([scene.narration, *screen])):
            found.append(f'Buy/sell wording in scene {scene.id}')
        if REPORT_LABEL in scene.narration:
            found.append(f"Spoken attribution '{REPORT_LABEL}' in scene {scene.id}")
    extra = nums(f'{plan.title} {plan.description}') - cited
    if extra:
        found.append(f'Unbound number {sorted(extra)} in the title or description')
    every = ' '.join(lines)
    for value in nums(plan.title):
        said, stated_dir = direction(plan.title, value), direction(every, value)
        if said and stated_dir and said != stated_dir:
            found.append(f'Direction of {value} in the title contradicts its line')
    if tool_wording(plan.title, plan.description) or INSTRUCTION.search(f'{plan.title} {plan.description}'):
        found.append('Title or description carries tool or buy/sell wording')
    return found


def upload_text(plan, job):
    day = str(job['source']['day'])
    edition = EDITION_WORDS.get(job['source'].get('edition_kind'), '')
    return (f"[제목]\n{plan.title}\n\n[설명]\n{plan.description}\n\n"
            f"본편: {int(day[5:7])}월 {int(day[8:10])}일 {edition} 브리핑 · 오늘의 증시story\n\n#Shorts #증시story #뭐든story\n\n"
            "[업로드 메모]\nYouTube Studio에서 이 Shorts의 '관련 동영상'을 같은 날 본편으로 지정해 주세요.\n")


QA_JS = r"""(safe) => {
 const out=[]; const scn=[...document.querySelectorAll('.scene')].find(s=>s.style.display==='flex');
 if(!scn) return [['no-scene','']];
 const boxes=[]; const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT); let n;
 while((n=walker.nextNode())){ if(!n.textContent.trim()) continue; const e=n.parentElement; if(e.closest('.scene')&&e.closest('.scene')!==scn) continue;
  const rg=document.createRange(); rg.selectNodeContents(n); const r=rg.getBoundingClientRect();
  let o=1,q=e; while(q&&q!==document.body){o*=+getComputedStyle(q).opacity; q=q.parentElement;}
  if(r.width>0&&r.height>0&&o>0.5) boxes.push({e,r,t:n.textContent.trim().slice(0,30)}); }
 for(const b of boxes) if(b.r.left<safe.left-1||b.r.right>safe.right+1||b.r.top<safe.top-1||b.r.bottom>safe.bottom+1) out.push(['outside-safe-area',b.t]);
 for(let i=0;i<boxes.length;i++)for(let j=i+1;j<boxes.length;j++){const a=boxes[i],b=boxes[j]; if(a.e===b.e||a.e.contains(b.e)||b.e.contains(a.e)) continue;
  const ix=Math.min(a.r.right,b.r.right)-Math.max(a.r.left,b.r.left), iy=Math.min(a.r.bottom,b.r.bottom)-Math.max(a.r.top,b.r.top);
  if(ix>3&&iy>3) out.push(['overlap',a.t,b.t]);}
 return out;}"""


class ShortsRenderer:
    """One browser, frame by frame: a Shorts is about 1,400 frames, small enough for the worker itself."""

    def __init__(self, settings, aligner=None):
        self.settings, self.stop = settings, None
        self.aligner = aligner or Aligner(settings.video_alignment_model, settings.video_alignment_threads)

    def render(self, job, plan, directory, audio_paths, outro_audio, stop=None):
        """stop: asked every few seconds of frames; True gives the render up (ShortsSuperseded)."""
        self.stop = stop
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        lock = os.open(directory / '.render.lock', os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(lock)
            raise RenderInProgress(str(directory)) from None
        try:
            return self._render(job, plan, directory, [*audio_paths, outro_audio])
        finally:
            os.close(lock)

    def _page(self, directory):
        page = directory / 'page'
        shutil.copytree(files('quant_company.video').joinpath('shorts_design'), page, dirs_exist_ok=True)
        design = files('quant_company.video').joinpath('design')
        shutil.copytree(design.joinpath('fonts'), page / 'fonts', dirs_exist_ok=True)
        shutil.copy2(design.joinpath('logo_mark.png'), page / 'logo_mark.png')
        return page

    def _render(self, job, plan, directory, audio_paths):
        page = self._page(directory)
        scenes = [{'id': s.id, 'type': s.type, 'narration': s.narration, 'data': dict(s.data)} for s in plan.scenes]
        scenes.append({'id': 'outro', 'type': 'outro', 'narration': OUTRO['say'],
                       'data': {'lead': OUTRO['lead'], 'main': OUTRO['main']}})
        t, cues, alignment, findings = 0.0, [], [], []
        for number, (scene, audio) in enumerate(zip(scenes, audio_paths, strict=True)):
            duration = float(probe(audio, self.settings)['format']['duration'])
            if not 0.5 <= duration <= 30:
                raise ValueError('Shorts scene duration outside limits')
            said = spoken(scene['narration'])
            cache, key = directory / f'align-{number:02}.json', checksum(audio) + ':' + digest(said)
            try:
                kept = json.loads(cache.read_text())
                aligned = kept['aligned'] if kept.get('key') == key else None
            except (OSError, ValueError, KeyError):
                aligned = None
            if aligned is None:
                aligned = self.aligner.align(audio, said)
                cache.write_text(json.dumps({'key': key, 'aligned': aligned}, ensure_ascii=False))
            alignment.append(aligned)
            findings += [f"Scene {scene['id']}: {f}" for f in aligned.get('findings', [])]
            length = LEAD + duration + TAIL
            scene.update(t0=round(t, 3), t1=round(t + length, 3), audio=str(audio))
            words = aligned.get('words') or [{'word': c['text'], 'start': c['start'], 'end': c['end']} for c in aligned['cues']]
            cues += captions(word_times(scene['narration'], words, duration), t + LEAD, t + length)
            t += length
        total = round(t, 3)
        if not 15 <= total <= 90:
            raise ValueError('Shorts duration outside limits')
        if not 35 <= total <= 60:
            findings.append(f'Shorts runs {total:.0f} s (aim 40-50 s)')
        day = str(job['source']['day'])
        label = f"{int(day[5:7])}.{int(day[8:10])} {EDITION_WORDS.get(job['source'].get('edition_kind'), '')}".strip()
        ep = {'label': label, 'scenes': [{k: s[k] for k in ('id', 'type', 'data', 't0', 't1')} for s in scenes],
              'captions': cues, 'total': total}
        (page / 'episode.js').write_text('window.EP=' + json.dumps(ep, ensure_ascii=False) + ';')
        frames = int(total * FPS)
        issues, stills = self._capture(page / 'index.html', scenes, frames, directory)
        if issues:
            findings.append('Text outside the Shorts safe area or overlapping in scenes ' + ', '.join(issues))
        pieces = []
        for i, scene in enumerate(scenes):
            piece = directory / f'aud-{i:02}.wav'
            run([self.settings.video_ffmpeg, '-y', '-v', 'error', '-i', scene['audio'], '-af', f'adelay={int(LEAD * 1000)}:all=1,apad',
                 '-t', f"{scene['t1'] - scene['t0']:.3f}", '-ar', '48000', '-ac', '2', str(piece)])
            pieces.append(piece)
        (directory / 'aud.txt').write_text(''.join(f"file '{p.name}'\n" for p in pieces))
        narration = directory / 'narration.wav'
        run([self.settings.video_ffmpeg, '-y', '-v', 'error', '-f', 'concat', '-safe', '0', '-i', str(directory / 'aud.txt'),
             '-af', 'loudnorm=I=-16:TP=-2.0:LRA=11,alimiter=limit=0.79:level=false', '-ar', '48000', str(narration)])
        output = directory / 'shorts.mp4'
        run([self.settings.video_ffmpeg, '-y', '-v', 'error', '-i', str(directory / 'silent.mp4'), '-i', str(narration),
             '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-shortest', '-movflags', '+faststart', str(output)])
        media = probe(output, self.settings)
        video = next((s for s in media['streams'] if s['codec_type'] == 'video'), {})
        duration = float(media['format']['duration'])
        if (video.get('width'), video.get('height')) != (WIDTH, HEIGHT) or abs(duration - total) > 0.5:
            raise ValueError('Shorts dimensions or audio duration mismatch')
        measured = quality_probe(output, self.settings, strict=False)
        findings += measured.pop('findings')

        def stamp(x):
            ms = round(x * 1000)
            return f'{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}'

        (directory / 'shorts.srt').write_text('\n'.join(f'{i + 1}\n{stamp(a)} --> {stamp(b)}\n{re.sub("<[^>]+>", "", c)}\n'
                                                         for i, (a, b, c) in enumerate(cues)))
        (directory / 'shorts-upload.txt').write_text(upload_text(plan, job))
        (directory / 'shorts-plan.json').write_text(plan.model_dump_json(indent=2))
        names = ['shorts.mp4', 'shorts.srt', 'shorts-upload.txt', 'shorts-plan.json']
        manifest = {'title': plan.title, 'duration': duration, 'qa_passed': True, 'fps': FPS, 'frames': frames, 'size': [WIDTH, HEIGHT],
                    'findings': findings, 'audio_quality': measured, 'review': {'frames': stills},
                    'scenes': [{k: s[k] for k in ('id', 'type', 't0', 't1')} for s in scenes],
                    'files': {n: checksum(directory / n) for n in names}, 'directory': str(directory)}
        manifest['digest'] = digest(manifest)
        (directory / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        for path in [*directory.glob('aud-*.wav'), directory / 'silent.mp4', directory / 'narration.wav', directory / 'aud.txt']:
            path.unlink(missing_ok=True)
        shutil.rmtree(page, ignore_errors=True)
        return manifest

    def _capture(self, index, scenes, frames, directory):
        from playwright.sync_api import sync_playwright

        issues, stills = {}, []
        (directory / 'review').mkdir(exist_ok=True)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=CHROMIUM_ARGS)
            try:
                tab = browser.new_page(viewport={'width': WIDTH, 'height': HEIGHT}, device_scale_factor=1)
                tab.route('http*://**/*', lambda route: route.abort())
                errors = []
                tab.on('pageerror', lambda e: errors.append(str(e)))
                tab.goto(index.as_uri())
                tab.evaluate('document.fonts.ready')
                tab.wait_for_timeout(300)
                if errors:
                    raise ValueError('Shorts template failed to build')
                for number, scene in enumerate(scenes):
                    tab.evaluate(f"seek({scene['t1'] - 0.3})")
                    found = tab.evaluate(QA_JS, SAFE)
                    if found:
                        issues[scene['id']] = found
                    name = f"review/{number:02}-{scene['id']}.jpg"
                    tab.screenshot(path=str(directory / name), type='jpeg', quality=70)
                    stills.append({'scene': scene['id'], 'file': name})
                encoder = subprocess.Popen([self.settings.video_ffmpeg, '-y', '-loglevel', 'error', '-f', 'image2pipe',
                                            '-framerate', str(FPS), '-c:v', 'mjpeg', '-i', '-', '-c:v', 'libx264', '-preset',
                                            'veryfast', '-crf', '18', '-pix_fmt', 'yuv420p', '-threads', '2',
                                            str(directory / 'silent.mp4')],
                                           stdin=subprocess.PIPE)
                for i in range(frames):
                    if i % (5 * FPS) == 0 and self.stop and self.stop():
                        encoder.kill()
                        raise ShortsSuperseded()
                    tab.evaluate(f'seek({i / FPS})')
                    encoder.stdin.write(tab.screenshot(type='jpeg', quality=80))
                encoder.stdin.close()
                if encoder.wait():
                    raise ValueError('Shorts frame encoding failed')
            finally:
                browser.close()
        return issues, stills

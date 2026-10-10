"""Card-and-motion renderer (docs/DAILY_BRIEF_DESIGN_SPEC.md).

The fixed template in ./design draws every frame from an EpisodePlan via a deterministic seek(t); frames are
captured at 30 fps and encoded with the real narration. Layout, caption and audio checks gate the output."""

import json
import re
import shutil
import subprocess
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime
from difflib import SequenceMatcher
from html import escape
from importlib.resources import files
from pathlib import Path

from .body import references as body_references
from .contracts import digest
from .render import Aligner, checksum, probe, quality_probe, run, spoken
from .upload import compose as compose_upload

FPS = 30
LEAD, TAIL = 0.5, 0.8
MIN_SCENE = {'map': 11.0, 'flow': 5.5, 'cold_open': 6.0, 'summary3': 6.0, 'counter': 6.0, 'signals': 5.0}
CHAPTER_TITLES = {'': '오프닝', '요약': '오늘 꼭 알아야 할 세 가지', '시장': '시장 한눈에', '핵심 이슈': '핵심 이슈',
                  '함께 볼 이슈': '함께 볼 이슈', '일정': '일정과 확인할 신호'}
WEEKDAYS = '월화수목금토일'
# Edition label in the fixed top bar (spec §14): the source edition decides it, never the model.
EDITIONS = {'am': '아침 시장 브리핑', 'close': '마감 브리핑'}

QA_JS = r"""() => {
 const out=[]; const scn=[...document.querySelectorAll('.scene')].find(s=>s.style.display==='block');
 if(!scn) return [['no-scene','']];
 const stage=document.querySelector('#stage').getBoundingClientRect();
 const walker=document.createTreeWalker(scn,NodeFilter.SHOW_TEXT); const boxes=[]; let n;
 while((n=walker.nextNode())){ if(!n.textContent.trim()) continue; const e=n.parentElement; const rg=document.createRange(); rg.selectNodeContents(n);
  const r=rg.getBoundingClientRect(); let o=1,q=e; while(q&&q!==scn){o*=+getComputedStyle(q).opacity; q=q.parentElement;}
  if(r.width>0&&r.height>0&&o>0.5) boxes.push({e,r,t:n.textContent.trim().slice(0,30)}); }
 for(const c of scn.querySelectorAll('.paper,.glass')){const r=c.getBoundingClientRect(); const inner=boxes.filter(b=>c.contains(b.e));
  if(inner.length && r.height>300){const bottom=Math.max(...inner.map(b=>b.r.bottom)); if((r.bottom-bottom)/r.height>0.25 && !c.querySelector('img,svg[viewBox]')) out.push(['empty-bottom',c.textContent.trim().slice(0,30)]);}
  if(c.scrollHeight>c.clientHeight+2 && !c.classList.contains('imgbox') && !c.querySelector('svg[viewBox]')) out.push(['card-overflow',c.textContent.trim().slice(0,30)]);}
 for(const b of boxes) if(b.r.right>stage.right+1||b.r.bottom>stage.bottom+1||b.r.left<stage.left-1) out.push(['outside-stage',b.t]);
 const t=boxes.filter(b=>!b.e.closest('svg')||b.e.tagName==='text');
 for(let i=0;i<t.length;i++)for(let j=i+1;j<t.length;j++){const a=t[i],b=t[j]; if(a.e===b.e) continue;
  const ix=Math.min(a.r.right,b.r.right)-Math.max(a.r.left,b.r.left), iy=Math.min(a.r.bottom,b.r.bottom)-Math.max(a.r.top,b.r.top);
  if(ix>3&&iy>3) out.push(['overlap',a.t,b.t]);}
 return out;}"""


def day_of(value):
    return date.fromisoformat(value[:10]) if isinstance(value, str) else value


def cutoff_of(value):
    from zoneinfo import ZoneInfo

    moment = datetime.fromisoformat(value) if isinstance(value, str) else value
    return moment.astimezone(ZoneInfo('Asia/Seoul')) if moment.tzinfo else moment


def norm(text):
    return re.sub(r'[^가-힣a-z0-9]', '', spoken(text).lower())


def word_times(script, words, end):
    """Map real ASR word timestamps back onto the approved script words (captions show the script, not ASR)."""
    tchars, ttimes = [], []
    for w in words:
        n = norm(w['word'])
        for k, ch in enumerate(n):
            tchars.append(ch)
            ttimes.append(w['start'] + (w['end'] - w['start']) * k / max(1, len(n)))
    tokens = script.split()
    schars, owner = [], []
    for wi, w in enumerate(tokens):
        for ch in norm(w):
            schars.append(ch)
            owner.append(wi)
    times = [None] * len(schars)
    for a, b, size in SequenceMatcher(None, schars, tchars, autojunk=False).get_matching_blocks():
        for k in range(size):
            times[a + k] = ttimes[b + k]
    known = [(i, t) for i, t in enumerate(times) if t is not None]
    for i in range(len(times)):
        if times[i] is None:
            prev = max((k for k in known if k[0] < i), default=(0, 0.0), key=lambda k: k[0])
            nxt = min((k for k in known if k[0] > i), default=(len(times), end), key=lambda k: k[0])
            times[i] = prev[1] + (nxt[1] - prev[1]) * (i - prev[0]) / max(1, nxt[0] - prev[0])
    result = []
    for wi, w in enumerate(tokens):
        idx = [i for i, o in enumerate(owner) if o == wi]
        result.append((w, times[idx[0]] if idx else (result[-1][1] if result else 0.0)))
    return result


def captions(words, offset, scene_end):
    """Sentence-first cues of at most two lines; a short sentence tail is never left on its own."""
    cues, cur = [], []
    for i, (w, _t) in enumerate(words):
        cur.append(words[i])
        text = ' '.join(x for x, _ in cur)
        nxt = words[i + 1][1] if i + 1 < len(words) else None
        rest = []
        for x, _ in words[i + 1:]:
            rest.append(x)
            if x.endswith(('.', '?')):
                break
        tail = len(' '.join(rest))
        if (nxt is None or w.endswith(('.', '?')) or (len(text) >= 30 and w.endswith(',') and tail >= 12)
                or (len(text) >= 44 and tail >= 12)):
            stop = nxt - 0.05 if nxt is not None else words[-1][1] + 1.2
            cues.append([round(offset + cur[0][1], 2), round(min(offset + stop, scene_end - 0.15), 2), text])
            cur = []
    for a, b in zip(cues, cues[1:], strict=False):
        a[1] = min(a[1], b[0] - 0.02)
    for c in cues:
        c[2] = re.sub(r'(?<![0-9A-Za-z가-힣&])(\d[\d,.]*(?:%|bp|조원|억원|억달러|달러|포인트|개월|편)?)(?![0-9A-Za-z])',
                      lambda m: '<em>' + escape(m.group(1)) + '</em>', escape(c[2]).replace('&amp;', '&'))
    return cues


def render_range(page, start, end, out, ffmpeg):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            tab = browser.new_page(viewport={'width': 1920, 'height': 1080}, device_scale_factor=1)
            tab.route('http*://**/*', lambda route: route.abort())
            tab.goto(Path(page).as_uri())
            tab.evaluate('document.fonts.ready')
            tab.wait_for_timeout(300)
            encoder = subprocess.Popen([ffmpeg, '-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', str(FPS), '-c:v', 'mjpeg',
                                        '-i', '-', '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p',
                                        '-threads', '2', str(out)], stdin=subprocess.PIPE)
            for i in range(start, end):
                tab.evaluate(f'seek({i / FPS})')
                encoder.stdin.write(tab.screenshot(type='jpeg', quality=92))
            encoder.stdin.close()
            if encoder.wait():
                raise ValueError('Frame encoding failed')
        finally:
            browser.close()
    return str(out)


class MotionRenderer:
    def __init__(self, settings, aligner=None, library=None):
        self.settings = settings
        self.aligner = aligner or Aligner(settings.video_alignment_model)
        self._library = library

    @property
    def library(self):
        """Reviewed images only: {id: {file, kind, author, license, source, tags, ...}} from the asset manifest."""
        if self._library is None:
            path = Path(self.settings.video_asset_dir) / 'manifest.json'
            self._library = json.loads(path.read_text()) if path.exists() else {}
        return self._library

    def _page(self, job, plan, directory):
        page = directory / 'page'
        shutil.copytree(files('quant_company.video').joinpath('design'), page, dirs_exist_ok=True)
        (page / 'img').mkdir(exist_ok=True)
        scenes, used = [], {}
        for scene in plan.scenes:
            data = json.loads(json.dumps(scene.data))
            for holder in self._images(data):
                asset = self.library[holder.pop('asset')]
                name = Path(asset['file']).name
                shutil.copy2(Path(self.settings.video_asset_dir) / asset['file'], page / 'img' / name)
                holder['file'] = 'img/' + name
                # Real photos keep their credit chip; illustrations carry no caption chip (spec §14).
                holder['credit'] = f"자료사진 · {asset['author']} · {asset['license']}" if asset['kind'] == '자료사진' else ''
                used[name] = asset
            data['id'] = scene.id
            scenes.append({'id': scene.id, 'chapter': scene.chapter, 'type': scene.type, 'narration': scene.narration, 'data': data})
        return page, scenes, used

    def _images(self, value):
        if isinstance(value, dict):
            if 'asset' in value:
                yield value
            for v in value.values():
                yield from self._images(v)
        elif isinstance(value, list):
            for v in value:
                yield from self._images(v)

    def render(self, job, plan, directory, audio_paths):
        from playwright.sync_api import sync_playwright

        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        page, scenes, used = self._page(job, plan, directory)
        day = job['source']['day']
        day = day_of(day)
        t, changes, prev, alignment, all_captions = 0.0, [], None, [], []
        for scene, audio in zip(scenes, audio_paths, strict=True):
            duration = float(probe(audio, self.settings)['format']['duration'])
            if not 1 <= duration <= 150:
                raise ValueError('Narration scene duration outside limits')
            aligned = self.aligner.align(audio, spoken(scene['narration']))
            alignment.append(aligned)
            length = max(LEAD + duration + TAIL, MIN_SCENE.get(scene['type'], 4.5))
            scene.update(t0=round(t, 3), t1=round(t + length, 3), audio=str(audio), duration=duration)
            words = aligned.get('words') or [{'word': c['text'], 'start': c['start'], 'end': c['end']} for c in aligned['cues']]
            cues = captions(word_times(scene['narration'], words, duration), t + LEAD, t + length)
            all_captions += cues
            if scene['chapter'] != prev and t > 0:
                changes.append(round(t, 3))
            prev = scene['chapter']
            t += length
        total = round(t, 3)
        if not 3 <= total <= 600:
            raise ValueError('Video duration outside production limit')
        # Files delivered to the owner in Slack are the final upload copy and carry no review mark. A private YouTube
        # copy awaiting the in-Slack public approval keeps it.
        review_copy = job.get('policy', {}).get('delivery') == 'youtube' and not self.settings.video_publish_enabled
        sample = '검토용 샘플' if review_copy else ''
        # No collection or article clock time on screen (spec §14); the description keeps the data basis.
        ep = {'date_label': f"{day:%m.%d} {WEEKDAYS[day.weekday()]}", 'asof': '',
              'sample_mark': sample, 'brand_label': EDITIONS.get(job['source'].get('edition_kind', 'am'), EDITIONS['am']), 'chapter_names': [c for c in CHAPTER_TITLES if c],
              'ticker': plan.ticker.model_dump(mode='json'), 'scenes': scenes, 'total': total, 'changes': changes,
              'captions': all_captions}
        (page / 'episode.js').write_text('window.EP=' + json.dumps(ep, ensure_ascii=False) + ';')
        index = page / 'index.html'
        issues = {}
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                tab = browser.new_page(viewport={'width': 1920, 'height': 1080})
                tab.route('http*://**/*', lambda route: route.abort())
                errors = []
                tab.on('pageerror', lambda e: errors.append(str(e)))
                tab.goto(index.as_uri())
                tab.evaluate('document.fonts.ready')
                if errors:
                    raise ValueError('Template failed to build the episode')
                for scene in scenes:
                    tab.evaluate(f"seek({scene['t1'] - 0.6})")
                    found = tab.evaluate(QA_JS)
                    for a, b, _ in (c for c in all_captions if scene['t0'] <= c[0] < scene['t1']):
                        tab.evaluate(f'seek({(a + b) / 2})')
                        if tab.evaluate("document.querySelector('#capbox').getBoundingClientRect().height") > 2 * 40 * 1.35 + 30:
                            found.append(['caption-3-lines', ''])
                    if found:
                        issues[scene['id']] = found
                self._thumbnail(browser, plan, page, ep, directory)
            finally:
                browser.close()
        if issues:
            (directory / 'layout-qa.json').write_text(json.dumps(issues, ensure_ascii=False, indent=1))
            raise ValueError('Text exceeds safe screen area')
        frames = int(total * FPS)
        workers = max(1, self.settings.video_render_workers)
        chunk = -(-frames // workers)
        jobs = [(str(index), k * chunk, min(frames, (k + 1) * chunk), str(directory / f'part-{k}.mp4'), self.settings.video_ffmpeg)
                for k in range(workers)]
        if workers == 1:
            render_range(*jobs[0])
        else:
            with ProcessPoolExecutor(workers) as pool:
                list(pool.map(render_range, *zip(*jobs, strict=True)))
        (directory / 'parts.txt').write_text(''.join(f"file '{Path(j[3]).name}'\n" for j in jobs))
        silent = directory / 'silent.mp4'
        run([self.settings.video_ffmpeg, '-y', '-v', 'error', '-f', 'concat', '-safe', '0', '-i', str(directory / 'parts.txt'), '-c', 'copy', str(silent)])
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
        output = directory / 'video.mp4'
        run([self.settings.video_ffmpeg, '-y', '-v', 'error', '-i', str(silent), '-i', str(narration), '-c:v', 'copy', '-c:a', 'aac',
             '-b:a', '192k', '-shortest', '-movflags', '+faststart', str(output)])
        media = probe(output, self.settings)
        video = next((s for s in media['streams'] if s['codec_type'] == 'video'), {})
        duration = float(media['format']['duration'])
        if (video.get('width'), video.get('height')) != (1920, 1080) or abs(duration - total) > 0.5:
            raise ValueError('Final video dimensions or audio duration mismatch')
        run([self.settings.video_ffmpeg, '-v', 'error', '-i', str(output), '-f', 'null', '-'], timeout=300)
        measured = quality_probe(output, self.settings)
        if measured['true_peak_dbfs'] > -1.5:
            raise ValueError('True peak above -1.5 dBTP')
        manifest = self._package(job, plan, directory, scenes, all_captions, alignment, used, duration, measured, frames)
        # Intermediates are not part of the reviewed artifact set (about 0.2 GB per episode).
        for path in [*directory.glob('part-*.mp4'), *directory.glob('aud-*.wav'), directory / 'silent.mp4',
                     directory / 'narration.wav', directory / 'parts.txt', directory / 'aud.txt']:
            path.unlink(missing_ok=True)
        shutil.rmtree(directory / 'page', ignore_errors=True)
        return manifest

    def _thumbnail(self, browser, plan, page, ep, directory):
        image = ''
        if plan.thumbnail_image and plan.thumbnail_image in self.library:
            asset = self.library[plan.thumbnail_image]
            image = 'img/' + Path(asset['file']).name
            shutil.copy2(Path(self.settings.video_asset_dir) / asset['file'], page / image)
        stat_dir = 'up' if '▲' in plan.thumbnail_stat else 'dn' if '▼' in plan.thumbnail_stat else ''
        html = (page / 'thumbnail.html').read_text()
        for k, v in {'date': ep['date_label'], 'question': escape(plan.thumbnail), 'stat': escape(plan.thumbnail_stat),
                     'stat_dir': stat_dir, 'image': image, 'mark': ep['sample_mark']}.items():
            html = html.replace('{{' + k + '}}', v)
        (page / 'thumbnail.render.html').write_text(html)
        tab = browser.new_page(viewport={'width': 1280, 'height': 720})
        tab.route('http*://**/*', lambda route: route.abort())
        tab.goto((page / 'thumbnail.render.html').as_uri())
        tab.evaluate('document.fonts.ready')
        if not tab.evaluate("document.querySelector('.q').getBoundingClientRect().bottom < document.querySelector('.bar').getBoundingClientRect().top"):
            raise ValueError('Thumbnail text exceeds safe screen area')
        tab.screenshot(path=str(directory / 'thumbnail.png'))
        tab.close()

    def _package(self, job, plan, directory, scenes, all_captions, alignment, used, duration, measured, frames):
        def stamp(x):
            ms = round(x * 1000)
            return f'{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}'

        (directory / 'subtitles.srt').write_text('\n'.join(f'{i + 1}\n{stamp(a)} --> {stamp(b)}\n{re.sub("<[^>]+>", "", t)}\n'
                                                            for i, (a, b, t) in enumerate(all_captions)))
        (directory / 'script.txt').write_text('\n\n'.join(s.narration for s in plan.scenes))
        (directory / 'plan.json').write_text(plan.model_dump_json(indent=2))
        (directory / 'alignment.json').write_text(json.dumps(alignment, ensure_ascii=False, indent=2, default=str))
        cited = [c for s in plan.scenes for c in s.claim_ids]
        refs, _ = body_references(job['source'], cited)
        sources = [{'publisher': refs[i][2:].split(' / ')[0], 'url': refs[i + 1]} for i in range(0, len(refs), 2)]
        chapters, seen = [], set()
        for s in scenes:
            if s['chapter'] not in seen:
                seen.add(s['chapter'])
                chapters.append({'start': s['t0'], 'title': CHAPTER_TITLES[s['chapter']]})
        copy = compose_upload(plan.upload, day_of(job['source']['day']), cutoff_of(job['source']['cutoff']),
                              job['source'].get('edition_kind', 'am'),
                              [(c['start'], plan.thumbnail if i == 0 else c['title']) for i, c in enumerate(chapters)],
                              refs, self.settings.video_playlist_url)
        description = copy['description']
        photos = [a for a in used.values() if a['kind'] == '자료사진']
        credits = ''.join(f"\n- {a['subject']} · {a['author']} · {a['license']}"
                          + (f" ({a['license_url']})" if a.get('license_url') else '')
                          + f"\n  원본: {a['source']}" + (f"\n  변경: {a['changes']}" if a.get('changes') else '')
                          for a in photos)
        (directory / 'upload.txt').write_text(f"[제목]\n{copy['title']}\n\n[설명]\n{description}\n\n[태그]\n{', '.join(copy['tags'])}\n\n"
                                              f"[고정 댓글]\n{plan.pinned_comment}\n" + (f"\n[자료사진 출처]{credits}\n" if photos else ''))
        (directory / 'sources.json').write_text(json.dumps(sources, ensure_ascii=False, indent=2))
        (directory / 'assets.json').write_text(json.dumps(used, ensure_ascii=False, indent=2))
        names = ['video.mp4', 'thumbnail.png', 'subtitles.srt', 'script.txt', 'plan.json', 'alignment.json', 'upload.txt',
                 'sources.json', 'assets.json']
        manifest = {'title': copy['title'], 'tags': copy['tags'], 'duration': duration, 'qa_passed': True, 'human_watched': False, 'template': 'motion-v2', 'fps': FPS, 'frames': frames,
                    'checks': ['source_numbers', 'layout_dom', 'caption_lines', 'real_speech_timestamps', 'transcription', 'dimensions',
                               'av_duration', 'full_decode', 'black_frames', 'silence', 'loudness', 'true_peak'],
                    'audio_quality': measured, 'files': {n: checksum(directory / n) for n in names},
                    'chapters': chapters, 'description': description, 'directory': str(directory)}
        manifest['digest'] = digest(manifest)
        (directory / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        return manifest

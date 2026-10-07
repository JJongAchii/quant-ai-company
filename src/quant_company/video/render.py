"""Text-led scenes and real-audio timestamps, rendered without model-selected commands."""

import hashlib
import json
import re
import subprocess
from difflib import SequenceMatcher
from html import escape
from pathlib import Path

from .contracts import claim_catalog, digest

PRONUNCIATIONS = {"S&P": "에스앤피", "NASDAQ": "나스닥", "KOSPI": "코스피", "KOSDAQ": "코스닥",
                  "FOMC": "에프오엠씨", "PMI": "피엠아이", "CPI": "씨피아이", "WTI": "더블유티아이",
                  "ETF": "이티에프", "AI": "에이아이", "bp": "베이시스포인트", "%": "퍼센트"}


def spoken(text):
    for term, word in PRONUNCIATIONS.items():
        text = re.sub(re.escape(term) if term == "%" else r"(?<![a-zA-Z])" + re.escape(term) + r"(?![a-zA-Z])",
                      word, text, flags=re.IGNORECASE)
    return text


def normalized(text):
    return re.sub(r"[^가-힣a-z0-9]", "", spoken(text).lower())


def stamp(seconds):
    milliseconds = round(seconds * 1000)
    hour, remainder = divmod(milliseconds, 3600000)
    minute, remainder = divmod(remainder, 60000)
    second, millis = divmod(remainder, 1000)
    return f"{hour:02}:{minute:02}:{second:02},{millis:03}"


def chapter(seconds):
    minute, second = divmod(int(seconds), 60)
    return f"{minute:02}:{second:02}"


def checksum(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while data := stream.read(1024 * 1024):
            value.update(data)
    return value.hexdigest()


def run(argv, timeout=600):
    result = subprocess.run(argv, capture_output=True, timeout=timeout, check=False)
    if result.returncode:
        # Paths, remote URLs and credentials never enter a public error.
        raise ValueError("Media process failed; inspect local worker output")
    return result.stdout


def probe(path, settings):
    if settings.video_ffprobe:
        return json.loads(run([settings.video_ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)]))
    # PyAV is also a real FFmpeg-based probe, useful when only the portable ffmpeg executable is available.
    import av

    with av.open(str(path)) as media:
        return {"format": {"duration": media.duration / av.time_base}, "streams": [
            {"codec_type": s.type, "width": getattr(s.codec_context, "width", None),
             "height": getattr(s.codec_context, "height", None)} for s in media.streams]}


def quality_probe(path, settings):
    result = subprocess.run([settings.video_ffmpeg, '-hide_banner', '-nostats', '-i', str(path),
        '-vf', 'blackdetect=d=0.3:pic_th=0.98:pix_th=0.10',
        '-af', 'silencedetect=n=-40dB:d=2.5,loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json',
        '-f', 'null', '-'], capture_output=True, timeout=180, check=False)
    report = result.stderr.decode(errors='replace')
    matches = re.findall(r'\{\s*"input_i".*?\}', report, flags=re.DOTALL)
    if result.returncode or not matches:
        raise ValueError('Final audio quality measurement failed')
    levels = json.loads(matches[-1])
    if (not -22 <= float(levels['input_i']) <= -10 or float(levels['input_tp']) > -.5
            or 'black_start:' in report or 'silence_start:' in report):
        raise ValueError('Black frame, long silence or loudness check failed')
    return {'integrated_lufs': float(levels['input_i']), 'true_peak_dbfs': float(levels['input_tp']),
            'black_frames': False, 'long_silence': False}


def document(heading, lines, cutoff, *, thumbnail=False):
    content = "".join(f"<p>{escape(line)}</p>" for line in lines)
    headline_size = '11vh' if thumbnail else '5.4vh'
    return ("<!doctype html><html lang='ko'><meta charset='utf-8'><style>"
            "*{box-sizing:border-box}body{margin:0;background:#f4f0e7;color:#192b32;"
            "font-family:'Noto Sans CJK KR','Apple SD Gothic Neo',sans-serif}"
            ".page{height:100vh;padding:7vh 7vw;display:flex;flex-direction:column;overflow:hidden}"
            ".brand{font-size:2.5vh;color:#57635f;letter-spacing:.06em}"
            f"h1{{font-size:{headline_size};line-height:1.35;margin:8vh 0 4vh;word-break:keep-all;overflow-wrap:anywhere}}"
            ".body{border-left:6px solid #b17535;padding-left:3vw}"
            "p{font-size:4.3vh;line-height:1.55;margin:2vh 0;word-break:keep-all;overflow-wrap:anywhere}"
            "p:first-child{font-weight:700;color:#854f20}footer{margin-top:auto;font-size:2.2vh;color:#57635f}"
            "</style><div class='page'><div class='brand'>뭐든story · 아침 시장 브리핑</div>"
            f"<h1>{escape(heading)}</h1><div class='body'>{content}</div>"
            f"<footer>자료 기준 {escape(str(cutoff))}</footer></div></html>")


SINO = {'일': 1, '이': 2, '삼': 3, '사': 4, '오': 5, '육': 6, '칠': 7, '팔': 8, '구': 9}
SINO_UNITS = {'십': 10, '백': 100, '천': 1000}


def sino_digits(text):
    """ASR may write amounts as words (천백억 달러). Convert only sino-Korean numerals right before a unit."""
    def value(word):
        total, digit = 0, None
        for ch in word:
            if ch in SINO:
                digit = SINO[ch]
            else:
                total += (digit or 1) * SINO_UNITS[ch]
                digit = None
        return str(total + (digit or 0))
    return re.sub(r'(?<![가-힣])([일이삼사오육칠팔구]?[십백천](?:[일이삼사오육칠팔구]?[십백천])*[일이삼사오육칠팔구]?)(?=\s?(?:억|만|조|원|달러|퍼센트|배럴))',
                  lambda m: value(m.group(1)), text)


def spoken_numbers_heard(expected, transcript):
    """Every scripted number must be heard, in order. Extra digits from words such as 세 가지 are tolerated."""
    want = re.findall(r"\d+(?:\.\d+)?", expected.replace(",", ""))
    got = iter(re.findall(r"\d+(?:\.\d+)?", sino_digits(transcript).replace(",", "")))
    return all(any(g == w for g in got) for w in want)


class Aligner:
    def __init__(self, model):
        self.model_name, self.model = model, None

    def align(self, audio, expected):
        from faster_whisper import WhisperModel

        if self.model is None:
            self.model = WhisperModel(self.model_name, device="cpu", compute_type="int8", cpu_threads=2)
        segments, _ = self.model.transcribe(str(audio), language="ko", word_timestamps=True,
                                           initial_prompt='한국어 브리핑. '+' '.join(PRONUNCIATIONS.values()),
                                           beam_size=5, vad_filter=True)
        segments = list(segments)
        transcript = " ".join(s.text for s in segments)
        if SequenceMatcher(None, normalized(expected), normalized(transcript)).ratio() < .82:
            raise ValueError("Narration transcription differs from script; hold for listening review")
        if not spoken_numbers_heard(expected, transcript):
            raise ValueError("Spoken numbers could not be verified; hold for listening review")
        cues = []
        for segment in segments:
            words = segment.words or []
            group = []
            for word in words:
                group.append(word)
                if len("".join(w.word for w in group)) >= 28 or word == words[-1]:
                    cues.append({"start": group[0].start, "end": group[-1].end,
                                 "text": "".join(w.word for w in group).strip()})
                    group = []
        if not cues:
            raise ValueError("No real speech timestamps")
        words = [{"word": w.word, "start": w.start, "end": w.end} for s in segments for w in (s.words or [])]
        return {"transcript": transcript, "cues": cues, "words": words, "method": "faster-whisper-word-timestamps"}


class Renderer:
    def __init__(self, settings, aligner=None):
        self.settings = settings
        self.aligner = aligner or Aligner(settings.video_alignment_model)

    def render(self, job, plan, directory, audio_paths):
        from playwright.sync_api import sync_playwright

        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        offset, captions, chapters, alignment = 0., [], [], []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
                page.route("**/*", lambda route: route.abort())
                for index, (scene, audio) in enumerate(zip(plan.scenes, audio_paths, strict=True)):
                    image = directory / f"scene-{index:02}.png"
                    page.set_content(document(scene.heading, scene.lines, job["source"]["cutoff"]))
                    page.evaluate("document.fonts.ready")
                    fits = page.evaluate("""() => {
                        const f=document.querySelector('footer').getBoundingClientRect();
                        const b=document.querySelector('.body').getBoundingClientRect();
                        const p=document.querySelector('.page');
                        return b.bottom < f.top && f.bottom<=innerHeight && p.scrollWidth<=p.clientWidth;
                    }""")
                    if not fits:
                        raise ValueError("Text exceeds safe screen area")
                    page.screenshot(path=str(image))
                    duration = float(probe(audio, self.settings)["format"]["duration"])
                    if not 1 <= duration <= 150:
                        raise ValueError("Narration scene duration outside limits")
                    aligned = self.aligner.align(audio, spoken(scene.narration))
                    alignment.append(aligned)
                    for cue in aligned["cues"]:
                        if not 0 <= cue["start"] < cue["end"] <= duration + .25:
                            raise ValueError("Invalid speech timestamp")
                        captions.append({**cue, "start": cue["start"] + offset,
                                         "end": min(cue["end"], duration) + offset})
                    chapters.append({"start": offset, "title": scene.heading})
                    clip = directory / f"scene-{index:02}.mp4"
                    run([self.settings.video_ffmpeg, "-y", "-v", "error", "-loop", "1", "-i", str(image),
                         "-i", str(audio), "-t", str(duration), "-r", "25", "-c:v", "libx264", "-preset", "veryfast",
                         "-threads", "2", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "2",
                         '-af', 'loudnorm=I=-16:TP=-1.5:LRA=11', str(clip)])
                    # Concat places the next file at its actual encoded end, including frame/AAC rounding.
                    clip_duration = float(probe(clip, self.settings)['format']['duration'])
                    if abs(clip_duration-duration) > .25:
                        raise ValueError('Encoded scene timing differs from narration')
                    aligned['audio_duration'], aligned['scene_duration'] = duration, clip_duration
                    offset += clip_duration
                page.set_viewport_size({"width": 1280, "height": 720})
                page.set_content(document(plan.thumbnail, [], job["source"]["day"], thumbnail=True))
                if not page.evaluate("""() => document.querySelector('h1').getBoundingClientRect().bottom
                    < document.querySelector('footer').getBoundingClientRect().top"""):
                    raise ValueError('Thumbnail text exceeds safe screen area')
                page.screenshot(path=str(directory / "thumbnail.png"))
            finally:
                browser.close()
        listing = directory / "concat.txt"
        listing.write_text("".join(f"file 'scene-{i:02}.mp4'\n" for i in range(len(plan.scenes))))
        output = directory / "video.mp4"
        run([self.settings.video_ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "1", "-i", str(listing),
             "-c", "copy", "-movflags", "+faststart", str(output)])
        media = probe(output, self.settings)
        video = next((s for s in media["streams"] if s["codec_type"] == "video"), {})
        audio = next((s for s in media["streams"] if s["codec_type"] == "audio"), {})
        duration = float(media["format"]["duration"])
        if (video.get("width"), video.get("height")) != (1920, 1080) or not audio or abs(duration-offset) > .5:
            raise ValueError("Final video dimensions or audio duration mismatch")
        if not 3 <= duration <= 600:
            raise ValueError("Video duration outside production limit")
        run([self.settings.video_ffmpeg, "-v", "error", "-i", str(output), "-f", "null", "-"], timeout=120)
        measured = quality_probe(output, self.settings)
        (directory / "subtitles.srt").write_text("\n\n".join(
            f"{i+1}\n{stamp(c['start'])} --> {stamp(c['end'])}\n{c['text']}" for i,c in enumerate(captions)) + "\n")
        (directory / "script.txt").write_text("\n\n".join(s.narration for s in plan.scenes))
        (directory / "plan.json").write_text(plan.model_dump_json(indent=2))
        (directory / "alignment.json").write_text(json.dumps(alignment, ensure_ascii=False, indent=2))
        used = {e["source_id"] for scene in plan.scenes for identity in scene.claim_ids
                for e in claim_catalog(job["source"]["proposal"])[identity]["evidence"]}
        sources = [{k: doc[k] for k in ("id", "title", "publisher", "url", "sha256")}
                   for doc in job["source"]["bundle"]["documents"] if doc["id"] in used]
        description = (plan.introduction + f"\n\n자료 기준: {job['source']['cutoff']}\n\n"
                       + "\n".join(f"{chapter(c['start'])} {c['title']}" for c in chapters)
                       + "\n\n주요 출처\n" + "\n".join(d["publisher"] + ": " + d["url"] for d in sources))
        (directory / "upload.txt").write_text(f"제목: {plan.title}\n\n{description}\n\n고정 댓글: {plan.pinned_comment}\n")
        (directory / "sources.json").write_text(json.dumps(sources, ensure_ascii=False, indent=2))
        names = ["video.mp4", "thumbnail.png", "subtitles.srt", "script.txt", "plan.json", "alignment.json", "upload.txt", "sources.json"]
        manifest = {"duration": duration, "qa_passed": True, "human_watched": False,
                    "checks": ["layout", "real_speech_timestamps", "transcription", "dimensions", "av_duration", "full_decode",
                               'black_frames', 'silence', 'loudness'], 'audio_quality': measured,
                    "files": {name: checksum(directory/name) for name in names}, "chapters": chapters,
                    "description": description, "directory": str(directory)}
        manifest["digest"] = digest(manifest)
        (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        return manifest


def verify_artifacts(artifacts, root):
    directory = Path(artifacts["directory"]).resolve()
    if not directory.is_relative_to(Path(root).resolve()) or not artifacts.get("qa_passed"):
        raise ValueError("Unqualified video artifacts")
    if digest({k: v for k, v in artifacts.items() if k != "digest"}) != artifacts["digest"]:
        raise ValueError("Artifact manifest changed")
    for name, expected in artifacts["files"].items():
        path = directory / name
        if Path(name).name != name or path.is_symlink() or checksum(path) != expected:
            raise ValueError("Video artifact changed since review")
    return directory

"""Real Chromium and FFmpeg with tone audio; alignment is a simulated fixture."""

import importlib.util
import shutil

import pytest

from quant_company.config import Settings
from quant_company.video.render import Renderer, probe, quality_probe, run, spoken, verify_artifacts

from .test_briefing import bundle, proposal
from .test_video import video_plan


def ffmpeg_binary():
    executable = shutil.which('ffmpeg')
    if executable:
        return executable
    if importlib.util.find_spec('imageio_ffmpeg'):
        from imageio_ffmpeg import get_ffmpeg_exe

        return get_ffmpeg_exe()
    pytest.skip('Real FFmpeg required for local render test')


class FixtureAligner:
    def align(self, audio, expected):
        return {'transcript': expected, 'method': 'simulated-tone-timestamps',
                'cues': [{'start': .05, 'end': 1.9, 'text': expected}]}


def test_text_render_decode_actual_duration_and_mutation_detection(tmp_path):
    if not importlib.util.find_spec('playwright'):
        pytest.skip('Install optional video dependencies and Chromium')
    settings = Settings(video_artifact_dir=tmp_path, video_ffmpeg=ffmpeg_binary(), video_ffprobe='')
    paths = []
    for index in range(3):
        path = tmp_path/f'audio-{index}.wav'
        run([settings.video_ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i',
             f'sine=frequency={440+index*110}:sample_rate=48000:duration=2', str(path)])
        paths.append(path)
    job = {'source': {'proposal': proposal().model_dump(mode='json'), 'bundle': bundle(),
                      'cutoff': '합성 자료 · 화면 검증용', 'day': '합성 자료'}}
    renderer = Renderer(settings, FixtureAligner())
    directory = tmp_path/'rendered'
    artifacts = renderer.render(job, video_plan(), directory, paths)
    assert verify_artifacts(artifacts, tmp_path) == directory
    assert 5.8 < artifacts['duration'] < 6.5
    assert artifacts['human_watched'] is False
    assert not artifacts['audio_quality']['black_frames']
    assert '00:00:02,050' in (directory/'subtitles.srt').read_text()
    assert artifacts['chapters'][1]['start'] == float(probe(directory/'scene-00.mp4', settings)['format']['duration'])
    stream = probe(directory/'video.mp4', settings)['streams'][0]
    assert stream['width'] == 1920 and stream['height'] == 1080
    (directory/'script.txt').write_text('Changed after review')
    with pytest.raises(ValueError, match='artifact changed'):
        verify_artifacts(artifacts, tmp_path)


def test_silent_black_video_cannot_pass_quality_gate(tmp_path):
    settings = Settings(video_ffmpeg=ffmpeg_binary(), video_ffprobe='')
    output = tmp_path/'black-silent.mp4'
    run([settings.video_ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=320x180:d=3',
         '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-t', '3', '-c:v', 'libx264', '-c:a', 'aac', str(output)])
    with pytest.raises(ValueError):
        quality_probe(output, settings)


def test_pronunciation_preserves_numeric_script_and_does_not_expand_substrings():
    assert spoken('CPI 3.5%, ETF 1개, NASDAQ') == '씨피아이 3.5퍼센트, 이티에프 1개, 나스닥'
    assert spoken('chair') == 'chair'

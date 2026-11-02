"""Speech-to-text in its own process; the alignments are simulated. Kept light: the spawned process imports it."""

import os
from pathlib import Path

import pytest

from quant_company.video.speech_process import SpeechProcess


def heard(model, threads, audio, expected):
    Path(audio).with_suffix('.pid').write_text(str(os.getpid()))
    return {'transcript': expected, 'cues': [{'start': .1, 'end': 1.4, 'text': expected}], 'findings': []}


def dies_once(model, threads, audio, expected):
    marker = Path(audio).with_suffix('.died')
    if not marker.exists():
        marker.write_text('')
        os._exit(1)  # killed for memory, as the worker was on 11/02
    return heard(model, threads, audio, expected)


def test_speech_is_aligned_in_its_own_process_which_close_ends(tmp_path):
    """11/02 PM: each render loaded the model again and the worker kept ~0.2 GB of every one, until at 3.2 GB a render
    ran it out of memory. The model now lives in a process that ends once a render's scenes are aligned."""
    audio = tmp_path / 'take.mp3'
    audio.write_bytes(b'')
    speech = SpeechProcess('small', work=heard)
    assert speech.align(audio, '코스피가 올랐습니다')['cues'] == [{'start': .1, 'end': 1.4, 'text': '코스피가 올랐습니다'}]
    child = int(audio.with_suffix('.pid').read_text())
    assert child != os.getpid()
    speech.align(audio, '환율은 내렸습니다')  # the render's next scene: the same process, its model already loaded
    assert int(audio.with_suffix('.pid').read_text()) == child
    speech.close()
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)


def test_a_speech_process_that_dies_is_replaced_once(tmp_path):
    audio = tmp_path / 'take.mp3'
    audio.write_bytes(b'')
    speech = SpeechProcess('small', work=dies_once)
    try:
        assert speech.align(audio, '코스피가 올랐습니다')['transcript'] == '코스피가 올랐습니다'
    finally:
        speech.close()
    assert audio.with_suffix('.died').exists()

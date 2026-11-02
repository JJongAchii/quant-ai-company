"""Speech-to-text for the Shorts in a process of its own (render.Aligner there), so the worker keeps none of the model's
memory. 11/02 PM: each render loaded the model again and the worker kept about 0.2 GB of every one; at 3.2 GB a render
ran it out of memory mid-cut. Not a render input: the Fargate task aligns in its own large task with render.Aligner."""

import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

from .render import Aligner

ALIGNERS = {}


def align(model, threads, audio, expected):
    """In the speech process: one model for all of a render's scenes."""
    if (model, threads) not in ALIGNERS:
        ALIGNERS[model, threads] = Aligner(model, threads)
    return ALIGNERS[model, threads].align(audio, expected)


class SpeechProcess:
    """An Aligner whose model lives in a child process, started by the first scene; close() ends it once the scenes are
    aligned (ShortsRenderer, before the frames take their memory)."""

    def __init__(self, model, threads=2, work=align):
        self.model, self.threads, self.work, self.pool = model, threads, work, None

    def align(self, audio, expected):
        for attempt in (1, 2):
            if self.pool is None:
                self.pool = ProcessPoolExecutor(1, mp_context=multiprocessing.get_context('spawn'))
            try:
                return self.pool.submit(self.work, self.model, self.threads, str(audio), expected).result()
            except BrokenProcessPool:
                # The process died (out of memory): a fresh one tries once more.
                self.close()
                if attempt == 2:
                    raise

    def close(self):
        if self.pool is not None:
            self.pool.shutdown()
            self.pool = None

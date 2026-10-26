"""The video worker's loops (ADR 0049). Real PostgreSQL; the adaptation model is a synthetic fixture."""

import asyncio
from types import SimpleNamespace

import pytest

from quant_company.loops import attempt
from quant_company.runtime import video_loops
from quant_company.video.runner import VideoRunner

from .test_video import SimulatedProvider, brief, new_job, video  # noqa: F401


def named(loops):
    return {loop.name: loop for loop in loops}


async def tick(loop):
    return await attempt(loop.name, loop.tick, timeout=loop.timeout, attempts=loop.attempts, retry_seconds=0,
                         lane=loop.lane)


def stub(name):
    return SimpleNamespace(tick=lambda: pytest.fail(f"{name} ticked"), store=SimpleNamespace())


@pytest.mark.integration
async def test_the_committed_plan_is_not_asked_again_after_a_restart(brief, video):  # noqa: F811
    store, _ = video
    job = new_job(brief, video)
    provider = SimulatedProvider()
    loop = named(video_loops(store.company, runner=VideoRunner(store.company, provider=provider),
                             writer=stub("writer")))["video"]
    result = await tick(loop)
    assert store.get(job["id"])["state"] != "queued" and result["job_id"] == str(job["id"])
    assert loop.wait(result) == 2  # still in production: the next stage follows right away
    # Stopping the lane at an operator checkpoint: the restarted worker has no video loop at all.
    store.settings.video_enabled = False
    restarted = VideoRunner(store.company, provider=provider)
    assert "video" not in named(video_loops(store.company, runner=restarted, writer=stub("writer")))
    assert (await restarted.tick())["state"] == "idle"
    assert len(provider.calls) == 1
    with store.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM video_effects WHERE job_id=%s AND state='completed'",
                            (job["id"],)).fetchone()["n"] == 1


@pytest.mark.integration
def test_video_loops_follow_the_flags_and_keep_analytics_and_questions_in_one_lane(video):  # noqa: F811
    store, _ = video
    settings = store.settings
    settings.video_analytics_enabled = False
    assert list(named(video_loops(store.company, runner=stub("video"), writer=stub("writer")))) == [
        "video", "newsletter"]
    settings.video_enabled = False
    assert list(named(video_loops(store.company, writer=stub("writer")))) == ["newsletter"]  # the email stays
    settings.video_enabled = settings.video_analytics_enabled = True
    loops = named(video_loops(store.company, runner=stub("video"), writer=stub("writer"),
                              analytics=stub("analytics")))
    assert list(loops) == ["video", "newsletter", "video-analytics", "video-questions"]
    reads, questions = loops["video-analytics"], loops["video-questions"]
    assert isinstance(reads.lane, asyncio.Semaphore) and questions.tick.__self__.lane is reads.lane
    assert loops["video"].lane is None and loops["newsletter"].lane is None
    # The video: an episode renders for most of an hour; two attempts; a stage in progress continues in 2 s.
    episode = loops["video"]
    assert (episode.timeout, episode.attempts, episode.retry_seconds) == (3 * 3600, 2, 30)
    assert [episode.wait(r) for r in ({"state": "synthesizing"}, {"state": "idle"}, {"state": "blocked"},
                                      {"state": "uncertain"}, None)] == [2, 30, 30, 30, 30]
    # The email writer: one attempt (the dispatcher's fallback covers a failing writer); 15 s while it writes.
    email = loops["newsletter"]
    assert (email.timeout, email.attempts) == (600, 1)
    assert [email.wait(r) for r in ({"state": "ready"}, {"state": "idle"}, {"state": "failed"}, None)] == [
        15, 60, 60, 60]
    # Analytics reads every 30 minutes, 2 minutes after cards went out; new questions are looked for every 5 s.
    assert (reads.timeout, reads.attempts, reads.retry_seconds) == (1800, 2, 30)
    assert [reads.wait(r) for r in ({"cards": 2}, {"cards": 0}, {"cards": "failed:HTTPError"}, None)] == [
        120, 1800, 1800, 1800]
    # Each answer is limited once it holds the analytics lane; waiting for the lane is not.
    assert questions.wait({"state": "idle"}) == 5 and questions.timeout is None

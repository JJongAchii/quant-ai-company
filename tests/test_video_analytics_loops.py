"""Video analytics in the video worker's loops (ADR 0049). Real PostgreSQL; YouTube reads and claude-runtime are
simulated fixtures."""

from types import SimpleNamespace

import pytest

from quant_company.loops import attempt
from quant_company.runtime import video_loops
from quant_company.video.analytics import AnalyticsRunner

from .test_briefing import brief  # noqa: F401
from .test_video import video  # noqa: F401
from .test_video_analytics_runner import SimulatedClient, SimulatedProvider, outbox
from .test_video_analytics_store import analytics  # noqa: F401

STUB = SimpleNamespace(tick=lambda: pytest.fail("not this loop"))


def loops_for(analytics, runner):  # noqa: F811
    return {loop.name: loop for loop in video_loops(analytics.company, runner=STUB, writer=STUB, analytics=runner)}


async def tick(loop):
    return await attempt(loop.name, loop.tick, timeout=loop.timeout, attempts=loop.attempts, retry_seconds=0,
                         lane=loop.lane)


@pytest.mark.integration
async def test_the_analytics_loop_creates_the_reporting_jobs(analytics):  # noqa: F811
    youtube = SimulatedClient()
    reads = loops_for(analytics, AnalyticsRunner(analytics.company, client=youtube, provider=SimulatedProvider()))
    await tick(reads["video-analytics"])
    assert youtube.created == ["channel_reach_basic_a1", "channel_reach_combined_a1"]
    await tick(reads["video-analytics"])
    assert youtube.created == ["channel_reach_basic_a1", "channel_reach_combined_a1"]  # created once


@pytest.mark.integration
async def test_a_question_in_a_delivered_thread_is_answered_by_the_question_loop(brief, video, analytics):  # noqa: F811
    from .test_video_analytics_questions import AnsweringProvider, card_delivered, reply

    provider = AnsweringProvider()
    _, runner = await card_delivered(brief, video, analytics, provider)
    question = reply(analytics)["video_question"]
    loop = loops_for(analytics, runner)["video-questions"]
    assert await tick(loop) == {"state": "answered", "question": question}
    assert outbox(analytics)[-1]["thread_ts"] == "300.001"
    answers = [c for c in provider.calls if c.output_contract == "video_answer_v1"]
    assert await tick(loop) == {"state": "idle"}  # answered once: no longer pending
    assert len([c for c in provider.calls if c.output_contract == "video_answer_v1"]) == len(answers) == 1


class PostingRunner(AnalyticsRunner):
    def __init__(self, company, cards):
        super().__init__(company, client=SimulatedClient(), provider=SimulatedProvider())
        self.cards = cards

    async def tick(self, at=None):
        return {"state": "ok", "cards": self.cards}


@pytest.mark.integration
@pytest.mark.parametrize("cards,seconds", [(2, 120), (0, 1800), ("failed:HTTPError", 1800)])
async def test_the_next_tick_comes_soon_after_cards_went_out(analytics, cards, seconds):  # noqa: F811
    loop = loops_for(analytics, PostingRunner(analytics.company, cards))["video-analytics"]
    assert loop.wait(await tick(loop)) == seconds

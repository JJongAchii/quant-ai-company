"""Each service runs its own loops in-process (docs/adr/0049-in-process-loops-instead-of-temporal.md).

Timeouts, retries and waits are those of the former Temporal workflows. Loops that shared a task queue share a lane,
so they still never tick at the same time. A loop exists only while its feature is enabled."""

import asyncio
import logging
import sys
from datetime import UTC, datetime, timedelta

from .accounts import AccountControl
from .briefing.runner import BriefCollector, BriefEditor
from .company import Company
from .config import Settings
from .data_watch.runner import DataWatchRunner
from .execution import TurnExecutor
from .housing_feed.runner import HousingFeedCollector
from .loops import Loop, QuestionLoop, TurnLoop, run_all
from .news.runner import NewsCollector, NewsDiscovery, NewsEditor
from .quant_feed.runner import COLLECTION_DELAY_SECONDS, QuantFeedCollector, QuantFeedEditor, editorial_delay
from .research.runner import ResearchRunner
from .slack import SlackIngress, SlackOutbox
from .staff.runner import StaffRunner
from .tech_feed.runner import TechFeedCollector
from .trend_feed.runner import TrendFeedCollector, TrendFeedEditor
from .trend_feed.schedule import next_publication

# Video analytics: every 30 minutes, or 2 minutes after cards went out so their diagnosis follows in the thread.
ANALYTICS_TICK, ANALYTICS_FOLLOW_UP = timedelta(minutes=30), timedelta(minutes=2)


def state(result):
    return (result or {}).get("state")


def worker_loops(company, executor=None, control=None):
    """Owner turns, staff practice, model accounts, research reconciliation and (shared) tech feed collection."""
    settings = company.settings
    executor = executor or TurnExecutor(company)
    # Turns and staff practice take one model call at a time, as the worker's shared queue did.
    main = asyncio.Semaphore(1)
    turns = TurnLoop(company, executor, main)
    # Each turn has its own 20-minute limit once it holds the lane (TurnLoop); waiting for the lane is not limited.
    loops = [Loop("turns", turns.tick, timeout=None,
                  delay=lambda result: 1 if state(result) in {None, "idle"} else 0)]
    if settings.company_staff_development_enabled:
        staff = StaffRunner(company, executor.provider)
        loops.append(Loop("staff", staff.tick, timeout=480, attempts=3, retry_seconds=5, lane=main,
                          delay=lambda result: 3 if state(result) in {"running", "completed"} else 60))
    if settings.model_accounts_enabled:
        # Its own lane: owner account commands never wait behind model inference.
        control = control or AccountControl(company, wake=turns.wake)
        loops.append(Loop("accounts", control.tick, timeout=60, attempts=3, retry_seconds=2, delay=2))
    if settings.company_research_enabled:
        research = ResearchRunner(company)
        loops.append(Loop("research", lambda: asyncio.to_thread(research.tick), timeout=300, attempts=3,
                          retry_seconds=5, delay=lambda result: (
                              60 if result is None else 2 if result["state"] in {"completed", "awaiting_audit"} else 30)))
    if settings.tech_feed_enabled and not settings.tech_feed_dedicated_worker:
        loops.append(tech_feed_loop(company))
    return loops, turns


def tech_feed_loop(company, collector=None):
    collector = collector or TechFeedCollector(company)
    return Loop("tech-feed", collector.tick, timeout=480, attempts=3, retry_seconds=5, delay=600)


def data_watch_loops(company, runner=None):
    if not company.settings.data_watch_enabled:
        return []
    runner = runner or DataWatchRunner(company)
    return [Loop("data-watch", runner.tick, timeout=120, attempts=3, delay=60)]


def brief_data_loops(company, collector=None):
    if not company.settings.briefing_enabled:
        return []
    from .briefing.data import BriefDataCollector

    collector = collector or BriefDataCollector(company)
    # A failed read keeps explicit coverage gaps for the edition.
    return [Loop("brief-data", collector.tick, timeout=90, attempts=2, retry_seconds=5, delay=30)]


def housing_feed_loops(company, collector=None):
    if not company.settings.housing_feed_enabled:
        return []
    collector = collector or HousingFeedCollector(company)
    return [Loop("housing-feed", collector.tick, timeout=720, attempts=3, retry_seconds=5, delay=300)]


def digest_wait(result, now=None):
    """The legacy 08:00-only trend digest: poll during the catch-up hour, then wait for the next morning slot."""
    after = (now or datetime.now(UTC)) + timedelta(seconds=60)
    return 60 + max(0.0, (next_publication(after) - after).total_seconds())


def news_loops(company, provider=None):
    """News, briefing editions and trend feed: collectors on their own, every model call in one news-model lane."""
    settings = company.settings
    model = asyncio.Semaphore(1)
    editor = NewsEditor(company, provider)
    loops = []
    if settings.company_news_enabled:
        collector = NewsCollector(company)
        loops += [
            Loop("news-collection", collector.tick, timeout=120, attempts=3, retry_seconds=5, delay=lambda result: (
                60 if result is None else 2 if result.get("article_fetched") or result["state"] == "collected" else 30)),
            Loop("news-editorial", editor.tick, timeout=480, attempts=3, retry_seconds=5, lane=model,
                 delay=lambda result: max(1, min(300, (result or {}).get("next_delay", 300)))),
        ]
        if settings.news_search_enabled:
            discovery = NewsDiscovery(company, editor.provider)
            loops.append(Loop("news-discovery", discovery.tick, timeout=480, attempts=3, retry_seconds=5, lane=model,
                              delay=300))
    if settings.briefing_enabled:
        brief = BriefEditor(company, editor.provider)
        loops += [
            Loop("brief-collection", BriefCollector(company).tick, timeout=300, attempts=3, retry_seconds=5, delay=30),
            # The request/runtime deadline owns the edition window; this outer limit must not truncate it.
            Loop("brief-editorial", brief.tick, timeout=87 * 60, attempts=3, retry_seconds=5, lane=model,
                 delay=lambda result: 1 if state(result) == "completed" else 30),
        ]
    if settings.trend_feed_enabled:
        collection = asyncio.Semaphore(2)
        trends = TrendFeedCollector(company)
        recurring = settings.trend_feed_publication_hours != [8] or settings.trend_feed_on_demand_enabled
        loops += [
            Loop("trend-collection", trends.tick, timeout=540, attempts=3, retry_seconds=5, lane=collection, delay=30),
            Loop("trend-publication" if recurring else "trend-digest", trends.finalize, timeout=60, attempts=3,
                 retry_seconds=5, lane=collection, delay=30 if recurring else digest_wait),
            Loop("trend-editorial", TrendFeedEditor(company, editor.provider).tick, timeout=480, attempts=3,
                 retry_seconds=5, lane=model, delay=60),
        ]
    return loops


def quant_feed_loops(company, provider=None):
    if not company.settings.quant_feed_enabled:
        return []
    return [
        Loop("quant-collection", QuantFeedCollector(company).tick, timeout=480, attempts=3,
             delay=COLLECTION_DELAY_SECONDS),
        Loop("quant-editorial", QuantFeedEditor(company, provider).tick, timeout=1080, attempts=3,
             delay=lambda result: editorial_delay(result or {})),
    ]


def video_loops(company, runner=None, writer=None, analytics=None):
    """The video, the evening email writer and the analytics reads, each in its own lane."""
    from .newsletter.store import NewsletterStore
    from .newsletter.writer import NewsletterWriter

    settings = company.settings
    loops = []
    if settings.video_enabled:
        from .video.runner import VideoRunner

        runner = runner or VideoRunner(company)
        # Rendering an 8-9 minute episode on two cores takes most of an hour.
        loops.append(Loop("video", runner.tick, timeout=3 * 3600, attempts=2, retry_seconds=30, delay=lambda result: (
            2 if state(result) not in {None, "idle", "blocked", "uncertain"} else 30)))
    writer = writer or NewsletterWriter(company)

    async def write():
        if not settings.newsletter_enabled and not await asyncio.to_thread(NewsletterStore(company).has_writing):
            return {"state": "idle"}
        return await writer.tick()

    # One attempt: the dispatcher's fallback covers a writer that keeps failing.
    loops.append(Loop("newsletter", write, timeout=600, delay=lambda result: (
        60 if state(result) in {None, "idle", "failed"} else 15)))
    if settings.video_analytics_enabled:
        from .video.analytics import AnalyticsRunner

        analytics = analytics or AnalyticsRunner(company)
        lane = asyncio.Semaphore(1)
        questions = QuestionLoop(analytics, lane)
        loops += [
            Loop("video-analytics", analytics.tick, timeout=1800, attempts=2, retry_seconds=30, lane=lane,
                 delay=lambda result: (ANALYTICS_FOLLOW_UP if isinstance((result or {}).get("cards"), int)
                                       and result["cards"] > 0 else ANALYTICS_TICK).total_seconds()),
            # Each answer has its own limit once it holds the analytics lane (QuestionLoop).
            Loop("video-questions", questions.tick, timeout=None, delay=5),
        ]
    return loops


async def serve(loops_for, settings=None):
    settings = settings or Settings()
    loops = loops_for(Company(settings))
    # The operator's check that a service started the loops it should (docs/adr/0049).
    print("Running loops:", ", ".join(loop.name for loop in loops) or "none", file=sys.stderr, flush=True)
    await run_all(loops)


async def worker_main(settings=None):
    await serve(lambda company: worker_loops(company)[0], settings)


async def data_watch_worker_main(settings=None):
    await serve(data_watch_loops, settings)


async def brief_data_worker_main(settings=None):
    await serve(brief_data_loops, settings)


async def housing_feed_worker_main(settings=None):
    await serve(housing_feed_loops, settings)


async def news_worker_main(settings=None):
    await serve(news_loops, settings)


async def quant_feed_worker_main(settings=None):
    await serve(quant_feed_loops, settings)


async def video_worker_main(settings=None):
    await serve(video_loops, settings)


async def dispatch_once(company):
    if getattr(company.settings, "briefing_enabled", False):
        from .briefing.store import BriefStore

        try:
            await asyncio.to_thread(BriefStore(company).flush)
        except (ValueError, OSError) as exc:
            logging.getLogger(__name__).error("Briefing configuration unavailable: %s", type(exc).__name__)
        try:
            await send_newsletter(company)
        except Exception as exc:
            # Retried next loop; the email never holds back the brief or its Slack posts.
            logging.getLogger(__name__).error("Newsletter sending failed: %s", type(exc).__name__)
    if getattr(company.settings, "ops_watch_enabled", False):
        from .ops.watch import OpsWatch

        if not hasattr(company, "_ops_watch"):
            company._ops_watch = OpsWatch(company)
        try:
            await asyncio.to_thread(company._ops_watch.tick_if_due)
        except Exception as exc:
            # The watch must never stop replies; the host probe reports a watch that stops ticking.
            logging.getLogger(__name__).error("Ops watch tick failed: %s", type(exc).__name__)


async def dispatch_main(settings=None):
    settings = settings or Settings()
    company = Company(settings)
    outbox = SlackOutbox(company, SlackIngress(settings, company).credentials)
    from .housing_feed.panel import HousingMapPanel

    panel = HousingMapPanel(company, outbox.credentials)
    while True:
        await dispatch_once(company)
        await panel.send_one()
        await outbox.send_one()
        await asyncio.sleep(1.1)


async def send_newsletter(company):
    """The sending tick in a thread of its own, so a slow SMTP or IMAP server never delays a Slack post. The writer
    loop runs in the video worker (video_loops)."""
    from .newsletter.delivery import NewsletterDispatcher

    if not hasattr(company, "_newsletter"):
        company._newsletter = NewsletterDispatcher(company)
    task = getattr(company, "_newsletter_tick", None)
    # It runs at the sending tick's pace (10 s), not on every dispatch loop.
    if (task is not None and not task.done()) or not company._newsletter.due():
        return
    company._newsletter_tick = asyncio.create_task(asyncio.to_thread(company._newsletter.tick_if_due))

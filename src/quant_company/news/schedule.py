"""Automatic news uses Korea time; collection and user replies remain available."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def utcnow():
    return datetime.now(UTC)


def opening(at=None):
    local = (at or utcnow()).astimezone(KST)
    return local.replace(hour=6, minute=0, second=0, microsecond=0)


def quiet(settings, at=None):
    return settings.news_delivery_window_enabled and (at or utcnow()).astimezone(KST).hour < 6


def next_delay(settings):
    if quiet(settings):
        return max(1, min(300, int((opening() - utcnow()).total_seconds())))
    return 300


def overnight_start(at=None):
    return opening(at) - timedelta(hours=6)

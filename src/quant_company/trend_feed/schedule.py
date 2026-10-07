from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def utcnow():
    return datetime.now(UTC)


def times(at=None):
    local = (at or utcnow()).astimezone(KST)
    cutoff = local.replace(hour=7, minute=30, second=0, microsecond=0)
    return cutoff, cutoff.replace(hour=8, minute=0), cutoff.replace(hour=9, minute=0)


def next_publication(at):
    _, send, expiry = times(at)
    return send if at < send else at if at < expiry else send + timedelta(days=1)

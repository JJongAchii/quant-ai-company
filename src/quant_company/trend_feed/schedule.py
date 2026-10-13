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


def slot_times(at, hours):
    """Keep the legacy08:00 workflow helper above unchanged for history replay."""
    local = at.astimezone(KST)
    slots = [local.replace(hour=hour, minute=0, second=0, microsecond=0) for hour in hours]
    send = next((slot for slot in reversed(slots) if slot - timedelta(minutes=30) <= local), slots[0])
    return send - timedelta(minutes=30), send, send + timedelta(hours=1)


def next_slot(at, hours):
    local = at.astimezone(KST)
    slots = [local.replace(hour=hour, minute=0, second=0, microsecond=0) for hour in hours]
    return next((slot for slot in slots if slot > local), slots[0] + timedelta(days=1))

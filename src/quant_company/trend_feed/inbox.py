"""Owner commands for the configured channel, after the outer Slack signature check."""

import re

from .store import TrendFeedStore


def accept(company, payload, event, credential):
    if not company.settings.trend_feed_on_demand_enabled:
        return {"ok": True, "ignored": True, "reason": "trend_feed_delivery_identity_is_not_interactive"}
    if (not isinstance(event, dict) or event.get("type") not in {"message", "app_mention"}
            or event.get("bot_id") or event.get("subtype") or not event.get("user") or not event.get("ts")):
        return {"ok": True, "ignored": True}
    text = event.get("text", "")
    if not isinstance(text, str):
        return {"ok": True, "ignored": True}
    mentions = set(re.findall(r"<@([A-Z0-9]+)>", text))
    if mentions and credential.get("bot_user_id") not in mentions:
        return {"ok": True, "ignored": True}
    command = re.sub(r"<@[A-Z0-9]+>", "", text)
    command = re.sub(r"\s+", "", command).lower().rstrip("?!。.")
    if not re.fullmatch(r"(?:지금|현재|오늘)?(?:실시간검색어|검색트렌드|검색어트렌드|트렌드|trends)"
                        r"(?:좀)?(?:요청|알려줘|보여줘|보내줘|확인|부탁해)?", command):
        return {"ok": True, "ignored": True}
    channel, stamp = event.get("channel", ""), event["ts"]
    return TrendFeedStore(company).request(
        f"slack:{payload['team_id']}:{channel}:{stamp}:trend_scout", owner=event["user"], channel=channel,
        thread_ts=event.get("thread_ts") or stamp)

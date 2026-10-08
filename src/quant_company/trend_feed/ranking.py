"""Rank bounded observations without adding traffic buckets or estimating counts."""

from datetime import timedelta
from itertools import pairwise

from ..company import as_json, fingerprint
from ..news.feeds import timestamp
from .contracts import GOOGLE_CANDIDATE_LIMIT

WINDOW_HOURS = 6
FRESHNESS = timedelta(minutes=30)


def rank_observations(rows, snapshots, cutoff, source_started, *, on_demand=False):
    start = cutoff - timedelta(hours=WINDOW_HOURS)
    snapshots = sorted((s for s in snapshots if start-FRESHNESS <= s["observed_at"] < cutoff),
                       key=lambda s: (s["observed_at"], str(s["id"])))
    latest = snapshots[-1] if snapshots else None

    def covered(boundary):
        before = [s["observed_at"] for s in snapshots if boundary-FRESHNESS <= s["observed_at"] <= boundary]
        after = [s["observed_at"] for s in snapshots if boundary < s["observed_at"] < cutoff]
        return bool(source_started and source_started <= boundary and before
                    and all(b-a <= FRESHNESS for a, b in pairwise([before[-1], *after, cutoff])))

    grouped = {}
    for row in rows:
        if start-FRESHNESS <= row["observed_at"] < cutoff:
            grouped.setdefault(row["keyword"], []).append(row)
    candidates = []
    for keyword, history in grouped.items():
        history.sort(key=lambda r: (r["observed_at"], str(r["snapshot_id"])))
        current = history[-1]
        if current["observed_at"] < start:
            continue
        hours = 1 if on_demand and current["observed_at"] >= cutoff-timedelta(hours=1) else WINDOW_HOURS
        boundary = cutoff-timedelta(hours=hours)
        baseline = next((r for r in reversed(history) if boundary-FRESHNESS <= r["observed_at"] <= boundary), None)
        item = current["item"]
        complete = covered(boundary)
        first_seen = current["first_seen"]
        new = bool(complete and first_seen > boundary)
        change = "new" if new else "unavailable"
        if complete and baseline and timestamp(baseline["item"]["published_at"]) == timestamp(item["published_at"]):
            previous, value = baseline["item"]["traffic_floor"], item["traffic_floor"]
            if previous is not None and value is not None:
                change = "increased" if value > previous else "decreased" if value < previous else "steady"
        observed_latest = bool(latest and latest["id"] == current["snapshot_id"]
                               and cutoff-latest["observed_at"] <= FRESHNESS)
        candidates.append({"id": fingerprint(keyword)[:24], "kind": "rising_search", "title": item["title"],
            "traffic": item["traffic"], "traffic_floor": item["traffic_floor"],
            # Existing preview validators read this receipt field; selection now uses the latest value.
            "peak_snapshot": str(current["snapshot_id"]),
            "latest_snapshot": str(current["snapshot_id"]),
            "first_seen": first_seen, "last_seen": current["observed_at"], "new": new,
            "news": item["news"], "articles": [], "naver": {"state": "unavailable", "reason": "not_checked"},
            "observation": {"window_hours": hours, "in_latest": observed_latest, "change": change,
                "baseline_traffic": baseline["item"]["traffic"] if change in {"increased", "decreased", "steady"} else None,
                "baseline_at": baseline["observed_at"] if change in {"increased", "decreased", "steady"} else None}})
    candidates.sort(key=lambda c: (c["observation"]["window_hours"], not c["observation"]["in_latest"],
        c["observation"]["change"] not in {"new", "increased"}, -(c["traffic_floor"] or 0),
        -c["last_seen"].timestamp(), c["title"]))
    coverage = [s["observed_at"] for s in snapshots]
    gap = not coverage or cutoff-coverage[-1] > FRESHNESS or any(
        b-a > FRESHNESS for a, b in pairwise(coverage))
    if source_started and source_started <= start:
        gap = gap or not covered(start)
    return as_json({"candidates": candidates[:GOOGLE_CANDIDATE_LIMIT], "candidate_scope": "rolling_observations",
        "ranking_window": {"hours": WINDOW_HOURS, "start": start, "end": cutoff,
                           "focus_hours": 1 if on_demand else WINDOW_HOURS},
        "coverage_start": source_started, "partial_history": not source_started or source_started > start,
        "last_success": latest["observed_at"] if latest else None, "collection_gap": gap,
        "snapshot_ids": [str(s["id"]) for s in snapshots]})

# ruff: noqa: F811
from datetime import UTC, datetime, timedelta

import pytest

from quant_company.trend_feed.editor import render, validate_draft
from quant_company.trend_feed.ranking import rank_observations

from .test_trend_feed import ingest, morning, response, trend  # noqa: F401
from .test_trend_feed_recurring import clock, recurring

CUTOFF = datetime(2026, 10, 8, 5, tzinfo=UTC)
STARTED = CUTOFF-timedelta(days=1)


def snapshot(minutes):
    return {"id": str(minutes), "observed_at": CUTOFF-timedelta(minutes=minutes)}


def observation(title, minutes, floor=10000, *, first_seen=STARTED, published_at=STARTED):
    s = snapshot(minutes)
    return {"keyword": title, "snapshot_id": s["id"], "observed_at": s["observed_at"],
            "first_seen": first_seen, "item": {"title": title, "published_at": published_at.isoformat(),
            "traffic": f"{floor:,}+", "traffic_floor": floor, "news": []}}


def ranked(rows, *, snapshots=None, on_demand=False, started=STARTED):
    return rank_observations(rows, snapshots if snapshots is not None else [snapshot(n) for n in range(380, 0, -10)],
                             CUTOFF, started, on_demand=on_demand)


def test_six_hour_asof_boundary_excludes_old_and_future_observations():
    bundle = ranked([observation("too old", 361), observation("boundary", 360),
                     observation("cutoff", 0), observation("future", -10)],
                    snapshots=[snapshot(n) for n in (361, 360, 0, -10)])
    assert [c["title"] for c in bundle["candidates"]] == ["boundary"]
    assert bundle["snapshot_ids"] == ["361", "360"]
    assert datetime.fromisoformat(bundle["last_success"]) == CUTOFF-timedelta(hours=6)


def test_latest_bucket_replaces_peak_and_repeated_samples_do_not_add_volume():
    history = [observation("topic", 360, 100000), observation("topic", 20, 10000), observation("topic", 10, 10000)]
    bundle = ranked(history)
    c = bundle["candidates"][0]
    assert c["traffic_floor"] == 10000 and c["traffic"] == "10,000+"
    assert c["observation"]["change"] == "decreased" and c["observation"]["baseline_traffic"] == "100,000+"
    assert c["latest_snapshot"] == "10"
    assert ranked([history[0], history[-1]])["candidates"] == bundle["candidates"]


def test_one_hour_focus_then_six_hour_fallback_and_fresh_current_list_first():
    rows = [observation("old large", 120, 1000000), observation("recent disappeared", 20, 500000),
            observation("current smaller", 10, 1000)]
    candidates = ranked(rows, on_demand=True)["candidates"]
    assert [c["title"] for c in candidates] == ["current smaller", "recent disappeared", "old large"]
    assert [c["observation"]["window_hours"] for c in candidates] == [1, 1, 6]
    assert [c["observation"]["in_latest"] for c in candidates] == [True, False, False]


def test_new_and_increasing_topics_precede_unchanged_large_topic():
    rows = [observation("steady", 360, 100000), observation("steady", 10, 100000),
            observation("growing", 360, 1000), observation("growing", 10, 5000),
            observation("new", 10, 10000, first_seen=CUTOFF-timedelta(minutes=10))]
    candidates = ranked(rows)["candidates"]
    assert [c["title"] for c in candidates] == ["new", "growing", "steady"]
    assert [c["observation"]["change"] for c in candidates] == ["new", "increased", "steady"]


@pytest.mark.parametrize("problem", ["gap", "partial", "episode", "reentry", "stale"])
def test_incomplete_baseline_or_changed_episode_cannot_claim_growth(problem):
    rows = [observation("topic", 360, 1000), observation("topic", 10, 100000)]
    snapshots = [snapshot(n) for n in range(380, 0, -10)]
    started = STARTED
    if problem == "gap":
        snapshots = [s for s in snapshots if s["id"] not in {"200", "190", "180", "170"}]
    elif problem == "partial":
        started = CUTOFF-timedelta(hours=5)
    elif problem == "episode":
        rows[-1]["item"]["published_at"] = (CUTOFF-timedelta(hours=1)).isoformat()
    elif problem == "reentry":
        rows = rows[1:]
    else:
        snapshots = [s for s in snapshots if int(s["id"]) >= 40]
        rows[-1] = observation("topic", 40, 100000)
    bundle = ranked(rows, snapshots=snapshots, started=started)
    assert bundle["candidates"][0]["observation"]["change"] == "unavailable"
    assert not bundle["candidates"][0]["new"]
    if problem == "stale":
        assert not bundle["candidates"][0]["observation"]["in_latest"] and bundle["collection_gap"]


def test_first_hour_partial_history_is_distinct_from_collection_gap():
    bundle = ranked([observation("new", 10, first_seen=CUTOFF-timedelta(minutes=10))],
                    snapshots=[snapshot(20), snapshot(10)], started=CUTOFF-timedelta(minutes=20))
    assert bundle["partial_history"] and not bundle["collection_gap"]
    assert bundle["candidates"][0]["observation"]["change"] == "unavailable"


def test_postgres_frozen_window_keeps_latest_value_and_provenance(trend):
    clock(trend, 1, 10)
    ingest(trend, title="구간 밖")
    clock(trend, 2)
    ingest(trend, title="이전 관측 주제", traffic="1M+")
    clock(trend, 7, 10)
    ingest(trend, title="현재 관심 주제", traffic="100,000+")
    clock(trend, 7, 20)
    ingest(trend, title="현재 관심 주제", traffic="10,000+")
    morning(trend)
    bundle = trend.freeze()["bundle"]
    assert [c["title"] for c in bundle["candidates"]] == ["현재 관심 주제", "이전 관측 주제"]
    assert bundle["candidates"][0]["traffic_floor"] == 10000
    assert len(bundle["snapshot_ids"]) == 4  # Includes the pre-window baseline, never an out-of-window candidate.
    ingest(trend, title="마감 이후")
    assert trend.freeze()["bundle"] == bundle
    text = render(bundle, validate_draft(response("fixture", bundle), bundle).model_dump())
    assert "10/07 01:30–10/07 07:30 KST" in text and "마지막 관측 02:00 KST" in text
    assert "구간 밖" not in text and "100,000+" not in text and "절대 검색량으로 환산하지 않습니다" in text


def test_on_demand_never_reuses_a_scheduled_six_hour_draft(trend):
    recurring(trend)
    ingest(trend)
    morning(trend)
    claim = trend.claim_enrichment()
    trend.save_enrichment(claim, claim["bundle"])
    call = trend.prepare_call()
    trend.finish_call(call, response(call["id"], claim["bundle"]))
    trend.clock[0] += timedelta(minutes=1)
    requested = trend.request("new-focus", owner="UHUMAN", channel="CTRENDS", thread_ts="new-focus")
    assert not requested["cached"]
    with trend.db.transaction() as conn:
        row = conn.execute("SELECT bundle FROM trend_feed_digests WHERE id=%s", (requested["id"],)).fetchone()
    assert row["bundle"]["ranking_window"]["focus_hours"] == 1

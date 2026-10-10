import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quant_company.briefing import data_reader, schedule
from quant_company.briefing.data import BriefDataCollector
from quant_company.briefing.data_reader import (
    chart_catalogue,
    confirm_us_close,
    parse_chart,
    read_us_close,
    summarize,
    us_close_snapshot,
)
from quant_company.briefing.editor import render, validate
from quant_company.briefing.quality import assurance, reconcile
from quant_company.briefing.store import BriefStore

from .test_briefing import brief, bundle, definition, proposal, seed  # noqa: F401
from .test_briefing_quality import snapshot as lake_snapshot

EDITION = definition()
CLOSE = schedule.close("US", EDITION.us_session)
PRICES = {"^GSPC": ("5300.00", "5200.00"), "^IXIC": ("17100.00", "17000.00"), "^DJI": ("42000.12", "41900.00"),
          "^SOX": ("5100.5", "5000"), "^VIX": ("15.2", "16.1"), "^TNX": ("4.1230", "4.15"),
          "CL=F": ("70.1", "69.0"), "GC=F": ("2400.3", "2390"), "BTC-USD": ("60000.5", "59000"),
          "ETH-USD": ("3000.25", "2900")}


def chart(symbol, price, at, previous="100"):
    session = datetime.combine(EDITION.us_session, datetime.min.time(), UTC)+timedelta(hours=13, minutes=30)
    prior = datetime.combine(EDITION.previous_us_session, datetime.min.time(), UTC)+timedelta(hours=13, minutes=30)
    # Binary float noise, as the provider returns it.
    value = float(price)+1e-9
    return {"ok": True, "sha256": "f"*64, "bytes": 1000, "payload": {"chart": {"result": [{
        "meta": {"symbol": symbol, "regularMarketPrice": value, "regularMarketTime": int(at.timestamp()),
                 "chartPreviousClose": 1.0},
        "timestamp": [int(prior.timestamp()), int(session.timestamp())],
        "indicators": {"quote": [{"close": [float(previous), value]}]}}]}}}


class Charts:
    """Fake provider: one value/time per symbol, mutable between polls. No network."""

    def __init__(self, at=None):
        self.calls = []
        self.at = at or CLOSE+timedelta(seconds=30)
        self.override = {}

    def __call__(self, symbol):
        self.calls.append(symbol)
        price, previous = self.override.get(symbol, PRICES.get(symbol, ("101.50", "100")))
        at = self.at+timedelta(minutes=15) if symbol == "^VIX" and "^VIX" not in self.override else self.at
        return chart(symbol, price, at, previous)


def recorded(charts, slots, stocks=("NVDA",), state=None):
    state = state or {"session": str(EDITION.us_session), "close_at": CLOSE.isoformat(),
                      "catalogue": chart_catalogue(list(stocks)), "polls": []}
    for index in slots:
        at = CLOSE+timedelta(minutes=5+2*index, seconds=10)
        poll = read_us_close(EDITION.model_dump(mode="json"), state["catalogue"], index, charts, clock=lambda at=at: at)
        assert poll["ok"]
        state = {**state, "polls": [*state["polls"], poll]}
    return state


def confirmed(state):
    return confirm_us_close(state["polls"], state["catalogue"], CLOSE)


def test_chart_parsing_rounds_values_and_uses_prior_daily_bar():
    quote = parse_chart(chart("^GSPC", "5300.00", CLOSE, "5200.00")["payload"], "^GSPC", "sp500")
    assert quote["value"] == "5300.00" and quote["previous_close"] == "5200.00"
    assert quote["previous_basis"] == "prior_daily_bar" and quote["previous_session"] == str(EDITION.previous_us_session)
    assert parse_chart(chart("^TNX", "41.23", CLOSE, "41.5")["payload"], "^TNX", "ust10y")["value"] == "4.123"
    with pytest.raises(ValueError, match="chart_symbol_mismatch"):
        parse_chart(chart("^DJI", "1", CLOSE)["payload"], "^GSPC", "sp500")
    with pytest.raises(ValueError, match="chart_schema_rejected"):
        parse_chart({"chart": {"result": []}}, "^GSPC", "sp500")
    # Without a prior bar the provider's previousClose is kept, but undated.
    payload = chart("^GSPC", "5300.00", CLOSE)["payload"]
    payload["chart"]["result"][0].update(timestamp=[], indicators={"quote": [{"close": []}]})
    payload["chart"]["result"][0]["meta"]["previousClose"] = 5200.0
    fallback = parse_chart(payload, "^GSPC", "sp500")
    assert fallback["previous_basis"] == "meta.previousClose" and fallback["previous_session"] is None


def test_confirmation_requires_two_identical_polls_after_close():
    charts = Charts()
    one = confirmed(recorded(charts, [0]))
    assert "sp500" not in one["confirmed"]
    assert {"key": "sp500", "error": "awaiting_second_identical_poll"} in one["errors"]
    two = confirmed(recorded(charts, [0, 1]))
    sp500 = two["confirmed"]["sp500"]
    assert sp500["value"] == "5300.00" and sp500["previous_close"] == "5200.00" and sp500["slots"] == [0, 1]
    assert two["confirmed"]["NVDA"]["group"] == "stock" and two["confirmed"]["SPY"]["group"] == "etf"
    charts.override["^GSPC"] = ("5301.00", "5200.00")
    changed = confirmed(recorded(charts, [2], state=recorded(Charts(), [0, 1])))
    assert "sp500" not in changed["confirmed"]
    assert changed["errors"][0]["error"] == "value_changed_after_confirmation"
    again = confirmed(recorded(charts, [2, 3], state=recorded(Charts(), [0])))
    assert again["confirmed"]["sp500"]["value"] == "5301.00" and again["confirmed"]["sp500"]["slots"] == [2, 3]


def test_pre_close_source_time_is_rejected():
    state = confirmed(recorded(Charts(at=CLOSE-timedelta(minutes=1)), [0, 1, 2]))
    assert "sp500" not in state["confirmed"]
    assert {"key": "sp500", "error": "source_time_before_close"} in state["errors"]


def test_vix_needs_a_source_time_at_or_after_1615_et():
    charts = Charts()
    charts.override["^VIX"] = ("15.2", "16.1")  # source time = close + 30s
    assert "vix" not in confirmed(recorded(charts, [6, 7]))["confirmed"]
    vix = confirmed(recorded(Charts(), [6, 7]))["confirmed"]["vix"]  # close + 15 minutes 30 seconds
    assert vix["value"] == "15.20"
    derived = summarize(us_close_snapshot(recorded(Charts(), [6, 7])), EDITION)
    locked = next(o for o in derived["observations"] if o["instrument"] == "vix")
    assert locked["as_of"] == (CLOSE+timedelta(minutes=15)).isoformat()


def test_continuous_quotes_including_tnx_are_unsettled_and_never_locked():
    state = recorded(Charts(), [0, 1, 2, 3])
    result = confirmed(state)
    for key in ("ust10y", "wti", "gold", "btc", "eth"):
        assert key not in result["confirmed"] and result["unsettled"][key]["source_time"]
    derived = summarize(us_close_snapshot(state), EDITION)
    # A compact index table enters the prompt (VIX is not yet settled here); ETF/stock rows and
    # unsettled quotes stay in the poll receipts.
    assert [o["instrument"] for o in derived["observations"]] == ["sp500", "nasdaq", "dow", "sox"]
    assert derived["contexts"] == []
    doc = derived["documents"][0]
    assert doc["kind"] == "dataset" and doc["registration"] == "yahoo.chart:us_close"
    assert doc["url"] == "https://finance.yahoo.com/" and doc["receipt"]["poll_count"] == 4
    assert len(doc["receipt"]["rows"]) == 4 and "qdata_code_commit" not in doc["receipt"]
    assert doc["content"].splitlines() == [
        "US close 2026-09-21 vs 2026-09-18; two identical post-close polls"
        "|key|symbol|close|change|previous_close|source_time|provider",
        "sp500|^GSPC|5300.00|+1.92%|5200.00|20:00Z|yahoo", "nasdaq|^IXIC|17100.00|+0.59%|17000.00|20:00Z|yahoo",
        "dow|^DJI|42000.12|+0.24%|41900.00|20:00Z|yahoo", "sox|^SOX|5100.50|+2.01%|5000.00|20:00Z|yahoo"]
    assert derived["observations"][0]["evidence"][0]["quote"] == "sp500|^GSPC|5300.00|+1.92%|5200.00|20:00Z|yahoo"
    assert "SPY" not in doc["content"] and "CL=F" not in doc["content"]
    assert doc["receipt"]["rows"][-1]["quotes"]["ust10y"]["value"] == "4.123"
    assert doc["receipt"]["rows"][-1]["quotes"]["wti"]["source_time"] == (CLOSE+timedelta(seconds=30)).isoformat()


def test_fetch_errors_are_fixed_codes_and_do_not_confirm():
    def failing(symbol):
        return {"ok": False, "error": "http_status", "http_status": 429}

    state = recorded(failing, [0, 1])
    assert confirmed(state)["confirmed"] == {}
    assert state["polls"][0]["errors"][0] == {"key": "sp500", "symbol": "^GSPC", "error": "http_status",
                                               "http_status": 429}
    derived = summarize(us_close_snapshot(state), EDITION)
    assert derived["documents"] == [] and derived["observations"] == []
    assert {"dataset": "us_close", "error": "close_not_confirmed",
            "instruments": ["dow", "nasdaq", "sox", "sp500", "vix"]} in derived["diagnostics"]


def snapshot_bundle(state=None, published=None):
    b = bundle(EDITION)
    b["documents"][0]["published_at"] = (published or CLOSE+timedelta(minutes=10)).isoformat()
    b["exchange_closes"] = {"US": CLOSE.isoformat()}
    derived = summarize(us_close_snapshot(state or recorded(Charts(), [6, 7])), EDITION)
    b["documents"] += derived["documents"]
    b.update(locked_observations=derived["observations"], market_context=derived["contexts"],
             data_diagnostics=derived["diagnostics"])
    return b


def test_snapshot_and_one_post_close_article_lock_as_collected():
    b = snapshot_bundle()
    reconciled, conflicts = reconcile(proposal(), b)
    assert conflicts == []
    by = {o.instrument: o for o in reconciled.observations}
    assert by["sp500"].id == "data-sp500" and by["sp500"].value == Decimal("5300.00")
    assert by["sp500"].previous_value == Decimal("5200.00")
    assert [e.source_id for e in by["sp500"].evidence][1:] == ["source-1"]
    assert set(by) == {"sp500", "nasdaq"}
    assert assurance(reconciled, b) == {"sp500": "collected", "nasdaq": "collected"}
    assert validate(reconciled, b) == {}
    # A revision that keeps only the locked copy (with its article evidence) stays locked.
    again, _ = reconcile(reconciled, b)
    assert assurance(again, b) == {"sp500": "collected", "nasdaq": "collected"}


def test_locked_value_left_to_server_insertion_matches_a_named_post_close_sentence():
    b = snapshot_bundle()
    p = proposal()
    p.observations = []
    reconciled, conflicts = reconcile(p, b)
    by = {o.instrument: o for o in reconciled.observations}
    assert conflicts == [] and set(by) == {"sp500", "nasdaq"}
    assert by["sp500"].evidence[1].quote == ("On September 21, 2026, the S&P 500 closed at 5300.00 points "
                                              "versus its previous close of 5200.00.")
    assert assurance(reconciled, b) == {"sp500": "collected", "nasdaq": "collected"}
    assert validate(reconciled, b) == {}
    early = snapshot_bundle(published=CLOSE-timedelta(minutes=30))
    assert reconcile(p, early)[0].observations == []


def test_article_disagreement_withholds_number_with_diagnostic():
    charts = Charts()
    charts.override["^GSPC"] = ("5310.00", "5200.00")
    b = snapshot_bundle(recorded(charts, [0, 1]))
    reconciled, conflicts = reconcile(proposal(), b)
    assert [o.instrument for o in reconciled.observations] == ["nasdaq"]
    snapshot_doc = next(d["id"] for d in b["documents"] if d["registration"] == "yahoo.chart:us_close")
    assert conflicts == [{"instrument": "sp500", "session": "2026-09-21", "resolution": "withheld",
                          "diagnostic": "us_close_snapshot_article_disagreement",
                          "values": [{"id": "data-sp500", "value": "5310.00", "sources": [snapshot_doc]},
                                     {"id": "sp500", "value": "5300.00", "sources": ["source-1"]}]}]
    assert all(c["resolution"] != "collected_data_retained" for c in conflicts)
    b["quote_conflicts"] = conflicts
    parts, quality = render(reconciled, b)
    assert quality["missing_core"] == ["sp500"]
    assert "5310.00 / 5300.00 · 수치 제외" in "\n".join(parts)


def test_pre_close_article_cannot_lock_the_snapshot():
    b = snapshot_bundle(published=CLOSE-timedelta(minutes=30))
    reconciled, conflicts = reconcile(proposal(), b)
    assert conflicts == []
    by = {o.instrument: o for o in reconciled.observations}
    assert by["sp500"].id == "sp500" and assurance(reconciled, b)["sp500"] == "single_source"


def test_without_snapshot_two_outlets_remain_required():
    b = bundle(EDITION)
    reconciled, _ = reconcile(proposal(), b)
    assert assurance(reconciled, b)["sp500"] == "single_source"
    second = {**b["documents"][0], "id": "source-2", "publisher": "Second fixture", "origin_group": "second",
              "url": "https://www.yna.co.kr/fixture-close"}
    b["documents"].append(second)
    p = proposal()
    p.observations[0].evidence.append(p.observations[0].evidence[0].model_copy(update={"source_id": "source-2"}))
    assert assurance(reconcile(p, b)[0], b)["sp500"] == "corroborated"


def test_lake_datasets_are_unchanged_by_the_close_dataset():
    lake = lake_snapshot(EDITION)
    alone = summarize(lake, EDITION)
    polls = us_close_snapshot(recorded(Charts(), [0, 1]))
    combined = summarize({**lake, "datasets": {**lake["datasets"], **polls["datasets"]}}, EDITION)
    assert combined["documents"][0] == alone["documents"][0]
    assert combined["observations"][:len(alone["observations"])] == alone["observations"]
    assert alone["documents"][0]["registration"] == "qdata.api:krx_index"
    assert data_reader.DATASETS == ("krx_index", "prices", "fred", "ecos")


@pytest.fixture
def polling(brief):  # noqa: F811
    store, clock = brief
    store.company.settings.briefing_us_close_enabled = True
    store.company.settings.briefing_us_close_stocks = ["NVDA"]
    return store, clock


def stored(store):
    with store.db.transaction() as conn:
        return conn.execute("SELECT * FROM brief_editions WHERE id=%s", (EDITION.id,)).fetchone()


def test_policy_digest_is_unchanged_while_disabled(brief):  # noqa: F811
    store, _ = brief
    before = store.policy()
    store.company.settings.briefing_us_close_stocks = ["NVDA"]
    assert store.policy() == before
    store.company.settings.briefing_us_close_enabled = True
    assert store.policy() != before


def test_real_postgres_polls_persist_and_restart_is_idempotent(polling):
    store, clock = polling
    charts = Charts()
    clock["at"] = CLOSE+timedelta(minutes=4)
    assert BriefDataCollector(store.company, chart=charts).poll_us_close() is None
    clock["at"] = CLOSE+timedelta(minutes=5, seconds=5)
    assert clock["at"] < EDITION.starts_at  # registered for polling before collection opens
    collector = BriefDataCollector(store.company, chart=charts)
    assert collector.poll_us_close() == {"state": "polled", "slot": 0, "quotes": 20}
    assert store.claim_collection() is None and store.claim_data() is None
    calls = len(charts.calls)
    # Same slot after a worker restart: nothing is re-read or replaced.
    assert BriefDataCollector(store.company, chart=charts).poll_us_close() is None
    assert len(charts.calls) == calls
    target = {"id": EDITION.id, "definition": EDITION.model_dump(mode="json"), "close_at": CLOSE,
              "catalogue": {}, "slot": 0}
    duplicate = {"ok": True, "slot": 0, "started_at": clock["at"].isoformat(), "observed_at": clock["at"].isoformat(),
                 "quotes": {}, "errors": []}
    assert store.save_us_close(target, duplicate) == {"state": "duplicate", "slot": 0}
    clock["at"] = CLOSE+timedelta(minutes=7, seconds=5)
    assert collector.poll_us_close()["slot"] == 1
    saved = stored(store)["market_data"]["us_close"]
    assert [p["slot"] for p in saved["polls"]] == [0, 1]
    assert set(saved["catalogue"]) >= {"sp500", "SPY", "NVDA", "btc", "ust10y"}
    # A lake result replaces only its own fields; the recorded polls remain.
    clock["at"] = EDITION.starts_at+timedelta(minutes=1)
    claim = store.claim_data()
    store.save_data(claim, {"documents": [], "observations": [], "contexts": [], "diagnostics": []})
    assert stored(store)["market_data"]["us_close"] == saved
    # Freeze derives the dataset through the existing locked-observation path; raw polls stay off the prompt.
    seed((store, clock), EDITION)
    request = store.prepare()["request"]
    payload = json.loads(request["prompt"].split("BRIEF DATA JSON:\n")[1])
    frozen = stored(store)["bundle"]
    locked = {o["instrument"]: o for o in frozen["locked_observations"]}
    assert set(locked) == {"sp500", "nasdaq", "dow", "sox"} and locked["sp500"]["value"] == "5300.00"
    assert any(d["title"] == "Collected market data: us_close" for d in payload["documents"])
    assert "rows" not in json.dumps([d["receipt"] for d in payload["documents"]])
    assert any(d["registration"] == "yahoo.chart:us_close" and len(d["receipt"]["rows"]) == 2
               for d in frozen["documents"])


def test_real_postgres_no_poll_after_window_or_cutoff(polling):
    store, clock = polling
    charts = Charts()
    clock["at"] = CLOSE+timedelta(minutes=37)
    assert BriefDataCollector(store.company, chart=charts).poll_us_close() is None
    clock["at"] = EDITION.cutoff
    assert store.us_close_target() is None
    assert charts.calls == []

    def late(symbol):
        clock["at"] = EDITION.cutoff+timedelta(seconds=1)
        return charts(symbol)

    # A short (v6) window: the cutoff passes during a poll, which is then discarded.
    edition = EDITION.model_copy(update={"cutoff": CLOSE+timedelta(minutes=36)})
    clock["at"] = CLOSE+timedelta(minutes=35)
    target = {"id": EDITION.id, "definition": edition.model_dump(mode="json"), "close_at": CLOSE,
              "catalogue": chart_catalogue([]), "slot": 15}
    assert read_us_close(target["definition"], target["catalogue"], 15, late, clock=lambda: clock["at"])["error"] \
        == "data_cutoff_passed"
    observed = {"ok": True, "slot": 15, "started_at": clock["at"].isoformat(),
                "observed_at": EDITION.cutoff.isoformat(), "quotes": {}, "errors": []}
    store.register(CLOSE+timedelta(minutes=35))
    assert store.save_us_close(target, observed)["state"] == "discarded"
    assert not (stored(store)["market_data"] or {}).get("us_close")


def test_disabled_snapshot_never_reads_or_registers_early(brief):  # noqa: F811
    store, clock = brief
    clock["at"] = CLOSE+timedelta(minutes=6)

    def forbidden(symbol):
        raise AssertionError("network read while disabled")

    assert BriefStore(store.company).us_close_target() is None
    assert BriefDataCollector(store.company, chart=forbidden).poll_us_close() is None
    assert EDITION.id not in {e.id for e in store.register()}

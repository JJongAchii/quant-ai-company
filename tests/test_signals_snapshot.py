from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from quant_company.briefing.data_reader import fetch_chart
from quant_company.signals.snapshot import CATALOGUE, follow_us, parse_quote, read_snapshot, snapshot_text

KST, NYZ = ZoneInfo("Asia/Seoul"), ZoneInfo("America/New_York")
OBSERVED = datetime(2026, 11, 19, 6, 0, tzinfo=UTC)


def chart(symbol, tz, price, at, bars, prev=None):
    """bars: [(datetime, open, close)]."""
    meta = {"symbol": symbol, "exchangeTimezoneName": tz, "regularMarketPrice": price,
            "regularMarketTime": int(at.timestamp())}
    if prev is not None:
        meta["previousClose"] = prev
    return {"chart": {"result": [{"meta": meta, "timestamp": [int(b[0].timestamp()) for b in bars],
                                  "indicators": {"quote": [{"open": [b[1] for b in bars],
                                                            "close": [b[2] for b in bars]}]}}]}}


def test_krx_previous_close_uses_seoul_dates():
    bars = [(datetime(2026, 11, 18, 9, tzinfo=KST), 2500, 2510),
            (datetime(2026, 11, 19, 9, tzinfo=KST), 2520, 2530)]
    quote = parse_quote(chart("^KS11", "Asia/Seoul", 2535, datetime(2026, 11, 19, 15, 30, tzinfo=KST), bars),
                        "^KS11")
    assert quote["session_day"] == date(2026, 11, 19)
    assert quote["previous_close"] == "2510.00"
    assert quote["session_open"] == "2520.00"
    assert quote["change_pct"] == "1.00"


def ok(payload):
    return {"ok": True, "payload": payload, "sha256": "ab"*32, "bytes": 1}


def fake(ages, fail=None):
    def fetch(symbol):
        if symbol == fail:
            return {"ok": False, "error": "chart_network_unavailable"}
        tz = "Asia/Seoul" if symbol in {"^KS11", "069500.KS", "^KQ11"} else "America/New_York"
        zone = ZoneInfo(tz)
        at = OBSERVED-timedelta(minutes=ages.get(symbol, 1))
        bars = [(at.astimezone(zone)-timedelta(days=1), 99, 100), (at.astimezone(zone), 100, 101)]
        return ok(chart(symbol, tz, 101, at, bars))
    return fetch


def test_overnight_catalogue_and_delay():
    snap = read_snapshot("overnight", fake({"^KS11": 25, "SPY": 25}), clock=lambda: OBSERVED)
    assert set(snap["quotes"]) == set(CATALOGUE) and not snap["errors"]
    assert snap["quotes"]["kospi"]["delayed"] is True
    assert snap["quotes"]["spy"]["delayed"] is False
    assert "빠짐" not in snapshot_text(snap) and "지연" in snapshot_text(snap)
    intraday = read_snapshot("intraday", fake({}), clock=lambda: OBSERVED)
    assert {"n225", "hsi", "shanghai"}.isdisjoint(intraday["quotes"])
    assert intraday["quotes"]["kospi"]["delayed"] is False


def test_failures_are_listed_not_guessed():
    snap = read_snapshot("overnight", fake({}, fail="^HSI"), clock=lambda: OBSERVED)
    assert snap["errors"] == {"hsi": "chart_network_unavailable"} and "hsi" not in snap["quotes"]
    assert snapshot_text(snap).endswith("빠짐: hsi(chart_network_unavailable)")


def test_follow_us_sign():
    def spy(value, prev):
        return {"quotes": {"spy": {"value": value, "previous_close": prev}}}
    assert follow_us(spy("750.00", "745.00")) == "long"
    assert follow_us(spy("740.00", "745.00")) == "short"
    assert follow_us(spy("745.00", "745.00")) is None
    assert follow_us({"quotes": {}}) is None


class Conn:
    paths = []

    def __init__(self, *a, **k):
        pass

    def request(self, method, path, headers=None):
        Conn.paths.append(path)

    def getresponse(self):
        return type("R", (), {"status": 404})()

    def close(self):
        pass


def test_fetch_chart_default_path_unchanged():
    Conn.paths = []
    fetch_chart("^GSPC", connection_factory=Conn)
    fetch_chart("^GSPC", connection_factory=Conn, params="interval=1d&range=1mo&events=div")
    assert Conn.paths == ["/v8/finance/chart/%5EGSPC?interval=1d&range=5d",
                          "/v8/finance/chart/%5EGSPC?interval=1d&range=1mo&events=div"]


def test_previous_close_falls_back_to_meta_then_none():
    at = datetime(2026, 11, 19, 15, 30, tzinfo=KST)
    only_today = [(datetime(2026, 11, 19, 9, tzinfo=KST), 2520, 2530)]
    quote = parse_quote(chart("^KS11", "Asia/Seoul", 2535, at, only_today, prev=2500), "^KS11")
    assert quote["previous_close"] == "2500.00" and quote["change_pct"] == "1.40"
    quote = parse_quote(chart("^KS11", "Asia/Seoul", 2535, at, only_today), "^KS11")
    assert quote["previous_close"] is None and quote["change_pct"] is None
    snap = {"kind": "intraday", "observed_at": OBSERVED.isoformat(), "errors": {}, "quotes": {"kospi": quote}}
    assert "전일 대비 없음" in snapshot_text(snap)
    assert follow_us({"quotes": {"spy": {"value": "750.00", "previous_close": None}}}) is None


def test_session_open_missing_when_no_bar_for_session_day():
    at = datetime(2026, 11, 19, 15, 30, tzinfo=KST)
    bars = [(datetime(2026, 11, 18, 9, tzinfo=KST), 2500, 2510)]
    quote = parse_quote(chart("^KS11", "Asia/Seoul", 2535, at, bars), "^KS11")
    assert quote["session_open"] is None and quote["previous_close"] == "2510.00"


def test_malformed_payloads_map_to_fixed_codes():
    import pytest
    at = datetime(2026, 11, 19, 15, 30, tzinfo=KST)
    good = chart("^KS11", "Asia/Seoul", 2535, at, [(at, 1, 2)])
    for payload in ({}, {"chart": {"result": []}}, {"chart": {"result": None}}):
        with pytest.raises(ValueError, match="chart_schema_rejected"):
            parse_quote(payload, "^KS11")
    with pytest.raises(ValueError, match="chart_symbol_mismatch"):
        parse_quote(good, "^KQ11")
    bad = chart("^KS11", "Asia/Seoul", 2535, at, [(at, 1, 2)])
    bad["chart"]["result"][0]["indicators"]["quote"][0]["close"] = []
    with pytest.raises(ValueError, match="chart_schema_rejected"):
        parse_quote(bad, "^KS11")
    bad = chart("^KS11", "Asia/Seoul", 2535, at, [(at, 1, 2)])
    bad["chart"]["result"][0]["meta"]["exchangeTimezoneName"] = "Nowhere/Land"
    with pytest.raises(ValueError, match="chart_schema_rejected"):
        parse_quote(bad, "^KS11")
    bad = chart("^KS11", "Asia/Seoul", 0, at, [(at, 1, 2)])
    with pytest.raises(ValueError, match="chart_value_invalid"):
        parse_quote(bad, "^KS11")


def test_rejected_payload_keeps_receipt_and_raw_without_leaking_into_text():
    good = fake({})
    secret = {"chart": {"result": [], "note": "RAW-ONLY-CONTENT"}}

    def fetch(symbol):
        return ok(secret) if symbol == "^HSI" else good(symbol)
    snap = read_snapshot("overnight", fetch, clock=lambda: OBSERVED)
    assert snap["errors"]["hsi"] == "chart_schema_rejected" and "hsi" not in snap["quotes"]
    assert snap["receipts"]["hsi"] == "ab"*32 and snap["raw"]["hsi"] == secret
    assert set(snap["receipts"]) == set(snap["raw"]) == set(CATALOGUE)
    assert "RAW-ONLY-CONTENT" not in snapshot_text(snap)
    assert follow_us({**snap, "raw": {}}) == follow_us(snap) == "long"


def test_failed_fetch_has_no_receipt_and_deadline_is_listed(monkeypatch):
    import threading

    from quant_company.signals import snapshot as module
    snap = read_snapshot("intraday", fake({}, fail="SPY"), clock=lambda: OBSERVED)
    assert "spy" not in snap["receipts"] and "spy" not in snap["raw"]
    release = threading.Event()
    good = fake({})

    def slow(symbol):
        if symbol == "EWY":
            release.wait(5)
        return good(symbol)
    monkeypatch.setattr(module, "CHART_POLL_SECONDS", 0.3)
    snap = read_snapshot("intraday", slow, clock=lambda: OBSERVED)
    release.set()
    assert snap["errors"] == {"ewy": "poll_deadline"}

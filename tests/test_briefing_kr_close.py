"""PM edition from the KRX regular-session close (BRIEFING_KR_CLOSE_ENABLED, schedule v8).

The snapshot tests read tiny clean krx_close_* tables written exactly as the qdata contract states, through the
pinned qdata d6d7d0e `catalog.inspect_dataset` (a verbatim copy, verified by its git blob id) over a local
directory. Only `fsspec` and `qdata.settings` are replaced by local stubs; nothing reaches a network.
"""

import asyncio
import hashlib
import json
import subprocess
import sys
import types
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from quant_company.briefing import data_reader, schedule
from quant_company.briefing.contracts import BRIEFER, BriefProposal, CalendarOverride
from quant_company.briefing.data import BriefDataCollector, query
from quant_company.briefing.data_reader import (
    CLOSE_LABEL,
    INDEX_LABEL,
    SESSION_LABEL,
    read_snapshot,
    summarize,
)
from quant_company.briefing.editor import prompt, render, validate
from quant_company.briefing.quality import assurance, reconcile
from quant_company.briefing.store import BriefStore, priority_pending
from quant_company.company import Company, fingerprint, load_roles
from quant_company.config import Settings

from .test_briefing import DAY, brief, bundle, definition, proposal, response  # noqa: F401
from .test_briefing_quality import collected_pm, snapshot

KST = schedule.KST
PREVIOUS = schedule.previous("KR", DAY)
CLOSE = datetime.combine(DAY, time(15, 30), KST)
PINNED_CATALOG = Path(__file__).parent/"fixtures/qdata/catalog_d6d7d0e.py.txt"
# `git rev-parse d6d7d0e:src/qdata/catalog.py` in quant-data (deploy/qdata-source.json pins d6d7d0e).
PINNED_CATALOG_BLOB = "9ee4f0ea7a78019a457516fc5de567316d8dccf4"
# Computed on 1628c16 (timer/readability plus AM v7 integration) with the switch absent; scratch reproduction is the body of
# test_switch_off_is_byte_identical_to_the_v7_base. An unrelated contract change must recompute them there.
BASE = {
    "early_write_prompt": "7477551908c6c7c7834b3a425e66d5fad5fc6cb4d169dd699926bc7c9fc133f3",
    "editions": "e0137f6acff073ddf7b2497c11ed93a000d2ab4311b8446837efa5bda98a9860",
    "policy": "15290d86bd86553bfb5ee2d6f8fefa0c438b259dd8ad8be9392fa86fe0367bdb",
    "render": "f5aa26e983100360bfc0e69044a627331d2eb6d93c538ad03920c594b70f4fbc",
    "review_prompt": "23e5f15ea565fa7f31f59e189533f35a5cdd9d86e5283e4a5eda2f2402684dbd",
    "summaries": "9e896b9237bb74c950e4a5e9cce1a80baf1ef53672732b2492be6c4b2ffe5b9a",
    "write_prompt": "362122b5d1467de44de5108fd271ad37d0503d007e15f64e52908a922ca69830"
}


def kst(day, hour, minute, second=0):
    return datetime.combine(day, time(hour, minute, second), KST)


def stamp(at):
    """collected_at exactly as qdata writes it: UTC ISO-8601 with offset, seconds."""
    return at.astimezone(UTC).isoformat(timespec="seconds")


def pm(day=DAY, anchor=(20, 40, 115), changes=None):
    return next(e for e in schedule.editions(day, "CQUANT", "UHUMAN", changes, kr_close_anchor=anchor)
                if e.kind == "pm")


def hm(value):
    return value.astimezone(KST).strftime("%H:%M")


# ---------------------------------------------------------------- pinned qdata over a local lake


class LocalFS:
    """The fsspec surface the pinned catalog uses, over local files. `changing` names objects whose identity
    differs on the catalog's own post-read refresh (an object replaced during inspection)."""

    def __init__(self):
        self.changing = set()

    def info(self, path, refresh=False, **kwargs):
        file = Path(path)
        content = file.read_bytes()  # FileNotFoundError, like a missing object
        etag = hashlib.sha256(content).hexdigest()[:32]
        if refresh and file.stem in self.changing:
            etag = "replaced-"+etag[:23]
        return {"name": path, "type": "file", "size": len(content), "ETag": etag, "VersionId": None,
                "mtime": None}

    def open(self, path, mode="rb", **kwargs):
        return open(path, mode)


class PinnedApi:
    """qdata.api at d6d7d0e for the two functions the briefing reads: inspect_dataset is the pinned catalog;
    load_krx_stock_master is the pinned body (whole clean table, latest vintage on or before asof)."""

    def __init__(self, root, catalog):
        self.root, self.catalog, self.calls = root, catalog, []

    def inspect_dataset(self, dataset, *, sample_rows=0, columns=None):
        self.calls.append(("inspect_dataset", dataset, sample_rows, columns))
        return self.catalog.inspect_dataset(dataset, sample_rows=sample_rows, columns=columns)

    def load_krx_stock_master(self, asof=None):
        self.calls.append(("load_krx_stock_master", asof))
        df = pd.read_parquet(self.root/"clean"/"krx_stock_master.parquet")
        if df.empty:
            return df.reset_index(drop=True)
        if asof:
            avail = df.loc[df["asof"] <= pd.Timestamp(asof), "asof"]
            if avail.empty:
                raise KeyError(asof)
            selected = avail.max()
        else:
            selected = df["asof"].max()
        return df[df["asof"] == selected].reset_index(drop=True)


@pytest.fixture
def lake(tmp_path, monkeypatch):
    fs = LocalFS()
    fsspec = types.ModuleType("fsspec")
    fsspec.core = types.SimpleNamespace(url_to_fs=lambda root, **options: (fs, root))
    qdata = types.ModuleType("qdata")
    settings = types.ModuleType("qdata.settings")
    settings.lake_root = lambda: str(tmp_path)
    qdata.settings = settings
    for name, module in (("fsspec", fsspec), ("qdata", qdata), ("qdata.settings", settings)):
        monkeypatch.setitem(sys.modules, name, module)
    catalog = types.ModuleType("qdata.catalog")
    exec(compile(PINNED_CATALOG.read_bytes(), "qdata/catalog.py", "exec"), catalog.__dict__)
    (tmp_path/"clean").mkdir()
    api = PinnedApi(tmp_path, catalog)
    api.fs = fs
    return api


# Values like a real session: KOSPI -1.29%, KOSDAQ +0.62%.
INDEX = {DAY: {"KOSPI": ("2600.12", "2634.10", 456780000, 12345600000000),
               "KOSDAQ": ("850.55", "845.30", 987650000, 8765400000000)},
         PREVIOUS: {"KOSPI": ("2634.10", "2620.00", 400000000, 11000000000000),
                    "KOSDAQ": ("845.30", "850.00", 900000000, 8000000000000)}}
FLOWS = {"KOSPI": {"indiv": 123400000000, "frgn": -234567000000, "inst": 100000000000, "corp": 11167000000},
         "KOSDAQ": {"indiv": -50000000000, "frgn": 30000000000, "inst": 15000000000, "corp": 5000000000}}
TOP = [("005930", "KOSPI", 268500, -1.29, 5600000000000), ("000660", "KOSPI", 512000, 2.1, 3100000000000),
       ("0126Z0", "KOSDAQ", 15230, 29.98, 900000000000), ("247540", "KOSDAQ", 188000, -3.5, 800000000000),
       ("373220", "KOSPI", 401500, 0.0, 700000000000), ("035420", "KOSPI", 215000, 1.18, 600000000000)]
NAMES = {"005930": "삼성전자", "000660": "SK하이닉스", "0126Z0": "신규상장", "247540": "에코프로비엠",
         "373220": "LG에너지솔루션", "035420": "NAVER"}


def write(api, name, frame):
    # As qdata's write_clean: pandas.to_parquet without index, small row groups (contract: <= 4096).
    frame.to_parquet(api.root/"clean"/f"{name}.parquet", index=False, row_group_size=4096)


def index_frame(days=(DAY, PREVIOUS), at=None, values=None):
    rows = []
    for position, day in enumerate(days):
        session = values if values and not position else INDEX[(DAY, PREVIOUS)[min(position, 1)]]
        for symbol, (close, prior, volume, value) in session.items():
            rows.append({"date": pd.Timestamp(day), "index": symbol, "open": float(prior), "high": float(close)+5,
                         "low": float(close)-5, "close": float(close), "prev_close": float(prior),
                         "volume": volume, "value": value, "collected_at": stamp(at or kst(day, 15, 40, 12)),
                         "basis": "정규장 기준"})
    frame = pd.DataFrame(rows)
    frame["chg_pct"] = ((frame["close"]/frame["prev_close"]-1.0)*100.0).round(2)
    frame["date"] = pd.to_datetime(frame["date"]).astype("datetime64[ns]")
    columns = ["date", "index", "open", "high", "low", "close", "prev_close", "chg_pct", "volume", "value",
               "collected_at", "basis"]
    return frame[columns].sort_values(["date", "index"], ascending=[False, True], kind="mergesort")


def price_frame(days=(DAY, PREVIOUS), at=None, top=TOP):
    rows = []
    for day in days:
        listed = [*top, *((f"9{i:05d}", "KOSDAQ", 1000+i, 0.5, 1000000000-i) for i in range(30))]
        for ticker, market, close, change, value in listed:
            rows.append({"date": pd.Timestamp(day), "ticker": ticker, "market": market, "open": close, "high": close,
                         "low": close, "close": close, "chg_pct": change, "volume": value//max(close, 1),
                         "value": value, "collected_at": stamp(at or kst(day, 15, 40, 12)), "basis": "정규장 기준",
                         "close_basis": "정규장 종가"})
    frame = pd.DataFrame(rows)
    for column in ("open", "high", "low", "close", "volume", "value"):
        frame[column] = frame[column].astype("int64")
    frame["chg_pct"] = frame["chg_pct"].astype("float64").round(2)
    frame["date"] = pd.to_datetime(frame["date"]).astype("datetime64[ns]")
    return frame.sort_values(["date", "value", "ticker"], ascending=[False, False, True], kind="mergesort")


def flow_frame(days=(DAY, PREVIOUS), at=None):
    order = ("indiv", "frgn", "inst", "corp")
    rows = [{"date": pd.Timestamp(day), "market": market, "investor": investor, "net_value": FLOWS[market][investor],
             "collected_at": stamp(at or kst(day, 15, 40, 12)), "basis": "정규장 기준", "_rank": order.index(investor)}
            for day in days for market in ("KOSPI", "KOSDAQ") for investor in order]
    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["date"]).astype("datetime64[ns]")
    frame["net_value"] = frame["net_value"].astype("int64")
    frame = frame.sort_values(["date", "market", "_rank"], ascending=[False, True, True], kind="mergesort")
    return frame[["date", "market", "investor", "net_value", "collected_at", "basis"]]


def master_frame(vintages=None):
    vintages = vintages or {DAY-timedelta(days=1): NAMES, DAY: {**NAMES, "005930": "당일이름"}}
    rows = [{"asof": pd.Timestamp(day), "ticker": ticker, "market": "KOSPI", "name": name, "sector": "전기·전자",
             "mktcap": 1} for day, names in vintages.items() for ticker, name in names.items()]
    frame = pd.DataFrame(rows)
    frame["mktcap"] = frame["mktcap"].astype("Int64")
    return frame.sort_values(["asof", "market", "ticker"]).reset_index(drop=True)


def publish(api, **frames):
    tables = {"krx_close_index": index_frame(), "krx_close_prices": price_frame(), "krx_close_flows": flow_frame(),
              "krx_stock_master": master_frame()}
    tables.update(frames)
    for name, frame in tables.items():
        if frame is not None:
            write(api, name, frame)
    return api


def read(api, edition=None, at=None):
    edition = edition or pm()
    result = read_snapshot(edition.model_dump(mode="json"), api, clock=lambda: at or edition.starts_at,
                           krx_close=True)
    # The reader subprocess prints exactly this; NaN or non-JSON values would fail here as in production.
    return json.loads(json.dumps(result, ensure_ascii=False, allow_nan=False))


def derived(api, edition=None, at=None):
    edition = edition or pm()
    return summarize(read(api, edition, at), edition)


def frozen(result, edition=None, documents=None):
    edition = edition or pm()
    data = bundle(edition)
    data["documents"] += [*result["documents"], *(documents or [])]
    data.update(locked_observations=result["observations"], market_context=result["contexts"],
                data_diagnostics=result["diagnostics"],
                exchange_closes={"KR": schedule.close("KR", edition.kr_session).isoformat()})
    return data


def empty():
    return BriefProposal(summary=[], observations=[], issues=[], internals=[], watchpoints=[], watch_results=[],
                         calendar=[])


def errors(result):
    return [(d["dataset"], d["error"]) for d in result["diagnostics"]]


# ---------------------------------------------------------------- switch off: nothing changes


def test_switch_off_is_byte_identical_to_the_v7_base():
    days = [date(2026, 9, 14)+timedelta(days=i) for i in range(70)]
    editions = [e.model_dump(mode="json") for d in days for e in schedule.editions(d, "CQUANT", "UHUMAN")]
    off = schedule.kr_close_minutes(Settings())
    assert off is None and editions == [e.model_dump(mode="json") for d in days for e in schedule.editions(
        d, "CQUANT", "UHUMAN", kr_close_anchor=off)]
    summaries = [summarize(snapshot(e), e) for e in (definition("pm"), collected_pm(), definition("am"))]

    def pm_bundle(edition):
        data = bundle(edition)
        result = summarize(snapshot(edition), edition)
        data["documents"] += result["documents"]
        data.update(locked_observations=result["observations"], market_context=result["contexts"],
                    data_diagnostics=result["diagnostics"],
                    exchange_closes={"KR": schedule.close("KR", edition.kr_session).isoformat()})
        return data

    data = pm_bundle(collected_pm())
    written = proposal()
    written.observations = []
    reconciled, conflicts = reconcile(written, data)
    parts, quality = render(reconciled, data)
    settings = Settings(database_url="postgresql://unused.invalid/none",
                        operator_token="test-token-with-more-than-24-characters", model_provider="fixture",
                        fixture_mode=True, slack_team_id="TTEST", slack_allowed_users=["UHUMAN"],
                        slack_allowed_channels=["CQUANT"], briefing_enabled=True, briefing_publish_enabled=True,
                        briefing_search_enabled=False, briefing_channel_id="CQUANT", briefing_owner_user="UHUMAN")
    company = Company(settings, {})
    company.roles[BRIEFER] = load_roles(Settings(briefing_enabled=True))[BRIEFER]
    assert {
        "editions": fingerprint(editions),
        "summaries": fingerprint(summaries),
        "write_prompt": fingerprint(prompt(data, "write")),
        "early_write_prompt": fingerprint(prompt(pm_bundle(definition("pm")), "write")),
        "review_prompt": fingerprint(prompt(data, "review", reconciled.model_dump(mode="json"))),
        "render": fingerprint([parts, quality, conflicts]),
        "policy": BriefStore(company).policy(),
    } == BASE
    assert schedule.version(Settings()) == 6 and schedule.version(Settings(briefing_us_close_enabled=True)) == 7


def test_switch_off_reader_request_and_snapshot_are_unchanged(monkeypatch):
    seen = []

    def run(command, **kwargs):
        seen.append(json.loads(kwargs["input"]))
        return types.SimpleNamespace(returncode=0, stdout="{}")

    monkeypatch.setattr("quant_company.briefing.data.subprocess.run", run)
    edition = definition("pm")
    assert query("s3://example/qdata", edition) == {}
    assert query("s3://example/qdata", edition, krx_close=True) == {}
    assert seen == [{"definition": edition.model_dump(mode="json")},
                    {"definition": edition.model_dump(mode="json"), "krx_close": True}]

    # Without the flag the reader never touches krx_close or the stock master (the base lake loop only).
    class Base:
        def inspect_dataset(self, name, **kwargs):
            assert not kwargs and not name.startswith(("krx_close", "krx_stock"))
            raise FileNotFoundError(name)

    result = read_snapshot(edition.model_dump(mode="json"), Base(), clock=lambda: edition.starts_at)
    assert "krx_close" not in result["datasets"] and len(result["errors"]) == 4
    am_edition = definition("am")
    result = read_snapshot(am_edition.model_dump(mode="json"), Base(), clock=lambda: am_edition.starts_at,
                           krx_close=True)
    assert "krx_close" not in result["datasets"]


def test_reader_main_passes_the_flag_only_when_requested(monkeypatch, capsys):
    calls = []
    monkeypatch.setitem(sys.modules, "qdata", types.SimpleNamespace(api="pinned-api"))
    monkeypatch.setattr(data_reader, "read_snapshot", lambda definition, api, **kwargs: calls.append(kwargs) or {})
    for request in ({"definition": {}}, {"definition": {}, "krx_close": True}, {"definition": {}, "krx_close": "yes"}):
        monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(read=lambda size, r=request: json.dumps(r)))
        data_reader.main()
    assert calls == [{"krx_close": False}, {"krx_close": True}, {"krx_close": False}]
    capsys.readouterr()


# ---------------------------------------------------------------- settings and schedule v8


def test_settings_defaults_basis_and_window_validation(monkeypatch):
    s = Settings()
    assert (s.briefing_kr_close_enabled, s.briefing_kr_close_start_minutes, s.briefing_kr_close_cutoff_minutes,
            s.briefing_kr_close_due_minutes, s.briefing_kr_close_basis) == (False, 20, 40, 115, "regular")
    for basis in ("after_market", "after-market", "Regular", ""):
        with pytest.raises(ValidationError, match="BRIEFING_KR_CLOSE_BASIS must be 'regular'"):
            Settings(briefing_kr_close_basis=basis)
    monkeypatch.setenv("BRIEFING_KR_CLOSE_BASIS", "after_market")
    with pytest.raises(ValidationError, match="after-market"):
        Settings()
    monkeypatch.setenv("BRIEFING_KR_CLOSE_BASIS", "regular")
    for start, cutoff, due in ((40, 40, 115), (20, 115, 115), (50, 40, 115)):
        with pytest.raises(ValidationError, match="START_MINUTES < BRIEFING_KR_CLOSE_CUTOFF_MINUTES"):
            Settings(briefing_kr_close_start_minutes=start, briefing_kr_close_cutoff_minutes=cutoff,
                     briefing_kr_close_due_minutes=due)
    monkeypatch.setenv("BRIEFING_KR_CLOSE_ENABLED", "true")
    monkeypatch.setenv("BRIEFING_KR_CLOSE_DUE_MINUTES", "100")
    assert schedule.kr_close_minutes(Settings()) == (20, 40, 100)


def test_v8_default_minutes_follow_the_actual_krx_close():
    e = pm()
    assert [hm(e.starts_at), hm(e.cutoff), hm(e.due_at), hm(e.expires_at)] == ["15:50", "16:10", "17:25", "18:25"]
    assert e.starts_at.tzinfo == KST and e.cutoff == CLOSE+timedelta(minutes=40)
    base = next(x for x in schedule.editions(DAY, "CQUANT", "UHUMAN") if x.kind == "pm")
    assert e.id == base.id and e.model_dump(exclude={"starts_at", "cutoff", "due_at", "expires_at"}) == base.model_dump(
        exclude={"starts_at", "cutoff", "due_at", "expires_at"})
    # The AM edition never moves with the PM anchor.
    assert [x for x in schedule.editions(DAY, "C", "U", kr_close_anchor=(20, 40, 115)) if x.kind == "am"] == [
        x for x in schedule.editions(DAY, "C", "U") if x.kind == "am"]


def test_v8_custom_minutes_and_a_late_close_move_together():
    e = pm(anchor=(10, 30, 90))
    assert [hm(e.starts_at), hm(e.cutoff), hm(e.due_at)] == ["15:40", "16:00", "17:00"]
    day = date(2026, 11, 19)  # CSAT day: the official notice moves the close to 16:30.
    change = CalendarOverride(market="KR", day=day, close_at="2026-11-19T16:30:00+09:00",
                              source_url="https://global.krx.co.kr/fixture-official-notice")
    late = pm(day, changes={("KR", day): change})
    assert [hm(late.starts_at), hm(late.cutoff), hm(late.due_at)] == ["16:50", "17:10", "18:25"]
    closed = change.model_copy(update={"closed": True, "close_at": None})
    assert [x.kind for x in schedule.editions(day, "C", "U", {("KR", day): closed}, kr_close_anchor=(20, 40, 115))] == [
        "am"]


def test_v8_version_is_reported_only_when_switched_on():
    on = Settings(briefing_kr_close_enabled=True)
    assert schedule.version(on) == "6+8:20/40/115"
    assert schedule.version(Settings(briefing_kr_close_enabled=True, briefing_us_close_enabled=True)) == "7+8:20/40/115"
    assert schedule.version(Settings(briefing_kr_close_enabled=True, briefing_kr_close_start_minutes=10,
                                     briefing_kr_close_cutoff_minutes=30, briefing_kr_close_due_minutes=90)) == "6+8:10/30/90"
    at = kst(DAY, 12, 0)
    edition = next(e for e in schedule.scheduled(on.model_copy(update={"briefing_channel_id": "CQUANT",
                                                                       "briefing_owner_user": "UHUMAN"}), at)
                   if e.kind == "pm" and e.day == DAY)
    assert edition == pm()


# ---------------------------------------------------------------- snapshot read through the pinned catalog


def test_pinned_catalog_copy_is_the_d6d7d0e_blob():
    content = PINNED_CATALOG.read_bytes()
    blob = hashlib.sha1(b"blob %d\0" % len(content)+content).hexdigest()  # The git object id.
    assert blob == PINNED_CATALOG_BLOB
    pin = json.loads((Path(__file__).parents[1]/"deploy/qdata-source.json").read_text())
    assert pin["commit"].startswith("d6d7d0e")
    # load_krx_stock_master exists at d6d7d0e (api.py:189) but stays off this list: quant_feed_release
    # refuses any pin-file change, so the PM switch must not touch deploy/qdata-source.json.
    assert "qdata.api.inspect_dataset" in pin["publicFunctions"]


def test_valid_snapshot_is_read_with_bounded_samples_and_locks_the_regular_close(lake):
    publish(lake)
    edition = pm()
    raw = read(lake, edition)
    assert [c for c in lake.calls if c[0] == "load_krx_stock_master"] == [("load_krx_stock_master", "2026-09-21")]
    samples = [c for c in lake.calls if c[0] == "inspect_dataset" and c[2]]
    assert samples == [("inspect_dataset", name, rows, columns) for name, rows, columns, _ in (
        data_reader.KRX_CLOSE_TABLES[t] for t in ("index", "prices", "flows"))]
    assert all(rows <= 20 and len(columns) <= 8 for _, _, rows, columns in samples)
    snapshot_rows = raw["datasets"]["krx_close"]["rows"]
    assert {table: len(rows) for table, rows in snapshot_rows.items()} == {"index": 2, "prices": 20, "flows": 8}
    assert raw["datasets"]["krx_close"]["master"]["asof"] == "2026-09-21"
    assert raw["datasets"]["krx_close"]["master"]["vintage"] == "2026-09-21"
    result = summarize(raw, edition)
    assert not [e for e in errors(result) if e[0].startswith(("krx_close", "krx_stock"))]
    locked = {o["instrument"]: o for o in result["observations"]}
    assert set(locked) == {"kospi", "kosdaq", "kr_foreign", "kr_institution"}
    assert (locked["kospi"]["value"], locked["kospi"]["previous_value"], locked["kospi"]["reported_change"]) == (
        "2600.12", "2634.1", "-1.29")
    assert locked["kosdaq"]["reported_change"] == "0.62" and datetime.fromisoformat(locked["kospi"]["as_of"]) == CLOSE
    assert locked["kospi"]["previous_session_date"] == str(PREVIOUS)
    assert (locked["kr_foreign"]["value"], locked["kr_institution"]["value"]) == ("-2345.67", "1000.00")
    doc = next(d for d in result["documents"] if d["registration"] == "qdata.api:krx_close")
    assert doc["receipt"]["qdata_code_commit"] and doc["receipt"]["display"]["session"] == str(DAY)
    assert f"KOSPI | session={DAY} | close=2600.12 pt | chg_pct=-1.29% | previous_session={PREVIOUS}" in doc["content"]
    assert "15:40 KST 수집" in doc["content"] and SESSION_LABEL in doc["content"]
    assert f"| previous_close=2634.1 pt | {INDEX_LABEL}" in doc["content"]
    # Alone, the lagging index quote is never published: only the collected flows lock.
    data = frozen(result, edition)
    reconciled, conflicts = reconcile(empty(), data)
    assert not conflicts and validate(reconciled, data) == {}
    assert assurance(reconciled, data) == dict.fromkeys(("kr_foreign", "kr_institution"), "collected")
    # Beside a post-close report of the same closes, the snapshot levels lock as collected.
    data = frozen(result, edition, [closing(edition)])
    reconciled, conflicts = reconcile(reports(edition), data)
    assert not conflicts and validate(reconciled, data) == {}
    assert assurance(reconciled, data) == dict.fromkeys(("kosdaq", "kospi", "kr_foreign", "kr_institution"), "collected")
    kospi = next(o for o in reconciled.observations if o.instrument == "kospi")
    assert kospi.id == "data-kospi" and [e.source_id for e in kospi.evidence] == [doc["id"], "article-1"]
    # The writer sees the labelled table but never the snapshot index levels as locked values, the display
    # rows, names or master receipt.
    payload = json.loads(prompt(data, "write").split("BRIEF DATA JSON:\n")[1])
    shown = next(d for d in payload["documents"] if d["id"] == doc["id"])
    assert set(shown["receipt"]) <= {"source", "qdata_code_commit", "note"} and CLOSE_LABEL in shown["content"]
    assert {o["instrument"] for o in payload["locked_observations"]} == {"kr_foreign", "kr_institution"}
    review = json.loads(prompt(data, "review", reconciled.model_dump(mode="json")).split("BRIEF DATA JSON:\n")[1])
    assert len(review["locked_observations"]) == 4


def test_brief_lines_carry_the_regular_close_and_session_labels(lake):
    publish(lake)
    edition = pm()
    data = frozen(derived(lake), edition, [closing(edition)])
    reconciled, _ = reconcile(reports(edition), data)
    parts, quality = render(reconciled, data)
    main = parts[0]
    assert "*주요 숫자* · 한국 지수 2026-09-22 종가" in main
    block = main.split("*주요 숫자* · 한국 지수 2026-09-22 종가\n", 1)[1].split("\n내용 확인 중", 1)[0]
    # The first citation of the snapshot links it; later rows repeat the plain reference number.
    assert block.splitlines() == [
        "• *코스피* 2,600.12 pt · -1.29% · 정규장 종가 기준 <https://data.krx.co.kr/|[2]> "
        "<https://www.yna.co.kr/view/fixture-kospi-close|[3]>",
        "• *코스닥* 850.55 pt · +0.62% · 정규장 종가 기준 [2] [3]",
        "• *코스피 거래* 거래량 4억 5,678만주 · 거래대금 12조 3,456억원 · 장 마감 기준 · 시간외(16:00~20:00) 미포함 [2]",
        "• *코스닥 거래* 거래량 9억 8,765만주 · 거래대금 8조 7,654억원 · 장 마감 기준 · 시간외(16:00~20:00) 미포함 [2]",
        "• *코스피 수급* 개인 +1,234억원 · 외국인 -2,346억원 · 기관 +1,000억원 · 장 마감 기준 · 시간외(16:00~20:00) 미포함 [2]",
        "• *코스닥 수급* 개인 -500억원 · 외국인 +300억원 · 기관 +150억원 · 장 마감 기준 · 시간외(16:00~20:00) 미포함 [2]",
        "*거래대금 상위 종목*",
        "• 삼성전자(005930) 정규장 종가 기준 268,500원 · -1.29% [2]",
        "• SK하이닉스(000660) 정규장 종가 기준 512,000원 · +2.10% [2]",
        "• 신규상장(0126Z0) 정규장 종가 기준 15,230원 · +29.98% [2]",
        "• 에코프로비엠(247540) 정규장 종가 기준 188,000원 · -3.50% [2]",
        "• LG에너지솔루션(373220) 정규장 종가 기준 401,500원 · +0.00% [2]"]
    # Viewer-facing snapshot rows name no system, model or tool.
    for word in ("AI", "모델", "model", "Claude", "도구", "qdata", "snapshot", "스냅샷"):
        assert word not in block
    details = "\n".join(parts[1:])
    assert "KRX 외국인 순매수 -2,345.67 억원 · 장 마감 기준 · 시간외(16:00~20:00) 미포함 · 09/22 15:30 KST" in details
    assert "확인 부족" not in main and not quality.get("quote_conflicts")


@pytest.mark.parametrize(("frames", "table", "error"), [
    # The newest session in the file is the previous trading day: nothing for this edition.
    ({"krx_close_index": index_frame(days=(PREVIOUS,))}, "krx_close_index", "not_current_session"),
    # Collected at 16:00:00 KST: the after-market session has begun.
    ({"krx_close_flows": flow_frame(days=(DAY,), at=kst(DAY, 16, 0))}, "krx_close_flows",
     "collected_after_regular_session"),
    # Collected before the actual close C.
    ({"krx_close_prices": price_frame(days=(DAY,), at=kst(DAY, 15, 29, 59))}, "krx_close_prices",
     "collected_before_close"),
])
def test_a_table_outside_the_session_window_is_rejected_whole(lake, frames, table, error):
    publish(lake, **frames)
    result = derived(lake)
    assert (table, error) in errors(result)
    doc = next(d for d in result["documents"] if d["registration"] == "qdata.api:krx_close")
    rejected = {"krx_close_index": "KOSPI | session=", "krx_close_flows": "투자자별 순매수",
                "krx_close_prices": "거래대금 상위"}[table]
    assert rejected not in doc["content"]
    instruments = {o["instrument"] for o in result["observations"]}
    assert instruments == {"kospi", "kosdaq", "kr_foreign", "kr_institution"}-{
        "krx_close_index": {"kospi", "kosdaq"}, "krx_close_flows": {"kr_foreign", "kr_institution"},
        "krx_close_prices": set()}[table]


def test_a_sample_mixing_sessions_rejects_the_table(lake):
    # Only six of today's eight flow rows: the bounded sample reaches yesterday's rows.
    flows = flow_frame()
    today = flows[flows["date"] == pd.Timestamp(DAY)]
    publish(lake, krx_close_flows=pd.concat([today.iloc[:6], flows[flows["date"] != pd.Timestamp(DAY)]]))
    assert ("krx_close_flows", "not_current_session") in errors(derived(lake))


def test_a_late_close_day_has_no_valid_snapshot_and_falls_back(lake):
    day = date(2026, 11, 19)
    change = CalendarOverride(market="KR", day=day, close_at="2026-11-19T16:30:00+09:00",
                              source_url="https://global.krx.co.kr/fixture-official-notice")
    changes = {("KR", day): change}
    edition = pm(day, changes=changes)
    previous = schedule.previous("KR", day)
    publish(lake, krx_close_index=index_frame(days=(day, previous)), krx_close_prices=price_frame(days=(day, previous)),
            krx_close_flows=flow_frame(days=(day, previous)))
    result = summarize(read(lake, edition), edition, changes)
    assert {e for e in errors(result) if e[0].startswith("krx_close_")} == {
        (t, "collected_before_close") for t in ("krx_close_index", "krx_close_prices", "krx_close_flows")}
    assert not result["observations"] and not result["documents"]


@pytest.mark.parametrize("change", ["string_close", "timestamp_collected_at", "missing_basis", "nan_previous_close"])
def test_schema_mismatch_rejects_only_that_table(lake, change):
    index = index_frame()
    if change == "string_close":
        index["close"] = index["close"].astype(str)
    elif change == "timestamp_collected_at":
        index["collected_at"] = pd.to_datetime(index["collected_at"])
    elif change == "missing_basis":
        index = index.drop(columns=["basis"])
    else:
        index.loc[index["index"] == "KOSPI", "prev_close"] = float("nan")
    publish(lake, krx_close_index=index)
    result = derived(lake)
    # pandas writes NaN as a Parquet null, so a missing previous close is a schema mismatch too.
    assert ("krx_close_index", "krx_close_schema_mismatch") in errors(result)
    assert {o["instrument"] for o in result["observations"]} == {"kr_foreign", "kr_institution"}


def test_an_object_replaced_during_or_between_reads_is_rejected(lake):
    publish(lake)
    lake.fs.changing.add("krx_close_prices")  # The pinned catalog's own refresh sees a new identity.
    result = derived(lake)
    assert ("krx_close_prices", "source_changed_during_read") in errors(result)
    lake.fs.changing.clear()
    original = lake.inspect_dataset

    def replaced(dataset, **kwargs):
        meta = original(dataset, **kwargs)
        if dataset == "krx_close_flows" and kwargs.get("sample_rows"):
            write(lake, dataset, flow_frame().iloc[::-1])  # Replaced after the sample, before the identity re-check.
        return meta

    lake.inspect_dataset = replaced
    result = derived(lake)
    assert ("krx_close_flows", "source_changed_during_read") in errors(result)
    assert {o["instrument"] for o in result["observations"]} == {"kospi", "kosdaq"}


def test_inconsistent_index_change_and_reads_after_the_cutoff_are_rejected(lake):
    index = index_frame()
    index.loc[index["index"] == "KOSDAQ", "chg_pct"] = 1.62
    publish(lake, krx_close_index=index)
    assert ("krx_close_index", "change_inconsistent_with_previous_close") in errors(derived(lake))
    edition = pm()
    late = read_snapshot(edition.model_dump(mode="json"), lake, clock=lambda: edition.cutoff+timedelta(seconds=1),
                         krx_close=True)
    assert late == {"ok": False, "error": "data_cutoff_passed", "observed_at": late["observed_at"]}


def test_a_same_session_lake_close_never_doubles_the_snapshot_value(lake):
    # Only a cutoff after 18:00 lets the krx_index lake rule offer today's close as well.
    publish(lake)
    edition = pm(anchor=(20, 300, 360))
    raw = read(lake, edition)
    raw["datasets"]["krx_index"] = snapshot(edition)["datasets"]["krx_index"]
    result = summarize(raw, edition)
    snapshot_doc = next(d["id"] for d in result["documents"] if d["registration"] == "qdata.api:krx_close")
    kospi = [o for o in result["observations"] if o["instrument"] == "kospi"]
    assert len(kospi) == 1 and kospi[0]["evidence"][0]["source_id"] == snapshot_doc
    assert kospi[0]["value"] == "2600.12" and any("20거래일" in c["text"] for c in result["contexts"])


# ---------------------------------------------------------------- top stocks and the stock master


def test_master_uses_the_previous_day_vintage_and_skips_unnamed_tickers(lake):
    names = {k: v for k, v in NAMES.items() if k != "000660"}
    publish(lake, krx_stock_master=master_frame({DAY-timedelta(days=1): names, DAY: NAMES}))
    result = derived(lake)
    assert ("krx_stock_master", "stock_name_missing") in errors(result)
    assert {"dataset": "krx_stock_master", "error": "stock_name_missing", "ticker": "000660"} in result["diagnostics"]
    top = next(d for d in result["documents"] if d["registration"] == "qdata.api:krx_close")["receipt"]["display"]["top"]
    # The next ticker by trading value fills the place; today's vintage, which names 000660, is never used.
    assert [t["ticker"] for t in top] == ["005930", "0126Z0", "247540", "373220", "035420"]
    assert top[0]["text"] == "삼성전자(005930) 정규장 종가 기준 268,500원 · -1.29%"
    assert top[-1]["text"] == "NAVER(035420) 정규장 종가 기준 215,000원 · +1.18%"


@pytest.mark.parametrize("problem", ["missing", "over_budget", "no_earlier_vintage", "unsafe_name"])
def test_unreadable_master_omits_only_the_top_stock_lines(lake, monkeypatch, problem):
    master = master_frame()
    if problem == "missing":
        master = None
    elif problem == "over_budget":
        monkeypatch.setattr(data_reader, "MASTER_MAX_ROWS", len(master)-1)
    elif problem == "no_earlier_vintage":
        master = master_frame({DAY: NAMES})
    publish(lake, krx_stock_master=master)
    if problem == "unsafe_name":
        write(lake, "krx_stock_master", master_frame({DAY-timedelta(days=1): {**NAMES, "005930": "<!here>"}}))
    result = derived(lake)
    expected = {"missing": "dataset_or_series_unavailable", "over_budget": "dataset_exceeds_briefing_budget",
                "no_earlier_vintage": "dataset_or_series_unavailable"}.get(problem)
    if expected:
        assert ("krx_stock_master", expected) in errors(result)
        assert ("krx_close", "top_stocks_omitted_master_unavailable") in errors(result)
    else:
        assert {"dataset": "krx_stock_master", "error": "stock_name_missing", "ticker": "005930"} in result["diagnostics"]
    assert {o["instrument"] for o in result["observations"]} == {"kospi", "kosdaq", "kr_foreign", "kr_institution"}
    data = frozen(result)
    parts, _ = render(reconcile(empty(), data)[0], data)
    assert "*코스피 수급*" in parts[0] and ("*거래대금 상위 종목*" in parts[0]) is (problem == "unsafe_name")
    assert "<!here>" not in parts[0]


# ---------------------------------------------------------------- closing articles and fallback


def article(edition, quote, identity="article-1", url="https://www.yna.co.kr/view/fixture-kospi-close",
            publisher="Synthetic wire", published=None):
    from quant_company.briefing.contracts import SourceDocument

    return SourceDocument(id=identity, url=url, title="증시 마감", publisher=publisher, kind="media", content=quote,
        published_at=published or kst(edition.day, 15, 45), retrieved_at=kst(edition.day, 15, 50),
        sha256="b"*64, registration="fixture", receipt={"synthetic": True}).model_dump(mode="json")


def observed(edition, instrument, value, quote, source="article-1", **extra):
    unit = "억원" if instrument.startswith("kr_") else "pt"
    return {"id": f"{source}-{instrument}", "instrument": instrument, "value": value, "unit": unit,
            "session_date": str(edition.day), "as_of": schedule.close("KR", edition.day).isoformat(),
            "basis": "close", "venue": "KRX", "evidence": [{"source_id": source, "quote": quote}], **extra}


def proposed(*items):
    return BriefProposal.model_validate({**empty().model_dump(mode="json"), "observations": list(items)})


def written(edition, instrument, value, quote, **extra):
    return proposed(observed(edition, instrument, value, quote, **extra))


CLOSING = "코스피는 전 거래일보다 1.29% 내린 2,600.12에 장을 마쳤다. 코스닥은 0.62% 오른 850.55로 마감했다."


def closing(edition, text=CLOSING, **kwargs):
    return article(edition, text, **kwargs)


def reports(edition, source="article-1", text=CLOSING):
    kospi, kosdaq = text.split(". ", 1)
    return proposed(observed(edition, "kospi", "2600.12", kospi, source, reported_change="-1.29", change_unit="%"),
                    observed(edition, "kosdaq", "850.55", kosdaq, source, reported_change="0.62", change_unit="%"))


def test_field_case_a_lagging_index_snapshot_gives_way_to_the_closing_reports(lake):
    # 2026-10-08: KRX returned KOSPI 6,666.92 / KOSDAQ 893.44 at 15:45 (the 15:24:50 tick); the official closes
    # reported by the closing articles were 6,625.93 / 892.27.
    day = date(2026, 10, 8)
    edition = pm(day)
    previous = schedule.previous("KR", day)
    lagging = {"KOSPI": ("6666.92", "6600.00", 456780000, 12345600000000),
               "KOSDAQ": ("893.44", "890.00", 987650000, 8765400000000)}
    publish(lake, krx_close_index=index_frame(days=(day, previous), values=lagging),
            krx_close_prices=price_frame(days=(day, previous)), krx_close_flows=flow_frame(days=(day, previous)),
            krx_stock_master=master_frame({previous: NAMES}))
    result = derived(lake, edition)
    assert {o["instrument"]: o["value"] for o in result["observations"] if o["unit"] == "pt"} == {
        "kospi": "6666.92", "kosdaq": "893.44"}
    wire = "코스피는 전 거래일보다 0.39% 오른 6,625.93에 장을 마쳤다. 코스닥은 0.26% 오른 892.27로 마감했다."
    other = "KOSPI rose 0.39% to close at 6,625.93. KOSDAQ added 0.26% to 892.27 at the close."
    outlets = [article(edition, wire), article(edition, other, "article-2", "https://www.reuters.com/fixture-kospi",
                                               "Other wire", kst(day, 15, 52))]
    data = frozen(result, edition, outlets)
    written_values = []
    for source, (kospi, kosdaq) in (("article-1", wire.split(". ", 1)), ("article-2", other.split(". ", 1))):
        written_values += [observed(edition, "kospi", "6625.93", kospi, source, reported_change="0.39", change_unit="%"),
                           observed(edition, "kosdaq", "892.27", kosdaq, source, reported_change="0.26", change_unit="%")]
    reconciled, conflicts = reconcile(proposed(*written_values), data)
    # The snapshot levels are set aside and recorded; the two agreeing closing reports supply the closes.
    assert [(c["instrument"], c["resolution"], c["diagnostic"]) for c in conflicts] == [
        ("kosdaq", "article_values_used", "krx_close_snapshot_article_disagreement"),
        ("kospi", "article_values_used", "krx_close_snapshot_article_disagreement")]
    assert [v["value"] for v in conflicts[1]["values"]] == ["6666.92 (+1.01%)", "6625.93 (+0.39%)", "6625.93 (+0.39%)"]
    closes = {o.instrument: o for o in reconciled.observations}
    assert (closes["kospi"].value, closes["kosdaq"].value) == (Decimal("6625.93"), Decimal("892.27"))
    assert {e.source_id for e in closes["kospi"].evidence} == {"article-1", "article-2"}
    assert assurance(reconciled, data)["kospi"] == assurance(reconciled, data)["kosdaq"] == "corroborated"
    assert validate(reconciled, data) == {}
    parts, quality = render(reconciled, {**data, "quote_conflicts": conflicts})
    main, thread = parts[0], "\n".join(parts[1:])
    assert "• *코스피* 6,625.93 pt · 보도상 +0.39% <https://www.yna.co.kr/view/fixture-kospi-close|[3]> <" in main
    assert "• *코스닥* 892.27 pt · 보도상 +0.26% [3] [4]" in main
    assert "6,666.92" not in main+thread and "893.44" not in main+thread and "확인 부족" not in main
    # The lagging index volume/value stay out with the level; collected flows and stocks remain.
    assert "*코스피 거래*" not in main and "*코스피 수급* 개인 +1,234억원 · 외국인 -2,346억원" in main
    assert "*거래대금 상위 종목*\n• 삼성전자(005930) 정규장 종가 기준 268,500원 · -1.29% [2]" in main
    # A recorded diagnostic, not a reader notice or a qualification conflict.
    assert "출처 간 수치 차이" not in main and "수치 대조" not in thread
    assert quality["quote_conflicts"] == conflicts


def test_an_unconfirmed_index_snapshot_is_never_published_on_its_own(lake):
    publish(lake)
    edition = pm()
    # No closing report at all: the current fallback (missing core closes), flows and stocks still shown.
    data = frozen(derived(lake), edition)
    reconciled, conflicts = reconcile(empty(), data)
    parts, _ = render(reconciled, data)
    assert not conflicts and "확인 부족: 코스닥, 코스피" in parts[0]
    assert "2,600.12" not in parts[0] and "*코스피 거래*" not in parts[0] and "*코스피 수급*" in parts[0]
    # A matching report published before the close is not a closing report.
    early = closing(edition, published=kst(edition.day, 15, 20))
    data = frozen(derived(lake), edition, [early])
    reconciled, conflicts = reconcile(reports(edition), data)
    assert not conflicts and assurance(reconciled, data)["kospi"] == "single_source"
    assert next(o for o in reconciled.observations if o.instrument == "kospi").id == "article-1-kospi"


def test_a_disagreeing_closing_article_replaces_the_snapshot_value_with_a_diagnostic(lake):
    publish(lake)
    edition = pm()
    quote = "코스피는 전 거래일보다 1.10% 내린 2,605.12에 장을 마쳤다."
    data = frozen(derived(lake), edition, [article(edition, quote)])
    reconciled, conflicts = reconcile(written(edition, "kospi", "2605.12", quote, reported_change="-1.10",
                                              change_unit="%"), data)
    assert [(c["instrument"], c["resolution"]) for c in conflicts] == [("kospi", "article_values_used")]
    assert conflicts[0]["diagnostic"] == "krx_close_snapshot_article_disagreement"
    assert [v["value"] for v in conflicts[0]["values"]] == ["2600.12 (-1.29%)", "2605.12 (-1.10%)"]
    kospi = next(o for o in reconciled.observations if o.instrument == "kospi")
    assert kospi.value == Decimal("2605.12") and assurance(reconciled, data)["kospi"] == "single_source"
    parts, _ = render(reconciled, {**data, "quote_conflicts": conflicts})
    assert "*코스피* 2,605.12 pt" in parts[0] and "2,600.12" not in parts[0]
    # Same level but a different reported change is also a disagreement.
    _, conflicts = reconcile(written(edition, "kospi", "2600.12", quote, reported_change="-1.10", change_unit="%"),
                             data)
    assert [c["diagnostic"] for c in conflicts] == ["krx_close_snapshot_article_disagreement"]
    # Closing reports disagreeing among themselves keep the existing withheld rule.
    other = "코스피는 2,610.00에 마감했다."
    data = frozen(derived(lake), edition, [article(edition, quote), article(edition, other, "article-2",
                                                                              "https://www.reuters.com/fixture")])
    reconciled, conflicts = reconcile(proposed(observed(edition, "kospi", "2605.12", quote),
                                               observed(edition, "kospi", "2610.00", other, "article-2")), data)
    assert [c["resolution"] for c in conflicts] == ["article_values_used", "withheld"]
    assert "kospi" not in {o.instrument for o in reconciled.observations}
    # A KOSPI foreign flow beyond 억원 rounding: the report's figure replaces the snapshot's.
    flow_quote = "외국인은 유가증권시장에서 2,400억원을 순매도했다."
    data = frozen(derived(lake), edition, [article(edition, flow_quote)])
    reconciled, conflicts = reconcile(written(edition, "kr_foreign", "-2400", flow_quote), data)
    assert [(c["instrument"], c["resolution"]) for c in conflicts] == [("kr_foreign", "article_values_used")]
    assert next(o for o in reconciled.observations if o.instrument == "kr_foreign").value == Decimal("-2400")
    parts, _ = render(reconciled, {**data, "quote_conflicts": conflicts})
    assert "• *코스피 수급* 개인 +1,234억원 · 기관 +1,000억원 · 장 마감 기준" in parts[0]
    assert "*KRX 외국인 순매수* -2,400 억원" in parts[0]


def test_an_agreeing_closing_article_keeps_the_collected_value(lake):
    publish(lake)
    edition = pm()
    quote = "코스피는 1.29% 내린 2,600.12, 외국인은 2,346억원 순매도."
    data = frozen(derived(lake), edition, [article(edition, quote)])
    for instrument, value, extra in (("kospi", "2600.12", {"reported_change": "-1.29", "change_unit": "%"}),
                                     ("kr_foreign", "-2346", {})):
        reconciled, conflicts = reconcile(written(edition, instrument, value, quote, **extra), data)
        assert not conflicts
        kept = next(o for o in reconciled.observations if o.instrument == instrument)
        assert kept.id == "data-"+instrument and assurance(reconciled, data)[instrument] == "collected"


def test_without_a_valid_snapshot_the_pm_edition_is_exactly_the_current_two_outlet_path(lake):
    edition = pm()
    quote_a = "코스피는 2,600.12에 마감했다."
    quote_b = "KOSPI closed at 2,600.12."
    outlets = [article(edition, quote_a), {**article(edition, quote_b), "id": "article-2",
                                           "url": "https://www.reuters.com/fixture", "publisher": "Other wire"}]
    first = written(edition, "kospi", "2600.12", quote_a).observations[0].model_dump(mode="json")
    proposal_two = BriefProposal.model_validate({**empty().model_dump(mode="json"), "observations": [
        first, {**first, "id": "b", "evidence": [{"source_id": "article-2", "quote": quote_b}]}]})
    base_snapshot = {"ok": True, "observed_at": edition.starts_at.isoformat(), "qdata_code_commit": "c"*40,
                     "errors": [], "datasets": {}}
    base = frozen(summarize(base_snapshot, edition), edition, outlets)
    publish(lake, krx_close_index=index_frame(days=(PREVIOUS,)), krx_close_prices=price_frame(days=(PREVIOUS,)),
            krx_close_flows=flow_frame(days=(PREVIOUS,)))
    rejected = derived(lake, edition)
    assert not rejected["documents"] and not rejected["observations"]
    data = frozen(rejected, edition, outlets)
    assert data["documents"] == base["documents"] and data["locked_observations"] == base["locked_observations"] == []
    assert reconcile(proposal_two, data) == reconcile(proposal_two, base)
    assert assurance(reconcile(proposal_two, data)[0], data) == {"kospi": "corroborated"}
    assert render(reconcile(proposal_two, data)[0], {**data, "data_diagnostics": []}) == render(
        reconcile(proposal_two, base)[0], {**base, "data_diagnostics": []})


# ---------------------------------------------------------------- store: policy, early send, data worker


def kr_switched(store):
    store.company.settings.briefing_kr_close_enabled = True


def stored(store, edition_id):
    with store.db.transaction() as conn:
        return conn.execute("SELECT * FROM brief_editions WHERE id=%s", (edition_id,)).fetchone()


def seeded(brief, edition):  # noqa: F811
    store, clock = brief
    clock["at"] = edition.cutoff-timedelta(minutes=1)
    store.register()
    claimed = store.claim_collection()
    assert str(claimed["id"]) == edition.id
    store.save_collection(claimed, bundle(edition))
    clock["at"] = edition.cutoff
    return edition


def test_real_postgres_policy_changes_only_while_switched_on(brief):  # noqa: F811
    store, _ = brief
    off = store.policy()
    kr_switched(store)
    on = store.policy()
    store.company.settings.briefing_kr_close_due_minutes = 100
    assert len({off, on, store.policy()}) == 3
    store.company.settings.briefing_kr_close_due_minutes = 115
    assert store.policy() == on
    store.company.settings.briefing_kr_close_enabled = False
    assert store.policy() == off


def test_oct12_registration_and_shared_deadline_use_the_new_pm_window(brief):  # noqa: F811
    store, clock = brief
    kr_switched(store)
    store.company.settings.briefing_source_notes_enabled = True
    edition = pm(date(2026, 10, 12))
    clock['at'] = edition.starts_at-timedelta(seconds=1)
    store.register()
    assert stored(store, edition.id) is None
    clock['at'] = edition.starts_at
    store.register()
    row = stored(store, edition.id)
    assert (hm(datetime.fromisoformat(row['definition']['starts_at'])), hm(row['cutoff']), hm(row['due_at'])) == (
        '15:50', '16:10', '17:25')
    data = bundle(edition)
    data['candidate_documents'] = data['documents']
    claimed = store.claim_collection()
    assert str(claimed['id']) == edition.id
    store.save_collection(claimed, data)
    clock['at'] = edition.cutoff
    ready = store.prepare()['request']
    assert ready['request_id'].endswith('-plan')
    assert hm(datetime.fromtimestamp(ready['brief_deadline_unix'], KST)) == '16:20'
    assert schedule.version(store.company.settings) == '6+8:20/40/115'


def test_real_postgres_ready_pm_is_sent_after_the_cutoff_before_due_and_am_still_waits(brief):  # noqa: F811
    store, clock = brief
    kr_switched(store)
    store.company.settings.briefing_max_revisions = 0
    edition = seeded(brief, pm())
    row = stored(store, edition.id)
    assert (hm(row["cutoff"]), hm(row["due_at"]), hm(row["expires_at"])) == ("16:10", "17:25", "18:25")
    assert hm(datetime.fromisoformat(row["definition"]["starts_at"])) == "15:50"
    store.commit(response(store.prepare()["request"]))
    store.commit(response(store.prepare()["request"]))
    assert stored(store, edition.id)["state"] == "ready"
    clock["at"] = edition.cutoff-timedelta(minutes=1)
    assert store.flush()["committed"] == 0
    clock["at"] = edition.cutoff+timedelta(minutes=20)
    assert store.flush()["committed"] == 1
    row = stored(store, edition.id)
    assert row["state"] == "committed" and row["committed_at"] < edition.due_at


def test_real_postgres_switch_on_ready_am_still_waits_for_its_due(brief):  # noqa: F811
    store, clock = brief
    kr_switched(store)
    store.company.settings.briefing_max_revisions = 0
    edition = seeded(brief, definition("am"))
    store.commit(response(store.prepare()["request"]))
    store.commit(response(store.prepare()["request"]))
    assert stored(store, edition.id)["state"] == "ready"
    clock["at"] = edition.due_at-timedelta(minutes=1)
    assert store.flush()["committed"] == 0
    clock["at"] = edition.due_at
    assert store.flush()["committed"] == 1


def test_real_postgres_priority_window_covers_a_longer_v8_lead(brief):  # noqa: F811
    store, clock = brief
    kr_switched(store)
    s = store.company.settings
    s.briefing_kr_close_start_minutes, s.briefing_kr_close_cutoff_minutes, s.briefing_kr_close_due_minutes = 10, 30, 150
    edition = pm(anchor=(10, 30, 150))
    clock["at"] = edition.starts_at
    store.register()
    assert edition.due_at-clock["at"] == timedelta(minutes=140)
    assert priority_pending(store.company)


def test_real_postgres_data_worker_asks_for_the_snapshot_only_for_switched_on_pm(brief):  # noqa: F811
    store, clock = brief
    seen = []

    def reader(root, edition, **kwargs):
        seen.append((edition.kind, kwargs))
        return {"ok": True, "datasets": {}, "errors": [], "observed_at": clock["at"].isoformat()}

    collector = BriefDataCollector(store.company, reader=reader)
    for on in (False, True):
        store.company.settings.briefing_kr_close_enabled = on
        for edition in (definition("am"), pm() if on else definition("pm")):
            clock["at"] = edition.starts_at
            with store.db.transaction() as conn:
                conn.execute("DELETE FROM brief_editions")
            assert asyncio.run(collector.tick())["state"] == "collected"
    assert seen == [("am", {}), ("pm", {}), ("am", {}), ("pm", {"krx_close": True})]


def test_real_postgres_a_set_aside_snapshot_is_not_a_qualification_conflict(brief):  # noqa: F811
    from psycopg.types.json import Jsonb

    from quant_company.briefing.qualification import qualify

    store, clock = brief
    kr_switched(store)
    store.company.settings.briefing_max_revisions = 0
    edition = seeded(brief, pm())
    store.commit(response(store.prepare()["request"]))
    store.commit(response(store.prepare()["request"]))
    clock["at"] = edition.due_at
    store.flush()
    diagnostic = {"instrument": "kospi", "session": str(DAY), "resolution": "article_values_used",
                  "diagnostic": "krx_close_snapshot_article_disagreement", "values": []}

    def reasons(conflicts):
        with store.db.transaction() as conn:
            conn.execute("""UPDATE brief_editions SET quality=quality||%s WHERE id=%s""",
                         (Jsonb({"reduced": False, "quote_conflicts": conflicts}), edition.id))
        report = qualify(store.company, at=edition.due_at+timedelta(minutes=15))
        return next(c for c in report["editions"] if c["id"] == edition.id)["reasons"]

    assert "reduced_or_conflicting_coverage" not in reasons([diagnostic])
    assert "reduced_or_conflicting_coverage" in reasons([diagnostic, {**diagnostic, "resolution": "withheld"}])


# ---------------------------------------------------------------- deployment plumbing


def test_monitor_expects_v8_pm_editions_only_when_the_worker_has_the_switch(monkeypatch):
    from importlib.util import module_from_spec, spec_from_file_location

    spec = spec_from_file_location("analyst_monitor_v8", Path(__file__).parents[1]/"deploy/analyst_brief_monitor.py")
    monitor = module_from_spec(spec)
    spec.loader.exec_module(monitor)
    edition = pm()
    policy = {"mode": "continuous", "channel": "CQUANT", "owner": "UHUMAN",
              "starts_at": (edition.due_at-timedelta(days=1)).isoformat()}
    for env, expected in (([], {"now", "channel", "owner", "overrides", "us_close"}),
                          (["BRIEFING_KR_CLOSE_ENABLED=false"], {"now", "channel", "owner", "overrides", "us_close"}),
                          (["BRIEFING_KR_CLOSE_ENABLED=true"], {"now", "channel", "owner", "overrides", "us_close",
                                                                "kr_close"})):
        outputs = []

        def docker(command, want=expected, out=outputs, extra=env, **kwargs):
            if command[1] == "inspect":
                return json.dumps([{"Config": {"Image": "installed-image", "Env": [
                    "BRIEFING_CHANNEL_ID=CQUANT", "BRIEFING_OWNER_USER=UHUMAN", *extra]}}]).encode()
            payload = json.loads(kwargs["input"])
            assert set(payload) == want
            # Run the monitor's own calendar program against this checkout instead of the installed image.
            code = command[command.index("-c")+1]
            done = subprocess.run([sys.executable, "-c", code], input=kwargs["input"], capture_output=True,
                                  timeout=60, check=True, env={"PYTHONPATH": str(Path(__file__).parents[1]/"src")})
            out.append(json.loads(done.stdout))
            return done.stdout

        monkeypatch.setattr(monitor.subprocess, "check_output", docker)
        monitor.definitions(policy, edition.due_at+timedelta(minutes=20))
        day = next(d for d in outputs[0] if d["id"] == edition.id)
        assert (hm(datetime.fromisoformat(day["due_at"])) == "17:25") is ("kr_close" in expected)


def test_compose_and_example_env_pass_the_switch_with_its_defaults():
    root = Path(__file__).parents[1]/"deploy"
    compose = (root/"compose.yaml").read_text()
    example = (root/".env.example").read_text()
    for name, default in (("ENABLED", "false"), ("START_MINUTES", "20"), ("CUTOFF_MINUTES", "40"),
                          ("DUE_MINUTES", "115"), ("BASIS", "regular")):
        key = "BRIEFING_KR_CLOSE_"+name
        assert f"  {key}: ${{{key}:-{default}}}\n" in compose
        assert f"\n{key}={default}\n" in example

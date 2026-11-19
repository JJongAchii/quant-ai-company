"""Frozen baselines for the daily Analyst trading signals (design 2026-11-18).

Rules were fixed before any number was read; nothing here is tuned. Run with the quant-data environment:

    QDATA_LAKE=~/Quant/data-lake  ~/Quant/quant-data/.venv/bin/python -I signal_baselines.py [yahoo_dir]

Rules
- Intraday label of KRX day D: close_D / open_D - 1 (same-day raw prices; no adjustment needed within a day).
- Overnight label of day D: open_{D+1} / close_D - 1 on the KOSPI index (price index, no dividend artefacts).
- B0 always long: position +1.
- B1 gap fade: position -sign(open_D / close_{D-1} - 1) of the KOSPI index. Uses the D open itself, so it is a
  reference, not an executable rule.
- B2 follow US: position sign(SPY adj_close return of the latest US session dated strictly before D). The US
  session dated D-1 (ET) closes before the KRX open of D; a session dated D has not happened yet.
- Days with a zero label or a zero position are excluded from the hit rate. No costs are deducted.
- KODEX 200 (069500) rows with a non-positive open or close are dropped.
Optional yahoo_dir: saved Yahoo chart JSON (interval=1d) for ^KS11 and 069500.KS, compared with the lake.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from qdata import api

PERIODS = (("all", None, None), ("to-2017", None, "2018-01-01"), ("2018-", "2018-01-01", None),
           ("2024-", "2024-01-01", None))


def hit_table(frame, label, position):
    data = frame[[label, position]].dropna()
    data = data[(data[label] != 0) & (data[position] != 0)]
    pnl = data[position] * data[label]
    n = len(data)
    return {"n": n, "hit_pct": round(float((pnl > 0).mean()) * 100, 1),
            "hit_2se_pct": round(2 * float(np.sqrt(0.25 / n)) * 100, 1),
            "mean_bp": round(float(pnl.mean()) * 1e4, 1),
            "t": round(float(pnl.mean() / (pnl.std(ddof=1) / np.sqrt(n))), 2)}


def period(frame, start, end):
    if start:
        frame = frame[frame["date"] >= start]
    if end:
        frame = frame[frame["date"] < end]
    return frame


def kospi():
    frame = api.load_krx_index("2010-01-01", None)
    frame = frame[frame["index"] == "KOSPI"].sort_values("date").reset_index(drop=True)
    frame["date"] = pd.to_datetime(frame["date"])
    frame["prev_close"] = frame["close"].shift(1)
    frame["intraday"] = frame["close"] / frame["open"] - 1
    frame["gap"] = frame["open"] / frame["prev_close"] - 1
    frame["close_to_close"] = frame["close"] / frame["prev_close"] - 1
    frame["overnight"] = frame["open"].shift(-1) / frame["close"] - 1
    wide = api.load_prices(["SPY"], "2009-12-01", None, fields=("adj_close",))
    spy = pd.DataFrame({"us_date": pd.to_datetime(wide.index),
                        "us_ret": wide[("adj_close", "SPY")].pct_change().to_numpy()})
    frame = pd.merge_asof(frame, spy, left_on="date", right_on="us_date", direction="backward",
                          allow_exact_matches=False)
    frame["long"] = 1.0
    frame["fade"] = -np.sign(frame["gap"])
    frame["follow_us"] = np.sign(frame["us_ret"])
    return frame.iloc[1:].reset_index(drop=True), spy["us_date"].max()


def kodex200(index):
    frame = api.load_krx_etf_prices("2012-01-01", None, ["069500"]).sort_values("date")
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame[(frame["open"] > 0) & (frame["close"] > 0)]
    frame["intraday"] = frame["close"] / frame["open"] - 1
    merged = frame[["date", "intraday"]].merge(index[["date", "long", "fade", "follow_us"]], on="date")
    if len(merged) != len(frame[(frame["date"] >= index["date"].min()) & (frame["date"] <= index["date"].max())]):
        raise SystemExit("KODEX 200 rows were dropped by the join")
    return merged


def yahoo_bars(path):
    result = json.loads(Path(path).read_text())["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    days = pd.to_datetime(result["timestamp"], unit="s", utc=True).tz_convert("Asia/Seoul").date
    return pd.DataFrame({"date": pd.to_datetime(days), "y_open": quote["open"], "y_close": quote["close"]})


def compare_yahoo(directory, index):
    etf = api.load_krx_etf_prices("2026-04-01", None, ["069500"])[["date", "open", "close"]]
    etf["date"] = pd.to_datetime(etf["date"])
    pairs = (("KOSPI ^KS11", index[["date", "open", "close"]], "_5EKS11.json"),
             ("KODEX200 069500.KS", etf, "069500.KS.json"))
    for name, lake, file in pairs:
        both = lake.merge(yahoo_bars(Path(directory) / file), on="date")
        open_bp = ((both["y_open"] / both["open"] - 1) * 1e4).abs()
        close_bp = ((both["y_close"] / both["close"] - 1) * 1e4).abs()
        broken = both.loc[open_bp > 1, "date"].dt.strftime("%Y-%m-%d").tolist()
        print(f"{name}: {len(both)} days {both['date'].min().date()}..{both['date'].max().date()} "
              f"open max {open_bp.max():.2f}bp close max {close_bp.max():.2f}bp open mismatches {broken}")


def main():
    index, spy_last = kospi()
    print(f"KOSPI {index['date'].min().date()}..{index['date'].max().date()} rows {len(index)}; "
          f"SPY last {spy_last.date()}")
    rows = []
    for name, start, end in PERIODS:
        part = period(index, start, end)
        for rule, position, label in (("B0 always long", "long", "intraday"),
                                      ("B1 gap fade", "fade", "intraday"),
                                      ("B2 follow US", "follow_us", "intraday"),
                                      ("B2 follow US (gap)", "follow_us", "gap"),
                                      ("B2 follow US (close->close)", "follow_us", "close_to_close"),
                                      ("B0 always long (overnight)", "long", "overnight")):
            rows.append({"instrument": "KOSPI", "period": name, "rule": rule, **hit_table(part, label, position)})
    etf = kodex200(index)
    print(f"KODEX 200 {etf['date'].min().date()}..{etf['date'].max().date()} rows {len(etf)}")
    for name, start, end in PERIODS:
        part = period(etf, start, end)
        for rule, position in (("B0 always long", "long"), ("B1 gap fade", "fade"), ("B2 follow US", "follow_us")):
            rows.append({"instrument": "KODEX200", "period": name, "rule": rule,
                         **hit_table(part, "intraday", position)})
    pd.set_option("display.width", 200)
    print(pd.DataFrame(rows).to_string(index=False))
    if len(sys.argv) > 1:
        compare_yahoo(sys.argv[1], index)


if __name__ == "__main__":
    main()

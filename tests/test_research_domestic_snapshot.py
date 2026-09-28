"""Synthetic guards for prior-date cohort selection and visible missing sessions."""

from datetime import date, timedelta

import pytest

from quant_company.research.domestic_snapshot import build_rows, select_cohort


def prices(tickers=("000010", "000020", "000030", "000040"), *, sessions=22):
    return [{"date": date(2022, 11, 1) + timedelta(days=d), "ticker": ticker,
             "open": 10.0, "close": 10.0, "adj_close": 10.0,
             "volume": 100, "value": 100 + index * 10}
            for d in range(sessions) for index, ticker in enumerate(tickers)]


def test_cohort_uses_only_predevelopment_values_and_excludes_leveraged_etf():
    source = prices()
    selection = "2022-11-20"
    metadata = [{"date": selection, "ticker": ticker, "is_leveraged": ticker == "000040",
                 "is_inverse": False} for ticker in ("000010", "000020", "000030", "000040")]
    before, _ = select_cohort(source, selection_date=selection, cohort_size=3,
                              market="kr_etf", metadata=metadata)
    changed = [{**row, "value": 1_000_000} if row["date"] > date.fromisoformat(selection)
               and row["ticker"] == "000010" else row for row in source]
    after, _ = select_cohort(changed, selection_date=selection, cohort_size=3,
                             market="kr_etf", metadata=metadata)
    assert before == after == ["000030", "000020", "000010"]
    with pytest.raises(ValueError, match="observed_sessions"):
        select_cohort(source, selection_date="2022-10-31", cohort_size=3,
                      market="kr_etf", metadata=metadata)


def test_missing_and_halted_cohort_rows_are_reported_not_silently_filled():
    source = prices(sessions=22)
    source = [row for row in source if not (row["date"] == date(2022, 11, 20) and row["ticker"] == "000030")]
    for row in source:
        if row["date"] == date(2022, 11, 19) and row["ticker"] == "000020":
            row["open"] = 0
    warmup, development, quality = build_rows(source, market="kr_stock",
        cohort=["000010", "000020", "000030", "000040"], warmup_start="2022-11-01",
        development_start="2022-11-20", development_end="2022-11-22")
    assert len(quality["nontrading_rows"]) == len(quality["missing_rows"]) == 1
    assert not quality["bad_rows"]
    assert len(warmup) + len(development) == len(source)
    assert next(row for row in warmup if row["ticker"] == "000020" and row["date"] == "2022-11-19")["tradable"] is False
    assert {row["market"] for row in warmup + development} == {"kr_stock"}

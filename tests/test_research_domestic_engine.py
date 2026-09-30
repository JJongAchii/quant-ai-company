"""Small synthetic causal/accounting fixtures. No lake data or financial experiment."""

import copy
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from quant_company.research.evaluation import EvaluationSpec, evaluate_observations
from quant_company.research.mission_contracts import DateWindow
from quant_company.research.reference.domestic_engine import history_at, simulate, snapshot


def rows(count=7):
    return [{"date": (date(2020, 1, 1)+timedelta(days=i)).isoformat(), "ticker": ticker,
        "market": "kr_stock", "open": 100 + i * multiplier, "close": 100 + i * multiplier,
        "adj_close": 100 + i * multiplier, "value": 1e9,
        "available_at": (date(2020, 1, 1)+timedelta(days=i)).isoformat()+"T18:00:00+09:00", "tradable": True}
        for i in range(count) for ticker, multiplier in (("A", 1), ("B", 2), ("C", 3))]


def spec(end="2020-01-07"):
    return {"market": "kr_stock", "kind": "strategy", "development": {"start": "2020-01-02", "end": end},
            "base_cost_bps": 10, "stress_cost_bps": 30}


def test_future_rows_and_late_publications_never_reach_candidate():
    data = rows()
    data[0]["available_at"] = "2020-01-07T18:00:00+09:00"
    history = history_at(snapshot(data), "2020-01-03")
    assert all(row["date"] < "2020-01-03" for row in history)
    assert not any(row["ticker"] == "A" and row["date"] == "2020-01-01" for row in history)
    mutated = rows()
    mutated[-1]["adj_close"] *= 50
    assert history_at(snapshot(mutated), "2020-01-03") == history_at(snapshot(rows()), "2020-01-03")


def test_fill_is_next_open_costs_and_drift_are_recorded():
    calls = []
    def weights(history, config):
        calls.append(max(row["date"] for row in history))
        return {"A": 0.5}
    series, _, risks = simulate(snapshot(rows()), SimpleNamespace(weights=weights), {}, spec())
    assert calls == ["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-04", "2020-01-05"]
    assert series["stress"][0] == ("2020-01-02", 0)
    expected = (1 - .003 * .5) * (1 + .5 * (102 / 101 - 1)) - 1
    assert series["stress"][1][1] == pytest.approx(expected)
    assert series["stress"][1][1] < series["base"][1][1]
    assert risks["gross_exposure"] == .5


def test_prefix_and_per_ticker_adjustment_rescaling_preserve_earlier_results():
    candidate = SimpleNamespace(weights=lambda history, config: {"A": .4, "B": .4})
    full = snapshot(rows())
    baseline, _, _ = simulate(full, candidate, {}, spec())
    for cut in (3, 4, 5, 6):
        day = (date(2020, 1, 1)+timedelta(days=cut)).isoformat()
        prefix = [row for row in full if row["date"] <= day]
        shortened, _, _ = simulate(prefix, candidate, {}, spec(day))
        assert shortened["stress"] == baseline["stress"][:cut]
    changed = copy.deepcopy(full)
    for row in changed:
        row["adj_close"] *= 7 if row["ticker"] == "A" else .13
    rescaled, _, _ = simulate(changed, candidate, {}, spec())
    assert [r for _, r in rescaled["stress"]] == pytest.approx([r for _, r in baseline["stress"]])


def test_later_adjustment_rebases_do_not_change_historical_level_rank():
    original = snapshot(rows())
    rebased = copy.deepcopy(original)
    for row in rebased:
        row["adj_close"] *= {"A": 1000, "B": .01, "C": 5}[row["ticker"]]
    for cut in ("2020-01-03", "2020-01-05", "2020-01-07"):
        first = history_at(original, cut)
        second = history_at(rebased, cut)
        assert [(r["ticker"], r["date"]) for r in first] == [(r["ticker"], r["date"]) for r in second]
        assert [r["adj_close"] for r in first] == pytest.approx([r["adj_close"] for r in second])
        latest = max(r["date"] for r in first)
        assert max((r for r in first if r["date"] == latest), key=lambda r: r["adj_close"])["ticker"] == \
            max((r for r in second if r["date"] == latest), key=lambda r: r["adj_close"])["ticker"]


@pytest.mark.parametrize("change", ["missing", "halted", "universe", "nonfinite", "leverage"])
def test_unverified_execution_conditions_never_silently_drop_holdings(change):
    data, weights = rows(), {"A": .8}
    if change == "missing":
        data = [r for r in data if not (r["ticker"] == "A" and r["date"] == "2020-01-04")]
    elif change == "halted":
        data[6]["tradable"] = False
    elif change == "universe":
        weights = {"DELISTED_OR_FUTURE": .8}
    elif change == "nonfinite":
        weights = {"A": float("nan")}
    else:
        weights = {"A": 1.2}
    with pytest.raises(ValueError):
        simulate(snapshot(data), SimpleNamespace(weights=lambda history, config: weights), {}, spec())


def test_zero_open_halt_is_represented_but_cannot_price_a_held_position():
    data = rows()
    halted = next(row for row in data if row["ticker"] == "A" and row["date"] == "2020-01-04")
    halted.update(open=0, value=0, tradable=False)
    parsed = snapshot(data)
    assert next(row for row in parsed if row["ticker"] == "A" and row["date"] == "2020-01-04")["tradable"] is False
    with pytest.raises(ValueError, match="unexecutable"):
        simulate(parsed, SimpleNamespace(weights=lambda history, config: {"A": .5}), {}, spec())


def scientific_csv(value=.02):
    return ("date,value\n" + "".join(f"{year}-{month:02}-15,{value}\n"
        for year in (2020, 2021, 2022) for month in range(1, 13))).encode()


def test_scientific_results_use_frozen_monthly_blocks_and_replication_tolerance():
    window = DateWindow(start=date(2020, 1, 1), end=date(2022, 12, 31))
    claim = EvaluationSpec(kind="claim", metric="mean-effect", minimum_effect=.03)
    result = evaluate_observations(scientific_csv(), claim, window)
    assert result["block_count"] == 36 and result["conclusion"] == "not_supported"
    replication = EvaluationSpec(kind="replication", metric="absolute-replication-error", target=.02, tolerance=.001)
    assert evaluate_observations(scientific_csv(), replication, window)["conclusion"] == "supported"
    assert evaluate_observations(scientific_csv(.1), replication, window)["conclusion"] == "not_supported"
    with pytest.raises(ValueError, match="calendar blocks"):
        evaluate_observations(b"date,value\n2020-01-01,.1\n2020-01-02,.2\n", claim, window)


def test_duplicate_snapshot_rows_and_future_known_prices_are_not_valid_pit_keys():
    data = rows()
    with pytest.raises(ValueError, match="duplicate"):
        snapshot([*data, data[0]])
    data[0]["available_at"] = "2020-01-01T09:00:00+09:00"
    with pytest.raises(ValueError, match="pit"):
        snapshot(data)

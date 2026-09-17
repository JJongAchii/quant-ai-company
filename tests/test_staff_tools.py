import math

import pytest

from quant_company.staff.tools import data_quality, finance_compute, run_tool


def test_bond_par_zero_coupon_duration_and_finite_difference():
    args = dict(operation="bond", face=100, coupon_rate=0.04, yield_rate=0.04, years=5, frequency=2)
    value = finance_compute(args)["result"]
    assert value["price"] == pytest.approx(100)
    up = finance_compute({**args, "yield_rate": 0.04001})["result"]["price"]
    down = finance_compute({**args, "yield_rate": 0.03999})["result"]["price"]
    assert -(up-down)/0.00002/100 == pytest.approx(value["modified_duration_years"], rel=1e-8)
    assert (up+down-200)/1e-10/100 == pytest.approx(value["convexity_years_squared"], rel=1e-5)
    z = finance_compute({**args, "coupon_rate": 0, "frequency": 1, "shock_bps": 100})
    assert z["result"]["price"] == pytest.approx(100/1.04**5)
    assert z["result"]["macaulay_years"] == pytest.approx(5)
    assert z["result"]["shocked_price"] == pytest.approx(100/1.05**5)
    assert z["input_digest"]


def test_options_independent_reference_parity_and_greek_units():
    args = dict(operation="option", spot=100, strike=100, years=1, volatility=0.2, rate=0.05, dividend_yield=0)
    call = finance_compute({**args, "kind": "call"})["result"]
    put = finance_compute({**args, "kind": "put"})["result"]
    assert call["price"] == pytest.approx(10.4505835721856)
    assert put["price"] == pytest.approx(5.57352602225697)
    assert call["price"] - put["price"] == pytest.approx(100-100*math.exp(-0.05))
    assert call["vega_per_1pct_vol"] == pytest.approx(0.3752403469169)
    for q in [0.02, 0.06]:
        c = finance_compute({**args, "kind": "call", "dividend_yield": q})["result"]
        p = finance_compute({**args, "kind": "put", "dividend_yield": q})["result"]
        assert c["price"]-p["price"] == pytest.approx(100*math.exp(-q)-100*math.exp(-0.05))


def test_fx_cross_term_and_signed_linear_risk():
    assert finance_compute(dict(operation="fx_return", local_return=0.1, fx_return=-0.1))["result"]["base_currency_return"] == pytest.approx(-0.01)
    result = finance_compute(dict(operation="scenario", weights=[1, -1], shocks=[-0.2, 0.1], capital=100))["result"]
    assert result == {"gross_exposure": 2, "net_exposure": 0, "pnl": pytest.approx(-30), "contributions": [-20, -10]}


@pytest.mark.parametrize("args", [
    dict(operation="fx_return", local_return=True, fx_return=0),
    dict(operation="fx_return", local_return=float("nan"), fx_return=0),
    dict(operation="fx_return", local_return=0, fx_return=float("inf")),
    dict(operation="bond", face=100, coupon_rate=0.03, yield_rate=0.03, years=0.3, frequency=2),
    dict(operation="option", spot=100, strike=100, years=0, volatility=0.2, rate=0.03, dividend_yield=0, kind="call"),
    dict(operation="scenario", weights=[1], shocks=[], capital=100),
    dict(operation="scenario", weights=[1]*101, shocks=[0]*101, capital=100),
    dict(operation="fx_return", local_return=0, fx_return=0, execute="anything"),
])
def test_invalid_numbers_conventions_and_unbounded_inputs_return_error(args):
    assert run_tool("finance_compute", args)["ok"] is False


def test_data_quality_detects_timestamp_duplicates_nulls_and_missing_scope():
    args = {"rows": [
        {"id": "a", "available": "2030-01-01T09:00:00+09:00", "v": 0},
        {"id": "a", "available": "2030-01-01T00:00:00Z", "v": 2},
        {"id": "b", "available": "2030-01-01T00:00:01Z", "v": None},
        {"id": "c", "available": "2030-01-01", "v": 3}],
        "key_fields": ["id"], "required_fields": ["v"], "available_field": "available",
        "decision_at": "2030-01-01T00:00:00Z", "expected_ids": ["a", "b", "c", "d"], "id_field": "id"}
    value = data_quality(args)["result"]
    assert value["duplicate_rows"] == [1]
    assert value["missing_values"] == [{"row": 2, "fields": ["v"]}]
    assert value["future_rows"] == [2]
    assert value["invalid_time_rows"] == [3]
    assert value["missing_expected_ids"] == ["d"]
    assert not value["passed"]
    assert not data_quality({"rows": [], "key_fields": ["id"], "required_fields": []})["result"]["passed"]


def test_data_quality_requires_both_time_arguments_and_bounded_rows():
    base = {"rows": [{"id": "x"}], "key_fields": ["id"], "required_fields": []}
    assert not run_tool("data_quality", {**base, "decision_at": "2030-01-01T00:00:00Z"})["ok"]
    assert not run_tool("data_quality", {**base, "rows": [{}]*501})["ok"]
    assert not run_tool("data_quality", {**base, "rows": [{"id": float("nan")}]})["ok"]
    assert data_quality(base)["result"]["passed"]

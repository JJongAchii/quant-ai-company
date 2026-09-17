"""Small, bounded numerical tools with explicit conventions; no strategy execution."""

import hashlib
import json
import math
from datetime import datetime


def number(value, name, low=-1e12, high=1e12):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be a finite number in [{low}, {high}]")
    return value


def fields(args, required, optional=()):
    if not isinstance(args, dict) or not set(required) <= args.keys() <= set(required) | set(optional):
        raise ValueError(f"Required fields: {sorted(required)}; optional: {sorted(optional)}")


def receipt(operation, args, result, assumptions):
    encoded = json.dumps(args, sort_keys=True, allow_nan=False).encode()
    json.dumps(result, allow_nan=False)
    return {"ok": True, "operation": operation, "tool_version": "1", "inputs": args,
            "input_digest": hashlib.sha256(encoded).hexdigest(), "result": result,
            "assumptions": assumptions, "scope": "Provided inputs only; no market data or strategy backtest."}


def finance_compute(args):
    operation = args.get("operation")
    if operation == "bond":
        fields(args, {"operation", "face", "coupon_rate", "yield_rate", "years", "frequency"}, {"shock_bps"})
        face = number(args["face"], "face", 1e-8, 1e12)
        coupon = number(args["coupon_rate"], "coupon_rate", 0, 1)
        rate = number(args["yield_rate"], "yield_rate", -0.5, 2)
        years = number(args["years"], "years", 1/12, 100)
        freq = args["frequency"]
        if type(freq) is not int or freq not in {1, 2, 4, 12} or abs(years * freq - round(years * freq)) > 1e-9:
            raise ValueError("frequency is 1/2/4/12 and years*frequency must be an integer")
        n = round(years * freq)
        cash = [face * coupon / freq] * n
        cash[-1] += face
        discounted = [cf / (1 + rate / freq) ** i for i, cf in enumerate(cash, 1)]
        price = sum(discounted)
        macaulay = sum(i / freq * cf for i, cf in enumerate(discounted, 1)) / price
        modified = macaulay / (1 + rate / freq)
        convexity = sum(i * (i + 1) * cf for i, cf in enumerate(discounted, 1)) / (
            price * freq ** 2 * (1 + rate / freq) ** 2)
        result = {"price": price, "macaulay_years": macaulay, "modified_duration_years": modified,
                  "convexity_years_squared": convexity, "dv01_currency": price * modified * 0.0001}
        if "shock_bps" in args:
            shock = number(args["shock_bps"], "shock_bps", -10000, 10000) / 10000
            if 1 + (rate + shock) / freq <= 0:
                raise ValueError("shocked discount factor must be positive")
            result["shocked_price"] = sum(cf / (1 + (rate + shock) / freq) ** i for i, cf in enumerate(cash, 1))
            result["exact_price_change"] = result["shocked_price"] - price
            result["duration_convexity_estimate"] = price * (-modified * shock + 0.5 * convexity * shock**2)
        assumptions = "Coupon-date valuation; nominal annual yield compounded at frequency; positive fixed cashflows. "
        assumptions += "No accrued interest, default, embedded options or nonparallel yield-curve movement. Rates are fractions."
    elif operation == "option":
        fields(args, {"operation", "spot", "strike", "years", "volatility", "rate", "dividend_yield", "kind"})
        s = number(args["spot"], "spot", 1e-6, 1e10)
        k = number(args["strike"], "strike", 1e-6, 1e10)
        t = number(args["years"], "years", 1e-6, 50)
        v = number(args["volatility"], "volatility", 1e-6, 5)
        r = number(args["rate"], "rate", -0.5, 1)
        q = number(args["dividend_yield"], "dividend_yield", -0.5, 1)
        if args["kind"] not in {"call", "put"}:
            raise ValueError("kind must be call or put")
        sign = 1 if args["kind"] == "call" else -1
        d1 = (math.log(s/k) + (r-q+v*v/2)*t) / (v*math.sqrt(t))
        d2 = d1 - v*math.sqrt(t)
        def cdf(x):
            return 0.5 * math.erfc(-x / math.sqrt(2))
        density = math.exp(-d1*d1/2) / math.sqrt(2*math.pi)
        dq, dr = math.exp(-q*t), math.exp(-r*t)
        result = {"price": sign * (s*dq*cdf(sign*d1) - k*dr*cdf(sign*d2)),
                  "delta": sign*dq*cdf(sign*d1), "gamma_per_spot_unit": dq*density/(s*v*math.sqrt(t)),
                  "vega_per_1pct_vol": s*dq*density*math.sqrt(t)/100}
        assumptions = "European Black-Scholes-Merton; continuous rates/dividend yield; constant volatility; "
        assumptions += "price per underlying unit; no American exercise, discrete dividends, jumps or costs. "
        assumptions += "vega is price change per 0.01 absolute volatility, not per 1.0."
    elif operation == "fx_return":
        fields(args, {"operation", "local_return", "fx_return"})
        local = number(args["local_return"], "local_return", -1, 100)
        fx = number(args["fx_return"], "fx_return", -1, 100)
        result = {"base_currency_return": (1 + local) * (1 + fx) - 1}
        assumptions = "Fractions, unhedged; FX quote is base currency per foreign unit; no costs or taxes."
    elif operation == "scenario":
        fields(args, {"operation", "weights", "shocks", "capital"})
        w, s = args["weights"], args["shocks"]
        if not isinstance(w, list) or not isinstance(s, list) or not 1 <= len(w) == len(s) <= 100:
            raise ValueError("weights and shocks need equal lengths 1..100")
        w = [number(x, "weight", -10, 10) for x in w]
        s = [number(x, "shock", -1, 100) for x in s]
        capital = number(args["capital"], "capital", 0, 1e15)
        result = {"gross_exposure": sum(abs(x) for x in w), "net_exposure": sum(w),
                  "pnl": capital*sum(x*y for x, y in zip(w, s, strict=True)),
                  "contributions": [capital*x*y for x, y in zip(w, s, strict=True)]}
        assumptions = "Signed linear capital weights and return shocks; zero-return residual cash. "
        assumptions += "Not VaR, derivative repricing, liquidation or a probability forecast."
    else:
        raise ValueError("operation must be bond, option, fx_return or scenario")
    return receipt(operation, args, result, assumptions)


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("timestamp must be ISO-8601 with timezone")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timestamp requires timezone")
    return parsed


def data_quality(args):
    fields(args, {"rows", "key_fields", "required_fields"}, {"available_field", "decision_at", "expected_ids", "id_field"})
    rows, keys, required = args["rows"], args["key_fields"], args["required_fields"]
    if not isinstance(rows, list) or len(rows) > 500 or not all(isinstance(r, dict) for r in rows):
        raise ValueError("rows must be at most 500 objects")
    if len(json.dumps(args, allow_nan=False)) > 100000:
        raise ValueError("data_quality input too large")
    for names in (keys, required):
        if not isinstance(names, list) or len(names) > 20 or not all(isinstance(x, str) and x for x in names):
            raise ValueError("field names must be a list of at most 20 strings")
    if not keys:
        raise ValueError("key_fields cannot be empty")
    if ("available_field" in args) != ("decision_at" in args):
        raise ValueError("available_field and decision_at must be provided together")
    if ("expected_ids" in args) != ("id_field" in args):
        raise ValueError("expected_ids and id_field must be provided together")
    decision = timestamp(args["decision_at"]) if "decision_at" in args else None
    duplicates, missing, future, bad_times = [], [], [], []
    seen = set()
    for i, row in enumerate(rows):
        absent = [name for name in dict.fromkeys(keys + required) if row.get(name) is None or row.get(name) == ""]
        if absent:
            missing.append({"row": i, "fields": absent})
        if not any(name in absent for name in keys):
            key = json.dumps([row[name] for name in keys], sort_keys=True, allow_nan=False)
            if key in seen:
                duplicates.append(i)
            seen.add(key)
        if decision:
            try:
                if timestamp(row.get(args["available_field"])) > decision:
                    future.append(i)
            except (ValueError, TypeError):
                bad_times.append(i)
    absent_ids = []
    if "expected_ids" in args:
        expected = args["expected_ids"]
        if (not isinstance(expected, list) or len(expected) > 500 or
                not all(isinstance(x, str) for x in expected) or not isinstance(args["id_field"], str)):
            raise ValueError("expected_ids must be at most 500 strings, id_field a string")
        present = {str(r.get(args["id_field"])) for r in rows if r.get(args["id_field"]) is not None}
        absent_ids = sorted(set(expected) - present)
    result = {"row_count": len(rows), "duplicate_rows": duplicates, "missing_values": missing,
              "future_rows": future, "invalid_time_rows": bad_times, "missing_expected_ids": absent_ids,
              "passed": bool(rows) and not any((duplicates, missing, future, bad_times, absent_ids))}
    return receipt("data_quality", args, result,
                   "Zero-based row indexes. Supplied rows only; does not qualify full lake coverage, corporate actions, "
                   "timestamp truth or survivorship. Caller must supply correct required fields, scope and cutoff.")


TOOL_GUIDE = {
    "finance_compute": "operation=bond {face,coupon_rate,yield_rate,years,frequency,shock_bps?}; "
        "option {spot,strike,years,volatility,rate,dividend_yield,kind:call|put}; "
        "fx_return {local_return,fx_return}; scenario {weights:[],shocks:[],capital}. "
        "All rates/returns/weights are fractions; bond yield nominal, option rate continuous. No live data.",
    "data_quality": "{rows:array<=500,key_fields:[str],required_fields:[str],available_field?:str,decision_at?:ISO "
        "with timezone,expected_ids?:[str],id_field?:str}. Time and ID fields must be provided in pairs. "
        "Checks duplicates, missing values, future availability and expected-ID omissions in provided rows only.",
}


def run_tool(name, arguments):
    """Malformed model inputs are a correctable tool receipt, not a fatal task failure."""
    try:
        if name not in TOOL_GUIDE or not isinstance(arguments, dict):
            raise ValueError("Unknown specialist tool or invalid arguments")
        return finance_compute(arguments) if name == "finance_compute" else data_quality(arguments)
    except (ValueError, TypeError, KeyError, OverflowError, ZeroDivisionError) as exc:
        return {"ok": False, "error": str(exc)[:300], "tool_version": "1"}

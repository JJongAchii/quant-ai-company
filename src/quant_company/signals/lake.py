"""Credential-isolated read of the official KOSPI open and close for signal grading (runs as a subprocess)."""

import json
import sys
from datetime import date
from decimal import Decimal

MAX_BYTES = 4 * 1024 * 1024
MAX_ROWS = 400


def read(api, dates: list[date]) -> dict:
    """KOSPI rows {date, open, close} on the given dates only (the days the due slots need), with the dataset source
    as the receipt. The row cap counts those rows, so a long span between two needed days never blocks a read."""
    meta = api.inspect_dataset("krx_index")
    source = meta["source"]
    if source["size_bytes"] > MAX_BYTES:
        return {"ok": False, "error": "dataset_exceeds_signal_budget"}
    frame = api.load_krx_index(start=str(min(dates)), end=str(max(dates)))
    frame = frame[(frame["index"] == "KOSPI") & frame["date"].dt.date.isin(set(dates))]
    if len(frame) > MAX_ROWS:
        return {"ok": False, "error": "dataset_exceeds_signal_budget"}
    rows = []
    for row in frame[["date", "open", "close"]].to_dict("records"):
        values = {key: Decimal(str(row[key])) for key in ("open", "close")}
        if not all(v.is_finite() and v > 0 for v in values.values()):
            raise ValueError("invalid_index_value")
        rows.append({"date": str(row["date"].date()), "open": str(values["open"]), "close": str(values["close"])})
    if api.inspect_dataset("krx_index")["source"] != source:
        raise ValueError("source_changed_during_read")
    return {"ok": True, "rows": rows, "source": source}


def main():
    try:
        from qdata import api

        request = json.loads(sys.stdin.read(16384))
        result = read(api, [date.fromisoformat(day) for day in request["dates"]])
    except Exception:  # noqa: BLE001 - provider text never leaves the subprocess
        result = {"ok": False, "error": "lake_access_or_format_unavailable"}
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False, default=str)
    if len(encoded.encode()) > 131072:
        encoded = json.dumps({"ok": False, "error": "lake_result_too_large"})
    print(encoded)


def read_kospi(root: str, dates: list[date]) -> dict:
    from ..briefing.data import run_lake_module  # not imported in the reader subprocess

    return run_lake_module(root, "quant_company.signals.lake", {"dates": [str(day) for day in dates]})


if __name__ == "__main__":
    main()

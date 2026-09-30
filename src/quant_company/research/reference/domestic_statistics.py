"""Protected pure-Python scientific consumer shared by service and isolated worker."""

import csv
import io
import math
import statistics
from datetime import date


def evaluate_observations(content, evaluation, window):
    """Independent consumer recomputation from every dated observation.

    Monthly block means reduce daily pseudo-replication. The fixed normal interval
    is descriptive development evidence, not a multiple-testing-adjusted claim.
    """
    if evaluation["kind"] == "strategy":
        raise ValueError("Strategy metrics require executable return series")
    reader = csv.reader(io.StringIO(content.decode("utf-8")))
    if next(reader, None) != ["date", "value"]:
        raise ValueError("Scientific observations require date,value CSV")
    months, previous, count = {}, None, 0
    for row in reader:
        if len(row) != 2:
            raise ValueError("Observation width mismatch")
        day, value = date.fromisoformat(row[0]), float(row[1])
        if (day.isoformat() != row[0] or not math.isfinite(value) or previous and day <= previous
                or not date.fromisoformat(window["start"]) <= day <= date.fromisoformat(window["end"])):
            raise ValueError("Observation is nonfinite, unordered or outside development")
        months.setdefault(day.strftime("%Y-%m"), []).append(value)
        previous, count = day, count + 1
    if len(months) < evaluation["minimum_blocks"]:
        raise ValueError("Insufficient independent calendar blocks")
    values = [statistics.fmean(v) for v in months.values()]
    mean = statistics.fmean(values)
    se = statistics.stdev(values) / math.sqrt(len(values))
    low, high = mean - 1.959963984540054 * se, mean + 1.959963984540054 * se
    if evaluation["kind"] == "replication":
        score = abs(mean - evaluation["target"])
        supported = low >= evaluation["target"] - evaluation["tolerance"] and high <= evaluation["target"] + evaluation["tolerance"]
        rejected = high < evaluation["target"] - evaluation["tolerance"] or low > evaluation["target"] + evaluation["tolerance"]
    else:
        score = mean
        supported, rejected = low > evaluation["minimum_effect"], high <= evaluation["minimum_effect"]
    return {"value": score, "sample_count": count, "block_count": len(values), "mean": mean,
            "confidence_interval": [low, high],
            "conclusion": "supported" if supported else "not_supported" if rejected else "inconclusive",
            "scope": "development; fixed monthly-block normal interval; no multiplicity correction"}

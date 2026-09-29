"""Read-only comparison of the approved ETF inputs with the pinned qdata API.

Run with QDATA_LAKE set to the receipt's S3 mirror and PYTHONPATH pointing at a
git archive of the receipt's qdata commit. This does not read sealed inputs or
compute strategy returns.
"""

import argparse
import hashlib
import json
import math
import os
import subprocess
from collections import Counter, defaultdict
from datetime import UTC, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def day(value):
    return value.date().isoformat() if hasattr(value, "date") else str(value)[:10]


def positive(row):
    return all(isinstance(row[column], (int, float)) and math.isfinite(row[column]) and row[column] > 0
               for column in ("open", "close", "adj_close", "volume", "value"))


def pinned_package(api_path, repository, commit, source_root):
    if api_path.resolve() != (source_root / "qdata/api.py").resolve():
        raise ValueError("qdata_import_is_not_the_pinned_source_root")
    names = subprocess.check_output(["git", "-C", str(repository), "ls-tree", "-r", "--name-only",
                                     commit, "src/qdata"], text=True).splitlines()
    if not names:
        raise ValueError("pinned_qdata_tree_is_empty")
    for name in names:
        expected = subprocess.check_output(["git", "-C", str(repository), "show", f"{commit}:{name}"])
        if (source_root.parent / name).read_bytes() != expected:
            raise ValueError("qdata_import_tree_differs_from_pinned_commit:" + name)
    return len(names)


def selection_check(prices, metadata, *, selection_date, cohort):
    days = sorted({day(row["date"]) for row in prices if day(row["date"]) <= selection_date})
    if len(days) < 20 or days[-1] != selection_date:
        raise ValueError("selection_has_fewer_than_20_observed_sessions")
    window = set(days[-20:])
    meta_rows = [row for row in metadata if day(row["date"]) == selection_date]
    if len(meta_rows) != len({str(row["ticker"]) for row in meta_rows}):
        raise ValueError("selection_metadata_has_duplicate_tickers")
    eligible = {str(row["ticker"]) for row in meta_rows
                if row["is_leveraged"] is False and row["is_inverse"] is False}
    values = {}
    bad = set()
    for row in prices:
        key = (day(row["date"]), str(row["ticker"]))
        if key[0] not in window or key[1] not in eligible:
            continue
        if not positive(row):
            bad.add(key[1])
            continue
        by_day = values.setdefault(key[1], {})
        if key[0] in by_day:
            raise ValueError("selection_prices_have_duplicate_date_ticker")
        by_day[key[0]] = float(row["value"])
    rank = sorted(((sum(by_day.values()) / 20, ticker) for ticker, by_day in values.items()
                   if len(by_day) == 20 and ticker not in bad), key=lambda item: (-item[0], item[1]))
    selected = [ticker for _, ticker in rank[:len(cohort)]]
    return {"selection_sessions": days[-20:], "metadata_tickers": len(meta_rows),
            "eligible_complete_tickers": len(rank), "excluded_bad_tickers": len(bad),
            "selected_tickers": selected, "approved_cohort_matches": selected == cohort}


def compare_panel(current, frozen, *, cohort):
    current_by_key = {}
    for row in current:
        key = (day(row["date"]), str(row["ticker"]))
        if key in current_by_key:
            raise ValueError("current_panel_has_duplicate_date_ticker")
        current_by_key[key] = row
    frozen_by_key = {}
    for row in frozen:
        key = (row["date"], row["ticker"])
        if key in frozen_by_key:
            raise ValueError("frozen_panel_has_duplicate_date_ticker")
        frozen_by_key[key] = row
    if {ticker for _, ticker in frozen_by_key} != set(cohort):
        raise ValueError("frozen_cohort_differs_from_approved_cohort")
    mismatch = Counter()
    examples = {}
    adjustment_ratios = defaultdict(list)
    for key, frozen_row in frozen_by_key.items():
        current_row = current_by_key.get(key)
        if current_row is None:
            mismatch["missing_current_row"] += 1
            examples.setdefault("missing_current_row", key)
            continue
        for field in ("open", "close", "adj_close", "value"):
            value = current_row[field]
            if not isinstance(value, (int, float)) or not math.isfinite(value) or not math.isclose(
                    float(value), float(frozen_row[field]), rel_tol=0, abs_tol=1e-8):
                mismatch[field] += 1
                examples.setdefault(field, key)
        if (isinstance(current_row["adj_close"], (int, float))
                and math.isfinite(current_row["adj_close"]) and frozen_row["adj_close"] > 0):
            adjustment_ratios[key[1]].append(float(current_row["adj_close"]) / frozen_row["adj_close"])
        if positive(current_row) != frozen_row["tradable"]:
            mismatch["tradable"] += 1
            examples.setdefault("tradable", key)
    for key in current_by_key.keys() - frozen_by_key.keys():
        mismatch["extra_current_row"] += 1
        examples.setdefault("extra_current_row", key)
    days = sorted({key[0] for key in frozen_by_key})
    row_counts = Counter(key[0] for key in frozen_by_key)
    missing_cohort_sessions = sum(len(cohort) - row_counts[d] for d in days)
    if missing_cohort_sessions:
        mismatch["missing_frozen_cohort_row"] += missing_cohort_sessions
    nonconstant = [ticker for ticker, ratios in sorted(adjustment_ratios.items())
                   if max(ratios) - min(ratios) > 1e-8 * max(1, abs(ratios[0]))]
    return {"frozen_rows": len(frozen_by_key), "current_rows": len(current_by_key),
            "frozen_sessions": len(days), "missing_frozen_cohort_rows": missing_cohort_sessions,
            "adjustment_ratio_constant_by_ticker": not nonconstant,
            "nonconstant_adjustment_tickers": nonconstant,
            "mismatch_counts": dict(sorted(mismatch.items())),
            "first_mismatch_keys": {field: {"date": key[0], "ticker": key[1]}
                                    for field, key in sorted(examples.items())}}


def timing_check(frozen):
    dates = sorted({row["date"] for row in frozen})
    date_index = {date: index for index, date in enumerate(dates)}
    zone = ZoneInfo("Asia/Seoul")
    checked = late = 0
    for row in frozen:
        index = date_index[row["date"]]
        if index == len(dates) - 1:
            continue
        available = datetime.fromisoformat(row["available_at"])
        next_signal = datetime.combine(datetime.fromisoformat(dates[index + 1]).date(),
                                       time(8, 30), tzinfo=zone)
        checked += 1
        late += available > next_signal
    return {"next_session_0830_checked_rows": checked, "late_rows": late,
            "last_session_unchecked_rows": len(frozen) - checked,
            "origin_publication_timestamp_independently_verified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qdata-repo", type=Path, required=True)
    parser.add_argument("--qdata-source-root", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[4]
    from qdata import api

    prepared = json.loads((root / "docs/project/evidence/research-programs-20260928/real-inputs.json")
                          .read_text())["markets"]["kr_etf"]
    draft = json.loads((root / "docs/project/evidence/research-programs-20260928/first-program-draft.json")
                       .read_text())["program"]
    spec = next(item["template"] for item in draft["envelopes"] if item["name"] == "etf_strategy")
    source_root = args.qdata_source_root.resolve()
    package_files = pinned_package(Path(api.__file__), args.qdata_repo.resolve(), prepared["qdata_commit"],
                                   source_root)
    if os.environ.get("QDATA_LAKE") != prepared["lake_uri"]:
        raise ValueError("lake_uri_differs_from_preparation_receipt")
    if spec["data"]["input_files"] != prepared["input_files"]:
        raise ValueError("approved_input_hashes_differ_from_preparation_receipt")
    inputs = root / ".local/science-inputs/kr_etf-prepared-v2"
    frozen = []
    for name, expected in sorted(spec["data"]["input_files"].items()):
        path = inputs / name
        if sha(path) != expected:
            raise ValueError("frozen_input_hash_mismatch:" + name)
        frozen.extend(json.loads(path.read_text()))
    before = {name: api.inspect_dataset(name, sample_rows=0)["source"]
              for name in ("krx_etf", "krx_etf_meta")}
    selection_prices = api.load_krx_etf_prices(start=prepared["warmup_start"],
                                               end=prepared["selection_date"]).to_dict("records")
    selection_meta = api.load_krx_etf_meta(start=prepared["selection_date"],
                                           end=prepared["selection_date"]).to_dict("records")
    selection = selection_check(selection_prices, selection_meta,
                                selection_date=prepared["selection_date"], cohort=prepared["cohort"])
    panel = api.load_krx_etf_prices(start=prepared["warmup_start"], end=prepared["development_end"],
                                    tickers=prepared["cohort"]).to_dict("records")
    comparison = compare_panel(panel, frozen, cohort=prepared["cohort"])
    after = {name: api.inspect_dataset(name, sample_rows=0)["source"]
             for name in ("krx_etf", "krx_etf_meta")}
    if before != after:
        raise ValueError("lake_objects_changed_during_read")
    timing = timing_check(frozen)
    source_matches = before == prepared["source"]
    receipt = {"schema_version": 1, "observed_at": datetime.now(UTC).isoformat(),
               "scope": "read_only_approved_etf_input_vs_pinned_qdata_api",
               "verification_script_sha256": sha(Path(__file__)),
               "verification_command": (
                   "QDATA_LAKE=s3://insight-invest-datalake/qdata PYTHONPATH=<pinned-archive>/src "
                   "uv run --extra lake python "
                   "docs/project/evidence/research-programs-20260929/verify-etf-against-qdata.py "
                   "--qdata-repo <qdata-git-repo> --qdata-source-root <pinned-archive>/src"
               ),
               "approved_program_digest": "53392822414ca32e89fab0f3a9a1093a350196345bd13084654a45510297159b",
               "approved_input_files": spec["data"]["input_files"],
               "approved_execution_profile_digest": spec["execution_profile_digest"],
               "qdata_commit": prepared["qdata_commit"], "pinned_package_files_verified": package_files,
               "lake_source_at_preparation": prepared["source"], "lake_source_now": before,
               "lake_source_identity_matches_preparation": source_matches,
               "lake_source_version_ids_present": all(v.get("version_id") for v in before.values()),
               "selection": selection, "panel": comparison, "timing": timing,
               "execution_policy_independently_checked": False,
               "sealed_input_read": False, "performance_read": False,
               "data_readiness_decision": "not_made"}
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()

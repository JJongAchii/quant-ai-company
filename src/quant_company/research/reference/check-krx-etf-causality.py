#!/usr/bin/env python3
"""Verify KRX ETF point-in-time inputs and adjusted-anchor scale invariance.

This checker reads an already completed worker job. It writes booleans, identities,
hashes and row/check counts only; it never writes a return, score or risk magnitude.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

VARIANTS = ("M2", "A25", "A75", "B", "C", "D50", "D75")
COSTS = ("base", "stress")
PROFILE_FIELDS = {
    "asof", "issuer", "expense_ratio", "listing_date", "asset_class_raw", "market_class_raw",
    "replication_raw", "tax_type_raw", "cu_qty", "is_hedged",
}


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict | list:
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError(f"duplicate key in {path}: {key}")
            value[key] = item
        return value

    value = json.loads(path.read_text(), object_pairs_hook=pairs,
                       parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)))
    if not isinstance(value, (dict, list)):
        raise ValueError(f"JSON object or list required: {path}")
    return value


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise RuntimeError(reason)


def same(left, right, path="root") -> None:
    if isinstance(left, (float, np.floating)) or isinstance(right, (float, np.floating)):
        require(math.isclose(float(left), float(right), rel_tol=2e-9, abs_tol=2e-11), path)
    elif isinstance(left, dict) and isinstance(right, dict):
        require(left.keys() == right.keys(), path)
        for key in left:
            same(left[key], right[key], f"{path}.{key}")
    elif isinstance(left, list) and isinstance(right, list):
        require(len(left) == len(right), path)
        for index, (a, b) in enumerate(zip(left, right, strict=True)):
            same(a, b, f"{path}[{index}]")
    else:
        require(left == right, path)


def same_frame(left: pd.DataFrame, right: pd.DataFrame, label: str) -> None:
    try:
        pd.testing.assert_frame_equal(left, right, check_exact=False, rtol=2e-9, atol=2e-11)
    except AssertionError as exc:
        raise RuntimeError(label) from exc


def gpu_name() -> str:
    for command in (("/usr/lib/wsl/lib/nvidia-smi",), ("nvidia-smi",)):
        try:
            value = subprocess.check_output(
                [*command, "--query-gpu=name", "--format=csv,noheader"], text=True, timeout=10
            ).strip().splitlines()
        except (OSError, subprocess.SubprocessError):
            continue
        if value:
            return value[0]
    return "unavailable"


def scaled_prices(prices: pd.DataFrame) -> pd.DataFrame:
    result = prices.copy()
    scales = {
        ticker: 0.5 + (int(hashlib.sha256(ticker.encode()).hexdigest()[:8], 16) % 1501) / 1000
        for ticker in result.ticker.unique()
    }
    factor = result.ticker.map(scales).astype(float)
    result["adj_close"] = result.adj_close * factor
    result["adj_factor"] = result.adj_factor * factor
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-root", type=Path, required=True)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--source-contract", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--checker-commit", required=True)
    args = parser.parse_args()
    require(re.fullmatch(r"[0-9a-f]{40}", args.checker_commit) is not None, "invalid-checker-commit")
    job, inputs = args.job_root.resolve(), args.input_root.resolve()
    require(job.is_dir() and inputs.is_dir() and not args.output.exists(), "unsafe-path-or-existing-output")

    manifest = load_json(job / "manifest.json")
    job_receipt = load_json(job / "receipt.json")
    profile = load_json(job / "profile.json")
    contract = load_json(args.source_contract)
    require(isinstance(manifest, dict) and isinstance(job_receipt, dict), "invalid-job-json")
    require(isinstance(profile, dict) and isinstance(contract, dict), "invalid-contract-json")

    code_root = job / "workspace/code"
    lab = code_root / "labs/company-autonomous-etf"
    sys.path.insert(0, str(lab / "src"))
    from company_autonomous_etf import adapter, engine, signals

    verified_manifest, candidate = adapter.validate_manifest(
        manifest, lab / "configs/candidate.json", code_root
    )
    require(verified_manifest == manifest and tuple(signals.VARIANTS) == VARIANTS, "code-contract-mismatch")
    require(profile["id"] == manifest["plan"]["execution_profile"], "profile-mismatch")
    protected = {name: manifest["code_files"][name] for name in profile["protected_paths"]}
    require(all(sha(code_root / name) == digest for name, digest in protected.items()), "protected-code-hash")

    expected_inputs = manifest["plan"]["input_files"]
    require(all(sha(inputs / name) == digest for name, digest in expected_inputs.items()), "input-hash")
    require(job_receipt["input_files"] == expected_inputs, "job-input-binding")
    require(all(sha(job / "evaluate" / name) == digest
                for name, digest in job_receipt["output_files"].items()), "job-output-hash")

    snapshots = {stage: load_json(inputs / stage / "snapshot.json") for stage in ("warmup", "dev")}
    for snapshot in snapshots.values():
        require(snapshot["sourceCodeCommit"] == contract["source_code_commit"], "source-commit")
        require(snapshot["apiSha256"] == contract["api_sha256"], "api-hash")
        require(snapshot["returnBasis"] == "krx_reference_price_adjusted_return", "return-basis")
        require({key: value["etag"] for key, value in snapshot["sourceHeads"].items()}
                == contract["source_heads"], "source-heads")

    warmup = adapter.load_stage(inputs, manifest, "warmup")
    dev = adapter.load_stage(inputs, manifest, "dev")
    prices = pd.concat([warmup[0], dev[0]], ignore_index=True)
    meta = pd.concat([warmup[1], dev[1]], ignore_index=True)
    keys = ["date", "ticker"]
    require(not prices.duplicated(keys).any() and not meta.duplicated(keys).any(), "duplicate-pit-key")
    require(len(prices.merge(meta[keys], on=keys, how="outer", validate="one_to_one", indicator=True)
                .query("_merge != 'both'")) == 0, "price-meta-join")
    require(all(meta[column].isin([True, False]).all() for column in ("is_leveraged", "is_inverse")),
            "historical-exposure-flags")
    require(not PROFILE_FIELDS.intersection(meta.columns), "current-profile-field")
    start, end = pd.Timestamp(signals.POLICY["warmupStart"]), pd.Timestamp(signals.POLICY["devEnd"])
    sealed_start = pd.Timestamp(signals.POLICY["sealedStart"])
    require(prices.date.between(start, end).all() and meta.date.between(start, end).all(), "date-window")
    require(not prices.date.ge(sealed_start).any() and not meta.date.ge(sealed_start).any(), "sealed-row")

    panel = engine.make_panel(prices, meta, str(start.date()), str(end.date()))
    shifted = engine.make_panel(scaled_prices(prices), meta, str(start.date()), str(end.date()))
    dates = panel.frames["adj_close"].index
    cutoffs = [dates[index] for index in range(len(dates) - 1)
               if dates[index + 1].to_period("M") != dates[index].to_period("M")
               and dates[index + 1] >= pd.Timestamp(signals.POLICY["devStart"])]
    for variant in VARIANTS:
        for cutoff in cutoffs:
            same_frame(signals.features_asof(panel.frames, cutoff, variant),
                       signals.features_asof(shifted.frames, cutoff, variant),
                       f"feature-scale-invariance:{variant}:{cutoff.date()}")

    observed_dates = list(dates)
    next_date = {str(day.date()): str(observed_dates[index + 1].date())
                 for index, day in enumerate(observed_dates[:-1])}
    order_count = 0
    for cost in COSTS:
        orders = load_json(job / "evaluate" / f"orders-{cost}.json")
        require(isinstance(orders, list), "orders-json")
        for order in orders:
            require(next_date.get(order["signalDate"]) == order["fillDate"], "noncausal-order-date")
        order_count += len(orders)

    original_runs = {}
    for variant in VARIANTS:
        for cost in COSTS:
            original = signals.simulate(panel, variant, cost)
            shifted_run = signals.simulate(shifted, variant, cost)
            same_frame(original["daily"], shifted_run["daily"], f"daily-scale-invariance:{variant}:{cost}")
            for key in ("orders", "memberships", "riskConstraints", "terminalUses"):
                same(original[key], shifted_run[key], f"{key}-scale-invariance:{variant}:{cost}")
            if variant == candidate:
                original_runs[cost] = original

    for cost, run in original_runs.items():
        same(run["orders"], load_json(job / "evaluate" / f"orders-{cost}.json"), f"job-orders:{cost}")
        same(run["memberships"], load_json(job / "evaluate" / f"memberships-{cost}.json"),
             f"job-memberships:{cost}")
        require(adapter.return_csv(run["daily"])[0] == (job / "evaluate" / f"returns-{cost}.csv").read_bytes(),
                f"job-returns:{cost}")
        accounting = run["daily"].copy()
        accounting["date"] = accounting.date.dt.strftime("%Y-%m-%d")
        require(accounting.to_csv(index=False, lineterminator="\n").encode()
                == (job / "evaluate" / f"accounting-{cost}.csv").read_bytes(), f"job-accounting:{cost}")

    checker_path = Path(__file__).resolve()
    receipt = {
        "schema_version": 1,
        "kind": "krx_etf_causality_verification",
        "generated_at": datetime.now(UTC).isoformat(),
        "checker_commit": args.checker_commit,
        "checker_sha256": sha(checker_path),
        "source_contract_sha256": sha(args.source_contract),
        "source_code_commit": contract["source_code_commit"],
        "api_sha256": contract["api_sha256"],
        "source_heads": contract["source_heads"],
        "source_job_id": job_receipt["job_id"],
        "source_trial_id": str(manifest["trial_id"]),
        "source_archive_sha256": sha(job / "artifact.zip"),
        "execution": {"worker_id": "worker", "hostname": platform.node(), "gpu": gpu_name(),
                      "python": platform.python_version()},
        "input_files": expected_inputs,
        "protected_code_files": protected,
        "registered_variants": list(VARIANTS),
        "cost_scenarios": list(COSTS),
        "counts": {"price_rows": len(prices), "meta_rows": len(meta), "observed_sessions": len(dates),
                   "feature_cutoffs_per_variant": len(cutoffs), "simulation_comparisons": len(VARIANTS) * len(COSTS),
                   "job_orders_checked": order_count},
        "checks": {
            "input_hashes_match": True,
            "snapshots_bound_to_source_contract": True,
            "point_in_time_keys_unique": True,
            "price_meta_join_one_to_one": True,
            "historical_exposure_flags_boolean": True,
            "current_profile_fields_absent": True,
            "approved_dates_only": True,
            "sealed_rows_absent": True,
            "orders_use_next_observed_session": True,
            "all_registered_features_scale_invariant": True,
            "all_registered_simulations_scale_invariant": True,
            "risk_constraints_scale_invariant": True,
            "job_output_hashes_match": True,
            "candidate_job_reproduced": True,
            "performance_values_recorded": False,
        },
        "limitations": contract["known_limitations"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(receipt, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

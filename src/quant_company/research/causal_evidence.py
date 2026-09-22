"""Bind operator-produced KRX causal evidence to the exact audited trial bytes."""

from __future__ import annotations

import hashlib
import json
import re
from importlib.resources import files

from ..company import PolicyError
from .adaptive_report import ValidatedAdaptiveTrial

PROFILE_ID = "kr-etf-monthly-python-v1"
CONTRACT_FILE = "krx-etf-source-contract.json"
CHECKER_FILE = "check-krx-etf-causality.py"
RECEIPT_FILE = "krx-etf-causality-receipt.json"
REFERENCE_FILES = (CONTRACT_FILE, CHECKER_FILE, RECEIPT_FILE)
REQUIRED_CHECKS = {
    "input_hashes_match",
    "snapshots_bound_to_source_contract",
    "point_in_time_keys_unique",
    "price_meta_join_one_to_one",
    "historical_exposure_flags_boolean",
    "current_profile_fields_absent",
    "approved_dates_only",
    "sealed_rows_absent",
    "orders_use_next_observed_session",
    "all_registered_features_scale_invariant",
    "all_registered_simulations_scale_invariant",
    "risk_constraints_scale_invariant",
    "job_output_hashes_match",
    "candidate_job_reproduced",
    "performance_values_recorded",
}


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _object(content: bytes, label: str) -> dict:
    try:
        value = json.loads(content)
    except (UnicodeError, json.JSONDecodeError):
        raise PolicyError(f"{label}_invalid") from None
    if not isinstance(value, dict):
        raise PolicyError(f"{label}_invalid")
    return value


def _reference_bytes() -> dict[str, bytes]:
    root = files("quant_company.research").joinpath("reference")
    return {name: root.joinpath(name).read_bytes() for name in REFERENCE_FILES}


def audit_supplements(trials: tuple[ValidatedAdaptiveTrial, ...]) -> dict[str, bytes]:
    """Return the receipt only when its exact inputs and protected code cover every trial."""
    if not trials or any(trial.profile.id != PROFILE_ID for trial in trials):
        return {}
    if any(trial.manifest.plan.lake_id.startswith("synthetic:") for trial in trials):
        return {}

    evidence = _reference_bytes()
    contract = _object(evidence[CONTRACT_FILE], "krx_source_contract")
    receipt = _object(evidence[RECEIPT_FILE], "krx_causality_receipt")
    checks = receipt.get("checks")
    if (
        receipt.get("schema_version") != 1
        or receipt.get("kind") != "krx_etf_causality_verification"
        or not isinstance(checks, dict)
        or set(checks) != REQUIRED_CHECKS
        or checks["performance_values_recorded"] is not False
        or any(value is not True for key, value in checks.items() if key != "performance_values_recorded")
        or receipt.get("source_contract_sha256") != _sha(evidence[CONTRACT_FILE])
        or receipt.get("checker_sha256") != _sha(evidence[CHECKER_FILE])
        or not re.fullmatch(r"[0-9a-f]{40}", str(receipt.get("checker_commit", "")))
        or receipt.get("source_code_commit") != contract.get("source_code_commit")
        or receipt.get("api_sha256") != contract.get("api_sha256")
        or receipt.get("source_heads") != contract.get("source_heads")
        or receipt.get("registered_variants") != ["M2", "A25", "A75", "B", "C", "D50", "D75"]
        or receipt.get("cost_scenarios") != ["base", "stress"]
        or receipt.get("limitations") != contract.get("known_limitations")
    ):
        raise PolicyError("krx_causality_receipt_invalid")

    inputs = receipt.get("input_files")
    protected = receipt.get("protected_code_files")
    if not isinstance(inputs, dict) or not isinstance(protected, dict):
        raise PolicyError("krx_causality_receipt_invalid")
    source_trials = [trial for trial in trials
                     if str(trial.manifest.trial_id) == receipt.get("source_trial_id")]
    if (len(source_trials) != 1
            or source_trials[0].archive_sha256 != receipt.get("source_archive_sha256")):
        raise PolicyError("krx_causality_receipt_source_missing")
    for trial in trials:
        manifest = trial.manifest
        expected_protected = {
            path: manifest.code_files[path] for path in trial.profile.protected_paths
        }
        if manifest.plan.input_files != inputs or expected_protected != protected:
            raise PolicyError("krx_causality_receipt_scope_mismatch")
    return evidence

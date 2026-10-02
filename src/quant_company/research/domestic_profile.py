"""Prepare a reviewed worker source bundle; never activates a worker or runs science."""

import hashlib
import json
from importlib.resources import files
from pathlib import Path

from .adaptive_contracts import AdaptiveExecutionProfile, digest_model
from .builds import ServerResearchProfile
from .policy_contracts import ResearchDataPolicy, record_digest
from .sandbox import profile_from_dict
from .worker import atomic_json, sha_file
from .workspace import TextPatch, prepare_workspace

ROOT = "labs/company-domestic-research"
CANDIDATE = '''"""Engineering baseline only; replace inside the approved program scope."""

def weights(history, config):
    latest = max(row["date"] for row in history)
    tickers = sorted(row["ticker"] for row in history if row["date"] == latest)
    return {ticker: 1 / len(tickers) for ticker in tickers}

def predict(history, config):
    latest = max(row["date"] for row in history)
    tickers = sorted(row["ticker"] for row in history if row["date"] == latest)
    scores = {}
    for ticker in tickers:
        prices = [row["adj_close"] for row in history if row["ticker"] == ticker]
        if len(prices) < 2:
            raise ValueError("prediction_warmup_missing")
        scores[ticker] = prices[-1] / prices[-2] - 1
    return scores
'''


def prepare_domestic_profile(*, source_bundle, source_sha256, base_commit, destination, runtime,
                             input_sources, market, qualification_seconds, evaluation_seconds, fixture_only=False,
                             data_policy=None, policy_files=None):
    """Caller supplies an existing, verified runtime and immutable snapshots, not credentials."""
    if market not in {"kr_etf", "kr_stock"} or set(input_sources) != {"warmup.json", "development.json"}:
        raise ValueError("Domestic profile requires market and warmup/development snapshot bindings")
    runtime_profile = profile_from_dict(runtime)
    expected_id = market.replace("_", "-") + "-research-v2"
    if data_policy is not None:
        data_policy = ResearchDataPolicy.model_validate(data_policy)
        expected_id = market.replace("_", "-") + ("-retrospective-v1"
            if data_policy.mode == "frozen_vintage_retrospective" else "-pit-v3")
    if runtime_profile.profile_id != expected_id:
        raise ValueError("Runtime profile ID differs from the market profile")
    for path in input_sources.values():
        if not path.is_absolute() or path.is_symlink() or not path.is_file():
            raise ValueError("Input snapshots must be absolute immutable regular files")
    input_files = {name: sha_file(path) for name, path in input_sources.items()}
    policy_hashes = {}
    if data_policy is not None:
        expected = {ref.name: ref.sha256 for ref in data_policy.evidence_refs}
        if data_policy.input_files != input_files or set(policy_files or {}) != set(expected):
            raise ValueError("Policy must bind the actual inputs and all evidence files")
        for name, path in policy_files.items():
            if (not path.is_absolute() or path.is_symlink() or not path.is_file()
                    or path.stat().st_size > 8 * 1024 * 1024 or sha_file(path) != expected[name]):
                raise ValueError("Policy evidence is not a matching bounded regular file")
            policy_hashes[name] = sha_file(path)
    elif policy_files:
        raise ValueError("Legacy profiles cannot carry a research data policy")
    data_receipt_sha256 = None
    if not fixture_only:
        parents = {path.parent for path in input_sources.values()}
        if len(parents) != 1:
            raise ValueError("Scientific inputs must share one reviewed data receipt")
        data_receipt = next(iter(parents)) / "receipt.json"
        if not data_receipt.is_file() or data_receipt.is_symlink():
            raise ValueError("Scientific inputs require the data quality receipt")
        content = json.loads(data_receipt.read_text())
        if (content.get("state") != "ready" or content.get("market") != market
                or content.get("input_files") != input_files
                or content.get("quality", {}).get("bad_rows")
                or content.get("quality", {}).get("missing_rows")):
            raise ValueError("Scientific inputs failed the frozen quality and hash checks")
        data_receipt_sha256 = sha_file(data_receipt)
    resources = files("quant_company.research").joinpath("reference")
    contents = {
        ROOT + "/engine.py": resources.joinpath("domestic_engine.py").read_text(),
        ROOT + "/domestic_statistics.py": resources.joinpath("domestic_statistics.py").read_text(),
        ROOT + "/candidate.py": CANDIDATE,
        ROOT + "/config.json": json.dumps({"schema_version": 1, "description": "Engineering baseline; no economic claim"}),
    }
    destination.mkdir(parents=True, exist_ok=False)
    patches = (() if data_policy is not None else tuple(
        TextPatch(path=name, expected_text=None, replacement_text=content) for name, content in contents.items()))
    workspace = prepare_workspace(source_bundle, source_sha256, base_commit, destination / "source",
        (ROOT,), (), patches)
    entrypoint = ROOT + "/engine.py"
    if data_policy is not None:
        if not set(contents) <= workspace.manifest["files"].keys() or any(
                workspace.manifest["files"][name] != hashlib.sha256(contents[name].encode()).hexdigest()
                for name in (entrypoint, ROOT + "/domestic_statistics.py")):
            raise ValueError("Policy profiles require the unchanged prepared protected engine")
    public = AdaptiveExecutionProfile(**({"schema_version": 2, "data_policy_digest": record_digest(data_policy),
        "result_scope": data_policy.result_scope} if data_policy is not None else {}),
        id=expected_id, entrypoint=entrypoint,
        entrypoint_sha256=workspace.manifest["files"][entrypoint],
        protected_paths=[entrypoint, ROOT + "/domestic_statistics.py"], code_paths=sorted(contents),
        python_executable=runtime_profile.python_executable, python_sha256=runtime_profile.python_sha256,
        runtime_mounts=[{"target": mount.target, "sha256": mount.sha256} for mount in runtime_profile.mounts],
        qualification_input_names=["warmup.json"], evaluation_input_names=sorted(input_sources),
        qualification_timeout_seconds=qualification_seconds, evaluation_timeout_seconds=evaluation_seconds,
        fixture_only=fixture_only)
    server = ServerResearchProfile(public_profile=public, source_bundle=workspace.bundle_path,
        source_bundle_sha256=workspace.bundle_sha256, base_commit=workspace.commit,
        config_path=ROOT + "/config.json", config_files=[ROOT + "/config.json"],
        allowed_write_paths=[ROOT + "/candidate.py", ROOT + "/config.json"])
    from .adaptive_executor import AdaptiveProfile

    worker = AdaptiveProfile(public_profile=public, runtime=runtime, input_sources=input_sources,
        allowed_input_roots=sorted({path.parent for path in input_sources.values()}))
    atomic_json(destination / "server-profile.json", server.model_dump(mode="json"))
    atomic_json(destination / "worker-profile.json", worker.model_dump(mode="json"))
    identity = {"schema_version": 1, "state": "prepared_not_activated", "market": market,
        "base_commit": workspace.commit, "bundle_sha256": workspace.bundle_sha256,
        "execution_profile": public.id, "execution_profile_digest": digest_model(public),
        "input_files": {name: sha_file(path) for name, path in input_sources.items()},
        "protected_files": {name: workspace.manifest["files"][name] for name in public.protected_paths}}
    if data_receipt_sha256:
        identity["data_receipt_sha256"] = data_receipt_sha256
    if data_policy is not None:
        identity.update(data_policy=data_policy.model_dump(mode="json"), data_policy_digest=record_digest(data_policy),
                        policy_evidence_files=policy_hashes, qualified=False, activated=False)
        atomic_json(destination / "data-policy.json", data_policy.model_dump(mode="json"))
    atomic_json(destination / "identity.json", identity)
    return identity


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-bundle", type=Path, required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--base-commit", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--market", choices=["kr_etf", "kr_stock"], required=True)
    parser.add_argument("--qualification-seconds", type=int, required=True)
    parser.add_argument("--evaluation-seconds", type=int, required=True)
    parser.add_argument("--fixture-only", action="store_true")
    parser.add_argument("--data-policy", type=Path)
    parser.add_argument("--policy-evidence-root", type=Path)
    args = parser.parse_args()
    if args.destination.exists():
        parser.error("Use a new empty destination; preserve previous preparation receipts")
    policy = ResearchDataPolicy.model_validate_json(args.data_policy.read_text()) if args.data_policy else None
    if bool(policy) != bool(args.policy_evidence_root):
        parser.error("Data policy and policy evidence root must be supplied together")
    identity = prepare_domestic_profile(source_bundle=args.source_bundle.resolve(), source_sha256=args.source_sha256,
        base_commit=args.base_commit, destination=args.destination.resolve(), runtime=json.loads(args.runtime.read_text()),
        input_sources={name: args.input_root.resolve() / name for name in ("warmup.json", "development.json")},
        market=args.market, qualification_seconds=args.qualification_seconds,
        evaluation_seconds=args.evaluation_seconds, fixture_only=args.fixture_only, data_policy=policy,
        policy_files={ref.name: args.policy_evidence_root.resolve() / ref.name for ref in policy.evidence_refs}
        if policy else None)
    print(json.dumps(identity, sort_keys=True))


if __name__ == "__main__":
    main()

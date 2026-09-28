"""Actual Git preparation and synthetic worker producer/consumer; no market data."""

import json
from datetime import date

import pytest

from quant_company.research.adaptive_contracts import AdaptiveManifest, digest_model, record_digest
from quant_company.research.adaptive_executor import qualification_from_file, validate_result_files
from quant_company.research.builds import ServerResearchProfile
from quant_company.research.domestic_profile import ROOT, prepare_domestic_profile
from quant_company.research.mission_contracts import MissionSpec, TrialPlan
from quant_company.research.reference.domestic_engine import run
from quant_company.research.worker import sha_file

from .test_research_domestic_engine import rows
from .test_research_missions import spec_payload
from .test_research_workspace import make_snapshot


@pytest.mark.parametrize("market", ["kr_stock", "kr_etf"])
@pytest.mark.parametrize("kind", ["strategy", "claim", "replication"])
def test_prepared_bundle_qualifies_and_evaluates_with_exact_typed_consumer(tmp_path, market, kind, monkeypatch):
    _, bundle, base = make_snapshot(tmp_path)
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    data = [{**r, "market": market} for r in rows(7 if kind == "strategy" else 42)]
    start, end = "2020-01-03", "2020-01-07"
    if kind != "strategy":
        for row in data:
            index = (date.fromisoformat(row["date"]) - date(2020, 1, 1)).days
            row["date"] = date(2020 + index // 12, 1 + index % 12, 1).isoformat()
            row["available_at"] = row["date"] + "T18:00:00+09:00"
        start, end = "2020-03-01", "2023-06-01"
    for name, chosen in (("warmup.json", [r for r in data if r["date"] < start]),
                         ("development.json", [r for r in data if r["date"] >= start])):
        (inputs / name).write_text(json.dumps(chosen))
    runtime = {"profile_id": market.replace("_", "-") + "-research-v2", "python_executable": "/runtime/python",
        "python_sha256": "a"*64, "mounts": [{"source": str(tmp_path), "target": "/runtime", "sha256": "b"*64}],
        "allowed_roots": [str(tmp_path)], "bwrap_executable": "/usr/bin/bwrap"}
    destination = tmp_path / "release"
    receipt = prepare_domestic_profile(source_bundle=bundle, source_sha256=sha_file(bundle), base_commit=base,
        destination=destination, runtime=runtime, input_sources={name: inputs / name for name in ("warmup.json", "development.json")},
        market=market, qualification_seconds=30, evaluation_seconds=30, fixture_only=True)
    assert receipt["state"] == "prepared_not_activated"
    server = ServerResearchProfile.model_validate_json((destination / "server-profile.json").read_text())
    payload = spec_payload() | {"schema_version": 2, "market": market,
        "evaluation": {"kind": "strategy", "metric": "stress-net-absolute-cagr"},
        "execution_profile": server.public_profile.id, "execution_profile_digest": digest_model(server.public_profile),
        "development": {"start": start, "end": end}, "sealed": [],
        "data": {"lake_id": "synthetic-domestic-fixture", "input_files": receipt["input_files"]},
        "code": {"repository": "quant-lab", "base_commit": server.base_commit, "write_paths": server.allowed_write_paths}}
    if kind != "strategy":
        metric = "mean-effect" if kind == "claim" else "absolute-replication-error"
        payload.update(kind="claim", risk_constraints=[], evaluation={"kind": kind, "metric": metric,
            **({"target": .1, "tolerance": .01} if kind == "replication" else {})},
            objective={"metric": metric, "direction": "maximize" if kind == "claim" else "minimize", "unit": "fraction"})
    spec = MissionSpec.model_validate(payload)
    from uuid import uuid4

    code = destination / "source/code"
    monkeypatch.syspath_prepend(str(code / ROOT))
    config_path = ROOT + "/config.json"
    plan = TrialPlan(trial_id=uuid4(), proposal_id=uuid4(), mission_digest=record_digest(spec),
        execution_profile=server.public_profile.id, implementer="engineer", repository="quant-lab",
        code_commit=server.base_commit, changed_paths=[ROOT + "/candidate.py"],
        config_files={config_path: sha_file(code / config_path)}, input_files=receipt["input_files"],
        lake_id=spec.data.lake_id, development=spec.development, worker_id="worker",
        hostname="DESKTOP-5T00NAF", gpu="NVIDIA GeForce RTX 3070")
    manifest = AdaptiveManifest(id="kr-research-python-v2", mission_id=uuid4(), mission_digest=record_digest(spec),
        trial_id=plan.trial_id, plan_digest=record_digest(plan), plan=plan, spec=spec,
        code_files={name: sha_file(code / name) for name in server.public_profile.code_paths},
        bundle_sha256=server.source_bundle_sha256, config_path=config_path, company_commit="c"*40)
    path = tmp_path / "manifest.json"
    path.write_text(manifest.model_dump_json())
    for action in ("qualify", "evaluate"):
        output = tmp_path / action
        output.mkdir()
        # Explicit direct synthetic fixture. Production uses bubblewrap on the 3070 only.
        run(action, code / config_path, path, output, input_root=inputs)
    qualification_from_file(tmp_path / "qualify/qualification.json", manifest, server.public_profile)
    result = validate_result_files(tmp_path / "evaluate", manifest)
    assert result.metrics.sample_count == (5 if kind == "strategy" else 39)
    assert set(result.output_files) == ({"base.csv", "stress.csv"} if kind == "strategy" else {"observations.csv"})
    assert not any(name.endswith("token") for name in receipt)

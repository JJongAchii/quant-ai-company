"""Synthetic engineering fixtures; no market data or strategy is executed here."""

import csv
import hashlib
import io
import json
import stat
import subprocess
import zipfile
from datetime import date
from uuid import uuid4

import pytest

from quant_company.research.adaptive_contracts import (
    AdaptiveAssignment,
    AdaptiveExecutionProfile,
    AdaptiveExecutionReceipt,
    AdaptiveManifest,
    AdaptiveQualification,
    AdaptiveResult,
    digest_model,
    record_digest,
)
from quant_company.research.adaptive_report import (
    ReportHistory,
    build_adaptive_report,
    validate_adaptive_bundle,
)
from quant_company.research.mission_contracts import MissionSpec, TrialPlan
from quant_company.research.report import ValidationError

COMPANY = "c" * 40


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()


def sha(content):
    return hashlib.sha256(content).hexdigest()


def csv_bytes(rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["date", "net_return"])
    writer.writerows(rows)
    return stream.getvalue().encode()


def write_archive(path, contents, *, duplicate=None, symlink=None):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in contents.items():
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = ((stat.S_IFLNK | 0o777) if name == symlink else (stat.S_IFREG | 0o444)) << 16
            archive.writestr(info, content)
        if duplicate:
            with pytest.warns(UserWarning, match="Duplicate name"):
                archive.writestr(duplicate, contents[duplicate])


def producer(tmp_path):
    """A real isolated Git code snapshot produces typed fixture artifacts, never financial evidence."""
    repo = tmp_path / "repo"
    repo.mkdir()
    code = {
        "evaluator.py": b'raise RuntimeError("SYNTHETIC FIXTURE: validator must not execute this file")\n',
        "candidate.py": b"SYNTHETIC_FIXTURE = True\n",
        "config.json": encoded({"synthetic_fixture": True}),
    }
    for name, content in code.items():
        (repo / name).write_bytes(content)
    for args in (("init", "-q"), ("add", "."),
                 ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")):
        subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(repo), *args], check=True,
                       capture_output=True)
    commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    inputs = {"qualification.json": sha(b'{"synthetic_fixture":true}'), "development.csv": sha(b"synthetic\n")}
    profile = AdaptiveExecutionProfile(
        id="synthetic-monthly-v1", entrypoint="evaluator.py", entrypoint_sha256=sha(code["evaluator.py"]),
        protected_paths=["evaluator.py"], code_paths=list(code),
        python_executable="/runtime/bin/python", python_sha256="1" * 64,
        runtime_mounts=[{"target": "/runtime", "sha256": "2" * 64}],
        qualification_input_names=["qualification.json"], evaluation_input_names=list(inputs),
        qualification_timeout_seconds=10, evaluation_timeout_seconds=20, fixture_only=True,
    )
    spec = MissionSpec(
        title="Synthetic engineering fixture <script>not research</script>", kind="strategy",
        objective={"metric": "stress-net-absolute-cagr", "direction": "maximize", "unit": "fraction-per-year"},
        base_cost_bps=10, stress_cost_bps=30,
        risk_constraints=[{"metric": "max_drawdown", "maximum": .5, "unit": "fractional-loss"}],
        development={"start": "2024-01-01", "end": "2024-03-31"},
        sealed=[{"start": "2025-01-02", "end": "2025-12-31"}],
        data={"lake_id": "synthetic-fixture-not-research", "input_files": inputs},
        code={"repository": "quant-lab", "base_commit": commit, "write_paths": ["candidate.py", "config.json"]},
        allowed_changes=["implementation"], execution_profile=profile.id, execution_profile_digest=digest_model(profile),
        resources={"worker_id": "worker", "priority": "autonomous"},
        search={"max_trials_per_cycle": 3, "patience": 2, "min_improvement": .001, "continuous": True},
        baseline_source_ids=["fixture:baseline"],
    )
    trial = uuid4()
    plan = TrialPlan(
        trial_id=trial, proposal_id=uuid4(), mission_digest=record_digest(spec), execution_profile=profile.id,
        implementer="engineer", repository="quant-lab", code_commit=commit, changed_paths=["candidate.py"],
        config_files={"config.json": sha(code["config.json"])}, input_files=inputs, lake_id=spec.data.lake_id,
        development=spec.development, worker_id="worker", hostname="DESKTOP-5T00NAF", gpu="NVIDIA GeForce RTX 3070",
    )
    manifest = AdaptiveManifest(
        mission_id=uuid4(), mission_digest=record_digest(spec), trial_id=trial, plan_digest=record_digest(plan),
        plan=plan, spec=spec, code_files={name: sha(value) for name, value in code.items()},
        bundle_sha256="5" * 64, config_path="config.json", company_commit=COMPANY,
    )
    assignment = AdaptiveAssignment(job_id=uuid4(), project_id=uuid4(), revision=2,
                                    manifest_digest=digest_model(manifest), approval_event_id="synthetic-owner-event",
                                    lease_token="9" * 64, action="run", manifest=manifest)
    qualification = AdaptiveQualification(
        trial_id=trial, plan_digest=manifest.plan_digest, code_commit=commit, config_files=plan.config_files,
        input_files={"qualification.json": inputs["qualification.json"]}, sample_count=1,
        json_dates=["2024-01-01"], json_datetimes=["2024-01-01T00:00:00Z"],
        typed_schema={"date": "date", "timestamp": "datetime", "value": "float"}, primary_unit="fraction-per-year",
        empty_sample_rejected=True, non_finite_rejected=True, json_roundtrip_passed=True,
        performance_read=False, sealed_read=False,
    )
    outputs = {
        "base.csv": csv_bytes([("2024-01-01", 0), ("2024-01-31", .10), ("2024-02-29", -.05), ("2024-03-31", .02)]),
        "stress.csv": csv_bytes([("2024-01-01", 0), ("2024-01-31", .08), ("2024-02-29", -.06), ("2024-03-31", .01)]),
        "details.json": encoded({"synthetic_fixture": True}),
    }
    result = AdaptiveResult(
        trial_id=trial, plan_digest=manifest.plan_digest, code_commit=commit,
        metrics={"primary": {**spec.objective.model_dump(), "value": (1.08 * .94 * 1.01) ** (365.25 / 90) - 1},
                 "risks": {"max_drawdown": .06}, "sample_count": 4, "sample_window": spec.development},
        base_returns={"path": "base.csv", "sha256": sha(outputs["base.csv"]), "frequency": "daily", "unit": "fraction-per-period"},
        stress_returns={"path": "stress.csv", "sha256": sha(outputs["stress.csv"]), "frequency": "daily", "unit": "fraction-per-period"},
        output_files={name: sha(value) for name, value in outputs.items()},
    )
    runtime = {
        "schema_version": 1, "execution_profile_digest": digest_model(profile), "worker_id": "worker",
        "hostname": plan.hostname, "gpu": plan.gpu, "code_commit": commit, "company_commit": COMPANY,
        "python_executable": profile.python_executable, "python_sha256": profile.python_sha256,
        "runtime_mounts": [mount.model_dump() for mount in profile.runtime_mounts],
    }
    contents = {
        "manifest.json": manifest.model_dump_json().encode(), "profile.json": profile.model_dump_json().encode(),
        "qualification.json": qualification.model_dump_json().encode(), "result.json": result.model_dump_json().encode(),
        "runtime.json": encoded(runtime), **{"code/" + name: value for name, value in code.items()},
        **{"outputs/" + name: value for name, value in outputs.items()},
    }
    for phase, action, minute in (("qualification", "qualify", 1), ("evaluation", "evaluate", 3)):
        names = profile.qualification_input_names if action == "qualify" else profile.evaluation_input_names
        contents[f"sandbox-{phase}.json"] = encoded({
            "schema_version": 1, "spec_digest": "7" * 64, "code_commit": commit, "profile_id": profile.id,
            "pid": 123, "exit_code": 0, "timed_out": False,
            "started_at": f"2026-09-21T00:0{minute}:00Z", "completed_at": f"2026-09-21T00:0{minute + 1}:00Z",
            "stdout_path": action + "/stdout.log", "stderr_path": action + "/stderr.log", "action": action,
            "execution_profile_digest": digest_model(profile), "manifest_digest": digest_model(manifest),
            "input_files": {name: inputs[name] for name in names}, "entrypoint": profile.entrypoint,
            "argv": [action, "--config", "/code/config.json", "--manifest", "/inputs/__contract__/manifest.json", "--output", "/output"],
            "timeout_seconds": profile.qualification_timeout_seconds if action == "qualify" else profile.evaluation_timeout_seconds,
            "fixture_only": True,
        })
    receipt = AdaptiveExecutionReceipt(
        **assignment.model_dump(exclude={"lease_token", "action", "manifest"}),
        mission_id=manifest.mission_id, mission_digest=manifest.mission_digest,
        trial_id=trial, plan_digest=manifest.plan_digest, execution_profile_digest=digest_model(profile),
        worker_id="worker", hostname=plan.hostname, gpu=plan.gpu, code_commit=commit, company_commit=COMPANY,
        input_files=inputs, config_files=plan.config_files, output_files=result.output_files,
        qualification_sha256=sha(contents["qualification.json"]), result_sha256=sha(contents["result.json"]),
        runtime_sha256=sha(contents["runtime.json"]),
        sandbox_qualification_sha256=sha(contents["sandbox-qualification.json"]),
        sandbox_evaluation_sha256=sha(contents["sandbox-evaluation.json"]),
        started_at="2026-09-21T00:00:00Z", completed_at="2026-09-21T00:05:00Z", execution_count=1,
        qualification_passed=True, sealed_read=False, scientific_trials_added=0, fixture_only=True,
    )
    contents["receipt.json"] = receipt.model_dump_json().encode()
    path = tmp_path / "artifact.zip"
    write_archive(path, contents)
    return path, assignment, manifest, profile, contents


def validate(fixture):
    path, assignment, manifest, profile, _ = fixture
    return validate_adaptive_bundle(path, assignment, manifest, expected_company_commit=COMPANY, execution_profile=profile)


def history_for(trial):
    return ReportHistory(1, 0, 2, {str(trial.manifest.trial_id): 1}, trial.manifest.trial_id, trial.manifest.trial_id)


def mutate(fixture, name, change):
    path, _, _, _, contents = fixture
    payload = json.loads(contents[name])
    change(payload)
    contents[name] = encoded(payload)
    if name != "receipt.json":
        receipt = json.loads(contents["receipt.json"])
        field = {"qualification.json": "qualification_sha256", "result.json": "result_sha256",
                 "runtime.json": "runtime_sha256", "sandbox-qualification.json": "sandbox_qualification_sha256",
                 "sandbox-evaluation.json": "sandbox_evaluation_sha256"}.get(name)
        if field:
            receipt[field] = sha(contents[name])
        contents["receipt.json"] = encoded(receipt)
    write_archive(path, contents)


def test_actual_git_producer_to_internal_typed_artifact_and_hidden_report(tmp_path):
    fixture = producer(tmp_path)
    result = validate(fixture)
    assert result.receipt.code_commit == subprocess.check_output(
        ["git", "-C", str(tmp_path / "repo"), "rev-parse", "HEAD"], text=True).strip()
    assert result.receipt.fixture_only is True and result.receipt.scientific_trials_added == 0
    assert result.stress.absolute_cagr == pytest.approx((1.08 * .94 * 1.01) ** (365.25 / 90) - 1)
    assert result.stress.max_drawdown == pytest.approx(-.06)
    assert result.stress.monthly_returns == pytest.approx([.08, -.06, .01])
    assert result.stress.monthly[1][0] == date(2024, 2, 29)
    document = build_adaptive_report((result,), audit=None, history=history_for(result))
    assert document["summary"]["performance_visible"] is False
    assert document["summary"]["trials"] == [] and document["summary"]["excess_return"] is None
    assert "<svg" not in document["html"] and "<script>" not in document["html"]
    assert "-6.00%" not in document["html"]
    assert "SYNTHETIC FIXTURE" not in document["html"]
    with pytest.raises(TypeError):
        result.contents["result.json"] = b"modified"


@pytest.mark.parametrize("kind", ["duplicate", "extra", "path", "symlink", "json_duplicate", "json_trailing", "token"])
def test_archive_boundary_rejects_untrusted_structure(tmp_path, kind):
    fixture = producer(tmp_path)
    path, assignment, _, _, contents = fixture
    kwargs = {}
    if kind == "duplicate":
        kwargs["duplicate"] = "result.json"
    elif kind == "extra":
        contents["secret.txt"] = b"unexpected"
    elif kind == "path":
        contents["../outside"] = b"unexpected"
    elif kind == "symlink":
        kwargs["symlink"] = "outputs/stress.csv"
    elif kind == "json_duplicate":
        contents["result.json"] = contents["result.json"].replace(b'"schema_version":1', b'"schema_version":1,"schema_version":1')
    elif kind == "json_trailing":
        contents["result.json"] += b"{}"
    else:
        contents["outputs/details.json"] = encoded({"token": assignment.lease_token})
    write_archive(path, contents, **kwargs)
    with pytest.raises(ValidationError):
        validate(fixture)


@pytest.mark.parametrize(("name", "change", "code"), [
    ("receipt.json", lambda value: value.update(company_commit="a" * 40), "manifest_identity_mismatch"),
    ("receipt.json", lambda value: value.update(execution_count=True), "adaptive_typed_contract_mismatch"),
    ("receipt.json", lambda value: value.update(scientific_trials_added=1), "adaptive_typed_contract_mismatch"),
    ("qualification.json", lambda value: value.update(sample_count=0), "adaptive_typed_contract_mismatch"),
    ("qualification.json", lambda value: value.update(json_dates=["2024-01-01T00:00:00Z"]), "adaptive_typed_contract_mismatch"),
    ("qualification.json", lambda value: value.update(json_datetimes=["2024-01-01T00:00:00"]), "adaptive_typed_contract_mismatch"),
    ("qualification.json", lambda value: value.update(performance_read=True), "adaptive_typed_contract_mismatch"),
    ("qualification.json", lambda value: value.update(primary_unit="percent"), "adaptive_typed_contract_mismatch"),
    ("result.json", lambda value: value["metrics"]["primary"].update(value=99), "primary_metric_series_mismatch"),
    ("result.json", lambda value: value["metrics"].update(sample_count=5), "sample_count_mismatch"),
    ("result.json", lambda value: value["metrics"]["risks"].update(max_drawdown=0), "drawdown_metric_series_mismatch"),
    ("runtime.json", lambda value: value.update(python_sha256="f" * 64), "runtime_identity_mismatch"),
    ("sandbox-qualification.json", lambda value: value.update(exit_code=1), "sandbox_execution_failed"),
    ("sandbox-qualification.json", lambda value: value.update(completed_at="2026-09-21T00:04:00Z"), "qualification_not_before_evaluation"),
    ("sandbox-evaluation.json", lambda value: value.update(input_files={"sealed.csv": "a" * 64}), "sandbox_profile_scope_mismatch"),
])
def test_hash_valid_artifacts_must_satisfy_same_contract(tmp_path, name, change, code):
    fixture = producer(tmp_path)
    mutate(fixture, name, change)
    with pytest.raises(ValidationError, match=code):
        validate(fixture)


@pytest.mark.parametrize("rows", [[], [("2024-01-01", 0)], [("2024-01-01", .01), ("2024-03-31", .01)],
                                  [("2024-01-01T00:00:00Z", 0), ("2024-03-31", .01)],
                                  [("2024-01-01", 0), ("2024-01-01", .01)],
                                  [("2024-01-01", 0), ("2024-03-31", "nan")],
                                  [("2024-01-01", 0), ("2025-01-02", .01)]])
def test_empty_nonfinite_date_and_initial_return_contract(tmp_path, rows):
    fixture = producer(tmp_path)
    path, _, _, _, contents = fixture
    contents["outputs/stress.csv"] = csv_bytes(rows)
    output_hash = sha(contents["outputs/stress.csv"])
    result = json.loads(contents["result.json"])
    result["stress_returns"]["sha256"] = result["output_files"]["stress.csv"] = output_hash
    contents["result.json"] = encoded(result)
    receipt = json.loads(contents["receipt.json"])
    receipt["output_files"]["stress.csv"] = output_hash
    receipt["result_sha256"] = sha(contents["result.json"])
    contents["receipt.json"] = encoded(receipt)
    write_archive(path, contents)
    with pytest.raises(ValidationError):
        validate(fixture)


def test_changed_protected_evaluator_cannot_match_operator_profile(tmp_path):
    fixture = producer(tmp_path)
    path, _, _, _, contents = fixture
    contents["code/evaluator.py"] = b"print('untrusted changed evaluator')"
    write_archive(path, contents)
    with pytest.raises(ValidationError, match="artifact_content_hash_mismatch"):
        validate(fixture)


@pytest.mark.parametrize(("rows", "metric", "risk", "expected"), [
    ([("2024-01-01", 0), ("2024-01-31", -1), ("2024-02-29", 0), ("2024-03-31", 0)], -1, 1, [-1, None, None]),
    ([("2024-01-01", 0), ("2024-03-31", .01)], 1.01 ** (365.25 / 90) - 1, 0, [0, None, None]),
])
def test_bankruptcy_and_missing_months_are_retained_without_invented_monthly_returns(tmp_path, rows, metric, risk, expected):
    fixture = producer(tmp_path)
    path, _, _, _, contents = fixture
    result = json.loads(contents["result.json"])
    result["metrics"]["primary"]["value"] = metric
    result["metrics"]["risks"]["max_drawdown"] = risk
    result["metrics"]["sample_count"] = len(rows)
    for cost in ("base", "stress"):
        contents[f"outputs/{cost}.csv"] = csv_bytes(rows)
        digest = sha(contents[f"outputs/{cost}.csv"])
        result[f"{cost}_returns"]["sha256"] = result["output_files"][f"{cost}.csv"] = digest
    contents["result.json"] = encoded(result)
    receipt = json.loads(contents["receipt.json"])
    receipt["output_files"] = result["output_files"]
    receipt["result_sha256"] = sha(contents["result.json"])
    contents["receipt.json"] = encoded(receipt)
    write_archive(path, contents)
    trial = validate(fixture)
    assert list(trial.stress.monthly_returns) == expected
    assert trial.stress.absolute_cagr == pytest.approx(metric)

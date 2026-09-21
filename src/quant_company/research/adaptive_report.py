"""Hash-bound adaptive evidence and reports; validation alone never releases metrics."""

from __future__ import annotations

import calendar
import html
import io
import math
import re
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pydantic import ValidationError as ModelValidationError

from .adaptive_contracts import (
    AdaptiveAssignment,
    AdaptiveExecutionProfile,
    AdaptiveExecutionReceipt,
    AdaptiveManifest,
    AdaptiveQualification,
    AdaptiveResult,
    digest_model,
)
from .report import (
    MAX_ARCHIVE_BYTES,
    MAX_MEMBER_BYTES,
    ValidationError,
    _archive,
    _date,
    _instant,
    _json,
    _number,
    _require,
    _sha256,
    _table,
)

if TYPE_CHECKING:
    from .audit import VerifiedAudit

STATIC_MEMBERS = {
    "receipt.json", "manifest.json", "qualification.json", "result.json", "runtime.json",
    "sandbox-qualification.json", "sandbox-evaluation.json", "profile.json",
}


@dataclass(frozen=True)
class AdaptiveSeries:
    daily: tuple[tuple[date, float], ...]
    monthly: tuple[tuple[date, float | None], ...]
    monthly_returns: tuple[float | None, ...]
    drawdowns: tuple[float, ...]
    absolute_cagr: float
    max_drawdown: float


@dataclass(frozen=True)
class ValidatedAdaptiveTrial:
    """Internal evidence only. A genuine VerifiedAudit is required for public metrics."""

    manifest: AdaptiveManifest
    receipt: AdaptiveExecutionReceipt
    qualification: AdaptiveQualification
    result: AdaptiveResult
    profile: AdaptiveExecutionProfile
    contents: Mapping[str, bytes]
    base: AdaptiveSeries
    stress: AdaptiveSeries
    archive_sha256: str
    archive: bytes


@dataclass(frozen=True)
class ReportHistory:
    current_cycle: int
    cumulative_scientific_trials: int
    cumulative_technical_attempts: int
    trial_cycles: Mapping[str, int]
    best_trial_id: UUID | None
    last_trial_id: UUID

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_cycle": self.current_cycle,
            "cumulative_scientific_trials": self.cumulative_scientific_trials,
            "cumulative_technical_attempts": self.cumulative_technical_attempts,
            "trial_cycles": dict(self.trial_cycles),
            "best_trial_id": None if self.best_trial_id is None else str(self.best_trial_id),
            "last_trial_id": str(self.last_trial_id),
        }


def _model(model, content: bytes, token: str):
    # The JSON pass rejects duplicate keys, trailing data, non-finite values and secrets.
    raw = _json(content, token)
    try:
        value = model.model_validate_json(content)
        _scalar_contract(raw, value.model_dump(mode="json"))
        return value
    except (ModelValidationError, ValueError):
        raise ValidationError("adaptive_typed_contract_mismatch") from None


def _scalar_contract(raw: Any, parsed: Any) -> None:
    """Reject coercion while date validators retain JSON date/datetime handling."""
    if isinstance(parsed, dict):
        _require(isinstance(raw, dict), "json_scalar_type_mismatch")
        for key, item in raw.items():
            _require(key in parsed, "json_scalar_type_mismatch")
            _scalar_contract(item, parsed[key])
    elif isinstance(parsed, list):
        _require(isinstance(raw, list) and len(raw) == len(parsed), "json_scalar_type_mismatch")
        for item, normalized in zip(raw, parsed, strict=True):
            _scalar_contract(item, normalized)
    elif isinstance(parsed, float):
        _require(type(raw) in (int, float), "json_scalar_type_mismatch")
    else:
        _require(type(raw) is type(parsed), "json_scalar_type_mismatch")


def _preview_result(raw: bytes, token: str) -> AdaptiveResult:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            info = archive.getinfo("result.json")
            _require(info.file_size <= MAX_MEMBER_BYTES, "member_too_large")
            with archive.open(info) as stream:
                content = stream.read(MAX_MEMBER_BYTES + 1)
            _require(len(content) <= MAX_MEMBER_BYTES, "member_too_large")
            return _model(AdaptiveResult, content, token)
    except (KeyError, OSError, zipfile.BadZipFile, RuntimeError, EOFError):
        raise ValidationError("invalid_archive") from None


def _series(content: bytes, manifest: AdaptiveManifest) -> AdaptiveSeries:
    rows = _table(content, ["date", "net_return"])
    _require(len(rows) >= 2, "insufficient_return_observations")
    daily, drawdowns, months = [], [], {}
    wealth, peak = 1.0, 1.0
    for index, row in enumerate(rows):
        day, value = _date(row["date"]), _number(row["net_return"])
        _require(manifest.spec.development.start <= day <= manifest.spec.development.end,
                 "return_outside_development")
        _require(not daily or day > daily[-1][0], "series_date_order")
        _require(value >= -1 and (index != 0 or value == 0), "return_initial_or_domain_mismatch")
        wealth = _number(wealth * (1 + value))
        peak = max(peak, wealth)
        daily.append((day, wealth))
        drawdowns.append(wealth / peak - 1)
        month = date(day.year, day.month, calendar.monthrange(day.year, day.month)[1])
        months[(day.year, day.month)] = (month, wealth)
    monthly, monthly_returns, previous = [], [], 1.0
    first, last = daily[0][0], daily[-1][0]
    for serial in range(first.year * 12 + first.month - 1, last.year * 12 + last.month):
        year, month_index = divmod(serial, 12)
        month = month_index + 1
        month_end = date(year, month, calendar.monthrange(year, month)[1])
        wealth = months.get((year, month), (month_end, None))[1]
        monthly.append((month_end, wealth))
        # Missing observations and a zero denominator are unmeasured, not invented
        # flat returns or grounds for discarding a bankrupt scientific outcome.
        monthly_returns.append(_number(wealth / previous - 1)
                               if wealth is not None and previous is not None and previous > 0 else None)
        previous = wealth
    years = (daily[-1][0] - daily[0][0]).days / 365.25
    try:
        cagr = _number(daily[-1][1] ** (1 / years) - 1)
    except (OverflowError, ZeroDivisionError):
        raise ValidationError("non_finite_number") from None
    return AdaptiveSeries(tuple(daily), tuple(monthly), tuple(monthly_returns), tuple(drawdowns), cagr, min(drawdowns))


def validate_adaptive_bundle(
    path: Path, assignment: AdaptiveAssignment, expected_manifest: AdaptiveManifest, *,
    expected_company_commit: str, execution_profile: AdaptiveExecutionProfile,
) -> ValidatedAdaptiveTrial:
    """Check the operator-bound producer contract, derive metrics, keep them internal."""
    _require(isinstance(assignment, AdaptiveAssignment) and isinstance(expected_manifest, AdaptiveManifest),
             "adaptive_assignment_required")
    _require(assignment.action != "cancel", "assignment_cancelled")
    _require(assignment.manifest == expected_manifest
             and assignment.manifest_digest == digest_model(expected_manifest), "manifest_identity_mismatch")
    manifest = expected_manifest
    _require(manifest.company_commit == expected_company_commit, "company_commit_mismatch")
    _require(execution_profile.id == manifest.spec.execution_profile
             and digest_model(execution_profile) == manifest.spec.execution_profile_digest,
             "execution_profile_mismatch")
    _require(manifest.code_files.get(execution_profile.entrypoint) == execution_profile.entrypoint_sha256,
             "protected_evaluator_mismatch")
    _require(all(any(name == allowed or name.startswith(allowed + "/")
                     for allowed in execution_profile.code_paths) for name in manifest.code_files)
             and all(any(name == allowed or name.startswith(allowed + "/") for name in manifest.code_files)
                     for allowed in execution_profile.code_paths), "operator_code_scope_mismatch")
    try:
        _require(path.is_file() and not path.is_symlink(), "archive_not_regular")
        with path.open("rb") as stream:
            raw = stream.read(MAX_ARCHIVE_BYTES + 1)
    except OSError:
        raise ValidationError("invalid_archive") from None
    _require(len(raw) <= MAX_ARCHIVE_BYTES, "archive_too_large")
    result_preview = _preview_result(raw, assignment.lease_token)
    names = STATIC_MEMBERS | {f"code/{name}" for name in manifest.code_files} | {
        f"outputs/{name}" for name in result_preview.output_files
    }
    contents, archive_sha = _archive(path, names, assignment.lease_token)
    _require(archive_sha == _sha256(raw), "archive_changed_during_validation")
    receipt = _model(AdaptiveExecutionReceipt, contents["receipt.json"], assignment.lease_token)
    receipt_raw = _json(contents["receipt.json"], assignment.lease_token)
    _require(all(type(receipt_raw[key]) is int for key in ("schema_version", "execution_count", "scientific_trials_added"))
             and all(type(receipt_raw[key]) is bool for key in ("qualification_passed", "sealed_read", "fixture_only")),
             "invalid_execution_receipt")
    actual_manifest = _model(AdaptiveManifest, contents["manifest.json"], assignment.lease_token)
    profile = _model(AdaptiveExecutionProfile, contents["profile.json"], assignment.lease_token)
    qualification = _model(AdaptiveQualification, contents["qualification.json"], assignment.lease_token)
    result = _model(AdaptiveResult, contents["result.json"], assignment.lease_token)
    _require(actual_manifest == manifest and profile == execution_profile, "manifest_or_profile_mismatch")
    for field in ("job_id", "project_id", "revision", "recipe_id", "manifest_digest", "approval_event_id"):
        _require(getattr(receipt, field) == getattr(assignment, field), "assignment_identity_mismatch")
    for field in ("mission_id", "mission_digest", "trial_id", "plan_digest", "company_commit"):
        _require(getattr(receipt, field) == getattr(manifest, field), "manifest_identity_mismatch")
    for field in ("worker_id", "hostname", "gpu", "code_commit", "input_files", "config_files"):
        _require(getattr(receipt, field) == getattr(manifest.plan, field), "execution_provenance_mismatch")
    _require(receipt.execution_profile_digest == digest_model(profile)
             and receipt.fixture_only == profile.fixture_only, "execution_profile_mismatch")
    _require(receipt.output_files == result.output_files, "output_inventory_mismatch")
    _require(receipt.qualification_sha256 == _sha256(contents["qualification.json"])
             and receipt.result_sha256 == _sha256(contents["result.json"]), "result_evidence_hash_mismatch")
    _require(receipt.sandbox_qualification_sha256 == _sha256(contents["sandbox-qualification.json"])
             and receipt.sandbox_evaluation_sha256 == _sha256(contents["sandbox-evaluation.json"])
             and receipt.runtime_sha256 == _sha256(contents["runtime.json"]), "sandbox_evidence_hash_mismatch")
    for item in (qualification, result):
        _require(item.trial_id == manifest.trial_id and item.plan_digest == manifest.plan_digest
                 and item.code_commit == manifest.plan.code_commit, "trial_evidence_identity_mismatch")
    _require(qualification.config_files == manifest.plan.config_files, "qualification_config_mismatch")
    qualification_raw = _json(contents["qualification.json"], assignment.lease_token)
    _require(all(type(qualification_raw[key]) is bool for key in (
        "empty_sample_rejected", "non_finite_rejected", "json_roundtrip_passed", "performance_read", "sealed_read",
    )), "qualification_boolean_contract_mismatch")
    _require(qualification.input_files == {
        key: manifest.plan.input_files[key] for key in profile.qualification_input_names
        if key in manifest.plan.input_files
    } and set(qualification.input_files) == set(profile.qualification_input_names),
        "qualification_input_scope_mismatch")
    _require(set(profile.evaluation_input_names) == set(manifest.plan.input_files), "evaluation_input_scope_mismatch")
    for prefix, inventory in (("code", manifest.code_files), ("outputs", result.output_files)):
        for name, digest in inventory.items():
            _require(_sha256(contents[f"{prefix}/{name}"]) == digest, "artifact_content_hash_mismatch")
    for name, content in contents.items():
        if name.endswith(".json"):
            _json(content, assignment.lease_token)
    runtime = _json(contents["runtime.json"], assignment.lease_token)
    _require(runtime == {
        "schema_version": 1, "execution_profile_digest": digest_model(profile),
        "worker_id": receipt.worker_id, "hostname": receipt.hostname, "gpu": receipt.gpu,
        "code_commit": receipt.code_commit, "company_commit": receipt.company_commit,
        "python_executable": profile.python_executable, "python_sha256": profile.python_sha256,
        "runtime_mounts": [mount.model_dump(mode="json") for mount in profile.runtime_mounts],
    }, "runtime_identity_mismatch")
    phases = []
    for phase, action in (("qualification", "qualify"), ("evaluation", "evaluate")):
        payload = _json(contents[f"sandbox-{phase}.json"], assignment.lease_token)
        _require(isinstance(payload, dict) and payload.get("exit_code") == 0
                 and type(payload.get("exit_code")) is int and payload.get("timed_out") is False,
                 "sandbox_execution_failed")
        expected_names = profile.qualification_input_names if action == "qualify" else profile.evaluation_input_names
        expected_fields = {
            "schema_version": 1, "code_commit": manifest.plan.code_commit, "profile_id": profile.id,
            "action": action, "execution_profile_digest": digest_model(profile),
            "manifest_digest": digest_model(manifest),
            "input_files": {name: manifest.plan.input_files[name] for name in expected_names},
            "entrypoint": profile.entrypoint,
            "argv": [action, "--config", "/code/" + manifest.config_path, "--manifest",
                     "/inputs/__contract__/manifest.json", "--output", "/output"],
            "timeout_seconds": (profile.qualification_timeout_seconds if action == "qualify"
                                else profile.evaluation_timeout_seconds),
            "fixture_only": profile.fixture_only,
            "stdout_path": action + "/stdout.log", "stderr_path": action + "/stderr.log",
        }
        _require(set(payload) == set(expected_fields) | {
            "spec_digest", "pid", "exit_code", "timed_out", "started_at", "completed_at",
        } and all(payload.get(key) == value for key, value in expected_fields.items())
            and isinstance(payload.get("spec_digest"), str)
            and re.fullmatch(r"[a-f0-9]{64}", payload["spec_digest"]) is not None
            and type(payload.get("pid")) is int and payload["pid"] > 0
            and type(payload.get("fixture_only")) is bool
            and isinstance(payload.get("started_at"), str) and isinstance(payload.get("completed_at"), str),
            "sandbox_profile_scope_mismatch")
        start, end = _instant(payload.get("started_at", "")), _instant(payload.get("completed_at", ""))
        _require(receipt.started_at <= start <= end <= receipt.completed_at, "sandbox_execution_time_mismatch")
        phases.append((start, end))
    _require(phases[0][1] <= phases[1][0], "qualification_not_before_evaluation")
    base = _series(contents[f"outputs/{result.base_returns.path}"], manifest)
    stress = _series(contents[f"outputs/{result.stress_returns.path}"], manifest)
    _require(tuple(day for day, _ in base.daily) == tuple(day for day, _ in stress.daily),
             "cost_calendar_mismatch")
    metrics = result.metrics
    _require(metrics.sample_count == len(stress.daily), "sample_count_mismatch")
    _require(metrics.sample_window == manifest.spec.development
             and stress.daily[0][0] == metrics.sample_window.start
             and stress.daily[-1][0] == metrics.sample_window.end, "sample_window_mismatch")
    _require(math.isclose(metrics.primary.value, stress.absolute_cagr, rel_tol=0, abs_tol=1e-12),
             "primary_metric_series_mismatch")
    if "max_drawdown" in metrics.risks:
        _require(math.isclose(metrics.risks["max_drawdown"], -stress.max_drawdown, rel_tol=0, abs_tol=1e-12),
                 "drawdown_metric_series_mismatch")
    _require(all(risk.metric in metrics.risks for risk in manifest.spec.risk_constraints),
             "required_risk_metric_missing")
    return ValidatedAdaptiveTrial(
        manifest.model_copy(deep=True), receipt, qualification, result, profile,
        MappingProxyType(contents), base, stress, archive_sha, raw,
    )


def _curve(trial: ValidatedAdaptiveTrial, *, drawdown: bool) -> str:
    series = (trial.base, trial.stress)
    values = [item.drawdowns if drawdown else tuple(value for _, value in item.daily) for item in series]
    low, high = min(map(min, values)), max(map(max, values))
    span = high - low or 1
    start, end = trial.stress.daily[0][0], trial.stress.daily[-1][0]
    polylines = []
    for item, numbers, color in zip(series, values, ("#2563eb", "#b91c1c"), strict=True):
        points = " ".join(f"{50 + (day - start).days / (end - start).days * 680:.2f},"
                          f"{20 + (high - value) / span * 180:.2f}"
                          for (day, _), value in zip(item.daily, numbers, strict=True))
        polylines.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2"/>')
    title = "Drawdown" if drawdown else "Wealth (initial 1)"
    top, bottom = (f"{high:.1%}", f"{low:.1%}") if drawdown else (f"{high:.3f}", f"{low:.3f}")
    return (f'<svg viewBox="0 0 780 245" role="img" aria-label="{title}"><title>{title}</title>'
            f'<text x="0" y="20">{top}</text><text x="0" y="200">{bottom}</text>'
            f'{"".join(polylines)}<text x="50" y="232">{start}</text>'
            f'<text x="650" y="232">{end}</text></svg>')


def _percent(value: float | None) -> str:
    return "미측정" if value is None else f"{value:.2%}"


def build_adaptive_report(
    trials: tuple[ValidatedAdaptiveTrial, ...], *, audit: VerifiedAudit | None, history: ReportHistory,
) -> dict[str, Any]:
    """Standalone artifact-derived HTML; no independent verification means no metrics/prose."""
    from .audit import assert_verified_trials

    _require(bool(trials) and all(isinstance(trial, ValidatedAdaptiveTrial) for trial in trials),
             "validated_trials_required")
    ids = {str(trial.manifest.trial_id) for trial in trials}
    _require(len(ids) == len(trials) and set(history.trial_cycles) == ids
             and history.current_cycle >= 1
             and history.cumulative_scientific_trials >= sum(trial.receipt.scientific_trials_added for trial in trials)
             and history.cumulative_technical_attempts >= 0
             and all(type(cycle) is int and 1 <= cycle <= history.current_cycle
                     for cycle in history.trial_cycles.values())
             and str(history.last_trial_id) in ids
             and (history.best_trial_id is None or str(history.best_trial_id) in ids), "report_history_mismatch")
    summary = {
        "kind": "adaptive_discovery", "development_only": True, "performance_visible": audit is not None,
        "synthetic": all(trial.receipt.fixture_only for trial in trials),
        "baseline_measured": False, "excess_return": None, "confirmation_claim": False, "live_claim": False,
        "current_cycle": history.current_cycle, "cumulative_scientific_trials": history.cumulative_scientific_trials,
        "cumulative_technical_attempts": history.cumulative_technical_attempts, "trials": [],
    }
    sections = []
    if audit is None:
        sections.append('<section><h2>성과 비공개</h2><p>현재 산출물에 연결된 독립 감사가 검증되지 않았습니다. '
                        '수익률·낙폭·연구 해석은 공개하지 않습니다.</p></section>')
    else:
        assert_verified_trials(audit, trials)
        from .audit import assert_verified_history
        assert_verified_history(audit, history)
        eligible = [trial for trial in sorted(trials, key=lambda item: (item.receipt.started_at, str(item.manifest.trial_id)))
                    if all(trial.result.metrics.risks[risk.metric] <= risk.maximum
                           for risk in trial.manifest.spec.risk_constraints)]
        best = max(eligible, key=lambda trial: trial.stress.absolute_cagr) if eligible else None
        _require(history.best_trial_id == (best.manifest.trial_id if best else None), "reported_best_trial_mismatch")
        for trial in trials:
            trial_id = str(trial.manifest.trial_id)
            role = "best" if trial.manifest.trial_id == history.best_trial_id else "candidate"
            if trial.manifest.trial_id == history.last_trial_id:
                role += " / last"
            item = {
                "trial_id": trial_id, "cycle": history.trial_cycles[trial_id], "selection": role,
                "code_commit": trial.receipt.code_commit, "archive_sha256": trial.archive_sha256,
                "base": {"absolute_cagr": trial.base.absolute_cagr, "max_drawdown": trial.base.max_drawdown},
                "stress": {"absolute_cagr": trial.stress.absolute_cagr, "max_drawdown": trial.stress.max_drawdown},
            }
            summary["trials"].append(item)
            rows = "".join(
                f"<tr><th>{day}</th><td>{_percent(base)}</td><td>{_percent(stress)}</td></tr>"
                for (day, _), base, stress in zip(trial.base.monthly, trial.base.monthly_returns,
                                                trial.stress.monthly_returns, strict=True)
            )
            costs = trial.manifest.spec
            sections.append(
                f'<section><h2>{html.escape(trial_id)} · {role}</h2>'
                f'<p>Cycle {history.trial_cycles[trial_id]} · base {costs.base_cost_bps:g}bp / '
                f'stress {costs.stress_cost_bps:g}bp. 파랑 base, 빨강 stress.</p>'
                '<table><thead><tr><th>비용</th><th>절대 CAGR</th><th>최대 낙폭</th></tr></thead><tbody>'
                f'<tr><th>base</th><td>{trial.base.absolute_cagr:.2%}</td><td>{trial.base.max_drawdown:.2%}</td></tr>'
                f'<tr><th>stress</th><td>{trial.stress.absolute_cagr:.2%}</td><td>{trial.stress.max_drawdown:.2%}</td></tr>'
                f'</tbody></table><h3>자산곡선</h3>{_curve(trial, drawdown=False)}'
                f'<h3>낙폭</h3>{_curve(trial, drawdown=True)}<h3>월별 수익률</h3>'
                '<table><thead><tr><th>달력 월말</th><th>base</th><th>stress</th></tr></thead>'
                f'<tbody>{rows}</tbody></table><p>Exact code: <code>{trial.receipt.code_commit}</code></p></section>'
            )
        summary["audit"] = audit.public_receipt()
    document = (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>자율 연구 · 개발구간 보고서</title><style>'
        'body{font:16px/1.6 system-ui;max-width:1040px;margin:36px auto;padding:0 20px;color:#172033}'
        'section{border:1px solid #cbd5e1;padding:20px;margin:24px 0}table{width:100%;border-collapse:collapse}'
        'th,td{padding:8px;text-align:right;border-bottom:1px solid #ddd}th:first-child{text-align:left}'
        'svg{width:100%;height:auto}svg text{font:12px system-ui}code{overflow-wrap:anywhere}'
        '</style></head><body><h1>자율 연구 · development-only</h1>'
        + ('<p><strong>Synthetic engineering fixture — 실제 연구 성과가 아닙니다.</strong></p>'
           if summary["synthetic"] else '') +
        f'<p>누적 과학 시행 {history.cumulative_scientific_trials}회 · '
        f'기술 시도 {history.cumulative_technical_attempts}회 · 현재 cycle {history.current_cycle}.</p>'
        '<p>벤치마크·초과수익은 미측정입니다. 확증 통과 또는 실거래 성과를 뜻하지 않습니다.</p>'
        '<p>initial-zero-calendar-cagr-v1: 첫 수익률은 0, 시작자산은 1. '
        'CAGR = 최종자산^(365.25 / 실제 경과일수) − 1. 낙폭은 자산 / 누적 최고자산 − 1. '
        '월 수익률은 월말 마지막 관측 자산 / 전월말 자산 − 1 (첫 달 분모 1).</p>'
        '<p>월별 관측이 빠졌거나 전월 자산이 0이면 해당 월의 수익률은 미측정으로 표시합니다.</p>'
        f'{"".join(sections)}<footer><p>다음 단계: 승인된 목표와 연구 예산 안에서 근거를 검토하고 '
        '후속 가설을 선택합니다.</p></footer></body></html>'
    )
    return {"html": document, "summary": summary}

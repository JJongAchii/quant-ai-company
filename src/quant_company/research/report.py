"""Validate a frozen P11 replay before deriving any report or model-visible metrics."""

from __future__ import annotations

import calendar
import csv
import hashlib
import html
import io
import json
import math
import stat
import zipfile
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any

from pydantic import ValidationError as ModelValidationError

from .contracts import Assignment, ExecutionReceipt, Recipe
from .recipes import recipe_digest

MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_MEMBER_BYTES = 32 * 1024 * 1024
DEV_START = date(2014, 1, 1)
CANDIDATES = ("m1", "m2", "m3")
COSTS = ("base", "stress")
OUTPUT_NAMES = {"objective.json"} | {
    f"{kind}-{cost}.{extension}"
    for kind, extension in (
        ("daily", "csv"), ("monthly", "csv"), ("orders", "json"),
        ("memberships", "json"), ("terminal-events", "json"),
    )
    for cost in COSTS
}
METADATA_NAMES = {"receipt.json", "audit.md", "audit.receipt.json", "audit-verification.json"}
DAILY_COLUMNS = ["date", "equity", "cashWeight", "grossWeight", "turnover", "cost", "holdings"]
RETURN_BASIS = "krx_reference_price_adjusted_return"
AUDIT_API = "qlab.audits.record.validate_audit+qlab.audits.receipt.check_receipt"


class ValidationError(ValueError):
    """A stable, sanitized reason: never include worker-controlled text or metrics."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(f"Replay validation failed: {code}")


@dataclass(frozen=True)
class Series:
    daily: tuple[tuple[date, float], ...]
    monthly: tuple[tuple[date, float], ...]
    monthly_returns: tuple[float, ...]
    drawdowns: tuple[float, ...]
    absolute_cagr: float
    max_drawdown: float


@dataclass(frozen=True)
class Candidate:
    base: Series
    stress: Series
    objective: Mapping[str, Any]


@dataclass(frozen=True)
class ValidatedReplay:
    recipe: Recipe
    receipt: ExecutionReceipt
    outputs: Mapping[str, bytes]
    candidates: Mapping[str, Candidate]
    audit_verification: Mapping[str, Any]
    archive_sha256: str


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ValidationError(code)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _safe_name(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and (
        not path.is_absolute() and str(path) == name and ".." not in path.parts
        and "\\" not in name and ":" not in name
        and all(ord(char) >= 32 and ord(char) != 127 for char in name)
    )


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in result, "json_duplicate_key")
        result[key] = value
    return result


def _bad_constant(_: str) -> None:
    raise ValidationError("non_finite_number")


def _json(content: bytes, lease_token: str) -> Any:
    try:
        value = json.loads(content.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_bad_constant)
        pending = [value]
        while pending:
            item = pending.pop()
            if isinstance(item, float):
                _require(math.isfinite(item), "non_finite_number")
            elif isinstance(item, str):
                _require(lease_token not in item, "lease_secret_in_artifact")
            elif isinstance(item, dict):
                _require("lease_token" not in item and "leaseToken" not in item, "lease_secret_in_artifact")
                pending.extend(item.keys())
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
        return value
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, ValidationError):
            raise
        raise ValidationError("invalid_json") from None


def _archive(path: Path, names: set[str], lease_token: str) -> tuple[dict[str, bytes], str]:
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_ARCHIVE_BYTES + 1)
        _require(len(raw) <= MAX_ARCHIVE_BYTES, "archive_too_large")
        _require(lease_token.encode() not in raw, "lease_secret_in_artifact")
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            _require(len(infos) == len(names), "member_set_mismatch")
            _require(len({info.filename for info in infos}) == len(infos), "duplicate_member")
            _require(all(_safe_name(info.orig_filename) and info.orig_filename == info.filename for info in infos), "unsafe_member_path")
            _require({info.filename for info in infos} == names, "member_set_mismatch")
            total = 0
            contents: dict[str, bytes] = {}
            for info in infos:
                mode = stat.S_IFMT(info.external_attr >> 16)
                _require(not info.is_dir() and mode in (0, stat.S_IFREG), "non_regular_member")
                _require(not info.flag_bits & 1, "encrypted_member")
                _require(info.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED), "unsupported_compression")
                _require(info.file_size <= MAX_MEMBER_BYTES, "member_too_large")
                total += info.file_size
                _require(total <= MAX_EXPANDED_BYTES, "expanded_archive_too_large")
                with archive.open(info) as member:
                    content = member.read(MAX_MEMBER_BYTES + 1)
                _require(len(content) <= MAX_MEMBER_BYTES, "member_too_large")
                _require(len(content) == info.file_size, "member_size_mismatch")
                _require(lease_token.encode() not in content, "lease_secret_in_artifact")
                _require(not zipfile.is_zipfile(io.BytesIO(content)), "nested_archive")
                contents[info.filename] = content
            return contents, _sha256(raw)
    except (OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError, zlib.error):
        raise ValidationError("invalid_archive") from None


def _instant(value: str) -> datetime:
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        _require("T" in value and instant.utcoffset() is not None, "invalid_execution_time")
        return instant
    except ValueError as exc:
        if isinstance(exc, ValidationError):
            raise
        raise ValidationError("invalid_execution_time") from None


def _receipt(
    content: bytes, recipe: Recipe, assignment: Assignment, expected_company_commit: str,
) -> ExecutionReceipt:
    payload = _json(content, assignment.lease_token)
    _require(isinstance(payload, dict), "invalid_execution_receipt")
    try:
        receipt = ExecutionReceipt.model_validate_json(content, strict=True)
    except ModelValidationError:
        raise ValidationError("invalid_execution_receipt") from None
    for field in ("job_id", "project_id", "revision", "recipe_id", "manifest_digest", "approval_event_id"):
        _require(getattr(receipt, field) == getattr(assignment, field), "assignment_identity_mismatch")
    for field in ("worker_id", "hostname", "gpu", "code_commit", "input_files", "config_files"):
        _require(getattr(receipt, field) == getattr(recipe, field), "execution_provenance_mismatch")
    _require(receipt.company_commit == expected_company_commit, "company_commit_mismatch")
    _require(receipt.output_files == recipe.expected_outputs, "output_inventory_mismatch")
    _require(receipt.qualification_passed is True, "qualification_unverified")
    _require(type(payload["execution_count"]) is int and type(payload["scientific_trials_added"]) is int
             and type(payload["sealed_read"]) is bool,
             "invalid_execution_receipt")
    _require(_instant(receipt.completed_at) >= _instant(receipt.started_at), "invalid_execution_time")
    return receipt


def _audit(contents: dict[str, bytes], recipe: Recipe, lease_token: str) -> dict[str, Any]:
    _require(_sha256(contents["audit.md"]) == recipe.audit_sha256, "audit_hash_mismatch")
    _require(_sha256(contents["audit.receipt.json"]) == recipe.audit_receipt_sha256, "audit_receipt_hash_mismatch")
    audit_receipt = _json(contents["audit.receipt.json"], lease_token)
    _require(isinstance(audit_receipt, dict), "invalid_audit_receipt")
    expected_receipt = {
        "audit": PurePosixPath(recipe.audit_path).name,
        "audit_sha256": recipe.audit_sha256,
        "scope_digest": recipe.scope_digest,
        "verdict": "pass",
    }
    _require(all(audit_receipt.get(key) == value for key, value in expected_receipt.items()),
             "audit_receipt_identity_mismatch")
    verification = _json(contents["audit-verification.json"], lease_token)
    expected = {
        "schema_version": 1, "api": AUDIT_API,
        "evidence_commit": recipe.evidence_commit,
        "audit_sha256": recipe.audit_sha256,
        "audit_receipt_sha256": recipe.audit_receipt_sha256,
        "objective_digest": recipe.objective_digest,
        "scope_digest": recipe.scope_digest, "scope_files": recipe.scope_files,
        "verdict": "pass", "violations": [], "receipt_violations": [],
    }
    _require(isinstance(verification, dict) and verification == expected
             and type(verification.get("schema_version")) is int, "audit_revalidation_mismatch")
    return verification


def _date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
        _require(value == parsed.isoformat(), "invalid_series_date")
        return parsed
    except ValueError as exc:
        if isinstance(exc, ValidationError):
            raise
        raise ValidationError("invalid_series_date") from None


def _number(value: Any) -> float:
    _require(isinstance(value, (str, int, float)) and not isinstance(value, bool), "invalid_numeric_field")
    try:
        result = float(value)
    except (ValueError, OverflowError):
        raise ValidationError("invalid_numeric_field") from None
    _require(math.isfinite(result), "non_finite_number")
    return result


def _table(content: bytes, columns: list[str]) -> list[dict[str, str]]:
    try:
        reader = csv.DictReader(io.StringIO(content.decode("utf-8"), newline=""), strict=True)
        _require(reader.fieldnames == columns, "csv_schema_mismatch")
        rows = list(reader)
        _require(bool(rows), "empty_series")
        _require(all(set(row) == set(columns) and all(value is not None for value in row.values())
                     for row in rows), "csv_schema_mismatch")
        return rows
    except (UnicodeError, csv.Error):
        raise ValidationError("invalid_csv") from None


def _series(daily_content: bytes, monthly_content: bytes) -> Series:
    daily_rows = _table(daily_content, DAILY_COLUMNS)
    daily: list[tuple[date, float]] = []
    month_ends: dict[tuple[int, int], tuple[date, float]] = {}
    peak = 1.0
    drawdowns: list[float] = []
    for row in daily_rows:
        day = _date(row["date"])
        _require(day > DEV_START and (not daily or day > daily[-1][0]), "series_date_order")
        numbers = {field: _number(row[field]) for field in DAILY_COLUMNS[1:]}
        equity = numbers["equity"]
        _require(equity >= 0, "invalid_equity")
        _require(numbers["holdings"] >= 0 and numbers["holdings"].is_integer(), "invalid_holdings")
        daily.append((day, equity))
        peak = max(peak, equity)
        drawdowns.append(equity / peak - 1)
        month_end = date(day.year, day.month, calendar.monthrange(day.year, day.month)[1])
        month_ends[(day.year, day.month)] = (month_end, equity)
    monthly_rows = _table(monthly_content, ["date", "equity"])
    monthly = tuple((_date(row["date"]), _number(row["equity"])) for row in monthly_rows)
    expected_months = tuple(month_ends.values())
    _require(len(monthly) == len(expected_months), "monthly_coverage_mismatch")
    for index, ((day, equity), (expected_day, expected_equity)) in enumerate(zip(monthly, expected_months, strict=True)):
        _require(day == expected_day and equity == expected_equity, "monthly_daily_mismatch")
        if index:
            previous = monthly[index - 1][0]
            _require(day.year * 12 + day.month == previous.year * 12 + previous.month + 1,
                     "monthly_coverage_mismatch")
    previous_equity = 1.0
    monthly_returns = []
    for _, equity in monthly:
        _require(previous_equity > 0, "undefined_monthly_return")
        monthly_returns.append(_number(equity / previous_equity - 1))
        previous_equity = equity
    years = ((daily[-1][0] - DEV_START).days + 1) / 365.2425
    try:
        cagr = _number(daily[-1][1] ** (1 / years) - 1)
    except OverflowError:
        raise ValidationError("non_finite_number") from None
    return Series(tuple(daily), monthly, tuple(monthly_returns), tuple(drawdowns), cagr, min(drawdowns))


def validate_bundle(
    path: Path, recipe: Recipe, assignment: Assignment, expected_company_commit: str,
) -> ValidatedReplay:
    """Return data eligible for an equivalent-replay report; failures contain no results."""
    expected_names = {f"{candidate}/{name}" for candidate in CANDIDATES for name in OUTPUT_NAMES}
    _require(set(recipe.expected_outputs) == expected_names, "recipe_output_contract_mismatch")
    _require(assignment.recipe_id == recipe.id and assignment.manifest_digest == recipe_digest(recipe),
             "recipe_manifest_mismatch")
    _require(assignment.action != "cancel", "assignment_cancelled")
    contents, archive_digest = _archive(path, expected_names | METADATA_NAMES, assignment.lease_token)
    receipt = _receipt(contents["receipt.json"], recipe, assignment, expected_company_commit)
    for name, digest in recipe.expected_outputs.items():
        _require(_sha256(contents[name]) == digest, "economic_output_hash_mismatch")
    verification = _audit(contents, recipe, assignment.lease_token)
    parsed_json = {
        name: _json(contents[name], assignment.lease_token)
        for name in expected_names if name.endswith(".json")
    }
    for name, payload in parsed_json.items():
        if not name.endswith("/objective.json"):
            _require(isinstance(payload, list), "economic_json_schema_mismatch")
    candidates = {}
    common_dates = None
    for candidate in CANDIDATES:
        series = {
            cost: _series(contents[f"{candidate}/daily-{cost}.csv"], contents[f"{candidate}/monthly-{cost}.csv"])
            for cost in COSTS
        }
        for result in series.values():
            dates = tuple(day for day, _ in result.daily)
            if common_dates is None:
                common_dates = dates
            _require(dates == common_dates, "candidate_calendar_mismatch")
        objective = parsed_json[f"{candidate}/objective.json"]
        _require(isinstance(objective, dict), "objective_schema_mismatch")
        _require(set(objective) == {"metric", "unit", "value", "minimumHurdle", "riskConstraintResults"},
                 "objective_schema_mismatch")
        _require(objective["metric"] == "stress-net-absolute-cagr" and objective["unit"] == "fraction-per-year"
                 and objective["riskConstraintResults"] == [
                     {"constraint": "long-only", "within": True},
                     {"constraint": "gross-exposure-at-most-one", "within": True},
                     {"constraint": "no-leveraged-or-inverse-etfs", "within": True},
                 ], "objective_schema_mismatch")
        _require(type(objective["value"]) in (int, float) and type(objective["minimumHurdle"]) in (int, float),
                 "objective_schema_mismatch")
        _require(math.isclose(_number(objective["value"]), series["stress"].absolute_cagr,
                             rel_tol=0, abs_tol=1e-12), "objective_series_mismatch")
        candidates[candidate] = Candidate(series["base"], series["stress"], MappingProxyType(objective))
    return ValidatedReplay(
        recipe.model_copy(deep=True), receipt, MappingProxyType(contents), MappingProxyType(candidates),
        MappingProxyType(verification), archive_digest,
    )


def _escaped_json(value: Any) -> str:
    return html.escape(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def _curve(base: Series, stress: Series, *, drawdown: bool) -> str:
    title = "낙폭" if drawdown else "누적 자산 (시작자산 1)"
    values = [result.drawdowns if drawdown else tuple(equity for _, equity in result.daily)
              for result in (base, stress)]
    initial = 0.0 if drawdown else 1.0
    low = min(initial, *(min(sequence) for sequence in values))
    high = max(initial, *(max(sequence) for sequence in values))
    span = high - low or 1.0
    horizon = (base.daily[-1][0] - DEV_START).days
    paths = []
    for result, sequence, color in zip((base, stress), values, ("#2563eb", "#dc2626"), strict=True):
        points = [f"64,{30 + (high - initial) / span * 180:.2f}"]
        for (day, _), value in zip(result.daily, sequence, strict=True):
            x = 64 + (day - DEV_START).days / horizon * 660
            y = 30 + (high - value) / span * 180
            points.append(f"{x:.2f},{y:.2f}")
        paths.append(f'<polyline points="{" ".join(points)}" stroke="{color}" fill="none" stroke-width="1.6"/>')
    top = f"{high:.1%}" if drawdown else f"{high:.2f}"
    bottom = f"{low:.1%}" if drawdown else f"{low:.2f}"
    return (
        f'<svg viewBox="0 0 780 250" role="img" aria-label="{title}">'
        f'<title>{title} — base 10bp / stress 30bp</title>'
        '<path d="M64 25V210H724" fill="none" stroke="#94a3b8"/>'
        f'<text x="2" y="35">{top}</text><text x="2" y="210">{bottom}</text>'
        f'<text x="64" y="238">{DEV_START}</text>'
        f'<text x="634" y="238">{base.daily[-1][0]}</text>{"".join(paths)}</svg>'
    )


def build_report(validated: ValidatedReplay) -> dict[str, Any]:
    """Render only data returned by validate_bundle; this does not issue a research verdict."""
    if not isinstance(validated, ValidatedReplay):
        raise ValidationError("validated_bundle_required")
    recipe, receipt = validated.recipe, validated.receipt
    summary: dict[str, Any] = {
        "kind": "equivalent_replay", "development_only": True,
        "scientific_trials_added": 0, "scientific_exploration_consumed": 3,
        "scientific_exploration_budget": 3, "baseline_measured": False, "excess_return": None,
        "confirmation_claim": False, "live_claim": False,
        "return_basis": RETURN_BASIS, "cost_bp": {"base": 10, "stress": 30},
        "execution_receipt": receipt.model_dump(mode="json"),
        "archive_sha256": validated.archive_sha256,
        "audit": {"kind": "existing_independent_audit_reused", "path": recipe.audit_path,
                  "sha256": recipe.audit_sha256, "scope_digest": recipe.scope_digest,
                  "evidence_commit": recipe.evidence_commit},
        "candidates": {},
    }
    sections = []
    for name, candidate in validated.candidates.items():
        candidate_summary = {
            cost: {"absolute_cagr": getattr(candidate, cost).absolute_cagr,
                   "max_drawdown": getattr(candidate, cost).max_drawdown}
            for cost in COSTS
        }
        candidate_summary.update({
            "primary_objective": dict(candidate.objective),
            "first_session": candidate.base.daily[0][0].isoformat(),
            "last_session": candidate.base.daily[-1][0].isoformat(),
            "daily_rows": len(candidate.base.daily), "monthly_rows": len(candidate.base.monthly),
        })
        summary["candidates"][name] = candidate_summary
        month_rows = "".join(
            f"<tr><th>{day.isoformat()}</th><td>{base_return:.2%}</td><td>{stress_return:.2%}</td>"
            f"<td>{base_equity:.6f}</td><td>{stress_equity:.6f}</td></tr>"
            for ((day, base_equity), (_, stress_equity), base_return, stress_return) in zip(
                candidate.base.monthly, candidate.stress.monthly,
                candidate.base.monthly_returns, candidate.stress.monthly_returns, strict=True,
            )
        )
        sections.append(
            f'<section><h2>{html.escape(name)} — 고정 후보</h2>'
            '<table><caption>절대수익과 낙폭 · 단위 %</caption>'
            '<thead><tr><th>고정 비용</th><th>절대 CAGR</th><th>최대 낙폭</th></tr></thead><tbody>'
            f'<tr><th>base · 10bp</th><td>{candidate.base.absolute_cagr:.2%}</td>'
            f'<td>{candidate.base.max_drawdown:.2%}</td></tr>'
            f'<tr><th>stress · 30bp (primary)</th><td>{candidate.stress.absolute_cagr:.2%}</td>'
            f'<td>{candidate.stress.max_drawdown:.2%}</td></tr></tbody></table>'
            '<p class="legend">파랑: base 10bp · 빨강: stress 30bp</p>'
            f'<h3>자산곡선</h3>{_curve(candidate.base, candidate.stress, drawdown=False)}'
            f'<h3>낙폭</h3>{_curve(candidate.base, candidate.stress, drawdown=True)}'
            f'<h3>전체 월별 수익률 · {len(candidate.base.monthly)}개월</h3>'
            '<div class="scroll"><table><thead><tr><th>달력 월말</th><th>base 수익률</th>'
            '<th>stress 수익률</th><th>base 자산</th><th>stress 자산</th></tr></thead>'
            f'<tbody>{month_rows}</tbody></table></div></section>'
        )
    provenance = {
        "recipe_id": recipe.id, "manifest_digest": receipt.manifest_digest,
        "code_commit": recipe.code_commit, "company_commit": receipt.company_commit,
        "evidence_commit": recipe.evidence_commit, "lake_id": recipe.lake_id,
        "input_files": recipe.input_files, "config_files": recipe.config_files,
        "expected_outputs": recipe.expected_outputs, "archive_sha256": validated.archive_sha256,
        "audit_path": recipe.audit_path, "audit_sha256": recipe.audit_sha256,
        "audit_receipt_sha256": recipe.audit_receipt_sha256,
        "objective_digest": recipe.objective_digest, "scope_digest": recipe.scope_digest,
        "scope_files": recipe.scope_files,
    }
    document = (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>P11 고정 재실행 · 개발구간 보고서</title><style>'
        'body{font:16px/1.65 system-ui,sans-serif;max-width:1040px;margin:40px auto;padding:0 20px;'
        'color:#172033;background:#f8fafc}h1,h2,h3{line-height:1.3}section{background:white;'
        'padding:24px;margin:28px 0;border:1px solid #cbd5e1;border-radius:8px}'
        '.notice{border-left:5px solid #b45309;padding:16px;background:#fffbeb}'
        'table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}'
        'th,td{padding:8px 12px;border-bottom:1px solid #e2e8f0;text-align:right}'
        'th:first-child{text-align:left}caption{text-align:left;font-weight:600}'
        '.scroll{overflow:auto}svg{display:block;width:100%;height:auto;background:#fff}'
        'svg text{font:12px system-ui;fill:#334155}pre{white-space:pre-wrap;overflow-wrap:anywhere;'
        'font:12px/1.5 ui-monospace,monospace}details{margin:18px 0}.legend{font-size:14px}'
        '</style></head><body><header><p>P11 · equivalent_replay · development-only</p>'
        f'<h1>{html.escape(recipe.title)}</h1></header>'
        '<div class="notice"><strong>고정 P11 엔지니어링 재실행</strong>'
        '<p>개발구간의 기존 후보 3개를 동일 코드·입력·설정으로 재실행했습니다. '
        '반환된 모든 경제 산출물의 실제 바이트가 등록된 SHA-256과 일치했습니다. '
        '기존 독립 감사의 고정 범위와 재검증 영수증을 확인하여 그 근거를 재사용합니다. '
        '새로운 독립 감사 pass 판정은 발급하지 않습니다.</p>'
        '<p>추가 과학 시행 0회 · 기존 탐색 예산 3/3 사용. '
        '확증 통과·실거래·배치 승격을 주장하지 않습니다. 벤치마크는 미측정이며 초과수익은 제공하지 않습니다.</p></div>'
        f'<p>수익 기준: <code>{RETURN_BASIS}</code>. '
        'KRX 기준가격 조정 수익이며 현금 분배금을 별도 재투자한 총수익이 아닙니다. '
        '비용 가정은 base 10bp / stress 30bp로 고정되어 있습니다.</p>'
        '<p>CAGR = 마지막 자산^(1 / 연수) − 1, 연수 = '
        '(마지막 날짜 − 2014-01-01 + 1일) / 365.2425. 시작자산은 1입니다. '
        '낙폭은 일별 자산 / max(1, 누적 최고자산) − 1입니다. '
        '월 수익률은 달력 월말의 마지막 관측 자산 / 전월 자산 − 1이며 첫 달의 분모는 1입니다. '
        'CSV equity는 시작자산 대비 배수, cashWeight·grossWeight·turnover·cost는 NAV 비율, '
        'holdings는 종목 수입니다. objective value의 단위는 fraction-per-year입니다.</p>'
        f'{"".join(sections)}<section><h2>실행 영수증과 재현 근거</h2>'
        f'<details open><summary>실제 실행 영수증</summary><pre>{_escaped_json(receipt.model_dump(mode="json"))}</pre></details>'
        f'<details><summary>소스·입력·설정·감사 범위와 SHA-256</summary><pre>{_escaped_json(provenance)}</pre></details>'
        '<details><summary>기존 감사 재검증 영수증</summary>'
        f'<pre>{_escaped_json(dict(validated.audit_verification))}</pre></details></section>'
        '<footer><p>다음 단계: 재실행의 코드·입력·산출물 일치 여부를 검토합니다. '
        '새 전략 연구나 확증·배치는 각각 승인된 명세와 해당 범위의 독립 감사를 필요로 합니다.</p>'
        '</footer></body></html>'
    )
    return {"html": document, "summary": summary}

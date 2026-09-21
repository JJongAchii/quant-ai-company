"""Synthetic producer -> ZIP -> validator -> HTML checks; no research is executed."""

import calendar
import csv
import hashlib
import io
import json
import stat
import struct
import zipfile
from datetime import date
from uuid import uuid4

import pytest

from quant_company.research import report
from quant_company.research.contracts import Assignment, ExecutionReceipt, Recipe
from quant_company.research.recipes import recipe_digest
from quant_company.research.report import ValidationError, build_report, validate_bundle

COMPANY_COMMIT = "c" * 40


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode()


def sha(content):
    return hashlib.sha256(content).hexdigest()


def csv_bytes(header, rows):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(header)
    writer.writerows(rows)
    return output.getvalue().encode()


def producer(tmp_path, *, transform=None, title="고정된 P11 재실행", approval="event-approved"):
    """Pin an independent fixture producer's outputs before creating its execution receipt."""
    days = [date(2014, 1, 2), date(2014, 1, 31), date(2014, 2, 28), date(2014, 3, 28)]
    outputs = {}
    for index, name in enumerate(("m1", "m2", "m3")):
        for cost, offset in (("base", 0.02), ("stress", 0.0)):
            equities = [0.90, 0.95, 1.03 + offset, 0.98 + offset + index * 0.01]
            outputs[f"{name}/daily-{cost}.csv"] = csv_bytes(
                ["date", "equity", "cashWeight", "grossWeight", "turnover", "cost", "holdings"],
                [(day, equity, 0.1, 0.9, 0.01, 0.00003, 3) for day, equity in zip(days, equities, strict=True)],
            )
            outputs[f"{name}/monthly-{cost}.csv"] = csv_bytes(
                ["date", "equity"],
                [(date(day.year, day.month, calendar.monthrange(day.year, day.month)[1]), equity)
                 for day, equity in zip(days[1:], equities[1:], strict=True)],
            )
            for kind in ("orders", "memberships", "terminal-events"):
                outputs[f"{name}/{kind}-{cost}.json"] = encoded([])
        years = ((days[-1] - date(2014, 1, 1)).days + 1) / 365.2425
        outputs[f"{name}/objective.json"] = encoded({
            "metric": "stress-net-absolute-cagr", "unit": "fraction-per-year",
            "value": (0.98 + index * 0.01) ** (1 / years) - 1,
            "minimumHurdle": 0.0, "riskConstraintResults": [
                {"constraint": "long-only", "within": True},
                {"constraint": "gross-exposure-at-most-one", "within": True},
                {"constraint": "no-leveraged-or-inverse-etfs", "within": True},
            ],
        })
    if transform:
        transform(outputs)
    audit = b"# Synthetic prior audit\nverdict: pass\n"
    audit_receipt = encoded({
        "audit": "AUDIT.md", "audit_sha256": sha(audit), "scope_digest": "scope-digest",
        "verdict": "pass", "renderer": 1, "rendered_at": "2026-09-19T00:00:00Z",
        "html_sha256": "a" * 64, "artifact_url": None,
    })
    recipe = Recipe(
        id="kr-etf-p11-replay-v1", title=title, kind="equivalent_replay",
        code_commit="a" * 40, evidence_commit="b" * 40, lake_id="synthetic-fixture",
        config_files={"configs/approved.json": "d" * 64},
        input_files={"data/development.csv": "e" * 64},
        expected_outputs={name: sha(content) for name, content in outputs.items()},
        audit_path="runs/fixture/AUDIT.md", audit_sha256=sha(audit),
        audit_receipt_sha256=sha(audit_receipt), objective_digest="objective-digest",
        scope_digest="scope-digest", scope_files={"src/frozen.py": "f" * 64}, description="Synthetic fixture",
    )
    assignment = Assignment(
        job_id=uuid4(), project_id=uuid4(), revision=7, recipe_id=recipe.id,
        manifest_digest=recipe_digest(recipe), approval_event_id=approval,
        lease_token="9" * 64, action="run",
    )
    receipt = ExecutionReceipt(
        **{key: value for key, value in assignment.model_dump().items() if key not in ("lease_token", "action")},
        worker_id=recipe.worker_id, hostname=recipe.hostname, gpu=recipe.gpu,
        code_commit=recipe.code_commit, company_commit=COMPANY_COMMIT,
        input_files=recipe.input_files, config_files=recipe.config_files, output_files=recipe.expected_outputs,
        started_at="2026-09-21T02:00:00+00:00", completed_at="2026-09-21T02:01:00Z",
        execution_count=1, qualification_passed=True, sealed_read=False, scientific_trials_added=0,
    )
    contents = {
        **outputs, "receipt.json": receipt.model_dump_json().encode(), "audit.md": audit,
        "audit.receipt.json": audit_receipt,
        "audit-verification.json": encoded({
            "schema_version": 1, "api": "qlab.audits.record.validate_audit+qlab.audits.receipt.check_receipt",
            "evidence_commit": recipe.evidence_commit, "audit_sha256": recipe.audit_sha256,
            "audit_receipt_sha256": recipe.audit_receipt_sha256, "objective_digest": recipe.objective_digest,
            "scope_digest": recipe.scope_digest, "scope_files": recipe.scope_files,
            "verdict": "pass", "violations": [], "receipt_violations": [],
        }),
    }
    path = tmp_path / "returned.zip"
    write_archive(path, contents)
    return path, recipe, assignment, contents


def write_archive(path, contents, **kwargs):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in contents.items():
            if kwargs.get("symlink") == name:
                name = zipfile.ZipInfo(name)
                name.create_system = 3
                name.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(name, content)
        if kwargs.get("duplicate"):
            with pytest.warns(UserWarning, match="Duplicate name"):
                archive.writestr(kwargs["duplicate"], b"duplicate")


def mutate_json(contents, name, updates):
    payload = json.loads(contents[name])
    payload.update(updates)
    contents[name] = encoded(payload)


def test_producer_archive_to_typed_data_html_and_serializable_summary(tmp_path):
    path, recipe, assignment, contents = producer(tmp_path)
    validated = validate_bundle(path, recipe, assignment, COMPANY_COMMIT)
    result = build_report(validated)
    summary, document = result["summary"], result["html"]
    assert len(validated.outputs) == 37
    assert dict(validated.outputs) == contents
    assert validated.receipt.job_id == assignment.job_id
    assert summary["kind"] == "equivalent_replay"
    assert summary["scientific_trials_added"] == 0
    assert summary["scientific_exploration_consumed"] == summary["scientific_exploration_budget"] == 3
    assert summary["baseline_measured"] is False and summary["excess_return"] is None
    assert summary["return_basis"] == "krx_reference_price_adjusted_return"
    assert summary["cost_bp"] == {"base": 10, "stress": 30}
    assert summary["execution_receipt"]["company_commit"] == COMPANY_COMMIT
    assert summary["audit"]["kind"] == "existing_independent_audit_reused"
    json.dumps(summary, allow_nan=False)
    assert document.startswith('<!doctype html><html lang="ko">')
    assert "development-only" in document and "추가 과학 시행 0회" in document
    assert "새로운 독립 감사 pass 판정은 발급하지 않습니다" in document
    assert "현금 분배금을 별도 재투자한 총수익이 아닙니다" in document
    assert "벤치마크는 미측정" in document
    assert document.count("<svg ") == 6
    assert document.count("<th>2014-03-31</th>") == 3
    assert document.count("<th>2014-02-28</th>") == 3
    assert document.count("<th>2014-01-31</th>") == 3
    assert "https://" not in document and "<script" not in document
    assert assignment.lease_token not in document
    assert "lease_token" not in json.dumps(summary)
    with pytest.raises(TypeError):
        validated.outputs["receipt.json"] = b"replacement"


def test_returns_use_initial_capital_inclusive_frozen_year_basis_and_monthend_calendar(tmp_path):
    path, recipe, assignment, _ = producer(tmp_path)
    validated = validate_bundle(path, recipe, assignment, COMPANY_COMMIT)
    series = validated.candidates["m1"].stress
    # Independent arithmetic: 87 inclusive days and initial equity 1, not first observed 0.90.
    expected_cagr = 0.98 ** (365.2425 / 87) - 1
    assert series.absolute_cagr == pytest.approx(expected_cagr, abs=1e-14)
    assert series.max_drawdown == pytest.approx(-0.10)
    assert series.drawdowns[0] == pytest.approx(-0.10)
    assert series.monthly_returns == pytest.approx([0.95 - 1, 1.03 / 0.95 - 1, 0.98 / 1.03 - 1])
    assert series.daily[-1][0] == date(2014, 3, 28)
    assert series.monthly[-1][0] == date(2014, 3, 31)
    assert "-5.00%" in build_report(validated)["html"]


def test_html_escapes_all_worker_controlled_receipt_text_and_trusted_titles(tmp_path):
    attack = '</pre><script src="https://evil.invalid/a">alert(1)</script><img onerror="bad">'
    path, recipe, assignment, _ = producer(tmp_path, title=attack, approval=attack)
    document = build_report(validate_bundle(path, recipe, assignment, COMPANY_COMMIT))["html"]
    assert "<script" not in document and "<img" not in document
    assert "&lt;script" in document and "&lt;/pre&gt;" in document
    assert "src=&quot;" in document


@pytest.mark.parametrize("field,value", [
    ("job_id", str(uuid4())), ("project_id", str(uuid4())), ("revision", 8),
    ("approval_event_id", "some-other-approval"), ("manifest_digest", "0" * 64),
    ("code_commit", "0" * 40), ("company_commit", "0" * 40),
    ("worker_id", "worker5090"), ("hostname", "unknown-host"), ("gpu", "other-gpu"),
    ("input_files", {}), ("config_files", {}), ("output_files", {}),
    ("qualification_passed", False), ("execution_count", 2), ("scientific_trials_added", 1),
    ("sealed_read", True), ("sealed_read", 0), ("execution_count", True), ("revision", "7"),
    ("started_at", "2026-09-21"), ("started_at", "2026-09-21T01:00:00"),
    ("started_at", "2026-09-21T03:00:00Z"), ("completed_at", "not a date"),
])
def test_untrusted_execution_claims_cannot_replace_identity_or_qualification(tmp_path, field, value):
    path, recipe, assignment, contents = producer(tmp_path)
    mutate_json(contents, "receipt.json", {field: value})
    write_archive(path, contents)
    with pytest.raises(ValidationError):
        validate_bundle(path, recipe, assignment, COMPANY_COMMIT)


def test_manifest_binds_every_recipe_field(tmp_path):
    path, recipe, assignment, _ = producer(tmp_path)
    changed = recipe.model_copy(update={"lake_id": "changed-lake"})
    with pytest.raises(ValidationError, match="recipe_manifest_mismatch"):
        validate_bundle(path, changed, assignment, COMPANY_COMMIT)
    with pytest.raises(ValidationError, match="assignment_cancelled"):
        validate_bundle(path, recipe, assignment.model_copy(update={"action": "cancel"}), COMPANY_COMMIT)
    changed = recipe.model_copy(update={"expected_outputs": {}})
    with pytest.raises(ValidationError, match="recipe_output_contract_mismatch"):
        validate_bundle(path, changed, assignment, COMPANY_COMMIT)


@pytest.mark.parametrize("field,value", [
    ("schema_version", 2), ("schema_version", True), ("api", "worker-says-pass"),
    ("evidence_commit", "0" * 40), ("audit_sha256", "0" * 64),
    ("audit_receipt_sha256", "0" * 64), ("objective_digest", "other"),
    ("scope_digest", "other"), ("scope_files", {}), ("verdict", "unverified"),
    ("violations", ["failure"]), ("receipt_violations", ["failure"]), ("unexpected", True),
])
def test_audit_revalidation_cannot_be_replaced_by_a_worker_boolean(tmp_path, field, value):
    path, recipe, assignment, contents = producer(tmp_path)
    mutate_json(contents, "audit-verification.json", {field: value})
    write_archive(path, contents)
    with pytest.raises(ValidationError, match="audit_revalidation_mismatch"):
        validate_bundle(path, recipe, assignment, COMPANY_COMMIT)


@pytest.mark.parametrize("name", ["audit.md", "audit.receipt.json", "m2/orders-base.json", "m3/daily-stress.csv"])
def test_actual_byte_hashes_override_unchanged_worker_receipt_claims(tmp_path, name):
    path, recipe, assignment, contents = producer(tmp_path)
    contents[name] += b" "
    write_archive(path, contents)
    with pytest.raises(ValidationError, match="hash_mismatch"):
        validate_bundle(path, recipe, assignment, COMPANY_COMMIT)


def test_audit_receipt_identity_is_checked_even_when_pinned(tmp_path):
    path, recipe, assignment, contents = producer(tmp_path)
    mutate_json(contents, "audit.receipt.json", {"verdict": "fail"})
    recipe = recipe.model_copy(update={"audit_receipt_sha256": sha(contents["audit.receipt.json"])})
    assignment = assignment.model_copy(update={"manifest_digest": recipe_digest(recipe)})
    mutate_json(contents, "receipt.json", {"manifest_digest": assignment.manifest_digest})
    mutate_json(contents, "audit-verification.json", {"audit_receipt_sha256": recipe.audit_receipt_sha256})
    write_archive(path, contents)
    with pytest.raises(ValidationError, match="audit_receipt_identity_mismatch"):
        validate_bundle(path, recipe, assignment, COMPANY_COMMIT)


@pytest.mark.parametrize("kind", ["missing", "extra", "duplicate", "symlink", "traversal", "absolute", "backslash", "directory"])
def test_zip_envelope_rejects_unregistered_or_unsafe_members(tmp_path, kind):
    path, recipe, assignment, contents = producer(tmp_path)
    options = {}
    if kind == "missing":
        del contents["audit.md"]
    elif kind == "extra":
        contents["extra.txt"] = b"extra"
    elif kind in ("duplicate", "symlink"):
        options[kind] = "receipt.json"
    else:
        name = {"traversal": "../receipt.json", "absolute": "/receipt.json",
                "backslash": "m1\\receipt.json", "directory": "m1/"}[kind]
        contents[name] = contents.pop("receipt.json")
    write_archive(path, contents, **options)
    with pytest.raises(ValidationError):
        validate_bundle(path, recipe, assignment, COMPANY_COMMIT)


def test_zip_encryption_flag_is_rejected_before_reading(tmp_path):
    path, recipe, assignment, _ = producer(tmp_path)
    data = bytearray(path.read_bytes())
    offset = 0
    while True:
        offset = data.find(b"PK\x01\x02", offset)
        if offset < 0:
            break
        flags = struct.unpack_from("<H", data, offset + 8)[0]
        struct.pack_into("<H", data, offset + 8, flags | 1)
        offset += 4
    path.write_bytes(data)
    with pytest.raises(ValidationError, match="encrypted_member"):
        validate_bundle(path, recipe, assignment, COMPANY_COMMIT)


@pytest.mark.parametrize("limit,expected", [
    ("MAX_ARCHIVE_BYTES", "archive_too_large"),
    ("MAX_MEMBER_BYTES", "member_too_large"),
    ("MAX_EXPANDED_BYTES", "expanded_archive_too_large"),
])
def test_zip_size_limits_fail_closed_without_allocating_large_fixtures(tmp_path, monkeypatch, limit, expected):
    path, recipe, assignment, _ = producer(tmp_path)
    monkeypatch.setattr(report, limit, 50)
    with pytest.raises(ValidationError, match=expected):
        validate_bundle(path, recipe, assignment, COMPANY_COMMIT)


def test_nested_zip_disguised_as_an_expected_output_is_rejected(tmp_path):
    nested = io.BytesIO()
    with zipfile.ZipFile(nested, "w") as archive:
        archive.writestr("payload", "nested")
    path, recipe, assignment, _ = producer(
        tmp_path, transform=lambda outputs: outputs.update({"m1/orders-base.json": nested.getvalue()}),
    )
    with pytest.raises(ValidationError, match="nested_archive"):
        validate_bundle(path, recipe, assignment, COMPANY_COMMIT)


@pytest.mark.parametrize("payload", [
    b'{"duplicate":1,"duplicate":2}', b'{"value":NaN}', b'{"value":Infinity}',
    b'{"value":1e999}', b'{"broken":}', b'\xff',
])
def test_malformed_or_nonfinite_json_is_rejected_even_if_its_bytes_are_pinned(tmp_path, payload):
    path, recipe, assignment, _ = producer(
        tmp_path, transform=lambda outputs: outputs.update({"m1/orders-base.json": payload}),
    )
    with pytest.raises(ValidationError):
        validate_bundle(path, recipe, assignment, COMPANY_COMMIT)


@pytest.mark.parametrize("payload", [b'"' + b"9" * 64 + b'"', b'"' + b"\\u0039" * 64 + b'"', b'{"lease_token":"redacted"}'])
def test_lease_secret_is_never_accepted_in_any_artifact(tmp_path, payload):
    path, recipe, assignment, _ = producer(
        tmp_path, transform=lambda outputs: outputs.update({"m1/orders-base.json": payload}),
    )
    with pytest.raises(ValidationError, match="lease_secret_in_artifact"):
        validate_bundle(path, recipe, assignment, COMPANY_COMMIT)


@pytest.mark.parametrize("old,new", [
    (b"equity", b"netAssetValue"), (b"0.9,", b"NaN,"), (b"0.9,", b"inf,"),
    (b"0.9,", b"1e999,"), (b"0.9,", b"-0.9,"),
    (b"2014-01-02", b"2014-01-02T00:00:00Z"), (b"2014-01-02", b"20140102"),
    (b"2014-01-31", b"2014-01-02"), (b"2014-01-02", b"2014-02-30"),
    (b",3\r\n", b",3.5\r\n"), (b",3\r\n", b",-1\r\n"),
    (b",3\r\n", b"\r\n"), (b",3\r\n", b",3,extra\r\n"),
])
def test_csv_types_field_names_finiteness_and_dates_are_checked_before_reporting(tmp_path, old, new):
    def transform(outputs):
        outputs["m1/daily-base.csv"] = outputs["m1/daily-base.csv"].replace(old, new, 1)

    path, recipe, assignment, _ = producer(tmp_path, transform=transform)
    with pytest.raises(ValidationError):
        validate_bundle(path, recipe, assignment, COMPANY_COMMIT)


@pytest.mark.parametrize("kind", ["empty", "missing", "duplicate", "trading_date", "wrong_equity", "month_gap"])
def test_monthly_rows_must_be_complete_calendar_monthends_reconciled_to_daily(tmp_path, kind):
    def transform(outputs):
        lines = outputs["m1/monthly-base.csv"].splitlines(keepends=True)
        if kind == "empty":
            lines = lines[:1]
        elif kind == "missing":
            del lines[2]
        elif kind == "duplicate":
            lines[2] = lines[1]
        elif kind == "trading_date":
            lines[-1] = lines[-1].replace(b"2014-03-31", b"2014-03-28")
        elif kind == "wrong_equity":
            lines[-1] = b"2014-03-31,9999\r\n"
        else:
            lines[-1] = lines[-1].replace(b"2014-03-31", b"2014-04-30")
            outputs["m1/daily-base.csv"] = outputs["m1/daily-base.csv"].replace(b"2014-03-28", b"2014-04-28")
        outputs["m1/monthly-base.csv"] = b"".join(lines)

    path, recipe, assignment, _ = producer(tmp_path, transform=transform)
    with pytest.raises(ValidationError):
        validate_bundle(path, recipe, assignment, COMPANY_COMMIT)


@pytest.mark.parametrize("updates", [
    {"unit": "percent"}, {"metric": "excess-cagr"}, {"value": 12.0},
    {"value": "0.1"}, {"value": True}, {"riskConstraintResults": {}},
    {"riskConstraintResults": []}, {"extra": 1},
])
def test_primary_objective_unit_schema_and_value_match_verified_stress_series(tmp_path, updates):
    path, recipe, assignment, _ = producer(
        tmp_path, transform=lambda outputs: mutate_json(outputs, "m1/objective.json", updates),
    )
    with pytest.raises(ValidationError):
        validate_bundle(path, recipe, assignment, COMPANY_COMMIT)


def test_invalid_archive_and_errors_do_not_expose_worker_text_or_metrics(tmp_path):
    path, recipe, assignment, _ = producer(tmp_path)
    attack = "SECRET-UNVERIFIED-PERFORMANCE=99.9"
    path.write_bytes(attack.encode())
    with pytest.raises(ValidationError) as raised:
        validate_bundle(path, recipe, assignment, COMPANY_COMMIT)
    assert str(raised.value) == "Replay validation failed: invalid_archive"
    assert attack not in str(raised.value)
    assert not hasattr(raised.value, "metrics")
    with pytest.raises(ValidationError, match="validated_bundle_required"):
        build_report({"unverified": "data"})


def test_embedded_null_member_name_cannot_alias_an_allowed_path(tmp_path):
    path, recipe, assignment, contents = producer(tmp_path)
    contents["receipt.jsonX"] = contents.pop("receipt.json")
    write_archive(path, contents)
    path.write_bytes(path.read_bytes().replace(b"receipt.jsonX", b"receipt.json\x00"))
    with pytest.raises(ValidationError, match="unsafe_member_path"):
        validate_bundle(path, recipe, assignment, COMPANY_COMMIT)


def test_corrupt_deflate_stream_is_a_sanitized_validation_error(tmp_path):
    path, recipe, assignment, _ = producer(tmp_path)
    data = bytearray(path.read_bytes())
    name_length, extra_length = struct.unpack_from("<HH", data, 26)
    data_start = 30 + name_length + extra_length
    data[data_start:data_start + 8] = b"\xff" * 8
    path.write_bytes(data)
    with pytest.raises(ValidationError, match="invalid_archive"):
        validate_bundle(path, recipe, assignment, COMPANY_COMMIT)


@pytest.mark.parametrize("payload", [b"null", b"{}", b"123", b"true", b'"text"'])
def test_economic_json_root_must_be_a_record_list(tmp_path, payload):
    path, recipe, assignment, _ = producer(
        tmp_path, transform=lambda outputs: outputs.update({"m1/orders-base.json": payload}),
    )
    with pytest.raises(ValidationError, match="economic_json_schema_mismatch"):
        validate_bundle(path, recipe, assignment, COMPANY_COMMIT)

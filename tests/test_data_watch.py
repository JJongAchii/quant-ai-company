"""Real PostgreSQL; bounded synthetic lake and Slack receipts. No production Slack writes."""

import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from pydantic import SecretStr

from quant_company.api import create_app
from quant_company.company import Company, PolicyError, load_roles
from quant_company.config import Settings
from quant_company.data_watch.checker import check, errors_for
from quant_company.data_watch.contracts import CheckAssignment, CheckReceipt, FreshnessContract, freshness
from quant_company.data_watch.core import CoreChecks
from quant_company.data_watch.reporting import list_text, status_text
from quant_company.data_watch.runner import DataWatchRunner
from quant_company.data_watch.store import DataWatchStore
from quant_company.research.recipes import load_recipe, recipe_digest
from quant_company.research.worker import sha_file
from quant_company.slack import SlackIngress, SlackOutbox


@pytest.fixture
def watch(company, monkeypatch):
    company.settings.data_watch_enabled = True
    company.settings.data_watch_publish_enabled = True
    company.settings.data_watch_channel_id = "CDATA"
    company.settings.data_watch_owner_user = "UHUMAN"
    company.settings.slack_allowed_channels.append("CDATA")
    company.settings.company_lake_uri = "s3://example/qdata"
    company.roles["data"].tools.append("data_watch_status")
    clock = [datetime(2026, 9, 22, 0, 0, tzinfo=UTC)]
    for module in ("store", "core", "reporting"):
        monkeypatch.setattr(f"quant_company.data_watch.{module}.utcnow", lambda: clock[0])
    store = DataWatchStore(company)
    return SimpleNamespace(company=company, store=store, clock=clock)


def catalog(watch, count=1, etag="v1"):
    return {"ok": True, "data": {"observed_at": watch.clock[0].isoformat(), "method": "object_metadata_only",
        "datasets": [{"dataset": "krx_etf" if n == 0 else f"dataset_{n}", "uri": f"s3://example/qdata/clean/data_{n}.parquet",
                      "size_bytes": 123, "last_modified": "2026-09-21T10:00:00Z", "etag": etag, "version_id": None}
                     for n in range(count)]}}


def descriptor(item, *, maximum="2026-09-21T00:00:00", rows=3):
    return {"ok": True, "qdata_code_commit": "a" * 40, "data": {"dataset": item["dataset"],
        "source": {k: v for k, v in item.items() if k != "dataset"}, "row_count": rows,
        "method": "parquet_footer_statistics", "sample": None,
        "columns": [{"name": "date", "type": "timestamp[ms]"}],
        "date_bounds": {"date": {"statistics_complete": True, "min": "2012-01-02", "max": maximum}}}}


async def tick(watch, data=None, *, fail=False, reads=None):
    data = data or catalog(watch)

    def query(root, name, args):
        if reads is not None:
            reads.append((name, args))
        if name == "lake_catalog":
            return data
        item = next(item for item in data["data"]["datasets"] if item["dataset"] == args["dataset"])
        return {"ok": False, "error": "fixture"} if fail else descriptor(item)

    return await DataWatchRunner(watch.company, query).tick()


def rows(watch, table):
    assert table in {"data_watch_incidents", "data_watch_inventory", "data_watch_checks", "outbox", "turns"}
    with watch.company.db.transaction() as conn:
        return conn.execute(f"SELECT * FROM {table} ORDER BY created_at,id").fetchall()


def delivery(watch, credentials, *, ambiguous=False):
    calls = []

    def send(request):
        calls.append(json.loads(request.content))
        if ambiguous:
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, json={"ok": True, "ts": f"80.{len(calls):06d}"})

    return SlackOutbox(watch.company, credentials, httpx.MockTransport(send)), calls


async def drain(box):
    for _ in range(25):
        if not await box.send_one():
            return
    pytest.fail("outbox did not drain")


def test_settings_and_employee_identity():
    with pytest.raises(ValueError, match="dedicated allowed"):
        Settings(data_watch_enabled=True)
    settings = Settings(data_watch_enabled=True, data_watch_channel_id="CDATA", data_watch_owner_user="UOWNER",
                        slack_allowed_channels=["CDATA"], slack_allowed_users=["UOWNER"])
    roles = load_roles(settings)
    assert roles["data"].active and "data_watch_status" in roles["data"].tools
    assert not roles["researcher_global"].active


async def test_catalog_every_30_minutes_and_only_changed_descriptors_with_bounded_batches(watch):
    reads, data = [], catalog(watch, 6)
    await tick(watch, data, reads=reads)
    assert len(reads) == 5 and sum(n == "lake_describe" for n, _ in reads) == 4
    await tick(watch, data, reads=reads)
    assert len(reads) == 7
    await tick(watch, data, reads=reads)
    assert len(reads) == 7
    watch.clock[0] += timedelta(minutes=30)
    await tick(watch, data, reads=reads)
    assert len(reads) == 8
    watch.clock[0] += timedelta(minutes=30)
    data["data"]["datasets"][1]["etag"] = "changed"
    await tick(watch, data, reads=reads)
    assert reads[-1] == ("lake_describe", {"dataset": "dataset_1"}) and len(reads) == 10
    assert len(rows(watch, "outbox")) == 1 and not rows(watch, "turns")
    state = watch.store.status()
    assert all(d["freshness"]["state"] == "unregistered" for d in state["datasets"])
    assert state["datasets"][0]["source"]["last_modified"] != state["datasets"][0]["date_bounds"]["date"]["max"]


async def test_inventory_failure_preserves_previous_presence_and_changed_object_cannot_commit_old_footer(watch):
    await tick(watch)
    watch.clock[0] += timedelta(minutes=30)
    await tick(watch, {"ok": False, "error": "private provider detail"})
    failed = watch.store.status()
    assert failed["datasets"][0]["inspection"] == "metadata_checked"
    failed_text = status_text(failed)
    assert "현재 목록 조회 실패" in failed_text
    assert "아래 날짜와 숫자는 09/22 09:00 KST 마지막 성공 기록 기준" in failed_text
    assert "한국시장: ETF 2026-09-21" in failed_text
    assert "이번 목록 조회 실패" in list_text(failed)
    assert rows(watch, "data_watch_incidents")[0]["state"] == "active"
    watch.clock[0] += timedelta(minutes=30)
    item = catalog(watch, etag="new")
    claim = watch.store.claim_inventory()
    watch.store.save_inventory(claim, item)
    desc = watch.store.claim_descriptions()[0]
    watch.clock[0] += timedelta(minutes=30)
    latest = catalog(watch, etag="newer")
    watch.store.save_inventory(watch.store.claim_inventory(), latest)
    assert not watch.store.save_description(desc, descriptor(item["data"]["datasets"][0]))
    assert watch.store.status()["datasets"][0]["inspection"] == "unchecked"


async def test_descriptor_identity_and_whole_catalog_validation_fail_closed(watch):
    claim = watch.store.claim_inventory()
    data = catalog(watch)
    data["data"]["datasets"] *= 2
    assert watch.store.save_inventory(claim, data)
    assert not watch.store.status()["datasets"]
    watch.clock[0] += timedelta(minutes=30)
    watch.store.save_inventory(watch.store.claim_inventory(), catalog(watch))
    claim = watch.store.claim_descriptions()[0]
    receipt = descriptor(catalog(watch, etag="other")["data"]["datasets"][0])
    watch.store.save_description(claim, receipt)
    assert watch.store.status()["datasets"][0]["problem"] == "descriptor_unavailable_or_changed"


def test_known_qdata_footer_budget_is_reported_as_an_inspection_limit(watch):
    watch.store.save_inventory(watch.store.claim_inventory(), catalog(watch))
    claim = watch.store.claim_descriptions()[0]
    watch.store.save_description(claim, {"ok": False, "error": "inspection_limit_or_invalid_request",
        "detail": "Parquet footer exceeds the 2 MiB inspection budget"})
    snapshot = watch.store.status()
    assert snapshot["datasets"][0]["inspection"] == "unchecked"
    assert snapshot["datasets"][0]["problem"] == "parquet_footer_limit"
    with watch.company.db.transaction() as conn:
        saved = conn.execute("SELECT receipt FROM data_watch_descriptions WHERE id=%s",
                             (claim["lease_token"],)).fetchone()["receipt"]
    assert saved == {"ok": False, "error": "parquet_footer_limit"}
    watch.store.report()
    incident = next(row["text"] for row in rows(watch, "outbox") if "점검 필요" in row["text"])
    assert "2 MiB 검사 한도" in incident
    assert "원본 데이터 손상" in incident
    assert "descriptor_unavailable_or_changed" not in incident
    assert "version:" not in incident


def test_daily_summary_shows_actual_source_dates_and_the_right_date_axis():
    from quant_company.data_watch.coverage import dataset_date, observed

    def row(name, column, maximum, *, problem=None, other=None):
        bounds = {column: {"statistics_complete": True, "max": maximum}, **(other or {})}
        return {"dataset": name, "date_bounds": bounds, "source": {"last_modified": "2026-09-25T11:00:00Z"},
                "inspection": "unchecked" if problem else "metadata_checked",
                "freshness": {"state": "unregistered"}, "problem": problem}

    data = [row("krx_prices", "date", "2026-09-23"),
            row("prices", "date", "2026-09-24"),
            row("us_shortvol", "date", "2026-09-24"),
            row("sec_fundamental", "filed", "2026-03-31", other={
                "ddate": {"statistics_complete": True, "max": "2215-09-30"}}),
            row("sec_13f", "filed", "2026-05-29"),
            row("sec_insider", "filed", "2026-03-31"),
            row("us_dividends", "asof", "2026-09-28", other={
                "ex_date": {"statistics_complete": True, "max": "2030-12-13"}}),
            row("us_prices", "date", None, problem="parquet_footer_limit")]
    snapshot = {"checked_at": "2026-09-28T04:51:00+00:00",
                "next_inventory_at": "2026-09-28T05:00:00+00:00",
                "inventory": {"receipt": {"ok": True}, "checked_at": "2026-09-28T04:30:00+00:00"},
                "datasets": data, "core_checks": []}
    summary = status_text(snapshot)
    listing = list_text(snapshot)
    assert len(summary) < 3000 and len(listing) < 3000
    assert "공개분 미반영 5건 · 날짜 미확인 1건" in summary
    assert "한국시장: 주식 2026-09-23" in summary
    assert "09/24~27 추석 휴장" in summary
    assert ("미국 일별: 미국 ETF·주가 2026-09-24, 미국 전종목 미확인, "
            "FINRA 공매도량 2026-09-24") in summary
    assert "sec_fundamental: 제출일 2026-03-31" in summary
    assert "us_prices: 거래일 미확인" in summary and "2 MiB" in summary
    assert "FINRA가 9/25분을 공개한 뒤에도" in summary
    assert "2026 Q2 공개본이 레이크" in summary
    assert "SEC 2026년 6~8월 13F 공개본이 레이크" in summary
    assert "SEC 분기 Form 3·4·5의 2026 Q2 공개본이 레이크" in summary
    assert "09/28 13:30 KST 성공" in summary
    assert "파일 교체 2026-09-25" in listing and "sec_fundamental 제출일 2026-03-31" in listing
    assert dataset_date(data[3]) == date(2026, 3, 31)
    assert dataset_date(data[6]) == date(2026, 9, 28)
    assert observed(data[0], snapshot["checked_at"])["state"] == "observed"
    assert observed(data[1], snapshot["checked_at"])["state"] == "source_gap"
    assert observed(data[3], snapshot["checked_at"])["state"] == "source_gap"
    assert observed(data[4], snapshot["checked_at"])["state"] == "source_gap"
    assert observed(data[5], snapshot["checked_at"])["state"] == "source_gap"
    assert observed(data[0], "2026-09-28T11:01:00+00:00")["state"] == "source_gap"


async def test_claims_survive_restart_and_stale_lease_is_rejected(watch):
    first = watch.store.claim_inventory()
    restarted = DataWatchStore(Company(watch.company.settings, watch.company.roles))
    assert restarted.claim_inventory() is None
    watch.clock[0] += timedelta(minutes=3)
    next_claim = restarted.claim_inventory()
    assert next_claim["id"] == first["id"] and next_claim["lease_token"] != first["lease_token"]
    assert not watch.store.save_inventory(first, catalog(watch))
    assert restarted.save_inventory(next_claim, catalog(watch))


async def test_incident_dedup_recovery_thread_and_ambiguous_root(watch, credentials):
    await tick(watch, fail=True)
    box, calls = delivery(watch, credentials, ambiguous=True)
    await drain(box)
    assert len(calls) == 2  # one issue, one daily summary
    watch.clock[0] += timedelta(minutes=30)
    await tick(watch)
    await drain(box)
    assert len(calls) == 2 and rows(watch, "data_watch_incidents")[0]["state"] == "recovered"
    assert all(row["status"] == "uncertain" for row in rows(watch, "outbox"))
    assert "미확인" in calls[0]["text"]


async def test_recovery_and_recurrence_use_same_receipted_thread_and_revocation_blocks(watch, credentials):
    await tick(watch, fail=True)
    box, calls = delivery(watch, credentials)
    await drain(box)
    watch.clock[0] += timedelta(minutes=30)
    await tick(watch)
    await drain(box)
    assert len(calls) == 3 and calls[2]["thread_ts"] == calls[0].get("ts", "80.000001")
    await tick(watch)
    assert len(rows(watch, "outbox")) == 3
    watch.clock[0] += timedelta(minutes=30)
    await tick(watch, catalog(watch, etag="v2"), fail=True)
    assert len(rows(watch, "data_watch_incidents")) == 1
    watch.company.settings.slack_allowed_users.clear()
    await drain(box)
    assert len(calls) == 3 and rows(watch, "outbox")[-1]["status"] == "stale"


async def test_daily_schedule_uses_kst_without_backfill(watch):
    watch.clock[0] = datetime(2026, 9, 21, 23, 59, tzinfo=UTC)
    await tick(watch)
    assert not rows(watch, "outbox")
    watch.clock[0] += timedelta(minutes=1)
    await tick(watch)
    await tick(watch)
    assert len(rows(watch, "outbox")) == 1
    watch.clock[0] += timedelta(days=4)
    await tick(watch)
    assert len(rows(watch, "outbox")) == 2


def test_explicit_publication_calendar_distinguishes_holiday_expiry_and_incomplete_stats():
    contract = FreshnessContract.model_validate({"dataset": "krx_etf", "date_column": "date",
        "evidence": "Synthetic fixture calendar; not an actual exchange calendar", "valid_until": "2026-09-23T12:00:00Z",
        "dates": [{"data_date": "2026-09-18", "expected_after": "2026-09-18T09:00:00Z"},
                  {"data_date": "2026-09-22", "expected_after": "2026-09-22T09:00:00Z"}]})
    desc = {"data": {"date_bounds": {"date": {"statistics_complete": True, "max": "2026-09-18"}}}}
    assert freshness(contract, desc, datetime(2026, 9, 22, 0, tzinfo=UTC))["state"] == "fresh"
    assert freshness(contract, desc, datetime(2026, 9, 22, 10, tzinfo=UTC))["state"] == "stale"
    assert freshness(contract, desc, datetime(2026, 9, 24, 0, tzinfo=UTC))["state"] == "unregistered"
    assert freshness(contract, {}, datetime(2026, 9, 22, 10, tzinfo=UTC))["state"] == "unchecked"


async def test_data_is_channel_lead_and_status_does_not_invoke_model(watch, credentials):
    ingress = SlackIngress(watch.company.settings, watch.company, credentials)
    payload = {"team_id": "TTEST", "api_app_id": credentials["data"]["app_id"], "event_id": "E1",
        "event": {"type": "message", "channel": "CDATA", "user": "UHUMAN", "ts": "12.1", "text": "상태"}}
    first = ingress.accept("data", payload, credentials["data"])
    assert ingress.accept("data", payload, credentials["data"])["duplicate"]
    payload["api_app_id"] = credentials["director"]["app_id"]
    assert ingress.accept("director", payload, credentials["director"])["ignored"]
    assert not rows(watch, "turns")
    assert "첫 목록 조회 대기" in watch.company.project_state(first["project_id"])["tasks"][0]["result"]
    await tick(watch)
    payload["api_app_id"] = credentials["data"]["app_id"]
    payload["event"]["ts"] = "12.2"
    payload["event"]["text"] = "목록"
    listed = ingress.accept("data", payload, credentials["data"])
    assert any("krx_etf" in task["result"] for task in watch.company.project_state(listed["project_id"])["tasks"])
    assert not rows(watch, "turns")


async def test_oversized_data_watch_publication_fails_closed(watch, credentials):
    await tick(watch)
    with watch.company.db.transaction() as conn:
        conn.execute("UPDATE outbox SET text=%s WHERE agent='data'", ("x" * 3001,))
    box, calls = delivery(watch, credentials)
    await drain(box)
    assert not calls
    assert rows(watch, "outbox")[0]["status"] == "stale"


@pytest.fixture
def core(watch, tmp_path, monkeypatch):
    recipe = load_recipe()
    root, scratch = tmp_path / "inputs", tmp_path / "scratch"
    scratch.mkdir()
    hashes = {}
    for name in recipe.input_files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        year = 2012 if "/warmup/" in name else 2014
        if name.endswith(".json"):
            path.write_text('{}\n')
        else:
            data = {"date": [datetime(year, 1, 2), datetime(year, 1, 3)], "ticker": ["123456", "123456"]}
            if name.endswith("prices.parquet"):
                data.update(adj_close=[10., 11.], close=[10., 11.], value=[1000., 1100.])
            pq.write_table(pa.table(data), path)
        hashes[name] = sha_file(path)
    recipe = recipe.model_copy(update={"input_files": hashes})
    for module in ("core", "checker", "worker"):
        monkeypatch.setattr(f"quant_company.data_watch.{module}.load_recipe", lambda: recipe)
    c = watch.company
    c.settings.data_watch_core_enabled = c.settings.company_research_enabled = True
    c.settings.research_worker_token = SecretStr("synthetic-worker-token-that-is-long-enough")
    task = c.ingest(event_key="core", text="Synthetic previously approved research", owner="UHUMAN", channel="CQUANT", thread_ts="1.1")
    job_id = str(uuid4())
    with c.db.transaction() as conn:
        conn.execute("""INSERT INTO research_jobs(id,project_id,task_id,revision,recipe_id,manifest,manifest_digest,
            company_commit,state,approval_event_id,approved_by,approved_at) VALUES(%s,%s,%s,1,%s,%s,%s,%s,
            'completed','synthetic-original-approval','UHUMAN',now())""", (job_id, task["project_id"], task["task_id"],
            recipe.id, Jsonb(recipe.model_dump(mode="json")), recipe_digest(recipe), "c" * 40))
    checks = CoreChecks(watch.store)
    checks.plan()
    assignment = CheckAssignment.model_validate(checks.poll()["assignment"])
    return SimpleNamespace(**vars(watch), root=root, scratch=scratch, recipe=recipe, assignment=assignment,
                           checks=checks, job_id=job_id, project_id=task["project_id"])


def read_core(core):
    return check(core.assignment, core.root, core.scratch).model_copy(update={"checked_at": core.clock[0]})


def test_worker_producer_service_consumer_full_input_and_idempotent_receipt(core, credentials):
    receipt = read_core(core)
    assert receipt.state == "checked" and all(not item.errors for item in receipt.files.values())
    assert {name: sha_file(core.root / name) for name in core.recipe.input_files} == core.recipe.input_files
    app = create_app(core.company.settings, core.company, credentials)
    client = TestClient(app)
    path = f"/v1/data-watch/worker/checks/{core.assignment.id}"
    assert client.post(path, json=receipt.model_dump(mode="json")).status_code == 401
    headers = {"Authorization": "Bearer " + core.company.settings.research_worker_token.get_secret_value(),
               "X-Data-Watch-Lease": core.assignment.lease_token}
    first = client.post(path, headers=headers, json=receipt.model_dump(mode="json"))
    assert first.status_code == 200, first.text
    assert client.post(path, headers=headers, json=receipt.model_dump(mode="json")).json()["duplicate"]
    assert core.store.status()["core_checks"][0]["problem"] is None
    assert "전체 입력 검사 완료" in status_text(core.store.status())
    assert len(rows(core, "data_watch_checks")) == 1


@pytest.mark.parametrize("change", ["hash", "scope", "revision", "owner", "findings", "null_coverage", "time"])
def test_stale_or_inconsistent_core_receipts_rejected(core, change):
    receipt = read_core(core)
    if change == "hash":
        next(iter(receipt.files.values())).sha256 = "f" * 64
    elif change == "scope":
        receipt.scope_digest = "f" * 64
    elif change == "time":
        receipt.checked_at += timedelta(days=1)
    elif change == "findings":
        next(item for name, item in receipt.files.items() if name.endswith("prices.parquet")).duplicate_keys = 1
    elif change == "null_coverage":
        next(item for name, item in receipt.files.items() if name.endswith("prices.parquet")).nulls = {}
    else:
        with core.company.db.transaction() as conn:
            if change == "revision":
                conn.execute("UPDATE projects SET revision=revision+1 WHERE id=%s", (core.project_id,))
            else:
                conn.execute("UPDATE projects SET owner_user='UOTHER' WHERE id=%s", (core.project_id,))
    with pytest.raises(PolicyError):
        core.checks.accept(str(core.assignment.id), core.assignment.lease_token, receipt)
    assert rows(core, "data_watch_checks")[0]["receipt"] is None


def test_core_reader_detects_hash_change_symlink_and_never_reads_other_files(core):
    name = next(iter(core.recipe.input_files))
    (core.root / name).write_bytes(b"modified")
    assert read_core(core).error == "input_identity_changed"
    (core.root / name).unlink()
    outside = core.scratch / "outside"
    outside.write_bytes(b"secret canary; do not read")
    (core.root / name).symlink_to(outside)
    assert read_core(core).error == "input_identity_changed"


def test_quality_checker_reports_duplicate_null_invalid_and_window_counts(core):
    from quant_company.data_watch.checker import inspect_file

    name = next(n for n in core.recipe.input_files if n.endswith("dev/prices.parquet"))
    path = core.scratch / "bad.parquet"
    pq.write_table(pa.table({"date": [date(2014, 1, 2), date(2014, 1, 2), None, date(2026, 9, 1)],
        "ticker": ["1", "1", "1", "1"], "adj_close": [1., -1., 2., 3.], "close": [1., 1., 2., 3.],
        "value": [100., 100., 100., float("nan")]}), path)
    value = inspect_file(path, name, sha_file(path), core.scratch)
    assert value.rows == 4 and value.duplicate_keys == 1 and value.invalid_values == 2
    assert value.errors == ["invalid_keys", "duplicate_keys", "invalid_values", "outside_window"]
    assert value.errors == errors_for(name, value)


def test_unregistered_core_manifest_never_polled(core):
    with core.company.db.transaction() as conn:
        conn.execute("UPDATE research_jobs SET manifest_digest=%s WHERE id=%s", ("e" * 64, core.job_id))
    assert core.checks.poll()["assignment"] is None
    assert core.store.status()["core_checks"] == []


def test_core_receipt_is_immutable_and_daily_wait_is_not_recovery(core):
    failed = CheckReceipt(check_id=core.assignment.id, scope_digest=core.assignment.scope_digest,
        checked_at=core.clock[0], state="unavailable", error="input_unavailable")
    core.checks.accept(str(core.assignment.id), core.assignment.lease_token, failed)
    with pytest.raises(PolicyError, match="different committed"):
        core.checks.accept(str(core.assignment.id), core.assignment.lease_token, read_core(core))
    core.store.report()
    core.clock[0] += timedelta(days=1)
    core.checks.plan()
    core.store.report()
    assert rows(core, "data_watch_incidents")[0]["state"] == "active"


async def test_registered_subprocess_transport_reuses_receipt_after_lost_ack(core, credentials, monkeypatch):
    import asyncio
    import sys
    from pathlib import Path

    from quant_company.data_watch.worker import DataWatchTransport

    # Exercise the real child entry point with the shipped recipe and deliberately absent inputs.
    # Successful synthetic-Parquet producer/consumer coverage is checked separately above.
    recipe = load_recipe()
    for module in ("core", "worker"):
        monkeypatch.setattr(f"quant_company.data_watch.{module}.load_recipe", lambda: recipe)
    core.clock[0] = datetime.now(UTC)
    with core.company.db.transaction() as conn:
        conn.execute("DELETE FROM data_watch_checks")
        conn.execute("UPDATE research_jobs SET manifest=%s,manifest_digest=%s WHERE id=%s",
                     (Jsonb(recipe.model_dump(mode="json")), recipe_digest(recipe), core.job_id))
    client = TestClient(create_app(core.company.settings, core.company, credentials), headers={
        "Authorization": "Bearer " + core.company.settings.research_worker_token.get_secret_value()})
    real_post, attempts = client.post, []

    def post(path, **kwargs):
        reply = real_post(path, **kwargs)
        if "/checks/" in path:
            attempts.append(kwargs["json"])
            if len(attempts) == 1:
                raise httpx.ReadTimeout("synthetic response lost after commit")
        return reply

    monkeypatch.setattr(client, "post", post)
    assert core.checks.poll()["assignment"] is None  # Transport does not own scheduling.
    core.checks.plan()
    lane = DataWatchTransport(SimpleNamespace(state_dir=core.scratch, input_source=core.scratch / "absent",
        company_repo=Path.cwd(), research_python=Path(sys.executable)), client)
    lane.step()
    try:
        async with asyncio.timeout(15):
            while not lane.pending.done():
                await asyncio.sleep(.05)
        with pytest.raises(httpx.ReadTimeout):
            lane.step()
        lane.step()
        assert attempts[0] == attempts[1] and attempts[0]["error"] == "input_unavailable"
        assert rows(core, "data_watch_checks")[0]["state"] == "complete"
        assert lane.pending is None
    finally:
        lane.pool.shutdown(wait=True)


def test_data_checker_environment_excludes_service_and_model_credentials(core, monkeypatch):
    import subprocess
    from pathlib import Path

    from quant_company.data_watch.worker import DataWatchTransport

    for key in ("DATABASE_URL", "AWS_SECRET_ACCESS_KEY", "MODEL_RUNTIME_TOKEN", "SLACK_BOT_TOKEN"):
        monkeypatch.setenv(key, "private-canary")

    def run(*args, **kwargs):
        assert "private-canary" not in json.dumps(kwargs["env"])
        assert kwargs["timeout"] == 190 and "OMP_NUM_THREADS" in kwargs["env"]
        raise subprocess.TimeoutExpired(args[0], 190)

    monkeypatch.setattr(subprocess, "run", run)
    lane = DataWatchTransport(SimpleNamespace(state_dir=core.scratch, input_source=core.root,
        company_repo=Path.cwd(), research_python=Path("unused-python")), None)
    try:
        assert lane.read(core.assignment).error == "reader_timeout"
    finally:
        lane.pool.shutdown(wait=True)


def test_old_worker_reports_unknown_scope_without_running_reader(core, monkeypatch):
    import subprocess
    from pathlib import Path

    from quant_company.data_watch.worker import DataWatchTransport

    monkeypatch.setattr(subprocess, "run", lambda *_, **__: pytest.fail("reader must not launch"))
    assignment = core.assignment.model_copy(update={"scope_digest": "f" * 64})
    lane = DataWatchTransport(SimpleNamespace(state_dir=core.scratch, input_source=core.root,
        company_repo=Path.cwd(), research_python=Path("unused-python")), None)
    try:
        assert lane.read(assignment).error == "scope_not_registered"
    finally:
        lane.pool.shutdown(wait=True)


async def test_calendar_expiry_cannot_close_a_delay_incident(watch, tmp_path):
    path = tmp_path / "calendar.json"
    path.write_text(json.dumps([{"dataset": "krx_etf", "date_column": "date", "evidence": "synthetic reviewed calendar",
        "valid_until": "2026-09-22T01:00:00Z", "dates": [{"data_date": "2026-09-22",
            "expected_after": "2026-09-21T23:00:00Z"}]}]))
    watch.company.settings.data_watch_contracts_file = path
    await tick(watch)
    assert rows(watch, "data_watch_incidents")[0]["detail"]["problem"] == "data_late"
    watch.clock[0] += timedelta(hours=2)
    await tick(watch)
    assert watch.store.status()["datasets"][0]["freshness"]["state"] == "unregistered"
    assert rows(watch, "data_watch_incidents")[0]["state"] == "active"


async def test_research_revocation_between_queue_and_delivery_blocks_data_notice(core, credentials):
    receipt = CheckReceipt(check_id=core.assignment.id, scope_digest=core.assignment.scope_digest,
        checked_at=core.clock[0], state="unavailable", error="input_unavailable")
    core.checks.accept(str(core.assignment.id), core.assignment.lease_token, receipt)
    core.store.report()
    box, calls = delivery(core, credentials)
    # Claim the incident before the owner revises its original research thread.
    with core.company.db.transaction() as conn:
        conn.execute("UPDATE outbox SET status='stale' WHERE agent<>'data'")
    claimed = box.claim()
    assert claimed["message_kind"] == "data_watch"
    with core.company.db.transaction() as conn:
        conn.execute("UPDATE projects SET revision=revision+1 WHERE id=%s", (core.project_id,))
    assert not box.before_send(claimed)
    await drain(box)
    assert all(row["status"] == "stale" for row in rows(core, "outbox"))
    assert not calls

"""The signal qualification script: its refusals, and one run of the real signal path in a disposable local database
with an injected provider. No real model is called here and nothing reaches Slack: the Slack sender is made to fail
if anything constructs it."""

import asyncio
import importlib.util
import json
import os
from datetime import date
from pathlib import Path

import psycopg
import pytest

from quant_company.contracts import AgentDecision, ArtifactDraft, ProviderResponse

from .signals_fixtures import snapshot

MONDAY = date(2026, 11, 16)
SOURCE = "signal:2026-11-16:overnight:snapshot"
EDITION = "[합성 요약 · 검증용] 미국 증시는 혼조로 마감했고 반도체가 강했다."


@pytest.fixture
def script():
    path = Path(__file__).resolve().parents[1] / "scripts/qualify_signals.py"
    spec = importlib.util.spec_from_file_location("qualify_signals_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def admin():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Real PostgreSQL required: set TEST_DATABASE_URL to a disposable local test cluster")
    return url


@pytest.fixture
def no_database(script, monkeypatch):
    """A refusal happens before any database connection."""
    def refuse(*args, **kwargs):
        raise AssertionError("a refused qualification must not connect to PostgreSQL")
    monkeypatch.setattr(script.psycopg, "connect", refuse)


@pytest.fixture
def no_slack(monkeypatch):
    from quant_company import slack

    def refuse(self, *args, **kwargs):
        raise AssertionError("the qualification must never construct the Slack sender")
    monkeypatch.setattr(slack.SlackOutbox, "__init__", refuse)


class Boom:
    async def run(self, request):
        raise AssertionError("a refused qualification must not reach the model")


def run(script, tmp_path, *, admin, live=True, provider=None, **kwargs):
    return asyncio.run(script.qualify(
        admin=admin, kind="overnight", edition=EDITION, output=tmp_path / "receipt.json", live=live,
        snapshot=kwargs.pop("snapshot", snapshot("overnight", MONDAY)), day=kwargs.pop("day", MONDAY),
        provider=provider, jobs_dir=tmp_path / "jobs", **kwargs))


def dropped(admin, receipt):
    """The run's disposable database is gone, and the receipt says so."""
    name = receipt["database"]["name"]
    assert name.startswith("company_signal_qualification_") and receipt["database"]["dropped"] is True
    with psycopg.connect(admin, autocommit=True) as conn:
        return conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone() is None


def test_refuses_without_live(script, tmp_path, admin, no_database):
    with pytest.raises(ValueError, match="qualification_requires_live"):
        run(script, tmp_path, admin=admin, live=False, provider=Boom())
    assert not (tmp_path / "receipt.json").exists()


def test_cli_refuses_without_live(script, tmp_path, capsys):
    edition = tmp_path / "edition.txt"
    edition.write_text(EDITION)
    with pytest.raises(SystemExit) as stop:
        script.main(["--kind", "overnight", "--fetch-snapshot", "--edition", str(edition),
                     "--output", str(tmp_path / "receipt.json")])
    assert stop.value.code == 2
    assert json.loads(capsys.readouterr().out) == {"state": "refused", "error": "qualification_requires_live"}
    assert not (tmp_path / "receipt.json").exists()


@pytest.mark.parametrize("url", ["postgresql://postgres@db.example.com:5432/postgres",
                                 "postgresql://postgres@10.0.0.5/postgres", "postgresql:///postgres"])
def test_refuses_a_database_that_is_not_local(script, tmp_path, url, no_database):
    with pytest.raises(ValueError, match="qualification_requires_local_database"):
        run(script, tmp_path, admin=url, provider=Boom())
    assert not (tmp_path / "receipt.json").exists()


def proposal_answer(request):
    content = json.dumps({"kind": "overnight", "session_day": str(MONDAY), "direction": "short",
                          "reasons": [{"text": "아시아 지수가 약하다", "points": 10, "source_ids": [SOURCE]}],
                          "invalidation": "마감 동시호가에서 반등하면", "source_ids": [SOURCE]}, ensure_ascii=False)
    return ProviderResponse(request_id=request.request_id, decision=AgentDecision(
        status="complete", say="숏으로 봅니다.", artifacts=[ArtifactDraft(title="매매 신호", content=content)]))


def test_runs_the_signal_path_in_a_disposable_database(script, tmp_path, admin, no_slack):
    requests = []

    class Analyst:
        async def run(self, request):
            requests.append(request)
            return proposal_answer(request)

        async def cancel(self, request_id):
            return "cancelled"

    receipt = run(script, tmp_path, admin=admin, provider=Analyst(), synthetic_edition=True)
    assert receipt == json.loads((tmp_path / "receipt.json").read_text())
    assert (receipt["state"], receipt["kind"], receipt["session_day"]) == ("committed", "overnight", "2026-11-16")
    # The worker's own prompt: the company header, the service's signal instruction and the edition in the thread.
    [request] = requests
    assert request.output_contract == "agent_decision" and "TASK DATA JSON:\n" in request.prompt
    assert "오버나잇 신호 업무다" in request.prompt and SOURCE in request.prompt
    assert json.dumps(EDITION, ensure_ascii=False)[1:-1] in request.prompt
    assert (receipt["model"], receipt["reasoning_effort"]) == (request.model, request.reasoning_effort)
    assert [(turn["sequence"], turn["state"], turn["task_status"]) for turn in receipt["turns"]] == [
        (1, "completed", "completed")]
    assert receipt["turns"][0]["latency_seconds"] >= 0
    assert (receipt["proposal"]["direction"], receipt["timing"], receipt["format_failures"], receipt["unparsed"]) == (
        "short", "on_time", 0, False)
    # The post exists only as an undispatched row of the dropped database.
    [post] = receipt["posts"]
    assert post["outbox_status"] == "pending" and post["text"].startswith("🌙 오버나잇 신호 · 11월 16일(월)")
    assert (receipt["real_model"], receipt["slack_sender_constructed"]) == (False, False)
    assert (receipt["completed_model_turns"], receipt["provider_faults"]) == (1, [])
    assert receipt["edition"]["synthetic"] is True and receipt["snapshot"]["origin"] == "file"
    assert any("Injected provider" in note for note in receipt["notes"])
    assert dropped(admin, receipt)


def test_records_a_model_fault(script, tmp_path, admin, no_slack):
    from quant_company.contracts import ProviderFault

    class Unavailable:
        async def run(self, request):
            raise ProviderFault("unavailable", "The runtime requires the validated Codex CLI.")

        async def cancel(self, request_id):
            return "cancelled"

    receipt = run(script, tmp_path, admin=admin, provider=Unavailable())
    assert receipt["state"] == "deferred"
    assert [(turn["state"], turn["reason"]) for turn in receipt["turns"]] == [("defer", "unavailable")]
    assert receipt["proposal"] is None and receipt["posts"] == []
    # The runner's own fault text is kept, and the notes never claim a model turn that did not happen.
    assert [(fault["code"], fault["message"]) for fault in receipt["provider_faults"]] == [
        ("unavailable", "The runtime requires the validated Codex CLI.")]
    assert receipt["completed_model_turns"] == 0
    assert any(note.startswith("No model turn completed: the provider raised unavailable") for note in receipt["notes"])
    assert dropped(admin, receipt)

"""The briefing, the news desk and Quant Scout on Claude (owner, 2026-11-17).

Real PostgreSQL, real subprocess and ASGI HTTP; the Claude CLI and its outputs are synthetic and no Slack message is
sent. Lanes, deadlines, provider identity and the reviewed allocation are checked; real subscription capacity is not.
"""

import fcntl
import json
import os
import time
from contextlib import contextmanager
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from quant_company.api import create_app
from quant_company.briefing.contracts import SourcePlan
from quant_company.briefing.store import BRIEFER
from quant_company.company import PolicyError
from quant_company.contracts import ProviderFault, ProviderRequest
from quant_company.news.screening import NewsScreeningStore
from quant_company.news.store import NewsStore
from quant_company.providers.claude_runner import LOCKS, NEWS_DESK_SECONDS, ClaudeRunner, cli_schema, command
from quant_company.providers.codex_runner import output_schema
from quant_company.providers.routing import CLAUDE_MODEL, CLAUDE_SONNET, CompanyProvider, own_lane
from quant_company.staff_models import (
    EDITORIAL,
    EDITORIAL_PLAN,
    PRESERVED,
    TARGETS,
    StaffAllocation,
    allocate,
)

from .test_briefing import brief, bundle, seed  # noqa: F401
from .test_briefing_inventory import composition, fragment_inventory
from .test_briefing_inventory import review as inventory_review
from .test_claude_company import allocation_office, client
from .test_claude_runtime import fake_claude as fake_claude
from .test_news import reply
from .test_news_efficiency import efficient as efficient  # noqa: F401
from .test_news_efficiency import news as news  # noqa: F401
from .test_news_efficiency import overnight, screen_reply

BRIEF = "news-brief-00000000-0000-0000-0000-000000000001-"


def bind(company, **targets):
    company.settings.model_assignments_enabled = True
    with company.db.transaction() as conn:
        conn.execute("UPDATE model_assignment_policy SET bindings=%s WHERE id=1", (Jsonb({
            target: {"model": model, "reasoning_effort": effort} for target, (model, effort) in targets.items()}),))


def decision_of(response):
    return {"decision_json": response.decision.model_dump_json()}


@contextmanager
def held(config, lane):
    """Hold one Claude CLI slot the way a running turn does (an episode holds the shared one)."""
    config.jobs_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(config.jobs_dir / LOCKS[lane], os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        yield
    finally:
        os.close(fd)


def news_request(identity="news-screen-"+"0"*8, model=CLAUDE_SONNET):
    return ProviderRequest(request_id=identity, model=model, reasoning_effort="low", prompt="Synthetic news fixture")


def test_lanes_follow_the_codex_request_prefixes():
    assert own_lane(news_request(BRIEF+"write", CLAUDE_MODEL)) == "brief"
    assert own_lane(news_request("quant-feed-x")) == "quant"
    for identity in ("news-screen-x", "news-x", "news-trends-x", "news-search-x"):
        assert own_lane(news_request(identity)) == "news"
    assert own_lane(news_request(str(uuid4()), CLAUDE_MODEL)) is None
    assert own_lane(news_request("video-x", CLAUDE_MODEL)) is None
    assert own_lane(news_request("news-x", "gpt-6-astra")) is None


async def test_episode_priority_holds_only_shared_slot_work(company, monkeypatch):
    from quant_company.newsletter.store import NewsletterStore

    monkeypatch.setattr(NewsletterStore, "video_needs_model", lambda _: True)

    class Recorder:
        def __init__(self):
            self.calls = []

        async def run(self, request):
            self.calls.append(request.request_id)
            return request.request_id

    codex, claude = Recorder(), Recorder()
    routed = CompanyProvider(company, codex, claude)
    for identity, model in (("news-screen-a", CLAUDE_SONNET), ("news-b", CLAUDE_SONNET),
                            ("quant-feed-c", CLAUDE_SONNET), (BRIEF+"write", CLAUDE_MODEL)):
        assert await routed.run(news_request(identity, model)) == identity
    with pytest.raises(ProviderFault) as busy:
        await routed.run(news_request(str(uuid4()), CLAUDE_MODEL))
    assert busy.value.code == "busy"
    assert claude.calls == ["news-screen-a", "news-b", "quant-feed-c", BRIEF+"write"] and codex.calls == []
    assert await routed.run(news_request("news-d", "gpt-6-astra")) == "news-d" and codex.calls == ["news-d"]


async def test_a_held_episode_slot_never_blocks_the_news_desk_and_slots_stay_separate(fake_claude):
    config, configure, calls = fake_claude
    configure(model=CLAUDE_SONNET, used_model=CLAUDE_SONNET, review=decision_of(screen_reply({
        "request_id": "x", "prompt": "SCREEN DATA JSON:\n" + json.dumps({"articles": []})})))
    runner = ClaudeRunner(config, source_environment={"PATH": os.environ["PATH"]})
    with held(config, None):
        result = await runner.run(news_request("news-screen-1"))
        assert result.provider == "claude" and result.usage["actual_model"] == CLAUDE_SONNET
        with pytest.raises(ProviderFault) as shared:
            await runner.run(news_request(str(uuid4()), CLAUDE_SONNET))
        assert shared.value.code == "busy"
    with held(config, "news"):
        with pytest.raises(ProviderFault) as desk:
            await runner.run(news_request("news-screen-2"))
        assert desk.value.code == "busy"
        assert (await runner.run(news_request("quant-feed-3"))).provider == "claude"
    assert [call["args"][call["args"].index("--model") + 1] for call in calls()] == [CLAUDE_SONNET] * 2


async def test_brief_turns_keep_their_frozen_deadline_and_a_passed_one_never_runs(fake_claude):
    config, configure, calls = fake_claude
    news = news_request("news-x")
    staff = news_request(str(uuid4()), CLAUDE_MODEL)
    assert config.process_limit(news) == NEWS_DESK_SECONDS and config.process_limit(staff) == config.timeout_seconds
    timed = ProviderRequest(request_id=BRIEF+"plan", model=CLAUDE_MODEL, reasoning_effort="xhigh",
                            prompt="Synthetic plan", output_contract="brief_plan_v1",
                            brief_deadline_unix=int(time.time()) + 3000)
    assert 2990 <= config.process_limit(timed) <= 3000 and config.process_limit(timed) > NEWS_DESK_SECONDS
    late = timed.model_copy(update={"request_id": BRIEF+"review", "output_contract": "brief_review_v2",
                                    "brief_deadline_unix": int(time.time()) - 1})
    runner = ClaudeRunner(config, source_environment={"PATH": os.environ["PATH"]})
    with pytest.raises(ProviderFault) as fault:
        await runner.run(late)
    assert fault.value.code == "timeout" and calls() == []
    assert not (config.jobs_dir / f"{late.request_id}.json").exists()
    # A briefing phase reads the strict wire schema Codex receives; other company work keeps its own.
    assert cli_schema(timed) == output_schema(timed)
    args = command(config, timed)
    assert json.loads(args[args.index("--json-schema") + 1]) == output_schema(timed)
    assert args[args.index("--tools") + 1] == ""


async def test_news_screen_and_review_run_on_claude_and_reject_a_codex_answer(efficient, fake_claude):  # noqa: F811
    store, _ = efficient
    config, configure, calls = fake_claude
    company = store.company
    company.settings.model_provider = "codex"
    bind(company, news_screening=(CLAUDE_SONNET, "low"), reporter=(CLAUDE_SONNET, "medium"))
    overnight(store, suffix="claude")
    screen = NewsScreeningStore(company)
    request = screen.prepare()["request"]
    assert (request["model"], request["reasoning_effort"]) == (CLAUDE_SONNET, "low")
    configure(model=CLAUDE_SONNET, used_model=CLAUDE_SONNET, review=decision_of(screen_reply(request)))
    answer = await client(config).run(ProviderRequest.model_validate(request))
    with pytest.raises(ValueError, match="news_screen_artifact_only"):
        screen.commit(answer.model_copy(update={"provider": "codex"}))
    assert screen.commit(answer)["state"] != "blocked"
    review = NewsStore(company).prepare_review()["request"]
    assert (review["model"], review["reasoning_effort"]) == (CLAUDE_SONNET, "medium")
    configure(review=decision_of(reply(review)))
    answer = await client(config).run(ProviderRequest.model_validate(review))
    with pytest.raises(ValueError, match="news_unexpected_provider"):
        NewsStore(company).commit_review(answer.model_copy(update={"provider": "codex"}))
    assert NewsStore(company).commit_review(answer)["state"] != "stale"
    efforts = [call["args"][call["args"].index("--effort") + 1] for call in calls()]
    assert efforts == ["low", "medium"]
    with company.db.transaction() as conn:
        bound = conn.execute("SELECT target,selection FROM model_execution_bindings ORDER BY target").fetchall()
        reviews = conn.execute("SELECT response FROM news_reviews").fetchall()
    assert {row["target"]: row["selection"]["model"] for row in bound} == {
        "news_screening": CLAUDE_SONNET, "reporter": CLAUDE_SONNET}
    assert [row["response"]["provider"] for row in reviews] == ["claude"]


async def test_brief_edition_runs_every_phase_on_claude_opus(brief, fake_claude, monkeypatch):  # noqa: F811
    store, clock = brief
    config, configure, calls = fake_claude
    # Frozen phase deadlines are read against the edition's (fixture) clock on both sides of the runtime.
    monkeypatch.setattr("quant_company.briefing.execution.wall_time", lambda: clock["at"].timestamp())
    company = store.company
    company.settings.model_provider = "codex"
    company.settings.briefing_source_notes_enabled = True
    company.settings.briefing_publish_enabled = False
    bind(company, market_brief=(CLAUDE_MODEL, "xhigh"))
    edition = seed(brief)
    data = bundle()
    data["candidate_documents"] = data["documents"]  # the planned, inventoried edition (test_briefing_inventory)
    with company.db.transaction() as conn:
        conn.execute("UPDATE brief_editions SET bundle=%s WHERE id=%s", (Jsonb(data), edition.id))
    outputs = {"brief_plan_v1": SourcePlan(selections=[{"source_id": "source-1",
                   "reason": "시장의 종가와 업종 참여를 함께 설명하는 원문이다."}], priorities=["시장 참여의 폭을 확인한다."]),
               "brief_inventory_v2": fragment_inventory(), "brief_compose_v1": composition(),
               "brief_review_v2": inventory_review()}
    phases = []
    for _ in outputs:
        ready = store.prepare()
        if ready.get("state") != "ready":
            break
        request = ready["request"]
        assert (request["model"], request["reasoning_effort"]) == (CLAUDE_MODEL, "xhigh")
        configure(model=CLAUDE_MODEL, used_model=CLAUDE_MODEL,
                  review=json.loads(outputs[request["output_contract"]].model_dump_json()))
        answer = await client(config).run(ProviderRequest.model_validate(request))
        assert answer.provider == "claude" and answer.usage["billing_mode"] == "subscription"
        if not phases:
            with pytest.raises(ValueError, match="brief_provider_mismatch"):
                store.commit(answer.model_copy(update={"provider": "codex"}))
        assert store.commit(answer)["state"] == "completed"
        phases.append(request["output_contract"])
    assert phases[0] == "brief_plan_v1" and "brief_compose_v1" in phases and phases[-1].startswith("brief_review")
    for call in calls():
        args = call["args"]
        assert args[args.index("--model") + 1] == CLAUDE_MODEL and args[args.index("--effort") + 1] == "xhigh"
        assert args[args.index("--tools") + 1] == ""
    with company.db.transaction() as conn:
        providers = {row["provider"] for row in conn.execute(
            "SELECT response->>'provider' AS provider FROM brief_calls WHERE state='completed'").fetchall()}
    assert providers == {"claude"}
    assert BRIEFER == "market_brief"


def test_editorial_allocation_is_reviewed_atomic_and_disjoint_from_staff(company, credentials):
    original = allocation_office(company)
    command_ = StaffAllocation(operation_id="owner-editorial-1", expected_revision=2)
    preview = allocate(company, command_, EDITORIAL_PLAN)
    assert preview["state"] == "preview"
    for target, (model, effort) in EDITORIAL.items():
        assert (preview["after"][target]["model"], preview["after"][target]["reasoning_effort"]) == (model, effort)
    assert {target: preview["after"][target] for target in TARGETS} == {target: original[target] for target in TARGETS}
    applied = allocate(company, command_.model_copy(update={"dry_run": False, "plan_digest": preview["plan_digest"]}),
                       EDITORIAL_PLAN)
    assert applied["revision"] == 3 and not applied["cached"]
    with pytest.raises(PolicyError, match="different allocation"):
        allocate(company, StaffAllocation(operation_id="owner-editorial-1", expected_revision=2))
    # Staff afterwards leaves every editorial target as applied, whichever order the operator uses.
    staff = StaffAllocation(operation_id="owner-staff-after", expected_revision=3)
    staffed = allocate(company, staff.model_copy(update={"dry_run": False,
                       "plan_digest": allocate(company, staff)["plan_digest"]}))
    assert {target: staffed["after"][target] for target in EDITORIAL} == {
        target: applied["after"][target] for target in EDITORIAL}
    assert set(EDITORIAL) == set(PRESERVED)
    with company.db.transaction() as conn:
        kinds = [r["kind"] for r in conn.execute("""SELECT kind FROM events WHERE kind LIKE '%claude_allocation'
            ORDER BY created_at""").fetchall()]
        assert conn.execute("SELECT count(*) n FROM outbox").fetchone()["n"] == 0
    assert kinds == ["editorial_claude_allocation", "staff_claude_allocation"]
    api = TestClient(create_app(company.settings, company, credentials))
    assert api.post("/v1/model-assignments/claude-editorial", json=command_.model_dump()).status_code == 401


def test_quant_scout_moved_from_the_staff_allocation_to_the_editorial_one():
    assert "quant_scout" not in TARGETS and "quant_scout" in PRESERVED
    assert EDITORIAL["quant_scout"] == (CLAUDE_SONNET, "high") and EDITORIAL["market_brief"][0] == CLAUDE_MODEL

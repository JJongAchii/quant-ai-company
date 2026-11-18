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
from quant_company.ops.status import current_windows
from quant_company.ops.watch import claude_subscription_findings
from quant_company.providers.claude_runner import (
    LOCKS,
    NEWS_DESK_SECONDS,
    SUBSCRIPTION_STATUS,
    ClaudeRunner,
    cli_schema,
    command,
    subscription_window,
)
from quant_company.providers.codex_runner import output_schema
from quant_company.providers.routing import CLAUDE_MODEL, CLAUDE_SONNET, CompanyProvider, own_lane
from quant_company.staff.review_contract import REVIEW_MODEL
from quant_company.staff_models import (
    BRIEFING,
    EDITORIAL,
    EDITORIAL_PLAN,
    PRESERVED,
    REPORTER,
    REPORTER_PLAN,
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
    assert own_lane(news_request(str(uuid4()), CLAUDE_MODEL)) == "company"  # staff and research
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
    staff = str(uuid4())
    for identity, model in (("news-screen-a", CLAUDE_SONNET), ("news-b", CLAUDE_SONNET),
                            ("quant-feed-c", CLAUDE_SONNET), (BRIEF+"write", CLAUDE_MODEL), (staff, CLAUDE_MODEL)):
        assert await routed.run(news_request(identity, model)) == identity
    # Only work that would share the episode's own slot still yields to it (none of the company's turns).
    with pytest.raises(ProviderFault) as busy:
        await routed.run(news_request("review-x", CLAUDE_MODEL))
    assert busy.value.code == "busy"
    assert claude.calls == ["news-screen-a", "news-b", "quant-feed-c", BRIEF+"write", staff] and codex.calls == []
    assert await routed.run(news_request("news-d", "gpt-6-astra")) == "news-d" and codex.calls == ["news-d"]


async def test_a_held_episode_slot_never_blocks_company_work_and_slots_stay_separate(fake_claude):
    config, configure, calls = fake_claude
    configure(model=CLAUDE_SONNET, used_model=CLAUDE_SONNET, review=decision_of(screen_reply({
        "request_id": "x", "prompt": "SCREEN DATA JSON:\n" + json.dumps({"articles": []})})))
    runner = ClaudeRunner(config, source_environment={"PATH": os.environ["PATH"]})
    with held(config, None):  # an episode turn holds its slot
        result = await runner.run(news_request("news-screen-1"))
        assert result.provider == "claude" and result.usage["actual_model"] == CLAUDE_SONNET
        assert (await runner.run(news_request(str(uuid4()), CLAUDE_SONNET))).provider == "claude"
    with held(config, "company"):
        with pytest.raises(ProviderFault) as shared:
            await runner.run(news_request(str(uuid4()), CLAUDE_SONNET))
        assert shared.value.code == "busy"
    with held(config, "news"):
        with pytest.raises(ProviderFault) as desk:
            await runner.run(news_request("news-screen-2"))
        assert desk.value.code == "busy"
        assert (await runner.run(news_request("quant-feed-3"))).provider == "claude"
    assert [call["args"][call["args"].index("--model") + 1] for call in calls()] == [CLAUDE_SONNET] * 3


async def test_a_shorts_turn_takes_its_own_slot_and_never_the_episodes(fake_claude):
    # Owner, 2026-11-18: a Shorts turn the API lane cannot take runs on its own subscription slot beside the episode
    # (11-18 AM: the capped lane held the morning cut 50 minutes until its episode was out).
    config, configure, calls = fake_claude
    configure(review={"facts": True, "numbers": True, "conditions": True, "coverage": True, "readability": True,
                      "concerns": []})
    runner = ClaudeRunner(config, source_environment={"PATH": os.environ["PATH"]})
    job = str(uuid4())

    def review(identity):
        return ProviderRequest(request_id=f"video-{job}-{identity}", model=REVIEW_MODEL, reasoning_effort="max",
                               prompt="Plan: fixture", output_contract="video_review_v1")

    with held(config, None):  # an episode turn holds its slot
        assert (await runner.run(review("shorts-review-1"))).provider == "claude"
        with pytest.raises(ProviderFault) as episode:
            await runner.run(review("review-1"))
        assert episode.value.code == "busy"
    with held(config, "shorts"):  # a cut's turn running: the episode never waits for it
        assert (await runner.run(review("review-2"))).provider == "claude"
        with pytest.raises(ProviderFault) as cut:
            await runner.run(review("shorts-review-2"))
        assert cut.value.code == "busy"
    assert len(calls()) == 2


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


async def test_news_screen_on_sonnet_and_review_on_opus_reject_a_codex_answer(efficient, fake_claude):  # noqa: F811
    store, _ = efficient
    config, configure, calls = fake_claude
    company = store.company
    company.settings.model_provider = "codex"
    bind(company, news_screening=(CLAUDE_SONNET, "low"), reporter=(CLAUDE_MODEL, "medium"))
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
    assert (review["model"], review["reasoning_effort"]) == (CLAUDE_MODEL, "medium")
    configure(model=CLAUDE_MODEL, used_model=CLAUDE_MODEL, review=decision_of(reply(review)))
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
        "news_screening": CLAUDE_SONNET, "reporter": CLAUDE_MODEL}
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


def apply(company, operation, revision, plan=None):
    command_ = StaffAllocation(operation_id=operation, expected_revision=revision)
    preview = allocate(company, command_, *([plan] if plan else []))
    applied = allocate(company, command_.model_copy(update={"dry_run": False, "plan_digest": preview["plan_digest"]}),
                       *([plan] if plan else []))
    return preview, applied


def test_two_stage_editorial_allocations_are_reviewed_atomic_and_disjoint_from_staff(company, credentials):
    original = allocation_office(company)
    preview, applied = apply(company, "owner-editorial-1", 2, EDITORIAL_PLAN)
    assert preview["state"] == "preview" and applied["revision"] == 3 and not applied["cached"]
    for target, (model, effort) in EDITORIAL.items():
        assert (applied["after"][target]["model"], applied["after"][target]["reasoning_effort"]) == (model, effort)
    # Stage 1 leaves the news desk on Codex and every staff target as it was.
    untouched = (*TARGETS, *REPORTER)
    assert {t: applied["after"][t] for t in untouched} == {t: original[t] for t in untouched}
    with pytest.raises(PolicyError, match="different allocation"):
        allocate(company, StaffAllocation(operation_id="owner-editorial-1", expected_revision=3), REPORTER_PLAN)
    _, reporter = apply(company, "owner-reporter-1", 3, REPORTER_PLAN)
    assert reporter["after"]["reporter"]["model"] == CLAUDE_MODEL and reporter["revision"] == 4
    assert reporter["after"]["news_search"] == {**reporter["after"]["reporter"], "operation_id": "owner-reporter-1"}
    assert {t: reporter["after"][t] for t in EDITORIAL} == {t: applied["after"][t] for t in EDITORIAL}
    # Staff afterwards leaves every editorial target as applied, whichever order the operator uses.
    _, staffed = apply(company, "owner-staff-after", 4)
    assert {t: staffed["after"][t] for t in (*EDITORIAL, *REPORTER)} == {
        t: reporter["after"][t] for t in (*EDITORIAL, *REPORTER)}
    assert set(EDITORIAL) | set(REPORTER) | set(BRIEFING) == set(PRESERVED)
    assert not (set(EDITORIAL) & set(REPORTER) or set(EDITORIAL) & set(BRIEFING) or set(REPORTER) & set(BRIEFING))
    # The briefing stage is separate and was not applied: market_brief stays on Codex.
    assert staffed["after"]["market_brief"] == original["market_brief"]
    with company.db.transaction() as conn:
        kinds = [r["kind"] for r in conn.execute("""SELECT kind FROM events WHERE kind LIKE '%claude_allocation'
            ORDER BY created_at""").fetchall()]
        assert conn.execute("SELECT count(*) n FROM outbox").fetchone()["n"] == 0
    assert kinds == ["editorial_claude_allocation", "reporter_claude_allocation", "staff_claude_allocation"]
    api = TestClient(create_app(company.settings, company, credentials))
    for path in ("claude-editorial", "claude-reporter", "claude-briefing"):
        assert api.post(f"/v1/model-assignments/{path}", json={"operation_id": "x", "expected_revision": 4}
                        ).status_code == 401


def test_quant_scout_moved_from_the_staff_allocation_to_the_editorial_one():
    assert "quant_scout" not in TARGETS and "quant_scout" in PRESERVED
    assert EDITORIAL["quant_scout"] == (CLAUDE_SONNET, "high") and BRIEFING["market_brief"] == (CLAUDE_MODEL, "xhigh")
    assert "market_brief" not in EDITORIAL
    assert REPORTER["reporter"] == (CLAUDE_MODEL, "medium")


# The CLI 2.1.280 shape observed in the 2026-11-17 trial (scripts/trial_claude_editorial.py).
RATE_LIMIT = {"status": "allowed", "resetsAt": 1795136400, "rateLimitType": "seven_day", "overageStatus": "rejected",
              "overageDisabledReason": "org_level_disabled", "isUsingOverage": False,
              "unifiedWindows": {"seven_day": {"utilization": 0.83, "resetsAt": 1795136400},
                                 "five_hour": {"utilization": 0.2, "resetsAt": 1794900000}}}


async def test_subscription_windows_reach_the_status_file_and_the_ops_watch(fake_claude):
    from datetime import UTC, datetime

    config, configure, _ = fake_claude
    configure(model=CLAUDE_SONNET, used_model=CLAUDE_SONNET, rate_limit=RATE_LIMIT, review=decision_of(screen_reply({
        "request_id": "x", "prompt": "SCREEN DATA JSON:\n" + json.dumps({"articles": []})})))
    runner = ClaudeRunner(config, source_environment={"PATH": os.environ["PATH"]})
    await runner.run(news_request("news-screen-window"))
    report = json.loads((config.jobs_dir / SUBSCRIPTION_STATUS).read_text())
    assert report["model"] == CLAUDE_SONNET and report["status"] == "allowed" and report["overage"] == "rejected"
    assert report["windows"]["seven_day"] == {"utilization": 0.83, "resets_at": 1795136400}
    assert "org_level_disabled" not in json.dumps(report) and "isUsingOverage" not in report
    before_reset = datetime.fromtimestamp(1794890000, UTC)
    assert set(current_windows(report, before_reset)["windows"]) == {"seven_day", "five_hour"}
    after_five_hours = datetime.fromtimestamp(1794950000, UTC)
    assert set(current_windows(report, after_five_hours)["windows"]) == {"seven_day"}
    found = claude_subscription_findings(report, before_reset)
    assert [(f["key"], f["severity"]) for f in found] == [("claude_subscription:seven_day", "warning")]
    report["windows"]["seven_day"]["utilization"] = 0.97
    report["status"] = "rejected"
    assert {(f["key"], f["severity"]) for f in claude_subscription_findings(report, before_reset)} == {
        ("claude_subscription:limited", "critical"), ("claude_subscription:seven_day", "critical")}
    assert claude_subscription_findings(None, before_reset) == []
    assert subscription_window(b'not json\n{"type":"result"}') is None

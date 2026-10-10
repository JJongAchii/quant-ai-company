import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from quant_company.briefing import schedule
from quant_company.briefing.contracts import (
    BRIEFER,
    REVIEW_CHECKS,
    BriefProposal,
    BriefReview,
    CalendarOverride,
    MarketObservation,
    SourceDocument,
)
from quant_company.briefing.editor import artifact, observation_text, render, validate
from quant_company.briefing.runner import BriefEditor
from quant_company.briefing.store import BriefStore, priority_pending
from quant_company.cli import manifests
from quant_company.company import PolicyError, load_roles
from quant_company.config import Settings
from quant_company.contracts import AgentDecision, ProviderFault, ProviderResponse, ToolRequest
from quant_company.slack import SlackIngress, SlackOutbox

DAY = date(2026, 9, 22)
CONTENT = ("On September 21, 2026, the S&P 500 closed at 5300.00 points versus its previous close of 5200.00. "
           "The Nasdaq Composite closed at 17100.00 points versus 17000.00. Treasury yields declined. "
           "Semiconductor shares led the advance, but participation outside technology was mixed. "
           "한국 반도체 업종의 흐름은 추가로 확인해야 합니다.")


def definition(kind="am", day=DAY):
    return next(e for e in schedule.editions(day, "CQUANT", "UHUMAN") if e.kind == kind)


def bundle(edition=None):
    edition = edition or definition()
    doc = SourceDocument(id="source-1", url="https://www.cnbc.com/fixture-market-report.html",
        title="Synthetic closing report", publisher="Synthetic fixture", kind="media", content=CONTENT,
        published_at=edition.cutoff-timedelta(hours=1), retrieved_at=edition.cutoff-timedelta(minutes=2),
        sha256="a"*64, registration="fixture", receipt={"synthetic": True})
    return {"edition": edition.model_dump(mode="json"), "documents": [doc.model_dump(mode="json")],
            "collection_errors": [], "morning_watchpoints": []}


def evidence():
    return [{"source_id": "source-1", "quote": CONTENT}]


def proposal():
    e = definition()
    values = [{"id": name, "instrument": name, "value": value, "unit": "pt", "session_date": str(e.us_session),
               "as_of": "2026-09-21T20:00:00Z", "basis": "close", "venue": "US consolidated regular close",
               "previous_value": previous, "previous_session_date": str(e.previous_us_session), "evidence": evidence()}
              for name, value, previous in (("sp500", "5300.00", "5200.00"), ("nasdaq", "17100.00", "17000.00"))]
    def claim(identity, text, kind="fact"):
        return {"id": identity, "text": text, "kind": kind, "evidence": evidence()}

    return BriefProposal(summary=[claim("summary", "미국 증시는 반도체가 주도했으며 업종별 흐름은 엇갈렸습니다.")],
        overview=[claim("overview", "국채 수익률 하락과 반도체 상승이 함께 나타났지만 기술주 밖의 참여는 혼조입니다.")],
        observations=values, issues=[{"headline": "반도체 주도와 제한된 확산",
            "fact": claim("fact", "미국 지수는 상승했고 반도체가 상승을 주도했습니다."),
            "interpretation": claim("view", "할인율 하락이 도움이 됐을 수 있지만 시장 전반의 호조로 확대 해석하지 않습니다.", "interpretation"),
            "next_check": claim("condition", "비기술 업종으로 상승이 확산되는지 확인합니다.", "condition"),
            "analysis": {"horizon": "session", "causal_basis": "conditional_hypothesis",
                "mechanism": claim("mechanism", "수익률 하락은 다른 조건이 같을 때 성장주의 할인 부담을 낮출 수 있습니다.", "interpretation"),
                "alternative": claim("alternative", "금리보다 반도체 자체의 재료가 영향을 줬을 수도 있습니다.", "interpretation")}}],
        internals=[], calendar=[],
        watchpoints=[{"id": "watch", "text": "한국 반도체 업종으로 강세가 이어지는지 확인합니다.",
                      "kind": "condition", "evidence": evidence()}], watch_results=[])


def response(request, value=None, **kwargs):
    phase = request["request_id"].rsplit("-", 1)[-1]
    value = value or (proposal() if phase in {"write", "revise"} else review())
    return ProviderResponse(request_id=request["request_id"], provider="fixture",
        decision=AgentDecision(status="complete", say="", artifacts=[{
            "title": "Synthetic briefing", "content": value.model_dump_json(), "source_ids": []}]), **kwargs)


def review(**updates):
    return BriefReview(verdict="publish", checks=dict.fromkeys(sorted(REVIEW_CHECKS), True),
        source_assessments=[{"source_id": "source-1", "treatment": "covered",
                             "reason": "합성 마감 원문의 내용이 핵심 요약에 반영됨", "item_ids": ["summary"],
                             "material_facts": [{"fact": "반도체가 상승을 주도했고 기술주 밖의 참여는 혼조",
                                 "quote": "Semiconductor shares led the advance, but participation outside technology was mixed.",
                                 "main_item_ids": ["summary"]}]}], **updates)


@pytest.fixture
def brief(company, monkeypatch):
    s = company.settings
    s.briefing_enabled = True
    s.briefing_publish_enabled = True
    s.briefing_search_enabled = False
    s.briefing_channel_id = "CQUANT"
    s.briefing_owner_user = "UHUMAN"
    company.roles[BRIEFER] = load_roles(Settings(briefing_enabled=True))[BRIEFER]
    clock = {"at": definition().cutoff-timedelta(minutes=1)}
    monkeypatch.setattr(schedule, "utcnow", lambda: clock["at"])
    return BriefStore(company), clock


def seed(brief, edition=None):
    store, clock = brief
    edition = edition or definition()
    clock["at"] = edition.cutoff-timedelta(minutes=1)
    store.register()
    claimed = store.claim_collection()
    assert str(claimed["id"]) == edition.id
    store.save_collection(claimed, bundle(edition))
    clock["at"] = edition.cutoff
    return edition


def complete(brief):
    store, clock = brief
    edition = seed(brief)
    first = store.prepare()
    store.commit(response(first["request"]))
    second = store.prepare()
    store.commit(response(second["request"]))
    clock["at"] = edition.due_at
    store.flush()
    return edition, first, second


def test_calendar_weekends_holidays_and_dst():
    assert schedule.editions(date(2026, 9, 20), "C", "U") == []
    saturday = schedule.editions(date(2026, 9, 19), "C", "U")
    assert [(e.kind, e.weekly) for e in saturday] == [("am", "review")]
    monday = schedule.editions(date(2026, 9, 21), "C", "U")
    assert monday[0].us_session is None and monday[0].weekly == "outlook"
    # US July 3 is a market holiday; Saturday has no new session.
    assert schedule.editions(date(2026, 7, 4), "C", "U") == []
    for day, hour in ((date(2026, 3, 6), 6), (date(2026, 3, 9), 5), (date(2026, 11, 2), 6)):
        assert schedule.close("US", day).astimezone(schedule.KST).hour == hour
    early = schedule.close("US", date(2026, 11, 27)).astimezone(schedule.NY)
    assert early.hour == 13
    us_holiday = definition(day=date(2026, 9, 8))
    assert us_holiday.us_session is None and us_holiday.kr_session


def test_calendar_official_override_delayed_close_and_closure():
    day = date(2026, 11, 19)
    change = CalendarOverride(market="KR", day=day, close_at="2026-11-19T16:30:00+09:00",
                              source_url="https://global.krx.co.kr/fixture-official-notice")
    rows = schedule.editions(day, "C", "U", {("KR", day): change})
    assert rows[-1].due_at.astimezone(schedule.KST).strftime("%H:%M") == "18:05"
    assert rows[0].due_at.astimezone(schedule.KST).strftime("%H:%M") == "07:45"
    assert [e.cutoff.astimezone(schedule.KST).strftime("%H:%M") for e in rows] == ["06:30", "16:50"]
    assert [e.starts_at.astimezone(schedule.KST).strftime("%H:%M") for e in rows] == ["06:10", "16:30"]
    later = change.model_copy(update={"close_at": datetime.fromisoformat("2026-11-19T17:00:00+09:00")})
    delayed = schedule.editions(day, "C", "U", {("KR", day): later})[-1]
    assert delayed.due_at.astimezone(schedule.KST).strftime("%H:%M") == "18:35"
    assert delayed.starts_at == later.close_at
    closed = change.model_copy(update={"closed": True, "close_at": None})
    assert [e.kind for e in schedule.editions(day, "C", "U", {("KR", day): closed})] == ["am"]


def test_numeric_validation_and_decimal_calculation():
    p = proposal()
    assert validate(p, bundle()) == {}
    assert "+1.92%" in observation_text(p.observations[0])
    bad = p.model_copy(deep=True)
    bad.observations[0].value = Decimal("9999")
    bad.observations[1].session_date = date(2026, 9, 18)
    bad.summary[0].text = "기업 이익이 9999배 증가했습니다."
    rejected = validate(bad, bundle())
    assert rejected["sp500"] == "numeric_value_not_in_evidence"
    assert rejected["nasdaq"] == "wrong_close_session"
    assert rejected["summary"] == "unsupported_prose_number"


def test_quotes_times_and_unsupported_comparison_are_rejected():
    p = proposal()
    p.summary[0].evidence[0].quote = "This original does not actually contain the alleged fact."
    p.observations[0].as_of = definition().cutoff+timedelta(minutes=1)
    p.observations[1].previous_session_date = date(2026, 9, 17)
    rejected = validate(p, bundle())
    assert rejected["summary"] == "evidence_not_in_frozen_original"
    assert rejected["sp500"] == "observation_time_stale_or_future"
    assert rejected["nasdaq"] == "incompatible_comparison"


def test_calendar_window_and_missing_watch_result_are_visible():
    b = bundle(definition("pm"))
    b["morning_watchpoints"] = [{"id": "morning", "text": "아침 원문을 바꾸지 않습니다."}]
    p = proposal().model_copy(update={"observations": [], "watchpoints": []})
    parts, quality = render(p, b)
    assert "일부 확인 중" in parts[0] and "판단 불가" in "\n".join(parts)
    assert quality["missing_core"] == ["kosdaq", "kospi"]
    assert all(len(part) <= 3500 for part in parts) and len(parts) <= 3


def test_artifact_cannot_request_a_tool_or_delegation():
    r = response({"request_id": "news-brief-fixture-write"})
    r.decision = AgentDecision(status="continue", say="", tools=[{"name": "web_read", "arguments": {"url": "https://example.org"}}])
    with pytest.raises(ValueError, match="artifact_only"):
        artifact(r, BriefProposal)


def test_instrument_unit_and_timezone_validation():
    raw = proposal().observations[0].model_dump(mode="json")
    for updates in ({"unit": "USD"}, {"value": "NaN"}, {"as_of": "2026-09-21T20:00:00"},
                    {"instrument": "nasdaq100"}, {"reported_change": 2, "change_unit": "bp"}):
        with pytest.raises(ValueError):
            MarketObservation.model_validate(raw | updates)


def test_default_off_and_manifest_identity(tmp_path):
    settings = Settings(briefing_calendar_overrides_file="")
    assert not settings.briefing_enabled and not settings.briefing_publish_enabled
    assert settings.briefing_calendar_overrides_file is None
    from quant_company.company import Company

    company = Company(settings)
    assert not company.roles[BRIEFER].active
    assert company.roles[BRIEFER].can_delegate_to == []
    manifests(company, None, tmp_path, include_market_brief=True)
    app = json.loads((tmp_path / "market_brief.json").read_text())
    assert app["display_information"]["name"] == "Analyst"
    assert app["features"]["bot_user"]["display_name"] == "analyst"
    assert app["settings"]["socket_mode_enabled"]
    assert "chat:write" in app["oauth_config"]["scopes"]["bot"]


def test_real_postgres_freezes_calls_and_one_edition(brief):
    store, clock = brief
    e = seed(brief)
    with ThreadPoolExecutor(3) as pool:
        ready = list(pool.map(lambda _: store.prepare(), range(3)))
    assert len({r["request"]["request_id"] for r in ready}) == 1
    original = response(ready[0]["request"])
    store.commit(original)
    assert store.commit(original)["duplicate"]
    changed = original.model_copy(deep=True)
    changed.decision.artifacts[0].title = "changed"
    with pytest.raises(ValueError, match="committed_brief_response_changed"):
        store.commit(changed)
    review = store.prepare()
    store.commit(response(review["request"]))
    assert store.flush()["committed"] == 0
    clock["at"] = e.due_at
    with ThreadPoolExecutor(3) as pool:
        results = list(pool.map(lambda _: store.flush(), range(3)))
    assert sum(r["committed"] for r in results) == 1
    with store.db.transaction() as conn:
        assert conn.execute("SELECT count(*) n FROM brief_messages").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) n FROM brief_calls").fetchone()["n"] == 2
        assert conn.execute("SELECT reserved FROM daily_usage").fetchone()["reserved"] == 2
        sources = conn.execute("SELECT synthetic FROM sources WHERE id LIKE 'brief:%'").fetchall()
        assert sources and all(s["synthetic"] for s in sources)


def test_collection_finishing_during_search_preserves_the_frozen_search_arguments(brief):
    store, clock = brief
    store.company.settings.briefing_search_enabled = True
    store.company.settings.company_web_enabled = True
    e = definition()
    clock["at"] = e.starts_at+timedelta(minutes=1)
    store.save_collection(store.claim_collection(), bundle(e))
    clock["at"] += timedelta(minutes=6)
    collecting = store.claim_collection()
    request = store.prepare()["request"]
    assert request["web_search"] and request["request_id"].endswith("-search")
    store.save_collection(collecting, bundle(e))
    candidate = {"url": "https://www.cnbc.com/fixture-report.html", "title": "Nasdaq market close", "snippet": "Candidate only"}
    result = ProviderResponse(request_id=request["request_id"], provider="fixture",
        web_searches=[{"id": "synthetic-search", "action": {"type": "search"}}],
        decision=AgentDecision(status="complete", say="", artifacts=[{
            "title": "Search", "content": json.dumps({"results": [candidate]}), "source_ids": []}]))
    assert store.commit(result)["state"] == "completed"
    assert store.prepare()["state"] == "idle"
    assert store.claim_collection()["candidates"][0]["url"] == candidate["url"]


def test_reviewer_rejection_reduces_coverage(brief):
    store, clock = brief
    e = seed(brief)
    store.commit(response(store.prepare()["request"]))
    critic = store.prepare()["request"]
    clock["at"] = e.due_at  # Too little time remains for repair plus a second review.
    store.commit(response(critic, review(rejected_ids=["sp500"])))
    clock["at"] = e.due_at
    store.flush()
    current = next(row for row in store.status()["editions"] if row["id"] == e.id)
    assert current["quality"]["reduced"] and current["quality"]["missing_core"] == ["sp500"]
    assert "rendered" not in current and current["collection_errors"] == []
    assert store.status(include_rendered=True)["editions"][0]["rendered"]


def test_deadline_fallback_is_committed_without_replaying_ambiguous_model(brief):
    store, clock = brief
    e = seed(brief)
    request = store.prepare()["request"]
    store.fault(request["request_id"], "outcome_unknown")
    clock["at"] = e.due_at+timedelta(minutes=10)
    store.flush()
    with store.db.transaction() as conn:
        row = conn.execute("SELECT * FROM brief_editions WHERE id=%s", (e.id,)).fetchone()
        assert row["quality"]["fallback"] == "outcome_unknown" and row["proposal"] is None
        # Owner rule (2026-10-10): no notice-only edition. With no draft, the originals' own lines ship
        # and the ambiguous call is still not replayed.
        assert row["quality"]["review_incomplete"] and "Synthetic closing report" in row["rendered"][0]
        assert "확인 지연" not in row["rendered"][0]
        assert conn.execute("SELECT count(*) n FROM brief_calls").fetchone()["n"] == 1
    assert store.commit(response(request))["state"] == "stale"
    assert store.prepare()["state"] == "idle"


def test_long_outage_marks_missed_without_backlog_post(brief):
    store, clock = brief
    e = seed(brief)
    clock["at"] = e.expires_at+timedelta(seconds=1)
    store.flush()
    with store.db.transaction() as conn:
        assert conn.execute("SELECT state FROM brief_editions WHERE id=%s", (e.id,)).fetchone()["state"] == "missed"
        assert conn.execute("SELECT count(*) n FROM outbox").fetchone()["n"] == 0


def test_late_review_is_stale_and_the_validated_draft_ships_reduced(brief):
    store, clock = brief
    e = seed(brief)
    store.commit(response(store.prepare()["request"]))
    review = store.prepare()["request"]
    clock["at"] = e.due_at+timedelta(minutes=10)
    assert store.commit(response(review))["state"] == "stale"
    store.flush()
    row = next(r for r in store.status(include_rendered=True)["editions"] if r["id"] == e.id)
    assert row["state"] == "committed" and row["quality"]["fallback"] is None and row["quality"]["reduced"]
    assert row["quality"]["review_incomplete"] and row["quality"]["salvaged_from"] == "reviewing"
    assert "미국 증시는 반도체가 주도했으며" in row["rendered"][0] and "일부 확인 중" in row["rendered"][0]


def test_invalid_review_response_gets_one_retry_and_the_edition_ships_its_body(brief):
    """2026-10-09 AM: one invalid review response ended the edition with a deadline notice."""
    store, clock = brief
    e = seed(brief)
    store.commit(response(store.prepare()["request"]))
    first = store.prepare()["request"]
    store.fault(first["request_id"], "invalid_brief_response")
    retry = store.prepare()["request"]
    assert retry["request_id"] == first["request_id"]+"-r1" and retry["prompt"] == first["prompt"]
    assert store.commit(response(retry, review()))["state"] == "completed"
    clock["at"] = e.due_at
    store.flush()
    row = next(r for r in store.status(include_rendered=True)["editions"] if r["id"] == e.id)
    assert row["state"] == "committed" and not row["quality"]["reduced"] and row["quality"]["fallback"] is None
    assert "미국 증시는 반도체가 주도했으며" in row["rendered"][0]
    calls = [(c["phase"], c["state"]) for c in store.status()["calls"]]
    assert sorted(calls) == [("review", "completed"), ("review", "retry"), ("write", "completed")]


def test_second_invalid_response_is_final_and_the_draft_ships_at_due(brief):
    store, clock = brief
    e = seed(brief)
    store.commit(response(store.prepare()["request"]))
    for _ in range(2):
        store.fault(store.prepare()["request"]["request_id"], "invalid_brief_response")
    assert store.prepare()["state"] == "idle"
    clock["at"] = e.due_at
    store.flush()
    row = next(r for r in store.status(include_rendered=True)["editions"] if r["id"] == e.id)
    assert row["state"] == "committed" and row["quality"]["salvaged_from"] == "blocked"
    assert "미국 증시는 반도체가 주도했으며" in row["rendered"][0]
    assert len(store.status()["calls"]) == 3


def test_retry_needs_the_shared_deadline_runway(brief):
    store, clock = brief
    e = seed(brief)
    store.commit(response(store.prepare()["request"]))
    request = store.prepare()["request"]
    clock["at"] = e.due_at
    store.fault(request["request_id"], "invalid_brief_response")
    with store.db.transaction() as conn:
        assert conn.execute("SELECT state FROM brief_editions WHERE id=%s", (e.id,)).fetchone()["state"] == "blocked"


def test_missing_model_receipt_remains_unresolved_after_fallback(brief):
    store, clock = brief
    e = seed(brief)
    request = store.prepare()["request"]
    clock["at"] = e.due_at+timedelta(minutes=10)
    store.flush()
    assert store.status()["calls"][0]["state"] == "unresolved"
    assert store.commit(response(request))["state"] == "stale"


def test_preview_cannot_be_republished_after_enabling(brief):
    store, clock = brief
    store.company.settings.briefing_publish_enabled = False
    e, _, _ = complete(brief)
    with store.db.transaction() as conn:
        assert conn.execute("SELECT count(*) n FROM outbox").fetchone()["n"] == 0
        assert conn.execute("SELECT state FROM brief_editions WHERE id=%s", (e.id,)).fetchone()["state"] == "previewed"
    store.company.settings.briefing_publish_enabled = True
    store.flush()
    assert not store.status()["deliveries"]


async def test_simulated_slack_parent_receipt_precedes_details_and_question_routing(brief, credentials):
    store, clock = brief
    e, _, _ = complete(brief)
    credentials[BRIEFER] = {**credentials["director"], "app_id": "ABRIEF", "bot_user_id": "UBRIEF"}
    sent = []

    def post(request):
        payload = json.loads(request.content)
        sent.append(payload)
        text = ''.join(block['text']['text'] for block in payload['blocks'])
        assert 'text' not in payload and "<@" not in text and "[지시" not in text
        return httpx.Response(200, json={"ok": True, "ts": "100.001" if len(sent) == 1 else "100.002"})

    outbox = SlackOutbox(store.company, credentials, httpx.MockTransport(post))
    assert await outbox.send_one()
    assert "thread_ts" not in sent[0]
    store.flush()
    assert await outbox.send_one()
    assert sent[1]["thread_ts"] == "100.001"
    ingress = SlackIngress(store.company.settings, store.company, credentials)
    payload = {"team_id": "TTEST", "api_app_id": "ABRIEF", "event_id": "brief-followup",
               "event": {"type": "message", "channel": "CQUANT", "user": "UHUMAN", "ts": "200.1",
                         "thread_ts": "100.001", "text": "반도체 흐름을 더 설명해줘"}}
    result = ingress.accept(BRIEFER, payload, credentials[BRIEFER])
    assert ingress.accept(BRIEFER, payload, credentials[BRIEFER])["duplicate"]
    with store.db.transaction() as conn:
        assert conn.execute("SELECT agent FROM tasks WHERE id=%s", (result["task_id"],)).fetchone()["agent"] == BRIEFER
        turn_id = str(conn.execute("SELECT id FROM turns WHERE task_id=%s", (result["task_id"],)).fetchone()["id"])
    prepared = store.company.prepare_turn(turn_id)
    assert prepared["state"] == "ready"
    assert "SPECIALIST PROCEDURE JSON" in prepared["request"]["prompt"]
    assert "brief:" in prepared["request"]["prompt"]
    payload["event"] = {**payload["event"], "ts": "200.2", "text": "금융전략 검토: 이 변화가 연구 가정에 미치는 영향을 설명해줘"}
    handoff = ingress.accept(BRIEFER, payload, credentials[BRIEFER])
    with store.db.transaction() as conn:
        assert conn.execute("SELECT agent FROM tasks WHERE id=%s", (handoff["task_id"],)).fetchone()["agent"] == "financial_strategist"


@pytest.mark.parametrize("mode", ["timeout", "missing_ts", "server_error"])
async def test_uncertain_slack_never_replays_or_creates_threads(brief, credentials, mode):
    store, _ = brief
    complete(brief)
    credentials[BRIEFER] = credentials["director"]
    calls = []

    def post(request):
        calls.append(1)
        if mode == "timeout":
            raise httpx.ReadTimeout("synthetic ambiguous response")
        return httpx.Response(500 if mode == "server_error" else 200, json={"ok": True})

    sender = SlackOutbox(store.company, credentials, httpx.MockTransport(post))
    await sender.send_one()
    for _ in range(2):
        store.flush()
        assert not await sender.send_one()
    assert len(calls) == 1
    assert store.status()["deliveries"][0]["status"] == "uncertain"


async def test_policy_checked_again_before_network_send(brief, credentials):
    store, _ = brief
    complete(brief)
    credentials[BRIEFER] = credentials["director"]
    sender = SlackOutbox(store.company, credentials)
    row = sender.claim()
    assert row
    store.company.settings.briefing_publish_enabled = False
    assert not sender.before_send(row)
    assert store.status()["deliveries"][0]["status"] == "stale"


def test_budget_and_global_pause_preserve_no_new_model_call(brief):
    store, clock = brief
    seed(brief)
    with store.db.transaction() as conn:
        conn.execute("INSERT INTO daily_usage(day,reserved) VALUES(CURRENT_DATE,1) ON CONFLICT(day) DO UPDATE SET reserved=1")
    store.company.settings.company_max_daily_turns = 1
    assert store.prepare()["reason"] == "daily_limit"
    with store.db.transaction() as conn:
        assert conn.execute("SELECT count(*) n FROM brief_calls").fetchone()["n"] == 0
    store.company.settings.company_max_daily_turns = None
    with store.db.transaction() as conn:
        conn.execute("UPDATE runtime_control SET paused_until=%s", (clock["at"]+timedelta(hours=1),))
    assert store.prepare()["state"] == "defer"
    with store.db.transaction() as conn:
        assert conn.execute("SELECT count(*) n FROM brief_calls").fetchone()["n"] == 0


def test_morning_watchpoints_are_frozen_in_afternoon(brief):
    store, clock = brief
    store.company.settings.briefing_publish_enabled = False
    complete(brief)
    seed(brief, definition("pm"))
    request = store.prepare()["request"]
    data = json.loads(request["prompt"].split("BRIEF DATA JSON:\n")[1])
    assert data["morning_watchpoints"][0]["text"] == proposal().watchpoints[0].text


def test_brief_status_is_owner_scoped_and_does_not_delegate(brief):
    store, _ = brief
    request = store.company.ingest(event_key="brief-status", owner="UHUMAN", agent=BRIEFER, text="상태")
    with store.db.transaction() as conn:
        task = conn.execute("SELECT * FROM tasks WHERE id=%s", (request["task_id"],)).fetchone()
        result = store.company._tool(conn, request["project_id"], ToolRequest(name="briefing_status", arguments={}), task=task)
        assert result["enabled"]
        project = store.company._project(conn, request["project_id"])
        with pytest.raises(PolicyError, match="Unauthorized peer"):
            store.company.validate_decision(conn, project, task, AgentDecision(status="wait", say="",
                delegations=[{"agent": "financial_strategist", "instruction": "Do unsolicited research"}]))
        with pytest.raises(PolicyError, match="cannot schedule work"):
            store.company.validate_decision(conn, project, task, AgentDecision(status="complete", say="Scheduled",
                follow_up={"at": datetime.now(UTC)+timedelta(hours=1), "instruction": "Start unsolicited research"}))


async def test_runner_preserves_frozen_quota_request(brief):
    store, clock = brief
    seed(brief)
    calls = []

    class Provider:
        async def run(self, request):
            calls.append(request)
            if len(calls) == 1:
                raise ProviderFault("quota", "synthetic", 5)
            return response(request.model_dump())

    editor = BriefEditor(store.company, Provider())
    assert (await editor.tick())["state"] == "defer"
    clock["at"] += timedelta(seconds=6)
    assert (await editor.tick())["state"] == "completed"
    assert calls[0] == calls[1]


def test_priority_released_after_ready_or_missed(brief):
    store, clock = brief
    e = seed(brief)
    assert priority_pending(store.company)
    store.commit(response(store.prepare()["request"]))
    store.commit(response(store.prepare()["request"]))
    assert not priority_pending(store.company)
    clock["at"] = e.expires_at+timedelta(seconds=1)
    store.flush()
    assert not priority_pending(store.company)

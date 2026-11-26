import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from quant_company.contracts import AgentDecision, ArtifactDraft, ProviderResponse
from quant_company.market_desk import editor
from quant_company.market_desk.runner import MarketDesk
from quant_company.providers.openai_credits import OpenAICreditLane
from quant_company.runtime import news_loops

AT = datetime(2026, 11, 26, 2, 5, tzinfo=UTC)
QUOTE = "반도체 장비의 공급 일정을 조정하는 계약을 체결했습니다."


def filing(name="시험전자", minutes=60):
    return {"id": name, "kind": "filing", "publisher": "금융감독원 DART", "company": name,
            "title": name + " 공급계약 체결", "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=12345678901234",
            "published_at": (AT - timedelta(minutes=minutes)).isoformat(), "content": (QUOTE + " ") * 8}


def market(move=True):
    return {"version": 1, "trade_date": "2026-11-26", "observed_at": AT.isoformat(),
            "quote_delay_minutes": 20, "large_companies": ["시험전자", "다른기업"], "moves": [{
                "kind": "sector", "subject": "전기전자", "id": "2026-11-26/sector/전기전자/down", "change": -1.3,
                "unit": "%p", "companies": ["시험전자"], "from_at": (AT - timedelta(minutes=10)).isoformat(),
                "observed_at": AT.isoformat()}] if move else []}


def response(request, quote=QUOTE):
    body = {"supported": True, "headline": "시험기업 계약 공시", "fact": "공급 일정이 바뀌었습니다.",
            "interpretation": "계약 시점 변경은 납품 일정에 영향을 줄 수 있습니다.", "watch": "다음 계약 변경 공시",
            "citations": [{"source_id": "시험전자", "quote": quote}]}
    return ProviderResponse(request_id=request.request_id, provider="codex", decision=AgentDecision(
        status="complete", say="", artifacts=[ArtifactDraft(title="해설", content=json.dumps(body, ensure_ascii=False))]))


def test_later_news_and_unrelated_company_cannot_explain_a_move():
    unrelated, late = filing("다른기업"), filing(minutes=2)
    result = list(editor.candidates(market(), [unrelated, late], AT))
    assert result[0]["kind"] == "move" and result[0]["docs"] == []
    assert result[0]["followups"] == [editor.public_source(late)]
    assert len(result) == 3
    packet = next(editor.candidates(market(), [filing()], AT))
    req = editor.request_for("a" * 32, packet)
    row = {"request": req.model_dump(), "packet": packet, "article": editor.initial(packet, "same", AT)}
    with pytest.raises(ValueError, match="unverified"):
        editor.edited(response(req, quote="원문에 없는 사실로 작성된 잘못된 인용입니다."), row, AT)
    assert editor.edited(response(req), row, AT)["sources"][0]["url"] == filing()["url"]


async def test_writer_is_excluded_before_the_paid_api_checks_any_credentials(tmp_path):
    lane = OpenAICreditLane(tmp_path, None, None)
    def should_never_check_budget(*args):
        pytest.fail("paid API was consulted")
    lane.configuration = should_never_check_budget
    req = editor.request_for("b" * 32, next(editor.candidates(market(), [filing()], AT)))
    assert await lane.run(req, tmp_path / "receipt", "", "primary", 1, 300) is None


class Storage:
    def __init__(self):
        self.input = market(False)
        self.board = None

    def market(self):
        return self.input

    def write(self, board):
        self.board = board


@pytest.mark.integration
async def test_invalid_krx_observation_does_not_block_company_publications(company):
    storage = Storage()
    storage.input = market()
    storage.input["moves"][0]["from_at"] = "missing time"
    desk = MarketDesk(company, AsyncMock(), storage, filings=lambda at: [], clock=lambda: AT)
    await desk.tick()
    assert storage.board["market_at"] is None
    assert storage.board["filings_at"] is not None
    assert storage.board["issues"] == []


@pytest.mark.integration
async def test_publisher_restarts_preserve_issue_and_model_allowance(company):
    storage = Storage()
    provider = AsyncMock()
    provider.run.side_effect = response
    documents = []
    def make_desk():
        return MarketDesk(company, provider, storage, filings=lambda at: documents, clock=lambda: AT)
    desk = make_desk()
    await desk.tick()
    await desk.write_article()
    assert not storage.board["issues"] and not provider.run.await_count
    documents[:] = [filing()]
    await desk.tick()
    first = storage.board["issues"][0]["id"]
    await desk.write_article()
    await make_desk().tick()
    assert storage.board["issues"][0]["status"] == "analysis"
    documents.append(filing("다른기업"))
    storage.input = market()
    await desk.tick()
    await desk.write_article()
    await make_desk().tick()
    await desk.write_article()
    assert provider.run.await_count == 2
    by_id = {i["id"]: i for i in storage.board["issues"]}
    assert by_id[first]["observed_at"] == AT.isoformat()
    assert len(by_id) == 2
    assert len({c.args[0].request_id for c in provider.run.await_args_list}) == 2
    company.settings.insight_market_bucket = "example"
    assert {"insight-market-publication", "insight-market-editor"} <= {loop.name for loop in news_loops(company, provider)}


@pytest.mark.integration
def test_home_mirrors_only_delivered_briefs_for_configured_owner(company):
    from quant_company.market_desk.store import DeskStore

    company.settings.briefing_channel_id = "desk-channel"
    company.settings.briefing_owner_user = "desk-owner"
    store = DeskStore(company)
    with company.db.transaction() as conn:
        for i, (owner, status) in enumerate([("desk-owner", "delivered"), ("desk-owner", "pending"),
                                             ("other-owner", "delivered")]):
            pid, eid, mid = uuid4(), uuid4(), uuid4()
            at = AT - timedelta(days=3-i)
            conn.execute("INSERT INTO projects(id,title,instruction,owner_user,channel) VALUES(%s,'test','test',%s,'desk-channel')",
                         (pid, owner))
            conn.execute("""INSERT INTO brief_editions(id,day,kind,channel,owner_user,definition,due_at,cutoff,
                expires_at,policy_digest,publish) VALUES(%s,%s,'am','desk-channel',%s,'{}',%s,%s,%s,'test',true)""",
                         (eid, at.date(), owner, at, at, AT))
            conn.execute("""INSERT INTO messages(id,project_id,revision,author,kind,text)
                VALUES(%s,%s,1,'market_brief','briefing',%s)""", (mid, pid, f"edition {i}"))
            conn.execute("""INSERT INTO brief_messages(id,edition_id,part,policy_digest,expires_at)
                VALUES(%s,%s,0,'test',%s)""", (mid, eid, AT))
            conn.execute("""INSERT INTO outbox(id,project_id,revision,agent,channel,text,status,sent_ts)
                VALUES(%s,%s,1,'market_brief','desk-channel','test',%s,%s)""",
                         (mid, pid, status, str(at.timestamp()) if status == "delivered" else None))
        assert [r["body"] for r in store.briefs(conn, AT)] == ["edition 0"]

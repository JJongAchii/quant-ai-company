import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from psycopg.types.json import Jsonb

from quant_company.briefing.schedule import KST
from quant_company.contracts import AgentDecision, ArtifactDraft, ProviderResponse
from quant_company.market_desk import lead
from quant_company.market_desk.runner import MarketDesk

CLOSE = datetime(2026, 11, 26, 15, 30, tzinfo=KST)
PRIOR = CLOSE - timedelta(days=1)
EARLY = CLOSE.replace(hour=10, minute=50)
NOW = CLOSE + timedelta(minutes=45)
PRIOR_LINE = "코스피 지수는 전장보다 100.00포인트(4.00%) 내린 2,400.00로 장을 마쳤다."
CLOSE_LINE = "코스피 지수는 전장보다 10.00포인트(0.42%) 오른 2,410.00로 장을 마쳤다."
EARLY_LINE = "오전 유가증권시장에서 외국인은 순매수로 출발했다."
FLOW_LINE = "마감 집계에서 기관은 순매수했고 외국인은 순매도로 돌아섰다."


def doc(key, at, title, content):
    return {"id": key, "published_at": at, "collected_at": at, "title": title, "publisher": "시장통신사",
            "url": "https://example.org/" + key, "content": (content + "\n") * 45}


def originals():
    return [doc("prior", PRIOR + timedelta(minutes=20), "코스피, 전일 급락 마감", PRIOR_LINE),
            doc("early", EARLY, "코스피, 오전 상승세", EARLY_LINE),
            doc("last", CLOSE + timedelta(minutes=30), "코스피, 반등 마감", CLOSE_LINE + " " + FLOW_LINE)]


def draft(request, phase="close"):
    current = {"source_id": "last", "quote": CLOSE_LINE} if phase == "close" else {"source_id": "early", "quote": EARLY_LINE}
    content = {"supported": True, "headline": "코스피, 외국인 수급이 갈린 반등",
               "reading": {"text": "코스피는 전일 낙폭의 일부를 만회하는 흐름이다.",
                           "citations": [current, {"source_id": "prior", "quote": PRIOR_LINE}]},
               "qualification": {"text": "오전에 외국인이 순매수했다는 점도 함께 봐야 한다.",
                                 "citations": [{"source_id": "early", "quote": EARLY_LINE}]},
               "watch": "외국인 매수가 다음 집계에도 이어지는지 확인한다."}
    return ProviderResponse(request_id=request.request_id, decision=AgentDecision(status="complete", say="",
                            artifacts=[ArtifactDraft(title="시장 해설", content=json.dumps(content, ensure_ascii=False))]))


def test_session_packet_rejects_flash_future_and_incompatible_quotes():
    rows = originals()
    assert lead.packet_for(rows, CLOSE, CLOSE, PRIOR, None) is None
    assert lead.packet_for(rows, EARLY, CLOSE, PRIOR, {"observed_at": (EARLY-timedelta(hours=2)).isoformat()}) is None
    assert lead.packet_for([{**r, "collected_at": NOW + timedelta(hours=1)} for r in rows], NOW, CLOSE, PRIOR, None) is None
    later_policy = doc("policy", NOW, "기준금리 추가 발표", "정책 당국이 기자회견을 열었다.")
    packet = lead.packet_for([*rows, later_policy], NOW, CLOSE, PRIOR, None)
    assert [d["id"] for d in packet["docs"]] == ["last", "early", "prior"]
    assert lead.packet_for([{**rows[0], "title": "코스닥, 하락 마감"}, rows[-1]], NOW, CLOSE, PRIOR, None) is None
    req = lead.request_for("fixture", packet)
    row = {"packet": packet, "request": req.model_dump(mode="json")}
    response = draft(req)
    assert lead.edited(response, row, NOW)["comparison"]["share_pct"] == 10
    result = json.loads(response.decision.artifacts[0].content)
    result["reading"]["text"] += " 상승률은 99%였다."
    response.decision.artifacts[0].content = json.dumps(result)
    with pytest.raises(ValueError, match="unsupported figure"):
        lead.edited(response, row, NOW)
    assert not lead._supported_figures("100% 하락했다.", [PRIOR_LINE])
    mismatch = [dict(d, content=d["content"].replace("2,410.00", "2,430.00")) for d in packet["docs"]]
    assert lead.close_comparison(mismatch, "코스피", "") is None
    assert lead.close_comparison(packet["docs"], "코스닥 마감", "") is None


@pytest.mark.integration
@pytest.mark.parametrize("closing_run", [NOW, CLOSE + timedelta(hours=6)])
async def test_intraday_and_close_share_two_requests_and_survive_restarts(company, closing_run):
    with company.db.transaction() as conn:
        conn.execute("INSERT INTO news_sources(id,config,config_digest,enabled) VALUES('wire',%s,'ok',true)",
                     (Jsonb({"publisher": "시장통신사", "use_for_summary": True}),))
        after_cutoff = CLOSE + timedelta(hours=3, minutes=1)
        delayed = {**doc("delayed", NOW, "코스피, 뒤늦게 수집한 마감", CLOSE_LINE), "collected_at": after_cutoff}
        for row in [*originals(), delayed, doc("night", after_cutoff, "코스피, 저녁 반등 마감", CLOSE_LINE)]:
            conn.execute("""INSERT INTO news_articles(id,source_id,url,title,summary,feed_digest,published_at,
                collected_at,source_digest,state,content) VALUES(%s,'wire',%s,%s,'',%s,%s,%s,'ok','ready',%s)""",
                         (row["id"], row["url"], row["title"], row["id"], row["published_at"], row["collected_at"], row["content"]))
    at = EARLY + timedelta(minutes=5)

    class Storage:
        def market(self):
            return {"version": 1, "trade_date": at.date().isoformat(), "observed_at": at.isoformat(),
                    "quote_delay_minutes": 20, "large_companies": [], "moves": [],
                    "session": {"observed_at": at.isoformat(), "headline": "장중 시황", "market": "두 지수 상승", "sectors": "", "watch": ""}}

        def write(self, board):
            self.board = board

    storage, provider = Storage(), AsyncMock()
    for phase, now in (("intraday", at), ("close", closing_run)):
        at = now
        provider.run.side_effect = lambda request, phase=phase: draft(request, phase)
        desk = MarketDesk(company, provider, storage, filings=lambda _: [], clock=lambda at=at: at)
        await desk.tick()
        await desk.write_article()
        if phase == "close":
            article = storage.board["lead"]
            assert datetime.fromisoformat(article["cutoff"]) == min(closing_run, CLOSE + timedelta(hours=3))
            assert datetime.fromisoformat(article["as_of"]) == CLOSE + timedelta(minutes=30)
            assert datetime.fromisoformat(article["published_at"]) == closing_run
        await desk.tick()
        await desk.write_article()
        assert storage.board["lead"]["phase"] == phase and not storage.board["issues"]
    assert provider.run.await_count == 2
    assert lead.phase_at(CLOSE + timedelta(days=1), CLOSE) is None

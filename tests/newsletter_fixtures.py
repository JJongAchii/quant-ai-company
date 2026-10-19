"""Synthetic 10/19 PM edition for the evening brief email, shaped like the delivered brief (tests/test_briefing.py);
every number of the approved v5 mockup is in its text. Fixture data only, never a real edition."""

from datetime import date, datetime, timedelta

from psycopg.types.json import Jsonb

from quant_company.briefing import schedule
from quant_company.briefing.contracts import BriefProposal, SourceDocument
from quant_company.briefing.editor import render
from quant_company.contracts import AgentDecision, ArtifactDraft, ProviderFault, ProviderResponse
from quant_company.newsletter.contracts import NewsletterDraft

from .test_briefing import definition

PM_DAY = date(2026, 10, 19)
KST = schedule.KST
CLOSE = ("10월 19일 코스피는 3.17% 하락한 5,488.41로 출발했으나 1.76% 오른 5,768.01에 장을 마쳤다. 2거래일 연속 상승이다. "
         "코스닥은 2.73% 하락 출발 후 1.78% 오른 808.89로 마감했다. 유가증권시장에서 기관은 5,731억원을 순매수했고 "
         "외국인은 357억원, 개인은 1조869억원을 순매도했다.")


def pm_bundle(edition=None):
    edition = edition or definition("pm", PM_DAY)
    doc = SourceDocument(id="close-1", url="https://www.yna.co.kr/view/fixture-kospi-close", title="증시 마감",
        publisher="Synthetic wire", kind="media", content=CLOSE, published_at=edition.cutoff - timedelta(minutes=30),
        retrieved_at=edition.cutoff - timedelta(minutes=5), sha256="c" * 64, registration="fixture",
        receipt={"synthetic": True})
    return {"edition": edition.model_dump(mode="json"), "documents": [doc.model_dump(mode="json")],
            "collection_errors": [], "morning_watchpoints": []}


def claim(identity, text, kind="fact"):
    return {"id": identity, "text": text, "kind": kind, "evidence": [{"source_id": "close-1", "quote": CLOSE[:200]}]}


def observed(edition, instrument, value, change=None):
    unit = "억원" if instrument.startswith("kr_") else "pt"
    return {"id": instrument, "instrument": instrument, "value": value, "unit": unit, "session_date": str(edition.day),
            "as_of": schedule.close("KR", edition.day).isoformat(), "basis": "close", "venue": "KRX",
            "evidence": [{"source_id": "close-1", "quote": CLOSE[:200]}],
            **({"reported_change": change, "change_unit": "%"} if change else {})}


def event(identity, title, day, hour=9):
    at = datetime(2026, 10, day, hour, tzinfo=KST)
    return {"id": identity, "title": title, "at": at.isoformat(), "source_timezone": "Asia/Seoul", "status": "scheduled",
            "evidence": [{"source_id": "close-1", "quote": CLOSE[:200]}]}


def issue(n, headline, fact, interpretation, check, counterpoint=None, context=()):
    return {"headline": headline, "fact": claim(f"fact{n}", fact),
            "interpretation": claim(f"view{n}", interpretation, "interpretation"),
            "next_check": claim(f"check{n}", check, "condition"),
            "analysis": {"horizon": "days_weeks", "causal_basis": "reported_explanation",
                         "mechanism": claim(f"mechanism{n}", "조달 비용과 수요 전망이 함께 주가 기대에 반영됩니다.", "interpretation"),
                         "alternative": claim(f"alternative{n}", "단기 수급 요인이 더 크게 작용했을 수도 있습니다.", "interpretation")},
            **({"counterpoint": claim(f"counter{n}", counterpoint, "interpretation")} if counterpoint else {}),
            "context": [claim(f"context{n}{i}", text) for i, text in enumerate(context)]}


def pm_proposal(edition=None, issues=None):
    edition = edition or definition("pm", PM_DAY)
    default = [
        issue(1, "AI 투자 자금의 조달 비용 상승",
              "AI 개발사 앤트로픽은 구글의 AI 반도체(TPU) 구매를 위해 600억달러 조달을 추진하며 일부 채권에 연 10%가 넘는 금리를 제시했습니다.",
              "외신은 신용도가 비슷한 기업의 통상 금리(약 7%)보다 높아 AI 인프라 투자의 위험 프리미엄이 상승한 것으로 해석했습니다.",
              "21일 메타 실적에서 투자비를 뒷받침하는 현금창출이 확인되는지 봅니다.",
              "현재까지 집계된 미국 S&P500 기업의 3분기 이익은 전년 대비 29.6% 증가했습니다."),
        issue(2, "중국 성장률 예상 상회, 소비·부동산은 부진",
              "중국의 3분기 국내총생산(GDP)은 전년 동기 대비 4.5% 증가해 예상치(4.4%)를 웃돌았습니다.",
              "생산이 성장을 이끌었지만 소비와 건설 수요는 약해 한국 소비재·소재 기업의 판매 회복이 늦어질 수 있습니다.",
              "중국 소매판매 개선과 부동산 투자 감소폭 축소 여부를 확인합니다.",
              "한국의 9월 자동차 수출은 5.5% 감소했으며, 아시아 수출이 60.0% 줄어든 반면 북미는 9.2% 늘었습니다.",
              ["1~9월 부동산 투자는 20.6% 감소해 감소폭이 확대됐습니다."]),
        issue(3, "유가 하락에도 남아 있는 공급 위험",
              "브렌트유는 1.80% 하락한 배럴당 107.88달러를 기록했습니다.",
              "러시아 석유 터미널과 사우디아라비아 인근 유조선 두 척이 공격을 받는 등 공급 위험은 이어지고 있습니다.",
              "피격 터미널의 선적 정상화 여부를 확인합니다.",
              context=["미국이 러시아산 에너지 구매국에 대한 신규 관세를 유예하면서 공급 확대 기대가 반영됐습니다."]),
    ]
    return BriefProposal.model_validate({
        "summary": [claim("summary1", "코스피는 3.17% 하락한 5,488.41로 출발했으나 낙폭을 모두 만회하고 1.76% 오른 5,768.01에 마감했습니다."),
                    claim("summary2", "유가증권시장에서 기관은 5,731억원을 순매수했고 외국인은 357억원, 개인은 1조869억원을 순매도했습니다.")],
        "overview": [claim("overview1", "2거래일 연속 상승입니다. 코스닥도 2.73% 하락 출발 후 1.78% 오른 808.89에 마감했습니다."),
                     claim("overview2", "외국인은 장 초반 순매수에서 오전 중 순매도로 전환했으며, 이달 2일부터 8거래일 연속 18조1,308억원을 순매도했습니다.")],
        "observations": [observed(edition, "kospi", "5768.01", "1.76"), observed(edition, "kosdaq", "808.89", "1.78"),
                         observed(edition, "kr_institution", "5731"), observed(edition, "kr_foreign", "-357")],
        "issues": default if issues is None else issues,
        "internals": [claim("internal1", "장중에는 에너지저장장치(ESS) 관련 이차전지와 바이오 업종으로 순환매가 나타났습니다. "
                                         "거래대금 상위 종목은 SK하이닉스(+0.16%), 삼성전자(+1.16%)였습니다."),
                      claim("internal2", "현대로템은 K2 전차 주포 생산 역량을 갖춘 현대위아 특수사업부를 약 3,584억원에 인수하기로 결정했으며, "
                                         "거래는 11월 27일 현대위아 임시 주주총회 특별결의를 거쳐 진행됩니다.")],
        "watchpoints": [], "watch_results": [],
        "calendar": [event("cal1", "넷플릭스 실적 · 미국 주택착공", 20, 6), event("cal2", "메타 · 테슬라 실적", 21, 6),
                     event("cal3", "미국 신규 실업수당 청구", 22, 21), event("cal4", "미시간대 소비심리 · 기대인플레이션", 23, 23),
                     event("cal5", "미국 연준 통화정책 회의(27~28일)", 27, 3)]})


def pm_parts(proposal=None):
    edition = definition("pm", PM_DAY)
    proposal = pm_proposal(edition) if proposal is None else proposal
    return render(proposal, pm_bundle(edition))[0]


def pm_row(proposal=None):
    """A brief_editions row as the PM commit sees it (no database)."""
    edition = definition("pm", PM_DAY)
    proposal = pm_proposal(edition) if proposal is None else proposal
    return {"id": edition.id, "day": PM_DAY, "kind": "pm", "publish": True, "rendered": pm_parts(proposal),
            "proposal": proposal.model_dump(mode="json")}


def commit_pm(brief, proposal=None, *, fallback=None):
    """The real PM commit (BriefStore.flush) of the fixture edition; returns the edition definition."""
    store, clock = brief
    edition = definition("pm", PM_DAY)
    clock["at"] = edition.cutoff - timedelta(minutes=1)
    store.register()
    data = pm_bundle(edition)
    proposal = pm_proposal(edition) if proposal is None and not fallback else proposal
    parts, quality = render(proposal, data, fallback=fallback)
    with store.db.transaction() as conn:
        conn.execute("""UPDATE brief_editions SET state='ready',bundle=%s,proposal=%s,rendered=%s,quality=%s,review=%s
            WHERE id=%s""", (Jsonb(data), Jsonb(proposal.model_dump(mode="json")) if proposal else None, Jsonb(parts),
                             Jsonb(quality), Jsonb({"verdict": "publish"}), edition.id))
    clock["at"] = edition.due_at
    store.flush()
    return edition


def deliver_root(store, edition_id, ts="200.000"):
    """Simulated Slack receipt of the brief's root post, as video tests do."""
    with store.db.transaction() as conn:
        root = conn.execute("SELECT id FROM brief_messages WHERE edition_id=%s AND part=0", (edition_id,)).fetchone()
        conn.execute("UPDATE outbox SET status='delivered',sent_ts=%s WHERE id=%s", (ts, root["id"]))
        conn.execute("UPDATE projects SET thread_ts=%s WHERE id=(SELECT project_id FROM brief_editions WHERE id=%s)",
                     (ts, edition_id))


def v5_draft(**changes):
    """The approved v5 mockup's content as a writer would return it."""
    value = {
        "headline": "코스피, 3.2% 급락 출발 후", "accent": "1.8% 상승 마감",
        "preview": "기관 5,731억원 순매수, 외국인은 마감까지 순매도. AI 투자 자금의 조달 비용 상승은 부담 요인입니다.",
        "lead": "기관은 5,731억원을 순매수했고, 외국인은 마감까지 순매도를 이어갔습니다. AI 투자 자금의 조달 비용 상승은 여전히 부담 요인입니다.",
        "market": {
            "heading": "장중 낙폭을 모두 만회한 코스피",
            "swing": {"index": "코스피", "open_level": "5,488.41", "open_change": "-3.17%", "close_level": "5,768.01",
                      "close_change": "+1.76%"},
            "flow": "코스피는 3.17% 하락한 5,488.41로 출발했으나 낙폭을 모두 만회하고 1.76% 오른 5,768.01에 마감했습니다. "
                    "2거래일 연속 상승입니다. 코스닥도 2.73% 하락 출발 후 1.78% 오른 808.89에 마감했습니다.",
            "investors": [{"name": "기관", "amount": "+5,731억"}, {"name": "외국인", "amount": "-357억"},
                          {"name": "개인", "amount": "-1조 869억"}],
            "investors_text": "유가증권시장에서 기관은 순매수, 개인과 외국인은 순매도했습니다.",
            "investors_note": "외국인은 장 초반 순매수에서 오전 중 순매도로 전환했습니다. 이달 2일부터 8거래일 연속 18조원 넘게 "
                              "순매도한 흐름이 아직 돌아서지 않아, 수급 회복은 엇갈린 것으로 볼 수 있습니다.",
            "sectors": "장중에는 에너지저장장치(ESS) 관련 이차전지와 바이오 업종으로 순환매가 나타났습니다. "
                       "거래대금 상위 종목은 SK하이닉스(+0.16%), 삼성전자(+1.16%)였습니다.",
            "glossary": [{"term": "순매수", "meaning": "매수 금액에서 매도 금액을 뺀 값으로, 음수이면 매도 우위를 뜻합니다."},
                         {"term": "순환매", "meaning": "투자 자금이 한 업종에서 다른 업종으로 옮겨 가는 현상입니다."}]},
        "issues": [
            {"headline": "AI 투자 자금의 조달 비용 상승",
             "body": "AI 개발사 앤트로픽은 구글의 AI 반도체(TPU) 구매를 위해 600억달러 조달을 추진하며, 이 가운데 일부 채권에 연 10%가 "
                     "넘는 금리를 제시했습니다. 신용도가 비슷한 기업의 통상 금리(약 7%)보다 높아, 외신은 AI 인프라 투자의 위험 "
                     "프리미엄(투자자가 위험을 감수하는 대가로 요구하는 추가 수익)이 상승한 것으로 해석했습니다.",
             "implication": "AI 투자금 회수에 대한 우려는 국내 반도체 기업에 대한 기대에도 부담으로 작용하고 있습니다. 반면 현재까지 "
                            "집계된 미국 S&P500 기업의 3분기 이익은 전년 대비 29.6% 증가해, 이익 확대가 이자 부담을 흡수할 수 "
                            "있다는 시각도 있습니다.",
             "check_kind": "일정", "check": "21일 메타 실적 · 투자비를 뒷받침하는 현금창출 여부"},
            {"headline": "중국 성장률 예상 상회, 소비·부동산은 부진",
             "body": "중국의 3분기 국내총생산(GDP)은 전년 동기 대비 4.5% 증가해 예상치(4.4%)를 웃돌았습니다. 다만 생산이 성장을 "
                     "이끈 반면, 1~9월 부동산 투자는 20.6% 감소해 감소폭이 확대됐습니다.",
             "implication": "중국의 소비와 건설 수요가 약하면 한국 소비재·소재 기업의 판매 회복은 지연될 수 있습니다. 한국의 9월 "
                            "자동차 수출은 5.5% 감소했으며, 아시아 수출이 60.0% 줄어든 반면 북미는 9.2% 늘어 지역별 차이가 컸습니다.",
             "check_kind": "지표", "check": "중국 소매판매 개선과 부동산 투자 감소폭 축소 여부"},
            {"headline": "유가 하락에도 남아 있는 공급 위험",
             "body": "브렌트유는 1.80% 하락한 배럴당 107.88달러를 기록했습니다. 미국이 러시아산 에너지 구매국에 대한 신규 관세를 "
                     "유예하면서 공급 확대 기대가 반영됐습니다. 그러나 러시아 석유 터미널과 사우디아라비아 인근 유조선 두 척이 공격을 "
                     "받는 등 공급 위험은 이어지고 있습니다.",
             "implication": "유가 하락은 국내 기업의 원가 부담을 낮추는 요인이지만, 실제 공급 확대는 아직 확인되지 않았습니다.",
             "check_kind": "사항", "check": "피격 터미널의 선적 정상화 여부"}],
        "company": "현대로템은 K2 전차 주포 생산 역량을 갖춘 현대위아 특수사업부를 약 3,584억원에 인수하기로 결정했습니다. 거래는 "
                   "11월 27일 현대위아 임시 주주총회 특별결의를 거쳐 진행됩니다.",
        "calendar": [{"when": "20일 (화)", "what": "넷플릭스 실적 · 미국 주택착공"},
                     {"when": "21일 (수)", "what": "메타 · 테슬라 실적", "note": "AI 투자 부담 관련 확인"},
                     {"when": "22일 (목)", "what": "미국 신규 실업수당 청구"},
                     {"when": "23일 (금)", "what": "미시간대 소비심리 · 기대인플레이션"},
                     {"when": "27~28일", "what": "미국 연준 통화정책 회의"}]}
    for path, new in changes.items():
        target, *keys = path.split("__")
        node = value
        for key in [target, *keys][:-1]:
            node = node[int(key)] if isinstance(node, list) else node[key]
        last = [target, *keys][-1]
        if isinstance(node, list):
            node[int(last)] = new
        else:
            node[last] = new
    return NewsletterDraft.model_validate(value)


class SimulatedProvider:
    """claude-runtime stand-in: returns queued drafts or faults; no model, no network."""

    def __init__(self, *outputs):
        self.outputs, self.calls = list(outputs), []

    async def run(self, request):
        self.calls.append(request)
        output = self.outputs.pop(0) if self.outputs else v5_draft()
        if isinstance(output, ProviderFault):
            raise output
        content = output if isinstance(output, str) else output.model_dump_json()
        return ProviderResponse(request_id=request.request_id, provider="claude",
                                usage={"actual_model": request.model, "output_tokens": 1},
                                decision=AgentDecision(status="complete", say="저녁 브리핑 메일 작성 완료",
                                                       artifacts=[ArtifactDraft(title="Evening brief email", content=content)]))

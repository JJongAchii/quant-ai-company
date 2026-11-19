"""The instruction the service gives the Analyst for one trading signal."""

import json

from .contracts import Slot
from .render import KIND_WORDS, clock, day_text, window

INTRADAY_ONLY = "밤사이 정보는 대부분 시초가에 반영된다. 시가 이후의 움직임을 판단하라."
FINAL_NOW = "도구 없이 지금 최종 답을 내세요"


def final_note(reason: str) -> str:
    """The service's note before a turn without tools: after a format failure, the tool cutoff or the tool-turn cap."""
    return f"서비스 안내: {FINAL_NOW}. 사유: {reason}"


def instruction(slot: Slot, source_id: str, text: str) -> str:
    kind = KIND_WORDS[slot.kind]
    baselines = ("항상 롱(장중): 코스피 48.6%, KODEX 200 49.9%.\n"
                 "직전 미국(SPY) 장 방향 따라가기(장중): 코스피 47.1%, KODEX 200 47.7%.\n"
                 "같은 미국 규칙은 갭 방향은 75.5%, 종가 대 종가는 62.9% 맞는다.\n"
                 "항상 롱(오버나잇): 코스피 59.4%(하루 +8.7bp).")
    if slot.kind == "intraday":
        trade = (f"{day_text(slot.day)} 시가(KST {clock(slot.entry_at)})에 진입해 같은 날 종가"
                 f"(KST {clock(slot.exit_at)})에 청산하는 코스피 거래")
        extra = INTRADAY_ONLY + "\n"
    else:
        trade = (f"{day_text(slot.day)} 종가(KST {clock(slot.entry_at)})에 진입해 {day_text(slot.exit_day)} "
                 f"시가(KST {clock(slot.exit_at)})에 청산하는 코스피 거래")
        extra = ""
    example = json.dumps({
        "kind": slot.kind, "session_day": str(slot.day), "direction": "long", "confidence": 55,
        "reasons": [{"text": "근거 한 줄", "source_ids": [source_id]}],
        "invalidation": "틀렸다고 볼 조건 한 줄", "source_ids": [source_id]}, ensure_ascii=False)
    return f"""{kind} 신호 업무다. 아래 거래가 오를지 내릴지 롱 또는 숏 중 하나를 반드시 고른다.
거래: {trade} ({window(slot)}).
신호는 KST {clock(slot.send_at)}에 사장님께 전달된다. 도구는 KST {clock(slot.tools_until)}까지만 쓴다. 그 뒤에는 도구 없이 최종 답을 낸다.
이 글은 사장님의 판단을 돕는 분석이며 주문이 아니다.
{extra}
과거 적중률(로컬 자료: 코스피 2010-01~2026-07-21, KODEX 200 2012-01~):
{baselines}

기준 시세 스냅샷 (source_id: {source_id}):
{text}

출력: status는 complete, artifacts는 정확히 하나. 그 content는 SignalProposal JSON이다.
필드: kind("{slot.kind}"), session_day("{slot.day}"), direction("long" 또는 "short"), confidence(50~90 정수),
reasons(1~3개, 각 {{"text", "source_ids"}}), invalidation(틀렸다고 볼 조건), source_ids(인용한 모든 ID).
artifact의 source_ids는 비워 둔다. ID는 JSON 안에만 쓴다.
형식 예시(모양만 보이는 것이며 방향 제안이 아니다):
{example}
모든 근거는 승인된 source ID를 하나 이상 인용하고, 스냅샷 ID {source_id}는 항상 인용한다."""

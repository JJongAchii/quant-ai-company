"""The instruction the service gives the Analyst for one trading signal."""

import json

from .contracts import EVEN_CONFIDENCE, MAX_CONFIDENCE, MAX_REASON_POINTS, Slot
from .render import KIND_WORDS, clock, day_text, window

INTRADAY_ONLY = "밤사이 정보는 대부분 시초가에 반영된다. 시가 이후의 움직임을 판단하라."
FINAL_NOW = "도구 없이 지금 최종 답을 내세요"


def final_note(reason: str) -> str:
    """The service's note before a turn without tools: after a format failure, the tool cutoff or the tool-turn cap."""
    return f"서비스 안내: {FINAL_NOW}. 사유: {reason}"


def instruction(slot: Slot, source_id: str, text: str) -> str:
    kind = KIND_WORDS[slot.kind]
    if slot.kind == "intraday":
        trade = (f"{day_text(slot.day)} 시가(KST {clock(slot.entry_at)})에 진입해 같은 날 종가"
                 f"(KST {clock(slot.exit_at)})에 청산하는 코스피 거래")
        extra = INTRADAY_ONLY + "\n"
    else:
        trade = (f"{day_text(slot.day)} 종가(KST {clock(slot.entry_at)})에 진입해 {day_text(slot.exit_day)} "
                 f"시가(KST {clock(slot.exit_at)})에 청산하는 코스피 거래")
        extra = ""
    # A template, not a proposal: no direction or number in it can anchor the answer (the old example's 55 did).
    template = json.dumps({
        "kind": slot.kind, "session_day": str(slot.day), "direction": "long 또는 short",
        "reasons": [{"text": "오늘 근거 한 줄", "points": f"-{MAX_REASON_POINTS}~+{MAX_REASON_POINTS} 정수",
                     "source_ids": [source_id]}],
        "invalidation": "틀렸다고 볼 조건 한 줄", "source_ids": [source_id]}, ensure_ascii=False)
    most = MAX_CONFIDENCE - EVEN_CONFIDENCE
    return f"""{kind} 신호 업무다. 아래 거래가 오를지 내릴지 롱 또는 숏 중 하나를 반드시 고른다.
거래: {trade} ({window(slot)}).
신호는 KST {clock(slot.send_at)}에 사장님께 전달된다. 도구는 KST {clock(slot.tools_until)}까지만 쓴다. 그 뒤에는 도구 없이 최종 답을 낸다.
이 글은 사장님의 판단을 돕는 분석이며 주문이 아니다.
{extra}
기준 시세 스냅샷 (source_id: {source_id}):
{text}

방향과 확신도는 오늘의 상황과 근거로 정한다. 과거 적중률이나 "보통 오른다" 같은 경향은 시작점도 근거도 아니다.
확신도는 오늘 이 신호가 맞을 가능성이고 서비스가 계산한다: 반반({EVEN_CONFIDENCE})에 오늘 근거별 points를 더한다.
points는 그 근거가 오늘 이 신호가 맞을 가능성을 몇 %p 높이는지다(-{MAX_REASON_POINTS}~+{MAX_REASON_POINTS} 정수).
강하고 직접적인 근거는 크게, 약하거나 이미 가격에 반영된 근거는 작게, 고른 방향에 불리한 근거는 음수로 적는다.
points 합이 0보다 작으면 근거가 반대 방향을 가리키므로 그 방향은 고를 수 없고, 합은 +{most}을 넘지 않는다(확신도 최대 {MAX_CONFIDENCE}).

출력: status는 complete, artifacts는 정확히 하나. 그 content는 SignalProposal JSON이다.
필드: kind("{slot.kind}"), session_day("{slot.day}"), direction("long" 또는 "short"),
reasons(1~3개, 각 {{"text"(200자 이하), "points", "source_ids"}}), invalidation(틀렸다고 볼 조건, 200자 이하),
source_ids(인용한 모든 ID).
confidence 필드는 쓰지 않는다. artifact의 source_ids는 비워 둔다. ID는 JSON 안에만 쓴다.
형식(자리 표시만 있는 틀이며 방향이나 점수 제안이 아니다. direction과 points에는 실제 값을 쓴다):
{template}
모든 근거는 승인된 source ID를 하나 이상 인용하고, 스냅샷 ID {source_id}는 항상 인용한다."""

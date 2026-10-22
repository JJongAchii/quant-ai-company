"""Posts in the analytics channel: the three-day card, the seven-day update, the Monday review with the owner's
lesson buttons, and the binding checks for those buttons."""

import json

from ..briefing.contracts import BRIEFER
from ..company import PolicyError
from .analytics_metrics import KST
from .analytics_store import AnalyticsStore

EDITIONS = {"am": "아침", "close": "마감", "shorts": "Shorts", None: "기타"}
# Analytics API insightTrafficSourceType names as Studio shows them.
TRAFFIC = {"SUBSCRIBER": "탐색(홈·구독)", "YT_SEARCH": "검색", "RELATED_VIDEO": "추천 영상", "YT_CHANNEL": "채널 페이지",
           "EXT_URL": "외부", "NOTIFICATION": "알림", "PLAYLIST": "재생목록", "NO_LINK_OTHER": "직접·기타",
           "YT_OTHER_PAGE": "기타 YouTube", "END_SCREEN": "최종 화면", "SHORTS": "Shorts", "HASHTAGS": "해시태그",
           "ADVERTISING": "광고", "ANNOTATION": "카드", "NO_LINK_EMBEDDED": "퍼가기"}
STAGES = {"impressions": "노출", "ctr": "클릭", "hook": "초반 30초", "scenes": "장면", "value": "가치", "search": "검색"}
CONFIDENCE = {"low": "낮음", "medium": "중간", "high": "높음"}
SCOPES = {"title": "제목", "thumbnail": "썸네일", "opening": "오프닝", "structure": "구성", "length": "길이",
          "keywords": "키워드", "shorts": "쇼츠"}
VERDICTS = {"keep": "유지", "retire": "은퇴 제안", "too_early": "판단 이르음"}
LABELS = {"adopt": "채택", "reject": "거절", "retire": "은퇴"}
TARGETS = {"title": "제목", "description": "설명 첫 줄", "tags": "태그 추가"}
OUTLIER_NAMES = {"views": "조회", "ctr": "CTR", "hook_30s": "30초 유지"}
THREAD_HINT = "자세한 진단은 이 스레드에 이어집니다. 스레드에 질문이나 지시를 남기면 답합니다."
WAIT_TEXT = "지금 영상을 만드는 중이라, 제작이 끝나면 이 스레드에 답할게요."
EXPIRED_TEXT = "영상 제작이 길어져 이 질문에 답하지 못했어요. 다시 물어봐 주세요."
LESSON_HINT = "아래 버튼으로 채택하거나 거절해 주세요. 채택한 교훈은 다음에 만드는 회차부터 반영됩니다."


def _pct(value, digits=1):
    return f"{value * 100:.{digits}f}%"


def _versus(value, base, ratio=False):
    if value is None:
        return ""
    if base is None:
        return " (기준 부족)"
    if ratio:
        return f" (기준 {_pct(base)})"
    return f" (기준 대비 {(value / base - 1) * 100:+.0f}%)" if base else ""


def _clock(seconds):
    seconds = round(seconds or 0)
    return f"{seconds // 60}:{seconds % 60:02}"


def headline(row):
    day = row["published_at"].astimezone(KST)
    return f"{day.month}/{day.day} {EDITIONS.get(row['edition_kind'], '기타')} · {row['title']}"


def metric_lines(metrics, base):
    lines = []
    if metrics.get("impressions") is None:
        lines.append("노출·CTR: 아직 YouTube 보고서가 없습니다")
    else:
        lines.append(f"노출 {metrics['impressions']:,}{_versus(metrics['impressions'], base.get('impressions'))}"
                     + (f" · CTR {_pct(metrics['ctr'])}{_versus(metrics['ctr'], base.get('ctr'), True)}"
                        if metrics.get("ctr") is not None else ""))
    views = metrics.get("views")
    parts = [f"조회 {views:,}{_versus(views, base.get('views'))}" if views is not None else "조회 확인 불가"]
    if metrics.get("avg_view_s") is not None:
        parts.append(f"평균 시청 {_clock(metrics['avg_view_s'])} ({metrics.get('avg_view_pct') or 0:.1f}%)")
    if metrics.get("hook_30s") is not None:
        parts.append(f"30초 유지 {metrics['hook_30s']:.0%}")
    if metrics.get("subscribers") is not None:
        parts.append(f"구독 {metrics['subscribers']:+d}")
    lines.append(" · ".join(parts))
    return lines


def window_line(row, base):
    kind = EDITIONS.get(row["edition_kind"], "기타")
    return (f"업로드 후 3일({row['window_start']:%m/%d}~{row['window_end']:%m/%d}, 태평양 시간) · 비교: 같은 {kind} 회차 "
            f"최근 28일 {base['n']}편 중앙값")


def source_lines(metrics):
    """Where viewers came from, what they searched and the scene they left fastest."""
    lines = []
    traffic = metrics.get("traffic") or []
    if traffic:
        lines.append("유입: " + " · ".join(f"{TRAFFIC.get(t['source'], t['source'])} {t['share']:.0%}"
                                          for t in traffic[:3] if t.get("share") is not None))
    terms = metrics.get("search_terms") or []
    if terms:
        lines.append("검색어: " + " · ".join(f"\"{t['term']}\" {t['views']}" for t in terms[:3]))
    steep = next((d for d in metrics.get("scene_drops") or [] if d["steepest"]), None)
    if steep:
        lines.append(f"이탈이 가장 가파른 장면: {steep['heading']}({steep['type']}) {round(steep['start'])}~"
                     f"{round(steep['end'])}초, 1분당 −{steep['per_minute'] * 100:.0f}%p")
    return lines


def change_lines(changes):
    """What to paste into YouTube Studio, numbered, each with its one-line reason."""
    if not changes:
        return ["✏️ 지금 YouTube에서 바꿀 것은 없습니다."]
    lines = ["✏️ YouTube에서 바꿔볼 것"]
    for n, change in enumerate(changes, 1):
        lines += [f"{n}. {TARGETS[change['target']]} → `{change['text']}`", f"   왜: {change['reason']}"]
    return lines


def outlier_lines(row, reasons, compared):
    """The metrics that made this upload an outlier of its kind; none for a request frozen before that rule."""
    if not reasons:
        return []
    kind = EDITIONS.get(row["edition_kind"], "기타")
    return ["튄 지표: " + " · ".join(f"{OUTLIER_NAMES[r['metric']]} {r['ratio']:.1f}배" for r in reasons)
            + f" (같은 {kind} 최근 28일 {compared}편 중앙값 대비)"]


def card_text(row, base, receipt, reasons=(), compared=0):
    """Three-day card: the metrics that made it an outlier of its kind, the numbers against the same-edition
    baseline, what to change in YouTube Studio and the one-line diagnosis; the full diagnosis follows in the thread.
    When the analysis turn failed, the numbers alone with where viewers came from."""
    metrics = row["metrics"]
    output = (receipt or {}).get("output")
    top = [f"📊 {headline(row)}", *outlier_lines(row, reasons, compared)]
    if output:
        return "\n".join([*top, *metric_lines(metrics, base), "", *change_lines(output["changes"]),
                          "", f"진단: {output['summary']}", THREAD_HINT])
    return "\n".join([*top, window_line(row, base), *metric_lines(metrics, base), *source_lines(metrics),
                      "", f"진단: 분석 실행 실패({(receipt or {}).get('code', 'unknown')}). 수치만 전달합니다."])


def detail_text(row, base, output):
    """The card's thread: the comparison window, where viewers came from and left, strengths, weaknesses and the
    staged hypotheses with their evidence."""
    lines = [f"🔎 자세한 진단 · {window_line(row, base)}", *source_lines(row["metrics"]), ""]
    lines += [f"+ {s}" for s in output["strengths"]] + [f"− {w}" for w in output["weaknesses"]]
    lines += [f"• [{STAGES[h['stage']]} · 확신 {CONFIDENCE[h['confidence']]}] {h['claim']} — {h['evidence']}"
              for h in output["hypotheses"]]
    return "\n".join(lines)


def answer_text(output, changes, lesson):
    """The reply to the owner's message: the answer, then what to paste into Studio and a proposed lesson."""
    lines = [output["answer"]]
    if changes:
        lines += ["", *change_lines(changes)]
    if lesson:
        lines += ["", f"💡 교훈 제안 · {SCOPES[lesson['scope']]}: {lesson['text']}", LESSON_HINT]
    return "\n".join(lines)


def failed_answer_text(code):
    return f"답변을 만들지 못했어요({code}). 다시 물어봐 주세요."


def update_text(row, base):
    return "\n".join([f"7일 갱신({row['window_start']:%m/%d}~{row['window_end']:%m/%d}) · 같은 회차 {base['n']}편과 비교",
                      *metric_lines(row["metrics"], base)])


def weekly_text(monday, output, episodes, verdicts, unlinked, proposals):
    titles = {e["youtube_id"]: e["title"] for e in episodes}
    lines = [f"📈 주간 성과 리뷰 · {monday:%m/%d}(월) 기준 최근 28일 {len(episodes)}편", output["summary"]]
    for heading, notes in (("잘된 영상", output["top"]), ("아쉬운 영상", output["bottom"])):
        if notes:
            lines += ["", f"*{heading}*"] + [f"• {titles.get(n['youtube_id'], n['youtube_id'])} — {n['reason']}"
                                             for n in notes]
    if output["patterns"]:
        lines += ["", "*반복 패턴*"] + [f"• {p}" for p in output["patterns"]]
    if verdicts:
        lines += ["", "*활성 교훈 평가*"] + [f"• [{VERDICTS[v['verdict']]}] {v['text']} — {v['reason']}" for v in verdicts]
    if unlinked:
        lines += ["", f"제작 작업과 연결 안 된 업로드 {unlinked}편: quant-company video-analytics link 로 연결할 수 있습니다."]
    if proposals:
        lines += ["", f"교훈 제안 {proposals}건은 아래 버튼으로 채택하거나 거절해 주세요. 채택한 교훈은 다음에 만드는 회차부터 반영됩니다."]
    return "\n".join(lines)


def failed_weekly_text(monday, code):
    return f"📈 주간 성과 리뷰 · {monday:%m/%d}(월)\n분석 실행 실패({code}). 이번 주 리뷰와 교훈 제안은 없습니다."


def blocks(company, row):
    """A review: its text, then adopt/reject for each proposal it made and retire for every active lesson. An answer
    that proposed a lesson: its text and adopt/reject. Rendered when sent, so the states shown are current."""
    with company.db.transaction() as conn:
        review = conn.execute("""SELECT id,kind FROM video_analyses WHERE message_id=%s AND kind IN ('weekly','answer')""",
                              (row["id"],)).fetchone()
        if not review:
            return None
        proposed = conn.execute("""SELECT * FROM video_lessons WHERE analysis_id=%s AND state='proposed'
            ORDER BY id""", (review["id"],)).fetchall()
    if review["kind"] == "answer" and not proposed:
        return None
    active = AnalyticsStore(company).lessons(["active"]) if review["kind"] == "weekly" else []
    result = [{"type": "section", "text": {"type": "mrkdwn", "text": row["text"][:3000]}}]

    def buttons(lesson, actions):
        value = json.dumps({"id": str(lesson["id"]), "message": str(row["id"])}, separators=(",", ":"))
        return {"type": "actions", "block_id": "video-lesson:" + str(lesson["id"]), "elements": [
            {"type": "button", "action_id": "video_lesson_" + a, "text": {"type": "plain_text", "text": LABELS[a]},
             "value": value, **({"style": "primary"} if a == "adopt" else {})} for a in actions]}

    if review["kind"] == "answer":
        return result + [buttons(lesson, ("adopt", "reject")) for lesson in proposed]
    for n, lesson in enumerate(proposed, 1):
        evidence = lesson["evidence"]
        result.append({"type": "section", "text": {"type": "mrkdwn", "text": (
            f"*교훈 제안 {n}* · {SCOPES[lesson['scope']]}\n{lesson['text']}\n근거: {evidence['evidence']} "
            f"(영상 {len(evidence['videos'])}편) · 기대 지표: {evidence['expected']} · "
            f"확신 {CONFIDENCE[evidence['confidence']]}")[:3000]}})
        result.append(buttons(lesson, ("adopt", "reject")))
    for lesson in active:
        result.append({"type": "section", "text": {"type": "mrkdwn",
                                                   "text": f"*활성 교훈* · {SCOPES[lesson['scope']]}\n{lesson['text']}"}})
        result.append(buttons(lesson, ("retire",)))
    return result


def reply_text(outcome, lesson, active, limit):
    return {"active": f"채택: {lesson['text']}\n활성 교훈 {active}/{limit} · 다음에 만드는 회차부터 대본 작성 지시에 들어갑니다.",
            "full": f"활성 교훈이 {limit}개라 채택하지 않았습니다. '은퇴' 버튼으로 하나를 내린 뒤 다시 눌러 주세요.",
            "rejected": f"거절: {lesson['text']}",
            "retired": f"은퇴: {lesson['text']}\n다음에 만드는 회차부터 빠집니다.",
            "unchanged": f"이미 처리된 교훈입니다(현재 상태: {lesson['state']})."}[outcome]


def accept(ingress, role, payload, credential):
    """Owner's adopt/reject/retire click on a delivered Monday review, or adopt/reject on a delivered answer, in the
    analytics channel. The outcome is said in that message's thread."""
    settings = ingress.settings
    try:
        if (role != BRIEFER or payload["type"] != "block_actions" or payload["team"]["id"] != settings.slack_team_id
                or payload["api_app_id"] != credential["app_id"] or len(payload["actions"]) != 1):
            raise ValueError("Unexpected lesson interaction")
        action, message, container = payload["actions"][0], payload["message"], payload["container"]
        selected = action["action_id"].removeprefix("video_lesson_")
        binding = json.loads(action["value"])
        user, channel = payload["user"]["id"], payload["channel"]["id"]
        if (selected not in LABELS or action["action_id"] != "video_lesson_" + selected or action["type"] != "button"
                or action["text"]["text"] != LABELS[selected] or action["block_id"] != "video-lesson:" + binding["id"]
                or channel != settings.video_analytics_channel_id or container["channel_id"] != channel
                or message["user"] != credential["bot_user_id"]
                or message.get("app_id", credential["app_id"]) != credential["app_id"]
                or message["ts"] != container["message_ts"] or container["type"] != "message"
                or container.get("is_ephemeral")):
            raise ValueError("Invalid lesson message binding")
        store = AnalyticsStore(ingress.company)
        thread = store.enabled() and store.bound(binding["id"], binding["message"], message["ts"], channel, selected)
        if not thread:
            raise ValueError("Lesson button outside a delivered review or answer")
        event_key = f"video-lesson:{binding['id']}:{action['action_ts']}:{user}"
    except (KeyError, TypeError, ValueError, AttributeError):
        raise PolicyError("Malformed video lesson interaction") from None
    outcome = store.decide(binding["id"], selected, user, event_key)
    lesson = store.lesson(binding["id"])
    weekly = thread.startswith("weekly:")
    store.post(thread, "lesson-action:" + event_key,
               f"주간 성과 리뷰 {thread.removeprefix('weekly:')}" if weekly else f"영상 성과 {thread}",
               reply_text(outcome, lesson, len(store.active_lessons()), settings.video_analytics_max_lessons),
               reply_to=thread if weekly else f"{thread}:d3")
    return {"ok": True, "lesson_action": True, "outcome": outcome}

"""Posts in the analytics channel: the three-day card, the seven-day update, the Monday/Thursday channel strategy
report with the owner's lesson buttons and its thread replies (what did not fit, Monday's benchmark), and the binding
checks for those buttons."""

import json
from datetime import date

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
VERDICTS = {"keep": "유지", "retire": "해제 권고", "too_early": "아직 판단 이름"}
# The owner reads lessons as strategies (owner, 2026-10-23): retiring one is 채택 해제.
LABELS = {"adopt": "채택", "reject": "거절", "retire": "채택 해제"}
STATES = {"proposed": "제안", "active": "적용 중", "rejected": "거절", "retired": "채택 해제"}
TARGETS = {"title": "제목", "description": "설명 첫 줄", "tags": "태그 추가"}
OUTLIER_NAMES = {"views": "조회", "ctr": "CTR", "hook_30s": "30초 유지"}
THREAD_HINT = "자세한 진단은 이 스레드에 이어집니다. 스레드에 질문이나 지시를 남기면 답합니다."
WAIT_TEXT = "지금 영상을 만드는 중이라, 제작이 끝나면 이 스레드에 답할게요."
EXPIRED_TEXT = "영상 제작이 길어져 이 질문에 답하지 못했어요. 다시 물어봐 주세요."
LESSON_HINT = "아래 버튼으로 채택하거나 거절해 주세요. 채택한 전략은 다음에 만드는 회차부터 반영됩니다."
# The report's root post: AnalyticsStore.post keeps 2,900 characters and one Slack section block takes 3,000.
REPORT_LIMIT = 2900
EFFECTS = {"worked": "효과 있음", "did_not_work": "효과 없음", "too_early": "아직 판단 이름"}
# A finding compared on fewer videos than this a side reads as too early, whatever the model called it.
JUDGE_MIN = 5
WEEKDAYS = "월화수목금토일"
# Where findings or verdicts that did not fit the root were moved to the thread.
MOVED = "… (글자 수 제한으로 나머지는 스레드에 이어집니다)"
KINDS = (("episodes", "본편"), ("shorts", "Shorts"))
FIGURES = (("views", "편당 조회"), ("subscribers", "구독"), ("impressions", "편당 노출"), ("ctr", "CTR"),
           ("hook_30s", "30초 유지"), ("avg_view_pct", "평균 시청"))


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
    """The reply to the owner's message: the answer, then what to paste into Studio and a proposed lesson (shown as a
    strategy)."""
    lines = [output["answer"]]
    if changes:
        lines += ["", *change_lines(changes)]
    if lesson:
        lines += ["", f"💡 전략 제안 · {SCOPES[lesson['scope']]}: {lesson['text']}", LESSON_HINT]
    return "\n".join(lines)


def failed_answer_text(code):
    return f"답변을 만들지 못했어요({code}). 다시 물어봐 주세요."


def update_text(row, base):
    return "\n".join([f"7일 갱신({row['window_start']:%m/%d}~{row['window_end']:%m/%d}) · 같은 회차 {base['n']}편과 비교",
                      *metric_lines(row["metrics"], base)])


def _report_day(day):
    return f"{day:%m/%d}({WEEKDAYS[day.weekday()]})"


def _figure(key, value):
    if value is None:
        return "–"
    if key == "subscribers":
        return f"{round(value):+,}"
    if key == "ctr":
        return _pct(value)
    if key == "hook_30s":
        return f"{value:.0%}"
    return f"{value:.0f}%" if key == "avg_view_pct" else f"{round(value):,}"


def figure_lines(stats):
    """The period's numbers for episodes and Shorts apart, the previous period's in brackets with both counts: values
    per video (medians; CTR weighted by impressions) and subscribers in total, since a period is three or four days
    long. A metric measured in neither period (a Shorts' reach) is left out."""
    lines = ["*이번 기간 숫자* · 직전 리포트 이후 측정한 영상, 괄호는 지난 기간 · 편당 값은 중앙값, 구독은 합계"]
    for kind, name in KINDS:
        this, before = stats["this"][kind], stats["previous"][kind]
        lines.append(f"{name} {this['n']}편(지난 {before['n']}편) · " + " · ".join(
            f"{label} {_figure(k, this[k])} ({_figure(k, before[k])})" for k, label in FIGURES
            if this[k] is not None or before[k] is not None) if this["n"] else f"{name} · 이번 기간 새로 측정한 영상 없음")
    return lines


def report_verdicts(output, extra):
    """The verdicts on the lessons the report was asked about (frozen with its request), with their texts."""
    lessons = extra.get("lessons") or {}
    return [{**v, "text": lessons[v["lesson_id"]]} for v in output["verdicts"] if v["lesson_id"] in lessons]


def _sections(items, continued=None):
    """Lines of (section heading, line) items, each section under its heading; `continued` marks a heading whose
    section began in an earlier post."""
    lines, heading = [], object()
    for title, line in items:
        if title != heading:
            heading = title
            lines += [""] + ([title + (" (이어서)" if title == continued else "")] if title else [])
        lines.append(line)
    return lines


def _posts(lines):
    """Lines packed into posts of at most REPORT_LIMIT characters, cut only between lines and never dropped."""
    posts, current = [], []
    for line in lines:
        if current and len("\n".join(current + [line])) > REPORT_LIMIT:
            posts.append(current)
            current = []
        if current or line:
            current.append(line)
    return ["\n".join(p).rstrip("\n") for p in posts + [current] if p]


def report_texts(day, output, extra, verdicts, proposals):
    """(root, replies): the whole report in one post when it fits REPORT_LIMIT. Otherwise the root keeps the
    conclusion, the period's numbers, the experiment and the strategy line with as many findings and verdicts as fit;
    the rest of them open the details (viewers, traffic, search, keywords, Shorts), which follow as thread replies,
    each within REPORT_LIMIT. Nothing the report wrote is left out."""
    # A request frozen before the strategy report (a weekly review re-asked across the release) has no stats.
    stats = extra.get("stats") or {}
    data = f" · 데이터 기준 {date.fromisoformat(stats['data_day']):%m/%d}(태평양 시간)" if stats.get("data_day") else ""
    head = [f"📈 채널 전략 리포트 · {_report_day(day)}{data}", output["headline"],
            *(["", *figure_lines(stats)] if stats else [])]
    findings = "*효과가 있었던 것과 없었던 것*"
    items = [(findings, f"• [{EFFECTS['too_early' if f['n'] < JUDGE_MIN else f['effect']]} · n={f['n']} · "
                        f"확신 {CONFIDENCE[f['confidence']]}] {f['text']}") for f in output["findings"]]
    items += [("*적용 중인 전략 평가*", f"• [{VERDICTS[v['verdict']]}] {v['text']} — {v['reason']}") for v in verdicts]
    if extra.get("unlinked"):
        items.append((None, f"최근 28일 업로드 중 제작 작업과 연결 안 된 {extra['unlinked']}편: quant-company "
                            "video-analytics link 로 연결할 수 있습니다."))
    trial = output["experiment"]
    tail = ["", f"*실험* {trial['change']} — 비교: {trial['compare']} · {trial['episodes']}편 · 지표: {trial['metric']}"]
    if proposals:
        tail += ["", f"다음 전략 {proposals}가지는 아래 버튼으로 채택하거나 거절해 주세요. 채택한 전략은 다음에 만드는 회차부터 "
                     "반영됩니다."]
    detail = report_detail_lines(output, extra)
    full = "\n".join(head + _sections(items) + tail + detail)
    if len(full) <= REPORT_LIMIT:
        return full, []
    kept = len(items)
    while kept and len("\n".join(head + _sections(items[:kept]) + [MOVED] + tail)) > REPORT_LIMIT:
        kept -= 1
    moved = items[kept:]
    continued = moved[0][0] if moved and kept and items[kept - 1][0] == moved[0][0] else None
    root = head + _sections(items[:kept]) + ([MOVED] if moved else []) + tail
    return "\n".join(root), _posts(_sections(moved, continued) + detail)


def _shares(shares, before):
    # Sorted here: the frozen stats come back from JSONB, which does not keep key order.
    top = sorted(shares.items(), key=lambda kv: (-kv[1], kv[0]))[:4]
    return " · ".join(f"{TRAFFIC.get(k, k)} {v:.0%}(" + (f"{before[k]:.0%}" if k in before else "–") + ")" for k, v in top)


def report_detail_lines(output, extra):
    """The report's details, each section after a blank line: what viewers preferred, where they came from, what they
    searched (all measured uploads), the keywords to use and how the Shorts relate to their episodes (every pair);
    each section only when it has something to say."""
    stats, lines = extra.get("stats") or {}, []
    if output["viewers"]:
        lines += ["", "*시청자가 좋아한 것*"] + [f"• {v}" for v in output["viewers"]]
    traffic = stats.get("traffic")
    shares = [f"{name}: {_shares(traffic['this'][kind], traffic['previous'][kind])}"
              for kind, name in KINDS if traffic and traffic["this"][kind]]
    if shares or output["traffic"]:
        lines += ["", "*유입 경로* · 이번 기간(지난 기간)", *shares] + ([output["traffic"]] if output["traffic"] else [])
    terms = (stats.get("search_terms") or [])[:8]
    if terms:
        lines += ["", "*검색어 상위(누적)*", " · ".join(f"\"{t['term']}\" {t['views']:,}회({t['videos']}편)" for t in terms)]
    if output["keywords"]:
        lines += ["", "*키워드 전략*"] + [f"• {k}" for k in output["keywords"]]
    pairs = stats.get("shorts_pairs") or {}
    if pairs.get("count") or output["shorts"]:
        lines += ["", "*Shorts → 본편(누적)*"]
        if pairs.get("count"):
            paired, alone = pairs["with_shorts"], pairs["without"]
            lines.append(f"짝 {pairs['count']}쌍 · Shorts 조회 중앙값 {_figure('views', pairs['shorts_views'])} · "
                         f"Shorts가 있던 본편 {paired['n']}편 조회 중앙값 {_figure('views', paired['views'])} · 없던 본편 "
                         f"{alone['n']}편 조회 중앙값 {_figure('views', alone['views'])}")
        if output["shorts"]:
            lines.append(output["shorts"])
    return lines


def benchmark_texts(output, extra):
    """Monday's benchmark replies: each picked video the model wrote a note on (the picked videos alone when it wrote
    none), with why it grew and what this channel can use; a note is never cut, the next one starts a further reply
    when it does not fit. A skipped fetch is one line; [] for a report without a benchmark (Thursday)."""
    bench = extra.get("benchmark")
    if bench is None:
        return []
    if isinstance(bench, dict):
        return [f"*벤치마크*: 이번에는 후보를 가져오지 못했습니다({bench.get('skipped', 'unknown')})."]
    picked, notes = {v["youtube_id"]: v for v in bench}, {}
    for note in output.get("benchmark") or []:
        if note["youtube_id"] in picked:
            notes.setdefault(note["youtube_id"], note)
    items = [(picked[i], n) for i, n in notes.items()] or [(v, None) for v in bench]
    texts, lines = [], [f"*벤치마크* · 한국 인기 영상 중 구독자 대비 조회가 큰 {len(items)}편(개설 1년 이내 채널 가중)"]
    for n, (video, note) in enumerate(items, 1):
        block = [f"{n}. {video['channel']} · 「{video['title']}」 조회 {video['views']:,} · 구독 {video['subscribers']:,} · "
                 f"https://youtu.be/{video['youtube_id']}"]
        if note:
            block += [f"   왜: {note['why']}", f"   옮길 점: {' / '.join(note['transfer'])}"]
        if n > 1 and len("\n".join(lines + block)) > REPORT_LIMIT:
            texts.append("\n".join(lines))
            lines = []
        lines += block
    return texts + ["\n".join(lines)]


def failed_report_text(day, code, extra):
    from .analytics import STRATEGY_HOUR, next_report_day  # analytics imports this module

    lines = [f"📈 채널 전략 리포트 · {_report_day(day)}",
             f"분석 실행 실패({code}). 이번 전략 제안은 없습니다. 다음 리포트: {_report_day(next_report_day(day))} "
             f"{STRATEGY_HOUR:02}:00"]
    return "\n".join(lines + (["", *figure_lines(extra["stats"])] if extra.get("stats") else []))


def _buttons(row, lesson, actions):
    value = json.dumps({"id": str(lesson["id"]), "message": str(row["id"])}, separators=(",", ":"))
    return {"type": "actions", "block_id": "video-lesson:" + str(lesson["id"]), "elements": [
        {"type": "button", "action_id": "video_lesson_" + a, "text": {"type": "plain_text", "text": LABELS[a]},
         "value": value, **({"style": "primary"} if a == "adopt" else {})} for a in actions]}


def blocks(company, row):
    """A strategy report: its text, then adopt/reject for each strategy it proposed and retire for every active lesson.
    An answer that proposed a lesson: its text and adopt/reject. A reply to the owner's decision: its text and retire
    while that lesson is active, so an adoption (or a repeated 채택 on an adopted lesson) can be undone in place.
    Rendered when sent, so the states shown are current."""
    with company.db.transaction() as conn:
        review = conn.execute("""SELECT id,kind FROM video_analyses WHERE message_id=%s AND kind IN ('weekly','answer')""",
                              (row["id"],)).fetchone()
        if not review:
            adopted = conn.execute("""SELECT l.id FROM video_lesson_actions d JOIN video_lessons l ON l.id=d.lesson_id
                WHERE d.message_id=%s AND l.state='active'""", (row["id"],)).fetchone()
            return [{"type": "section", "text": {"type": "mrkdwn", "text": row["text"][:3000]}},
                    _buttons(row, adopted, ("retire",))] if adopted else None
        proposed = conn.execute("""SELECT * FROM video_lessons WHERE analysis_id=%s AND state='proposed'
            ORDER BY id""", (review["id"],)).fetchall()
    if review["kind"] == "answer" and not proposed:
        return None
    active = AnalyticsStore(company).lessons(["active"]) if review["kind"] == "weekly" else []
    result = [{"type": "section", "text": {"type": "mrkdwn", "text": row["text"][:3000]}}]
    if review["kind"] == "answer":
        return result + [_buttons(row, lesson, ("adopt", "reject")) for lesson in proposed]
    for n, lesson in enumerate(proposed, 1):
        evidence = lesson["evidence"]
        result.append({"type": "section", "text": {"type": "mrkdwn", "text": (
            f"*전략 {n}* · {SCOPES[lesson['scope']]}\n{lesson['text']}\n근거: {evidence['evidence']} "
            f"(영상 {len(evidence['videos'])}편) · 움직일 지표: {evidence['expected']} · "
            f"확신 {CONFIDENCE[evidence['confidence']]}")[:3000]}})
        result.append(_buttons(row, lesson, ("adopt", "reject")))
    for lesson in active:
        result.append({"type": "section", "text": {"type": "mrkdwn",
                                                   "text": f"*적용 중인 전략* · {SCOPES[lesson['scope']]}\n{lesson['text']}"}})
        result.append(_buttons(row, lesson, ("retire",)))
    return result


def reply_text(outcome, lesson, active, limit):
    return {"active": f"채택: {lesson['text']}\n적용 중인 전략 {active}/{limit} · 다음에 만드는 회차부터 대본 작성 지시에 "
                      "들어갑니다.",
            "full": f"적용 중인 전략이 {limit}개라 채택하지 않았습니다. '채택 해제' 버튼으로 하나를 뺀 뒤 다시 눌러 주세요.",
            "rejected": f"거절: {lesson['text']}",
            "retired": f"채택 해제: {lesson['text']}\n다음에 만드는 회차부터 빠집니다.",
            "unchanged": f"이미 처리된 전략입니다(현재 상태: {STATES[lesson['state']]})."}[outcome]


def accept(ingress, role, payload, credential):
    """Owner's adopt/reject/retire click on a delivered strategy report, adopt/reject on a delivered answer, or retire
    on a delivered reply to an earlier decision, in the analytics channel. The outcome is said in that message's thread."""
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
               f"채널 전략 리포트 {thread.removeprefix('weekly:')}" if weekly else f"영상 성과 {thread}",
               reply_text(outcome, lesson, len(store.active_lessons()), settings.video_analytics_max_lessons),
               reply_to=thread if weekly else f"{thread}:d3", decision=event_key)
    return {"ok": True, "lesson_action": True, "outcome": outcome}

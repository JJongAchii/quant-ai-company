import html
from urllib.parse import quote

from .contracts import SOURCES
from .schedule import KST


def safe(value):
    return html.escape(str(value), quote=False).replace("@", "＠").replace("*", "＊").replace("`", "ˋ")


def render(notice, at, *, changed=False, reminders=()):
    heading = "청약 일정 알림" if reminders else "분양 공고 변경" if changed else "분양 공고"
    lines = [f"*{heading} · {notice.region}*", f"*{safe(notice.title)}*",
             f"{safe(SOURCES[notice.source])} · {safe(notice.category)} · 공고 {notice.published:%Y.%m.%d}"]
    if reminders:
        lines.extend("• " + safe(text) for text in reminders)
    if notice.address:
        lines.append("위치: " + safe(notice.address))
        lines.append(f"<https://map.kakao.com/link/search/{quote(notice.address, safe='')}|카카오맵에서 주소 검색>")
    if notice.map_location:
        lines.append("Slack 지도 패널: " + safe(notice.map_location.label)
                     + " 중심 참고 지도 · 실제 분양 대지의 정확한 위치는 공고문을 확인하세요.")
        lines.append("지도 데이터: <https://www.openstreetmap.org/copyright|© OpenStreetMap contributors>")
    if notice.supply:
        lines.append("공급: " + safe(notice.supply))
    if notice.price_summary:
        lines.append(safe(notice.price_summary))
    if notice.windows:
        for period in notice.windows:
            dates = f"{period.start:%m/%d}"
            if period.end != period.start:
                dates += f" ~ {period.end:%m/%d}"
            lines.append(f"• {safe(period.label)}: {dates}")
    if notice.result_date:
        lines.append(f"당첨자 발표: {notice.result_date:%m/%d}")
    lines.append(safe(notice.schedule_note))
    lines.append(f"<{notice.url}|공식 공고·신청 안내 확인>" + (
        f" · <{notice.document_url}|모집공고문>" if notice.document_url else ""))
    lines.append(f"확인 {at.astimezone(KST):%m/%d %H:%M} KST · 신청자격은 공고문 기준으로 직접 확인하세요.")
    return "\n".join(lines)

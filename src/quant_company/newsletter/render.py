"""The email itself: subject, inline-styled table HTML (no web fonts) and a plain-text part, as approved in v5.

Structure follows docs/superpowers/specs/assets/2026-10-19-evening-brief-email-v5.html: header, 오늘의 시장, 주요 이슈,
기업 소식, 주요 일정, 영상 버튼, footer. No AI label, no source lines, no footnote numbers."""

from email.headerregistry import Address
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formatdate
from html import escape
from pathlib import Path
from urllib.parse import quote

from .contracts import KIND_NAMES, edition_label

# Read in place; the brand mark stays where the video design keeps it.
LOGO = Path(__file__).resolve().parent.parent / "video" / "design" / "logo_mark.png"
LOGO_CID = "logo@newsletter.anything-story"
UNSUBSCRIBE_SUBJECT = "수신거부"
FONT = "-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo','Malgun Gothic','Noto Sans KR',Arial,sans-serif"
INK, NAVY, CREAM, GOLD, LABEL = "#1B2440", "#011A43", "#FCF1DB", "#FDC749", "#9B6B00"
UP, DOWN, MUTED, SOFT, LINE = "#D7263D", "#1E5EFF", "#7A86A8", "#4A5578", "#E8ECF4"
INTRO = "뭐든story는 매 거래일 아침과 장 마감 후 국내 증시의 주요 내용을 정리해 보내 드립니다."
CONSENT = "이 메일은 뭐든story 리포트 수신에 동의하신 분께 발송됩니다."
DISCLAIMER = "투자 권유가 아닌 정보 제공용 자료이며, 투자 판단과 책임은 본인에게 있습니다."
# A sender in the reader's contacts keeps the report in the inbox (owner, 2026-10-20); the configured address.
SAFE_SENDER = "받은편지함에서 놓치지 않으려면 {sender}을(를) 연락처에 추가해 주세요."


def subject(draft, day, kind="pm"):
    return " ".join(f"{draft.headline} {draft.accent}".split()) + f" | {edition_label(day)} {KIND_NAMES[kind]} 브리핑"


def masthead(day, kind):
    """The header's right label and the plain text's first line."""
    return f"{edition_label(day)} {'아침' if kind == 'am' else '장 마감'} · 오늘의 증시story"


def unsubscribe_url(sender):
    return f"mailto:{sender}?subject={quote(UNSUBSCRIBE_SUBJECT)}"


def tone(text):
    return UP if text.strip().startswith("+") else DOWN if text.strip().startswith("-") else INK


def arrow(change):
    return ("▲ " if change.startswith("+") else "▼ " if change.startswith("-") else "") + change.lstrip("+-")


def e(text):
    return escape(text or "", quote=False)


def section(label, inner, extra=""):
    return (f'<tr><td style="padding:18px 22px;border-bottom:1px solid {LINE};{extra}">'
            + (f'<div style="font-size:12px;font-weight:800;color:{LABEL};letter-spacing:.06em;margin:0 0 8px;">'
               f'{e(label)}</div>' if label else "") + inner + "</td></tr>")


def paragraph(text, lead=""):
    if not text:
        return ""
    head = f'<b>{e(lead)}</b> · ' if lead else ""
    return f'<p style="margin:6px 0;font-size:15px;line-height:1.7;color:{INK};">{head}{e(text)}</p>'


def heading(text, top=0, number=None):
    badge = (f'<span style="display:inline-block;width:22px;height:22px;border-radius:11px;background:{NAVY};'
             f'color:{GOLD};font-size:12px;font-weight:800;text-align:center;line-height:22px;margin-right:6px;'
             f'vertical-align:2px;">{number}</span>' if number else "")
    return (f'<h3 style="font-size:17px;line-height:1.4;font-weight:800;margin:{top}px 0 6px;color:{NAVY};">'
            f'{badge}{e(text)}</h3>')


def swing_box(label, level, change):
    color = tone(change)
    background = "#FFECEE" if color == UP else "#EAF0FF" if color == DOWN else "#F5F7FB"
    return (f'<td align="center" style="background:{background};border-radius:10px;padding:9px 10px;">'
            f'<div style="font-size:11.5px;font-weight:700;color:{SOFT};">{e(label)}</div>'
            f'<div style="font-size:19px;font-weight:800;color:{color};">{e(level)}</div>'
            f'<div style="font-size:11.5px;font-weight:700;color:{color};">{e(arrow(change))}</div></td>')


def swing(market):
    s = market.swing
    if not s:
        return ""
    cells = (swing_box(f"{s.index} 출발", s.open_level, s.open_change)
             + '<td width="34" align="center" style="font-size:20px;color:#9AA6C8;">→</td>'
             if s.open_level else "") + swing_box(f"{s.index} 마감", s.close_level, s.close_change)
    return ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="margin:10px 0 4px;"><tr>{cells}</tr></table>')


def investors(market):
    if not market.investors:
        return ""
    cells = "".join(
        f'<td align="center" width="{100 // len(market.investors)}%" style="padding:0 3px;"><div style="background:#F5F7FB;'
        f'border-radius:8px;padding:7px 4px;font-size:12px;color:{SOFT};">{e(row.name)}<div style="font-size:14px;'
        f'font-weight:800;color:{tone(row.amount)};">{e(row.amount)}</div></div></td>' for row in market.investors)
    return ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="margin:8px 0 4px;"><tr>{cells}</tr></table>')


def board(market):
    if not market.numbers:
        return ""
    rows = "".join(f'<li style="margin:2px 0;">{e(line)}</li>' for line in market.numbers)
    return f'<ul style="margin:8px 0;padding-left:18px;font-size:14px;line-height:1.6;color:{INK};">{rows}</ul>'


def glossary(market):
    if not market.glossary:
        return ""
    rows = "<br>".join(f'<b style="color:{SOFT};">{e(t.term)}</b> {e(t.meaning)}' for t in market.glossary)
    return (f'<div style="font-size:12.5px;line-height:1.6;color:{MUTED};background:#F7F8FB;border-radius:6px;'
            f'padding:5px 9px;margin-top:8px;">{rows}</div>')


def issue_html(issue, number):
    implication = (f'<div style="background:#FFF8E6;border-radius:8px;padding:8px 11px;margin-top:8px;font-size:14px;'
                   f'line-height:1.65;color:{INK};"><b style="color:{LABEL};">시사점</b> {e(issue.implication)}</div>'
                   if issue.implication else "")
    return (heading(issue.headline, 0 if number == 1 else 18, number) + paragraph(issue.body) + implication
            + f'<div style="font-size:13.5px;margin-top:8px;color:#127A66;font-weight:700;line-height:1.5;">'
              f'▶ 확인할 {e(issue.check_kind)}: {e(issue.check)}</div>')


def calendar_html(rows):
    cells = "".join(
        f'<tr><td width="80" valign="top" style="padding:6px 0;border-bottom:1px solid #F0F2F8;font-size:14px;'
        f'font-weight:700;color:{NAVY};">{e(r.when)}</td><td style="padding:6px 0;border-bottom:1px solid #F0F2F8;'
        f'font-size:14px;color:{INK};">{e(r.what)}'
        + (f'<div style="font-size:12.5px;font-weight:700;color:#127A66;">{e(r.note)}</div>' if r.note else "")
        + "</td></tr>" for r in rows)
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{cells}</table>'


def html(draft, *, day, sender, channel_url, kind="pm", logo_src=f"cid:{LOGO_CID}"):
    m = draft.market
    market = (heading(m.heading) + swing(m) + paragraph(m.flow) + paragraph(m.investors_text, "투자자별 매매")
              + investors(m) + paragraph(m.investors_note) + paragraph(m.sectors, "업종·종목") + board(m)
              + glossary(m))
    issues = "".join(issue_html(issue, n) for n, issue in enumerate(draft.issues, 1))
    company = (section("기업 소식", f'<div style="font-size:14px;line-height:1.65;padding:9px 11px;border:1px solid {LINE};'
                                  f'border-radius:8px;color:{INK};">{e(draft.company)}</div>') if draft.company else "")
    calendar = section("주요 일정", calendar_html(draft.calendar)) if draft.calendar else ""
    accent = f'<br><span style="color:{GOLD};">{e(draft.accent)}</span>' if draft.accent else ""
    unsubscribe = e(unsubscribe_url(sender))
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>{e(subject(draft, day, kind))}</title></head>
<body style="margin:0;padding:0;background:#EEF1F7;word-break:keep-all;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:#EEF1F7;">{e(draft.preview)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#EEF1F7;">
<tr><td align="center" style="padding:16px 8px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:560px;background:#FFFFFF;border-radius:10px;font-family:{FONT};color:{INK};">
<tr><td style="background:{NAVY};padding:18px 22px 20px;border-radius:10px 10px 0 0;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
<td style="font-size:15px;font-weight:800;color:{CREAM};"><img src="{logo_src}" alt="" height="22" style="height:22px;vertical-align:middle;margin-right:8px;border:0;">뭐든story</td>
<td align="right" style="font-size:12px;font-weight:600;color:#AEB7D4;">{masthead(day, kind)}</td>
</tr></table>
<h1 style="font-size:23px;line-height:1.35;font-weight:800;margin:14px 0 6px;color:#FFFFFF;letter-spacing:-.02em;">{e(draft.headline)}{accent}</h1>
<p style="margin:0;font-size:14.5px;line-height:1.6;color:#D5DBEC;">{e(draft.lead)}</p>
</td></tr>
{section("오늘의 시장", market)}
{section("주요 이슈", issues)}
{company}{calendar}
{section("", f'<a href="{e(channel_url)}" style="display:inline-block;background:#E62117;color:#FFFFFF;font-weight:800;padding:10px 18px;border-radius:8px;text-decoration:none;font-size:14px;">▶ 영상으로 보기</a>', "text-align:center;")}
<tr><td style="padding:16px 22px;font-size:11.5px;line-height:1.65;color:{MUTED};background:#F7F8FB;border-radius:0 0 10px 10px;">
{e(INTRO)} {e(CONSENT)}<br>{e(DISCLAIMER)}<br>{e(SAFE_SENDER.format(sender=sender))}<br><a href="{unsubscribe}" style="color:{SOFT};">수신 거부</a>
</td></tr>
</table></td></tr></table>
</body></html>
"""


def text(draft, *, day, sender, channel_url, kind="pm"):
    m = draft.market
    out = [f"뭐든story · {masthead(day, kind)}", "", f"{draft.headline} {draft.accent}".strip(),
           "", draft.lead, "", "[오늘의 시장]", m.heading]
    if m.swing:
        s = m.swing
        close = f"마감 {s.close_level} ({arrow(s.close_change)})"
        out.append(f"{s.index} " + (f"출발 {s.open_level} ({arrow(s.open_change)}) → " if s.open_level else "") + close)
    out.append(m.flow)
    if m.investors_text:
        out.append(f"투자자별 매매 · {m.investors_text}")
    if m.investors:
        out.append(" · ".join(f"{row.name} {row.amount}" for row in m.investors))
    out += [line for line in (m.investors_note, f"업종·종목 · {m.sectors}" if m.sectors else "") if line]
    out += [f"• {line}" for line in m.numbers]
    out += [f"{t.term}: {t.meaning}" for t in m.glossary]
    out += ["", "[주요 이슈]"]
    for n, issue in enumerate(draft.issues, 1):
        out += [f"{n}. {issue.headline}", issue.body]
        if issue.implication:
            out.append(f"시사점 · {issue.implication}")
        out += [f"▶ 확인할 {issue.check_kind}: {issue.check}", ""]
    if draft.company:
        out += ["[기업 소식]", draft.company, ""]
    if draft.calendar:
        out.append("[주요 일정]")
        out += [f"{r.when}  {r.what}" + (f" ({r.note})" if r.note else "") for r in draft.calendar]
        out.append("")
    out += [f"▶ 영상으로 보기: {channel_url}", "", "—", INTRO, CONSENT, DISCLAIMER, SAFE_SENDER.format(sender=sender),
            f"수신 거부: {unsubscribe_url(sender)}"]
    return "\n".join(out) + "\n"


def message(draft, *, day, sender, name, to, message_id, channel_url, kind="pm", logo=None):
    """multipart/alternative: plain text, then HTML with the logo as a related part (Content-ID)."""
    mail = EmailMessage(policy=SMTP)
    mail["From"] = Address(display_name=name, addr_spec=sender)
    mail["To"] = to
    mail["Subject"] = subject(draft, day, kind)
    mail["Date"] = formatdate()
    mail["Message-ID"] = message_id
    mail["List-Unsubscribe"] = f"<{unsubscribe_url(sender)}>"
    mail.set_content(text(draft, day=day, sender=sender, channel_url=channel_url, kind=kind))
    mail.add_alternative(html(draft, day=day, sender=sender, channel_url=channel_url, kind=kind), subtype="html")
    mail.get_payload()[1].add_related(LOGO.read_bytes() if logo is None else logo, "image", "png",
                                      cid=f"<{LOGO_CID}>", disposition="inline", filename="logo.png")
    return mail

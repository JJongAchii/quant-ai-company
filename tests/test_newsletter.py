"""Evening brief email. Real PostgreSQL; claude-runtime, Gmail SMTP/IMAP and Slack are simulated in-process (no
network, no real mailbox, no Slack): nothing here stands for a deployed or sent email."""

import email
import smtplib
from datetime import timedelta
from email.message import EmailMessage
from email.policy import default

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from quant_company.api import create_app
from quant_company.company import stable
from quant_company.contracts import ProviderFault, ProviderRequest
from quant_company.newsletter import checks, contracts, render
from quant_company.newsletter.commands import parse
from quant_company.newsletter.delivery import NewsletterDispatcher
from quant_company.newsletter.store import FALLBACK_AFTER, NewsletterStore, mask, masked
from quant_company.newsletter.writer import NewsletterWriter

from .newsletter_fixtures import (
    PM_DAY,
    SimulatedProvider,
    commit_pm,
    deliver_root,
    pm_proposal,
    pm_row,
    v5_draft,
)
from .test_briefing import brief, complete, definition  # noqa: F401
from .test_slack import event, signed
from .test_temporal import temporal_environment  # noqa: F401

SENDER = "anything.story.fixture@gmail.com"
PASSWORD = "abcdefghijklmnop"
OWNER_MAIL = "owner.fixture@example.com"
FRIEND_MAIL = "friend.fixture@example.org"


class SimulatedSmtp:
    """In-process stand-in for Gmail SMTP (simulated). Records accepted messages per recipient."""

    def __init__(self, password=PASSWORD):
        self.password, self.accepted, self.sessions = password, [], 0
        self.reject, self.drop_in_data, self.refuse = {}, set(), False

    def __call__(self, host, port, timeout):
        assert (host, port, timeout) == ("smtp.gmail.com", 587, 30)
        if self.refuse:
            raise ConnectionRefusedError("simulated")
        self.sessions += 1
        return SimulatedSmtpSession(self)

    def to(self, address):
        return [data for rcpt, data in self.accepted if rcpt == address]


class SimulatedSmtpSession:
    def __init__(self, server):
        self.server, self.tls, self.user, self.recipient = server, False, None, None

    def ehlo(self):
        return 250, b"ok"

    def starttls(self, context=None):
        self.tls = True
        return 220, b"ready"

    def login(self, user, password):
        assert self.tls and user == SENDER
        if password != self.server.password:
            raise smtplib.SMTPAuthenticationError(535, b"simulated: bad credentials")
        self.user = user

    def mail(self, sender):
        assert self.user and sender == SENDER
        return 250, b"ok"

    def rcpt(self, to):
        self.recipient = to
        return self.server.reject.get(to, 250), b""

    def data(self, message):
        if self.recipient in self.server.drop_in_data:
            raise smtplib.SMTPServerDisconnected("simulated: connection lost after the message was sent")
        self.server.accepted.append((self.recipient, message))
        return 250, b"queued"

    def rset(self):
        return 250, b"ok"

    def quit(self):
        return 221, b"bye"

    def close(self):
        pass


class SimulatedInbox:
    """In-process stand-in for the sender's Gmail IMAP inbox (simulated)."""

    def __init__(self, *mails):
        self.mails = {str(n).encode(): {"header": header, "seen": False} for n, header in enumerate(mails, 1)}

    def __call__(self, host, port, ssl_context=None, timeout=None):
        assert (host, port) == ("imap.gmail.com", 993)
        return self

    def login(self, user, password):
        assert (user, password) == (SENDER, PASSWORD)

    def select(self, box):
        return "OK", [str(len(self.mails)).encode()]

    def search(self, charset, criterion):
        assert criterion == "UNSEEN"
        return "OK", [b" ".join(n for n, m in self.mails.items() if not m["seen"])]

    def fetch(self, number, spec):
        assert "BODY.PEEK" in spec
        return "OK", [(number + b" (BODY[HEADER.FIELDS (FROM SUBJECT MESSAGE-ID DATE)] {1}", self.mails[number]["header"]),
                      b")"]

    def store(self, number, op, flags):
        assert (op, flags) == ("+FLAGS", "\\Seen")
        self.mails[number]["seen"] = True

    def logout(self):
        pass


def reply(sender, subject, identity):
    mail = EmailMessage(policy=default)
    mail["From"] = f"Fixture Reader <{sender}>"
    mail["Subject"] = subject
    mail["Message-ID"] = f"<{identity}@example.com>"
    return bytes(mail)


@pytest.fixture
def mail(brief, tmp_path, monkeypatch):  # noqa: F811
    store, clock = brief
    s = store.company.settings
    s.newsletter_enabled = True
    s.newsletter_from_address = SENDER
    secret = tmp_path / "newsletter" / "app_password"
    secret.parent.mkdir()
    secret.write_text("abcd efgh ijkl mnop\n")
    s.newsletter_password_file = secret
    monkeypatch.setattr("quant_company.newsletter.store.utcnow", lambda: clock["at"])
    return NewsletterStore(store.company), clock


def dispatcher(store, smtp=None, inbox=None):
    return NewsletterDispatcher(store.company, smtp=smtp or SimulatedSmtp(), imap=inbox or SimulatedInbox())


def recipient(store, address, state="active"):
    with store.db.transaction() as conn:
        conn.execute("INSERT INTO newsletter_recipients(id,address,state) VALUES(%s,%s,%s)",
                     (stable("newsletter-recipient:" + address), address, state))


def rows(store, query, *args):
    with store.db.transaction() as conn:
        return conn.execute(query, args).fetchall()


def issue_of(store, edition):
    found = rows(store, "SELECT * FROM newsletter_issues WHERE edition_id=%s", edition.id)
    return found[0] if found else None


def command(store, text, key, *, owner="UHUMAN", thread="900.000"):
    return store.company.ingest(event_key=key, text=text, owner=owner, channel="CQUANT", thread_ts=thread,
                                newsletter_command=parse(text))


def outbox(store, kind=None):
    return rows(store, """SELECT o.text,o.agent,o.thread_ts,m.kind FROM outbox o JOIN messages m ON m.id=o.id
        WHERE %s::text IS NULL OR m.kind=%s ORDER BY o.created_at""", kind, kind)


async def write(store, *outputs):
    provider = SimulatedProvider(*outputs)
    writer = NewsletterWriter(store.company, provider=provider)
    return writer, provider


# ---- Checks (no database) --------------------------------------------------------------------------------------

def test_the_approved_draft_passes_and_rounding_stays_in_its_unit():
    source = contracts.source(pm_row())
    assert checks.findings(v5_draft(), source) == []
    # 3.17% -> 3.2%, 1.76% -> 1.8%, 18조1,308억원 -> 18조원 넘게 / 18.1조원, 1조869억원 -> 1조 869억, 1.16% -> 1.2%,
    # 5,731억원 -> 5천억원 넘게, 3,584억원 -> 3,600억원
    rounded = v5_draft(market__investors_note="외국인은 18.1조원, 18조원 넘게, 18조1,300억원을 순매도했습니다.",
                       market__sectors="삼성전자는 1.2% 올랐습니다. 기관은 5천억원 넘게 샀습니다. 순환매가 나타났습니다.",
                       company="현대로템은 약 3,600억원에 현대위아 특수사업부를 인수하기로 했습니다.")
    assert checks.findings(rounded, source) == []


@pytest.mark.parametrize("field, text, number", [
    ("accent", "1.7% 상승 마감", "1.7%"),                          # rounds 1.76 the wrong way
    ("headline", "코스피, 3.3% 급락 출발 후", "3.3%"),
    ("market__flow", "외국인은 18억원을 순매도했습니다. 순환매가 나타났습니다.", "18억원"),  # 18조 is not 18억
    ("market__flow", "외국인은 1,308조원을 순매도했습니다. 순환매가 나타났습니다.", "1,308조원"),
    ("market__flow", "낙폭 4.93%p를 만회했습니다. 순환매가 나타났습니다.", "4.93%p"),        # a computed difference
    ("market__flow", "기관은 6천억원 넘게 샀습니다. 순환매가 나타났습니다.", "6천억원"),  # 5,731억원 is not over 6천억
    ("company", "현대로템은 3,500억원에 인수합니다.", "3,500억원"),     # 3,584 rounds to 3,600, not 3,500
])
def test_a_number_the_body_does_not_have_is_rejected(field, text, number):
    found = checks.findings(v5_draft(**{field: text}), contracts.source(pm_row()))
    assert any(f"'{number}'" in f and "없는 숫자" in f for f in found), found


def test_advice_production_wording_citations_and_register_are_rejected_but_topics_pass():
    source = contracts.source(pm_row())
    bad = v5_draft(company="지금 매수하세요. AI가 정리한 내용이며 Claude로 작성했습니다[1]. 출처: https://example.com",
                   lead="오늘 시장은 반등했어요. 기관이 다시 사들였죠.")
    found = " ".join(checks.findings(bad, source))
    for expected in ("매수하세요", "AI가 정리", "Claude", "[1]", "출처:", "https://", "구어체 어미 '어요'", "'죠'"):
        assert expected in found, expected
    # The brief itself is about Anthropic and AI investment: those topics are not production wording.
    assert "앤트로픽" in v5_draft().issues[0].body and checks.findings(v5_draft(), source) == []


def test_structure_needs_two_issues_the_calendar_and_a_number_in_the_title():
    source = contracts.source(pm_row())
    draft = v5_draft(headline="장중 낙폭을 만회한 코스피", accent="")
    short = draft.model_copy(update={"issues": draft.issues[:1], "calendar": []})
    found = checks.findings(short, source)
    assert any(f.startswith("issues:") for f in found) and any(f.startswith("calendar:") for f in found)
    assert any(f.startswith("headline:") for f in found)
    with pytest.raises(ValidationError):
        v5_draft(market__swing={"index": "코스피", "open_level": "5,488.41", "close_level": "5,768.01",
                                "close_change": "+1.76%"})


def test_fallback_uses_only_the_body_text_and_passes_every_check():
    source = contracts.source(pm_row())
    draft = checks.fallback(source)
    assert checks.findings(draft, source) == []
    assert render.subject(draft, source["day"]) == "코스피 1.76% 상승, 5,768.01 마감 | 10/19 마감 브리핑"
    assert [i.headline for i in draft.issues] == [i["headline"] for i in source["issues"]]
    # Each issue: its headline, the first sentence of its fact and its next check.
    assert draft.issues[0].body.startswith("AI 개발사 앤트로픽은") and draft.issues[0].check.startswith("21일 메타 실적")
    assert draft.calendar[0].when == "20일 (화)" and draft.market.numbers[0] == "코스피 5,768.01 pt · 보도상 +1.76%"


def test_source_holds_only_what_the_reader_received():
    row = pm_row()
    hidden = row["proposal"]["issues"][0]["fact"]["evidence"][0]["quote"]
    source = contracts.source(row)
    text = str(source)
    assert hidden not in text and "source_notes" not in text and "evidence" not in text
    assert "Analyst" not in text and "출처" not in [line["section"] for line in source["lines"]]
    assert len(source["issues"]) == 3 and source["issue_count"] == 3


def test_contract_is_claude_only_and_scoped_to_newsletter_requests():
    from quant_company.contracts import ToolRequest
    from quant_company.providers.claude_runner import ClaudeRequest, output_schema

    request = ClaudeRequest(request_id="newsletter-x-1", model="claude-opus-5-5", prompt="p",
                            output_contract="newsletter_v1")
    assert output_schema(request)[0] is contracts.NewsletterDraft
    for bad in ({"request_id": "video-x-1"}, {"web_search": True}):
        with pytest.raises(ValueError):
            ClaudeRequest(**{"request_id": "newsletter-x-1", "model": "claude-opus-5-5", "prompt": "p",
                             "output_contract": "newsletter_v1", **bad})
    with pytest.raises(ValueError):
        ProviderRequest(request_id="newsletter-x-1", model="claude-opus-5-5", prompt="p", output_contract="newsletter_v1")
    # Nothing codex-runtime validates changes: no new tool.
    assert not any("mail" in name for name in ToolRequest.model_fields["name"].annotation.__args__)


def test_the_email_is_not_a_render_input():
    """Ships without a new render image: nothing the render task imports changes (release.render_changes)."""
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("release_for_newsletter", root / "deploy/release.py")
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    changed = [str(p.relative_to(root)) for p in (root / "src/quant_company/newsletter").glob("*")] + [
        "src/quant_company/providers/claude_runner.py", "src/quant_company/providers/codex_runtime.py",
        "src/quant_company/providers/claude_runtime.py", "src/quant_company/runtime.py", "src/quant_company/slack.py",
        "src/quant_company/company.py", "src/quant_company/config.py", "src/quant_company/db.py",
        "src/quant_company/briefing/store.py"]
    assert release.render_changes(changed, [root]) == []
    assert "newsletter" not in (root / "src/quant_company/contracts.py").read_text()


def test_prompt_states_the_rules_and_carries_findings_on_a_rewrite():
    source = contracts.source(pm_row())
    first = contracts.prompt(source)
    for rule in ("~습니다", "metaphors", "round within the same unit", "buy/sell", "AI, models, tools", "footnote"):
        assert rule in first, rule
    again = contracts.prompt(source, v5_draft().model_dump(mode="json"), ["accent: '1.7%' ..."])
    assert "rejected by the service checks" in again and "PREVIOUS VERSION" in again and "'1.7%'" in again


def test_masking_and_command_parsing():
    assert mask("qkrwhdgus824@gmail.com") == "qk***@gmail.com" and mask("ab@x.io") == "a***@x.io"
    assert masked("이메일 추가 <mailto:owner.fixture@example.com|owner.fixture@example.com>") == "이메일 추가 ow***@example.com"
    assert parse("이메일 추가 <mailto:Owner.Fixture@Example.com|Owner.Fixture@Example.com>") == {
        "action": "add", "address": OWNER_MAIL}
    assert parse("이메일 시험발송 owner.fixture@example.com") == {"action": "test", "address": OWNER_MAIL}
    assert parse("이메일 시험 발송 owner.fixture@example.com") == {"action": "test", "address": OWNER_MAIL}
    assert parse("이메일 목록") == {"action": "list"} and parse("이메일 재동의 a@b.co")["action"] == "reconsent"
    assert parse("이메일 추가 a@b.co c@d.co") == {"action": "add", "address": None}
    for conversation in ("이메일 추가하는 방법 알려줘", "이메일 목록 보여줘", "뉴스레터 추가 a@b.co"):
        assert parse(conversation) is None


def test_html_and_plain_text_follow_the_approved_layout():
    draft = v5_draft()
    data = render.message(draft, day=PM_DAY.isoformat(), sender=SENDER, name="뭐든story", to=OWNER_MAIL,
                          message_id="<fixture@gmail.com>", channel_url="https://www.youtube.com/@anything-story",
                          logo=b"\x89PNG fixture").as_bytes()
    parsed = email.message_from_bytes(data, policy=default)
    assert parsed["Subject"] == "코스피, 3.2% 급락 출발 후 1.8% 상승 마감 | 10/19 마감 브리핑"
    assert parsed["From"].addresses[0].display_name == "뭐든story" and parsed["Message-ID"] == "<fixture@gmail.com>"
    assert parsed["List-Unsubscribe"] == f"<mailto:{SENDER}?subject=%EC%88%98%EC%8B%A0%EA%B1%B0%EB%B6%80>"
    assert parsed.get_content_type() == "multipart/alternative"
    plain, related = parsed.get_payload()
    assert plain.get_content_type() == "text/plain" and related.get_content_type() == "multipart/related"
    html, logo = related.get_payload()
    assert logo["Content-ID"] == f"<{render.LOGO_CID}>" and logo.get_content_disposition() == "inline"
    page = html.get_content()
    labels = ["오늘의 시장", "투자자별 매매", "업종·종목", "주요 이슈", "시사점", "▶ 확인할 일정", "기업 소식", "주요 일정",
              "▶ 영상으로 보기", "수신 거부"]
    positions = [page.index(label) for label in labels]
    assert positions == sorted(positions)
    assert f"cid:{render.LOGO_CID}" in page and 'href="https://www.youtube.com/@anything-story"' in page
    assert "font-face" not in page and "<table" in page and "@media" not in page
    for absent in ("Analyst", "AI 시장분석", "[1]", "출처", "근거"):
        assert absent not in page and absent not in plain.get_content()
    text = plain.get_content()
    assert "코스피 출발 5,488.41 (▼ 3.17%) → 마감 5,768.01 (▲ 1.76%)" in text and "기관 +5,731억" in text
    assert render.DISCLAIMER in text and "수신 거부: mailto:" in text


# ---- PostgreSQL ------------------------------------------------------------------------------------------------

@pytest.mark.integration
def test_the_pm_commit_creates_one_email_per_edition_and_none_for_the_morning(mail, brief):  # noqa: F811
    store, _ = mail
    complete(brief)  # a committed AM edition
    assert not rows(store, "SELECT * FROM newsletter_issues")
    edition = commit_pm(brief)
    brief[0].flush()
    issues = rows(store, "SELECT * FROM newsletter_issues")
    assert len(issues) == 1 and str(issues[0]["edition_id"]) == edition.id
    issue = issues[0]
    assert issue["state"] == "writing" and issue["auto"] and issue["max_attempts"] == 3
    assert issue["fallback_at"] == edition.due_at + FALLBACK_AFTER
    assert issue["source_digest"] and issue["source"]["label"] == "10/19"


@pytest.mark.integration
async def test_with_the_flag_off_nothing_is_written_or_sent(mail, brief):  # noqa: F811
    store, _ = mail
    store.settings.newsletter_enabled = False
    recipient(store, OWNER_MAIL)
    edition = commit_pm(brief)
    deliver_root(store, edition.id)
    assert issue_of(store, edition) is None
    writer, provider = await write(store)
    assert await writer.tick() == {"state": "idle"} and not provider.calls
    smtp = SimulatedSmtp()
    dispatcher(store, smtp).tick()
    assert smtp.sessions == 0 and not rows(store, "SELECT * FROM newsletter_sends")


@pytest.mark.integration
def test_an_enqueue_failure_never_holds_back_the_brief(mail, brief, monkeypatch):  # noqa: F811
    store, _ = mail

    def broken(self, conn, edition, at, *, auto):
        conn.execute("SELECT 1 FROM no_such_newsletter_table")

    monkeypatch.setattr(NewsletterStore, "ensure_issue", broken)
    edition = commit_pm(brief)
    assert rows(store, "SELECT state FROM brief_editions WHERE id=%s", edition.id)[0]["state"] == "committed"
    assert issue_of(store, edition) is None


@pytest.mark.integration
def test_an_edition_without_issues_sends_nothing_and_says_so_once_in_its_thread(mail, brief):  # noqa: F811
    store, _ = mail
    recipient(store, OWNER_MAIL)
    edition = commit_pm(brief, pm_proposal(issues=[]))
    issue = issue_of(store, edition)
    assert issue["state"] == "skipped" and issue["reason"] == "no_issues"
    smtp = SimulatedSmtp()
    dispatcher(store, smtp).tick()
    assert not outbox(store, "newsletter_status")  # the brief's root post is not delivered yet
    deliver_root(store, edition.id)
    dispatcher(store, smtp).tick()
    dispatcher(store, smtp).tick()
    notices = outbox(store, "newsletter_status")
    assert len(notices) == 1 and "보내지 않음 · 10/19" in notices[0]["text"] and notices[0]["thread_ts"] == "200.000"
    assert smtp.sessions == 0 and "<@" not in notices[0]["text"]


@pytest.mark.integration
async def test_a_checked_draft_goes_once_to_each_active_recipient_with_receipts(mail, brief):  # noqa: F811
    store, clock = mail
    for address, state in ((OWNER_MAIL, "active"), (FRIEND_MAIL, "active"), ("gone.fixture@example.com", "removed"),
                           ("no.fixture@example.com", "unsubscribed")):
        recipient(store, address, state)
    edition = commit_pm(brief)
    deliver_root(store, edition.id)
    writer, provider = await write(store, v5_draft())
    assert (await writer.tick())["state"] == "ready"
    request = provider.calls[0]
    issue = issue_of(store, edition)
    assert request.output_contract == "newsletter_v1" and request.request_id == f"newsletter-{issue['id']}-1"
    assert issue["state"] == "ready" and issue["origin"] == "model"
    smtp = SimulatedSmtp()
    result = dispatcher(store, smtp).tick()
    assert result["sent"] == 2 and len(smtp.accepted) == 2
    assert sorted(r for r, _ in smtp.accepted) == sorted([OWNER_MAIL, FRIEND_MAIL])
    sends = rows(store, "SELECT * FROM newsletter_sends ORDER BY address")
    assert [s["state"] for s in sends] == ["sent", "sent"]
    owner = rows(store, "SELECT id FROM newsletter_recipients WHERE address=%s", OWNER_MAIL)[0]["id"]
    expected = f"<{stable(f'newsletter:{edition.id}:{owner}')}@gmail.com>"
    sent = email.message_from_bytes(smtp.to(OWNER_MAIL)[0], policy=default)
    assert sent["Message-ID"] == expected and sent["To"] == OWNER_MAIL
    assert sent["Subject"].endswith("| 10/19 마감 브리핑") and sent["List-Unsubscribe"].startswith(f"<mailto:{SENDER}")
    # Another tick, another dispatcher (a restart), a later edition flush: nobody gets it twice.
    clock["at"] += timedelta(minutes=5)
    dispatcher(store, smtp).tick()
    brief[0].flush()
    assert len(smtp.accepted) == 2
    notice = [n for n in outbox(store, "newsletter_status") if "저녁 브리핑 메일" in n["text"]]
    assert len(notice) == 1 and "2명 발송" in notice[0]["text"] and notice[0]["thread_ts"] == "200.000"
    assert issue_of(store, edition)["state"] == "sent"
    assert OWNER_MAIL not in str(rows(store, "SELECT detail FROM events")) + str(outbox(store))


@pytest.mark.integration
async def test_failed_checks_are_rewritten_with_their_findings_then_the_fallback_goes(mail, brief):  # noqa: F811
    store, clock = mail
    recipient(store, OWNER_MAIL)
    edition = commit_pm(brief)
    deliver_root(store, edition.id)
    unbound = v5_draft(accent="1.7% 상승 마감")
    writer, provider = await write(store, unbound, "{\"headline\": 1}", unbound)
    for _ in range(3):
        assert (await writer.tick())["state"] == "attempted"
    assert await writer.tick() == {"state": "idle"}  # out of attempts: nothing more is asked
    attempts = issue_of(store, edition)["attempts"]
    assert [a["outcome"] for a in attempts] == ["check_failed", "invalid_output", "check_failed"]
    second, third = provider.calls[1], provider.calls[2]
    assert "'1.7%'" in second.prompt and "PREVIOUS VERSION" in second.prompt
    assert "invalid_output" in third.prompt and "PREVIOUS VERSION" in third.prompt
    assert len({c.request_id for c in provider.calls}) == 3
    smtp = SimulatedSmtp()
    dispatcher(store, smtp).tick()
    issue = issue_of(store, edition)
    assert issue["origin"] == "fallback" and issue["reason"] == "model_checks_failed" and issue["findings"] == []
    assert len(smtp.to(OWNER_MAIL)) == 1
    sent = email.message_from_bytes(smtp.to(OWNER_MAIL)[0], policy=default)
    assert sent["Subject"] == "코스피 1.76% 상승, 5,768.01 마감 | 10/19 마감 브리핑"
    assert any("검사를 통과하지 못해" in n["text"] for n in outbox(store, "newsletter_status"))


@pytest.mark.integration
async def test_without_a_checked_draft_45_minutes_after_the_commit_the_fallback_goes(mail, brief):  # noqa: F811
    store, clock = mail
    recipient(store, OWNER_MAIL)
    edition = commit_pm(brief)
    deliver_root(store, edition.id)
    busy = ProviderFault("busy", "simulated: a video script holds the lane", 10)
    writer, provider = await write(store, busy)
    assert (await writer.tick())["state"] == "waiting"
    clock["at"] = edition.due_at + FALLBACK_AFTER - timedelta(minutes=1)
    smtp = SimulatedSmtp()
    dispatcher(store, smtp).tick()
    assert issue_of(store, edition)["state"] == "writing" and not smtp.accepted
    clock["at"] = edition.due_at + FALLBACK_AFTER
    assert await writer.tick() == {"state": "idle"}  # the writer stops asking at the fallback time
    dispatcher(store, smtp).tick()
    issue = issue_of(store, edition)
    assert issue["origin"] == "fallback" and issue["reason"] == "model_busy" and len(smtp.accepted) == 1
    assert any("45분 안에" in n["text"] for n in outbox(store, "newsletter_status"))
    # A late model answer cannot replace what was sent.
    assert not NewsletterStore(store.company).accept(issue, v5_draft(), {"outcome": "passed"})


@pytest.mark.integration
async def test_a_still_running_turn_is_asked_again_by_the_same_id(mail, brief):  # noqa: F811
    store, clock = mail
    edition = commit_pm(brief)
    pending = ProviderFault("pending", "simulated: still running", 20)
    writer, provider = await write(store, pending, v5_draft())
    await writer.tick()
    clock["at"] += timedelta(seconds=20)
    await writer.tick()
    assert provider.calls[0].model_dump() == provider.calls[1].model_dump()
    assert issue_of(store, edition)["state"] == "ready" and len(issue_of(store, edition)["attempts"]) == 1


@pytest.mark.integration
async def test_an_interrupted_send_is_uncertain_and_never_sent_again(mail, brief):  # noqa: F811
    store, clock = mail
    recipient(store, OWNER_MAIL)
    recipient(store, FRIEND_MAIL)
    edition = commit_pm(brief)
    deliver_root(store, edition.id)
    writer, _ = await write(store, v5_draft())
    await writer.tick()
    smtp = SimulatedSmtp()
    smtp.drop_in_data.add(OWNER_MAIL)
    for _ in range(3):
        dispatcher(store, smtp).tick()
        clock["at"] += timedelta(minutes=1)
    states = {r["address"]: r for r in rows(store, "SELECT * FROM newsletter_sends")}
    assert states[OWNER_MAIL]["state"] == "uncertain" and states[OWNER_MAIL]["error"] == "smtp_data_interrupted"
    assert states[OWNER_MAIL]["attempts"] == 1 and states[FRIEND_MAIL]["state"] == "sent"
    assert smtp.to(FRIEND_MAIL) and not smtp.to(OWNER_MAIL)
    notice = [n["text"] for n in outbox(store, "newsletter_status") if "저녁 브리핑 메일" in n["text"]]
    assert notice and "결과 불확실 1명(자동으로 다시 보내지 않음)" in notice[0]


@pytest.mark.integration
async def test_a_send_left_in_progress_becomes_uncertain_and_a_refused_login_sends_nothing(mail, brief):  # noqa: F811
    store, clock = mail
    recipient(store, OWNER_MAIL)
    recipient(store, FRIEND_MAIL)
    edition = commit_pm(brief)
    writer, _ = await write(store, v5_draft())
    await writer.tick()
    refused = SimulatedSmtp(password="another-password")
    dispatcher(store, refused).tick()
    assert not refused.accepted
    assert {r["state"] for r in rows(store, "SELECT state FROM newsletter_sends")} == {"pending"}
    # A crash between 'sending' and the SMTP reply: the row stays as it was and is reconciled, never replayed.
    with store.db.transaction() as conn:
        conn.execute("UPDATE newsletter_sends SET state='sending',started_at=%s WHERE address=%s",
                     (clock["at"], OWNER_MAIL))
    clock["at"] += timedelta(minutes=11)
    smtp = SimulatedSmtp()
    dispatcher(store, smtp).tick()
    states = {r["address"]: r["state"] for r in rows(store, "SELECT * FROM newsletter_sends")}
    assert states == {OWNER_MAIL: "uncertain", FRIEND_MAIL: "sent"} and not smtp.to(OWNER_MAIL)
    assert issue_of(store, edition)["state"] in {"sending", "sent"}


@pytest.mark.integration
async def test_a_missing_or_empty_password_leaves_the_email_inactive(mail, brief):  # noqa: F811
    store, _ = mail
    recipient(store, OWNER_MAIL)
    store.settings.newsletter_password_file.unlink()
    edition = commit_pm(brief)
    deliver_root(store, edition.id)
    writer, _ = await write(store, v5_draft())
    await writer.tick()
    smtp, inbox = SimulatedSmtp(), SimulatedInbox(reply(OWNER_MAIL, "수신거부", "a"))
    dispatcher(store, smtp, inbox).tick()
    store.settings.newsletter_password_file.write_text("\n")
    dispatcher(store, smtp, inbox).tick()
    assert smtp.sessions == 0 and not rows(store, "SELECT * FROM newsletter_sends")
    assert not inbox.mails[b"1"]["seen"]
    command(store, f"이메일 시험 발송 {OWNER_MAIL}", "test-unconfigured")
    dispatcher(store, smtp, inbox).tick()
    test = rows(store, "SELECT * FROM newsletter_sends WHERE kind='test'")[0]
    assert test["state"] == "failed" and test["error"] == "sender_not_configured" and smtp.sessions == 0
    reply_text = [n["text"] for n in outbox(store) if "시험 발송 실패" in n["text"]]
    assert reply_text and "앱 비밀번호" in reply_text[0] and OWNER_MAIL not in reply_text[0]


@pytest.mark.integration
async def test_an_unsubscribe_mail_stops_the_recipient_and_only_reconsent_restarts_it(mail, brief):  # noqa: F811
    store, _ = mail
    recipient(store, OWNER_MAIL)
    edition = commit_pm(brief)
    deliver_root(store, edition.id)
    inbox = SimulatedInbox(reply(OWNER_MAIL, "Re: 수신거부", "u1"), reply("stranger@example.net", "수신거부", "u2"),
                           reply(OWNER_MAIL, "질문이 있습니다", "u3"))
    dispatcher(store, inbox=inbox).tick()
    assert rows(store, "SELECT state FROM newsletter_recipients")[0]["state"] == "unsubscribed"
    assert [inbox.mails[n]["seen"] for n in (b"1", b"2", b"3")] == [True, False, False]
    notices = [n for n in outbox(store, "newsletter_status") if "수신 거부" in n["text"]]
    assert len(notices) == 1 and "ow***@example.com" in notices[0]["text"] and "<@" not in notices[0]["text"]
    assert notices[0]["thread_ts"] == "200.000" and OWNER_MAIL not in notices[0]["text"]
    dispatcher(store, inbox=inbox).tick()  # read again: nothing new
    assert len(rows(store, "SELECT * FROM newsletter_unsubscribes")) == 1
    command(store, f"이메일 추가 {OWNER_MAIL}", "add-again")
    assert rows(store, "SELECT state FROM newsletter_recipients")[0]["state"] == "unsubscribed"
    command(store, f"이메일 재동의 {OWNER_MAIL}", "reconsent")
    assert rows(store, "SELECT state FROM newsletter_recipients")[0]["state"] == "active"
    replies = [n["text"] for n in outbox(store, "control")]
    assert "직접 수신 거부한 주소라 다시 켜지 않았습니다" in replies[0] and "재동의를 확인한 것으로 기록" in replies[1]


@pytest.mark.integration
def test_owner_commands_through_signed_slack_are_masked_and_owner_only(mail, credentials):
    store, _ = mail
    company = store.company
    company.settings.slack_allowed_users = ["UHUMAN", "UOTHER"]
    client = TestClient(create_app(company.settings, company, credentials))

    def send(text, ts, **overrides):
        raw, headers = signed(event(credentials, text=f"<@UBOT0> {text}", ts=ts, **overrides), credentials["director"])
        return client.post("/slack/events/director", content=raw, headers=headers).json()

    linked = f"<mailto:{OWNER_MAIL}|{OWNER_MAIL}>"
    assert send(f"이메일 추가 {linked}", "300.001")["owner_control"]
    assert send(f"이메일 추가 {linked}", "300.001")["duplicate"]
    assert send(f"이메일 추가 {FRIEND_MAIL}", "300.002", user="UOTHER")["reason"] == "newsletter_owner_scope"
    send(f"이메일 추가 {FRIEND_MAIL}", "300.003")
    send(f"이메일 삭제 {FRIEND_MAIL}", "300.004")
    send("이메일 추가 not-an-address", "300.005")
    send("이메일 목록", "300.006")
    states = {r["address"]: r["state"] for r in rows(store, "SELECT * FROM newsletter_recipients")}
    assert states == {OWNER_MAIL: "active", FRIEND_MAIL: "removed"}
    replies = [r["text"] for r in outbox(store, "control")]
    assert "수신자 추가 · ow***@example.com" in replies[0] and "자동 발송은" not in replies[0]
    assert "수신을 껐습니다 · fr***@example.org" in replies[2] and "형식을 확인하지 못했습니다" in replies[3]
    assert "수신 중 1 · 삭제 1 · 수신 거부 0" in replies[4] and "• ow***@example.com · 수신 중" in replies[4]
    stored = str(rows(store, "SELECT instruction,result FROM tasks")) + str(rows(store, "SELECT text FROM messages"))
    stored += str(rows(store, "SELECT title,instruction FROM projects")) + str(rows(store, "SELECT receipt FROM newsletter_commands"))
    stored += str(rows(store, "SELECT text FROM outbox")) + str(rows(store, "SELECT detail FROM events"))
    assert OWNER_MAIL not in stored and FRIEND_MAIL not in stored
    assert not rows(store, "SELECT * FROM turns")  # no model saw any of it
    # Another employee never receives a recipient command (its model would see the address).
    raw, headers = signed(event(credentials, role="financial_strategist", text=f"이메일 추가 {FRIEND_MAIL}",
                                channel="DSTRATEGIST", channel_type="im", ts="300.007"), credentials["financial_strategist"])
    assert client.post("/slack/events/financial_strategist", content=raw,
                       headers=headers).json()["reason"] == "newsletter_director_only"


@pytest.mark.integration
async def test_a_test_send_works_with_the_flag_off_and_reaches_only_the_named_address(mail, brief):  # noqa: F811
    store, clock = mail
    store.settings.newsletter_enabled = False
    recipient(store, FRIEND_MAIL)
    edition = commit_pm(brief)
    deliver_root(store, edition.id)
    assert issue_of(store, edition) is None and not NewsletterStore(store.company).has_writing()
    command(store, f"이메일 시험 발송 {OWNER_MAIL}", "test-1")
    issue = issue_of(store, edition)
    assert issue["state"] == "writing" and not issue["auto"] and issue["max_attempts"] == 1
    assert NewsletterStore(store.company).has_writing()  # the dispatcher now starts the writer workflow
    writer, provider = await write(store, v5_draft())
    assert (await writer.tick())["state"] == "ready" and len(provider.calls) == 1
    smtp = SimulatedSmtp()
    dispatcher(store, smtp).tick()
    assert [r for r, _ in smtp.accepted] == [OWNER_MAIL]
    assert not rows(store, "SELECT * FROM newsletter_sends WHERE kind='regular'")
    done = [n for n in outbox(store) if "시험 발송 완료" in n["text"]]
    assert len(done) == 1 and done[0]["thread_ts"] == "900.000" and "ow***@example.com" in done[0]["text"]
    # A second request sends again (its own Message-ID); the email is not rewritten.
    command(store, f"이메일 시험 발송 {OWNER_MAIL}", "test-2")
    dispatcher(store, smtp).tick()
    ids = {email.message_from_bytes(data, policy=default)["Message-ID"] for data in smtp.to(OWNER_MAIL)}
    assert len(ids) == 2 and len(provider.calls) == 1
    command(store, f"이메일 시험 발송 {FRIEND_MAIL}", "test-3")
    with store.db.transaction() as conn:
        conn.execute("UPDATE newsletter_recipients SET state='unsubscribed' WHERE address=%s", (FRIEND_MAIL,))
    command(store, f"이메일 시험 발송 {FRIEND_MAIL}", "test-4")
    dispatcher(store, smtp).tick()
    assert not smtp.to(FRIEND_MAIL)
    assert any("시험 메일도 보내지 않습니다" in n["text"] for n in outbox(store, "control"))


@pytest.mark.integration
async def test_newsletter_workflow_survives_a_worker_restart(mail, brief, temporal_environment):  # noqa: F811
    import asyncio
    from uuid import uuid4

    from temporalio.worker import Replayer

    from quant_company.newsletter.workflow import NewsletterWorkflow
    from quant_company.runtime import make_newsletter_worker

    store, _ = mail
    edition = commit_pm(brief)
    store.settings.temporal_task_queue = "newsletter-" + uuid4().hex
    provider = SimulatedProvider(v5_draft())
    client = temporal_environment.client
    async with make_newsletter_worker(client, store.company, NewsletterWriter(store.company, provider=provider)):
        handle = await client.start_workflow(NewsletterWorkflow.run, id=store.settings.temporal_task_queue,
                                             task_queue=store.settings.temporal_task_queue + "-newsletter")
        async with asyncio.timeout(15):
            while issue_of(store, edition)["state"] == "writing":
                await asyncio.sleep(.1)
    async with make_newsletter_worker(client, store.company, NewsletterWriter(store.company, provider=provider)):
        try:
            await asyncio.sleep(2)
            await Replayer(workflows=[NewsletterWorkflow]).replay_workflow(await handle.fetch_history())
            assert len(provider.calls) == 1 and issue_of(store, edition)["origin"] == "model"
        finally:
            await handle.cancel()



@pytest.mark.integration
async def test_dispatch_starts_the_writer_only_when_there_is_something_to_write(mail, brief):  # noqa: F811
    from quant_company.runtime import start_newsletter

    class Client:
        """Simulated Temporal client: records workflow starts only."""

        def __init__(self):
            self.started = []

        async def start_workflow(self, run, id, task_queue, id_reuse_policy):
            self.started.append((id, task_queue))

    store, _ = mail
    store.settings.newsletter_enabled = False
    store.company._newsletter = dispatcher(store)
    client = Client()
    await start_newsletter(client, store.company)
    await store.company._newsletter_tick
    assert client.started == []
    commit_pm(brief)
    command(store, f"이메일 시험 발송 {OWNER_MAIL}", "test-start")
    store.company._newsletter.next_tick = 0
    await start_newsletter(client, store.company)
    await start_newsletter(client, store.company)
    assert client.started == [("company-newsletter-v1", store.settings.temporal_task_queue + "-newsletter")]
    await store.company._newsletter_tick


def test_the_fallback_holds_for_a_thin_edition():
    from quant_company.briefing.contracts import BriefProposal

    from .newsletter_fixtures import issue

    data = pm_proposal(issues=[issue(1, "금리", "금리가 올랐습니다.", "부담입니다.", "확인합니다.")]).model_dump(mode="json")
    data.update(summary=[], overview=[], observations=[], internals=[], calendar=[])
    source = contracts.source(pm_row(BriefProposal.model_validate(data)))
    draft = checks.fallback(source)
    assert draft.issues[0].headline == "금리" and draft.market.swing is None and draft.calendar == []
    assert not [f for f in checks.findings(draft, source) if "없는 숫자" in f]
    assert "금리가 올랐습니다." in render.html(draft, day=source["day"], sender=SENDER, channel_url="https://example.com")

"""Sending (Gmail SMTP) and the unsubscribe inbox (Gmail IMAP), in the dispatcher only.

The app password is read from its mounted file on every check, so a password set while dispatch runs is used at the
next tick and an absent or empty file leaves both inactive. SMTP has no idempotency key: each row is committed as
'sending' before the session names its recipient, and anything that goes wrong from the DATA command on leaves the
row 'uncertain' (never sent again automatically)."""

import email
import hashlib
import imaplib
import logging
import re
import smtplib
import ssl
import time
from email.policy import default
from email.utils import parseaddr

from . import store as records
from .contracts import NewsletterDraft
from .render import message
from .store import NewsletterStore, mask

LOG = logging.getLogger(__name__)
TICK_SECONDS, INBOX_SECONDS = 10, 300
SESSION_LIMIT = 10
MAX_ATTEMPTS = 6
UNSUBSCRIBE = re.compile(r"수신\s*거부|unsubscribe", re.I)


class Rejected(Exception):
    """The server answered with an error code: it does not hold the message."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


class Broken(Exception):
    """The session ended before the DATA command: the message was not handed over."""


class Uncertain(Exception):
    """The session ended from the DATA command on: the message may have been accepted."""


def password(settings):
    path = settings.newsletter_password_file
    if not path:
        return ""
    try:
        return re.sub(r"\s+", "", path.read_text())[:128]
    except OSError:
        return ""


class SmtpMailer:
    def __init__(self, settings, secret, connect=smtplib.SMTP):
        self.settings, self.secret, self.connect, self.smtp = settings, secret, connect, None

    def __enter__(self):
        s = self.settings
        self.smtp = self.connect(s.newsletter_smtp_host, s.newsletter_smtp_port, timeout=30)
        try:
            self.smtp.ehlo()
            self.smtp.starttls(context=ssl.create_default_context())
            self.smtp.ehlo()
            self.smtp.login(s.newsletter_from_address, self.secret)
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *exc):
        try:
            self.smtp.quit()
        except (smtplib.SMTPException, OSError):
            self.close()

    def close(self):
        try:
            self.smtp.close()
        except (smtplib.SMTPException, OSError):
            pass

    def reset(self):
        try:
            self.smtp.rset()
        except (smtplib.SMTPException, OSError):
            raise Broken() from None

    def send(self, recipient, data):
        sender = self.settings.newsletter_from_address
        try:
            code, _ = self.smtp.mail(sender)
            if code != 250:
                raise Rejected(code)
            code, _ = self.smtp.rcpt(recipient)
            if code not in (250, 251):
                raise Rejected(code)
        except Rejected:
            self.reset()
            raise
        except smtplib.SMTPResponseException as error:
            self.reset()
            raise Rejected(error.smtp_code) from None
        except (smtplib.SMTPException, OSError):
            raise Broken() from None
        try:
            self.smtp.data(data)
        except smtplib.SMTPDataError as error:
            # An error reply to DATA or to the message itself: refused, not delivered.
            self.reset()
            raise Rejected(error.smtp_code) from None
        except (smtplib.SMTPException, OSError):
            raise Uncertain() from None


class ImapInbox:
    def __init__(self, settings, secret, connect=imaplib.IMAP4_SSL):
        self.settings, self.secret, self.connect, self.imap = settings, secret, connect, None

    def __enter__(self):
        s = self.settings
        self.imap = self.connect(s.newsletter_imap_host, 993, ssl_context=ssl.create_default_context(), timeout=30)
        self.imap.login(s.newsletter_from_address, self.secret)
        self.imap.select("INBOX")
        return self

    def __exit__(self, *exc):
        try:
            self.imap.logout()
        except (imaplib.IMAP4.error, OSError):
            pass

    def unread(self, limit=100):
        """(number, sender address, subject, digest) of unread mails, without marking them read."""
        status, data = self.imap.search(None, "UNSEEN")
        if status != "OK":
            return []
        rows = []
        for number in (data[0] or b"").split()[-limit:]:
            status, parts = self.imap.fetch(number, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT MESSAGE-ID DATE)])")
            header = next((p[1] for p in parts or [] if isinstance(p, tuple)), b"") if status == "OK" else b""
            mail = email.message_from_bytes(header, policy=default)
            sender = parseaddr(str(mail.get("From", "")))[1].strip().lower()
            key = str(mail.get("Message-ID") or "") or f"{sender}|{mail.get('Date', '')}|{mail.get('Subject', '')}"
            rows.append((number, sender, str(mail.get("Subject", "")), hashlib.sha256(key.encode()).hexdigest()))
        return rows

    def seen(self, number):
        self.imap.store(number, "+FLAGS", "\\Seen")


class NewsletterDispatcher:
    def __init__(self, company, smtp=smtplib.SMTP, imap=imaplib.IMAP4_SSL, clock=time.monotonic):
        self.company, self.settings = company, company.settings
        self.store = NewsletterStore(company)
        self.smtp, self.imap, self.clock = smtp, imap, clock
        self.next_tick = self.next_inbox = 0.0

    def configured(self):
        secret = password(self.settings)
        return secret if self.settings.newsletter_from_address and secret else ""

    def due(self):
        return self.clock() >= self.next_tick

    def tick_if_due(self):
        if not self.due():
            return None
        self.next_tick = self.clock() + TICK_SECONDS
        try:
            return self.tick()
        except Exception as error:  # noqa: BLE001 - the email never stops the dispatcher's Slack work
            LOG.error("newsletter tick failed: %s", type(error).__name__)
            return {"state": "failed", "error": type(error).__name__}

    def tick(self, at=None):
        at = at or records.utcnow()
        result = {"uncertain": self.store.recover(at), "fallbacks": self.store.fallbacks(at)}
        secret = self.configured()
        if secret and self.settings.newsletter_enabled:
            result["fanned_out"] = self.store.fan_out(at)
        if not secret:
            result["unconfigured"] = self.store.fail_unconfigured()
        result["sent"] = self.send(secret, at) if secret else 0
        result["notices"] = self.store.notices(at)
        if secret and self.settings.newsletter_enabled and self.clock() >= self.next_inbox:
            self.next_inbox = self.clock() + INBOX_SECONDS
            result["unsubscribed"] = self.read_inbox(secret)
        return result

    def send(self, secret, at):
        rows = self.store.due(at, SESSION_LIMIT)
        if not rows:
            return 0
        sent = 0
        try:
            with SmtpMailer(self.settings, secret, self.smtp) as session:
                for row in rows:
                    if not self.store.begin(row, at):
                        continue
                    try:
                        data = self.compose(row)
                    except (ValueError, OSError) as error:
                        self.store.finish(row, "failed", error="compose_" + type(error).__name__, at=at)
                        continue
                    more = row["attempts"] + 1 < MAX_ATTEMPTS
                    try:
                        session.send(row["address"], data)
                    except Rejected as error:
                        retry = 300 if 400 <= int(error.code or 0) < 500 and more else None
                        self.store.finish(row, "failed", error=f"smtp_{error.code}", retry=retry, at=at)
                        continue
                    except Broken:
                        # Nothing reached the DATA command, so trying again later cannot duplicate the email.
                        self.store.finish(row, "failed", error="smtp_session_lost", retry=60 if more else None, at=at)
                        break
                    except Uncertain:
                        self.store.finish(row, "uncertain", error="smtp_data_interrupted", at=at)
                        LOG.warning("newsletter send to %s is uncertain; it is not sent again", mask(row["address"]))
                        break
                    self.store.finish(row, "sent", receipt={"message_id": row["message_id"],
                                                           "sha256": hashlib.sha256(data).hexdigest()}, at=at)
                    sent += 1
        except smtplib.SMTPAuthenticationError:
            LOG.error("newsletter SMTP login was refused; check the sender address and app password")
            self.store.retry_session(rows, "smtp_auth", MAX_ATTEMPTS, at)
        except (smtplib.SMTPException, OSError) as error:
            LOG.error("newsletter SMTP session failed: %s", type(error).__name__)
            self.store.retry_session(rows, "smtp_unavailable", MAX_ATTEMPTS, at)
        return sent

    def compose(self, row):
        draft = NewsletterDraft.model_validate(row["draft"])
        s = self.settings
        mail = message(draft, day=row["source"]["day"], sender=s.newsletter_from_address, name=s.newsletter_from_name,
                       to=row["address"], message_id=row["message_id"], channel_url=s.newsletter_channel_url)
        return mail.as_bytes()

    def read_inbox(self, secret):
        changed = 0
        try:
            with ImapInbox(self.settings, secret, self.imap) as inbox:
                for number, sender, subject, digest in inbox.unread():
                    if not UNSUBSCRIBE.search(subject) or not sender:
                        continue
                    result = self.store.unsubscribe(sender, digest)
                    if result is None:
                        continue  # not a recipient: the owner reads it in the mailbox
                    changed += bool(result)
                    inbox.seen(number)
        except (imaplib.IMAP4.error, OSError) as error:
            LOG.error("newsletter inbox check failed: %s", type(error).__name__)
        return changed

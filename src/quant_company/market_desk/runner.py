import asyncio
import json
import logging
from datetime import date, time, timedelta
from typing import Literal

from pydantic import AwareDatetime, BaseModel, FiniteFloat, ValidationError

from ..briefing import schedule
from ..contracts import ProviderFault, ProviderRequest
from . import disclosures, editor, lead, note
from .store import DeskStore

LOG = logging.getLogger(__name__)
PREFIX = "app/market_desk/"


class MarketMove(BaseModel):
    id: str
    kind: Literal["index", "sector"]
    subject: str
    from_at: AwareDatetime
    observed_at: AwareDatetime
    change: FiniteFloat
    unit: Literal["%", "%p"]
    companies: list[str]


class MarketInput(BaseModel):
    version: Literal[1]
    quote_delay_minutes: Literal[20]
    observed_at: AwareDatetime
    trade_date: date
    large_companies: list[str]
    moves: list[MarketMove]


class SessionObservation(BaseModel):
    observed_at: AwareDatetime
    headline: str
    market: str
    sectors: str
    watch: str


class BoardStorage:
    def __init__(self, bucket, credential_endpoint=""):
        # The production lake image already ships botocore for the S3 report publisher.
        from botocore.config import Config
        from botocore.credentials import CredentialResolver
        from botocore.session import Session

        if credential_endpoint:
            from .identity import CertificateProvider

            session = Session()
            provider = CertificateProvider(credential_endpoint)
            session.register_component("credential_provider", CredentialResolver([provider]))
        else:
            session = Session(profile="market-desk")
            session.set_config_variable("credentials_file", "/run/market-desk/credentials")
        self.s3 = session.create_client("s3", region_name="ap-northeast-2",
                                        config=Config(connect_timeout=4, read_timeout=8, retries={"max_attempts": 1}))
        self.bucket = bucket

    def market(self):
        obj = self.s3.get_object(Bucket=self.bucket, Key=PREFIX + "input.json")
        with obj["Body"] as stream:
            raw = stream.read(512_001)
        if len(raw) > 512_000:
            raise ValueError("oversize market observations")
        return json.loads(raw)

    def write(self, board):
        self.s3.put_object(Bucket=self.bucket, Key=PREFIX + "board.json", ContentType="application/json",
                           Body=json.dumps(board, ensure_ascii=False, allow_nan=False).encode())


class MarketDesk:
    def __init__(self, company, provider, storage=None, filings=disclosures.recent, clock=schedule.utcnow):
        self.company, self.provider = company, provider
        self.store, self.storage = DeskStore(company), storage
        self.filings, self.clock = filings, clock

    def session(self, at):
        local = at.astimezone(schedule.KST)
        if local.time() < time(9) or local.time() > time(16, 40):
            return False
        close = schedule.close("KR", local.date(), schedule.overrides(self.company.settings))
        return close is not None and at <= close + timedelta(minutes=5)

    def read_market(self, at):
        raw = self.storage.market()
        value = MarketInput.model_validate(raw)
        if value.observed_at > at:
            return None
        if raw.get("session"):
            try:
                session = SessionObservation.model_validate(raw["session"])
                raw["session"] = (session.model_dump(mode="json") if session.observed_at <= value.observed_at
                                  and session.observed_at.astimezone(schedule.KST).date() == value.trade_date else None)
            except ValidationError:
                raw["session"] = None
        if at - value.observed_at > timedelta(minutes=25) or value.trade_date != at.astimezone(schedule.KST).date():
            return {**raw, "moves": [], "large_companies": []}
        return raw

    def collect(self):
        at = self.clock()
        active = self.session(at)
        if self.storage is None:
            settings = self.company.settings
            self.storage = BoardStorage(settings.insight_market_bucket, settings.insight_market_credential_endpoint)
        market = None
        try:
            market = self.read_market(at)
        except Exception as exc:
            LOG.warning("Market desk KRX input unavailable: %s", type(exc).__name__)
        filings, filings_at = [], None
        if active:
            try:
                filings = self.filings(at)
                filings_at = at.isoformat()
            except Exception as exc:
                LOG.warning("Market desk disclosures unavailable: %s", type(exc).__name__)
        self.store.publish(at, market, filings, filings_at, active, self.storage.write)
        local = at.astimezone(schedule.KST)
        frequent = local.weekday() < 5 and time(6, 30) <= local.time() < time(20, 30)
        return {"state": "published", "active": active, "next_delay": 300 if frequent else 1800}

    async def tick(self):
        return await asyncio.to_thread(self.collect)

    async def write_article(self):
        at = self.clock()
        close = schedule.close("KR", at.astimezone(schedule.KST).date(), schedule.overrides(self.company.settings))
        closing = lead.phase_at(at, close) == "close"
        if not closing and not self.session(at):
            return
        row = await asyncio.to_thread(self.store.claim, at, closing)
        if row is None:
            return
        is_note = row["packet"]["kind"] == "note"
        if is_note and at - row["created_at"] > note.LATE:
            # Its map is too old to describe the market now; the next note will.
            await asyncio.to_thread(self.store.finish, row, "failed", self.clock())
            return
        try:
            result = await self.provider.run(ProviderRequest.model_validate(row["request"]))
            writer = {"session": lead, "note": note}.get(row["packet"]["kind"], editor)
            article = writer.edited(result, row, self.clock())
        except ProviderFault as fault:
            # A queue clears in minutes. A note refused for quota or an outage skips its round (design §6.6): the
            # video lane shares the subscription and must never be starved by notes.
            retry = fault.code in {"busy", "pending"} or (fault.code in {"quota", "unavailable"} and not is_note)
            after = self.clock() + timedelta(seconds=max(120, fault.retry_after_seconds))
            await asyncio.to_thread(self.store.finish, row, "queued" if retry else "failed", after)
            return
        except ValueError:
            LOG.warning("Market desk draft rejected for revision %s", row["revision"])
            await asyncio.to_thread(self.store.finish, row, "failed", self.clock())
            return
        await asyncio.to_thread(self.store.finish, row, "written", self.clock(), article)
        if article and (closing or row["packet"]["kind"] == "note"):
            # A written note reaches the board now, not at the next publication round.
            await self.tick()

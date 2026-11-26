import asyncio
import json
import logging
from datetime import datetime, time, timedelta

from ..briefing import schedule
from ..contracts import ProviderFault, ProviderRequest
from . import disclosures, editor
from .store import DeskStore

LOG = logging.getLogger(__name__)
PREFIX = "app/market_desk/"


class BoardStorage:
    def __init__(self, bucket):
        from botocore.config import Config
        from botocore.session import Session

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
        value = json.loads(raw)
        if value["version"] != 1 or value["quote_delay_minutes"] != 20:
            raise ValueError("unknown market observation contract")
        return value

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

    def collect(self):
        at = self.clock()
        active = self.session(at)
        if self.storage is None:
            self.storage = BoardStorage(self.company.settings.insight_market_bucket)
        market = None
        try:
            market = self.storage.market()
        except Exception as exc:
            LOG.warning("Market desk KRX input unavailable: %s", type(exc).__name__)
        filings, filings_at = [], None
        if active:
            try:
                filings = self.filings(at)
                filings_at = at.isoformat()
            except Exception as exc:
                LOG.warning("Market desk disclosures unavailable: %s", type(exc).__name__)
        if market:
            stamp = datetime.fromisoformat(market["observed_at"])
            if stamp.tzinfo is None or stamp > at:
                market = None
            elif not (at - timedelta(minutes=25) <= stamp and
                    market["trade_date"] == at.astimezone(schedule.KST).date().isoformat()):
                market = {**market, "moves": [], "large_companies": []}
        self.store.publish(at, market, filings, filings_at, active, self.storage.write)
        local = at.astimezone(schedule.KST)
        frequent = local.weekday() < 5 and time(6, 30) <= local.time() < time(20, 30)
        return {"state": "published", "active": active, "next_delay": 300 if frequent else 1800}

    async def tick(self):
        return await asyncio.to_thread(self.collect)

    async def write_article(self):
        at = self.clock()
        if not self.session(at):
            return
        row = await asyncio.to_thread(self.store.claim, at)
        if row is None:
            return
        try:
            result = await self.provider.run(ProviderRequest.model_validate(row["request"]))
            article = editor.edited(result, row, self.clock())
        except ProviderFault as fault:
            retry = fault.code in {"busy", "pending", "quota", "unavailable"}
            after = self.clock() + timedelta(seconds=max(120, fault.retry_after_seconds))
            await asyncio.to_thread(self.store.finish, row, "queued" if retry else "failed", after)
            return
        except ValueError:
            LOG.warning("Market desk draft rejected for revision %s", row["revision"])
            await asyncio.to_thread(self.store.finish, row, "failed", self.clock())
            return
        await asyncio.to_thread(self.store.finish, row, "written", self.clock(), article)

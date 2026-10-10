"""Credential-isolated data activity for the lake-enabled briefing worker."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from temporalio import activity

from ..lake_tools import _query_lock
from . import schedule
from .contracts import BriefEdition
from .data_reader import fetch_chart, read_us_close, summarize
from .store import BriefStore


def query(root, edition):
    if not root:
        return {"ok": False, "error": "lake_not_connected"}
    env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "SSL_CERT_FILE",
           "AWS_SHARED_CREDENTIALS_FILE", "AWS_PROFILE", "AWS_DEFAULT_REGION", "QDATA_CODE_COMMIT") if key in os.environ}
    env.update(QDATA_LAKE=root, AWS_EC2_METADATA_DISABLED="true", PYTHONDONTWRITEBYTECODE="1",
               OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", PYTHON_DOTENV_DISABLED="1",
               PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    if not _query_lock.acquire(timeout=1):
        return {"ok": False, "error": "lake_reader_busy"}
    try:
        result = subprocess.run([sys.executable, "-m", "quant_company.briefing.data_reader"],
            input=json.dumps({"definition": edition.model_dump(mode="json")}), text=True,
            capture_output=True, timeout=45, env=env)
        if result.returncode or len(result.stdout.encode()) > 131072:
            return {"ok": False, "error": "lake_reader_failed"}
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, ValueError):
        return {"ok": False, "error": "lake_reader_timeout_or_invalid_result"}
    finally:
        _query_lock.release()


class BriefDataCollector:
    def __init__(self, company, reader=query, chart=fetch_chart):
        self.store, self.reader, self.chart = BriefStore(company), reader, chart

    def poll_us_close(self):
        """One recorded close-poll slot per tick; a restart re-reads only a slot that was never saved."""
        target = self.store.us_close_target()
        if not target:
            return None
        poll = read_us_close(target["definition"], target["catalogue"], target["slot"], self.chart,
                             clock=schedule.utcnow)
        return self.store.save_us_close(target, poll)

    async def tick(self):
        polled = await asyncio.to_thread(self.poll_us_close)
        extra = {"us_close": polled} if polled else {}
        row = await asyncio.to_thread(self.store.claim_data)
        if not row:
            return {"state": "idle", **extra}
        edition = BriefEdition.model_validate(row["definition"])
        snapshot = await asyncio.to_thread(self.reader, self.store.company.settings.company_lake_uri, edition)
        result = await asyncio.to_thread(summarize, snapshot, edition, schedule.overrides(self.store.company.settings))
        await asyncio.to_thread(self.store.save_data, row, result)
        return {"state": "collected", "edition_id": str(row["id"]), "observations": len(result["observations"]), **extra}

    @activity.defn(name="company_brief_data")
    async def activity_tick(self):
        return await self.tick()

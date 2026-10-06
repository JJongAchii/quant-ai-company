"""Read-only live summary/Slack checks, with credentials kept inside the service."""

import hashlib
import importlib.util
import json
from pathlib import Path

import httpx

from quant_company.company import Company
from quant_company.config import Settings
from quant_company.data_watch.reporting import list_text, status_text
from quant_company.data_watch.store import DataWatchStore

company = Company(Settings())
store = DataWatchStore(company)
snapshot = store.status()
with company.db.transaction() as conn:
    message = conn.execute("""SELECT o.id,o.status,o.sent_ts,o.text,o.created_at
        FROM data_watch_publications p JOIN outbox o ON o.id=p.id
        WHERE p.kind='summary' AND p.policy=%s AND o.text LIKE '*데이터 업데이트 현황%%'
        ORDER BY o.created_at DESC LIMIT 1""", (store.policy(),)).fetchone()
readback = None
if message and message["status"] == "delivered":
    credential = json.loads(company.settings.slack_credentials_file.read_text())["data"]
    response = httpx.get("https://slack.com/api/conversations.history",
                        headers={"Authorization": "Bearer " + credential["bot_token"]},
                        params={"channel": company.settings.data_watch_channel_id, "limit": 30}, timeout=20).json()
    if response.get("ok"):
        matches = [item for item in response["messages"] if item.get("ts") == message["sent_ts"]]
        if matches:
            item = matches[0]
            readback = {"ts": item["ts"], "client_msg_id": item.get("client_msg_id"), "text": item.get("text"),
                        "bot_user_matched": item.get("user") == credential["bot_user_id"]}
    else:
        readback = {"error": response.get("error")}
root = Path(importlib.util.find_spec("quant_company").origin).parent / "data_watch"
inventory = snapshot["inventory"]
summary, listing = status_text(snapshot), list_text(snapshot)
print(json.dumps({
    "observedAt": snapshot["checked_at"],
    "inventory": {"ok": inventory["receipt"].get("ok"), "checked_at": inventory["checked_at"]} if inventory else None,
    "datasetCount": len(snapshot["datasets"]),
    "sourceHashes": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in ("coverage.py", "reporting.py")},
    "currentSummary": summary, "currentSummaryCharacters": len(summary),
    "currentList": listing, "currentListCharacters": len(listing),
    "dailyOutbox": message, "slackReadback": readback,
    "ownerTypedCommandE2E": "not performed; current text was rendered read-only",
}, default=str, ensure_ascii=False))

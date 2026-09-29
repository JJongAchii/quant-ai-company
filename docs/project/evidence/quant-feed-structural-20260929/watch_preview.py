"""Bounded read-only progress projection; never prints prompts or response content."""

import json
import re
import sys
import time
from pathlib import Path

label = sys.argv[1]
if not re.fullmatch(r"[a-z0-9-]{1,50}", label):
    raise ValueError("invalid_preview_label")
path = Path("/var/lib/quant-company/operations/quant-feed-structural-20260929/preview") / (label + ".json")
last = None
until = time.monotonic() + 3600
while time.monotonic() < until:
    if path.exists():
        receipt = json.loads(path.read_text())
        view = {"state": receipt["state"], "error": receipt.get("error"), "fault": receipt.get("fault"),
                "calls": [{key: row.get(key) for key in ("case", "state", "disposition")}
                          for row in receipt["calls"]]}
        if view != last:
            print(json.dumps(view), flush=True)
            last = view
        if receipt["state"] != "running":
            break
    time.sleep(5)
else:
    raise SystemExit("Observation deadline reached; no request was cancelled or reissued.")

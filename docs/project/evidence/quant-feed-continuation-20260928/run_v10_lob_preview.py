"""Run a distinct full-context limit-order-book candidate through the v10 preview gate."""

import importlib.util
from pathlib import Path

SOURCE = Path(
    "/var/lib/quant-company/operations/quant-feed-continuation-20260928/"
    "source-v10-b64292b/docs/project/evidence/quant-feed-continuation-20260928/"
    "run_combined_positive_preview.py"
)
spec = importlib.util.spec_from_file_location("quant_v10_preview_base", SOURCE)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

runner.CANDIDATE = "b64292b2247bdd3ad408af56abdc60ceae3c1f65"
runner.TARGET = Path(
    "/var/lib/quant-company/operations/quant-feed-continuation-20260928/source-v10-b64292b"
)
runner.POSITIVE = "32879918c5c174b3777913287bcd3a2304bf33c6681c7fb8c2cbf1938d1612e9"
runner.RECEIPT = "qualification-agentic-lob-v10.json"

if __name__ == "__main__":
    runner.main()

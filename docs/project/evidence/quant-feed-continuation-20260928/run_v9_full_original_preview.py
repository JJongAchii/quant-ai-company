"""Run the historical fail-closed preview harness with a distinct v9 source and receipt."""

import importlib.util
from pathlib import Path

SOURCE = Path(
    "/var/lib/quant-company/operations/quant-feed-continuation-20260928/"
    "source-v9-0f39749/docs/project/evidence/quant-feed-continuation-20260928/"
    "run_combined_positive_preview.py"
)
spec = importlib.util.spec_from_file_location("quant_v9_preview_base", SOURCE)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

runner.CANDIDATE = "0f397498078f1d5ea25e6634e33c0b5cf9a0a3f2"
runner.TARGET = Path(
    "/var/lib/quant-company/operations/quant-feed-continuation-20260928/source-v9-0f39749"
)
runner.POSITIVE = "1dabb1c146c0ddd1afa86349399df38f1d50d1bd35e84810099042247736adef"
runner.RECEIPT = "qualification-crypto-volatility-v9.json"

if __name__ == "__main__":
    runner.main()

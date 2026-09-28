"""Retry only the pre-admission busy critique using the reserved Quant lane."""

import importlib.util
from pathlib import Path

ROOT = Path("/var/lib/quant-company/operations/quant-feed-quality-20260928")
spec = importlib.util.spec_from_file_location("staged_existing_critic", ROOT / "run_staged_existing_critic.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
runner.RECEIPT = "existing-regime-critic-quant-lane.json"
runner.main()

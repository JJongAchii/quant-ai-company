"""Run a distinct staged preview for Two Sigma's regime-modeling PDF."""

import importlib.util
from pathlib import Path

ROOT = Path("/var/lib/quant-company/operations/quant-feed-quality-20260928")
spec = importlib.util.spec_from_file_location("staged_quant_preview", ROOT / "run_staged_preview.py")
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)
preview.POSITIVE = "a7dc910cf962c50bddc6763d8ad0d04aa209dda009c9caaaf85dbefb3ac65600"
preview.RECEIPT = "qualification-regime.json"
preview.main()

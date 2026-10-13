"""Use the current image/settings in an isolated reader without revealing credentials."""

import fcntl
import importlib.util
import sys
from pathlib import Path

ROOT = Path("/opt/quant-company/operator-releases/trend-feed-20261006")


if __name__ == "__main__":
    spec = importlib.util.spec_from_file_location("trend_operator", ROOT / "production-cutover.py")
    operator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(operator)
    with (operator.STATE / ".backup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        reader = operator.inspect()["news-worker"]
        override = operator.STATE / "config/trend-feed-20261006-news-worker.compose.json"
        # Qualify the reviewed one-file renderer delta before changing the resident services.
        staged = (ROOT / "immediate-editor.py").read_text()
        bootstrap = ("import sys, types\n"
                     "editor = types.ModuleType('quant_company.trend_feed.editor')\n"
                     "editor.__package__ = 'quant_company.trend_feed'\n"
                     f"exec(compile({staged!r}, 'staged/trend_feed/editor.py', 'exec'), editor.__dict__)\n"
                     "sys.modules['quant_company.trend_feed.editor'] = editor\n")
        print(operator.run([*operator.compose(reader, override), "run", "--rm", "--no-deps", "--pull", "never",
                            "-T", "news-worker", "python", "-", sys.argv[1]],
                           input=bootstrap + (ROOT / "immediate-brief.py").read_text(), timeout=600))

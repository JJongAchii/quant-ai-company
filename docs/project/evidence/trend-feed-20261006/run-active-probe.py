"""Read the resident cohort and run the probe in its actual active image."""

import importlib.util
import json
from pathlib import Path

ROOT = Path("/opt/quant-company/operator-releases/trend-feed-20261006")


if __name__ == "__main__":
    spec = importlib.util.spec_from_file_location("trend_operator", ROOT / "production-cutover.py")
    operator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(operator)
    rows = operator.inspect()
    override = operator.STATE / "config/trend-feed-20261006-news-worker.compose.json"
    probe = json.loads(operator.run([*operator.compose(rows["news-worker"], override), "run", "--rm", "--no-deps", "--pull", "never",
                                    "-T", "news-worker", "python", "-"], input=(ROOT / "active-probe.py").read_text(), timeout=90))
    probe["targets"] = {name: {"id": rows[name]["Id"], "image": rows[name]["Image"],
                              "running": rows[name]["State"]["Running"], "oom_killed": rows[name]["State"]["OOMKilled"],
                              "pids_limit": rows[name]["HostConfig"]["PidsLimit"],
                              "trend_flags": {k: v for k, v in (s.split("=", 1) for s in rows[name]["Config"]["Env"])
                                              if k.startswith("TREND_FEED_")}}
                        for name in operator.TARGETS}
    probe["all_company_containers_running"] = all(r["State"]["Running"] for r in rows.values())
    print(json.dumps(probe, ensure_ascii=False))

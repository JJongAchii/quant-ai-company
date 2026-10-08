"""Resolve the scoped Compose plan against active values without changing services."""

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path


def main():
    root = Path("/tmp/revision-loop-runtime-patch-20261008-v4")
    spec = importlib.util.spec_from_file_location("operator", root / "apply-revision-loop-patch-20261008.py")
    operator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(operator)
    manifest = json.loads((root / "manifest.json").read_bytes())
    before = operator.inspect()
    results = []
    for service in ("api", "worker"):
        row = before["quant-company-" + service + "-1"]
        expected = operator.environment(row)
        preview = root / (service + "-environment-preview.compose.json")
        assert not preview.exists()
        try:
            operator.save(preview, {"services": {service: {
                "image": "quant-company-revision-loop-" + service + ":" + manifest["commit"],
                "environment": expected,
            }}})
            resolved = json.loads(operator.run([*operator.compose(row, preview), "config", "--format", "json"]))
            actual = resolved["services"][service]["environment"]
            assert actual == expected, "exact_environment_plan_differs"
            results.append({"service": service, "environment_values_match": True,
                            "environment_keys": len(expected),
                            "environment_sha256": operator.sha(json.dumps(expected, sort_keys=True).encode())})
        finally:
            preview.unlink(missing_ok=True)
    assert operator.public(operator.inspect()) == operator.public(before), "concurrent_deployment_changed"
    print(json.dumps({"observed_at": datetime.now(UTC).isoformat(), "state": "exact_active_environment_plan_verified",
                      "source_commit": manifest["commit"], "services": results,
                      "production_changed": False, "private_previews_removed": True}, indent=2))


if __name__ == "__main__":
    main()

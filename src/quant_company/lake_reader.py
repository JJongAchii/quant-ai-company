"""One bounded query per short-lived process; consumes only the qdata public API."""

import json
import math
import os
import sys


def normalize(value):
    if isinstance(value, dict):
        return {str(k): normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or type(value) in (bool, int, float):
        return value
    text = str(value)
    return text if len(text) <= 512 else text[:512] + " [truncated]"


def main():
    try:
        from qdata import api

        request = json.loads(sys.stdin.read(4096))
        args = request["arguments"]
        if request["name"] == "lake_catalog":
            data = api.dataset_catalog()
        elif request["name"] in {"lake_describe", "lake_sample"}:
            data = api.inspect_dataset(args["dataset"],
                                       sample_rows=args["limit"] if request["name"] == "lake_sample" else 0,
                                       columns=args["columns"])
        else:
            raise ValueError("Unknown lake operation")
        result = {"ok": True, "qdata_code_commit": os.environ.get("QDATA_CODE_COMMIT", "unqualified"),
                  "data": normalize(data)}
    except FileNotFoundError:
        result = {"ok": False, "error": "dataset_not_found"}
    except ValueError as exc:
        result = {"ok": False, "error": "inspection_limit_or_invalid_request", "detail": str(exc)[:200]}
    except Exception:
        # Third-party failures may carry credential/provider details. Do not expose them to the model.
        result = {"ok": False, "error": "lake_access_or_format_unavailable"}
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode()) > 32768:
        encoded = json.dumps({"ok": False, "error": "lake_result_too_large"})
    print(encoded)


if __name__ == "__main__":
    main()

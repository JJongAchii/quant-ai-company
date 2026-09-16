"""Bounded read-only qdata consumer. AWS credentials never enter model requests."""

import json
import os
import re
import subprocess
import sys
import threading

from pydantic import BaseModel, ConfigDict, Field, StrictInt

LAKE_TOOLS = {"lake_catalog", "lake_describe", "lake_sample"}
_query_lock = threading.Lock()


class LakeQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset: str = Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")
    columns: list[str] | None = Field(default=None, min_length=1, max_length=8)
    limit: StrictInt = Field(default=5, ge=1, le=20)


def validate_arguments(name, arguments):
    if name == "lake_catalog":
        if arguments:
            raise ValueError("lake_catalog takes no arguments")
        return {}
    if name == "lake_describe" and set(arguments) != {"dataset"}:
        raise ValueError("lake_describe requires dataset only")
    query = LakeQuery.model_validate(arguments)
    if query.columns and any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", c) for c in query.columns):
        raise ValueError("Invalid column name")
    return query.model_dump()


def query_lake(root, name, arguments):
    arguments = validate_arguments(name, arguments)
    if not root:
        return {"ok": False, "error": "lake_not_connected"}
    # Keep company DB, Slack, Temporal and model secrets out of the data reader.
    env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "SSL_CERT_FILE",
           "AWS_SHARED_CREDENTIALS_FILE", "AWS_PROFILE", "AWS_DEFAULT_REGION", "QDATA_CODE_COMMIT")
           if key in os.environ}
    env.update(QDATA_LAKE=root, AWS_EC2_METADATA_DISABLED="true", PYTHONDONTWRITEBYTECODE="1",
               OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1")
    if not _query_lock.acquire(timeout=1):
        return {"ok": False, "error": "lake_reader_busy"}
    try:
        request = json.dumps({"name": name, "arguments": arguments})
        result = subprocess.run([sys.executable, "-m", "quant_company.lake_reader"], input=request,
                                text=True, capture_output=True, timeout=18, env=env)
        if result.returncode or len(result.stdout.encode()) > 32768:
            return {"ok": False, "error": "lake_reader_failed"}
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, ValueError):
        return {"ok": False, "error": "lake_reader_timeout_or_invalid_result"}
    finally:
        _query_lock.release()

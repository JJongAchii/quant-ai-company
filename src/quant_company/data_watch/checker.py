"""Worker-only full-row quality checks of six frozen files; never imports a strategy or reads sealed data."""

import json
import math
import os
import signal
import sqlite3
import sys
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path

from ..research.recipes import load_recipe
from ..research.worker import atomic_json, exclusive_lock, sha_file
from .contracts import MAX_INPUT_BYTES, CheckAssignment, CheckReceipt, CoreScope, FileCheck

WINDOWS = {"warmup": (date(2012, 1, 1), date(2013, 12, 31)), "dev": (date(2014, 1, 1), date(2026, 8, 31))}


def required_columns(name):
    return ["date", "ticker", "adj_close", "close", "value"] if name.endswith("prices.parquet") else ["date", "ticker"]


def errors_for(name, item):
    if not name.endswith(".parquet"):
        return []
    errors = []
    required = required_columns(name)
    if not set(required) <= set(item.columns):
        errors.append("missing_columns")
    if item.rows == 0:
        errors.append("empty_input")
    if any(item.nulls.get(k, 0) for k in ("date", "ticker")):
        errors.append("invalid_keys")
    if item.duplicate_keys:
        errors.append("duplicate_keys")
    if item.invalid_values:
        errors.append("invalid_values")
    window = next((bounds for part, bounds in WINDOWS.items() if f"/{part}/" in name), None)
    if window is None or (item.min_date and item.min_date < window[0]) or (item.max_date and item.max_date > window[1]):
        errors.append("outside_window")
    return errors


def checked_path(root, name):
    parts = Path(name).parts
    if not parts or Path(name).is_absolute() or any(p in {"..", ".", "sealed"} for p in parts):
        raise ValueError("input_identity_changed")
    path = root
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("input_identity_changed")
    if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("input_unavailable")
    return path


def inspect_file(path, name, digest, scratch):
    item = FileCheck(sha256=digest, bytes=path.stat().st_size)
    if name.endswith(".json"):
        if item.bytes > 65536:
            raise ValueError("read_limit")
        json.loads(path.read_text())
        return item
    import pyarrow.parquet as pq

    with path.open("rb") as stream:
        parquet = pq.ParquetFile(stream)
        item.columns = parquet.schema_arrow.names
        if (parquet.metadata.num_rows > 4000000 or len(item.columns) > 100
                or any(parquet.metadata.row_group(i).total_byte_size > 64 * 1024 * 1024
                       for i in range(parquet.metadata.num_row_groups))):
            raise ValueError("read_limit")
        columns = [c for c in required_columns(name) if c in item.columns]
        item.rows, item.nulls = 0, {c: 0 for c in columns}
        with tempfile.TemporaryDirectory(dir=scratch) as directory, sqlite3.connect(Path(directory) / "keys.sqlite") as keys:
            keys.execute("PRAGMA journal_mode=OFF")
            keys.execute("PRAGMA cache_size=-2048")
            keys.execute("CREATE TABLE keys (date TEXT,ticker TEXT,PRIMARY KEY(date,ticker)) WITHOUT ROWID")
            for batch in parquet.iter_batches(batch_size=8192, columns=columns, use_threads=False):
                for row in batch.to_pylist():
                    item.rows += 1
                    for key, value in row.items():
                        item.nulls[key] += value is None
                    stamp, ticker = row.get("date"), row.get("ticker")
                    if stamp is not None and ticker is not None:
                        if not isinstance(stamp, (date, datetime)) or not isinstance(ticker, str) or not ticker.strip():
                            item.invalid_values += 1
                        else:
                            stamp = stamp.date() if isinstance(stamp, datetime) else stamp
                            item.min_date = min(stamp, item.min_date or stamp)
                            item.max_date = max(stamp, item.max_date or stamp)
                            inserted = keys.execute("INSERT OR IGNORE INTO keys VALUES(?,?)", (str(stamp), ticker)).rowcount
                            item.duplicate_keys += not inserted
                    for key in ("adj_close", "close", "value"):
                        if key not in row:
                            continue
                        value = row[key]
                        if (not isinstance(value, (int, float)) or not math.isfinite(value)
                                or (value <= 0 if key != "value" else value < 0)):
                            item.invalid_values += 1
            if item.rows != parquet.metadata.num_rows:
                raise ValueError("reader_failed")
    item.errors = errors_for(name, item)
    return item


def check(assignment, root, scratch, *, recipe=None):
    expected = CoreScope.from_recipe(recipe or load_recipe())
    if assignment.scope != expected or assignment.scope_digest != expected.digest():
        raise ValueError("unregistered_data_check_scope")
    result = dict(check_id=assignment.id, scope_digest=assignment.scope_digest, checked_at=datetime.now(UTC))
    try:
        paths = {name: checked_path(root, name) for name in expected.input_files}
        if sum(path.stat().st_size for path in paths.values()) > MAX_INPUT_BYTES:
            raise ValueError("read_limit")
        hashes = {name: sha_file(path) for name, path in paths.items()}
        if hashes != expected.input_files:
            raise ValueError("input_identity_changed")
        files = {name: inspect_file(path, name, hashes[name], scratch) for name, path in paths.items()}
        if {name: sha_file(checked_path(root, name)) for name in paths} != hashes:
            raise ValueError("input_identity_changed")
        return CheckReceipt(**result, state="checked", files=files)
    except ImportError:
        error = "reader_unavailable"
    except (OSError, ValueError, TypeError) as exc:
        error = str(exc) if str(exc) in {"input_identity_changed", "input_unavailable", "read_limit"} else "reader_failed"
    return CheckReceipt(**result, state="unavailable", error=error)


def main():
    assignment = CheckAssignment.model_validate_json(Path(sys.argv[1]).read_text())
    root, scratch, output = (Path(value) for value in sys.argv[2:5])
    # A stopped poller cannot leave an unbounded checker, or start concurrent full scans after restart.
    signal.alarm(180)
    os.umask(0o077)
    try:
        with exclusive_lock(scratch.parent / "checker.lock"):
            result = check(assignment, root, scratch)
    except BlockingIOError:
        result = CheckReceipt(check_id=assignment.id, scope_digest=assignment.scope_digest,
                              checked_at=datetime.now(UTC), state="unavailable", error="checker_busy")
    atomic_json(output, result.model_dump(mode="json"))


if __name__ == "__main__":
    main()

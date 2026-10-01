"""Operator provenance guards; no lake access, strategy, or cloud simulation claim."""

import copy
import hashlib
import io
import runpy
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "docs/project/evidence/etf-raw-lineage-20260930/trace-raw-lineage.py"
OPERATOR = runpy.run_path(str(SCRIPT))
ENTRY = {"trade_date": "2025-12-30", "Key": "qdata-raw/raw/krx_etf/snapshot/20251230.parquet",
         "Size": 3, "ETag": '"fixed"', "LastModified": "2026-07-29T00:00:00+00:00"}


@pytest.mark.parametrize("change", ["sealed_date", "extra_date", "duplicate", "different_key"])
def test_manifest_rejects_objects_outside_exact_approved_dates(change):
    manifest = {"objects": [copy.deepcopy(ENTRY)], "missing_dates": []}
    if change == "sealed_date":
        manifest["objects"][0]["trade_date"] = "2026-01-02"
    elif change == "extra_date":
        manifest["objects"].append({**ENTRY, "trade_date": "2026-01-02"})
    elif change == "duplicate":
        manifest["objects"].append(copy.deepcopy(ENTRY))
    else:
        manifest["objects"][0]["Key"] = "qdata-raw/raw/krx_etf/snapshot/20260102.parquet"
    with pytest.raises(ValueError):
        OPERATOR["approved_objects"](manifest, [ENTRY["trade_date"]])


class Source:
    def __init__(self, change):
        self.change = change
        self.head_calls = 0

    def response(self):
        return {"ContentLength": 3, "ETag": '"fixed"', "LastModified": datetime(2026, 7, 29, tzinfo=UTC)}

    def head_object(self, **kwargs):
        self.head_calls += 1
        response = self.response()
        if self.head_calls == 2 and self.change == "after_get":
            response["ETag"] = '"changed"'
        return response

    def get_object(self, **kwargs):
        assert kwargs["IfMatch"] == ENTRY["ETag"]
        assert kwargs["Key"] == ENTRY["Key"]
        response = self.response()
        response["Body"] = io.BytesIO(b"ab" if self.change == "truncated" else b"abc")
        if self.change == "during_get":
            response["ETag"] = '"changed"'
        return response


@pytest.mark.parametrize("change", ["after_get", "during_get", "truncated"])
def test_download_rejects_changed_or_incomplete_body_without_saving(tmp_path, change):
    with pytest.raises(ValueError):
        OPERATOR["download_one"](Source(change), ENTRY, tmp_path)
    assert not list(tmp_path.iterdir())


def test_raw_duplicate_keys_fail_before_builder_can_deduplicate(tmp_path):
    directory = tmp_path / "bounded/raw/krx_etf/snapshot"
    directory.mkdir(parents=True)
    columns = ["ISU_SRT_CD", "NAV", "TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC", "TDD_CLSPRC",
               "FLUC_RT", "ACC_TRDVOL", "ACC_TRDVAL", "OBJ_STKPRC_IDX", "ISU_ABBRV", "IDX_IND_NM",
               "INVSTASST_NETASST_TOTAMT", "MKTCAP", "LIST_SHRS"]
    path = directory / "20251230.parquet"
    pq.write_table(pa.table({name: ["069500", "069500"] if name == "ISU_SRT_CD" else ["1", "1"]
                             for name in columns}), path)
    entry = {**ENTRY, "Size": path.stat().st_size}
    body = {"trade_date": entry["trade_date"], "key": entry["Key"], "size_bytes": entry["Size"],
            "etag": entry["ETag"], "last_modified": entry["LastModified"],
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    manifest = {"program_digest": OPERATOR["PROGRAM"], "source_manifest_sha256": OPERATOR["MANIFEST_SHA"],
                "script_sha256": OPERATOR["sha"](SCRIPT), "failures": [], "objects": [body]}
    with pytest.raises(ValueError, match="duplicate_ticker"):
        OPERATOR["check_local_bodies"]([entry], tmp_path, manifest)

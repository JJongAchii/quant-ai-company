"""Regression for the genuine case 672a94ca: owner history displaced already read code."""

import copy
import json

from quant_company.maintenance.policy import Triage, digest
from quant_company.maintenance.runner import proposal_material


def test_history_detail_yields_before_requested_code_and_preserves_evidence_ids():
    needed = {
        "key": "code:" + "a" * 40 + ":src/quant_company/maintenance/requests.py",
        "path": "src/quant_company/maintenance/requests.py",
        "start_line": 219,
        "total_lines": 300,
        "content": "\n".join(f"{219+i}: required_state_{i} = 'operator evidence and next action'" for i in range(100)),
    }
    history = {
        "evidence": [{"key": f"message:{i}", "created_at": f"2026-10-08T04:{i:02d}:00Z",
                      "text": "unrelated historical detail " * 210} for i in range(14)],
        "roster": [{"id": f"employee_{i}", "instructions": "historical instructions " * 500} for i in range(4)],
    }
    request = "Inspect the status producer and consumer, then prepare a real code PR."
    material = {"requested_diagnosis": request, "history": history,
                "observations": [{"key": "message:original", "text": request}],
                "investigated_code": [needed],
                "inspection_catalog": [{"key": needed["key"], "path": needed["path"],
                                        "start_line": 219, "line_count": 100}]}
    original = copy.deepcopy(material)
    shown, prompt = proposal_material(material, Triage)
    assert shown["investigated_code"][0]["content"] == needed["content"], (
        "Unrelated history discarded the already read progress_text tail")
    assert shown["inspection_catalog"][0]["shown"] is True
    assert shown["requested_diagnosis"] == request and shown["observations"] == original["observations"]
    assert {row["key"] for row in shown["history"]["evidence"]} == {row["key"] for row in history["evidence"]}
    assert shown["prompt_history_compaction"] >= 1 and shown["prompt_excerpted"]
    assert len(prompt) <= 88000 and digest(material) == digest(original)
    assert json.loads(prompt.split("EVIDENCE JSON:\n", 1)[1]) == shown


def test_small_history_and_original_required_request_are_not_compacted():
    material = {"requested_diagnosis": "unchanged original functional acceptance " * 100,
                "observations": [{"key": "original", "text": "exact failure evidence"}],
                "history": {"evidence": [{"key": "message:one", "text": "Bound original response"}],
                            "roster": [{"id": "director", "mission": "unchanged"}]},
                "investigated_code": []}
    before = copy.deepcopy(material)
    shown, _ = proposal_material(material, Triage)
    assert "prompt_history_compaction" not in shown
    assert shown["history"] == before["history"]
    assert shown["requested_diagnosis"] == before["requested_diagnosis"]
    assert digest(material) == digest(before)

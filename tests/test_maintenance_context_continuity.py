"""Regression for case2: earlier exact callers must survive later test inspection."""
import copy

from quant_company.maintenance.investigation import inspection_context
from quant_company.maintenance.policy import Triage, digest
from quant_company.maintenance.runner import proposal_material


def test_old_explicit_producer_consumer_and_new_test_survive_static_context():
    paths = ["src/producer.py", "src/consumer.py", "tests/test_feature.py"]
    rows = [{"key": "code:" + "a"*40 + ":" + path, "path": path, "start_line": 1,
             "total_lines": 160,
             "content": "\n".join(f"{n}: exact_{i}_{n} = '" + "x"*65 + "'" for n in range(1, 161))}
            for i, path in enumerate(paths)]
    requests = [{"requests": [{"path": row["path"], "start_line": 1, "line_count": 160}],
                 "evidence": [row]} for row in rows]
    context = inspection_context({"investigation_evidence": rows, "investigation_requests": requests})
    history = {"evidence": [{"key": f"message:{n}", "created_at": str(n),
                            "text": "unrelated history " * 400, "project_id": "same_project",
                            "detail": {str(i): "long static context " * 300 for i in range(10)}}
                           for n in range(30)], "roster": [{"id": "maintainer", "mission": "review service"}]}
    material = {"requested_diagnosis": "Implement the unchanged explicit feature criteria",
                "observations": [{"key": "message:original", "text": "original functional requirement", "project_id": "same_project",
                                  "original_marker": {"_prompt_omitted_items": 1, "reason": "shared_provider_context_budget"}}],
                "history": history, "current_implementation": {
                    "source_files": [{"key": f"code:preview:{n}", "path": f"src/preview{n}.py",
                                     "content": "preview " * 125} for n in range(50)]},
                **context}
    before = copy.deepcopy(material)
    shown, prompt = proposal_material(material, Triage)
    visible = {line for row in shown["investigated_code"] for line in row["content"].splitlines()}
    for row in rows:
        assert set(row["content"].splitlines()) <= visible, "Earlier exact implementation must survive"
    assert all(row["shown"] for row in shown["inspection_catalog"])
    assert shown["requested_diagnosis"] == before["requested_diagnosis"]
    assert shown["observations"] == before["observations"]
    assert {row["key"] for row in shown["history"]["evidence"]} == {row["key"] for row in history["evidence"]}
    assert len(prompt) <= 88000 and digest(material) == digest(before)

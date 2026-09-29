"""Real PostgreSQL evidence delivery; decisions are synthetic, not an audit judgment."""
# ruff: noqa: F811 -- pytest injects imported fixtures by name

import hashlib
import json

import pytest
from psycopg.types.json import Jsonb

from quant_company.company import PolicyError
from quant_company.contracts import AgentDecision, ProviderResponse
from quant_company.research.audit_delivery import check_delivery, packet_data
from quant_company.research.controller import MissionController
from quant_company.staff.packs import employee_pack

from .test_research_controller import FixtureBackend, active, mission  # noqa: F401 -- real PG fixture


def setup_audit(mission, contents, resident=()):
    company = mission.company
    controller = MissionController(company, backend=FixtureBackend(company))
    controller.tick()
    row, first = active(mission)
    required, mappings = {}, {}
    for name, content in contents.items():
        path = company.settings.research_artifact_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        digest = hashlib.sha256(content.encode()).hexdigest()
        required[name] = {"sha256": digest, "characters": len(content)}
        mappings[name] = {"path": str(path), "sha256": digest}
    context = {"audit": {"scope": sorted(contents), "resident_evidence_paths": list(resident)},
               "_audit": {"delivery_version": 2, "required_reads": required}, "_private_files": mappings}
    with company.db.transaction() as conn:
        conn.execute("UPDATE research_mission_stages SET stage='audit',actor='validator',context=%s WHERE id=%s",
                     (Jsonb(context), row["id"]))
        conn.execute("UPDATE tasks SET agent='validator' WHERE id=%s", (row["task_id"],))
    return controller, row, first


def complete(company, identity, value):
    response = ProviderResponse(request_id=identity, provider="fixture", thread_id="synthetic-audit-thread",
        usage={"input_tokens": 100, "cached_input_tokens": 50, "output_tokens": 10},
        decision=AgentDecision(say="", status="complete", artifacts=[{"title": "Synthetic", "content": json.dumps(value)}]))
    return company.commit_turn(identity, response)


def test_complete_evidence_delivered_in_batches_with_notes_and_resident_final_context(mission):
    company = mission.company
    contents = {"a-code.py": "x" * 66000, "b-records.csv": "수치가 아닌 합성 자료\n" * 17000, "c-empty.txt": ""}
    _, row, identity = setup_audit(mission, contents, ["a-code.py"])
    collected = {name: "" for name in contents}
    prompts = []
    previous = None
    while True:
        request = company.prepare_turn(identity)["request"]
        prompts.append(request["prompt"])
        assert employee_pack("validator") in request["prompt"]
        assert request["session"] == {"id": str(row["task_id"]), "previous_request_id": previous}
        assert len(request["prompt"]) <= 90000
        data = packet_data(request["prompt"])
        if data["phase"] == "final":
            assert "".join(chunk["content"] for chunk in data["resident_evidence"]) == contents["a-code.py"]
            complete(company, identity, {"markdown": "Synthetic final; never a published judgment."})
            break
        if previous:
            assert data["previous_notes"] == "Cumulative synthetic findings with a-code.py references."
        for chunk in data["read_chunks"]:
            assert len(collected[chunk["path"]]) == chunk["offset"]
            collected[chunk["path"]] += chunk["content"]
        result = complete(company, identity, {"packet_digest": data["packet_digest"],
                                             "notes": "Cumulative synthetic findings with a-code.py references."})
        assert result["audit_packet_reviewed"]
        previous = identity
        _, identity = active(mission)
    assert collected == contents
    assert len(prompts) < 12  # 20+ individual 12k read calls before batching.
    with company.db.transaction() as conn:
        stage = conn.execute("SELECT * FROM research_mission_stages WHERE id=%s", (row["id"],)).fetchone()
        turns = conn.execute("SELECT * FROM turns WHERE task_id=%s ORDER BY sequence", (row["task_id"],)).fetchall()
        check_delivery(conn, stage, turns)
        corrupted = dict(turns[0]["request"])
        data = packet_data(corrupted["prompt"])
        data["read_chunks"][0]["content"] = "Not delivered"
        corrupted["prompt"] = "\nAUDIT PACKET JSON:\n" + json.dumps(data)
        conn.execute("UPDATE turns SET request=%s WHERE id=%s", (Jsonb(corrupted), turns[0]["id"]))
        with pytest.raises(PolicyError, match="delivery_mismatch"):
            check_delivery(conn, stage, turns)


@pytest.mark.parametrize("budget", ["turns", "uncached_tokens", "output_tokens"])
def test_audit_budget_holds_without_another_provider_request(mission, budget):
    company = mission.company
    controller, row, identity = setup_audit(mission, {"large.txt": "x" * 150000})
    # Settings are runtime operator controls; direct fixture assignment lets a tiny
    # synthetic response exercise the same boundary as a production-size budget.
    setattr(company.settings, "research_audit_max_" + budget, {"turns": 1, "uncached_tokens": 50, "output_tokens": 10}[budget])
    data = packet_data(company.prepare_turn(identity)["request"]["prompt"])
    complete(company, identity, {"packet_digest": data["packet_digest"], "notes": "Synthetic bounded review."})
    _, following = active(mission)
    result = company.prepare_turn(following)
    assert result["status"] == "blocked"
    assert "budget_exhausted" in result["reason"]
    for _ in range(3):
        assert controller.tick()["state"] == "waiting"
    with company.db.transaction() as conn:
        assert conn.execute("SELECT request FROM turns WHERE id=%s", (following,)).fetchone()["request"] is None
        assert conn.execute("SELECT attempt FROM research_mission_stages WHERE id=%s", (row["id"],)).fetchone()["attempt"] == 1


def test_incomplete_packet_cannot_be_marked_as_final(mission):
    company = mission.company
    _, row, identity = setup_audit(mission, {"large.txt": "x" * 150000})
    company.prepare_turn(identity)
    with pytest.raises(PolicyError, match="packet_review_invalid"):
        complete(company, identity, {"markdown": "Pretend all evidence was read."})
    company.block_turn(identity, "auth")
    with company.db.transaction() as conn:
        stage = conn.execute("SELECT * FROM research_mission_stages WHERE id=%s", (row["id"],)).fetchone()
        assert stage["context"]["_audit_hold"]["reason"] == "audit_runtime_requires_reconciliation"
        assert stage["retry_at"] is None

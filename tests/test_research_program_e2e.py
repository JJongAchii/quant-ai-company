"""Actual PG/Git/qlab gates; scripted opinions and synthetic worker ZIPs, not live research."""

import json
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

import pytest

from quant_company.company import PolicyError
from quant_company.research.feedback import resolve_challenges
from quant_company.research.mission_contracts import MissionSpec

from .test_research_audit import qlab_profile  # noqa: F401
from .test_research_exploration import create_exploratory_task, prepare_exploratory_program
from .test_research_mission_backend import BackendHarness
from .test_research_programs import create_task, program  # noqa: F401


@pytest.mark.parametrize("exploratory", [False, True])
def test_two_program_trials_wait_for_independent_meaning_review_before_publication(program, qlab_profile, exploratory):  # noqa: F811
    p = program
    if exploratory:
        prepare_exploratory_program(p)
        create_exploratory_task(p)
    else:
        create_task(p)
    qlab = p.company.settings.research_artifact_dir / "qlab.json"
    qlab.write_text(json.dumps({"root": str(qlab_profile.root), "commit": qlab_profile.commit,
                                "python_executable": str(qlab_profile.python_executable)}))
    p.company.settings.research_qlab_profile_file = qlab
    p.company.settings.company_code_commit = "c" * 40
    p.company.settings.research_library_channel_id = "CQUANT"
    h = BackendHarness(p.company, p.original, p.public, qlab_profile)
    h.mission_id = p.mission_id
    current = h.snapshot()
    h.spec, h.digest = MissionSpec.model_validate(current["spec"]), current["manifest_digest"]

    def select():
        proposal = h.add()
        challenge = h.challenge(proposal)
        # Fixed worker fixture timestamps and reverse UUID order must retain the first incumbent.
        trial = str(UUID(int=100 - len(h.snapshot()["trials"])))
        with h.company.db.transaction() as conn:
            resolve_challenges(h.company, conn, h.snapshot(), proposal.id, {
                "decision": "execute", "rationale": "Fixture contract check", "responses": [{
                    "challenge_id": str(challenge.id), "disposition": "test", "rationale": "Validate exact evidence",
                    "source_ids": ["fixture:baseline"], "test_plan": "Validate every producer/consumer hash"}]}, "director")
        h.invoke("select", h.mission_id, proposal.id, actor="director", challenge_ids=[challenge.id],
                 rationale="Retain the independent test obligation", trial_id=trial)
        return trial, proposal
    h.select = select
    best = None
    for number in (1, 2):
        h.build(replacement=f"SYNTHETIC_FIXTURE = {number}\n")
        h.deliver()
        assert h.backend.reconcile()["state"] == "received"
        h.interpret_current()
        audit_row, snapshot = h.audit_response()
        assert h.backend.apply_stage(audit_row, snapshot) == {"state": "waiting", "stage": "meaning"}
        assert not h.snapshot(public=True)["trials"][-1]["performance_visible"]
        row = h.stage_row()
        assert row["stage"] == "meaning" and row["actor"] == "financial_strategist"
        for name in row["context"]["required_meaning_reads"]:
            size = len(Path(row["context"]["_private_files"][name]["path"]).read_text())
            for offset in range(0, max(1, size), 12000):
                row = h.respond(row, read_path=name, offset=offset)
        outcome = h.snapshot()["outcomes"][-1]
        review = {"trial_id": outcome["trial_id"], "outcome_digest": outcome["digest"],
            "conclusion": "inconclusive", "rationale": "Synthetic fixture has no financial meaning",
            "multiple_testing": "All fixture trials retained", "execution_costs": "No real market fills",
            "alternative_explanations": "Scripted transport fixture", "unresolved": ["Real scientific research not executed"],
            "tests": [{"challenge_id": response["challenge_id"], "conclusion": "unresolved", "evidence_paths": [],
                       "rationale": "Synthetic fixtures do not establish scientific validity"}
                      for response in h.snapshot()["challenge_responses"]
                      if response["proposal_id"] == h.snapshot()["trials"][-1]["proposal_id"]]}
        if exploratory:
            assert any("scope/supplements/exploratory-data/limitations.txt" in name
                       for name in row["context"]["required_meaning_reads"])
            with pytest.raises(PolicyError, match="cannot establish supported"):
                h.respond(row, review | {"conclusion": "supported", "unresolved": [], "tests": []})
        h.respond(row, review)
        published = h.backend.apply_stage(audit_row, snapshot)
        assert published["state"] == "completed"
        if best is None:
            best = outcome["trial_id"]
        assert h.snapshot()["incumbent_trial_id"] == best
        with h.company.db.transaction() as conn:
            source = conn.execute("SELECT content FROM sources WHERE id=%s", (published["source_id"],)).fetchone()
            summary = json.loads(source["content"])["summary"]
            assert summary["meaning_review"]["conclusion"] == "inconclusive"
            if exploratory:
                assert summary["exploratory_only"] and not summary["historical_point_in_time_verified"]
                assert not summary["confirmation_eligible"] and not summary["deployment_eligible"]
                assert summary["data_policy"] == current["spec"]["data"]["policy"]
                uri = json.loads(source["content"])["report"]["uri"]
                assert "한계를 명시한 탐색 연구" in Path(urlparse(uri).path).read_text()
            library = conn.execute("SELECT content FROM sources WHERE id=%s",
                                   (published["source_id"] + ":library",)).fetchone()
            assert library["content"] == source["content"]
            assert conn.execute("SELECT count(*) AS n FROM outbox WHERE text LIKE %s",
                                ("탐색 연구 기록 · 결론 보류%" if exploratory else "검증된 연구 기록 · 결론 보류%",)).fetchone()["n"] == number
            # Ordinary director delivery is independently tested by the legacy producer/consumer gate.
            conn.execute("UPDATE turns SET status='stale' WHERE status='queued'")
            conn.execute("UPDATE tasks SET status='completed'")
    assert h.snapshot(public=True)["stage"] == "owner_review"
    with h.company.db.transaction() as conn:
        usage = p.program_store.usage(conn, p.program_id)
        assert usage["trials"] == 2 and usage["outstanding"] == 0
        assert conn.execute("SELECT count(*) AS n FROM research_meaning_reviews").fetchone()["n"] == 2

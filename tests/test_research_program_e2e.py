"""Actual PG/Git/qlab gates; scripted opinions and synthetic worker ZIPs, not live research."""

import json
from pathlib import Path
from uuid import UUID

from quant_company.research.feedback import resolve_challenges
from quant_company.research.mission_contracts import MissionSpec

from .test_research_audit import qlab_profile  # noqa: F401
from .test_research_mission_backend import BackendHarness
from .test_research_programs import create_task, program  # noqa: F401


def test_two_program_trials_wait_for_independent_meaning_review_before_publication(program, qlab_profile):  # noqa: F811
    p = program
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
        h.respond(row, {"trial_id": outcome["trial_id"], "outcome_digest": outcome["digest"],
            "conclusion": "inconclusive", "rationale": "Synthetic fixture has no financial meaning",
            "multiple_testing": "All fixture trials retained", "execution_costs": "No real market fills",
            "alternative_explanations": "Scripted transport fixture", "unresolved": ["Real scientific research not executed"],
            "tests": [{"challenge_id": response["challenge_id"], "conclusion": "unresolved", "evidence_paths": [],
                       "rationale": "Synthetic fixtures do not establish scientific validity"}
                      for response in h.snapshot()["challenge_responses"]
                      if response["proposal_id"] == h.snapshot()["trials"][-1]["proposal_id"]]})
        published = h.backend.apply_stage(audit_row, snapshot)
        assert published["state"] == "completed"
        if best is None:
            best = outcome["trial_id"]
        assert h.snapshot()["incumbent_trial_id"] == best
        with h.company.db.transaction() as conn:
            source = conn.execute("SELECT content FROM sources WHERE id=%s", (published["source_id"],)).fetchone()
            assert json.loads(source["content"])["summary"]["meaning_review"]["conclusion"] == "inconclusive"
            library = conn.execute("SELECT content FROM sources WHERE id=%s",
                                   (published["source_id"] + ":library",)).fetchone()
            assert library["content"] == source["content"]
            assert conn.execute("SELECT count(*) AS n FROM outbox WHERE text LIKE %s",
                                ("검증된 연구 기록 · 결론 보류%",)).fetchone()["n"] == number
            # Ordinary director delivery is independently tested by the legacy producer/consumer gate.
            conn.execute("UPDATE turns SET status='stale' WHERE status='queued'")
            conn.execute("UPDATE tasks SET status='completed'")
    assert h.snapshot(public=True)["stage"] == "owner_review"
    with h.company.db.transaction() as conn:
        usage = p.program_store.usage(conn, p.program_id)
        assert usage["trials"] == 2 and usage["outstanding"] == 0
        assert conn.execute("SELECT count(*) AS n FROM research_meaning_reviews").fetchone()["n"] == 2

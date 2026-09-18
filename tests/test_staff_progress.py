"""Real PostgreSQL practice progression; scripted answers are not competence evidence."""

import pytest

from quant_company.company import load_roles
from quant_company.staff.packs import coaching, pack
from quant_company.staff.progress import development, profile
from quant_company.staff.store import StaffStore, status

from .test_staff_development import oracle_answer, reply


@pytest.fixture
def staff(company):
    company.roles = load_roles(company.settings)
    return company


def finish(company, *, passed=True):
    store = StaffStore(company)
    identity = store.enqueue("data", "UHUMAN")
    prepared = store.prepare()
    store.commit(identity, reply(prepared["request"], answer=oracle_answer(company, identity) if passed else {}))
    return identity


def test_feedback_survives_one_pass_and_curriculum_preserves_breadth(staff):
    failure = finish(staff, passed=False)
    families = []
    for _ in range(5):
        with staff.db.transaction() as conn:
            report = development(conn, "UHUMAN", "data", staff.roles["data"].model)
            families.append(report["next_practice"]["family"])
        finish(staff)
        if len(families) == 2:  # One pass in the failed family is insufficient.
            with staff.db.transaction() as conn:
                assert coaching(conn, "UHUMAN", "data", staff.roles["data"].model)[0]["run_id"] == failure
    assert all(len(set(families[i:i+3])) > 1 for i in range(len(families)-2))
    with staff.db.transaction() as conn:
        assert coaching(conn, "UHUMAN", "data", staff.roles["data"].model) == []
        report = status(conn, staff, "UHUMAN", "data")
        assert report["development"][0]["families"][0]["consecutive_fresh_passes"] == 3
        assert report["development"][0]["unmeasured_requirements"]
        assert report["recent_exercises"][0]["curriculum"]["reason"] == "needs_recheck"
        assert status(conn, staff, "someone-else", "data")["development"][0]["families"][0]["state"] == "unassessed"


def test_evidence_is_not_pooled_across_models_or_procedures(staff):
    for _ in range(6):
        finish(staff)
    with staff.db.transaction() as conn:
        report = development(conn, "UHUMAN", "data", "new-unassessed-model")
        assert all(f["state"] == "unassessed" for f in report["families"])
    # Duplicate cases, infrastructure failures and disputed rows cannot manufacture a streak.
    from quant_company.staff.cases import FAMILIES, SUITE_VERSION

    row = {"id": "one", "model": "m", "pack_digest": pack("data")["digest"], "suite_version": SUITE_VERSION,
           "family": FAMILIES["data"][0], "case_digest": "same", "state": "completed",
           "grade": {"objective_passed": True}}
    rows = [row, row, {**row, "state": "blocked"}, {**row, "state": "disputed"},
            {**row, "case_digest": "different", "pack_digest": "old"}]
    report = profile(rows, "data", "m", pack("data")["digest"])
    assert report["families"][0]["consecutive_fresh_passes"] == 1
    assert report["families"][0]["blocked"] == report["families"][0]["disputed"] == 1


def test_disputed_failure_does_not_drive_coaching(staff):
    identity = finish(staff, passed=False)
    StaffStore(staff).review(identity, "disputed", "Synthetic fixture: source question is being independently reviewed.")
    with staff.db.transaction() as conn:
        assert coaching(conn, "UHUMAN", "data", staff.roles["data"].model) == []
        assert development(conn, "UHUMAN", "data", staff.roles["data"].model)["families"][0]["state"] == "unassessed"

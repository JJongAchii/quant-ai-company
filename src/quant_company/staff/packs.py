"""Explicitly deliver only the employee's reviewed playbook; never discover host skills."""

import hashlib
import json
from functools import lru_cache
from importlib.resources import files

STAFF = (
    "director", "financial_strategist", "data", "researcher_kr", "maintainer",
    "engineer", "validator", "risk", "researcher_global", "researcher_crypto", "operations",
)
PACK_VERSION = "2026-09-17.1"


@lru_cache(maxsize=12)
def pack(employee: str) -> dict:
    if employee not in (*STAFF, "reporter"):
        raise ValueError("Unknown specialist")
    text = files("quant_company.staff").joinpath(f"playbooks/{employee}.md").read_text()
    return pack_content(employee, text)


def pack_content(employee: str, text: str) -> dict:
    return {"employee": employee, "version": PACK_VERSION,
            "digest": hashlib.sha256(text.encode()).hexdigest(), "procedure": text,
            "meaning": "Reviewed work procedure, not a qualification certificate or new tool permission."}


def employee_pack(employee: str) -> str:
    return render_pack(pack(employee))


def render_pack(value: dict) -> str:
    return "SPECIALIST PROCEDURE JSON:\n" + json.dumps(value, ensure_ascii=False) + "\n"


def coaching(conn, owner: str, employee: str, model: str | None = None, reasoning_effort=None) -> list[dict]:
    """Only attributable synthetic feedback; never expose another owner's data or answer key."""
    if model is None:
        latest = conn.execute("""SELECT model,role_snapshot->>'reasoning_effort' AS reasoning_effort
            FROM staff_runs WHERE owner_user=%s AND employee=%s
            ORDER BY created_at DESC,id DESC LIMIT 1""", (owner, employee)).fetchone()
        model = latest["model"] if latest else ""
        reasoning_effort = latest["reasoning_effort"] if latest else None
    return conn.execute("""SELECT f.employee,f.run_id::text,f.weaknesses,f.practice_advice,f.created_at::text,
        r.model AS observed_model,r.role_snapshot->>'reasoning_effort' AS observed_reasoning_effort,
        r.pack_snapshot->>'digest' AS observed_pack_digest,r.suite_version,f.family
        FROM staff_feedback f JOIN staff_runs r ON r.id=f.run_id
        WHERE f.owner_user=%s AND f.employee=%s AND r.state='completed'
        AND (SELECT count(DISTINCT newer.case_digest) FROM staff_runs newer WHERE newer.owner_user=r.owner_user
            AND newer.employee=r.employee AND newer.public_case->>'family'=r.public_case->>'family'
            AND newer.created_at>r.created_at AND newer.state='completed'
            AND newer.grade->>'objective_passed'='true'
            AND newer.pack_snapshot->>'digest'=%s AND newer.model=%s
            AND newer.role_snapshot->>'reasoning_effort' IS NOT DISTINCT FROM %s::text
            AND newer.suite_version=r.suite_version
            AND NOT EXISTS(SELECT 1 FROM staff_runs failed WHERE failed.owner_user=r.owner_user
                AND failed.employee=r.employee AND failed.public_case->>'family'=r.public_case->>'family'
                AND failed.state='completed' AND failed.grade->>'objective_passed'='false'
                AND failed.created_at>newer.created_at)) < 3
        ORDER BY f.created_at DESC,f.id DESC LIMIT 3""",
                        (owner, employee, pack(employee)["digest"], model, reasoning_effort)).fetchall()

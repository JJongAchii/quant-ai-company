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


@lru_cache(maxsize=11)
def pack(employee: str) -> dict:
    if employee not in STAFF:
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


def coaching(conn, owner: str, employee: str) -> list[dict]:
    """Only attributable synthetic feedback; never expose another owner's data or answer key."""
    return conn.execute("""SELECT f.employee,f.run_id::text,f.weaknesses,f.practice_advice,f.created_at::text
        FROM staff_feedback f JOIN staff_runs r ON r.id=f.run_id
        WHERE f.owner_user=%s AND f.employee=%s AND r.state='completed'
        AND NOT EXISTS(SELECT 1 FROM staff_runs newer WHERE newer.owner_user=r.owner_user
            AND newer.employee=r.employee AND newer.public_case->>'family'=r.public_case->>'family'
            AND newer.created_at>r.created_at AND newer.state='completed'
            AND newer.grade->>'objective_passed'='true')
        ORDER BY f.created_at DESC,f.id DESC LIMIT 3""", (owner, employee)).fetchall()

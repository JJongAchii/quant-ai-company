"""Append-only independent objections and accountable director dispositions."""

from psycopg.types.json import Jsonb

from ..company import PolicyError, fingerprint
from .library import require_current
from .program_contracts import ReviewDecision


def resolve_challenges(company, conn, mission, proposal_id, value, actor):
    decision = ReviewDecision.model_validate(value)
    if actor != "director":
        raise PolicyError("Only the director resolves independent challenges")
    challenges = conn.execute("""SELECT * FROM research_mission_challenges
        WHERE mission_id=%s AND proposal_id=%s ORDER BY created_at,id""", (mission["id"], proposal_id)).fetchall()
    answers = {str(r.challenge_id): r for r in decision.responses}
    if not challenges or len(answers) != len(decision.responses) or set(answers) != {str(c["id"]) for c in challenges}:
        raise PolicyError("Every independent challenge needs exactly one disposition")
    for challenge in challenges:
        response = answers[str(challenge["id"])]
        company._check_sources(conn, mission["project_id"], response.source_ids)
        require_current(conn, response.source_ids)
        if response.disposition == "revise" and decision.decision != "revise":
            raise PolicyError("Required revision prevents execution")
        payload = response.model_dump(mode="json")
        old = conn.execute("SELECT digest FROM research_challenge_responses WHERE challenge_id=%s", (challenge["id"],)).fetchone()
        if old and old["digest"] != fingerprint(payload):
            raise PolicyError("Challenge disposition is immutable")
        conn.execute("""INSERT INTO research_challenge_responses(challenge_id,mission_id,proposal_id,payload,digest)
            VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
            (challenge["id"], mission["id"], proposal_id, Jsonb(payload), fingerprint(payload)))
    return {"decision": decision.decision, "rationale": decision.rationale, "challenge_ids": list(answers)}


def require_resolutions(conn, mission_id, proposal_id):
    if conn.execute("""SELECT 1 FROM research_mission_challenges c
        LEFT JOIN research_challenge_responses r ON r.challenge_id=c.id
        WHERE c.mission_id=%s AND c.proposal_id=%s AND
        (r.challenge_id IS NULL OR r.payload->>'disposition'='revise')""", (mission_id, proposal_id)).fetchone():
        raise PolicyError("Unresolved or revision-required challenge prevents execution")

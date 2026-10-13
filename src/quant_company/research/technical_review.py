"""Independent disposition of a verified, pre-evaluation technical failure.

An operator evidence packet requests a review; only the authenticated director's
typed decision can retire the candidate. Neither operation creates a result,
changes scientific counters or approves another experiment.
"""

import hashlib
import json
from typing import Literal
from uuid import UUID

from psycopg.types.json import Jsonb

from ..company import PolicyError, as_json, fingerprint
from .mission_contracts import MissionModel, SourceIds, Text


class TechnicalDisposition(MissionModel):
    trial_id: UUID
    failed_job_id: UUID
    decision: Literal["retire_candidate", "wait"]
    rationale: Text
    source_ids: SourceIds


def qualification_failure(company, conn, mission, trial):
    """Require a registered original diagnostic, never the model's phase claim."""
    job = conn.execute("SELECT * FROM research_jobs WHERE id=%s FOR UPDATE", (trial["job_id"],)).fetchone()
    if (not job or str(job["mission_id"]) != str(mission["id"])
            or str(job["trial_id"]) != str(trial["id"]) or job["state"] != "failed"
            or job["artifact_sha256"] is not None or job["artifact_path"] is not None):
        raise PolicyError("technical_review_requires_terminal_failed_job_without_archive")
    if conn.execute("""SELECT 1 FROM research_mission_outcomes WHERE trial_id=%s AND kind='result'""",
                    (trial["id"],)).fetchone():
        raise PolicyError("technical_review_cannot_retire_a_measured_result")
    failure_id = "research-failure:" + str(job["id"])
    company._check_sources(conn, str(mission["project_id"]), [failure_id])
    source = conn.execute("SELECT * FROM sources WHERE id=%s", (failure_id,)).fetchone()
    try:
        diagnostic = json.loads(source["content"])
    except (TypeError, ValueError):
        raise PolicyError("technical_review_requires_verified_qualification_diagnostic") from None
    if ((source["synthetic"] and not company.settings.fixture_mode)
            or str(source["project_id"]) != str(mission["project_id"])
            or diagnostic.get("job_id") != str(job["id"])
            or diagnostic.get("trial_id") != str(trial["id"])
            or diagnostic.get("mission_id") != str(mission["id"])
            or diagnostic.get("code_commit") != job["manifest"]["plan"]["code_commit"]
            or diagnostic.get("code_files") != job["manifest"]["code_files"]
            or diagnostic.get("evaluation_started") is not False
            or type(diagnostic.get("qualification", {}).get("exit_code")) is not int
            or diagnostic["qualification"]["exit_code"] == 0):
        raise PolicyError("technical_review_requires_verified_pre_evaluation_failure")
    if not conn.execute("""SELECT 1 FROM research_mission_outcomes
        WHERE trial_id=%s AND job_id=%s AND kind='technical_failure'""",
                        (trial["id"], job["id"])).fetchone():
        raise PolicyError("technical_review_requires_recorded_failure_outcome")
    return job, failure_id


def retire_candidate(store, conn, mission_id, value, *, actor):
    value = TechnicalDisposition.model_validate(value)
    if value.decision != "retire_candidate" or not value.rationale.strip():
        raise PolicyError("technical_review_requires_explicit_retirement")
    project, mission, _ = store._locked(conn, mission_id, active=True)
    store._actor(actor, "director")
    trial = store._trial(conn, mission_id, value.trial_id)
    payload = value.model_dump(mode="json")
    old = conn.execute("SELECT * FROM research_technical_dispositions WHERE trial_id=%s",
                       (trial["id"],)).fetchone()
    if old:
        if old["digest"] != fingerprint(payload):
            raise PolicyError("technical_review_disposition_is_immutable")
        return as_json(trial)
    if (trial["state"] != "technical_waiting" or trial["technical_closed_at"] is not None
            or str(trial["job_id"]) != str(value.failed_job_id)):
        raise PolicyError("technical_review_requires_current_failed_trial")
    held = conn.execute("""SELECT * FROM research_mission_stages WHERE mission_id=%s
        AND stage='repair' AND state='waiting' AND context ? '_repair_hold'
        AND context->'mission'->'stage'->>'trial_id'=%s ORDER BY created_at DESC,id DESC LIMIT 1 FOR UPDATE""",
                        (mission_id, str(trial["id"]))).fetchone()
    evidence = held["context"].get("_technical_review_evidence") if held else None
    if not isinstance(evidence, dict) or not evidence.get("source_ids"):
        raise PolicyError("technical_review_requires_new_operator_evidence")
    if not set(evidence["source_ids"]) <= set(value.source_ids):
        raise PolicyError("technical_review_must_address_registered_new_evidence")
    store._sources(conn, project["id"], value.source_ids)
    for identity in evidence["source_ids"]:
        source = conn.execute("SELECT * FROM sources WHERE id=%s", (identity,)).fetchone()
        if (str(source["project_id"]) != str(project["id"])
                or (source["synthetic"] and not store.company.settings.fixture_mode)
                or hashlib.sha256(source["content"].encode()).hexdigest()
                   != evidence.get("content_sha256", {}).get(identity)):
            raise PolicyError("technical_review_operator_evidence_changed")
        try:
            packet = json.loads(source["content"])
        except (TypeError, ValueError):
            raise PolicyError("technical_review_operator_evidence_invalid") from None
        if (packet.get("mission_id") != str(mission_id)
                or packet.get("mission_digest") != mission["manifest_digest"]
                or packet.get("trial_id") != str(trial["id"])
                or packet.get("job_id") != str(value.failed_job_id)
                or packet.get("code_commit") != trial["plan"]["code_commit"]):
            raise PolicyError("technical_review_operator_evidence_identity_mismatch")
    job, failure_id = qualification_failure(store.company, conn, mission, trial)
    if failure_id not in value.source_ids:
        raise PolicyError("technical_review_must_cite_original_failure")
    conn.execute("""INSERT INTO research_technical_dispositions(trial_id,mission_id,job_id,payload,digest)
        VALUES (%s,%s,%s,%s,%s)""", (trial["id"], mission_id, job["id"], Jsonb(payload), fingerprint(payload)))
    saved = conn.execute("""UPDATE research_mission_trials SET technical_closed_at=now(),updated_at=now()
        WHERE id=%s RETURNING *""", (trial["id"],)).fetchone()
    store.company._event(conn, "research_technical_candidate_retired", {
        "mission_id": str(mission_id), "trial_id": str(trial["id"]), "job_id": str(job["id"]),
        "actor": actor, "disposition_digest": fingerprint(payload),
        "scientific_result_created": False, "scientific_counters_changed": False,
    }, project["id"])
    return as_json(saved)

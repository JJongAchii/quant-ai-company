"""Owner-bound cross-program evidence and atomic scientific budget checks."""

from ..company import PolicyError, as_json, fingerprint


def _origins(conn, project_id, authority):
    refs = {str(ref.task_id): ref for ref in authority.originating_task_refs}
    registered = conn.execute("SELECT task_id,task_digest FROM research_scientific_lineage_origins WHERE lineage_id=%s",
                              (authority.id,)).fetchall()
    if not {str(row["task_id"]) for row in registered} <= refs.keys():
        raise PolicyError("Scientific lineage omitted prior task history")
    tasks = []
    for ref in sorted(refs.values(), key=lambda item: str(item.task_id)):
        task = conn.execute("""SELECT t.*,p.project_id FROM research_program_tasks t
            JOIN research_programs p ON p.id=t.program_id WHERE t.id=%s""", (ref.task_id,)).fetchone()
        if (not task or task["project_id"] != project_id or task["program_id"] != ref.program_id
                or task["digest"] != ref.task_digest or fingerprint(task["proposal"]) != ref.task_digest):
            raise PolicyError("Scientific origin is not an exact task in this project")
        old = conn.execute("SELECT lineage_id FROM research_scientific_lineage_origins WHERE task_id=%s",
                           (ref.task_id,)).fetchone()
        if old and old["lineage_id"] != authority.id:
            raise PolicyError("A prior candidate cannot reset history in another scientific lineage")
        if task["mission_id"]:
            mission = conn.execute("SELECT * FROM research_missions WHERE id=%s", (task["mission_id"],)).fetchone()
            search = mission["spec"]["search"]
            ended = (mission["cumulative_trials"] >= search.get("max_total_trials", 2**63)
                     or (not search["continuous"] and (mission["cycle_trials"] >= search["max_trials_per_cycle"]
                         or mission["stagnant_trials"] >= search["patience"])))
            pending = conn.execute("""SELECT 1 FROM research_mission_trials t LEFT JOIN research_jobs j ON j.id=t.job_id
                WHERE t.mission_id=%s AND (EXISTS(SELECT 1 FROM research_program_reservations r
                    WHERE r.trial_id=t.id AND r.settled_at IS NULL)
                    OR j.state IN ('queued','claimed','running','uncertain','cancel_requested')
                    OR (t.state<>'reported' AND NOT(t.state='technical_waiting' AND j.state IN ('failed','cancelled')
                        AND EXISTS(SELECT 1 FROM research_mission_outcomes o WHERE o.trial_id=t.id
                            AND o.kind='technical_failure'))))""", (mission["id"],)).fetchone()
            if pending or (mission["state"] != "cancelled" and not ended):
                raise PolicyError("Prior scientific mission must be terminal before inheriting it")
        tasks.append(task)
    return tasks


def history(conn, project_id, authority):
    """A read-only preimage for the fresh signature; never authorizes a lineage itself."""
    row = conn.execute("SELECT * FROM research_scientific_lineages WHERE id=%s", (authority.id,)).fetchone()
    if row and row["project_id"] != project_id:
        raise PolicyError("Scientific lineage belongs to another project")
    tasks = _origins(conn, project_id, authority)
    missions = [task["mission_id"] for task in tasks if task["mission_id"]]
    trials = conn.execute("""SELECT DISTINCT t.id,t.mission_id,t.state,t.plan_digest,o.kind,o.digest
        FROM research_mission_trials t LEFT JOIN research_mission_outcomes o ON o.trial_id=t.id
        WHERE t.id IN (SELECT trial_id FROM research_scientific_lineage_trials WHERE lineage_id=%s)
        OR t.mission_id=ANY(%s::uuid[]) ORDER BY t.id,o.digest""", (authority.id, missions)).fetchall()
    payload = as_json({"scientific_lineage_id": authority.id, "project_id": project_id,
        "trial_limit": row["trial_limit"] if row else None,
        "origins": [{"program_id": task["program_id"], "task_id": task["id"], "task_digest": task["digest"]}
                    for task in tasks], "trials": trials})
    return payload, fingerprint(payload)


def validate_preimage(conn, project_id, authority):
    payload, digest = history(conn, project_id, authority)
    if digest != authority.history_digest:
        raise PolicyError("Scientific history changed since the proposed signed authority")
    completed = {trial["id"] for trial in payload["trials"] if trial["kind"] == "result"}
    if len(completed) >= authority.max_total_trials:
        raise PolicyError("Signed scientific lineage has no remaining trial capacity")
    return payload


def authorize(conn, project_id, program_id, envelope):
    authority = envelope.template.scientific_lineage
    if authority is None:
        return
    old = conn.execute("SELECT * FROM research_scientific_lineages WHERE id=%s FOR UPDATE", (authority.id,)).fetchone()
    if old and old["project_id"] != project_id:
        raise PolicyError("Scientific lineage project mismatch")
    payload = validate_preimage(conn, project_id, authority)
    conn.execute("""INSERT INTO research_scientific_lineages(id,project_id,trial_limit) VALUES(%s,%s,%s)
        ON CONFLICT(id) DO UPDATE SET trial_limit=EXCLUDED.trial_limit""",
                 (authority.id, project_id, authority.max_total_trials))
    for ref in authority.originating_task_refs:
        conn.execute("""INSERT INTO research_scientific_lineage_origins(task_id,lineage_id,task_digest)
            VALUES(%s,%s,%s) ON CONFLICT DO NOTHING""", (ref.task_id, authority.id, ref.task_digest))
    for trial in {row["id"] for row in payload["trials"]}:
        attached = conn.execute("SELECT lineage_id FROM research_scientific_lineage_trials WHERE trial_id=%s",
                                (trial,)).fetchone()
        if attached and attached["lineage_id"] != authority.id:
            raise PolicyError("Prior scientific trial already has a different lineage")
        conn.execute("INSERT INTO research_scientific_lineage_trials(trial_id,lineage_id) VALUES(%s,%s) ON CONFLICT DO NOTHING",
                     (trial, authority.id))
    records = conn.execute("""SELECT s.result FROM research_mission_stages s
        JOIN research_missions m ON m.id=s.mission_id WHERE m.id=ANY(%s::uuid[])
        AND s.stage IN ('implementation','repair') AND s.state='completed' ORDER BY s.created_at,s.id""",
        ([task["mission_id"] for task in _origins(conn, project_id, authority) if task["mission_id"]],)).fetchall()
    for record in records:
        receipt = record["result"].get("build_receipt")
        if receipt:
            bind_experiment(conn, project_id, authority.id, receipt["manifest"]["trial_id"], receipt["code_signature"])
    conn.execute("""INSERT INTO research_program_lineage_authorizations(program_id,envelope,lineage_id,authority_digest)
        VALUES(%s,%s,%s,%s)""", (program_id, envelope.name, authority.id,
                                 fingerprint(authority.model_dump(mode="json"))))


def require_authority(conn, project_id, program_id, envelope):
    authority = envelope.template.scientific_lineage
    if authority is None:
        return
    found = conn.execute("""SELECT a.*,l.project_id FROM research_program_lineage_authorizations a
        JOIN research_scientific_lineages l ON l.id=a.lineage_id WHERE a.program_id=%s AND a.envelope=%s""",
                         (program_id, envelope.name)).fetchone()
    if (not found or found["lineage_id"] != authority.id or found["project_id"] != project_id
            or found["authority_digest"] != fingerprint(authority.model_dump(mode="json"))):
        raise PolicyError("Scientific lineage has no matching signed program authority")


def attach_task(conn, task, spec):
    if spec.scientific_lineage is None:
        return
    old = conn.execute("SELECT lineage_id FROM research_scientific_lineage_origins WHERE task_id=%s",
                       (task["id"],)).fetchone()
    if old and old["lineage_id"] != spec.scientific_lineage.id:
        raise PolicyError("Task already belongs to a different scientific lineage")
    conn.execute("""INSERT INTO research_scientific_lineage_origins(task_id,lineage_id,task_digest)
        VALUES(%s,%s,%s) ON CONFLICT DO NOTHING""", (task["id"], spec.scientific_lineage.id, task["digest"]))


def usage(conn, lineage_id):
    return conn.execute("""SELECT count(DISTINCT t.id)::integer AS trials
        FROM research_scientific_lineage_trials l JOIN research_mission_trials t ON t.id=l.trial_id
        WHERE l.lineage_id=%s AND (EXISTS(SELECT 1 FROM research_mission_outcomes o
            WHERE o.trial_id=t.id AND o.kind='result') OR EXISTS(SELECT 1 FROM research_program_reservations r
            WHERE r.trial_id=t.id AND r.settled_at IS NULL))""", (lineage_id,)).fetchone()["trials"]


def bind_experiment(conn, project_id, lineage_id, trial_id, signature):
    """Only trusted build receipts supply this signature, under the project lock."""
    old = conn.execute("""SELECT lineage_id,trial_id FROM research_scientific_lineage_experiments
        WHERE project_id=%s AND code_signature=%s""", (project_id, signature)).fetchone()
    if old and (str(old["trial_id"]) != str(trial_id) or str(old["lineage_id"]) != str(lineage_id)):
        raise PolicyError("Scientific configuration already exists; a new program or lineage cannot reset it")
    conn.execute("""INSERT INTO research_scientific_lineage_experiments(project_id,code_signature,lineage_id,trial_id)
        VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING""", (project_id, signature, lineage_id, trial_id))


def overview(conn, project_id, lineage_id):
    """Current internal evidence, separate from the frozen owner approval preimage."""
    lineage = conn.execute("SELECT * FROM research_scientific_lineages WHERE id=%s AND project_id=%s",
                           (lineage_id, project_id)).fetchone()
    origins = conn.execute("""SELECT t.id,t.program_id,t.digest,t.proposal,t.data_assessment,t.decision,t.state,t.mission_id
        FROM research_scientific_lineage_origins l JOIN research_program_tasks t ON t.id=l.task_id
        WHERE l.lineage_id=%s ORDER BY t.created_at,t.id""", (lineage_id,)).fetchall() if lineage else []
    trials = conn.execute("""SELECT t.id,t.mission_id,t.state,t.plan_digest,t.cycle,t.ordinal,
        o.id AS outcome_id,o.kind,o.digest AS outcome_digest,o.payload
        FROM research_scientific_lineage_trials l JOIN research_mission_trials t ON t.id=l.trial_id
        LEFT JOIN research_mission_outcomes o ON o.trial_id=t.id WHERE l.lineage_id=%s ORDER BY t.id,o.created_at,o.id""",
                         (lineage_id,)).fetchall() if lineage else []
    experiments = conn.execute("""SELECT code_signature,trial_id FROM research_scientific_lineage_experiments
        WHERE lineage_id=%s ORDER BY code_signature""", (lineage_id,)).fetchall() if lineage else []
    publications = conn.execute("""SELECT p.trial_id,p.digest,p.payload FROM research_mission_publications p
        JOIN research_scientific_lineage_trials l ON l.trial_id=p.trial_id
        WHERE l.lineage_id=%s ORDER BY p.created_at,p.trial_id""", (lineage_id,)).fetchall() if lineage else []
    return as_json({"scientific_lineage_id": lineage_id, "project_id": project_id,
        "trial_limit": lineage["trial_limit"] if lineage else None,
        "charged_trials": usage(conn, lineage_id) if lineage else 0,
        "completed_trials": len({row["id"] for row in trials if row["kind"] == "result"}),
        "technical_failures": sum(row["kind"] == "technical_failure" for row in trials),
        "origins": origins, "trials": trials, "experiments": experiments, "publications": publications})


def summary(value):
    return {key: value[key] for key in ("scientific_lineage_id", "trial_limit", "charged_trials",
                                       "completed_trials", "technical_failures")}


def reserve(conn, spec, trial_id):
    authority = spec.scientific_lineage
    if authority is None:
        return
    row = conn.execute("SELECT * FROM research_scientific_lineages WHERE id=%s FOR UPDATE", (authority.id,)).fetchone()
    if row is None:
        raise PolicyError("Scientific lineage is not authorized")
    if not conn.execute("SELECT 1 FROM research_scientific_lineage_experiments WHERE trial_id=%s AND lineage_id=%s",
                        (trial_id, authority.id)).fetchone():
        raise PolicyError("Scoped scientific trial requires a trusted canonical build signature")
    existing = conn.execute("SELECT lineage_id FROM research_scientific_lineage_trials WHERE trial_id=%s",
                            (trial_id,)).fetchone()
    if existing and existing["lineage_id"] != authority.id:
        raise PolicyError("Trial cannot switch scientific lineage")
    pending = conn.execute("SELECT 1 FROM research_program_reservations WHERE trial_id=%s AND settled_at IS NULL",
                           (trial_id,)).fetchone()
    if pending:
        raise PolicyError("Scientific trial already has an unresolved execution reservation")
    if usage(conn, authority.id) >= min(row["trial_limit"], authority.max_total_trials):
        raise PolicyError("Cumulative scientific lineage budget exhausted")
    conn.execute("INSERT INTO research_scientific_lineage_trials(trial_id,lineage_id) VALUES(%s,%s) ON CONFLICT DO NOTHING",
                 (trial_id, authority.id))

"""Program authority, task provenance and crash-safe resource reservations."""

import math

from psycopg.types.json import Jsonb

from ..company import PolicyError, as_json, fingerprint, stable
from .builds import profile_for
from .data_evidence import load_packets
from .library import available_sources, require_current, verify_citations
from .mission_contracts import MissionSpec
from .missions import MissionStore, typed
from .program_contracts import DataAssessment, ResearchProgram, ResearchTaskProposal, TaskDecision


def public_progress(conn, project_id, *, limit=3):
    """Bounded, service-owned program state for owner conversation and status commands."""
    programs = conn.execute("""SELECT id,revision,state,approved_at,spec FROM research_programs
        WHERE project_id=%s ORDER BY created_at DESC,id DESC LIMIT %s""", (project_id, limit)).fetchall()
    result = []
    for program in programs:
        stage = conn.execute("""SELECT stage,actor,state,attempt,updated_at
            FROM research_mission_stages WHERE program_id=%s
            ORDER BY updated_at DESC,id DESC LIMIT 1""", (program["id"],)).fetchone()
        tasks = conn.execute("""SELECT state,count(*) AS n FROM research_program_tasks
            WHERE program_id=%s GROUP BY state ORDER BY state""", (program["id"],)).fetchall()
        missions = conn.execute("SELECT count(*) AS n FROM research_missions WHERE program_id=%s",
                                (program["id"],)).fetchone()["n"]
        result.append(as_json({"id": program["id"], "title": program["spec"]["title"],
                               "revision": program["revision"], "state": program["state"],
                               "approved_at": program["approved_at"], "stage": stage,
                               "task_states": tasks, "mission_count": missions}))
    return result


class ProgramStore:
    def __init__(self, company):
        self.company = company

    def locked(self, conn, program_id, *, active=False):
        found = conn.execute("SELECT project_id FROM research_programs WHERE id=%s", (program_id,)).fetchone()
        if not found:
            raise PolicyError("Unknown research program")
        project = self.company._project(conn, found["project_id"])
        row = conn.execute("SELECT * FROM research_programs WHERE id=%s FOR UPDATE", (program_id,)).fetchone()
        spec = typed(ResearchProgram, row["spec"])
        if fingerprint(spec.model_dump(mode="json")) != row["manifest_digest"]:
            raise PolicyError("Program specification changed")
        if active and (row["state"] != "active" or not row["approval_event_id"]
                       or project["status"] != "active" or project["revision"] != row["revision"]
                       or project["owner_user"] != row["owner_user"]):
            raise PolicyError("Program is not active in this project revision")
        return project, row, spec

    def create(self, conn, project, payload):
        spec = typed(ResearchProgram, payload)
        if (not self.company.settings.company_autonomous_research_enabled or project["status"] != "active"
                or project["owner_user"] not in self.company.settings.slack_allowed_users
                or not project["channel"] or not project["thread_ts"]):
            raise PolicyError("Program requires an authorized active research thread")
        self.company._check_sources(conn, project["id"], spec.source_ids)
        for envelope in spec.envelopes:
            profile_for(self.company, envelope.template)
            self.company._check_sources(conn, project["id"], envelope.template.baseline_source_ids)
        digest = fingerprint(spec.model_dump(mode="json"))
        identity = stable(f"program:{project['id']}:{project['revision']}:{digest}")
        conn.execute("""INSERT INTO research_programs(id,project_id,owner_user,revision,spec,manifest_digest)
            VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""", (identity, project["id"], project["owner_user"],
                project["revision"], Jsonb(spec.model_dump(mode="json")), digest))
        return self.snapshot(conn, identity)

    def owner_command(self, conn, program_id, *, owner, revision, manifest_digest, event_key, action):
        project, row, spec = self.locked(conn, program_id)
        MissionStore(self.company)._owner(conn, project, row, owner=owner, revision=revision,
            manifest_digest=manifest_digest, event_key=event_key, action=action, target_kind="program")
        if action == "approve":
            if row["state"] == "active" and row["approval_event_id"] == event_key:
                return self.snapshot(conn, program_id)
            if row["state"] != "draft":
                raise PolicyError("Program approval requires a draft")
            for envelope in spec.envelopes:
                profile_for(self.company, envelope.template)
            conn.execute("""UPDATE research_programs SET state='active',approval_event_id=%s,approved_at=now(),
                updated_at=now() WHERE id=%s""", (event_key, program_id))
        elif action == "cancel":
            conn.execute("UPDATE research_programs SET state='cancelled',updated_at=now() WHERE id=%s", (program_id,))
            conn.execute("UPDATE research_missions SET state='cancelled' WHERE program_id=%s", (program_id,))
            conn.execute("""UPDATE research_jobs SET state=CASE WHEN state IN
                ('claimed','running','uncertain','cancel_requested') THEN 'cancel_requested' ELSE 'cancelled' END
                WHERE mission_id IN (SELECT id FROM research_missions WHERE program_id=%s)
                AND state NOT IN ('completed','cancelled','failed')""", (program_id,))
        else:
            raise PolicyError("Unsupported program control")
        self.company._event(conn, "research_program_" + action, {"program_id": str(program_id),
            "manifest_digest": manifest_digest, "owner_event_id": event_key}, project["id"])
        return self.snapshot(conn, program_id)

    def usage(self, conn, program_id):
        return conn.execute("""SELECT coalesce(sum(CASE WHEN settled_at IS NULL THEN reserved_seconds
            ELSE actual_seconds END),0)::bigint AS compute_seconds,
            count(*) FILTER(WHERE scientific_trial OR settled_at IS NULL)::integer AS trials,
            count(*) FILTER(WHERE settled_at IS NULL)::integer AS outstanding
            FROM research_program_reservations WHERE program_id=%s""", (program_id,)).fetchone()

    def snapshot(self, conn, program_id):
        _, row, spec = self.locked(conn, program_id)
        missions = conn.execute("SELECT id FROM research_missions WHERE program_id=%s ORDER BY created_at,id",
                               (program_id,)).fetchall()
        return as_json({key: row[key] for key in ("id", "project_id", "revision", "manifest_digest", "state")} | {
            "title": spec.title, "usage": self.usage(conn, program_id),
            "limits": {"trials": spec.max_total_trials, "compute_seconds": spec.max_compute_seconds,
                       "missions": spec.max_missions},
            "missions": [MissionStore(self.company).snapshot(conn, m["id"]) for m in missions]})

    def propose(self, conn, program_id, value, *, actor):
        project, row, spec = self.locked(conn, program_id, active=True)
        MissionStore(self.company)._actor(actor, "researcher_kr")
        proposal = typed(ResearchTaskProposal, value)
        if proposal.envelope not in {e.name for e in spec.envelopes}:
            raise PolicyError("Task selects an unapproved envelope")
        allowed = {s["id"] for s in available_sources(self.company, conn, project, spec, program_id)}
        if not set(proposal.source_ids) <= allowed:
            raise PolicyError("Task cites sources outside the program library")
        require_current(conn, proposal.source_ids)
        verify_citations(conn, proposal)
        for prior in proposal.predecessor_mission_ids:
            if not conn.execute("SELECT 1 FROM research_missions WHERE id=%s AND program_id=%s",
                                (prior, program_id)).fetchone():
                raise PolicyError("Predecessor is outside this program")
        payload = proposal.model_dump(mode="json")
        digest = fingerprint(payload)
        identity = stable(f"program-task:{program_id}:{digest}")
        if conn.execute("SELECT 1 FROM research_program_tasks WHERE id=%s", (identity,)).fetchone():
            raise PolicyError("Task already considered; use prior evidence to change the question")
        conn.execute("INSERT INTO research_program_tasks(id,program_id,proposal,digest) VALUES(%s,%s,%s,%s)",
                     (identity, program_id, Jsonb(payload), digest))
        return str(identity)

    def assess(self, conn, program_id, task_id, payload, *, actor):
        project, program, spec = self.locked(conn, program_id, active=True)
        MissionStore(self.company)._actor(actor, "data")
        value = typed(DataAssessment, payload)
        self.company._check_sources(conn, project["id"], value.source_ids)
        task = conn.execute("SELECT * FROM research_program_tasks WHERE id=%s AND program_id=%s FOR UPDATE",
                            (task_id, program_id)).fetchone()
        if not task or task["state"] != "proposed":
            raise PolicyError("Data assessment requires an unassessed proposal")
        envelope = next(e for e in spec.envelopes if e.name == task["proposal"]["envelope"])
        if value.decision == "ready":
            profile = profile_for(self.company, envelope.template)
            if not profile.public_profile.fixture_only:
                packet = load_packets(self.company, program, {envelope.name: envelope}).get(envelope.name)
                if packet is None:
                    raise PolicyError("Real data readiness requires a verified input evidence packet")
                if packet[0].blocking_gaps:
                    raise PolicyError("Data evidence packet still records blocking gaps")
        if (task["proposal"]["mode"] == "exact_replication" and value.decision == "ready"
                and not value.original_conditions):
            raise PolicyError("Exact replication requires the original conditions; register a transfer instead")
        conn.execute("UPDATE research_program_tasks SET data_assessment=%s,state='assessed' WHERE id=%s",
                     (Jsonb(value.model_dump(mode="json")), task_id))

    def decide(self, conn, program_id, task_id, payload, *, actor):
        project, row, spec = self.locked(conn, program_id, active=True)
        MissionStore(self.company)._actor(actor, "director")
        decision = typed(TaskDecision, payload)
        task = conn.execute("SELECT * FROM research_program_tasks WHERE id=%s AND program_id=%s FOR UPDATE",
                            (task_id, program_id)).fetchone()
        if not task or task["state"] != "assessed":
            raise PolicyError("Task selection requires a separate data assessment")
        if decision.decision != "accept":
            conn.execute("UPDATE research_program_tasks SET state=%s,decision=%s WHERE id=%s",
                ("waiting" if decision.decision == "wait" else "rejected", Jsonb(decision.model_dump()), task_id))
            return
        if task["data_assessment"]["decision"] != "ready":
            raise PolicyError("Data prerequisites are unresolved")
        used = self.usage(conn, program_id)
        count = conn.execute("SELECT count(*) AS n FROM research_missions WHERE program_id=%s", (program_id,)).fetchone()["n"]
        if count >= spec.max_missions or used["trials"] >= spec.max_total_trials or used["compute_seconds"] >= spec.max_compute_seconds:
            raise PolicyError("Program budget exhausted")
        active = [m for m in self.snapshot(conn, program_id)["missions"]
                  if m["stage"] not in {"owner_review", "cancelled"}]
        if len(active) >= spec.max_parallel_missions:
            raise PolicyError("Program parallel mission limit reached")
        proposal = typed(ResearchTaskProposal, task["proposal"])
        envelope = next(e for e in spec.envelopes if e.name == proposal.envelope)
        require_current(conn, proposal.source_ids)
        mission_spec = envelope.template.model_dump(mode="json")
        mission_spec.update(title=proposal.title, baseline_source_ids=sorted(set(
            envelope.template.baseline_source_ids + proposal.source_ids + task["data_assessment"]["source_ids"])))
        mission_spec = MissionSpec.model_validate(mission_spec)
        require_current(conn, mission_spec.baseline_source_ids)
        profile_for(self.company, mission_spec)
        mission = MissionStore(self.company).create(conn, project["id"], row["owner_user"], row["revision"], mission_spec)
        if mission["state"] != "draft":
            raise PolicyError("A task cannot take authority over a pre-existing approved mission")
        # Program identity is separate from immutable legacy mission bytes.
        conn.execute("""UPDATE research_missions SET program_id=%s,state='active',approval_event_id=%s,
            approved_at=%s WHERE id=%s AND state='draft'""", (program_id, row["approval_event_id"], row["approved_at"], mission["id"]))
        conn.execute("UPDATE research_program_tasks SET state='accepted',mission_id=%s,decision=%s WHERE id=%s",
                     (mission["id"], Jsonb(decision.model_dump()), task_id))
        self.company._event(conn, "research_program_task_authorized", {"program_id": str(program_id),
            "program_digest": row["manifest_digest"], "task_id": str(task_id), "mission_id": mission["id"],
            "mission_digest": mission["manifest_digest"], "approval_event_id": row["approval_event_id"]}, project["id"])

    def require_authorized(self, conn, mission):
        if not mission.get("program_id"):
            return
        _, program, spec = self.locked(conn, mission["program_id"], active=True)
        task = conn.execute("SELECT * FROM research_program_tasks WHERE mission_id=%s AND state='accepted'",
                            (mission["id"],)).fetchone()
        if (not task or task["program_id"] != program["id"]
                or mission["approval_event_id"] != program["approval_event_id"]
                or mission["owner_user"] != program["owner_user"] or mission["revision"] != program["revision"]):
            raise PolicyError("Child mission has no matching program authorization")
        envelope = next((e for e in spec.envelopes if e.name == task["proposal"]["envelope"]), None)
        if envelope is None:
            raise PolicyError("Program envelope is missing")
        expected = envelope.template.model_dump(mode="json")
        expected.update(title=task["proposal"]["title"], baseline_source_ids=sorted(set(
            envelope.template.baseline_source_ids + task["proposal"]["source_ids"] + task["data_assessment"]["source_ids"])))
        if expected != mission["spec"]:
            raise PolicyError("Child mission escaped the approved envelope")

    def reserve(self, conn, mission, job_id, trial_id, seconds):
        if not mission.get("program_id"):
            return
        _, _, spec = self.locked(conn, mission["program_id"], active=True)
        self.require_authorized(conn, mission)
        require_current(conn, mission["spec"]["baseline_source_ids"])
        old = conn.execute("SELECT * FROM research_program_reservations WHERE job_id=%s", (job_id,)).fetchone()
        if old:
            if str(old["trial_id"]) != str(trial_id) or old["reserved_seconds"] != seconds:
                raise PolicyError("Resource reservation identity changed")
            return
        usage = self.usage(conn, mission["program_id"])
        if usage["trials"] + 1 > spec.max_total_trials or usage["compute_seconds"] + seconds > spec.max_compute_seconds:
            raise PolicyError("Program resource budget exhausted; reservation denied")
        conn.execute("""INSERT INTO research_program_reservations(job_id,program_id,trial_id,reserved_seconds)
            VALUES(%s,%s,%s,%s)""", (job_id, mission["program_id"], trial_id, seconds))

    def settle(self, conn, mission, job_id, outcome):
        if not mission.get("program_id"):
            return
        self.locked(conn, mission["program_id"])
        row = conn.execute("SELECT * FROM research_program_reservations WHERE job_id=%s FOR UPDATE", (job_id,)).fetchone()
        if not row:
            raise PolicyError("Program outcome has no resource reservation")
        digest = fingerprint(outcome.model_dump(mode="json"))
        if row["settled_at"]:
            if row["receipt_digest"] != digest:
                raise PolicyError("Resource receipt changed")
            return
        duration = math.ceil((outcome.finished_at - outcome.started_at).total_seconds())
        # Failure timestamps can include queuing. Charge conservatively and never release
        # an uncertain execution reservation without a terminal trusted receipt.
        conn.execute("""UPDATE research_program_reservations SET actual_seconds=%s,scientific_trial=%s,
            receipt_digest=%s,settled_at=now() WHERE job_id=%s""", (duration, outcome.status == "result", digest, job_id))

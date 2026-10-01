"""PostgreSQL mission lifecycle; callers own transactions and trusted executor/verifier adapters.

Model proposals never authorize owner approval, create worker jobs, or publish audit passes.
All mutations lock the project before the mission. Evidence history is append-only; the
mutable rows are pointers/counters derived from that history, not replacements for it.
"""

from collections.abc import Callable
from pathlib import PurePosixPath

from psycopg.types.json import Jsonb

from ..company import PolicyError, as_json, fingerprint, now, stable
from .mission_contracts import (
    AuditPublication,
    Challenge,
    HypothesisProposal,
    Interpretation,
    MissionSpec,
    TrialOutcome,
    TrialPlan,
)

EXECUTION_PROFILE = "kr-etf-monthly-python-v1"
PROTECTED = {"AGENTS.md", "PREREG.md", "LEDGER.md", "SEARCH.jsonl", "docs/objective.md"}


def typed(cls, value):
    # Frozen Pydantic objects still contain containers; revalidate at every trust boundary.
    return cls.model_validate(value.model_dump(mode="json") if isinstance(value, cls) else value)


def mission_digest(spec: MissionSpec) -> str:
    return fingerprint(typed(MissionSpec, spec).model_dump(mode="json"))


def _same(row, payload, digest_key="digest"):
    if row[digest_key] != fingerprint(payload):
        raise PolicyError("Identity was already used for different content")


def _within(path, roots):
    value = PurePosixPath(path)
    return any(value == PurePosixPath(root) or PurePosixPath(root) in value.parents for root in roots)


def _writable(path, roots):
    value = PurePosixPath(path)
    return (_within(path, roots) and path not in PROTECTED and value.name not in PROTECTED
            and not {".git", "audits"}.intersection(value.parts))


class MissionStore:
    def __init__(self, company):
        self.company = company

    def _actor(self, actor, required=None):
        role = self.company.roles.get(actor)
        internal = (actor in {"engineer", "validator"}
                    and getattr(self.company.settings, "company_autonomous_research_enabled", False))
        if not role or not (role.active or internal) or (required and actor != required):
            raise PolicyError("Role is not authorized for this research stage")

    def _locked(self, conn, mission_id, *, current=True, active=False):
        found = conn.execute("SELECT project_id FROM research_missions WHERE id=%s", (mission_id,)).fetchone()
        if not found:
            raise PolicyError("Unknown research mission")
        project = self.company._project(conn, found["project_id"])
        row = conn.execute("SELECT * FROM research_missions WHERE id=%s FOR UPDATE", (mission_id,)).fetchone()
        spec = typed(MissionSpec, row["spec"])
        if mission_digest(spec) != row["manifest_digest"]:
            raise PolicyError("Stored mission specification changed")
        if current and (project["revision"] != row["revision"] or project["owner_user"] != row["owner_user"]
                        or project["status"] != "active"):
            raise PolicyError("Mission requires the current active project revision and owner")
        if active and (row["state"] != "active" or not row["approval_event_id"]):
            raise PolicyError("Mission is not active and approved")
        if active and row.get("program_id"):
            from .programs import ProgramStore

            ProgramStore(self.company).require_authorized(conn, row)
        return project, row, spec

    def _trial(self, conn, mission_id, trial_id):
        row = conn.execute("SELECT * FROM research_mission_trials WHERE id=%s AND mission_id=%s FOR UPDATE",
                           (trial_id, mission_id)).fetchone()
        if not row:
            raise PolicyError("Unknown trial in this mission")
        return row

    def _sources(self, conn, project_id, source_ids):
        if not source_ids or len(set(source_ids)) != len(source_ids):
            raise PolicyError("Distinct authorized evidence sources are required")
        self.company._check_sources(conn, project_id, source_ids)

    def _owner(self, conn, project, row, *, owner, revision, manifest_digest, event_key, action, target_kind="mission"):
        if (owner not in self.company.settings.slack_allowed_users or owner != project["owner_user"]
                or owner != row["owner_user"] or revision != row["revision"]
                or revision != project["revision"] or manifest_digest != row["manifest_digest"]):
            raise PolicyError("Owner command does not match the current mission specification")
        inbound = conn.execute("""SELECT i.task_id FROM inbound i JOIN messages m ON m.task_id=i.task_id
            WHERE i.event_key=%s AND i.project_id=%s AND m.project_id=i.project_id
            AND m.author=%s AND m.kind='human' AND m.revision=%s LIMIT 1""",
                               (event_key, project["id"], owner, revision)).fetchone()
        if not inbound:
            raise PolicyError("Mission control requires authenticated owner ingress")
        if action == "approve":
            records = conn.execute("""SELECT detail FROM events WHERE project_id=%s
                AND kind='research_approval_authorized' AND detail->>'owner_event_id'=%s""",
                                   (project["id"], event_key)).fetchall()
            expected = {"kind": target_kind, "target_id": str(row["id"]), "revision": revision,
                        "manifest_digest": manifest_digest}
            matched = False
            for record in records:
                detail = record["detail"]
                target, provenance = detail.get("target", {}), detail.get("provenance", {})
                matched |= (detail.get("action") == "approve"
                            and str(detail.get("task_id")) == str(inbound["task_id"])
                            and all(target.get(key) == value for key, value in expected.items())
                            and provenance.get("owner") == owner
                            and provenance.get("channel") == project["channel"]
                            and provenance.get("thread_ts") == project["thread_ts"])
            if not matched:
                raise PolicyError("Mission approval requires a bound authenticated approval record")

    def create(self, conn, project_id, owner, revision, spec, *, mission_id=None):
        spec = typed(MissionSpec, spec)
        project = self.company._project(conn, project_id, owner)
        if (project["status"] != "active" or project["revision"] != revision
                or owner not in self.company.settings.slack_allowed_users
                or not project["channel"] or not project["thread_ts"]):
            raise PolicyError("A current authorized owner research thread is required")
        self._sources(conn, project_id, spec.baseline_source_ids)
        if any(not _writable(path, spec.code.write_paths) for path in spec.code.write_paths):
            raise PolicyError("Protected paths cannot be approved research write scope")
        digest = mission_digest(spec)
        identity = mission_id or stable(f"mission:{project_id}:{revision}:{digest}")
        old = conn.execute("SELECT * FROM research_missions WHERE id=%s", (identity,)).fetchone()
        if old:
            if (str(old["project_id"]) != str(project_id) or old["owner_user"] != owner
                    or old["revision"] != revision or old["manifest_digest"] != digest):
                raise PolicyError("Mission identity was already used for different content")
            return self.snapshot(conn, identity)
        same = conn.execute("""SELECT id FROM research_missions WHERE project_id=%s AND revision=%s
            AND manifest_digest=%s""", (project_id, revision, digest)).fetchone()
        if same:
            raise PolicyError("Mission specification already has a different identity")
        conn.execute("""INSERT INTO research_missions(id,project_id,owner_user,revision,spec,manifest_digest)
            VALUES (%s,%s,%s,%s,%s,%s)""", (identity, project_id, owner, revision,
                                           Jsonb(spec.model_dump(mode="json")), digest))
        self.company._event(conn, "research_mission_created", {"mission_id": str(identity),
                            "manifest_digest": digest, "revision": revision}, project_id)
        return self.snapshot(conn, identity)

    def approve(self, conn, mission_id, *, owner, revision, manifest_digest, event_key):
        project, row, spec = self._locked(conn, mission_id)
        self._owner(conn, project, row, owner=owner, revision=revision, manifest_digest=manifest_digest,
                    event_key=event_key, action="approve")
        if spec.data.policy is not None:
            raise PolicyError("Retrospective exploration requires program authority and a separate data assessment")
        if not ((spec.kind == "strategy" and spec.execution_profile == EXECUTION_PROFILE)
                or spec.schema_version == 2):
            raise PolicyError("This mission kind or execution profile is draft-only")
        payload = {"action": "approve", "mission_id": str(mission_id), "owner": owner,
                   "revision": revision, "manifest_digest": manifest_digest}
        old = conn.execute("SELECT * FROM research_mission_controls WHERE event_key=%s", (event_key,)).fetchone()
        if old:
            _same(old, payload)
            return self.snapshot(conn, mission_id)
        if row["state"] != "draft" or row["approval_event_id"]:
            raise PolicyError("Mission already has an approval or is no longer a draft")
        conn.execute("INSERT INTO research_mission_controls VALUES (%s,%s,%s,%s,now())",
                     (event_key, mission_id, Jsonb(payload), fingerprint(payload)))
        conn.execute("""UPDATE research_missions SET state='active',approval_event_id=%s,approved_at=now(),
            updated_at=now() WHERE id=%s""", (event_key, mission_id))
        self.company._event(conn, "research_mission_approved", {**payload, "owner_event_id": event_key}, project["id"])
        return self.snapshot(conn, mission_id)

    def _control(self, conn, mission_id, action, *, owner, revision, manifest_digest, event_key, reason):
        project, row, _ = self._locked(conn, mission_id)
        self._owner(conn, project, row, owner=owner, revision=revision, manifest_digest=manifest_digest,
                    event_key=event_key, action=action)
        if not isinstance(reason, str) or not reason.strip():
            raise PolicyError("Owner control requires a reason")
        payload = {"action": action, "mission_id": str(mission_id), "owner": owner, "revision": revision,
                   "manifest_digest": manifest_digest, "reason": reason}
        old = conn.execute("SELECT * FROM research_mission_controls WHERE event_key=%s", (event_key,)).fetchone()
        if old:
            _same(old, payload)
            return self.snapshot(conn, mission_id)
        allowed = {"pause": {"active"}, "resume": {"paused"}, "cancel": {"draft", "active", "paused"}}
        if row["state"] not in allowed[action] or (action == "resume" and not row["approval_event_id"]):
            raise PolicyError("Invalid mission control transition")
        state = {"pause": "paused", "resume": "active", "cancel": "cancelled"}[action]
        conn.execute("INSERT INTO research_mission_controls VALUES (%s,%s,%s,%s,now())",
                     (event_key, mission_id, Jsonb(payload), fingerprint(payload)))
        conn.execute("UPDATE research_missions SET state=%s,updated_at=now() WHERE id=%s", (state, mission_id))
        self.company._event(conn, "research_mission_" + action, {**payload, "owner_event_id": event_key}, project["id"])
        # The controller owns worker cancellation/reconciliation; history and running jobs stay intact here.
        return self.snapshot(conn, mission_id)

    def pause(self, conn, mission_id, **kwargs):
        return self._control(conn, mission_id, "pause", **kwargs)

    def resume(self, conn, mission_id, **kwargs):
        return self._control(conn, mission_id, "resume", **kwargs)

    def cancel(self, conn, mission_id, **kwargs):
        return self._control(conn, mission_id, "cancel", **kwargs)

    def _lineage(self, conn, row, spec, proposal):
        predecessors = {str(value) for value in proposal.predecessor_trial_ids}
        if len(predecessors) != len(proposal.predecessor_trial_ids):
            raise PolicyError("Duplicate predecessor trial")
        for identity in predecessors:
            prior = conn.execute("""SELECT t.id FROM research_mission_trials t
                JOIN research_mission_interpretations i ON i.trial_id=t.id
                WHERE t.id=%s AND t.mission_id=%s AND t.result_id IS NOT NULL""",
                                 (identity, row["id"])).fetchone()
            if not prior:
                raise PolicyError("Predecessor must be an interpreted result in the same mission")
        latest = conn.execute("""SELECT id FROM research_mission_trials WHERE mission_id=%s
            AND result_id IS NOT NULL ORDER BY ordinal DESC LIMIT 1""", (row["id"],)).fetchone()
        if latest and str(latest["id"]) not in predecessors:
            raise PolicyError("Follow-up must reference the latest completed evidence")
        if not latest and (predecessors or not set(spec.baseline_source_ids).intersection(proposal.source_ids)):
            raise PolicyError("First hypothesis must reference the approved baseline")

    def add_proposal(self, conn, mission_id, proposal, *, actor):
        proposal = typed(HypothesisProposal, proposal)
        project, row, spec = self._locked(conn, mission_id, active=True)
        self._actor(actor)
        if actor != proposal.author:
            raise PolicyError("Proposal author does not match the authenticated role")
        payload = proposal.model_dump(mode="json")
        old = conn.execute("SELECT * FROM research_mission_proposals WHERE id=%s", (proposal.id,)).fetchone()
        if old:
            if str(old["mission_id"]) != str(mission_id):
                raise PolicyError("Proposal identity belongs to another mission")
            _same(old, payload)
            return as_json(old)
        if not set(proposal.change_axes) <= set(spec.allowed_changes):
            raise PolicyError("Proposed change is outside the approved scope")
        self._sources(conn, project["id"], proposal.source_ids)
        self._lineage(conn, row, spec, proposal)
        if proposal.supersedes_proposal_id:
            rejected = conn.execute("""SELECT p.id FROM research_mission_proposals p
                JOIN research_mission_rejections r ON r.proposal_id=p.id WHERE p.id=%s
                AND p.mission_id=%s AND p.cycle=%s""",
                                    (proposal.supersedes_proposal_id, mission_id, row["cycle"])).fetchone()
            if not rejected:
                raise PolicyError("Superseded proposal must be rejected in this mission cycle")
        saved = conn.execute("""INSERT INTO research_mission_proposals(id,mission_id,cycle,payload,digest)
            VALUES (%s,%s,%s,%s,%s) RETURNING *""",
                             (proposal.id, mission_id, row["cycle"], Jsonb(payload), fingerprint(payload))).fetchone()
        self.company._event(conn, "research_hypothesis_proposed", {"mission_id": str(mission_id),
                            "proposal_id": str(proposal.id), "digest": saved["digest"]}, project["id"])
        return as_json(saved)

    def add_challenge(self, conn, mission_id, challenge, *, actor):
        challenge = typed(Challenge, challenge)
        project, row, _ = self._locked(conn, mission_id, active=True)
        self._actor(actor)
        if challenge.reviewer != actor:
            raise PolicyError("Challenge reviewer does not match the authenticated role")
        payload = challenge.model_dump(mode="json")
        old = conn.execute("SELECT * FROM research_mission_challenges WHERE id=%s", (challenge.id,)).fetchone()
        if old:
            if str(old["mission_id"]) != str(mission_id):
                raise PolicyError("Challenge identity belongs to another mission")
            _same(old, payload)
            return as_json(old)
        proposal = conn.execute("SELECT * FROM research_mission_proposals WHERE id=%s AND mission_id=%s",
                                (challenge.proposal_id, mission_id)).fetchone()
        if not proposal or proposal["cycle"] != row["cycle"]:
            raise PolicyError("Challenge requires a current-cycle proposal")
        if actor == proposal["payload"]["author"]:
            raise PolicyError("A different role must challenge the proposal")
        self._sources(conn, project["id"], challenge.source_ids)
        saved = conn.execute("""INSERT INTO research_mission_challenges(id,mission_id,proposal_id,payload,digest)
            VALUES (%s,%s,%s,%s,%s) RETURNING *""", (challenge.id, mission_id, challenge.proposal_id,
                                                   Jsonb(payload), fingerprint(payload))).fetchone()
        self.company._event(conn, "research_hypothesis_challenged", {"mission_id": str(mission_id),
                            "challenge_id": str(challenge.id), "digest": saved["digest"]}, project["id"])
        return as_json(saved)

    def _cycle_ended(self, row, spec):
        return (row["cycle_trials"] >= spec.search.max_trials_per_cycle
                or row["stagnant_trials"] >= spec.search.patience)

    def _scope_exhausted(self, row, spec):
        return (spec.search.max_total_trials is not None
                and row["cumulative_trials"] >= spec.search.max_total_trials)

    def reject_proposal(self, conn, mission_id, proposal_id, *, actor, challenge_ids, rationale):
        project, row, _ = self._locked(conn, mission_id, active=True)
        self._actor(actor, "director")
        payload = as_json({"proposal_id": proposal_id, "actor": actor,
                           "challenge_ids": challenge_ids, "rationale": rationale})
        old = conn.execute("SELECT * FROM research_mission_rejections WHERE proposal_id=%s", (proposal_id,)).fetchone()
        if old:
            if str(old["mission_id"]) != str(mission_id):
                raise PolicyError("Rejected proposal belongs to another mission")
            _same(old, payload)
            return as_json(old)
        proposal = conn.execute("SELECT * FROM research_mission_proposals WHERE id=%s AND mission_id=%s",
                                (proposal_id, mission_id)).fetchone()
        if (not proposal or proposal["cycle"] != row["cycle"] or not challenge_ids
                or len(set(map(str, challenge_ids))) != len(challenge_ids)
                or not isinstance(rationale, str) or not rationale.strip()):
            raise PolicyError("Revision decision requires a current proposal, critique and rationale")
        if conn.execute("SELECT id FROM research_mission_trials WHERE proposal_id=%s", (proposal_id,)).fetchone():
            raise PolicyError("A selected proposal cannot be rejected retroactively")
        for identity in challenge_ids:
            challenge = conn.execute("""SELECT payload FROM research_mission_challenges
                WHERE id=%s AND mission_id=%s AND proposal_id=%s""", (identity, mission_id, proposal_id)).fetchone()
            if not challenge or challenge["payload"]["reviewer"] == proposal["payload"]["author"]:
                raise PolicyError("Revision decision requires independent challenge evidence")
            self._sources(conn, project["id"], challenge["payload"]["source_ids"])
        saved = conn.execute("""INSERT INTO research_mission_rejections(proposal_id,mission_id,payload,digest)
            VALUES (%s,%s,%s,%s) RETURNING *""", (proposal_id, mission_id, Jsonb(payload), fingerprint(payload))).fetchone()
        self.company._event(conn, "research_hypothesis_revision_requested", {"mission_id": str(mission_id), **payload}, project["id"])
        return as_json(saved)

    def select(self, conn, mission_id, proposal_id, *, actor, challenge_ids, rationale, trial_id):
        project, row, spec = self._locked(conn, mission_id, active=True)
        self._actor(actor, "director")
        if (not challenge_ids or len(set(map(str, challenge_ids))) != len(challenge_ids)
                or not isinstance(rationale, str) or not rationale.strip()):
            raise PolicyError("Director selection requires explicit rationale and independent challenges")
        selection = as_json({"proposal_id": proposal_id, "challenge_ids": challenge_ids,
                             "rationale": rationale, "actor": actor})
        old = conn.execute("SELECT * FROM research_mission_trials WHERE id=%s", (trial_id,)).fetchone()
        if old:
            if str(old["mission_id"]) != str(mission_id):
                raise PolicyError("Trial identity belongs to another mission")
            _same(old, selection, "selection_digest")
            return as_json(old)
        if self._scope_exhausted(row, spec):
            raise PolicyError("Approved scientific scope is exhausted; a new mission approval is required")
        if self._cycle_ended(row, spec):
            raise PolicyError("Cycle requires a report checkpoint and evidence-based renewal")
        pending = conn.execute("SELECT id FROM research_mission_trials WHERE mission_id=%s AND state<>'reported'",
                               (mission_id,)).fetchone()
        if pending:
            raise PolicyError("Previous trial must finish interpretation and publication before selection")
        proposal = conn.execute("SELECT * FROM research_mission_proposals WHERE id=%s AND mission_id=%s",
                                (proposal_id, mission_id)).fetchone()
        if not proposal or proposal["cycle"] != row["cycle"]:
            raise PolicyError("Selection requires a current-cycle proposal")
        if conn.execute("SELECT proposal_id FROM research_mission_rejections WHERE proposal_id=%s", (proposal_id,)).fetchone():
            raise PolicyError("Rejected proposal requires a revised hypothesis")
        self._sources(conn, project["id"], proposal["payload"]["source_ids"])
        self._lineage(conn, row, spec, typed(HypothesisProposal, proposal["payload"]))
        if row.get("program_id"):
            from .feedback import require_resolutions
            from .library import require_current

            require_current(conn, proposal["payload"]["source_ids"])
            require_resolutions(conn, mission_id, proposal_id)
        for identity in challenge_ids:
            challenge = conn.execute("""SELECT payload FROM research_mission_challenges
                WHERE id=%s AND mission_id=%s AND proposal_id=%s""", (identity, mission_id, proposal_id)).fetchone()
            if not challenge or challenge["payload"]["reviewer"] == proposal["payload"]["author"]:
                raise PolicyError("Selection challenge is missing or not independent")
            self._sources(conn, project["id"], challenge["payload"]["source_ids"])
        saved = conn.execute("""INSERT INTO research_mission_trials
            (id,mission_id,proposal_id,cycle,selection,selection_digest) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *""",
                             (trial_id, mission_id, proposal_id, row["cycle"], Jsonb(selection),
                              fingerprint(selection))).fetchone()
        self.company._event(conn, "research_trial_selected", {"mission_id": str(mission_id),
                            "trial_id": str(trial_id), **selection}, project["id"])
        return as_json(saved)

    def set_plan(self, conn, mission_id, trial_id, plan, *, actor, repair_source_ids=None, repair_rationale=None):
        plan = typed(TrialPlan, plan)
        project, row, spec = self._locked(conn, mission_id, active=True)
        trial = self._trial(conn, mission_id, trial_id)
        self._actor(actor, "engineer")
        if (plan.implementer != actor or str(plan.trial_id) != str(trial_id)
                or str(plan.proposal_id) != str(trial["proposal_id"]) or plan.mission_digest != row["manifest_digest"]
                or plan.execution_profile != spec.execution_profile or plan.repository != spec.code.repository
                or plan.input_files != spec.data.input_files or plan.lake_id != spec.data.lake_id
                or plan.development != spec.development or plan.worker_id != spec.resources.worker_id):
            raise PolicyError("Trial plan does not match the approved mission and selected proposal")
        if any(not _writable(path, spec.code.write_paths) for path in (*plan.changed_paths, *plan.config_files)):
            raise PolicyError("Trial plan writes outside the approved research scope")
        payload, digest = plan.model_dump(mode="json"), fingerprint(plan.model_dump(mode="json"))
        if trial["plan_digest"] == digest and trial["state"] != "technical_waiting":
            return as_json(trial)
        if trial["state"] not in {"selected", "prepared", "technical_waiting"}:
            raise PolicyError("An executing or finished trial plan is immutable")
        if trial["state"] == "technical_waiting":
            if not repair_rationale or not repair_rationale.strip():
                raise PolicyError("Technical retry requires repair evidence and rationale")
            self._sources(conn, project["id"], repair_source_ids)
        saved = conn.execute("""UPDATE research_mission_trials SET plan=%s,plan_digest=%s,state='prepared',
            job_id=NULL,updated_at=now() WHERE id=%s RETURNING *""", (Jsonb(payload), digest, trial_id)).fetchone()
        self.company._event(conn, "research_trial_plan_recorded", {"mission_id": str(mission_id),
                            "trial_id": str(trial_id), "plan": payload, "plan_digest": digest,
                            "repair_source_ids": repair_source_ids, "repair_rationale": repair_rationale}, project["id"])
        return as_json(saved)

    def attach_job(self, conn, mission_id, trial_id, job_id):
        project, row, spec = self._locked(conn, mission_id, active=True)
        trial = self._trial(conn, mission_id, trial_id)
        old = conn.execute("SELECT * FROM research_mission_attempts WHERE job_id=%s", (job_id,)).fetchone()
        if old:
            if str(old["trial_id"]) != str(trial_id) or old["plan_digest"] != trial["plan_digest"]:
                raise PolicyError("Worker job already belongs to a different trial attempt")
            return as_json(trial)
        if trial["state"] != "prepared":
            raise PolicyError("Only a prepared trial may attach a queued worker job")
        job = conn.execute("SELECT * FROM research_jobs WHERE id=%s FOR UPDATE", (job_id,)).fetchone()
        expected = {"mission_id": str(mission_id), "mission_digest": row["manifest_digest"],
                    "trial_id": str(trial_id), "plan_digest": trial["plan_digest"]}
        if (not job or str(job["project_id"]) != str(project["id"]) or job["revision"] != row["revision"]
                or job["recipe_id"] != ("kr-research-python-v2" if spec.schema_version == 2 else spec.execution_profile)
                or job["state"] != "queued"
                or job["approval_event_id"] != row["approval_event_id"]
                or job["approved_by"] != row["owner_user"]
                or any(job["manifest"].get(key) != value for key, value in expected.items())):
            raise PolicyError("Worker job is not bound to the same approved trial plan")
        conn.execute("INSERT INTO research_mission_attempts(job_id,trial_id,plan,plan_digest) VALUES (%s,%s,%s,%s)",
                     (job_id, trial_id, Jsonb(trial["plan"]), trial["plan_digest"]))
        saved = conn.execute("""UPDATE research_mission_trials SET job_id=%s,state='queued',updated_at=now()
            WHERE id=%s RETURNING *""", (job_id, trial_id)).fetchone()
        self.company._event(conn, "research_trial_queued", {**expected, "job_id": str(job_id)}, project["id"])
        return as_json(saved)

    def mark_running(self, conn, mission_id, trial_id, job_id):
        self._locked(conn, mission_id, active=True)
        trial = self._trial(conn, mission_id, trial_id)
        if str(trial["job_id"]) != str(job_id) or trial["state"] not in {"queued", "running"}:
            raise PolicyError("Running update does not match the active worker job")
        return as_json(conn.execute("""UPDATE research_mission_trials SET state='running',updated_at=now()
            WHERE id=%s RETURNING *""", (trial_id,)).fetchone())

    def record_outcome(self, conn, mission_id, trial_id, outcome):
        outcome = typed(TrialOutcome, outcome)
        project, row, spec = self._locked(conn, mission_id, active=True)
        trial = self._trial(conn, mission_id, trial_id)
        payload, digest = outcome.model_dump(mode="json"), fingerprint(outcome.model_dump(mode="json"))
        old = conn.execute("SELECT * FROM research_mission_outcomes WHERE id=%s", (outcome.id,)).fetchone()
        if old:
            if str(old["trial_id"]) != str(trial_id):
                raise PolicyError("Outcome identity belongs to another trial")
            _same(old, payload)
            return as_json(trial)
        if (trial["state"] not in {"queued", "running"} or not trial["job_id"]
                or fingerprint(outcome.plan.model_dump(mode="json")) != trial["plan_digest"]):
            raise PolicyError("Outcome does not match the submitted active trial plan")
        if outcome.metrics and (outcome.metrics.primary.model_dump(exclude={"value"}) != spec.objective.model_dump()
                                or not {risk.metric for risk in spec.risk_constraints} <= outcome.metrics.risks.keys()):
            raise PolicyError("Outcome is missing the approved objective or risk measurements")
        if outcome.finished_at > now():
            raise PolicyError("Outcome claims future execution evidence")
        conn.execute("""INSERT INTO research_mission_outcomes(id,trial_id,job_id,kind,payload,digest)
            VALUES (%s,%s,%s,%s,%s,%s)""", (outcome.id, trial_id, trial["job_id"], outcome.status,
                                           Jsonb(payload), digest))
        from .programs import ProgramStore

        ProgramStore(self.company).settle(conn, row, trial["job_id"], outcome)
        if outcome.status == "result":
            ordinal = row["cumulative_trials"] + 1
            conn.execute("""UPDATE research_missions SET cycle_trials=cycle_trials+1,
                cumulative_trials=cumulative_trials+1,updated_at=now() WHERE id=%s""", (mission_id,))
            saved = conn.execute("""UPDATE research_mission_trials SET state='received',result_id=%s,
                ordinal=%s,updated_at=now() WHERE id=%s RETURNING *""", (outcome.id, ordinal, trial_id)).fetchone()
        else:
            saved = conn.execute("""UPDATE research_mission_trials SET state='technical_waiting',updated_at=now()
                WHERE id=%s RETURNING *""", (trial_id,)).fetchone()
        self.company._event(conn, "research_trial_outcome", {"mission_id": str(mission_id),
                            "trial_id": str(trial_id), "outcome_id": str(outcome.id),
                            "digest": digest, "kind": outcome.status}, project["id"])
        return as_json(saved)

    def interpret(self, conn, mission_id, trial_id, interpretation, *, actor):
        value = typed(Interpretation, interpretation)
        project, row, spec = self._locked(conn, mission_id, active=True)
        trial = self._trial(conn, mission_id, trial_id)
        self._actor(actor)
        if value.author != actor or str(value.trial_id) != str(trial_id) or actor == (trial["plan"] or {}).get("implementer"):
            raise PolicyError("Interpretation requires a separate authenticated research role")
        payload = value.model_dump(mode="json")
        old = conn.execute("SELECT * FROM research_mission_interpretations WHERE trial_id=%s", (trial_id,)).fetchone()
        if old:
            _same(old, payload)
            return as_json(trial)
        result = conn.execute("SELECT * FROM research_mission_outcomes WHERE id=%s", (trial["result_id"],)).fetchone()
        if trial["state"] != "received" or not result or result["digest"] != value.outcome_digest:
            raise PolicyError("Interpretation requires the exact completed result")
        self._sources(conn, project["id"], value.source_ids)
        metrics = typed(TrialOutcome, result["payload"]).metrics
        feasible = all(metrics.risks[risk.metric] <= risk.maximum for risk in spec.risk_constraints)
        incumbent = None
        if row["incumbent_trial_id"]:
            incumbent = conn.execute("""SELECT o.payload FROM research_mission_trials t
                JOIN research_mission_outcomes o ON o.id=t.result_id WHERE t.id=%s""",
                                     (row["incumbent_trial_id"],)).fetchone()
        incumbent_value = typed(TrialOutcome, incumbent["payload"]).metrics.primary.value if incumbent else None
        score = metrics.primary.value
        delta = (score - incumbent_value) if incumbent_value is not None else None
        if delta is not None and spec.objective.direction == "minimize":
            delta = -delta
        best = feasible and (delta is None or delta > 0)
        improvement = feasible and (delta is None or delta > spec.search.min_improvement)
        conn.execute("INSERT INTO research_mission_interpretations(trial_id,payload,digest) VALUES (%s,%s,%s)",
                     (trial_id, Jsonb(payload), fingerprint(payload)))
        conn.execute("""UPDATE research_missions SET incumbent_trial_id=CASE WHEN %s THEN %s
            ELSE incumbent_trial_id END,stagnant_trials=CASE WHEN %s THEN 0 ELSE stagnant_trials+1 END,
            updated_at=now() WHERE id=%s""", (best, trial_id, improvement, mission_id))
        saved = conn.execute("""UPDATE research_mission_trials SET state='interpreted',feasible=%s,
            updated_at=now() WHERE id=%s RETURNING *""", (feasible, trial_id)).fetchone()
        self.company._event(conn, "research_trial_interpreted", {"mission_id": str(mission_id),
                            "trial_id": str(trial_id), "interpretation_digest": fingerprint(payload)}, project["id"])
        return as_json(saved)

    def checkpoint(self, conn, mission_id, trial_id, *, verify: Callable[[], AuditPublication]):
        """Internal only: the caller supplies a verifier, never model-submitted audit booleans.

        The adapter must verify actual audit/control/report file digests before returning
        AuditPublication and registering its approved source. This store binds that result
        to the exact immutable outcome and enforces implementer/validator independence.
        """
        project, row, _ = self._locked(conn, mission_id, active=True)
        trial = self._trial(conn, mission_id, trial_id)
        if trial["state"] not in {"interpreted", "reported"}:
            raise PolicyError("Publication requires an interpreted result")
        verified = verify()
        if not isinstance(verified, AuditPublication):
            raise PolicyError("Trusted verifier must return a bound AuditPublication, not a pass claim")
        value = typed(AuditPublication, verified)
        self._actor(value.verifier_role, "validator")
        result = conn.execute("SELECT digest FROM research_mission_outcomes WHERE id=%s", (trial["result_id"],)).fetchone()
        if (str(value.trial_id) != str(trial_id) or value.mission_digest != row["manifest_digest"]
                or value.outcome_digest != result["digest"] or value.verifier_role == trial["plan"]["implementer"]
                or value.published_at > now() or value.audit_files.get(value.verification.path) != value.verification.sha256):
            raise PolicyError("Verified publication is not bound to this exact independent audit scope")
        self._sources(conn, project["id"], [value.source_id])
        source = conn.execute("SELECT synthetic FROM sources WHERE id=%s", (value.source_id,)).fetchone()
        if source["synthetic"] and not self.company.settings.fixture_mode:
            raise PolicyError("Synthetic audit sources cannot publish real research")
        payload = value.model_dump(mode="json")
        old = conn.execute("SELECT * FROM research_mission_publications WHERE trial_id=%s", (trial_id,)).fetchone()
        if old:
            _same(old, payload)
            return self.snapshot(conn, mission_id)
        conn.execute("INSERT INTO research_mission_publications(trial_id,payload,digest) VALUES (%s,%s,%s)",
                     (trial_id, Jsonb(payload), fingerprint(payload)))
        conn.execute("UPDATE research_mission_trials SET state='reported',updated_at=now() WHERE id=%s", (trial_id,))
        self.company._event(conn, "research_trial_published", {"mission_id": str(mission_id),
                            "trial_id": str(trial_id), "publication_digest": fingerprint(payload)}, project["id"])
        return self.snapshot(conn, mission_id)

    def advance_cycle(self, conn, mission_id, *, cycle, actor, rationale, source_ids, predecessor_trial_ids):
        project, row, spec = self._locked(conn, mission_id, active=True)
        self._actor(actor, "director")
        payload = as_json({"cycle": cycle, "actor": actor, "rationale": rationale, "source_ids": source_ids,
                           "predecessor_trial_ids": predecessor_trial_ids})
        old = conn.execute("SELECT * FROM research_mission_cycles WHERE mission_id=%s AND cycle=%s",
                           (mission_id, cycle)).fetchone()
        if old:
            _same(old, payload)
            return self.snapshot(conn, mission_id)
        if self._scope_exhausted(row, spec):
            raise PolicyError("Approved scientific scope is exhausted; a new mission approval is required")
        if (not spec.search.continuous or row["cycle"] != cycle or not self._cycle_ended(row, spec)
                or not isinstance(rationale, str) or not rationale.strip()):
            raise PolicyError("Cycle renewal requires an approved continuous policy and completed cycle")
        latest = conn.execute("""SELECT id,state FROM research_mission_trials WHERE mission_id=%s
            AND cycle=%s ORDER BY ordinal DESC NULLS FIRST LIMIT 1""", (mission_id, cycle)).fetchone()
        if not latest or latest["state"] != "reported" or str(latest["id"]) not in set(map(str, predecessor_trial_ids)):
            raise PolicyError("Cycle renewal requires the latest audited report evidence")
        for identity in predecessor_trial_ids:
            prior = conn.execute("SELECT id FROM research_mission_trials WHERE id=%s AND mission_id=%s AND state='reported'",
                                 (identity, mission_id)).fetchone()
            if not prior:
                raise PolicyError("Cycle predecessor is not a reported result in this mission")
        self._sources(conn, project["id"], source_ids)
        conn.execute("INSERT INTO research_mission_cycles(mission_id,cycle,payload,digest) VALUES (%s,%s,%s,%s)",
                     (mission_id, cycle, Jsonb(payload), fingerprint(payload)))
        conn.execute("""UPDATE research_missions SET cycle=cycle+1,cycle_trials=0,stagnant_trials=0,
            updated_at=now() WHERE id=%s""", (mission_id,))
        self.company._event(conn, "research_cycle_renewed", {"mission_id": str(mission_id), **payload}, project["id"])
        return self.snapshot(conn, mission_id)

    def next_stage(self, conn, mission_id):
        project, row, spec = self._locked(conn, mission_id, current=False)
        result = {"mission_id": str(mission_id), "cycle": row["cycle"]}
        if project["revision"] != row["revision"] or project["owner_user"] != row["owner_user"] or project["status"] != "active":
            return {**result, "stage": "stale_revision"}
        if row["state"] != "active":
            return {**result, "stage": "approval" if row["state"] == "draft" else row["state"]}
        program_exhausted = False
        if row.get("program_id"):
            from .programs import ProgramStore

            _, program, program_spec = ProgramStore(self.company).locked(conn, row["program_id"])
            if program["state"] != "active":
                return {**result, "stage": "program_waiting"}
            usage = ProgramStore(self.company).usage(conn, row["program_id"])
            from .builds import profile_for

            profile = profile_for(self.company, spec).public_profile
            required_time = profile.qualification_timeout_seconds + profile.evaluation_timeout_seconds
            program_exhausted = (usage["trials"] >= program_spec.max_total_trials
                or usage["compute_seconds"] + required_time > program_spec.max_compute_seconds)
        trial = conn.execute("""SELECT * FROM research_mission_trials WHERE mission_id=%s AND state<>'reported'
            ORDER BY created_at,id LIMIT 1""", (mission_id,)).fetchone()
        if trial:
            if program_exhausted and trial["state"] in {"selected", "prepared", "technical_waiting"}:
                return {**result, "stage": "owner_review", "reason": "program_budget_exhausted"}
            stage = {"selected": "implementation", "prepared": "execution", "queued": "execution",
                     "running": "execution", "technical_waiting": "repair", "received": "interpretation",
                     "interpreted": "audit"}[trial["state"]]
            return {**result, "stage": stage, "trial_id": str(trial["id"]), "proposal_id": str(trial["proposal_id"])}
        if program_exhausted or self._scope_exhausted(row, spec):
            return {**result, "stage": "owner_review", "reason": "scientific_scope_exhausted"}
        if self._cycle_ended(row, spec):
            return {**result, "stage": "cycle_review" if spec.search.continuous else "owner_review"}
        latest = conn.execute("""SELECT id FROM research_mission_trials WHERE mission_id=%s
            AND result_id IS NOT NULL ORDER BY ordinal DESC LIMIT 1""", (mission_id,)).fetchone()
        proposals = conn.execute("""SELECT p.id,p.payload,EXISTS(SELECT 1 FROM research_mission_challenges c
            WHERE c.proposal_id=p.id) AS challenged FROM research_mission_proposals p WHERE p.mission_id=%s
            AND p.cycle=%s AND NOT EXISTS(SELECT 1 FROM research_mission_trials t WHERE t.proposal_id=p.id)
            AND NOT EXISTS(SELECT 1 FROM research_mission_rejections r WHERE r.proposal_id=p.id)
            ORDER BY p.created_at,p.id""", (mission_id, row["cycle"])).fetchall()
        for proposal in proposals:
            if latest and str(latest["id"]) not in proposal["payload"]["predecessor_trial_ids"]:
                continue
            return {**result, "stage": "selection" if proposal["challenged"] else "challenge",
                    "proposal_id": str(proposal["id"])}
        return {**result, "stage": "proposal"}

    def snapshot(self, conn, mission_id, *, public=True):
        _, row, _ = self._locked(conn, mission_id, current=False)
        trials = conn.execute("SELECT * FROM research_mission_trials WHERE mission_id=%s ORDER BY created_at,id",
                              (mission_id,)).fetchall()
        publications = conn.execute("""SELECT p.* FROM research_mission_publications p
            JOIN research_mission_trials t ON t.id=p.trial_id WHERE t.mission_id=%s""", (mission_id,)).fetchall()
        published = {str(value["trial_id"]): value["payload"] for value in publications}
        if public:
            # No prose, scores, risk measurements or worker-controlled exception text leaves
            # this consumer, even when it happens to contain numbers outside a metrics field.
            incumbent = str(row["incumbent_trial_id"]) if row["incumbent_trial_id"] else None
            return as_json({key: row[key] for key in ("id", "project_id", "revision", "manifest_digest", "state", "cycle",
                                                     "cycle_trials", "cumulative_trials", "created_at", "updated_at")} | {
                "stage": self.next_stage(conn, mission_id)["stage"],
                "incumbent_trial_id": incumbent if incumbent in published else None,
                "trials": [{"id": str(trial["id"]), "state": trial["state"], "cycle": trial["cycle"],
                            "ordinal": trial["ordinal"], "performance_visible": str(trial["id"]) in published,
                            **({"source_id": published[str(trial["id"])]["source_id"]}
                               if str(trial["id"]) in published else {})} for trial in trials]})
        result = as_json(row)
        result["stage"] = self.next_stage(conn, mission_id)
        result["trials"] = as_json(trials)
        result["challenge_responses"] = as_json(conn.execute("""SELECT * FROM research_challenge_responses
            WHERE mission_id=%s ORDER BY created_at""", (mission_id,)).fetchall())
        for name in ("proposals", "challenges", "rejections", "cycles"):
            result[name] = as_json(conn.execute(f"SELECT * FROM research_mission_{name} WHERE mission_id=%s ORDER BY created_at",
                                               (mission_id,)).fetchall())
        for name in ("attempts", "outcomes", "interpretations", "publications"):
            result[name] = as_json(conn.execute(f"""SELECT x.* FROM research_mission_{name} x
                JOIN research_mission_trials t ON t.id=x.trial_id WHERE t.mission_id=%s ORDER BY x.created_at""",
                                               (mission_id,)).fetchall())
        return result

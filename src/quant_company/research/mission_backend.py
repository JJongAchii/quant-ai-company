"""Trusted file adapters for mission stages; proposed code is never executed here.

Git, archive validation, qlab and publication run outside database transactions.
Every final transition rechecks the exact owner revision, stage and evidence identity.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from psycopg.types.json import Jsonb

from ..company import PolicyError, as_json, fingerprint, now, stable
from ..task_control import waiting_router
from .adaptive_contracts import AdaptiveAssignment, AdaptiveManifest, digest_model
from .builds import base_workspace, build_trial, profile_for
from .mission_contracts import MissionSpec, TrialOutcome
from .missions import MissionStore
from .policy_contracts import require_scope, scoped_kwargs
from .runner import ReportPublisher
from .worker import read_json, sha_file
from .workspace import canonical_path


def _json(value):
    return json.dumps(as_json(value), sort_keys=True, ensure_ascii=False, allow_nan=False).encode()


def _key(snapshot):
    return fingerprint([snapshot["stage"], len(snapshot["proposals"]), len(snapshot["outcomes"]),
                        len(snapshot.get("rejections", []))])


def _trial(snapshot, identity=None):
    identity = identity or snapshot["stage"].get("trial_id")
    for row in snapshot["trials"]:
        if str(row["id"]) == str(identity):
            return row
    raise PolicyError("mission_trial_missing")


class MissionBackend:
    def __init__(self, company, *, publisher=None):
        self.company = company
        self.store = MissionStore(company)
        self.publisher = publisher or ReportPublisher(company)

    @property
    def root(self):
        return self.company.settings.research_artifact_dir.resolve()

    def _path(self, path, *, exists=True):
        path = canonical_path(Path(path), exists=exists)
        if not path.is_relative_to(self.root):
            raise PolicyError("mission_file_outside_artifact_store")
        return path

    def _file(self, path, data):
        path = self._path(path, exists=False)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            with path.open("xb") as stream:
                stream.write(data)
            path.chmod(0o400)
        except FileExistsError:
            if path.is_symlink() or not path.is_file() or path.read_bytes() != data:
                raise PolicyError("mission_immutable_file_changed") from None
        return path

    def _blob(self, directory, label, value):
        data = _json(value)
        return self._file(directory / (label + "-" + hashlib.sha256(data).hexdigest() + ".json"), data)

    def _entry(self, path):
        path = self._path(path)
        return {"path": str(path), "sha256": sha_file(path), "size": path.stat().st_size}

    def _sources(self, snapshot):
        identities = set(snapshot["spec"]["baseline_source_ids"])
        for group in ("proposals", "challenges", "challenge_responses", "interpretations"):
            for row in snapshot.get(group, []):
                identities.update(row["payload"].get("source_ids", ()))
        identities.update(row["payload"]["source_id"] for row in snapshot["publications"])
        with self.company.db.transaction() as conn:
            self.company._check_sources(conn, snapshot["project_id"], sorted(identities))
            return conn.execute("""SELECT id,title,uri,content,available_at,synthetic FROM sources
                WHERE id=ANY(%s) ORDER BY id""", (sorted(identities),)).fetchall()

    def _compact(self, snapshot):
        """A navigable index; the full immutable snapshot remains available as a scoped file."""
        fields = ("id", "project_id", "owner_user", "revision", "manifest_digest", "state", "stage", "cycle",
                  "cycle_trials", "cumulative_trials", "stagnant_trials", "incumbent_trial_id")
        result = {key: snapshot[key] for key in fields}
        if snapshot.get("program_id"):
            result["program_id"] = snapshot["program_id"]
        result["spec"] = snapshot["spec"]
        spec = MissionSpec.model_validate(snapshot["spec"])
        if spec.research_scope is not None:
            result["research_scope"] = spec.research_scope.model_dump(mode="json")
        if snapshot.get("scientific_lineage") is not None:
            from .scientific_lineages import summary

            result["scientific_lineage"] = summary(snapshot["scientific_lineage"])
            result["scientific_lineage"]["full_history_file"] = "mission/scientific-lineage.json"
        result["full_history_file"] = "mission/history.json"
        result["full_spec_file"] = "mission/spec.json"
        result["incumbent_scope"] = (
            "incumbent_trial_id is this mission's best feasible measured trial only. "
            "Approved baseline_source_ids remain separate reference evidence; no replacement or "
            "superiority to an external baseline is established by becoming this mission's incumbent."
        )
        for group in ("trials", "proposals", "challenges", "challenge_responses", "rejections", "outcomes", "interpretations", "publications"):
            result[group] = []
            for row in snapshot.get(group, [])[-4:]:
                record = {key: row[key] for key in ("id", "trial_id", "proposal_id", "cycle", "state", "digest", "ordinal") if key in row}
                payload = row.get("payload", {})
                for key in ("author", "reviewer", "predecessor_trial_ids", "supersedes_proposal_id", "source_ids"):
                    if key in payload:
                        record[key] = payload[key]
                result[group].append(record)
        if snapshot["stage"].get("trial_id"):
            trial = _trial(snapshot)
            result["current_trial"] = {key: trial[key] for key in ("id", "proposal_id", "state", "plan_digest", "result_id")}
        if len(_json(result)) > 26000:
            spec = snapshot["spec"]
            result["spec"] = {key: spec[key] for key in ("schema_version", "title", "kind", "objective", "base_cost_bps",
                "stress_cost_bps", "risk_constraints", "development", "sealed", "code", "allowed_changes", "resources", "search")}
            result["spec"]["large_data_manifest_file"] = "mission/spec.json"
        if len(_json(result)) > 30000:
            raise PolicyError("mission_scope_context_requires_bounded_specification")
        return result

    def context(self, snapshot, row):
        if row and (row["state"] in {"running", "received", "completed"}
                    or (row["state"] == "waiting" and row["retry_at"] and row["retry_at"] > now())):
            return row["context"]
        spec = MissionSpec.model_validate(snapshot["spec"])
        profile = profile_for(self.company, spec)
        directory = self.root / "missions" / snapshot["id"] / "context"
        mappings = {
            "mission/history.json": self._entry(self._blob(directory, "history", snapshot)),
            "mission/spec.json": self._entry(self._blob(directory, "spec", snapshot["spec"])),
        }
        if snapshot.get("scientific_lineage") is not None:
            mappings["mission/scientific-lineage.json"] = self._entry(self._blob(
                directory, "scientific-lineage", snapshot["scientific_lineage"]))
        sources = self._sources(snapshot)
        source_index = []
        for source in sources:
            name = "sources/" + fingerprint(source["id"]) + ".json"
            mappings[name] = self._entry(self._blob(directory, "source", source))
            source_index.append({"source_id": source["id"], "file": name, "title": source["title"][:200]})
        stage = snapshot["stage"]["stage"]
        extra = {"mission": self._compact(snapshot), "evidence_sources": source_index}
        if snapshot.get("scientific_lineage", {}).get("origins"):
            extra["required_lineage_reads"] = ["mission/scientific-lineage.json"]
        # Staff can inspect the specific predecessor/critique without paging through
        # the entire append-only history. Full history remains available for older evidence.
        relevant = {}
        proposal_id = snapshot["stage"].get("proposal_id")
        for group in ("proposals", "challenges", "challenge_responses", "rejections", "interpretations", "outcomes"):
            rows = snapshot.get(group, [])
            if proposal_id and group in {"proposals", "challenges"}:
                rows = [item for item in rows if str(item.get("proposal_id", item.get("id"))) == proposal_id]
            for item in rows[-2:]:
                identity = str(item.get("id", item.get("trial_id", item.get("proposal_id"))))
                name = f"evidence/{group}/{identity}.json"
                mappings[name] = self._entry(self._blob(directory, group, item))
                relevant.setdefault(group, []).append(name)
        extra["relevant_evidence"] = relevant
        if stage in {"proposal", "challenge", "selection", "cycle_review", "implementation", "repair"}:
            prepared = base_workspace(self.company, profile)
            for name in profile.public_profile.code_paths:
                mappings["code/" + name] = self._entry(prepared.worktree / name)
            extra["frozen_experiment_code"] = {
                "config_path": "code/" + profile.config_path,
                "code_paths": ["code/" + name for name in profile.public_profile.code_paths],
                "rule": ("Inspect enough of this immutable code before proposing, challenging or selecting a change. "
                         "The change must already be registered and expressible through the approved write paths; "
                         "a new formula or parameter requires a new owner-approved mission."),
            }
            if snapshot.get("program_id"):
                extra["frozen_experiment_code"]["rule"] = (
                    "New feature, model and portfolio implementations are allowed inside approved write paths. "
                    "Keep evaluator, data scope, objective, costs and risk criteria fixed. "
                    "Implement the test obligations in challenge_responses; report their actual evidence.")
        if stage in {"implementation", "repair"}:
            extra["patch_base_commit"] = prepared.commit
            extra["patch_rule"] = "Patch paths omit the code/ prefix; expected_text is the entire base file."
        elif stage == "audit":
            extra.update(self._audit_context(snapshot, row, profile, mappings))
        extra["_private_files"] = mappings
        extra["available_files"] = [{"name": name, "sha256": item["sha256"], "size": item["size"]}
                                    for name, item in sorted(mappings.items())]
        if len(_json({key: value for key, value in extra.items() if not key.startswith("_")})) > 35000:
            raise PolicyError("mission_file_index_exceeds_context_budget")
        return extra

    def _current_stage(self, conn, row, snapshot):
        project = self.company._project(conn, snapshot["project_id"])
        _, mission, _ = self.store._locked(conn, snapshot["id"], active=True)
        current = self.store.snapshot(conn, snapshot["id"], public=False)
        stage = conn.execute("SELECT * FROM research_mission_stages WHERE id=%s FOR UPDATE", (row["id"],)).fetchone()
        if (not stage or str(stage["mission_id"]) != str(snapshot["id"])
                or stage["state"] != "received" or stage["attempt"] != row["attempt"]
                or str(stage["task_id"]) != str(row["task_id"])
                or stage["stage_key"] != _key(current) or stage["stage"] != current["stage"]["stage"]
                or mission["manifest_digest"] != snapshot["manifest_digest"]
                or fingerprint(stage["result"]) != fingerprint(row["result"])):
            raise PolicyError("mission_stage_superseded")
        if waiting_router(conn, project["id"]):
            raise PolicyError("mission_owner_instruction_pending")
        return project, current, stage

    def _build_records(self, conn, mission_id):
        return conn.execute("""SELECT id,task_id,result FROM research_mission_stages WHERE mission_id=%s
            AND stage IN ('implementation','repair') AND state='completed' ORDER BY created_at,id""", (mission_id,)).fetchall()

    def _check_repeated_experiment(self, records, receipt, trial_id, *, exclude_stage=None):
        def scientific_scope(manifest):
            return {key: manifest["spec"].get(key) for key in (
                "kind", "objective", "evaluation", "market", "data", "development", "base_cost_bps", "stress_cost_bps", "risk_constraints")}

        for row in records:
            if exclude_stage is not None and str(row["id"]) == str(exclude_stage):
                continue
            previous = row["result"].get("build_receipt")
            if (previous and previous["code_signature"] == receipt["code_signature"]
                    and scientific_scope(previous["manifest"]) == scientific_scope(receipt["manifest"])):
                if str(previous["manifest"]["trial_id"]) != str(trial_id):
                    raise PolicyError("mission_duplicate_scientific_configuration")

    def _program_records(self, conn, snapshot):
        if not snapshot.get("program_id"):
            return self._build_records(conn, snapshot["id"])
        return conn.execute("""SELECT s.id,s.task_id,s.result FROM research_mission_stages s
            JOIN research_missions m ON m.id=s.mission_id WHERE m.program_id=%s
            AND s.stage IN ('implementation','repair') AND s.state='completed' ORDER BY s.created_at,s.id""",
            (snapshot["program_id"],)).fetchall()

    def _repair_evidence(self, conn, snapshot, row, receipt, records):
        if row["stage"] != "repair":
            return []
        trial_id = str(receipt["manifest"]["trial_id"])
        previous = [record["result"]["build_receipt"] for record in records
                    if str(record["result"].get("build_receipt", {}).get("manifest", {}).get("trial_id")) == trial_id]
        if not previous:
            raise PolicyError("mission_repair_has_no_previous_attempt")
        source_ids = row["result"].get("repair_source_ids")
        self.company._check_sources(conn, snapshot["project_id"], source_ids or [])
        sources = conn.execute("SELECT id,content,available_at,synthetic FROM sources WHERE id=ANY(%s)",
                               (source_ids or [],)).fetchall()
        evidence = [{"source_id": source["id"], "content_sha256": hashlib.sha256(source["content"].encode()).hexdigest(),
                     "available_at": source["available_at"].isoformat(), "synthetic": source["synthetic"]} for source in sources]
        if previous[-1]["code_signature"] == receipt["code_signature"]:
            old_hashes = {item["content_sha256"] for prior in previous for item in prior.get("repair_evidence", [])}
            last_failure = next((item for item in reversed(snapshot["outcomes"])
                                 if item["trial_id"] == trial_id and item["kind"] == "technical_failure"), None)
            if not last_failure:
                raise PolicyError("mission_repair_has_no_failure_evidence")
            failed_at = datetime.fromisoformat(last_failure["payload"]["finished_at"])
            changed = any(item["content_sha256"] not in old_hashes
                          and datetime.fromisoformat(item["available_at"]) > failed_at
                          and (not item["synthetic"] or self.company.settings.fixture_mode) for item in evidence)
            if not changed:
                raise PolicyError("mission_unchanged_technical_retry")
        return evidence

    def apply_stage(self, row, snapshot):
        if row["stage"] == "audit":
            return self._publish_audit(row, snapshot)
        if row["stage"] not in {"implementation", "repair"}:
            raise PolicyError("mission_backend_stage_unsupported")
        receipt = build_trial(self.company, row, snapshot)
        manifest = AdaptiveManifest.model_validate(receipt["manifest"])
        with self.company.db.transaction() as conn:
            project, current, _ = self._current_stage(conn, row, snapshot)
            records = self._build_records(conn, current["id"])
            self._check_repeated_experiment(self._program_records(conn, current), receipt, manifest.trial_id)
            receipt["repair_evidence"] = self._repair_evidence(conn, current, row, receipt, records)
            kwargs = {}
            if row["stage"] == "repair":
                kwargs = {"repair_source_ids": row["result"]["repair_source_ids"],
                          "repair_rationale": row["result"]["rationale"]}
            self.store.set_plan(conn, current["id"], manifest.trial_id, manifest.plan, actor="engineer", **kwargs)
            if manifest.spec.scientific_lineage is not None:
                from .scientific_lineages import bind_experiment

                bind_experiment(conn, project["id"], manifest.spec.scientific_lineage.id,
                                manifest.trial_id, receipt["code_signature"])
            result = {"proposal": row["result"], "build_receipt": receipt}
            conn.execute("""UPDATE research_mission_stages SET state='completed',result=%s,error=NULL,updated_at=now()
                WHERE id=%s""", (Jsonb(result), row["id"]))
            self.company._event(conn, "research_trial_build_ready", {"mission_id": current["id"],
                                "trial_id": str(manifest.trial_id), "stage_id": str(row["id"]),
                                "plan_digest": manifest.plan_digest, "code_signature": receipt["code_signature"]}, project["id"])
        return {"state": "completed", "trial_id": str(manifest.trial_id)}

    def enqueue(self, snapshot):
        trial = _trial(snapshot)
        if trial["state"] in {"queued", "running"}:
            return {"state": "waiting", "job_id": trial["job_id"]}
        if trial["state"] != "prepared":
            return {"state": "waiting"}
        with self.company.db.transaction() as conn:
            records = self._build_records(conn, snapshot["id"])
        candidates = [row for row in records if row["result"].get("build_receipt", {}).get("manifest", {}).get("plan_digest")
                      == trial["plan_digest"] and str(row["result"]["build_receipt"]["manifest"]["trial_id"]) == str(trial["id"])]
        if not candidates:
            raise PolicyError("mission_trusted_build_receipt_missing")
        stage = candidates[-1]
        receipt = stage["result"]["build_receipt"]
        manifest = AdaptiveManifest.model_validate(receipt["manifest"])
        profile_for(self.company, manifest.spec)
        bundle = self._path(receipt["bundle_path"])
        if sha_file(bundle) != manifest.bundle_sha256:
            raise PolicyError("mission_code_bundle_changed")
        build_manifest = self._path(bundle.parent / "manifest.json")
        if sha_file(build_manifest) != receipt["build_manifest_sha256"]:
            raise PolicyError("mission_build_receipt_changed")
        identity = stable(f"mission-job:{trial['id']}:{trial['plan_digest']}:{stage['id']}")
        with self.company.db.transaction() as conn:
            project = self.company._project(conn, snapshot["project_id"])
            _, mission, _ = self.store._locked(conn, snapshot["id"], active=True)
            current = self.store.snapshot(conn, snapshot["id"], public=False)
            current_trial = _trial(current, trial["id"])
            if current_trial["state"] in {"queued", "running"}:
                return {"state": "waiting", "job_id": current_trial["job_id"]}
            if (current_trial["state"] != "prepared" or current_trial["plan_digest"] != manifest.plan_digest
                    or mission["manifest_digest"] != manifest.mission_digest or waiting_router(conn, project["id"])):
                raise PolicyError("mission_enqueue_superseded")
            self._check_repeated_experiment(self._program_records(conn, current), receipt, trial["id"], exclude_stage=stage["id"])
            old = conn.execute("SELECT * FROM research_jobs WHERE id=%s FOR UPDATE", (identity,)).fetchone()
            if old:
                if old["manifest_digest"] != digest_model(manifest) or old["bundle_sha256"] != manifest.bundle_sha256:
                    raise PolicyError("mission_job_identity_changed")
            else:
                conn.execute("""INSERT INTO research_jobs(id,project_id,task_id,revision,recipe_id,manifest,manifest_digest,
                    company_commit,state,approval_event_id,approved_by,approved_at,priority,bundle_path,bundle_sha256,
                    mission_id,trial_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'queued',%s,%s,%s,%s,%s,%s,%s,%s)""",
                             (identity, project["id"], stage["task_id"], mission["revision"], manifest.id,
                              Jsonb(manifest.model_dump(mode="json")), digest_model(manifest), manifest.company_commit,
                              mission["approval_event_id"], mission["owner_user"], mission["approved_at"],
                              0 if manifest.spec.resources.priority == "owner" else 100, str(bundle), manifest.bundle_sha256,
                              mission["id"], manifest.trial_id))
            from .programs import ProgramStore

            public = profile_for(self.company, manifest.spec).public_profile
            ProgramStore(self.company).reserve(conn, mission, identity, trial["id"],
                math.ceil(public.qualification_timeout_seconds + public.evaluation_timeout_seconds))
            self.store.attach_job(conn, mission["id"], trial["id"], identity)
        return {"state": "queued", "job_id": identity}

    def _assignment(self, job):
        return AdaptiveAssignment(recipe_id=job["recipe_id"], job_id=job["id"], project_id=job["project_id"], revision=job["revision"],
            manifest_digest=job["manifest_digest"], approval_event_id=job["approval_event_id"],
            lease_token=job["lease_token"], action="reconcile", manifest=AdaptiveManifest.model_validate(job["manifest"]))

    def _validated(self, job):
        from .adaptive_report import validate_adaptive_bundle

        assignment = self._assignment(job)
        archive = self._path(job["artifact_path"])
        if sha_file(archive) != job["artifact_sha256"]:
            raise PolicyError("mission_received_archive_changed")
        profile = profile_for(self.company, assignment.manifest.spec)
        return validate_adaptive_bundle(archive, assignment, assignment.manifest,
            expected_company_commit=job["company_commit"], execution_profile=profile.public_profile)

    def _failure_outcome(self, job):
        manifest = AdaptiveManifest.model_validate(job["manifest"])
        reason = job["error"] if re.fullmatch(r"[a-z_]{1,80}", job["error"] or "") else "worker_error"
        evidence = {key: job[key] for key in ("id", "project_id", "revision", "manifest_digest", "company_commit",
                    "worker_id", "claimed_at", "heartbeat_at", "sequence", "updated_at")}
        evidence.update(state="failed", reason=reason, source="terminal_worker_state_record")
        path = self._blob(self.root / "failures" / str(job["id"]), "failure", evidence)
        return TrialOutcome(**scoped_kwargs(manifest.spec), id=stable("mission-failure:" + str(job["id"])),
            plan=manifest.plan,
            status="technical_failure", started_at=job["claimed_at"] or job["created_at"],
            finished_at=job["heartbeat_at"] or job["updated_at"],
            evidence_files={str(path.relative_to(self.root)): sha_file(path)}, failure_code=reason,
            resume_condition="Correct the recorded failure with changed code or newly verified external evidence")

    def reconcile(self):
        with self.company.db.transaction() as conn:
            job = conn.execute("""SELECT j.* FROM research_jobs j JOIN research_missions m ON m.id=j.mission_id
                JOIN projects p ON p.id=j.project_id WHERE j.recipe_id IN ('kr-etf-monthly-python-v1','kr-research-python-v2')
                AND j.state IN ('received','failed')
                AND m.state='active' AND p.status='active' AND m.revision=p.revision
                AND NOT EXISTS(SELECT 1 FROM research_mission_outcomes o WHERE o.job_id=j.id)
                ORDER BY j.priority,j.created_at,j.id LIMIT 1""").fetchone()
        if not job:
            return {"state": "idle"}
        if job["state"] == "failed":
            outcome = self._failure_outcome(job)
        else:
            try:
                validated = self._validated(job)
                references = {name: hashlib.sha256(content).hexdigest() for name, content in validated.contents.items()}
                outcome = TrialOutcome(id=stable("mission-outcome:" + str(job["id"]) + ":" + job["artifact_sha256"]),
                    **scoped_kwargs(validated.manifest.spec),
                    plan=validated.manifest.plan, status="result", started_at=validated.receipt.started_at,
                    finished_at=validated.receipt.completed_at, evidence_files=references,
                    qualification={"path": "qualification.json", "sha256": validated.receipt.qualification_sha256},
                    result={"path": "result.json", "sha256": validated.receipt.result_sha256}, metrics=validated.result.metrics)
            except (ValueError, OSError):
                with self.company.db.transaction() as conn:
                    self.company._project(conn, job["project_id"])
                    conn.execute("""UPDATE research_jobs SET state='awaiting_audit',error='adaptive_evidence_unverified',
                        updated_at=now() WHERE id=%s AND state='received' AND artifact_sha256=%s""",
                                 (job["id"], job["artifact_sha256"]))
                return {"state": "awaiting_audit", "job_id": str(job["id"])}
        with self.company.db.transaction() as conn:
            self.company._project(conn, job["project_id"])
            self.store._locked(conn, job["mission_id"], active=True)
            current = conn.execute("SELECT * FROM research_jobs WHERE id=%s FOR UPDATE", (job["id"],)).fetchone()
            if (current["state"] != job["state"] or current["manifest_digest"] != job["manifest_digest"]
                    or current["artifact_sha256"] != job["artifact_sha256"]):
                return {"state": "superseded"}
            self.store.record_outcome(conn, job["mission_id"], job["trial_id"], outcome)
        return {"state": "received" if outcome.status == "result" else "technical_waiting", "job_id": str(job["id"])}

    # Audit methods below use only pinned qlab/file APIs. They never accept a model pass flag.
    def _qlab_profile(self):
        from .audit import QlabProfile

        path = self.company.settings.research_qlab_profile_file
        if not path or not path.is_file() or path.is_symlink():
            raise PolicyError("mission_qlab_profile_unavailable")
        value = read_json(path)
        if not isinstance(value, dict) or set(value) != {"root", "commit", "python_executable"}:
            raise PolicyError("mission_qlab_profile_invalid")
        return QlabProfile(Path(value["root"]), value["commit"], Path(value["python_executable"]))

    def _actor_turns(self, conn, task_id, actor, snapshot):
        task = conn.execute("SELECT * FROM tasks WHERE id=%s", (task_id,)).fetchone()
        turns = conn.execute("SELECT * FROM turns WHERE task_id=%s ORDER BY sequence", (task_id,)).fetchall()
        if (not task or task["agent"] != actor or task["kind"] != "research_stage"
                or str(task["project_id"]) != str(snapshot["project_id"]) or task["revision"] != snapshot["revision"]
                or task["status"] != "completed" or not turns
                or str(turns[0]["id"]) != stable("turn:" + str(task_id) + ":1")):
            raise PolicyError("mission_independent_task_binding_invalid")
        reconciled = set()
        if actor == "validator":
            events = conn.execute("""SELECT detail FROM events WHERE project_id=%s AND kind='operator_retry'
                ORDER BY id""", (task["project_id"],)).fetchall()
            for event in events:
                reconciled.update(event["detail"].get("reconciled_turn_ids", []))
        from .controller import AUDIT_RECONCILED_ERROR

        for turn in turns:
            if (turn["status"] == "stale" and str(turn["id"]) in reconciled
                    and turn["error"] == AUDIT_RECONCILED_ERROR and turn["request"]
                    and turn["request"].get("request_id") == str(turn["id"])
                    and turn["response"] is None):
                continue
            if (turn["status"] != "completed" or not turn["request"] or not turn["response"]
                    or turn["request"].get("request_id") != str(turn["id"])
                    or turn["response"].get("request_id") != str(turn["id"])):
                raise PolicyError("mission_independent_turn_evidence_missing")
        return turns

    def _audit_inputs(self, snapshot, validator_request_id):
        from .adaptive_report import ReportHistory
        from .audit import AuditBinding, AuditTrialBinding

        outcomes = [row for row in snapshot["outcomes"] if row["kind"] == "result"]
        if not outcomes:
            raise PolicyError("mission_audit_requires_result_evidence")
        with self.company.db.transaction() as conn:
            jobs = conn.execute("SELECT * FROM research_jobs WHERE id=ANY(%s)",
                                ([UUID(row["job_id"]) for row in outcomes],)).fetchall()
            records = self._build_records(conn, snapshot["id"])
            actors = {}
            for row in outcomes:
                plan_digest = fingerprint(row["payload"]["plan"])
                matching = [record for record in records if record["result"]["build_receipt"]["manifest"]["plan_digest"] == plan_digest]
                if not matching:
                    raise PolicyError("mission_audit_implementer_evidence_missing")
                turns = self._actor_turns(conn, matching[-1]["task_id"], "engineer", snapshot)
                actors[row["trial_id"]] = str(turns[0]["id"])
        by_job = {str(job["id"]): job for job in jobs}
        trials, bindings = [], []
        for row in outcomes:
            if row["job_id"] not in by_job:
                raise PolicyError("mission_audit_result_archive_missing")
            value = self._validated(by_job[row["job_id"]])
            trials.append(value)
            bindings.append(AuditTrialBinding(trial_id=row["trial_id"], plan_digest=value.manifest.plan_digest,
                outcome_digest=row["digest"], archive_sha256=value.archive_sha256,
                implementer_request_id=actors[row["trial_id"]]))
        binding = AuditBinding(**scoped_kwargs(MissionSpec.model_validate(snapshot["spec"])),
            mission_id=snapshot["id"], revision=snapshot["revision"],
            mission_digest=snapshot["manifest_digest"], validator_request_id=validator_request_id, trials=tuple(bindings))
        history = ReportHistory(current_cycle=snapshot["cycle"],
            cumulative_scientific_trials=sum(trial.receipt.scientific_trials_added for trial in trials),
            cumulative_technical_attempts=sum(row["kind"] == "technical_failure" for row in snapshot["outcomes"]),
            trial_cycles={str(trial.manifest.trial_id): _trial(snapshot, trial.manifest.trial_id)["cycle"] for trial in trials},
            best_trial_id=UUID(snapshot["incumbent_trial_id"]) if snapshot["incumbent_trial_id"] else None,
            last_trial_id=UUID(snapshot["stage"]["trial_id"]))
        if snapshot.get("scientific_lineage") is not None:
            from dataclasses import replace

            history = replace(history, scientific_lineage=snapshot["scientific_lineage"])
        return tuple(trials), binding, history

    def _audit_context(self, snapshot, row, profile, mappings):
        from .audit import audit_context, prepare_audit_package
        from .causal_evidence import audit_supplements

        stage_id = stable(f"mission-stage:{snapshot['id']}:{_key(snapshot)}")
        if row and str(row["id"]) != stage_id:
            raise PolicyError("mission_audit_stage_binding_invalid")
        attempt = row["attempt"] + 1 if row else 1
        task_id = stable(f"mission-stage-attempt:{stage_id}:{attempt}")
        request_id = stable(f"turn:{task_id}:1")
        trials, binding, history = self._audit_inputs(snapshot, request_id)
        directory = self.root / "missions" / snapshot["id"] / "audit" / stage_id / str(attempt)
        qlab_profile = self._qlab_profile()
        package = prepare_audit_package(directory, trials, mission_spec=MissionSpec.model_validate(snapshot["spec"]),
            binding=binding, qlab_profile=qlab_profile, history=history,
            supplements=audit_supplements(trials))
        required_reads = {}
        resident_paths = []
        for chunk in audit_context(package):
            name = "audit/" + chunk["path"]
            if chunk["encoding"] == "utf-8" and name not in required_reads:
                entry = self._entry(package.root / chunk["path"])
                entry["characters"] = chunk["total_chars"]
                mappings[name] = entry
                required_reads[name] = {"sha256": entry["sha256"], "characters": chunk["total_chars"]}
                causal_code = chunk["path"].endswith(("/engine.py", "/adapter.py", "/signals.py"))
                if causal_code or chunk["path"].startswith("scope/supplements/"):
                    resident_paths.append(name)
        metadata = self._entry(package.root / "package.json")
        mappings["audit/package.json"] = metadata
        issued = now().astimezone(UTC).date().isoformat()
        return {
            "audit": {"judge": "leak-auditor", "target": ".", "issued": issued,
                      "objective_digest": package.objective_digest, "scope_digest": package.scope_digest,
                      "scope": sorted(package.scope_files), "validator_request_id": request_id,
                      "binding_file": "audit/scope/binding.json", "requires_all_text_files_read": True,
                      "resident_evidence_paths": sorted(resident_paths)},
            "_audit": {"delivery_version": 2, "root": str(package.root), "binding": binding.model_dump(mode="json"),
                       "history": history.to_dict(), "issued": issued, "required_reads": required_reads,
                       "qlab_commit": qlab_profile.commit, "profile_digest": digest_model(profile.public_profile)},
        }

    def _check_audit_turns(self, conn, row, snapshot):
        audit = row["context"].get("_audit")
        if not audit or row["actor"] != "validator":
            raise PolicyError("mission_audit_context_missing")
        turns = self._actor_turns(conn, row["task_id"], "validator", snapshot)
        if audit["binding"]["validator_request_id"] != str(turns[0]["id"]):
            raise PolicyError("mission_validator_request_changed")
        from .audit_delivery import check_delivery, enabled

        if enabled(row):
            check_delivery(conn, row, turns)
            return
        current_ids = {UUID(stable("stage-read:" + str(turn["id"]))) for turn in turns}
        attempts = [row["attempt"]]
        allowed_ids = set(current_ids)
        resume = row["context"].get("_audit_resume")
        if resume:
            event = conn.execute("""SELECT 1 FROM events WHERE project_id=%s AND kind='operator_retry'
                AND detail->>'stage_id'=%s AND detail->>'task_id'=%s
                AND detail->>'recovery_mode'='next_attempt_exact_digest_reads' LIMIT 1""",
                                 (snapshot["project_id"], str(row["id"]), resume["task_id"])).fetchone()
            previous = conn.execute("""SELECT a.task_id,t.agent,t.kind,t.project_id,t.revision,t.status
                FROM research_stage_attempts a JOIN tasks t ON t.id=a.task_id
                WHERE a.stage_id=%s AND a.attempt=%s""", (row["id"], resume["attempt"])).fetchone()
            if (not event or not previous or str(previous["task_id"]) != resume["task_id"]
                    or previous["agent"] != "validator" or previous["kind"] != "research_stage"
                    or str(previous["project_id"]) != str(snapshot["project_id"])
                    or previous["revision"] != snapshot["revision"] or previous["status"] != "blocked"):
                raise PolicyError("mission_audit_resume_binding_invalid")
            prior_turns = conn.execute("SELECT * FROM turns WHERE task_id=%s ORDER BY sequence",
                                       (previous["task_id"],)).fetchall()
            failed_seen = False
            for turn in prior_turns:
                if str(turn["id"]) == resume["failed_turn_id"]:
                    failed_seen = (turn["status"] == "blocked" and turn["response"] is None
                                   and turn["request"] is not None)
                    continue
                if (turn["status"] != "completed" or not turn["request"] or not turn["response"]
                        or turn["request"].get("request_id") != str(turn["id"])
                        or turn["response"].get("request_id") != str(turn["id"])):
                    raise PolicyError("mission_audit_resume_turn_invalid")
                allowed_ids.add(UUID(stable("stage-read:" + str(turn["id"]))))
            if not failed_seen:
                raise PolicyError("mission_audit_resume_failure_missing")
            attempts.append(resume["attempt"])
        raw_reads = conn.execute("""SELECT id,attempt,path,character_offset,content,next_offset,sha256,created_at
            FROM research_stage_reads WHERE stage_id=%s AND attempt=ANY(%s)
            ORDER BY created_at,id""", (row["id"], attempts)).fetchall()
        if any(item["id"] not in allowed_ids for item in raw_reads):
            raise PolicyError("mission_audit_read_turn_binding_invalid")
        from .controller import _compatible_stage_reads

        reads = _compatible_stage_reads(row, raw_reads)
        # A successor attempt has a new provider thread. Digest-compatible reads
        # from the failed task remain auditable recovery evidence, but only bytes
        # delivered in this attempt can satisfy the validator's evidence contract.
        current_reads = [item for item in reads if item["attempt"] == row["attempt"]]
        delivered = set()
        for turn in turns:
            if turn["status"] != "completed" or not turn["response"]:
                continue
            try:
                data = json.loads(turn["request"]["prompt"].split("\nMISSION DATA JSON:\n", 1)[1])
                for chunk in data.get("read_chunks", []):
                    delivered.add((chunk["path"], chunk["offset"], chunk["content"], chunk["next_offset"]))
            except (ValueError, IndexError, KeyError, TypeError):
                raise PolicyError("mission_audit_prompt_delivery_invalid") from None
        if any((item["path"], item["character_offset"], item["content"], item["next_offset"]) not in delivered
               for item in current_reads):
            raise PolicyError("mission_audit_evidence_not_delivered")
        for path, expected in audit["required_reads"].items():
            position = 0
            file_reads = [item for item in current_reads if item["path"] == path]
            for item in file_reads:
                if item["sha256"] != expected["sha256"] or item["character_offset"] != position:
                    raise PolicyError("mission_audit_read_binding_invalid")
                position += len(item["content"])
                if item["next_offset"] is not None and item["next_offset"] != position:
                    raise PolicyError("mission_audit_read_chunk_invalid")
            if not file_reads or position != expected["characters"]:
                raise PolicyError("mission_audit_evidence_not_fully_read")
        artifacts = turns[-1]["response"].get("decision", {}).get("artifacts", [])
        if len(artifacts) != 1 or json.loads(artifacts[0]["content"]) != row["result"]:
            raise PolicyError("mission_audit_response_binding_invalid")

    def _report_archive(self, package, verified, report_path):
        names = sorted({*package.scope_files, *verified.audit_files, str(report_path.relative_to(package.root))})
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in names:
                content = self._path(package.root / name).read_bytes()
                entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                entry.create_system = 3
                entry.external_attr = 0o100444 << 16
                archive.writestr(entry, content)
        data = stream.getvalue()
        return self._file(package.root / (hashlib.sha256(data).hexdigest() + ".zip"), data)

    def _publish_audit(self, row, snapshot):
        from .adaptive_report import build_adaptive_report
        from .audit import (
            AuditBinding,
            audit_publication,
            inspect_audit_verdict,
            verify_audit_package,
            write_audit,
        )
        from .mission_contracts import EvidenceRef

        if set(row["result"]) != {"markdown"} or not isinstance(row["result"]["markdown"], str):
            raise PolicyError("mission_validator_requires_actual_markdown")
        with self.company.db.transaction() as conn:
            self._current_stage(conn, row, snapshot)
            self._check_audit_turns(conn, row, snapshot)
        context = row["context"]["_audit"]
        package_root = self._path(context["root"])
        qlab_profile = self._qlab_profile()
        if context["qlab_commit"] != qlab_profile.commit:
            raise PolicyError("mission_qlab_profile_changed_during_audit")
        expected = AuditBinding.model_validate(context["binding"])
        trials, current_binding, history = self._audit_inputs(snapshot, expected.validator_request_id)
        if current_binding != expected or history.to_dict() != context["history"]:
            raise PolicyError("mission_audit_history_changed")
        issued = datetime.fromisoformat(context["issued"]).date()
        audit_path = write_audit(package_root, row["result"]["markdown"], expected=expected,
                                 qlab_profile=qlab_profile, issued=issued, rendered_at=now())
        verdict = inspect_audit_verdict(package_root, audit_path, expected=expected, qlab_profile=qlab_profile)
        if verdict != "pass":
            from .audit_delivery import hold_audit

            with self.company.db.transaction() as conn:
                self._current_stage(conn, row, snapshot)
                hold_audit(self.company, conn, row, "audit_" + verdict, verdict=verdict,
                           audit_path=str(audit_path.relative_to(package_root)), audit_sha256=sha_file(audit_path),
                           scope_digest=row["context"]["audit"]["scope_digest"])
            return {"state": "waiting", "verdict": verdict, "requires_remediation": True}
        verified = verify_audit_package(package_root, audit_path, expected=expected, qlab_profile=qlab_profile)
        meaning = None
        if snapshot.get("program_id"):
            meaning = self._meaning_review(row, snapshot, verified)
            if meaning is None:
                return {"state": "waiting", "stage": "meaning"}
        report = build_adaptive_report(trials, audit=verified, history=history)
        if meaning is not None:
            import html

            report["summary"]["meaning_review"] = meaning
            report["html"] = report["html"].replace("</body>",
                "<section><h2>독립 해석·미해결 반론</h2><pre>" + html.escape(
                    json.dumps(meaning, ensure_ascii=False, indent=2)) + "</pre></section></body>")
        report_path = self._file(package_root / "report.html", report["html"].encode())
        archive = self._report_archive(verified.package, verified, report_path)
        current_trial = _trial(snapshot)
        published = self.publisher.publish(str(current_trial["job_id"]), report["html"], archive)
        if published.get("html_sha256") != sha_file(report_path):
            raise PolicyError("mission_published_report_identity_changed")
        published["renderer_company_commit"] = self.company.settings.company_code_commit
        published["report_archive_sha256"] = sha_file(archive)
        source_id = "mission-report:" + str(current_trial["id"])
        publication = audit_publication(verified, UUID(current_trial["id"]), source_id=source_id,
            report=EvidenceRef(path="report.html", sha256=sha_file(report_path)), published_at=now())
        source = {"kind": "adaptive_discovery", "mission_id": snapshot["id"], "trial_id": current_trial["id"],
                  "revision": snapshot["revision"], "manifest_digest": snapshot["manifest_digest"],
                  "summary": report["summary"], "report": published,
                  "interpretation": "독립 감사 파일에 연결된 개발구간 연구입니다. 확증·투자 승인은 포함하지 않습니다."}
        scope = MissionSpec.model_validate(snapshot["spec"]).research_scope
        scope_metadata = {"research_scope": scope.model_dump(mode="json")} if scope is not None else {}
        source.update(scope_metadata)
        if scope is not None and scope.data_mode == "frozen_vintage_retrospective":
            source["interpretation"] = (
                "고정 수정본과 가정된 가용 시각의 조건부 사후 연구입니다. 과거 실제 알파·정확 재현·"
                "분배금 총수익·실제 체결·배치·2026 미노출 확인을 입증하지 않습니다.")
        content = _json(source).decode()
        if len(content) > 100000:
            raise PolicyError("mission_verified_source_too_large")
        with self.company.db.transaction() as conn:
            project, current, _ = self._current_stage(conn, row, snapshot)
            self._check_audit_turns(conn, row, current)
            live_trial = _trial(current)
            job = conn.execute("SELECT * FROM research_jobs WHERE id=%s FOR UPDATE", (live_trial["job_id"],)).fetchone()
            if job["state"] != "received" or job["artifact_sha256"] != next(
                    value.archive_sha256 for value in trials if str(value.manifest.trial_id) == live_trial["id"]):
                raise PolicyError("mission_publication_job_changed")
            conn.execute("""INSERT INTO sources(id,title,uri,content,available_at,project_id,approved,synthetic,metadata)
                VALUES (%s,%s,%s,%s,now(),%s,true,%s,%s)""",
                         (source_id, "자율 연구 개발구간 보고서", published["uri"], content, project["id"],
                          self.company.settings.fixture_mode, Jsonb({**scope_metadata, "kind": "adaptive_discovery",
                          "mission_id": current["id"], "trial_id": live_trial["id"],
                          "manifest_digest": current["manifest_digest"], "audit_scope_digest": verified.package.scope_digest})))
            self.store.checkpoint(conn, current["id"], live_trial["id"], verify=lambda: publication)
            if meaning is not None:
                from .library import publish_library

                publish_library(self.company, conn, project, source_id, published, meaning["conclusion"])
            task = self.company._new_task(conn, project, "director",
                f"독립 검증을 마친 연구 출처 {source_id}를 read_source로 읽고 소유자를 태그하여 최종 보고하세요. "
                "보고서 링크·기존 최선과 이번 결과·개발구간 한계·다음 단계를 짧게 정리하세요. "
                "새 위임·실험·투자 승인을 추가하지 마세요. 합성 fixture라면 실제 금융 성과가 아님을 명시하세요.",
                task_id=stable("mission-report:" + live_trial["id"]), status_only=True, kind="answer")
            self.company._new_turn(conn, task)
            conn.execute("""UPDATE research_jobs SET state='completed',report=%s,error=NULL,
                notified_state='completed',updated_at=now() WHERE id=%s""",
                         (Jsonb({**published, "source_id": source_id, "director_task_id": str(task["id"])}), job["id"]))
            conn.execute("""UPDATE research_mission_stages SET state='completed',result=%s,error=NULL,updated_at=now()
                WHERE id=%s""", (Jsonb({"proposal": row["result"], "publication": publication.model_dump(mode="json"),
                                      "source_id": source_id}), row["id"]))
            self.company._event(conn, "research_mission_report_ready", {"mission_id": current["id"],
                "trial_id": live_trial["id"], "source_id": source_id, "html_sha256": published["html_sha256"],
                "director_task_id": str(task["id"])}, project["id"])
        return {"state": "completed", "trial_id": current_trial["id"], "source_id": source_id}

    def _meaning_review(self, audit_row, snapshot, verified):
        from .controller import MissionController
        from .program_contracts import MeaningReview

        current_trial = _trial(snapshot)
        meaning_snapshot = {**snapshot, "stage": {**snapshot["stage"], "stage": "meaning"}}
        controller = MissionController(self.company, backend=self)
        key = controller._key(meaning_snapshot)
        with self.company.db.transaction() as conn:
            project, _, _ = self._current_stage(conn, audit_row, snapshot)
            row = conn.execute("SELECT * FROM research_mission_stages WHERE mission_id=%s AND stage_key=%s FOR UPDATE",
                               (snapshot["id"], key)).fetchone()
            if row and row["state"] in {"received", "completed"}:
                value = MeaningReview.model_validate(row["result"])
                require_scope(MissionSpec.model_validate(snapshot["spec"]), value)
                result = next(o for o in snapshot["outcomes"] if o["id"] == current_trial["result_id"])
                if str(value.trial_id) != current_trial["id"] or value.outcome_digest != result["digest"]:
                    raise PolicyError("Meaning review is not bound to the validated outcome")
                obligations = {r["challenge_id"] for r in snapshot.get("challenge_responses", [])
                    if r["proposal_id"] == current_trial["proposal_id"] and r["payload"]["disposition"] == "test"}
                if len(value.tests) != len(obligations) or {str(t.challenge_id) for t in value.tests} != obligations:
                    raise PolicyError("Every registered test obligation requires independent disposition")
                for test in value.tests:
                    if any(path not in verified.package.scope_files for path in test.evidence_paths):
                        raise PolicyError("Test conclusion cites an artifact outside the validated package")
                payload = value.model_dump(mode="json")
                conn.execute("""INSERT INTO research_meaning_reviews(trial_id,payload,digest)
                    VALUES(%s,%s,%s) ON CONFLICT DO NOTHING""", (value.trial_id, Jsonb(payload), fingerprint(payload)))
                conn.execute("UPDATE research_mission_stages SET state='completed' WHERE id=%s", (row["id"],))
                return payload
            mappings = dict(audit_row["context"]["_private_files"])
            directory = self.root / "missions" / snapshot["id"] / "meaning"
            mappings["review/feedback.json"] = self._entry(self._blob(directory, "feedback", {
                "challenge_responses": snapshot.get("challenge_responses", []),
                "interpretations": snapshot["interpretations"], "outcomes": snapshot["outcomes"],
                "validity": verified.public_receipt(),
            }))
            extra = {"mission": self._compact(meaning_snapshot), "_private_files": mappings,
                "required_meaning_reads": [*audit_row["context"]["_audit"]["required_reads"], "review/feedback.json"],
                "available_files": [{"name": name, "sha256": item["sha256"], "size": item["size"]}
                                    for name, item in sorted(mappings.items())]}
            controller._schedule(conn, project, meaning_snapshot, extra)
        return None

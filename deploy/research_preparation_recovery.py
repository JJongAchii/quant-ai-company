"""Operator-only recovery of a proven P11 company-commit precheck failure.

No public endpoint or worker/model tool imports this helper. Keep polling stopped
until local preparation and the server transaction have both completed. An
unfinished local journal is a blocker, never an automatic retry instruction.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import Field, TypeAdapter

from quant_company.company import Company, fingerprint
from quant_company.config import Settings
from quant_company.contracts import StrictModel
from quant_company.research.contracts import Assignment, Commit, Digest, Recipe, WorkerUpdate
from quant_company.research.executor import verify_repository
from quant_company.research.recipes import load_recipe, recipe_digest
from quant_company.research.worker import (
    WorkerConfig,
    atomic_json,
    canonical_sha,
    exclusive_lock,
    process_identity,
    read_json,
    sha_file,
    sync_directory,
    utc_now,
)

ATTEMPT_FILES = {
    "execution-config.json", "result.json", "recipe.json", "state.json", "executor.log",
    "launch-intent.json", "execution.lock", "process.json",
}
PREPARED_FILES = {"state.json", "recipe.json", "execution-config.json", "launch-intent.json"}
LaunchId = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
Actor = Annotated[str, Field(pattern=r"^operator:[A-Za-z0-9_.@-]{1,80}$")]


class RecoveryBlocked(Exception):
    """Contains a stable code only; never include a private record in the message."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise RecoveryBlocked(code)


class RecoveryRequest(StrictModel):
    job_id: UUID
    project_id: UUID
    revision: int = Field(ge=1)
    recipe_id: Literal["kr-etf-p11-replay-v1"]
    manifest_digest: Digest
    approval_event_id: str = Field(pattern=r"^slack:.+:director$")
    approved_by: str = Field(pattern=r"^[UW][A-Z0-9]+$")
    sequence: int = Field(ge=1)
    failed_launch_id: LaunchId
    failed_company_commit: Commit
    company_commit: Commit
    failure_files: dict[str, Digest]


class FailureResult(StrictModel):
    state: Literal["failed"]
    reason: Literal["repository-commit-mismatch"]
    launch_id: LaunchId
    completed_at: datetime


class RecoveryProof(StrictModel):
    schema_version: Literal[1] = 1
    operation: Literal["company-commit-precheck-recovery"] = "company-commit-precheck-recovery"
    recovery_id: UUID
    request: RecoveryRequest
    actor: Actor
    recovery_tool_commit: Commit
    prepared_at: datetime
    new_launch_id: LaunchId
    history_path: str
    failure_digest: Digest
    prepared_files: dict[str, Digest]
    prepared_digest: Digest
    lease_sha256: Digest
    assignment_digest: Digest
    failed_update_digest: Digest
    failure: FailureResult
    worker_stopped: Literal[True] = True
    executor_and_group_absent: Literal[True] = True
    research_commands_absent: Literal[True] = True
    launch_attempt_failures: Literal[1] = 1
    economic_executions_before_recovery: Literal[0] = 0
    scientific_trials_added: Literal[0] = 0
    next_economic_execution_count: Literal[1] = 1
    execution_count_meaning: Literal[
        "execution_count=1 describes the sole economic replay; the preserved precheck launch ran no economics"
    ] = "execution_count=1 describes the sole economic replay; the preserved precheck launch ran no economics"


def proof_digest(proof: RecoveryProof) -> str:
    return canonical_sha(proof.model_dump(mode="json"))


def tree_files(root: Path) -> dict[str, str]:
    """Hash every file; symlinks and unknown filesystem object types are blockers."""
    require(root.is_dir() and not root.is_symlink(), "attempt-directory-invalid")
    result = {}
    for path in sorted(root.rglob("*")):
        mode = path.lstat().st_mode
        require(not stat.S_ISLNK(mode), "symlink-in-attempt")
        if stat.S_ISDIR(mode):
            # This specific precheck failure never creates a subdirectory.
            raise RecoveryBlocked("execution-trace-or-unknown-directory")
        require(stat.S_ISREG(mode), "nonregular-attempt-file")
        result[path.relative_to(root).as_posix()] = sha_file(path)
    return result


def require_dead_process(identity: dict) -> None:
    require(set(identity) == {"boot", "command", "pid", "pgid", "start"}, "process-identity-invalid")
    pid, pgid = identity["pid"], identity["pgid"]
    require(isinstance(pid, int) and pid > 1 and pid == pgid, "process-group-identity-invalid")
    require(process_identity(pid) is None, "executor-still-present-or-pid-reused")
    observed = subprocess.run(["ps", "-axo", "pid=,pgid="], capture_output=True, text=True, check=False)
    require(observed.returncode == 0, "process-group-unverifiable")
    try:
        members = [tuple(map(int, line.split())) for line in observed.stdout.splitlines() if line.strip()]
        require(all(len(item) == 2 for item in members), "process-group-unverifiable")
    except ValueError as exc:
        raise RecoveryBlocked("process-group-unverifiable") from exc
    require(all(p != pid and g != pgid for p, g in members), "executor-or-group-still-present")


def validate_failure(config: WorkerConfig, request: RecoveryRequest, job: Path) -> tuple[dict, dict, FailureResult]:
    require(set(request.failure_files) == ATTEMPT_FILES, "failure-manifest-file-set-invalid")
    observed = tree_files(job)
    require(set(observed) == ATTEMPT_FILES, "execution-trace-or-unknown-file")
    require(observed == request.failure_files, "failure-files-changed")
    require((job / "executor.log").stat().st_size == 0, "unexpected-executor-log")
    require((job / "execution.lock").stat().st_size == 0, "unexpected-execution-lock")
    state, intent = read_json(job / "state.json"), read_json(job / "launch-intent.json")
    require(set(state) == {"assignment", "sequence", "last_update", "last_update_at", "last_ack"},
            "local-state-not-terminal-acknowledged")
    assignment = Assignment.model_validate(state["assignment"])
    require(assignment.action in {"run", "reconcile"}, "cancelled-assignment")
    expected = request.model_dump(mode="json", include={
        "job_id", "project_id", "revision", "recipe_id", "manifest_digest", "approval_event_id",
    })
    require(all(assignment.model_dump(mode="json")[key] == value for key, value in expected.items()),
            "assignment-identity-mismatch")
    require(set(intent) == {"assignment", "launch_id", "created_at", "company_commit", "phase"},
            "launch-intent-invalid")
    require(intent["assignment"] == state["assignment"] and intent["phase"] == "spawning"
            and intent["launch_id"] == request.failed_launch_id
            and intent["company_commit"] == request.failed_company_commit, "launch-identity-mismatch")
    failure = FailureResult.model_validate(read_json(job / "result.json"))
    require(failure.launch_id == request.failed_launch_id, "failure-launch-mismatch")
    process = read_json(job / "process.json")
    require(set(process) == {"company_commit", "identity", "launch_id", "started_at"}, "process-receipt-invalid")
    require(process["company_commit"] == request.failed_company_commit
            and process["launch_id"] == request.failed_launch_id, "process-receipt-mismatch")
    require(datetime.fromisoformat(process["started_at"]) <= failure.completed_at, "failure-time-invalid")
    require_dead_process(process["identity"])
    old_config = WorkerConfig.model_validate(read_json(job / "execution-config.json"))
    require(old_config.company_commit == request.failed_company_commit
            and config.company_commit == request.company_commit
            and old_config.company_commit != config.company_commit, "company-pin-not-corrected")
    require(old_config.model_dump(exclude={"company_commit"}) == config.model_dump(exclude={"company_commit"}),
            "configuration-changed-outside-company-pin")
    verify_repository(config.company_repo, request.company_commit)
    recipe = Recipe.model_validate(read_json(job / "recipe.json"))
    require(recipe == load_recipe(request.recipe_id) and recipe_digest(recipe) == request.manifest_digest,
            "frozen-recipe-mismatch")
    require(platform.node() == recipe.hostname, "recovery-requires-approved-worker-host")
    update = WorkerUpdate.model_validate(state["last_update"])
    require(state["sequence"] == request.sequence == update.sequence
            and update.state == "failed" and update.reason == failure.reason
            and update.lease_token == assignment.lease_token, "failed-heartbeat-mismatch")
    ack = state["last_ack"]
    require(set(ack) == {"ok", "state", "duplicate"} and ack["ok"] is True and ack["state"] == "failed"
            and isinstance(ack["duplicate"], bool), "terminal-heartbeat-unacknowledged")
    return state, intent, failure


def sync_tree(root: Path) -> None:
    for path in root.iterdir():
        with path.open("rb") as stream:
            os.fsync(stream.fileno())
    sync_directory(root)


def prepare_local(
    config: WorkerConfig, request: RecoveryRequest, recovery_id: UUID, *, actor: str, tool_commit: str,
) -> dict:
    """Never spawn. A completed same-ID call returns its original proof only."""
    TypeAdapter(Actor).validate_python(actor)
    TypeAdapter(Commit).validate_python(tool_commit)
    require(not config.state_dir.is_symlink(), "state-directory-is-symlink")
    require(config.state_dir.is_dir(), "worker-state-missing")
    require(not (config.state_dir / "worker.lock").is_symlink(), "worker-lock-is-symlink")
    job = config.state_dir / "jobs" / str(request.job_id)
    require(not job.parent.is_symlink(), "jobs-directory-is-symlink")
    base = config.state_dir / "preparation-recoveries"
    require(not base.is_symlink(), "recovery-directory-is-symlink")
    with exclusive_lock(config.state_dir / "worker.lock"):
        base.mkdir(exist_ok=True, mode=0o700)
        recovery = base / str(recovery_id)
        if recovery.exists():
            require(not recovery.is_symlink(), "recovery-directory-is-symlink")
            journal_path, proof_path = recovery / "journal.json", recovery / "proof.json"
            require(journal_path.is_file() and not journal_path.is_symlink(), "incomplete-recovery-journal")
            journal = read_json(journal_path)
            require(journal.get("stage") == "complete" and proof_path.is_file() and not proof_path.is_symlink(),
                    "incomplete-recovery-journal")
            proof = RecoveryProof.model_validate(read_json(proof_path))
            require(proof.request == request and proof.actor == actor and proof.recovery_tool_commit == tool_commit,
                    "recovery-id-reused")
            require(proof_digest(proof) == journal.get("proof_digest"), "recovery-proof-changed")
            require(tree_files(config.state_dir / proof.history_path) == request.failure_files,
                    "failure-history-changed")
            return {"proof": proof.model_dump(mode="json"), "proof_digest": proof_digest(proof), "duplicate": True}
        for previous in base.iterdir():
            require(previous.is_dir() and not previous.is_symlink(), "unknown-recovery-entry")
            journal_path = previous / "journal.json"
            require(journal_path.is_file(), "incomplete-recovery-journal")
            require(read_json(journal_path).get("job_id") != str(request.job_id), "job-already-has-recovery")
        state, old_intent, failure = validate_failure(config, request, job)
        recovery.mkdir(mode=0o700)
        sync_directory(base)
        journal = {"job_id": str(request.job_id), "recovery_id": str(recovery_id), "stage": "staging"}
        atomic_json(recovery / "journal.json", journal)
        history_base = config.state_dir / "failure-history"
        require(not history_base.is_symlink(), "history-directory-is-symlink")
        history_base.mkdir(exist_ok=True, mode=0o700)
        history = history_base / f"{request.job_id}-{recovery_id}"
        require(not history.exists(), "failure-history-already-exists")
        shutil.copytree(job, history, copy_function=shutil.copy2)
        sync_tree(history)
        sync_directory(history_base)
        require(tree_files(history) == request.failure_files == tree_files(job), "failure-history-copy-mismatch")
        prepared = recovery / "prepared"
        prepared.mkdir(mode=0o700)
        new_launch_id = uuid4().hex
        atomic_json(prepared / "state.json", state)
        shutil.copyfile(job / "recipe.json", prepared / "recipe.json")
        (prepared / "recipe.json").chmod(0o600)
        atomic_json(prepared / "execution-config.json", config.model_dump(mode="json"))
        atomic_json(prepared / "launch-intent.json", {
            **old_intent, "launch_id": new_launch_id, "created_at": utc_now(),
            "company_commit": request.company_commit, "phase": "prepared",
        })
        sync_tree(prepared)
        prepared_files = tree_files(prepared)
        proof = RecoveryProof(
            recovery_id=recovery_id, request=request, actor=actor, recovery_tool_commit=tool_commit,
            prepared_at=utc_now(), new_launch_id=new_launch_id,
            history_path=history.relative_to(config.state_dir).as_posix(),
            failure_digest=canonical_sha(request.failure_files), prepared_files=prepared_files,
            prepared_digest=canonical_sha(prepared_files), failure=failure,
            lease_sha256=hashlib.sha256(state["assignment"]["lease_token"].encode()).hexdigest(),
            assignment_digest=canonical_sha(state["assignment"]),
            failed_update_digest=fingerprint({k: v for k, v in state["last_update"].items() if k != "lease_token"}),
        )
        journal.update(stage="installing", proof_digest=proof_digest(proof))
        atomic_json(recovery / "journal.json", journal)
        # Preserve the original directory itself as well as the independently
        # verified copy. A crash between renames leaves an incomplete journal and
        # polling must stay stopped; reinvocation cannot rearm another attempt.
        os.rename(job, recovery / "original")
        sync_directory(job.parent)
        sync_directory(recovery)
        os.rename(prepared, job)
        sync_directory(job.parent)
        sync_directory(recovery)
        require(tree_files(job) == prepared_files, "prepared-install-mismatch")
        journal["stage"] = "complete"
        atomic_json(recovery / "journal.json", journal)
        # Only a durably installed preparation may produce a server-consumable proof.
        atomic_json(recovery / "proof.json", proof.model_dump(mode="json"))
        return {"proof": proof.model_dump(mode="json"), "proof_digest": proof_digest(proof), "duplicate": False}


def validate_proof(proof: RecoveryProof, expected_digest: str, actor: str, tool_commit: str) -> None:
    request = proof.request
    require(proof_digest(proof) == expected_digest, "proof-digest-mismatch")
    require(proof.actor == actor and proof.recovery_tool_commit == tool_commit, "recovery-actor-or-tool-mismatch")
    require(set(request.failure_files) == ATTEMPT_FILES and set(proof.prepared_files) == PREPARED_FILES,
            "proof-file-set-invalid")
    require(proof.failure_digest == canonical_sha(request.failure_files)
            and proof.prepared_digest == canonical_sha(proof.prepared_files), "proof-file-digest-mismatch")
    require(request.failed_company_commit != request.company_commit
            and proof.failure.launch_id == request.failed_launch_id
            and proof.new_launch_id != request.failed_launch_id, "proof-attempt-identity-invalid")
    recipe = load_recipe(request.recipe_id)
    require(recipe_digest(recipe) == request.manifest_digest, "registered-recipe-changed")
    require(proof.history_path == f"failure-history/{request.job_id}-{proof.recovery_id}", "history-path-invalid")
    expected_update = {"sequence": request.sequence, "state": "failed", "reason": proof.failure.reason}
    require(proof.failed_update_digest == fingerprint(expected_update), "failed-update-proof-mismatch")


def resume_server(company: Company, proof: RecoveryProof, expected_digest: str, *, actor: str, tool_commit: str) -> dict:
    """Administrative DB transaction only. Never reset a lease or its sequence."""
    validate_proof(proof, expected_digest, actor, tool_commit)
    request = proof.request
    require(company.settings.company_research_enabled, "research-not-enabled")
    require(company.settings.company_code_commit == request.company_commit, "server-release-pin-mismatch")
    with company.db.transaction() as conn:
        # Reuse poll's assignment lock; within it keep the normal project→job order.
        conn.execute("SELECT pg_advisory_xact_lock(71350220)")
        project = company._project(conn, str(request.project_id))
        row = conn.execute("SELECT * FROM research_jobs WHERE id=%s AND project_id=%s FOR UPDATE",
                           (request.job_id, request.project_id)).fetchone()
        require(row is not None, "recovery-job-missing")
        prior = conn.execute("""SELECT detail FROM events WHERE kind='research_preparation_recovered'
            AND detail->>'recovery_id'=%s""", (str(proof.recovery_id),)).fetchall()
        if prior:
            require(len(prior) == 1 and prior[0]["detail"].get("proof_digest") == expected_digest
                    and prior[0]["detail"].get("job_id") == str(request.job_id), "recovery-id-reused")
            return {"ok": True, "state": row["state"], "duplicate": True, "recovery_id": str(proof.recovery_id)}
        require(project["status"] == "active" and project["revision"] == request.revision
                and project["owner_user"] == request.approved_by
                and request.approved_by in company.settings.slack_allowed_users, "project-revision-or-owner-changed")
        require(row["state"] == "failed" and row["error"] == "worker_error", "job-not-matching-precheck-failure")
        require(row["revision"] == request.revision and row["recipe_id"] == request.recipe_id
                and row["manifest_digest"] == request.manifest_digest
                and row["manifest"] == load_recipe(request.recipe_id).model_dump(mode="json"), "job-manifest-changed")
        require(row["approval_event_id"] == request.approval_event_id and row["approved_by"] == request.approved_by
                and row["approved_at"] is not None, "job-approval-changed")
        require(row["company_commit"] == request.company_commit, "job-company-pin-changed")
        require(row["worker_id"] == "worker" and bool(row["lease_token"])
                and hashlib.sha256(row["lease_token"].encode()).hexdigest() == proof.lease_sha256, "job-lease-changed")
        assignments = {canonical_sha(Assignment(
            job_id=request.job_id, project_id=request.project_id, revision=request.revision,
            recipe_id=request.recipe_id, manifest_digest=request.manifest_digest,
            approval_event_id=request.approval_event_id, lease_token=row["lease_token"], action=action,
        ).model_dump(mode="json")) for action in ("run", "reconcile")}
        require(proof.assignment_digest in assignments, "job-assignment-proof-mismatch")
        require(row["sequence"] == request.sequence and row["update_digest"] == proof.failed_update_digest,
                "job-sequence-or-failure-changed")
        require(not any(row[key] is not None for key in ("artifact_path", "artifact_sha256", "report")),
                "job-has-economic-artifact")
        busy = conn.execute("""SELECT id FROM research_jobs WHERE worker_id='worker'
            AND state IN ('claimed','running','cancel_requested','uncertain')""").fetchone()
        require(busy is None, "worker-has-another-unresolved-job")
        previous = conn.execute("""SELECT id FROM events WHERE kind='research_preparation_recovered'
            AND detail->>'job_id'=%s""", (str(request.job_id),)).fetchone()
        require(previous is None, "job-already-has-recovery")
        # The old error/sequence/update digest remain on the row until the normal
        # preparing heartbeat advances them. The failure event/history is append-only.
        conn.execute("UPDATE research_jobs SET state='claimed',notified_state=NULL,updated_at=now() WHERE id=%s",
                     (request.job_id,))
        company._event(conn, "research_preparation_recovered", {
            "recovery_id": str(proof.recovery_id), "job_id": str(request.job_id), "revision": request.revision,
            "manifest_digest": request.manifest_digest, "approval_event_id": request.approval_event_id,
            "old_launch_id": request.failed_launch_id, "new_launch_id": proof.new_launch_id,
            "failure_digest": proof.failure_digest, "proof_digest": expected_digest,
            "lease_sha256": proof.lease_sha256, "preserved_sequence": request.sequence,
            "failure_reason": proof.failure.reason, "prior_error": row["error"],
            "old_company_commit": request.failed_company_commit, "company_commit": request.company_commit,
            "actor": actor, "recovery_tool_commit": tool_commit, "launch_attempt_failures": 1,
            "economic_executions_before_recovery": 0, "scientific_trials_added": 0,
            "execution_count_meaning": proof.execution_count_meaning,
        }, str(request.project_id))
        return {"ok": True, "state": "claimed", "duplicate": False, "recovery_id": str(proof.recovery_id)}


def tool_identity() -> str:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True)
    require(result.returncode == 0 and bool(re.fullmatch(r"[a-f0-9]{40}", result.stdout.strip())),
            "recovery-tool-commit-unavailable")
    commit = result.stdout.strip()
    verify_repository(root, commit)
    return commit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    local = actions.add_parser("prepare-local")
    local.add_argument("--config", required=True, type=Path)
    local.add_argument("--request", required=True, type=Path)
    local.add_argument("--recovery-id", required=True, type=UUID)
    local.add_argument("--actor", required=True)
    server = actions.add_parser("resume-server")
    server.add_argument("--proof", required=True, type=Path)
    server.add_argument("--proof-digest", required=True)
    server.add_argument("--actor", required=True)
    args = parser.parse_args()
    try:
        commit = tool_identity()
        if args.action == "prepare-local":
            result = prepare_local(
                WorkerConfig.from_file(args.config.absolute()),
                RecoveryRequest.model_validate_json(args.request.read_text()), args.recovery_id,
                actor=args.actor, tool_commit=commit,
            )
        else:
            result = resume_server(
                Company(Settings(), roles={}), RecoveryProof.model_validate_json(args.proof.read_text()),
                args.proof_digest, actor=args.actor, tool_commit=commit,
            )
        print(json.dumps(result, sort_keys=True, ensure_ascii=False))
        return 0
    except RecoveryBlocked as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    except Exception:
        # Validation/SQL/OS exceptions can embed lease/DSN values. Never print them.
        print(json.dumps({"ok": False, "error": "recovery-input-or-state-unavailable"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""Pull a fixed research assignment; preserve ambiguity instead of relaunching it.

The polling process owns transport only. A detached executor owns each execution and
its lock. A launch intent is durable before Popen; an intent without a trustworthy
child receipt is never permission to spawn again.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import ipaddress
import json
import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from pydantic import Field, field_validator

from ..contracts import StrictModel
from .contracts import Assignment, Commit, Recipe, WorkerPoll, WorkerUpdate

LOG = logging.getLogger(__name__)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def canonical_sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(path: Path, data: Any) -> None:
    """Replace one local record durably; credentials remain owner-readable only."""
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("local-record-not-object")
    return value


@contextlib.contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def process_identity(pid: int) -> dict[str, Any] | None:
    """PID, start identity, command and process group; never use kill(pid, 0) alone.

    Linux receipts bind to boot ID and /proc start ticks. The ps fallback allows
    the same lifecycle tests on macOS; real research remains restricted to Linux.
    """
    try:
        if sys.platform == "linux":
            root = Path("/proc") / str(pid)
            fields = (root / "stat").read_text().rsplit(")", 1)[1].split()
            if fields[0] in {"Z", "X"}:
                return None
            return {
                "pid": pid,
                "pgid": int(fields[2]),
                "start": fields[19],
                "boot": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                "command": hashlib.sha256((root / "cmdline").read_bytes()).hexdigest(),
            }
        output = subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "pid=,pgid=,lstart=,state=,command="],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip().split(None, 8)
        if len(output) != 9 or output[7].startswith("Z"):
            return None
        return {
            "pid": int(output[0]),
            "pgid": int(output[1]),
            "start": " ".join(output[2:7]),
            "boot": "ps-lstart",
            "command": hashlib.sha256(output[8].encode()).hexdigest(),
        }
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def identity_alive(identity: dict[str, Any]) -> bool:
    return process_identity(identity["pid"]) == identity


def signal_group(identity: dict[str, Any], signum: int) -> bool:
    if identity.get("pgid") != identity.get("pid") or not identity_alive(identity):
        return False
    try:
        os.killpg(identity["pgid"], signum)
    except (ProcessLookupError, PermissionError):
        return False
    return True


class WorkerConfig(StrictModel):
    api_url: str
    token_file: Path
    state_dir: Path
    repo_source: Path
    input_source: Path
    evidence_repo: Path
    evidence_objective: str = "docs/objective.md"
    research_python: Path
    company_repo: Path
    company_commit: Commit
    worker_id: Literal["worker"] = "worker"
    poll_seconds: float = Field(default=5, ge=0.1, le=60)
    heartbeat_seconds: float = Field(default=15, ge=0.1, le=300)
    cancel_grace_seconds: float = Field(default=15, ge=0, le=300)
    http_timeout_seconds: float = Field(default=20, gt=0, le=60)

    @field_validator("api_url")
    @classmethod
    def loopback_api(cls, value: str) -> str:
        parts = urlsplit(value)
        try:
            local = parts.hostname == "localhost" or ipaddress.ip_address(parts.hostname or "").is_loopback
        except ValueError:
            local = False
        if (
            parts.scheme != "http" or not local or parts.username or parts.password
            or parts.query or parts.fragment or parts.path not in {"", "/"}
        ):
            raise ValueError("worker-api-must-use-preconfigured-loopback-tunnel")
        return value.rstrip("/")

    @classmethod
    def from_file(cls, path: Path) -> WorkerConfig:
        value = cls.model_validate_json(path.read_text())
        for field in (
            "token_file", "state_dir", "repo_source", "input_source", "evidence_repo",
            "research_python", "company_repo",
        ):
            item = getattr(value, field)
            # Do not resolve the venv executable symlink: its path selects the venv.
            setattr(value, field, Path(os.path.abspath(path.parent / item)))
        return value


def registered_recipe(recipe_id: str) -> Recipe:
    from .recipes import load_recipe

    return load_recipe(recipe_id)


class Worker:
    def __init__(
        self,
        config: WorkerConfig,
        client: httpx.Client,
        *,
        recipe_loader: Callable[[str], Recipe] = registered_recipe,
    ) -> None:
        self.config = config
        self.client = client
        self.recipe_loader = recipe_loader
        config.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.jobs = config.state_dir / "jobs"
        self.jobs.mkdir(exist_ok=True, mode=0o700)
        sync_directory(config.state_dir)
        self.children: list[subprocess.Popen] = []

    def _save(self, directory: Path, state: dict) -> None:
        atomic_json(directory / "state.json", state)

    @staticmethod
    def _same_assignment(first: dict, second: Assignment) -> bool:
        return {k: v for k, v in first.items() if k != "action"} == second.model_dump(
            mode="json", exclude={"action"}
        )

    @staticmethod
    def _heartbeat_ack(response: httpx.Response) -> dict:
        response.raise_for_status()
        value = response.json()
        if (
            not isinstance(value, dict) or set(value) != {"ok", "state", "duplicate"}
            or value["ok"] is not True or not isinstance(value["duplicate"], bool)
            or value["state"] not in {
                "running", "cancel_requested", "cancelled", "received", "completed",
                "failed", "awaiting_audit", "uncertain",
            }
        ):
            raise ValueError("invalid-heartbeat-acknowledgement")
        return value

    def _send_update(self, directory: Path, state: dict, status: str, reason: str = "") -> dict:
        path = f"/v1/research/worker/jobs/{state['assignment']['job_id']}/heartbeat"
        # Never replace an unacknowledged sequence with a different body.
        if state.get("pending_update"):
            state["last_ack"] = self._heartbeat_ack(self.client.post(path, json=state["pending_update"]))
            state["last_update"] = state.pop("pending_update")
            state["last_update_at"] = time.time()
            self._save(directory, state)
        previous = state.get("last_update", {})
        if previous.get("state") == status and previous.get("reason") == reason:
            if status != "running" or time.time() - state["last_update_at"] < self.config.heartbeat_seconds:
                return state["last_ack"]
        state["sequence"] += 1
        update = WorkerUpdate(
            lease_token=state["assignment"]["lease_token"],
            sequence=state["sequence"], state=status, reason=reason,
        )
        state["pending_update"] = update.model_dump(mode="json")
        self._save(directory, state)
        state["last_ack"] = self._heartbeat_ack(self.client.post(path, json=state["pending_update"]))
        state["last_update"] = state.pop("pending_update")
        state["last_update_at"] = time.time()
        self._save(directory, state)
        return state["last_ack"]

    def _stop(self, directory: Path, reason: str) -> None:
        if not (directory / "stop.json").exists():
            atomic_json(directory / "stop.json", {"reason": reason, "requested_at": time.time()})

    def _uncertain(self, directory: Path, state: dict, reason: str) -> None:
        self._stop(directory, reason)
        state["uncertain_reason"] = reason
        self._save(directory, state)
        self._send_update(directory, state, "uncertain", reason)

    def _other_active(self, directory: Path) -> bool:
        for other in self.jobs.iterdir():
            if other == directory or not (other / "launch-intent.json").exists():
                continue
            # A prepared launch suppressed by the server never attempted a spawn.
            if (other / "state.json").exists() and read_json(other / "state.json").get("server_terminal"):
                continue
            # A missing terminal receipt is unresolved even if no PID exists.
            if not (other / "result.json").exists():
                return True
        return False

    def _launch(self, directory: Path, state: dict, recipe: Recipe) -> None:
        if self._other_active(directory):
            self._uncertain(directory, state, "other-job-unreconciled")
            return
        launch_id = uuid4().hex
        atomic_json(directory / "recipe.json", recipe.model_dump(mode="json"))
        atomic_json(directory / "execution-config.json", self.config.model_dump(mode="json"))
        atomic_json(directory / "launch-intent.json", {
            "launch_id": launch_id, "assignment": state["assignment"], "created_at": utc_now(),
            "company_commit": self.config.company_commit, "phase": "prepared",
        })
        self._resume_prepared_launch(directory, state)

    def _resume_prepared_launch(self, directory: Path, state: dict) -> None:
        intent = read_json(directory / "launch-intent.json")
        if intent.get("phase") != "prepared":
            return
        if self._other_active(directory):
            self._uncertain(directory, state, "other-job-unreconciled")
            return
        config = WorkerConfig.model_validate(read_json(directory / "execution-config.json"))
        if config.company_commit != self.config.company_commit:
            self._uncertain(directory, state, "company-commit-changed-before-launch")
            return
        # The server must durably acknowledge this exact pending heartbeat before
        # any subprocess launch is attempted. Prepared is safe to resume; spawning
        # is never safe to repeat without a process/terminal receipt.
        acknowledgement = self._send_update(directory, state, "running", "preparing")
        if acknowledgement["state"] in {"cancel_requested", "cancelled"}:
            self._cancel(directory, state)
            return
        if acknowledgement["state"] in {"received", "completed", "failed", "awaiting_audit"}:
            self._stop(directory, "server-terminal-before-launch")
            state["server_terminal"] = acknowledgement["state"]
            self._save(directory, state)
            return
        if acknowledgement["state"] != "running":
            self._uncertain(directory, state, "server-did-not-authorize-launch")
            return
        intent["phase"] = "spawning"
        atomic_json(directory / "launch-intent.json", intent)
        launch_id = intent["launch_id"]
        command = [
            sys.executable, "-m", "quant_company.research.executor", "--job-dir", str(directory),
            "--launch-id", launch_id,
        ]
        fd = os.open(directory / "executor.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            with os.fdopen(fd, "ab") as log:
                process = subprocess.Popen(
                    command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    cwd=config.company_repo, start_new_session=True, close_fds=True,
                    env={**os.environ, "PYTHONPATH": str(config.company_repo / "src"),
                         "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"},
                )
            # Popen success is not execution evidence. This is only a short handoff
            # grace period for the first live polling process; restart cannot use it.
            self.children.append(process)
            deadline = time.monotonic() + 2
            while not (directory / "process.json").exists() and time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                time.sleep(0.02)
        except OSError:
            self._uncertain(directory, state, "launch-outcome-unknown")

    def _cancel(self, directory: Path, state: dict) -> None:
        self._stop(directory, "cancel-requested")
        intent = directory / "launch-intent.json"
        result_path = directory / "result.json"
        process_path = directory / "process.json"
        if (
            not intent.exists() or result_path.exists()
            or read_json(intent).get("phase") == "prepared"
        ):
            state["cancelled"] = True
            self._save(directory, state)
            self._send_update(directory, state, "cancelled", "cancel-requested")
            return
        if not process_path.exists():
            self._uncertain(directory, state, "cancel-launch-unresolved")
            return
        receipt = read_json(process_path)
        if receipt.get("launch_id") != read_json(intent)["launch_id"]:
            self._uncertain(directory, state, "process-receipt-mismatch")
            return
        identity = receipt["identity"]
        if not identity_alive(identity):
            # Without a terminal receipt a dead leader does not prove that every
            # descendant is gone. Never signal a reused PID/process group.
            self._uncertain(directory, state, "cancel-process-unresolved")
            return
        request = read_json(directory / "stop.json")
        elapsed = time.time() - request["requested_at"]
        signal_group(identity, signal.SIGKILL if elapsed >= self.config.cancel_grace_seconds else signal.SIGTERM)
        self._send_update(directory, state, "running", "cancellation-in-progress")

    def _upload(self, directory: Path, state: dict, result: dict) -> None:
        archive = directory / "artifact.zip"
        digest = sha_file(archive)
        if digest != result.get("artifact_sha256"):
            self._uncertain(directory, state, "local-artifact-digest-mismatch")
            return
        # The archive is frozen before this record; a lost response retries bytes.
        if not state.get("upload_sha256"):
            state["upload_sha256"] = digest
            self._save(directory, state)
        if state["upload_sha256"] != digest:
            self._uncertain(directory, state, "upload-identity-mismatch")
            return
        with archive.open("rb") as stream:
            reply = self.client.post(
                f"/v1/research/worker/jobs/{state['assignment']['job_id']}/artifact",
                content=stream,
                headers={"Content-Type": "application/zip", "X-Research-Lease": state["assignment"]["lease_token"],
                         "X-Artifact-Sha256": digest},
            )
        reply.raise_for_status()
        value = reply.json()
        if value.get("ok") is not True or value.get("state") not in {
            "received", "cancelled", "awaiting_audit", "completed",
        }:
            raise ValueError("unrecognized-artifact-acknowledgement")
        state["uploaded"] = True
        state["upload_ack"] = value
        self._save(directory, state)

    def reconcile(self, directory: Path, state: dict, *, cancel: bool = False) -> None:
        if cancel or state.get("cancelled"):
            self._cancel(directory, state)
            return
        if state.get("uploaded") or state.get("server_terminal"):
            return
        if state.get("uncertain_reason"):
            # An operator must resolve ambiguity. A later stale poll cannot rearm.
            self._send_update(directory, state, "uncertain", state["uncertain_reason"])
            return
        intent_path = directory / "launch-intent.json"
        if not intent_path.exists():
            self._uncertain(directory, state, "missing-launch-intent")
            return
        intent = read_json(intent_path)
        result_path = directory / "result.json"
        if result_path.exists():
            result = read_json(result_path)
            if result.get("launch_id") != intent["launch_id"]:
                self._uncertain(directory, state, "terminal-receipt-mismatch")
            elif result["state"] == "ready":
                self._upload(directory, state, result)
            else:
                self._send_update(directory, state, result["state"], result["reason"])
            return
        process_path = directory / "process.json"
        if not process_path.exists():
            self._uncertain(directory, state, "launch-without-process-receipt")
            return
        process = read_json(process_path)
        if process.get("launch_id") != intent["launch_id"] or not identity_alive(process["identity"]):
            self._uncertain(directory, state, "process-outcome-unknown")
            return
        self._send_update(directory, state, "running")

    def accept(self, assignment: Assignment) -> None:
        directory = self.jobs / str(assignment.job_id)
        directory.mkdir(exist_ok=True, mode=0o700)
        sync_directory(self.jobs)
        state_path = directory / "state.json"
        new = not state_path.exists()
        if new:
            state = {"assignment": assignment.model_dump(mode="json"), "sequence": 0}
            self._save(directory, state)
        else:
            state = read_json(state_path)
            if not self._same_assignment(state["assignment"], assignment):
                self._stop(directory, "assignment-identity-changed")
                process_path = directory / "process.json"
                if process_path.exists():
                    signal_group(read_json(process_path)["identity"], signal.SIGTERM)
                self._uncertain(directory, state, "assignment-identity-changed")
                return
        if assignment.action == "cancel":
            self.reconcile(directory, state, cancel=True)
            return
        if new and assignment.action != "run":
            self._uncertain(directory, state, "reconcile-without-local-state")
            return
        if new:
            if (directory / "launch-intent.json").exists():
                self._uncertain(directory, state, "launch-intent-without-local-state")
                return
            recipe = self.recipe_loader(assignment.recipe_id)
            if canonical_sha(recipe.model_dump(mode="json")) != assignment.manifest_digest:
                self._uncertain(directory, state, "recipe-manifest-mismatch")
                return
            self._launch(directory, state, recipe)
        elif not state.get("uncertain_reason") and not (directory / "stop.json").exists():
            intent_path = directory / "launch-intent.json"
            if intent_path.exists() and read_json(intent_path).get("phase") == "prepared":
                self._resume_prepared_launch(directory, state)
        self.reconcile(directory, state)

    def step(self) -> None:
        # Reap only our Popen handles, never confuse a disappeared daemon with an
        # executor failure. Detached executors survive polling-process restarts.
        self.children = [child for child in self.children if child.poll() is None]
        reply = self.client.post(
            "/v1/research/worker/poll", json=WorkerPoll(worker_id=self.config.worker_id).model_dump()
        )
        reply.raise_for_status()
        value = reply.json()["assignment"]
        active = Assignment.model_validate(value) if value else None
        if active:
            self.accept(active)
        for directory in sorted(self.jobs.iterdir()):
            if not directory.is_dir() or (active and directory.name == str(active.job_id)):
                continue
            state_path = directory / "state.json"
            if state_path.exists():
                state = read_json(state_path)
                if not state.get("uploaded"):
                    self.reconcile(directory, state)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--once", action="store_true", help="One transport/reconciliation iteration")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # HTTP client logs can include request metadata; our own messages are codes only.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        config = WorkerConfig.from_file(args.config.absolute())
        token = config.token_file.read_text().strip()
    except (OSError, ValueError):
        LOG.error("worker-configuration-unavailable")
        return 2
    if not token or any(c.isspace() for c in token):
        raise SystemExit("invalid-worker-token-file")
    config.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        with exclusive_lock(config.state_dir / "worker.lock"), httpx.Client(
            base_url=config.api_url, headers={"Authorization": f"Bearer {token}"},
            timeout=config.http_timeout_seconds, follow_redirects=False, trust_env=False,
        ) as client:
            worker = Worker(config, client)
            while True:
                try:
                    worker.step()
                except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError):
                    LOG.warning("worker-reconciliation-retry")
                    if args.once:
                        return 1
                if args.once:
                    return 0
                time.sleep(config.poll_seconds)
    except BlockingIOError:
        LOG.error("worker-already-running")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

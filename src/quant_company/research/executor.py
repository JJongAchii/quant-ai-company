"""Private, detached execution of the committed P11 equivalent-replay recipe.

There are no model-supplied commands, new configurations, date ranges, or automatic
retries here. Each launch gets a fresh checkout; the original inputs and audit
scope are only read. Non-performance qualification precedes each evaluation.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import signal
import stat
import subprocess
import sys
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from .contracts import Assignment, ExecutionReceipt, Recipe
from .worker import (
    WorkerConfig,
    atomic_json,
    canonical_sha,
    exclusive_lock,
    process_identity,
    read_json,
    registered_recipe,
    sha_file,
    sync_directory,
    utc_now,
)

PILOT = "labs/company-kr-etf-pilot"
DISCOVERY_ARGS = "docs/research/company-kr-etf-pilot/DISCOVERY-ARGS-v4-execution.json"
METHODS = ("m1", "m2", "m3")
ECONOMIC_FILES = tuple(
    f"{kind}-{cost}.{extension}"
    for cost in ("base", "stress")
    for kind, extension in (
        ("daily", "csv"), ("monthly", "csv"), ("orders", "json"),
        ("memberships", "json"), ("terminal-events", "json"),
    )
) + ("objective.json",)
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_EXPANDED_BYTES = 128 * 1024 * 1024
AUDIT_API = "qlab.audits.record.validate_audit+qlab.audits.receipt.check_receipt"


class ExecutionBlocked(Exception):
    """A stable public code, never raw command output or a performance value."""


def checked_path(root: Path, relative: str) -> Path:
    path = PurePosixPath(relative)
    if (
        not relative or path.is_absolute() or ".." in path.parts or "\\" in relative
        or path.as_posix() != relative or relative == "."
    ):
        raise ExecutionBlocked("invalid-registered-path")
    target = root / relative
    if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
        raise ExecutionBlocked("registered-path-escapes-root")
    return target


def verify_files(root: Path, expected: dict[str, str]) -> dict[str, str]:
    result = {}
    for name, digest in expected.items():
        path = checked_path(root, name)
        if not path.is_file() or sha_file(path) != digest:
            raise ExecutionBlocked("frozen-file-digest-mismatch")
        result[name] = digest
    return result


def git_value(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    if result.returncode:
        raise ExecutionBlocked("git-identity-unavailable")
    return result.stdout.strip()


def verify_repository(repo: Path, commit: str) -> None:
    if git_value(repo, "rev-parse", "HEAD") != commit:
        raise ExecutionBlocked("repository-commit-mismatch")
    if git_value(repo, "status", "--porcelain", "--untracked-files=all"):
        raise ExecutionBlocked("repository-not-clean")


def lab_environment(repo: Path) -> dict[str, str]:
    return {
        **os.environ,
        "PYTHONPATH": os.pathsep.join((str(repo / PILOT / "src"), str(repo / "core/src"))),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_OPTIONAL_LOCKS": "0",
    }


def run_checked(
    command: list[str], *, directory: Path, job: Path, label: str, env: dict[str, str] | None = None,
) -> None:
    if (job / "stop.json").exists():
        raise ExecutionBlocked("execution-stop-requested")
    log = job / f"{label}.log"
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        result = subprocess.run(command, cwd=directory, env=env, stdout=stream, stderr=subprocess.STDOUT)
        stream.flush()
        os.fsync(stream.fileno())
    # Commands and raw exit codes stay local; logs never enter the strict bundle.
    atomic_json(job / f"{label}.command.json", {"argv": command, "exit_code": result.returncode})
    if result.returncode:
        raise ExecutionBlocked(f"{label}-command-failed")
    if (job / "stop.json").exists():
        raise ExecutionBlocked("execution-stop-requested")


# This script invokes the existing audit and objective APIs from the exact
# provisioned evidence checkout. It returns their results, not a new judgment.
AUDIT_SCRIPT = r'''
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from qlab.audits.record import parse_audit, validate_audit
from qlab.audits.receipt import check_receipt, receipt_path
from qlab.control.record import objective_digest

root, recipe_path, objective_path, output = map(Path, sys.argv[1:])
recipe = json.loads(recipe_path.read_text())
path = root / recipe['audit_path']
record = parse_audit(path)
violations = validate_audit(record, root)
receipt_violations = check_receipt(record)
actual_scope = {}
for rel in record.scope:
    source = (root / record.target / rel).resolve()
    name = source.relative_to(root.resolve()).as_posix()
    actual_scope[name] = hashlib.sha256(source.read_bytes()).hexdigest()
actual_objective = objective_digest(root, str(objective_path))
if actual_objective != record.objective_digest:
    violations.append('audit-objective-content-mismatch')
result = {
    'schema_version': 1,
    'api': 'qlab.audits.record.validate_audit+qlab.audits.receipt.check_receipt',
    'evidence_commit': subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip(),
    'audit_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
    'audit_receipt_sha256': hashlib.sha256(receipt_path(path).read_bytes()).hexdigest(),
    'objective_digest': actual_objective,
    'scope_digest': record.scope_digest,
    'scope_files': actual_scope,
    'verdict': record.verdict,
    'violations': violations,
    'receipt_violations': receipt_violations,
}
output.write_text(json.dumps(result, sort_keys=True, ensure_ascii=False, allow_nan=False) + '\n')
'''


def verify_original_audit(config: WorkerConfig, recipe: Recipe, job: Path) -> Path:
    root = config.evidence_repo
    verify_repository(root, recipe.evidence_commit)
    verify_files(root, recipe.scope_files)
    audit_receipt = str(PurePosixPath(recipe.audit_path).with_suffix(".receipt.json"))
    verify_files(root, {recipe.audit_path: recipe.audit_sha256, audit_receipt: recipe.audit_receipt_sha256})
    checked_path(root, config.evidence_objective)
    output = job / "audit-verification.json"
    run_checked(
        [str(config.research_python), "-B", "-c", AUDIT_SCRIPT, str(root), str(job / "recipe.json"),
         config.evidence_objective, str(output)],
        directory=root, job=job, label="original-audit-verification", env=lab_environment(root),
    )
    value = read_json(output)
    expected = {
        "schema_version": 1, "api": AUDIT_API, "evidence_commit": recipe.evidence_commit,
        "audit_sha256": recipe.audit_sha256, "audit_receipt_sha256": recipe.audit_receipt_sha256,
        "objective_digest": recipe.objective_digest, "scope_digest": recipe.scope_digest,
        "scope_files": recipe.scope_files, "verdict": "pass", "violations": [], "receipt_violations": [],
    }
    if value != expected:
        raise ExecutionBlocked("original-audit-not-verified")
    # Recheck the immutable evidence around the independent API call.
    verify_files(root, recipe.scope_files)
    atomic_json(output, value)
    return output


def stage_checkout(config: WorkerConfig, recipe: Recipe, job: Path) -> Path:
    verify_repository(config.repo_source, recipe.code_commit)
    checkout = job / "checkout"
    if checkout.exists():
        raise ExecutionBlocked("execution-checkout-already-exists")
    run_checked(
        ["git", "clone", "--no-hardlinks", "--no-checkout", "--", str(config.repo_source), str(checkout)],
        directory=job, job=job, label="clone",
    )
    run_checked(
        ["git", "-c", "core.hooksPath=/dev/null", "checkout", "--detach", recipe.code_commit],
        directory=checkout, job=job, label="checkout",
    )
    verify_repository(checkout, recipe.code_commit)
    expected_inputs = {
        f"{PILOT}/data/snapshot-20260918/{window}/{filename}"
        for window in ("warmup", "dev") for filename in ("prices.parquet", "meta.parquet", "snapshot.json")
    }
    expected_configs = {f"{PILOT}/configs/{method}-liquid-11.json" for method in METHODS} | {DISCOVERY_ARGS}
    expected_outputs = {f"{method}/{name}" for method in METHODS for name in ECONOMIC_FILES}
    if (
        set(recipe.input_files) != expected_inputs or set(recipe.config_files) != expected_configs
        or set(recipe.expected_outputs) != expected_outputs
    ):
        raise ExecutionBlocked("recipe-outside-fixed-p11-envelope")
    verify_files(checkout, recipe.config_files)
    verify_files(config.input_source, recipe.input_files)
    for name, digest in recipe.input_files.items():
        destination = checked_path(checkout, name)
        if destination.exists():
            raise ExecutionBlocked("snapshot-destination-already-exists")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(checked_path(config.input_source, name), destination)
        if sha_file(destination) != digest:
            raise ExecutionBlocked("snapshot-copy-digest-mismatch")
        with destination.open("rb") as stream:
            os.fsync(stream.fileno())
        destination.chmod(0o444)
        sync_directory(destination.parent)
    verify_files(checkout, recipe.input_files)
    verify_repository(checkout, recipe.code_commit)
    return checkout


def verify_runtime(config: WorkerConfig, recipe: Recipe, checkout: Path, job: Path) -> None:
    code = (
        "import importlib.metadata as m,json,platform,sys; import qlab,company_kr_etf_pilot; "
        "print(json.dumps({'python':platform.python_version(),'executable':sys.executable,"
        "'qlab_path':qlab.__file__,'pilot_path':company_kr_etf_pilot.__file__,"
        "'distributions':dict(sorted((d.metadata['Name'],d.version) for d in m.distributions()))},sort_keys=True))"
    )
    run_checked(
        [str(config.research_python), "-B", "-c", code], directory=checkout, job=job,
        label="environment", env=lab_environment(checkout),
    )
    environment = read_json(job / "environment.log")
    if environment["python"] != "3.11.15":
        raise ExecutionBlocked("research-python-version-mismatch")
    if not Path(environment["qlab_path"]).resolve().is_relative_to(checkout / "core/src"):
        raise ExecutionBlocked("qlab-import-outside-exact-checkout")
    if not Path(environment["pilot_path"]).resolve().is_relative_to(checkout / PILOT / "src"):
        raise ExecutionBlocked("pilot-import-outside-exact-checkout")
    atomic_json(job / "environment.json", {
        **environment, "hostname": platform.node(), "gpu": recipe.gpu,
        "code_commit": recipe.code_commit, "company_commit": config.company_commit,
        "lake_id": recipe.lake_id,
    })


def verify_qualification(path: Path, recipe: Recipe, method: str) -> None:
    value = read_json(path)
    required = {
        "status": "passed", "performanceRead": False, "registeredPerformanceWindowRead": False,
        "sealedRead": False, "emptySampleRejected": True, "codeCommit": recipe.code_commit,
        "configDigest": recipe.config_files[f"{PILOT}/configs/{method}-liquid-11.json"],
        "executionMachine": "worker", "hostname": recipe.hostname, "gpu": recipe.gpu,
        "lakeId": recipe.lake_id, "python": "3.11.15", "dirtyPaths": [],
        "discoveryEnvelope": {"path": DISCOVERY_ARGS, "sha256": recipe.config_files[DISCOVERY_ARGS]},
    }
    if any(value.get(key) != expected for key, expected in required.items()):
        raise ExecutionBlocked("qualification-contract-mismatch")
    if value.get("qualifiedUniverseCount", 0) <= 0 or not value.get("jsonDates") or not value.get("typedSchema"):
        raise ExecutionBlocked("qualification-empty-typed-artifact")


def build_archive(
    job: Path, recipe: Recipe, receipt: ExecutionReceipt, output_root: Path, evidence_root: Path,
) -> str:
    members = {
        **{name: checked_path(output_root, name) for name in recipe.expected_outputs},
        "receipt.json": job / "receipt.json",
        "audit.md": checked_path(evidence_root, recipe.audit_path),
        "audit.receipt.json": checked_path(
            evidence_root, str(PurePosixPath(recipe.audit_path).with_suffix(".receipt.json"))
        ),
        "audit-verification.json": job / "audit-verification.json",
    }
    atomic_json(job / "receipt.json", receipt.model_dump(mode="json"))
    if sum(path.stat().st_size for path in members.values()) > MAX_EXPANDED_BYTES:
        raise ExecutionBlocked("artifact-expanded-size-limit")
    temporary = job / "artifact.zip.tmp"
    with zipfile.ZipFile(temporary, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, path in sorted(members.items()):
            if path.is_symlink() or not path.is_file():
                raise ExecutionBlocked("artifact-not-regular-file")
            # Fixed timestamps/modes make retry identity independent of filesystem metadata.
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o444) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    if temporary.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ExecutionBlocked("artifact-compressed-size-limit")
    with temporary.open("rb") as stream:
        os.fsync(stream.fileno())
    os.replace(temporary, job / "artifact.zip")
    sync_directory(job)
    return sha_file(job / "artifact.zip")


def execute_replay(
    config: WorkerConfig, assignment: Assignment, recipe: Recipe, job: Path, started_at: str,
) -> str:
    if sys.platform != "linux" or platform.node() != recipe.hostname:
        raise ExecutionBlocked("execution-requires-approved-3070-host")
    gpu_command = shutil.which("nvidia-smi") or "/usr/lib/wsl/lib/nvidia-smi"
    gpu = subprocess.run(
        [gpu_command, "--query-gpu=name", "--format=csv,noheader"], capture_output=True, text=True,
    )
    if gpu.returncode or gpu.stdout.strip() != recipe.gpu:
        raise ExecutionBlocked("execution-gpu-mismatch")
    if not Path(__file__).resolve().is_relative_to(config.company_repo / "src"):
        raise ExecutionBlocked("company-import-outside-exact-checkout")
    verify_repository(config.company_repo, config.company_commit)
    if registered_recipe(recipe.id) != recipe:
        raise ExecutionBlocked("committed-recipe-mismatch")
    # Missing original evidence blocks before any replay command is launched.
    verify_original_audit(config, recipe, job)
    checkout = stage_checkout(config, recipe, job)
    verify_runtime(config, recipe, checkout, job)
    outputs = job / "economic-outputs"
    outputs.mkdir()
    for method in METHODS:
        config_rel = f"{PILOT}/configs/{method}-liquid-11.json"
        for action, prefix in (("qualify", "q"), ("evaluate", "eval")):
            output_rel = f"{PILOT}/output/{prefix}-{method}-liquid-11"
            run_checked(
                [str(config.research_python), "-B", f"{PILOT}/run.py", action, "--config", config_rel,
                 "--output", output_rel, "--discovery-args", DISCOVERY_ARGS],
                directory=checkout, job=job, label=f"{method}-{action}", env=lab_environment(checkout),
            )
            if action == "qualify":
                verify_qualification(checkout / output_rel / "qualification.json", recipe, method)
        target = outputs / method
        target.mkdir()
        for name in ECONOMIC_FILES:
            source = checkout / PILOT / f"output/eval-{method}-liquid-11" / name
            if source.is_symlink() or not source.is_file():
                raise ExecutionBlocked("economic-output-missing")
            shutil.copyfile(source, target / name)
    # Hash bytes only; the builder/worker does not inspect performance to tune code.
    output_files = verify_files(outputs, recipe.expected_outputs)
    verify_files(checkout, recipe.input_files)
    verify_files(checkout, recipe.config_files)
    verify_files(config.input_source, recipe.input_files)
    verify_files(config.evidence_repo, recipe.scope_files)
    verify_repository(checkout, recipe.code_commit)
    receipt = ExecutionReceipt(
        **assignment.model_dump(exclude={"action", "lease_token"}),
        worker_id="worker", hostname=recipe.hostname, gpu=recipe.gpu,
        code_commit=recipe.code_commit, company_commit=config.company_commit,
        input_files=recipe.input_files, config_files=recipe.config_files, output_files=output_files,
        started_at=started_at, completed_at=utc_now(), execution_count=1,
        qualification_passed=True, sealed_read=False, scientific_trials_added=0,
    )
    return build_archive(job, recipe, receipt, outputs, config.evidence_repo)


def execute_child(
    job: Path, launch_id: str,
    *, operation: Callable[[WorkerConfig, Assignment, Recipe, Path, str], str] = execute_replay,
) -> int:
    """One lifetime of a detached child. Test callbacks exercise the same claim path."""
    cancelled = False

    def request_stop(signum: int, frame: object) -> None:
        nonlocal cancelled
        cancelled = True
        # Let subprocess.run reap the command after the whole group receives TERM.
        # The polling worker can KILL the verified group after its grace period.

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        with exclusive_lock(job / "execution.lock"):
            if (job / "process.json").exists() or (job / "result.json").exists():
                return 2
            intent = read_json(job / "launch-intent.json")
            assignment = Assignment.model_validate(intent["assignment"])
            recipe = Recipe.model_validate(read_json(job / "recipe.json"))
            config = WorkerConfig.model_validate(read_json(job / "execution-config.json"))
            if (
                launch_id != intent["launch_id"] or intent.get("phase") != "spawning"
                or assignment.manifest_digest != canonical_sha(
                    recipe.model_dump(mode="json")
                ) or config.company_commit != intent["company_commit"]
            ):
                return 3
            identity = process_identity(os.getpid())
            if identity is None or identity["pid"] != identity["pgid"]:
                return 4
            started_at = utc_now()
            atomic_json(job / "process.json", {
                "launch_id": launch_id, "identity": identity, "started_at": started_at,
                "company_commit": config.company_commit,
            })
            result = {"launch_id": launch_id, "completed_at": None}
            try:
                if cancelled or (job / "stop.json").exists():
                    result.update(state="cancelled", reason="cancel-requested")
                else:
                    digest = operation(config, assignment, recipe, job, started_at)
                    result.update(state="ready", artifact_sha256=digest)
            except ExecutionBlocked as exc:
                result.update(state="failed", reason=str(exc))
            except Exception:
                result.update(state="failed", reason="executor-operation-failed")
            if cancelled or (job / "stop.json").exists():
                result = {"launch_id": launch_id, "state": "cancelled", "reason": "cancel-requested"}
            result["completed_at"] = utc_now()
            atomic_json(job / "result.json", result)
            return 0 if result["state"] in {"ready", "cancelled"} else 1
    except BlockingIOError:
        return 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-dir", required=True, type=Path)
    parser.add_argument("--launch-id", required=True)
    args = parser.parse_args()
    try:
        return execute_child(args.job_dir.absolute(), args.launch_id)
    except (OSError, ValueError, KeyError):
        # No receipt means uncertain to the poller. Never reconstruct an intent.
        print("executor-local-state-unavailable", file=sys.stderr)
        return 5


if __name__ == "__main__":
    raise SystemExit(main())

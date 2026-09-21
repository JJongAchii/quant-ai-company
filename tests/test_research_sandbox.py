"""Fixture policy/launcher tests; opt-in Linux qualification never runs economics.

3070 procedure (committed checkout): set RESEARCH_SANDBOX_SMOKE_PROFILE to an
operator-owned JSON file with RuntimeProfile fields and pinned runtime mount
SHA-256 values (runtime_digest), then run the approved pytest -k qualification
command. Default Mac runs only build/validation fixtures and fake process tests.
"""

import json
import os
import platform
import signal
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from quant_company.research import sandbox
from quant_company.research.sandbox import (
    InputMount,
    RuntimeMount,
    RuntimeProfile,
    SandboxError,
    SandboxSpec,
    build_command,
    profile_from_dict,
    run_sandbox,
    runtime_digest,
)
from quant_company.research.workspace import content_digest, prepare_workspace

from .test_research_workspace import make_snapshot


@pytest.fixture
def spec(tmp_path):
    _, bundle, base = make_snapshot(tmp_path)
    prepared = prepare_workspace(bundle, content_digest(bundle.read_bytes()), base, tmp_path / "job", (), (), ())
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    executable = runtime / "python3"
    executable.write_bytes(b"Fixture runtime identity only; never executed.\n")
    executable.chmod(0o555)
    bwrap = tmp_path / "bwrap"
    bwrap.write_bytes(b"Fixture launcher identity only; never executed.\n")
    bwrap.chmod(0o555)
    profile = RuntimeProfile("synthetic-profile", "/runtime/python3", content_digest(executable.read_bytes()),
                             (RuntimeMount(runtime, "/runtime", runtime_digest(runtime, (runtime,))),),
                             (runtime,), bwrap)
    inputs = tmp_path / "approved-inputs"
    inputs.mkdir()
    data = inputs / "qualification.json"
    data.write_text('{"fixture":true}\n')
    output = tmp_path / "output"
    output.mkdir()
    return SandboxSpec(profile, prepared.worktree, prepared.commit, prepared.manifest["files"],
                       (InputMount(data, "sample.json", content_digest(data.read_bytes())),),
                       (inputs,), tmp_path, output, "src/entry.py", ("--fixture",), 20, True)


def test_command_uses_only_registered_files_private_namespaces_and_one_host_writable_mount(spec, monkeypatch):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-inherit")
    monkeypatch.setenv("PYTHONPATH", "/host/other-repo")
    command = build_command(spec)
    assert isinstance(command, tuple)
    assert "--unshare-all" in command and "--die-with-parent" in command and "--new-session" in command
    assert "--clearenv" in command and command[command.index("--cap-drop") + 1] == "ALL"
    assert command[command.index("--tmpfs") + 1] == "/"
    assert command[command.index("--proc") + 1] == "/proc"
    assert command.count("--bind") == 1
    assert command[command.index("--bind") + 1:command.index("--bind") + 3] == (str(spec.output_dir), "/output")
    assert command.count("--ro-bind") == len(spec.code_files) + len(spec.input_mounts) + len(spec.profile.mounts)
    assert str(spec.code_root) not in command  # no whole worktree or .git bind
    assert not any("repository.git" in arg or "/.git" in arg for arg in command)
    assert "/host/other-repo" not in command and "must-not-inherit" not in command
    assert command[-7:] == ("--", "/runtime/python3", "-E", "-s", "-B", "/code/src/entry.py", "--fixture")
    assert not any(spec.output_dir.iterdir())


def test_qualification_and_evaluation_have_distinct_pinned_input_mounts(spec):
    evaluation = spec.allowed_input_roots[0] / "evaluation.json"
    evaluation.write_text('{"different":"approved-development-input"}\n')
    alternate = replace(spec, input_mounts=(InputMount(evaluation, "sample.json", content_digest(evaluation.read_bytes())),))
    qualification_command, evaluation_command = build_command(spec), build_command(alternate)
    assert str(spec.input_mounts[0].source) in qualification_command
    assert str(evaluation) not in qualification_command
    assert str(evaluation) in evaluation_command
    assert str(spec.input_mounts[0].source) not in evaluation_command
    assert sandbox._validate(spec)[1]["inputs"] != sandbox._validate(alternate)[1]["inputs"]


@pytest.mark.parametrize("updates", [
    {"code_commit": "main"}, {"code_commit": "0" * 40}, {"code_files": {}},
    {"entrypoint": "../escape.py"}, {"entrypoint": "/bin/sh"}, {"entrypoint": "-c"},
    {"entrypoint": "src/missing.py"}, {"timeout_seconds": 0}, {"timeout_seconds": -1},
    {"timeout_seconds": float("nan")}, {"timeout_seconds": float("inf")}, {"timeout_seconds": True},
    {"argv": ["not-a-tuple"]}, {"argv": ("null\0byte",)},
])
def test_launch_requires_committed_entrypoint_identity_and_finite_positive_timeout(spec, updates):
    with pytest.raises(SandboxError):
        build_command(replace(spec, **updates))


def test_mac_heavy_launch_and_nonregistered_host_are_blocked_without_fallback(spec, monkeypatch):
    monkeypatch.setattr(sandbox.platform, "system", lambda: "Darwin")
    with pytest.raises(SandboxError, match="linux-required"):
        build_command(replace(spec, fixture_only=False))
    with pytest.raises(SandboxError, match="linux-execution-required"):
        run_sandbox(spec)
    monkeypatch.setattr(sandbox.platform, "system", lambda: "Linux")
    monkeypatch.setattr(sandbox.platform, "node", lambda: "unregistered-machine")
    with pytest.raises(SandboxError, match="registered-worker-required"):
        build_command(replace(spec, fixture_only=False))


@pytest.mark.parametrize("target", ["../sealed.csv", "/home/credentials", "a//b", "a\\b", ".git/config"])
def test_input_destinations_cannot_escape_the_private_inputs_directory(spec, target):
    with pytest.raises(SandboxError, match="invalid-input-target"):
        build_command(replace(spec, input_mounts=(replace(spec.input_mounts[0], target=target),)))


def test_input_hash_change_or_directory_mount_is_rejected(spec):
    source = spec.input_mounts[0].source
    source.write_text("changed after approval")
    with pytest.raises(SandboxError, match="input-digest-mismatch"):
        build_command(spec)
    with pytest.raises(SandboxError, match="input-must-be-a-file"):
        build_command(replace(spec, input_mounts=(replace(spec.input_mounts[0], source=source.parent),)))


def test_missing_overbroad_and_unapproved_roots_do_not_expose_host_home_or_sealed_data(spec, tmp_path):
    sealed = tmp_path / "sealed"
    sealed.mkdir()
    secret = sealed / "forward.json"
    secret.write_text("SEALED CONTENT MUST NOT BE MOUNTED")
    mount = InputMount(secret, "sample.json", content_digest(secret.read_bytes()))
    with pytest.raises(SandboxError, match="mount-outside-approved-root"):
        build_command(replace(spec, input_mounts=(mount,)))
    with pytest.raises(SandboxError, match="overbroad-mount-root"):
        build_command(replace(spec, allowed_input_roots=(Path("/"),)))
    with pytest.raises(SandboxError, match="overbroad-mount-root"):
        build_command(replace(spec, allowed_input_roots=(Path.home(),)))
    with pytest.raises(SandboxError, match="unsafe-mount-source"):
        build_command(replace(spec, input_mounts=(replace(mount, source=sealed / "missing"),)))


def test_input_symlinks_and_symlinked_parents_cannot_cross_approved_roots(spec, tmp_path):
    linked = spec.allowed_input_roots[0] / "linked.json"
    linked.symlink_to(spec.input_mounts[0].source)
    with pytest.raises(SandboxError, match="unsafe-mount-source"):
        build_command(replace(spec, input_mounts=(replace(spec.input_mounts[0], source=linked),)))
    linked_parent = tmp_path / "linked-input-directory"
    linked_parent.symlink_to(spec.allowed_input_roots[0], target_is_directory=True)
    with pytest.raises(SandboxError, match="unsafe-mount-source"):
        build_command(replace(spec, input_mounts=(replace(spec.input_mounts[0], source=linked_parent / "qualification.json"),)))


def test_mount_overlap_and_writable_aliases_are_rejected(spec):
    mount = spec.input_mounts[0]
    with pytest.raises(SandboxError, match="overlapping-input-mount"):
        build_command(replace(spec, input_mounts=(mount, replace(mount, target="sample.json/nested"))))
    writable_input = spec.output_dir / "input.json"
    writable_input.write_text("mutable")
    bad_mount = InputMount(writable_input, "sample.json", content_digest(writable_input.read_bytes()))
    with pytest.raises(SandboxError, match="writable-readonly-alias"):
        build_command(replace(spec, input_mounts=(bad_mount,), allowed_input_roots=(spec.allowed_job_root,)))
    with pytest.raises(SandboxError, match="invalid-job-mounts"):
        build_command(replace(spec, output_dir=spec.code_root))


@pytest.mark.parametrize("target", ["/", "/proc", "/home/host", "/inputs", "/output/runtime", "/code"])
def test_runtime_cannot_overmount_private_or_host_sensitive_paths(spec, target):
    mount = replace(spec.profile.mounts[0], target=target)
    with pytest.raises(SandboxError):
        build_command(replace(spec, profile=replace(spec.profile, mounts=(mount,))))


def test_runtime_identity_and_python_identity_are_checked(spec, tmp_path):
    with pytest.raises(SandboxError, match="runtime-digest-mismatch"):
        bad_profile = replace(spec.profile, mounts=(replace(spec.profile.mounts[0], sha256="0" * 64),))
        build_command(replace(spec, profile=bad_profile))
    with pytest.raises(SandboxError, match="python-digest-mismatch"):
        build_command(replace(spec, profile=replace(spec.profile, python_sha256="0" * 64)))
    with pytest.raises(SandboxError, match="python-outside-runtime"):
        build_command(replace(spec, profile=replace(spec.profile, python_executable="/bin/python3")))
    escaped = spec.profile.mounts[0].source / "escape"
    escaped.symlink_to(tmp_path)
    with pytest.raises(SandboxError, match="runtime-symlink-escape"):
        build_command(spec)


def test_runtime_hash_pins_files_modes_and_internal_symlink_targets(spec):
    root = spec.profile.mounts[0].source
    linked = root / "python-link"
    linked.symlink_to("python3")
    first = runtime_digest(root, (root,))
    assert first == runtime_digest(root, (root,))
    (root / "python3").chmod(0o500)
    assert runtime_digest(root, (root,)) != first
    linked.unlink()
    assert runtime_digest(root, (root,)) != first


def test_actual_launcher_clears_environment_uses_no_shell_and_returns_raw_exit(spec, monkeypatch):
    validated = sandbox._validate(spec)
    seen = {}

    class Process:
        pid = 12345

        def wait(self, timeout=None):
            seen["timeout"] = timeout
            return 17

    def popen(command, **kwargs):
        seen["argv"] = command
        seen["kwargs"] = kwargs
        kwargs["stdout"].write(b"fixture stdout\n")
        kwargs["stderr"].write(b"fixture stderr\n")
        return Process()

    monkeypatch.setattr(sandbox, "_validate", lambda requested: validated)
    monkeypatch.setattr(sandbox.platform, "system", lambda: "Linux")
    monkeypatch.setattr(sandbox.subprocess, "Popen", popen)
    receipt = run_sandbox(spec)
    assert seen["kwargs"]["env"] == {} and seen["kwargs"]["shell"] is False
    assert seen["kwargs"]["close_fds"] and seen["kwargs"]["start_new_session"]
    assert seen["kwargs"]["stdin"] == subprocess.DEVNULL
    assert seen["timeout"] == spec.timeout_seconds
    assert receipt.exit_code == 17 and receipt.timed_out is False
    assert receipt.code_commit == spec.code_commit and receipt.profile_id == spec.profile.profile_id
    assert len(receipt.spec_digest) == 64
    assert receipt.stdout_path.read_bytes() == b"fixture stdout\n"
    assert receipt.stderr_path.read_bytes() == b"fixture stderr\n"
    json.dumps(receipt.to_dict(), allow_nan=False)
    with pytest.raises(SandboxError, match="output-directory-not-empty"):
        run_sandbox(spec)


def test_timeout_kills_namespace_launcher_process_group_and_records_actual_exit(spec, monkeypatch):
    validated = sandbox._validate(spec)
    killed = []

    class Process:
        pid = 45678

        def wait(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired("fixture", timeout)
            return -signal.SIGKILL

    monkeypatch.setattr(sandbox, "_validate", lambda requested: validated)
    monkeypatch.setattr(sandbox.platform, "system", lambda: "Linux")
    monkeypatch.setattr(sandbox.subprocess, "Popen", lambda *args, **kwargs: Process())
    monkeypatch.setattr(sandbox.os, "killpg", lambda pid, number: killed.append((pid, number)))
    receipt = run_sandbox(spec)
    assert killed == [(45678, signal.SIGKILL)]
    assert receipt.timed_out is True and receipt.exit_code == -signal.SIGKILL


def test_operator_profile_rejects_unknown_fields(spec):
    profile = spec.profile
    value = {"profile_id": profile.profile_id, "python_executable": profile.python_executable,
             "python_sha256": profile.python_sha256, "allowed_roots": list(map(str, profile.allowed_roots)),
             "bwrap_executable": str(profile.bwrap_executable),
             "mounts": [{"source": str(m.source), "target": m.target, "sha256": m.sha256} for m in profile.mounts]}
    assert profile_from_dict(value) == profile
    with pytest.raises(SandboxError, match="invalid-runtime-profile"):
        profile_from_dict({**value, "environment": {"CREDENTIAL": "forbidden"}})


@pytest.mark.skipif(platform.system() != "Linux" or not os.environ.get("RESEARCH_SANDBOX_SMOKE_PROFILE"),
                    reason="Requires explicit operator-reviewed runtime profile on the 3070; no Mac fallback")
def test_linux_namespace_nonperformance_qualification(tmp_path, monkeypatch):
    profile = profile_from_dict(json.loads(Path(os.environ["RESEARCH_SANDBOX_SMOKE_PROFILE"]).read_text()))
    hidden = tmp_path / "host-secret-canary"
    hidden.write_text("synthetic credential canary")
    code = f'''import json, os, socket
from pathlib import Path
assert not Path({str(hidden)!r}).exists()
assert not Path('/code/.git').exists()
assert not Path('/proc/{os.getpid()}').exists()
assert 'AWS_SECRET_ACCESS_KEY' not in os.environ
assert 'OPERATOR_TOKEN' not in os.environ
assert set(name for _, name in socket.if_nameindex()) <= {{'lo'}}
assert json.loads(Path('/inputs/sample.json').read_text()) == {{'fixture': True}}
for filename in ['/code/src/entry.py', '/inputs/sample.json']:
    try:
        Path(filename).write_text('forbidden')
    except OSError:
        pass
    else:
        raise AssertionError('read-only mount was writable')
Path('/output/result.json').write_text(json.dumps({{'fixture': True, 'isolated': True}}))
print('non-performance namespace fixture complete')
'''
    _, bundle, base = make_snapshot(tmp_path, {"src/entry.py": code})
    prepared = prepare_workspace(bundle, content_digest(bundle.read_bytes()), base, tmp_path / "job", (), (), ())
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    data = inputs / "qualification.json"
    data.write_text('{"fixture":true}')
    output = tmp_path / "output"
    output.mkdir()
    spec = SandboxSpec(profile, prepared.worktree, prepared.commit, prepared.manifest["files"],
                       (InputMount(data, "sample.json", content_digest(data.read_bytes())),),
                       (inputs,), tmp_path, output, "src/entry.py", timeout_seconds=30, fixture_only=True)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "synthetic-do-not-inherit")
    monkeypatch.setenv("OPERATOR_TOKEN", "synthetic-do-not-inherit")
    receipt = run_sandbox(spec)
    assert receipt.exit_code == 0, receipt.stderr_path.read_text()
    assert receipt.timed_out is False
    assert json.loads((output / "result.json").read_text()) == {"fixture": True, "isolated": True}


@pytest.mark.parametrize("name", ["sealed/forward.json", ".ssh/id_rsa", ".env"])
def test_sensitive_input_cannot_hide_under_a_broad_registered_parent(spec, name):
    source = spec.allowed_input_roots[0] / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("not approved for research exposure")
    mount = InputMount(source, "sample.json", content_digest(source.read_bytes()))
    with pytest.raises(SandboxError, match="sensitive-input-path"):
        build_command(replace(spec, input_mounts=(mount,)))

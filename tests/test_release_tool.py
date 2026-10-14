import hashlib
import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("release_tool", Path(__file__).parents[1] / "deploy/release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

SHA, OLD = "a" * 40, "b" * 40
FILES = {
    "deploy/release.json": json.dumps({"schema_version": 1, "compose_files": ["compose.yaml"], "build": ["api"],
                                        "groups": [["api"], ["worker"]]}),
    "deploy/compose.yaml": "services: {}\n",
    "deploy/qdata-source.json": json.dumps({"commit": "q" * 40}),
    "pyproject.toml": "[project]\nname='x'\n",
    "uv.lock": "",
    "src/quant_company/__init__.py": "",
    "src/quant_company/company.py": "VALUE = 1\n",
}


def git_archive(files, commit=SHA, prefix="repo/"):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz", format=tarfile.PAX_FORMAT,
                      pax_headers={"comment": commit} if commit else None) as bundle:
        for path, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(prefix + path)
            info.size = len(data)
            bundle.addfile(info, io.BytesIO(data))
    return stream.getvalue()


@pytest.fixture
def host(tmp_path, monkeypatch):
    state, releases = tmp_path / "state", tmp_path / "opt/releases"
    (state / "config").mkdir(parents=True)
    (state / "config/runtime.env").write_text("RELEASE_COMMIT=old\nPINNED_CODEX_RUNTIME_IMAGE=codex:pinned\nKEEP=1\n")
    current = releases / OLD
    (current / "qdata/src").mkdir(parents=True)
    (current / "qdata/src/qdata.py").write_text("x = 1\n")
    (current / "deploy").mkdir()
    (current / "deploy/qdata-source.json").write_text(json.dumps({"commit": "q" * 40}))
    (releases.parent / "current").symlink_to(current)
    monkeypatch.setattr(release, "STATE", state)
    monkeypatch.setattr(release, "RELEASES", releases)
    return tmp_path


def prepared(host):
    archive = host / "release.tar.gz"
    archive.write_bytes(git_archive(FILES))
    release.prepare(SHA, archive)
    return release.RELEASES / SHA


def test_prepare_requires_the_requested_commit_and_copies_the_pinned_quant_data(host):
    archive = host / "release.tar.gz"
    archive.write_bytes(git_archive(FILES, commit=OLD))
    with pytest.raises(release.ReleaseError, match="requested commit"):
        release.prepare(SHA, archive)
    root = prepared(host)
    assert (root / "src/quant_company/company.py").read_text() == "VALUE = 1\n"
    assert (root / "qdata/src/qdata.py").is_file()
    receipt = json.loads((root / ".release.json").read_text())
    assert receipt["commit"] == SHA and receipt["qdata_commit"] == "q" * 40
    assert release.prepare(SHA, host / "release.tar.gz")["archive_sha256"] == receipt["archive_sha256"]
    archive.write_bytes(git_archive({**FILES, "src/quant_company/company.py": "VALUE = 2\n"}))
    with pytest.raises(release.ReleaseError, match="different content"):
        release.prepare(SHA, archive)


def test_prepare_rejects_unsafe_members_and_missing_required_files(host):
    archive = host / "release.tar.gz"
    archive.write_bytes(git_archive({**FILES, "../escape.py": "x"}))
    with pytest.raises(release.ReleaseError, match="unsafe"):
        release.prepare(SHA, archive)
    archive.write_bytes(git_archive({k: v for k, v in FILES.items() if k != "uv.lock"}))
    with pytest.raises(release.ReleaseError, match="uv.lock"):
        release.prepare(SHA, archive)


def test_release_variables_put_every_image_on_the_release_and_lift_the_old_pins(host):
    root = prepared(host)
    env = release.release_env(root, SHA)
    assert env["RELEASE_COMMIT"] == SHA and env["QDATA_BUILD_CONTEXT"] == str(root / "qdata")
    assert env["PINNED_COMPANY_WORKER_IMAGE"] == "quant-company-autonomous:" + SHA
    assert env["PINNED_CODEX_RUNTIME_IMAGE"] == ""
    args = release.compose_args(root)
    assert args[args.index("-f") + 1] == str(root / "deploy/compose.yaml") and "--profile" in args


def blob(text):
    data = text.encode()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


class FakeDocker:
    """Containers keyed by service; config hashes keyed by who renders (release or recorded files)."""

    def __init__(self, root, revision=SHA, wanted="new-hash", package=None, busy_turns=0):
        self.root, self.calls, self.busy_turns, self.calls_running = root, [], busy_turns, []
        self.revision, self.wanted = revision, wanted
        self.package = package if package is not None else {p.removeprefix("src/quant_company/"): blob(c)
                                                            for p, c in FILES.items() if p.startswith("src/")}
        self.containers = {name: self.container(OLD, "old-hash", "old-image") for name in ("api", "worker")}

    @staticmethod
    def container(revision, config, image):
        return {"Config": {"Image": image, "Labels": {
            "org.opencontainers.image.revision": revision, "com.docker.compose.config-hash": config,
            "com.docker.compose.project.config_files": "/old/compose.yaml,/old/overlay.json",
            "com.docker.compose.project.working_dir": "/old"}},
            "State": {"Running": True, "Health": {"Status": "healthy"}}, "RestartCount": 0}

    def __call__(self, args, env=None, input=None, check=True, timeout=None):
        self.calls.append((args, env))
        if args[:2] == ["docker", "inspect"]:
            service = args[2].removeprefix("quant-company-").removesuffix("-1")
            return json.dumps([self.containers[service]]) if service in self.containers else "[]"
        if args[:2] == ["docker", "top"]:
            return "PID COMMAND\n1 python uvicorn\n" + "".join(f"{n} {call}\n" for n, call in enumerate(self.calls_running))
        if args[:2] == ["docker", "exec"] and "psql" in args:
            return str(self.busy_turns) if "turns" in args[-1] else "0"
        if args[:2] == ["docker", "exec"]:
            return json.dumps(self.package) if "find_spec" in args[-1] else "0"
        if "--hash" in args:
            service = args[-1]
            return f"{service} {self.wanted if str(self.root) in ' '.join(args) else 'old-hash'}"
        if "up" in args:
            service = args[-1]
            if str(self.root) in " ".join(args):
                self.containers[service] = self.container(self.revision, self.wanted, "quant-company:" + SHA)
            else:
                self.containers[service] = self.container(OLD, "old-hash", "old-image")
            return ""
        raise AssertionError(args)


def test_apply_recreates_one_quiet_service_and_verifies_commit_config_and_package(host):
    root = prepared(host)
    docker = FakeDocker(root)
    record = release.apply(SHA, ["api"], runner=docker, sleep=lambda s: None)
    entry = record["services"][0]
    assert entry["result"] == "released" and entry["rollback_exact"]
    up = next((args, env) for args, env in docker.calls if "up" in args)
    assert str(root) in " ".join(up[0]) and up[1]["RELEASE_COMMIT"] == SHA
    assert any(path.name.endswith(SHA[:12] + ".json") for path in (release.STATE / "release-journal").iterdir())


@pytest.mark.parametrize("problem,kwargs", [
    ("image revision", {"revision": OLD}),
    ("package differs", {"package": {"__init__.py": blob(""), "company.py": blob("VALUE = 9\n")}}),
])
def test_apply_rolls_a_service_back_to_its_recorded_files_when_it_does_not_settle(host, problem, kwargs):
    root = prepared(host)
    docker = FakeDocker(root, **kwargs)
    with pytest.raises(release.ReleaseError, match=problem):
        release.apply(SHA, ["api"], runner=docker, sleep=lambda s: None)
    rollback = [args for args, env in docker.calls if "up" in args][-1]
    assert rollback[rollback.index("-f") + 1] == "/old/compose.yaml" and "/old/overlay.json" in rollback
    assert docker.containers["api"]["Config"]["Labels"]["com.docker.compose.config-hash"] == "old-hash"
    journal = next((release.STATE / "release-journal").iterdir())
    assert json.loads(journal.read_text())["services"][0]["rolled_back"] is True


def test_apply_waits_for_a_running_turn_and_skips_an_unchanged_service(host):
    root = prepared(host)
    docker = FakeDocker(root, busy_turns=1)
    with pytest.raises(release.ReleaseError, match="stayed busy: turn queued or running"):
        release.apply(SHA, ["worker"], quiet_seconds=0, runner=docker, sleep=lambda s: None)
    assert not any("up" in args for args, env in docker.calls)
    docker = FakeDocker(root)
    docker.containers["api"] = FakeDocker.container(SHA, "new-hash", "quant-company:" + SHA)
    assert release.apply(SHA, ["api"], runner=docker, sleep=lambda s: None)["services"][0]["result"] == "unchanged"


def test_apply_refuses_data_containers_and_unprepared_releases(host):
    with pytest.raises(release.ReleaseError, match="prepare"):
        release.apply(SHA, ["api"], runner=FakeDocker(host))
    prepared(host)
    with pytest.raises(release.ReleaseError, match="not releasable"):
        release.apply(SHA, ["postgres"], runner=FakeDocker(host))


def test_verify_reports_commit_config_and_package_drift(host):
    root = prepared(host)
    docker = FakeDocker(root)
    docker.containers["api"] = FakeDocker.container(SHA, "new-hash", "quant-company:" + SHA)
    rows = {row["service"]: row for row in release.verify(SHA, runner=docker)}
    assert rows["api"]["ok"]
    assert not rows["worker"]["ok"] and "revision" in rows["worker"]["problem"]


def test_activation_rewrites_only_release_pins(host, monkeypatch):
    root = prepared(host)
    monkeypatch.setattr(release, "verify", lambda sha, runner=None: [{"service": "api", "ok": True}])
    release.activate(SHA)
    text = (release.STATE / "config/runtime.env").read_text().splitlines()
    assert "RELEASE_COMMIT=" + SHA in text and "KEEP=1" in text
    assert "PINNED_COMPANY_WORKER_IMAGE=quant-company-autonomous:" + SHA in text
    assert not any(line.startswith("PINNED_CODEX_RUNTIME_IMAGE") for line in text)
    assert (release.RELEASES.parent / "current").resolve() == root.resolve()


def test_running_receipts_count_only_when_recent_and_in_the_lane(tmp_path):
    import os
    import subprocess
    import sys
    import time
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    for name, state, age in [("stale-crash.json", "running", 13 * 86400), ("turn-1.json", "running", 30),
                             ("turn-2.json", "complete", 30), ("news-1.json", "running", 30)]:
        path = jobs / name
        path.write_text(json.dumps({"state": state}))
        os.utime(path, (time.time() - age, time.time() - age))
    out = subprocess.run([sys.executable, "-c", release.RUNNING_JOBS, str(jobs), "1200", "news"],
                         capture_output=True, text=True, check=True).stdout
    assert out.strip() == "1"


def test_runtime_quietness_comes_from_its_own_live_cli_processes(host):
    root = prepared(host)
    docker = FakeDocker(root)
    assert release.busy("codex-runtime", runner=docker) == []
    docker.calls_running = ["node /usr/local/bin/codex exec --ignore-user-config --ignore-rules"]
    assert release.busy("codex-runtime", runner=docker) == ["model call running"]
    assert release.busy("claude-runtime", runner=docker) == []  # a codex call is not a Claude call
    docker.calls_running = ["/claude -p --safe-mode --model claude-opus-5-5"]
    assert release.busy("claude-runtime", runner=docker) == ["model call running"]


def test_services_may_follow_the_options(host, monkeypatch):
    seen = {}
    monkeypatch.setattr(release, "plan", lambda sha, services: seen.update(sha=sha, services=services) or [])
    assert release.main(["plan", "--sha", SHA, "api", "worker"]) == 0
    assert seen == {"sha": SHA, "services": ["api", "worker"]}

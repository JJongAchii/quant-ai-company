import hashlib
import importlib.util
import io
import json
import tarfile
from datetime import UTC, datetime
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
        self.root, self.calls, self.busy_turns, self.calls_running, self.windows = root, [], busy_turns, [], []
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
            hashed = self.wanted if str(self.root) in " ".join(args) else "old-hash"
            return "".join(f"{s} {hashed}\n" for s in (list(self.containers) if args[-1] == "*" else [args[-1]]))
        if "up" in args:
            service = args[-1]
            window = release.STATE / "ops/deploy-window.json"
            self.windows.append(json.loads(window.read_text())["services"] if window.exists() else None)
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
    assert docker.windows == [["api"]] and not (release.STATE / "ops/deploy-window.json").exists()
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
    assert docker.windows == [["api"], ["api"]] and not (release.STATE / "ops/deploy-window.json").exists()


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


# ------------------------------------------------------------------------------------------------- automatic release

IN_WINDOW = datetime(2026, 10, 14, 3, 0, tzinfo=UTC)  # 12:00 KST
OUT_OF_WINDOW = datetime(2026, 10, 14, 7, 0, tzinfo=UTC)  # 16:00 KST, PM edition collecting
RENDER = {"src/quant_company/video/__init__.py": "", "src/quant_company/video/remote.py": "from .motion import X\n",
          "src/quant_company/video/motion.py": "X = 1\n", "src/quant_company/video/runner.py": "STEP = 1\n"}


class FakeGitHub:
    def __init__(self, files, latest=SHA, status="ahead"):
        self.files, self.latest, self.status, self.downloads = files, latest, status, []

    def latest_green(self):
        return {"sha": self.latest, "run": "https://github.com/run/1", "completed_at": "2026-10-14T02:50:00Z"}

    def compare(self, base, head):
        return {"status": self.status, "ahead_by": 1}

    def tarball(self, sha, path):
        self.downloads.append(sha)
        path.write_bytes(git_archive(self.files, commit=sha))


@pytest.fixture
def auto_host(host, monkeypatch):
    """Production runs OLD (FILES plus a small renderer); verify, quiet and lock checks pass unless a test says so."""
    for path, content in {**FILES, **RENDER}.items():
        (release.RELEASES / OLD / path).parent.mkdir(parents=True, exist_ok=True)
        (release.RELEASES / OLD / path).write_text(content)
    monkeypatch.setattr(release, "verify", lambda sha, runner=None: [{"service": "api", "ok": True}])
    monkeypatch.setattr(release, "production_busy", lambda runner=None: [])
    monkeypatch.setattr(release, "host_free", lambda: True)
    steps = []
    monkeypatch.setattr(release, "build", lambda sha, runner=None: steps.append(("build", sha)))
    monkeypatch.setattr(release, "migrate", lambda sha, runner=None: steps.append(("migrate", sha)))
    monkeypatch.setattr(release, "activate", lambda sha, runner=None: steps.append(("activate", sha)))
    monkeypatch.setattr(release, "prune", lambda keep=3, protect=(), runner=None, unbuilt_only=False: [])

    def apply(sha, services, quiet_seconds=900, runner=None):
        steps.append(("apply", sha, list(services)))
        return {"services": [{"service": s, "result": "released"} for s in services]}

    monkeypatch.setattr(release, "apply", apply)
    return steps


def configure(**values):
    path = release.envfile()
    path.write_text(path.read_text() + "".join(f"{k}={v}\n" for k, v in values.items()))


def no_docker(args, env=None, input=None, check=True, timeout=None):
    return ""


def test_auto_only_reports_until_enabled_and_downloads_a_commit_once(auto_host):
    github = FakeGitHub({**FILES, **RENDER, "src/quant_company/company.py": "VALUE = 2\n"})
    status = release.auto(github, runner=no_docker, now=IN_WINDOW)
    assert status["mode"] == "report" and status["action"] == "would_release" and status["target"] == SHA
    assert auto_host == [] and github.downloads == [SHA]
    assert json.loads(release.status_file().read_text())["action"] == "would_release"
    assert release.auto(github, runner=no_docker, now=IN_WINDOW)["action"] == "would_release"
    assert github.downloads == [SHA]  # prepared once; later runs reuse it


@pytest.mark.parametrize("change,action", [
    ({"src/quant_company/video/motion.py": "X = 2\n"}, "render_required"),  # imported by the render task
    ({"deploy/Dockerfile.render": "FROM x\n"}, "render_required"),
    ({"src/quant_company/video/runner.py": "STEP = 2\n"}, "would_release"),  # video-worker only
    ({"deploy/release.json": json.dumps({"schema_version": 1, "compose_files": ["compose.yaml"], "build": ["api"],
                                         "groups": [["api"], ["worker"], ["dispatch"]]})}, "operator_required"),
])
def test_auto_holds_what_needs_a_new_render_image_or_an_operator(auto_host, change, action):
    status = release.auto(FakeGitHub({**FILES, **RENDER, **change}), runner=no_docker, now=IN_WINDOW)
    assert status["action"] == action
    if action == "render_required":
        assert status["render_files"] == list(change)
        configure(VIDEO_RENDER_ACCEPTED=SHA)  # the operator reviewed these files: they cannot change rendering
        status = release.auto(FakeGitHub({**FILES, **RENDER, **change}), runner=no_docker, now=IN_WINDOW)
        assert status["action"] == "would_release" and status["render"]["accepted"] == SHA


def test_auto_waits_for_window_quiet_and_a_clean_production(auto_host, monkeypatch):
    github = FakeGitHub({**FILES, **RENDER, "src/quant_company/company.py": "VALUE = 2\n"})
    assert release.auto(github, runner=no_docker, now=OUT_OF_WINDOW)["action"] == "waiting_for_window"
    monkeypatch.setattr(release, "production_busy", lambda runner=None: ["video job in production"])
    assert release.auto(github, runner=no_docker, now=IN_WINDOW)["detail"] == "video job in production"
    monkeypatch.setattr(release, "verify", lambda sha, runner=None: [{"service": "api", "ok": False, "problem": "x"}])
    assert release.auto(github, runner=no_docker, now=IN_WINDOW)["action"] == "held_for_drift"
    assert release.auto(FakeGitHub(FILES, latest=OLD), runner=no_docker, now=IN_WINDOW)["action"] == "drift"
    status = release.auto(FakeGitHub(FILES, status="diverged"), runner=no_docker, now=IN_WINDOW)
    assert status["action"] == "refused" and auto_host == []
    monkeypatch.setattr(release, "verify", lambda sha, runner=None: [{"service": "api", "ok": True}])
    status = release.auto(FakeGitHub(FILES, status="behind"), runner=no_docker, now=IN_WINDOW)
    assert status["action"] == "ahead_of_green" and status["target"] is None  # released before its CI finished


def test_a_main_that_changes_no_release_file_needs_no_release(auto_host):
    github = FakeGitHub({**FILES, **RENDER})  # identical release files: the new commits touched docs or tests only
    status = release.auto(github, runner=no_docker, now=IN_WINDOW)
    assert status["action"] == "up_to_date" and status["target"] is None and "changes no file" in status["detail"]
    configure(RELEASE_AUTO_ENABLED="true")
    assert release.auto(github, runner=no_docker, now=IN_WINDOW)["action"] == "up_to_date" and auto_host == []


def test_auto_reports_host_files_a_release_does_not_install(auto_host):
    github = FakeGitHub({**FILES, **RENDER, "deploy/quant-company-ops.timer": "[Timer]\n"})
    status = release.auto(github, runner=no_docker, now=IN_WINDOW)
    assert status["action"] == "would_release" and status["operator_files"] == ["deploy/quant-company-ops.timer"]


CHANGED = {**FILES, **RENDER, "src/quant_company/company.py": "VALUE = 2\n"}


def test_auto_release_runs_every_step_in_order(auto_host):
    configure(RELEASE_AUTO_ENABLED="true")
    status = release.auto(FakeGitHub(CHANGED), runner=no_docker, now=IN_WINDOW)
    assert status["action"] == "released" and status["current"] == SHA and status["target"] is None
    assert auto_host == [("build", SHA), ("migrate", SHA), ("apply", SHA, ["api"]), ("apply", SHA, ["worker"]),
                         ("activate", SHA)]
    assert status["released"]["services"] == ["api", "worker"] and status["step"] is None
    journal = (release.STATE / "release-journal/auto.jsonl").read_text().splitlines()
    assert [json.loads(line)["action"] for line in journal] == ["releasing", "released"]


def test_a_failed_release_returns_moved_services_and_is_not_retried(auto_host, monkeypatch):
    configure(RELEASE_AUTO_ENABLED="true")
    steps = auto_host

    def apply(sha, services, quiet_seconds=900, runner=None):
        steps.append(("apply", sha, list(services)))
        if sha == SHA:
            raise release.ReleaseError("worker: did not become running and healthy; rolled back (exact=True)")
        return {"services": []}

    monkeypatch.setattr(release, "apply", apply)
    monkeypatch.setattr(release, "moved", lambda sha, services, runner=None: ["api"])
    github = FakeGitHub(CHANGED)
    status = release.auto(github, runner=no_docker, now=IN_WINDOW)
    assert status["action"] == "failed" and status["failed_commit"] == SHA
    assert status["restored"] == {"services": ["api"], "ok": True}
    assert steps[-1] == ("apply", OLD, ["api"]) and ("activate", SHA) not in steps
    assert not release.progress_file().exists()
    assert release.auto(github, runner=no_docker, now=IN_WINDOW)["action"] == "held_after_failure"
    monkeypatch.setattr(release, "apply", lambda sha, services, quiet_seconds=900, runner=None: {"services": []})
    assert release.auto(github, runner=no_docker, now=IN_WINDOW, retry=True)["action"] == "released"


def test_an_incomplete_release_is_finished_at_any_hour_once_services_moved(auto_host, monkeypatch):
    configure(RELEASE_AUTO_ENABLED="true")

    def busy_apply(sha, services, quiet_seconds=900, runner=None):
        raise release.Busy("worker stayed busy: turn queued or running")

    monkeypatch.setattr(release, "apply", busy_apply)
    github = FakeGitHub(CHANGED)
    first = release.auto(github, runner=no_docker, now=IN_WINDOW)
    assert first["action"] == "incomplete" and first["target"] == SHA and first["failed_commit"] is None
    monkeypatch.setattr(release, "moved", lambda sha, services, runner=None: [])  # nothing moved: start over
    assert release.auto(github, runner=no_docker, now=OUT_OF_WINDOW)["action"] == "waiting_for_window"
    monkeypatch.setattr(release, "moved", lambda sha, services, runner=None: ["api"])
    configure(RELEASE_AUTO_ENABLED="false")  # paused: reported, production untouched, still owed
    assert release.auto(github, runner=no_docker, now=OUT_OF_WINDOW)["action"] == "would_resume"
    configure(RELEASE_AUTO_ENABLED="true")
    monkeypatch.setattr(release, "apply", lambda sha, services, quiet_seconds=900, runner=None: {"services": []})
    done = release.auto(github, runner=no_docker, now=OUT_OF_WINDOW)
    assert done["action"] == "released" and done["started_at"] == first["started_at"]
    assert not release.progress_file().exists()


def test_a_restore_that_could_not_finish_is_retried(auto_host, monkeypatch):
    configure(RELEASE_AUTO_ENABLED="true")
    release.write_json(release.progress_file(), {"phase": "restoring", "target": SHA, "previous": OLD,
                                                 "detail": "boom"})
    release.write_status({"action": "error", "failed_commit": SHA}, {})  # e.g. GitHub was down in between
    monkeypatch.setattr(release, "moved", lambda sha, services, runner=None: ["api"])
    status = release.auto(FakeGitHub(FILES), runner=no_docker, now=OUT_OF_WINDOW)
    assert status["action"] == "failed" and status["restored"] == {"services": ["api"], "ok": True}
    assert auto_host == [("apply", OLD, ["api"])] and not release.progress_file().exists()


def test_auto_records_errors_and_the_cli_exits_non_zero(auto_host, monkeypatch):
    class Down(FakeGitHub):
        def latest_green(self):
            raise release.ReleaseError("github_http_502")

    status = release.auto(Down(FILES), runner=no_docker, now=IN_WINDOW)
    assert status["action"] == "error" and "github_http_502" in status["detail"]
    monkeypatch.setattr(release, "auto", lambda retry=False: {"action": "error"})
    assert release.main(["auto"]) == 1


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def test_github_tokens_are_read_only_and_codeload_never_sees_one(tmp_path):
    import urllib.error
    config = tmp_path / "maintenance.json"
    config.write_text(json.dumps({"app_id": 1, "installation_id": 2, "repository": "owner/repo"}))
    granted, seen = {"contents": "read", "actions": "read", "metadata": "read"}, []

    def opener(request, timeout):
        seen.append((request.full_url, dict(request.header_items())))
        if request.full_url.endswith("/access_tokens"):
            assert json.loads(request.data)["permissions"] == {"contents": "read", "actions": "read"}
            return Response(json.dumps({"token": "installation-token", "permissions": granted}).encode())
        if "/tarball/" in request.full_url:
            raise urllib.error.HTTPError(request.full_url, 302, "Found",
                                         {"Location": "https://codeload.github.com/owner/repo/legacy.tar.gz/x"}, None)
        if request.full_url.startswith("https://codeload.github.com/"):
            return Response(b"archive")
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    github = release.GitHub(config, key=tmp_path / "key", opener=opener, signer=lambda unsigned: b"signature")
    github.tarball(SHA, tmp_path / "archive.tar.gz")
    assert (tmp_path / "archive.tar.gz").read_bytes() == b"archive"
    assert seen[1][1]["Authorization"] == "Bearer installation-token" and "Authorization" not in seen[2][1]
    with pytest.raises(release.ReleaseError, match="github_http_404"):
        github.compare(OLD, SHA)
    granted["contents"] = "write"
    with pytest.raises(release.ReleaseError, match="not_read_only"):
        release.GitHub(config, opener=opener, signer=lambda unsigned: b"signature").token()


def test_prepare_keeps_only_release_inputs_from_a_repository_tarball(host):
    archive = host / "repo.tar.gz"
    archive.write_bytes(git_archive({**FILES, "docs/adr/0001.md": "x", "tests/test_x.py": "x"}))
    root = release.RELEASES / SHA
    release.prepare(SHA, archive)
    assert (root / "src/quant_company/company.py").is_file()
    assert not (root / "docs").exists() and not (root / "tests").exists()


def test_render_inputs_follow_the_render_task_imports(tmp_path):
    for path, text in {"__init__.py": "", "contracts.py": "Y = 1\n", "video/__init__.py": "",
                       "video/remote.py": "from .motion import X\nfrom ..contracts import Y\nimport json\n"
                                          "import quant_company.video.render as render\n",
                       "video/motion.py": "X = 1\n", "video/render.py": "from . import frames\n",
                       "video/frames.py": "", "video/runner.py": "from .remote import X\n"}.items():
        (tmp_path / "src/quant_company" / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "src/quant_company" / path).write_text(text)
    found = {p.removeprefix("src/quant_company/") for p in release.imported(tmp_path, release.RENDER_ENTRY)}
    assert found == {"__init__.py", "contracts.py", "video/__init__.py", "video/remote.py", "video/motion.py",
                     "video/render.py", "video/frames.py"}


def test_windows_are_korean_time():
    assert release.in_window(IN_WINDOW, release.WINDOWS) and not release.in_window(OUT_OF_WINDOW, release.WINDOWS)
    assert release.in_window(datetime(2026, 10, 14, 11, 0, tzinfo=UTC), release.WINDOWS)  # 20:00 KST
    assert release.in_window(datetime(2026, 10, 14, 9, 30, tzinfo=UTC), release.WINDOWS)  # 18:30 KST, PM video queued


def test_prune_keeps_recent_current_running_and_protected_releases(host):
    names = [c * 40 for c in "0123456"]
    for index, name in enumerate(names):
        (release.RELEASES / name).mkdir()
        (release.RELEASES / name / ".release.json").write_text(json.dumps({"prepared_at": f"2026-10-1{index}"}))
    (release.RELEASES.parent / "current").unlink()
    (release.RELEASES.parent / "current").symlink_to(release.RELEASES / names[0])
    calls = []

    def runner(args, env=None, input=None, check=True, timeout=None):
        calls.append(args)
        if args[:2] == ["docker", "ps"]:
            return f"{release.RELEASES / names[1]}/deploy\n/opt/other/deploy\n"
        if args[:3] == ["docker", "image", "ls"]:
            tag = args[4].split(":")[1]
            return f"quant-company:{tag}\nquant-company-video:{tag}\n"
        return ""

    assert release.prune(keep=3, protect={names[2]}, runner=runner, unbuilt_only=True) == []  # all were built
    removed = release.prune(keep=3, protect={names[2]}, runner=runner)
    assert removed == [names[3]] and not (release.RELEASES / names[3]).exists()
    assert ["docker", "image", "rm", f"quant-company-video:{names[3]}"] in calls
    assert all((release.RELEASES / n).exists() for n in names if n != names[3])


def test_a_video_waiting_for_approval_does_not_hold_a_release():
    queries = []

    def runner(args, env=None, input=None, check=True, timeout=None):
        queries.append(args[-1])
        return "0"

    assert release.busy("video-worker", runner=runner) == []
    assert "NOT IN (" in queries[0] and "'awaiting_approval'" in queries[0] and "'approved'" not in queries[0]


def test_a_shorts_cut_in_production_holds_the_video_worker():
    def runner(args, env=None, input=None, check=True, timeout=None):
        return "1" if "video_shorts" in args[-1] else "0"

    assert release.busy("video-worker", runner=runner) == ["Shorts in production"]
    assert release.production_busy(runner=runner) == ["Shorts in production"]
    assert release.busy("video-worker", runner=lambda *a, **k: "") == []  # the table does not exist yet
    queries = []
    release.busy("video-worker", runner=lambda args, **k: queries.append(args[-1]) or "0")
    # A cut waiting 30 minutes for a valid version holds nothing in flight.
    assert any("video_shorts" in q and "no_valid_version" in q for q in queries)
    # Reviewed and inspected like the episode (2026-10-27): those states hold the worker too.
    shorts = next(q for q in queries if "video_shorts" in q)
    assert all(f"'{state}'" in shorts for state in ('writing', 'reviewing', 'synthesizing', 'rendering', 'inspecting'))


def test_an_email_being_sent_holds_dispatch():
    def runner(args, env=None, input=None, check=True, timeout=None):
        return "1" if "newsletter_sends" in args[-1] else "0"

    assert release.busy("dispatch", runner=runner) == ["email send in flight"]
    assert release.busy("dispatch", runner=lambda *a, **k: "") == []  # the table does not exist yet


def test_activation_refreshes_the_automatic_status_when_the_timer_is_installed(host, monkeypatch):
    prepared(host)
    monkeypatch.setattr(release, "verify", lambda sha, runner=None: [{"service": "api", "ok": True}])
    unit = host / "quant-company-release-auto.service"
    unit.write_text("[Service]\n")
    monkeypatch.setattr(release, "AUTO_UNIT", unit)
    calls = []
    release.activate(SHA, runner=lambda args, **kwargs: calls.append(args) or "")
    assert calls == [["systemctl", "start", "--no-block", "quant-company-release-auto.service"]]


def test_a_paused_check_only_reports_while_an_operator_works(auto_host, monkeypatch):
    configure(RELEASE_AUTO_ENABLED="paused")

    class Untouched(FakeGitHub):
        def latest_green(self):
            raise AssertionError("a paused check asks nothing")

    monkeypatch.setattr(release, "verify", lambda sha, runner=None: [{"service": "api", "ok": False, "problem": "x"}])
    status = release.auto(Untouched(FILES), runner=no_docker, now=IN_WINDOW)
    assert status["mode"] == "paused" and status["action"] == "paused" and auto_host == []


def test_a_check_during_a_backup_judges_nothing(auto_host, monkeypatch):
    # 2026-10-16 03:17 KST: the backup had stopped the worker, its package read failed and a drift alert went out.
    configure(RELEASE_AUTO_ENABLED="true")
    monkeypatch.setattr(release, "host_free", lambda: False)

    class Untouched(FakeGitHub):
        def latest_green(self):
            raise AssertionError("a check under the backup asks nothing")

    def stopped(sha, runner=None):
        raise AssertionError("services the backup stopped are not verified")

    monkeypatch.setattr(release, "verify", stopped)
    status = release.auto(Untouched(FILES), runner=no_docker, now=IN_WINDOW)
    assert status["action"] == "waiting_for_lock" and "drift" not in status and auto_host == []


def test_the_host_lock_waits_briefly_for_a_holder_that_lets_go(host):
    import fcntl
    holder = (release.STATE / ".backup.lock").open("a")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with pytest.raises(release.Busy):
        with release.host_lock(wait=0):
            pass
    with release.host_lock(wait=5, sleep=lambda s: holder.close()):  # lets go while we wait
        pass

"""Build and inspect exact inactive images without replacing any service."""

import fcntl
import hashlib
import json
import pathlib
import shutil
import subprocess
import tarfile
from datetime import UTC, datetime

SOURCE = "86aea8cdf03b5543666813c97689acb1e8158a6d"
OLD = "5c44ad07273adc780a56a47d7d35114fd0dc32c8"
ARCHIVE_SHA = "ea3ceb7604daf728ae73bbc7c98320f8c7f72251fee0b85d10d770121e170516"
STATE = pathlib.Path("/var/lib/quant-company")
ROOT = pathlib.Path("/opt/quant-company/releases") / SOURCE
RECORD = STATE / "releases" / ("scoped-data-stage-" + SOURCE + ".json")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(root):
    return {str(p.relative_to(root)): sha(p) for p in root.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts}


def run(args):
    return subprocess.check_output(args, text=True, timeout=60)


with (STATE / ".backup.lock").open("a") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    old = pathlib.Path("/opt/quant-company/current").resolve()
    assert old.name == OLD, "operating_release_changed"
    assert not RECORD.exists(), "read_existing_stage_receipt"
    assert shutil.disk_usage(ROOT.parent).free > 8 * 1024**3, "insufficient_stage_space"
    archive = pathlib.Path("/tmp/" + SOURCE + "-company.tar")
    assert sha(archive) == ARCHIVE_SHA
    before = {name: sha(STATE / "config" / name) for name in
              ("roles.json", "research-profiles.json", "research-qlab.json", "runtime.env")}
    resumed_archive = ROOT.exists()
    if not resumed_archive:
        ROOT.mkdir(mode=0o755)
    with tarfile.open(archive) as stream:
        members = stream.getmembers()
        for member in members:
            assert member.isfile() or member.isdir()
            assert not member.name.startswith("/") and ".." not in pathlib.PurePosixPath(member.name).parts
            target = ROOT / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                body = stream.extractfile(member).read()
                if target.exists():
                    assert not target.is_symlink() and target.read_bytes() == body, "partial_archive_bytes_changed"
                else:
                    target.write_bytes(body)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
        assert {str(p.relative_to(ROOT)) for p in ROOT.rglob('*') if p.is_file()} == {
            member.name for member in members if member.isfile()}, "unexpected_partial_release_files"
    qdata = json.loads((ROOT / "deploy/qdata-source.json").read_bytes())
    assert qdata == json.loads((old / "deploy/qdata-source.json").read_bytes())
    qdata_source = pathlib.Path("/opt/quant-company/releases/231d6ba0755f658f167636a8ea2c3ef65b6af562/qdata")
    qdata_tree = hashlib.sha256(json.dumps(inventory(qdata_source), sort_keys=True,
                                          separators=(",", ":")).encode()).hexdigest()
    assert qdata_tree == "528251801f09b120d9c33baeb6956a6a84e49b2c09aeb15420e5994fa170e396", "retained_qdata_bytes_changed"
    shutil.copytree(qdata_source, ROOT / "qdata")
    expected = inventory(ROOT / "src/quant_company")
    record = {"state": "staging_inactive", "source_commit": SOURCE, "previous": str(old),
              "archive_sha256": ARCHIVE_SHA, "qdata_commit": qdata["commit"],
              "config_sha256": before, "source_files": len(expected), "images": [],
              "services_replaced": 0, "scientific_trials_added": 0, "started_at": datetime.now(UTC).isoformat(),
              "qdata_tree_sha256": qdata_tree, "resumed_verified_archive": resumed_archive,
              "first_preparation_failure": "current_release_qdata_build_context_absent" if resumed_archive else None}

    def save():
        RECORD.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n")

    save()
    for target, prefix in (("app", "quant-company"), ("autonomous-research", "quant-company-autonomous"),
                           ("codex", "quant-company-codex")):
        image = prefix + ":" + SOURCE + "-scoped-data"
        log = STATE / "releases" / ("scoped-data-build-" + SOURCE + "-" + target + ".private.log")
        print(json.dumps({"phase": "build_inactive_target", "target": target}), flush=True)
        with log.open("wb") as stream:
            result = subprocess.run(["docker", "buildx", "build", "--builder", "default", "--load",
                "--progress", "plain", "--build-context", "qdata=" + str(ROOT / "qdata"),
                "--build-arg", "RELEASE_COMMIT=" + SOURCE, "--build-arg", "QDATA_COMMIT=" + qdata["commit"],
                "--target", target, "-f", str(ROOT / "deploy/Dockerfile"), "-t", image, str(ROOT)],
                stdout=stream, stderr=subprocess.STDOUT, timeout=900)
        assert result.returncode == 0, "inactive_build_failed_private_log_retained"
        probe = (
            "import hashlib,json,pathlib,os,subprocess,quant_company;"
            "from quant_company.contracts import ProviderRequest;"
            "from quant_company.providers.codex_runner import output_schema;"
            "r=ProviderRequest(request_id='12345678-1234-5678-1234-567812345678',model='gpt-5.6-terra',"
            "prompt='Synthetic import check',output_contract='research_stage_v1');"
            "root=pathlib.Path(quant_company.__file__).parent;"
            "print(json.dumps({'files':{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() "
            "for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts},"
            "'company_commit':os.environ['COMPANY_CODE_COMMIT'],'qdata_commit':os.environ['QDATA_CODE_COMMIT'],"
            "'output_fields':sorted(output_schema(r)['properties'])}))"
        )
        proof = json.loads(run(["docker", "run", "--rm", "--network=none", "--read-only", "--memory=256m",
            "--cpus=0.5", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--entrypoint", "python", image, "-c", probe]))
        assert proof["files"] == expected and proof["company_commit"] == SOURCE and proof["qdata_commit"] == qdata["commit"]
        assert proof["output_fields"] == ["action", "artifact_json", "read_offset", "read_path"]
        if target == "codex":
            assert run(["docker", "run", "--rm", "--network=none", "--read-only", "--entrypoint", "codex", image,
                        "--version"]).strip() == "codex-cli 0.154.0"
        row = json.loads(run(["docker", "image", "inspect", image]))[0]
        record["images"].append({"target": target, "image": image, "image_id": row["Id"],
                                 "source_bytes_verified": True, "uid": row["Config"]["User"],
                                 "build_log_sha256": sha(log)})
        save()
    assert pathlib.Path("/opt/quant-company/current").resolve() == old
    assert before == {name: sha(STATE / "config" / name) for name in before}
    record.update(state="exact_images_qualified_inactive", completed_at=datetime.now(UTC).isoformat())
    save()
    print(json.dumps(record, ensure_ascii=False), flush=True)

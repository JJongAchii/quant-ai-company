"""Build and inspect exact inactive images without replacing any service."""

import fcntl
import hashlib
import json
import pathlib
import shutil
import subprocess
import tarfile
from datetime import UTC, datetime

SOURCE = "bb1e42e8a33899f7a290e96bd71d3540a23eeb37"
OLD = "231d6ba0755f658f167636a8ea2c3ef65b6af562"
ARCHIVE_SHA = "fdbd1d502d27e275e442a6f18d74316a6ce9438f8591d470f4faed824ceb289f"
STATE = pathlib.Path("/var/lib/quant-company")
ROOT = pathlib.Path("/opt/quant-company/releases") / SOURCE
RECORD = STATE / "releases" / ("data-format-stage-" + SOURCE + ".json")


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
    assert not ROOT.exists() and not RECORD.exists(), "read_existing_stage_receipt"
    assert shutil.disk_usage(ROOT.parent).free > 8 * 1024**3, "insufficient_stage_space"
    archive = pathlib.Path("/tmp/" + SOURCE + "-company.tar")
    assert sha(archive) == ARCHIVE_SHA
    before = {name: sha(STATE / "config" / name) for name in
              ("roles.json", "research-profiles.json", "research-qlab.json", "runtime.env")}
    ROOT.mkdir(mode=0o755)
    with tarfile.open(archive) as stream:
        for member in stream.getmembers():
            assert member.isfile() or member.isdir()
            assert not member.name.startswith("/") and ".." not in pathlib.PurePosixPath(member.name).parts
            target = ROOT / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(stream.extractfile(member).read())
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
    qdata = json.loads((ROOT / "deploy/qdata-source.json").read_bytes())
    assert qdata == json.loads((old / "deploy/qdata-source.json").read_bytes())
    shutil.copytree(old / "qdata", ROOT / "qdata")
    expected = inventory(ROOT / "src/quant_company")
    record = {"state": "staging_inactive", "source_commit": SOURCE, "previous": str(old),
              "archive_sha256": ARCHIVE_SHA, "qdata_commit": qdata["commit"],
              "config_sha256": before, "source_files": len(expected), "images": [],
              "services_replaced": 0, "scientific_trials_added": 0, "started_at": datetime.now(UTC).isoformat()}

    def save():
        RECORD.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n")

    save()
    for target, prefix in (("app", "quant-company"), ("autonomous-research", "quant-company-autonomous"),
                           ("codex", "quant-company-codex")):
        image = prefix + ":" + SOURCE + "-data-format"
        log = STATE / "releases" / ("data-format-build-" + SOURCE + "-" + target + ".private.log")
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

"""Stage current-dependency images without changing operating services or authority."""

import argparse
import fcntl
import hashlib
import json
import shutil
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

STATE = Path("/var/lib/quant-company")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args):
    return subprocess.check_output(args, timeout=900)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    args = parser.parse_args()
    package = args.package.resolve()
    manifest = json.loads((package / "source-manifest.json").read_bytes())
    baseline = json.loads((package / "server-baseline.json").read_bytes())
    commit = manifest["source_commit"]
    target = Path("/opt/quant-company/releases") / commit
    journal = STATE / "releases" / ("conditional-runtime-stage-" + commit + ".json")
    with (STATE / ".backup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous = Path("/opt/quant-company/current").resolve()
        assert str(previous) == baseline["app_release"]
        assert not target.exists() and not journal.exists(), "stage_already_attempted"
        assert sha(STATE / "config/runtime.env") == baseline["config_hashes"]["runtime.env"]
        assert sha(package / (commit + ".tar")) == manifest["archive_sha256"]
        before = {name: json.loads(run(["docker", "inspect", name]))[0] for name in baseline["containers"]}
        assert all(row["Id"] == baseline["containers"][name]["id"] for name, row in before.items())
        target.mkdir()
        with tarfile.open(package / (commit + ".tar")) as archive:
            for member in archive.getmembers():
                relative = PurePosixPath(member.name)
                assert not relative.is_absolute() and ".." not in relative.parts
                assert member.isdir() or member.isfile(), "unsafe_archive_member"
            archive.extractall(target, filter="data")
        for name in ("pyproject.toml", "uv.lock", "deploy/entrypoint.py", "deploy/qdata-source.json"):
            assert sha(target / name) == sha(previous / name), "operating_dependencies_changed"
        if (previous / "qdata").is_dir():
            shutil.copytree(previous / "qdata", target / "qdata")
        source = target / "src/quant_company"
        assert {str(path.relative_to(source)): sha(path) for path in source.rglob("*") if path.is_file()} == manifest["company_files"]
        base = baseline["containers"]["quant-company-api-1"]
        assert before["quant-company-api-1"]["Image"] == base["image_id"]
        receipt = {"schema_version": 1, "source_commit": commit, "phase": "staging",
            "started_at": datetime.now(UTC).isoformat(), "previous_app": str(previous),
            "images": [], "production_changed": False, "scientific_authority_granted": False}

        def save():
            journal.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")
            journal.chmod(0o600)

        save()
        for service, repository, dockerfile in (
            ("api", "quant-company", target / "deploy/Dockerfile.quant-code-update"),
            ("worker", "quant-company-autonomous", package / "Dockerfile.worker"),
        ):
            image = repository + ":" + commit + "-conditional"
            log = STATE / "releases" / ("conditional-build-" + commit + "-" + service + ".log")
            print(json.dumps({"phase": "building", "service": service}), flush=True)
            with log.open("wb") as stream:
                subprocess.run(["docker", "buildx", "build", "--builder", "default", "--load", "--progress", "plain",
                    "--build-arg", "BASE_IMAGE=" + base["image"], "--build-arg", "RELEASE_COMMIT=" + commit,
                    "-f", str(dockerfile), "-t", image, str(target)],
                    check=True, stdout=stream, stderr=subprocess.STDOUT, timeout=900)
            probe = """import hashlib,importlib.util,json,os,pathlib,subprocess,sys
import exchange_calendars
from quant_company.company import fingerprint
from quant_company.research.program_contracts import ResearchProgram
from quant_company.research.approvals import short_command
root=pathlib.Path(importlib.util.find_spec('quant_company').origin).parent
assert str(root)=='/opt/quant-code/src/quant_company'
program=ResearchProgram.model_validate_json(pathlib.Path('/review/program.json').read_bytes())
assert program.schema_version==2 and program.envelopes[0].template.schema_version==3
assert short_command('연구 프로그램 취소 e06537d3-fac3-5c8c-bf25-ddabb3c7e282 53392822414ca32e89fab0f3a9a1093a350196345bd13084654a45510297159b')['action']=='cancel_program'
files={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}
git_version=subprocess.check_output(['git','--version'],text=True).strip() if sys.argv[1]=='worker' else None
print(json.dumps({'files':files,'source_commit':os.environ['COMPANY_CODE_COMMIT'],'program_digest':fingerprint(program.model_dump(mode='json')),'schema3_parser_verified':True,'signed_exact_cancel_parser_verified':True,'calendars_version':exchange_calendars.__version__,'git_version':git_version}))
"""
            proof = json.loads(run(["docker", "run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp",
                "--memory", "384m", "--cpus", "0.5", "-v", str(package / "candidate-program.json") + ":/review/program.json:ro",
                "--entrypoint", "/app/.venv/bin/python", image, "-B", "-c", probe, service]))
            assert proof.pop("files") == manifest["company_files"], "installed_source_mismatch"
            assert proof["source_commit"] == commit and proof["program_digest"] == manifest["program_digest"]
            receipt["images"].append({"service": service, "image": image,
                "image_id": json.loads(run(["docker", "image", "inspect", image]))[0]["Id"],
                "base_image": base["image"], "base_image_id": base["image_id"],
                "build_log_sha256": sha(log), "package_tree_sha256": manifest["company_tree_sha256"], "verification": proof})
            save()
        assert Path("/opt/quant-company/current").resolve() == previous
        assert sha(STATE / "config/runtime.env") == baseline["config_hashes"]["runtime.env"]
        assert all(json.loads(run(["docker", "inspect", name]))[0]["Id"] == row["Id"] for name, row in before.items())
        receipt.update(phase="qualified_images_staged_not_active", completed_at=datetime.now(UTC).isoformat())
        save()
        print(json.dumps(receipt), flush=True)


if __name__ == "__main__":
    main()

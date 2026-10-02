"""Stage exact compatible images under the deploy lock. Never selects or starts them."""

import argparse
import fcntl
import hashlib
import json
import pathlib
import shutil
import subprocess
import tarfile
from datetime import UTC, datetime

P = pathlib.Path
STATE = P("/var/lib/quant-company")


def run(args, **kwargs):
    return subprocess.check_output(args, timeout=900, **kwargs)


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=P, required=True)
    parser.add_argument("--manifest", type=P, required=True)
    parser.add_argument("--baseline", type=P, required=True)
    parser.add_argument("--program", type=P, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_bytes())
    baseline = json.loads(args.baseline.read_bytes())
    commit = manifest["source_commit"]
    assert len(commit) == 40 and all(char in "0123456789abcdef" for char in commit)
    destination = P("/opt/quant-company/releases") / commit
    record = STATE / "releases" / ("conditional-stage-" + commit + ".json")
    with (STATE / ".backup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous = P("/opt/quant-company/current").resolve()
        assert str(previous) == baseline["app_release"], "operating_release_changed"
        assert sha(STATE / "config/runtime.env") == baseline["config_hashes"]["runtime.env"], "runtime_changed"
        assert sha(args.archive) == manifest["archive_sha256"], "archive_changed"
        assert not destination.exists() and not record.exists(), "stage_already_attempted"
        assert shutil.disk_usage(destination.parent).free > 2 * 1024**3, "insufficient_disk"
        destination.mkdir(mode=0o755)
        with tarfile.open(args.archive) as archive:
            assert sum(item.size for item in archive.getmembers()) < 128 * 1024**2
            for item in archive.getmembers():
                path = pathlib.PurePosixPath(item.name)
                assert not path.is_absolute() and ".." not in path.parts
                assert item.isfile() or item.isdir()
                target = destination.joinpath(*path.parts)
                if item.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open("xb") as stream:
                        stream.write(archive.extractfile(item).read())
                    target.chmod(0o755 if item.mode & 0o111 else 0o644)
        for name in ("pyproject.toml", "uv.lock", "deploy/entrypoint.py", "deploy/qdata-source.json",
                     "deploy/Dockerfile.code-update"):
            assert sha(destination / name) == sha(previous / name), "fast_build_prerequisite_changed:" + name
        assert json.loads((destination / "deploy/qdata-source.json").read_bytes())["commit"] == manifest["qdata_commit"]
        shutil.copytree(previous / "qdata", destination / "qdata")
        observed = {str(path.relative_to(destination / "src/quant_company")): sha(path)
                    for path in (destination / "src/quant_company").rglob("*") if path.is_file()}
        assert observed == manifest["company_files"], "candidate_source_tree_changed"
        receipt = {"schema_version": 1, "phase": "staging", "source_commit": commit,
                   "archive_sha256": manifest["archive_sha256"], "started_at": datetime.now(UTC).isoformat(),
                   "previous_app": str(previous), "images": [], "active_changed": False,
                   "production_database_mutated": False, "scientific_authority_granted": False,
                   "market_backtest_executed": False}

        def save():
            record.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
            record.chmod(0o600)

        save()
        for service, name in (("api", "quant-company"), ("worker", "quant-company-autonomous")):
            captured = baseline["containers"]["quant-company-" + service + "-1"]
            base = json.loads(run(["docker", "inspect", "quant-company-" + service + "-1"]))[0]
            assert base["Id"] == captured["id"] and base["Image"] == captured["image_id"], "base_image_changed"
            image = name + ":" + commit
            log = STATE / "releases" / ("conditional-build-" + commit + "-" + service + ".log")
            print(json.dumps({"phase": "building_compatible_image", "service": service}), flush=True)
            with log.open("wb") as stream:
                subprocess.run([
                    "docker", "buildx", "build", "--builder", "default", "--load", "--progress", "plain",
                    "--build-arg", "BASE_IMAGE=" + captured["image"], "--build-arg", "RELEASE_COMMIT=" + commit,
                    "-f", str(destination / "deploy/Dockerfile.code-update"), "-t", image, str(destination),
                ], check=True, stdout=stream, stderr=subprocess.STDOUT, timeout=900)
            probe = """import hashlib,importlib.util,json,os,pathlib
from quant_company.company import fingerprint
from quant_company.research.program_contracts import ResearchProgram
from quant_company.research.data_evidence import DataEvidenceRegistry
root=pathlib.Path(importlib.util.find_spec('quant_company').origin).parent
program=ResearchProgram.model_validate_json(pathlib.Path('/review/program.json').read_bytes())
assert program.schema_version==2 and program.envelopes[0].template.schema_version==3
files={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}
print(json.dumps({'files':files,'source_commit':os.environ['COMPANY_CODE_COMMIT'],'program_digest':fingerprint(program.model_dump(mode='json')),'schema3_parser_verified':True}))
"""
            proof = json.loads(run([
                "docker", "run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp",
                "-v", str(args.program.resolve()) + ":/review/program.json:ro",
                "--entrypoint", "/app/.venv/bin/python", image, "-B", "-c", probe,
            ]))
            assert proof.pop("files") == manifest["company_files"], "installed_package_differs_from_source"
            assert proof["source_commit"] == commit and proof["program_digest"] == manifest["program_digest"]
            image_id = json.loads(run(["docker", "image", "inspect", image]))[0]["Id"]
            receipt["images"].append({"service": service, "image": image, "image_id": image_id,
                "base_image": captured["image"], "base_image_id": captured["image_id"],
                "build_log_sha256": sha(log), "package_tree_sha256": manifest["company_tree_sha256"],
                "isolated_parser_verification": proof})
            save()
        assert P("/opt/quant-company/current").resolve() == previous
        assert sha(STATE / "config/runtime.env") == baseline["config_hashes"]["runtime.env"]
        for name, captured in baseline["containers"].items():
            current = json.loads(run(["docker", "inspect", name]))[0]
            assert current["Id"] == captured["id"], "operating_container_changed_during_stage"
        receipt.update(phase="compatible_images_staged_not_active", completed_at=datetime.now(UTC).isoformat())
        save()
        print(json.dumps(receipt, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

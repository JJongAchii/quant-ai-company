"""Preserve the failed image; qualify new image tags at the base's actual import path."""

import argparse
import fcntl
import hashlib
import json
import pathlib
import subprocess
from datetime import UTC, datetime

P = pathlib.Path
STATE = P("/var/lib/quant-company")


def run(args, **kwargs):
    return subprocess.check_output(args, timeout=600, **kwargs)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=P, required=True)
    parser.add_argument("--baseline", type=P, required=True)
    parser.add_argument("--program", type=P, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_bytes())
    baseline = json.loads(args.baseline.read_bytes())
    commit = manifest["source_commit"]
    assert commit == "19807db8d1817a3a495ac10eee34b1bac2d287fe"
    destination = P("/opt/quant-company/releases") / commit
    record = STATE / "releases" / ("conditional-stage-" + commit + "-intake2.json")
    prior_path = STATE / "releases" / ("conditional-stage-" + commit + ".json")
    with (STATE / ".backup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert not record.exists(), "repair_already_attempted"
        previous = P("/opt/quant-company/current").resolve()
        assert str(previous) == baseline["app_release"]
        assert sha(STATE / "config/runtime.env") == baseline["config_hashes"]["runtime.env"]
        prior = json.loads(prior_path.read_bytes())
        assert prior["phase"] == "staging" and prior["images"] == [] and not prior["active_changed"]
        source = destination / "src/quant_company"
        assert {str(path.relative_to(source)): sha(path) for path in source.rglob("*") if path.is_file()} == manifest["company_files"]
        for name in ("pyproject.toml", "uv.lock", "deploy/entrypoint.py", "deploy/qdata-source.json",
                     "deploy/Dockerfile.quant-code-update"):
            assert sha(destination / name) == sha(previous / name)
        failed = json.loads(run(["docker", "image", "inspect", "quant-company:" + commit]))[0]
        assert failed["Id"] == "sha256:84d790966e66eee752c1f4b58f812faf3bb3d504e3c099a8f5de2b532165554e"
        receipt = {
            "schema_version": 1, "phase": "staging_forward_repair", "source_commit": commit,
            "started_at": datetime.now(UTC).isoformat(), "previous_app": str(previous), "images": [],
            "failed_attempt": {"receipt_sha256": sha(prior_path), "image_id": failed["Id"],
                "reason": "inherited_PYTHONPATH_selected_old_opt_quant_code_instead_of_new_wheel",
                "failed_image_preserved": True, "initial_stage_exit_code": 1},
            "active_changed": False, "production_database_mutated": False,
            "scientific_authority_granted": False, "market_backtest_executed": False,
        }

        def save():
            record.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
            record.chmod(0o600)

        save()
        for service, name in (("api", "quant-company"), ("worker", "quant-company-autonomous")):
            captured = baseline["containers"]["quant-company-" + service + "-1"]
            current = json.loads(run(["docker", "inspect", "quant-company-" + service + "-1"]))[0]
            assert current["Id"] == captured["id"] and current["Image"] == captured["image_id"]
            image = name + ":" + commit + "-intake2"
            log = STATE / "releases" / ("conditional-build-" + commit + "-" + service + "-intake2.log")
            print(json.dumps({"phase": "building_at_verified_import_path", "service": service}), flush=True)
            with log.open("wb") as stream:
                subprocess.run([
                    "docker", "buildx", "build", "--builder", "default", "--load", "--progress", "plain",
                    "--build-arg", "BASE_IMAGE=" + captured["image"], "--build-arg", "RELEASE_COMMIT=" + commit,
                    "-f", str(destination / "deploy/Dockerfile.quant-code-update"), "-t", image, str(destination),
                ], check=True, stdout=stream, stderr=subprocess.STDOUT, timeout=600)
            probe = """import hashlib,importlib.util,json,os,pathlib
from quant_company.company import fingerprint
from quant_company.research.program_contracts import ResearchProgram
root=pathlib.Path(importlib.util.find_spec('quant_company').origin).parent
assert str(root)=='/opt/quant-code/src/quant_company'
program=ResearchProgram.model_validate_json(pathlib.Path('/review/program.json').read_bytes())
assert program.schema_version==2 and program.envelopes[0].template.schema_version==3
files={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}
print(json.dumps({'files':files,'source_commit':os.environ['COMPANY_CODE_COMMIT'],'import_root':str(root),'program_digest':fingerprint(program.model_dump(mode='json')),'schema3_parser_verified':True}))
"""
            proof = json.loads(run([
                "docker", "run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp",
                "-v", str(args.program.resolve()) + ":/review/program.json:ro",
                "--entrypoint", "/app/.venv/bin/python", image, "-B", "-c", probe,
            ]))
            assert proof.pop("files") == manifest["company_files"], "installed_source_tree_differs"
            assert proof["source_commit"] == commit and proof["program_digest"] == manifest["program_digest"]
            receipt["images"].append({"service": service, "image": image,
                "image_id": json.loads(run(["docker", "image", "inspect", image]))[0]["Id"],
                "base_image": captured["image"], "base_image_id": captured["image_id"],
                "build_log_sha256": sha(log), "package_tree_sha256": manifest["company_tree_sha256"],
                "isolated_parser_verification": proof})
            save()
        assert P("/opt/quant-company/current").resolve() == previous
        assert sha(STATE / "config/runtime.env") == baseline["config_hashes"]["runtime.env"]
        for name, captured in baseline["containers"].items():
            assert json.loads(run(["docker", "inspect", name]))[0]["Id"] == captured["id"]
        receipt.update(phase="compatible_images_staged_not_active", completed_at=datetime.now(UTC).isoformat())
        save()
        print(json.dumps(receipt, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

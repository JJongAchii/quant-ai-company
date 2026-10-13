"""Freeze a current operating-compatible source package; no worker or server activation."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from quant_company.company import fingerprint
from quant_company.research.program_contracts import ResearchProgram

ROOT = Path(__file__).resolve().parents[4]
EVIDENCE = Path(__file__).resolve().parent
PREVIOUS = EVIDENCE.parent / "conditional-activation-20261001"
LOCAL = ROOT / ".local/conditional-runtime-20261002"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def write(path, value):
    with path.open("x") as stream:
        stream.write(json.dumps(value, sort_keys=True, indent=2) + "\n")


def main():
    source = git("rev-parse", "HEAD").decode().strip()
    baseline = json.loads((EVIDENCE / "server-baseline.json").read_bytes())
    operating = Path(baseline["app_release"]).name
    git("merge-base", "--is-ancestor", operating, source)
    for name in ("pyproject.toml", "uv.lock", "deploy/entrypoint.py", "deploy/qdata-source.json"):
        assert git("show", source + ":" + name) == git("show", operating + ":" + name), "operating_dependency_drift"
    # Every research source file except the independently tested cancellation
    # parser is unchanged by the current operating integration.
    changed = git("diff", "--name-only", "19807db8d1817a3a495ac10eee34b1bac2d287fe", source, "--",
                  "src/quant_company/research").decode().splitlines()
    assert changed == ["src/quant_company/research/approvals.py"], "unreviewed_research_source_drift"
    old_package = json.loads((PREVIOUS / "worker-package.json").read_bytes())
    old = ROOT / old_package["local_package"]
    assert all(sha(old / name) == digest for name, digest in old_package["files"].items())
    package = LOCAL / "worker-package"
    package.mkdir(mode=0o700, exist_ok=False)
    for name in old_package["files"]:
        if name not in {"company.bundle", "qualify-worker-inputs.py"}:
            shutil.copyfile(old / name, package / name)
    procedure = (PREVIOUS / "qualify-worker-inputs.py").read_text()
    old_source = "19807db8d1817a3a495ac10eee34b1bac2d287fe"
    assert procedure.count(old_source) == 1
    (EVIDENCE / "qualify-worker-inputs.py").write_text(procedure.replace(old_source, source))
    shutil.copyfile(EVIDENCE / "qualify-worker-inputs.py", package / "qualify-worker-inputs.py")
    subprocess.run(["git", "bundle", "create", str(package / "company.bundle"), "HEAD"], cwd=ROOT, check=True)
    assert (package / "company.bundle").stat().st_size <= 256 * 1024 * 1024
    manifest = {**old_package, "attempt": 1, "source_commit": source,
        "local_package": str(package.relative_to(ROOT)),
        "files": {path.name: sha(path) for path in package.iterdir() if path.is_file()},
        "preparation_basis": "current_operating_app_and_signed_exact_program_cancellation_integrated",
    }
    write(package / "worker-package.json", manifest)
    shutil.copyfile(package / "worker-package.json", EVIDENCE / "worker-package.json")
    program = ResearchProgram.model_validate_json((package / "candidate-program.json").read_bytes())
    assert fingerprint(program.model_dump(mode="json")) == manifest["program_digest"]
    archive = LOCAL / (source + ".tar")
    subprocess.run(["git", "archive", "--format=tar", "-o", str(archive), source,
                    "src", "deploy", "pyproject.toml", "uv.lock", "README.md"], cwd=ROOT, check=True)
    paths = git("ls-tree", "-r", "--name-only", source, "src/quant_company").decode().splitlines()
    files = {name.removeprefix("src/quant_company/"):
        hashlib.sha256(git("show", source + ":" + name)).hexdigest() for name in paths}
    source_manifest = {
        "schema_version": 1, "source_commit": source, "preserved_operating_app_commit": operating,
        "company_files": files,
        "company_tree_sha256": hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "archive_sha256": sha(archive), "archive_bytes": archive.stat().st_size,
        "archive_scope": "runtime_src_deploy_pyproject_lock_README_from_exact_commit",
        "program_digest": manifest["program_digest"], "worker_package_sha256": sha(EVIDENCE / "worker-package.json"),
        "qdata_commit": json.loads(git("show", source + ":deploy/qdata-source.json"))["commit"],
        "research_source_delta": changed, "operating_registry_sha256": baseline["registry_sha256"],
        "production_changed": False, "scientific_trials_added": 0,
    }
    write(EVIDENCE / "source-manifest.json", source_manifest)
    print(json.dumps({"source_commit": source, "package_files": len(manifest["files"]),
        "company_bundle_bytes": (package / "company.bundle").stat().st_size,
        "runtime_archive_bytes": archive.stat().st_size, "production_changed": False}))


if __name__ == "__main__":
    main()

"""Run a proposed regression against the unchanged parent, inside isolated CI only."""

import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


def run_cases(root, test, report):
    env = {**os.environ, "PYTHONPATH": str(root / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    probe = ("import quant_company,pathlib; assert pathlib.Path(quant_company.__file__).resolve()"
             ".is_relative_to(pathlib.Path.cwd().resolve())")
    subprocess.run([sys.executable, "-c", probe], cwd=root, env=env, check=True, timeout=20)
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "--rootdir", ".", test,
                             "--junitxml", str(report)], cwd=root, env=env, timeout=120, check=False)
    if not report.exists():
        raise ValueError("No comparison test receipt")
    suites = ET.parse(report).getroot()
    cases = list(suites.iter("testcase"))
    ids = [(case.get("classname"), case.get("name")) for case in cases]
    if not cases or len(ids) != len(set(ids)) or list(suites.iter("error")) or list(suites.iter("skipped")):
        raise ValueError("Comparison requires collected, unique tests with no errors or skips")
    return result, suites, set(ids)


def check(repository: Path) -> dict:
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=repository)

    parents = git("rev-list", "--parents", "-n", "1", "HEAD").decode().split()
    if len(parents) != 2:
        raise ValueError("Maintenance CI requires an exact single-parent candidate commit")
    changed = git("diff", "--name-only", parents[1], parents[0]).decode().splitlines()
    source_changed = any(p.startswith("src/quant_company/") and p.endswith(".py") for p in changed)
    if not source_changed:
        return {"state": "not_applicable", "reason": "no_python_runtime_change", "base": parents[1]}
    tests = [p for p in changed if p.startswith("tests/test_maintenance_regression_") and p.endswith(".py")]
    if len(tests) != 1:
        raise ValueError("A Python repair requires exactly one new regression test file")
    existing = set(git("ls-tree", "-r", "--name-only", parents[1]).decode().splitlines())
    if tests[0] in existing:
        raise ValueError("Existing baseline tests cannot be replaced")
    with tempfile.TemporaryDirectory(prefix="maintenance-baseline-") as directory:
        baseline = Path(directory)
        with tarfile.open(fileobj=io.BytesIO(git("archive", parents[1]))) as archive:
            archive.extractall(baseline, filter="data")
        target = baseline / tests[0]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((repository / tests[0]).read_bytes())
        # The installed test tools are reused, but application imports must resolve to the old source.
        result, suites, base_ids = run_cases(baseline, tests[0], baseline / "regression.xml")
        failures = list(suites.iter("failure"))
        if (result.returncode != 1 or not failures
                or not all((f.get("message") or "").startswith("assert ")
                           or "AssertionError" in (f.text or "") for f in failures)):
            raise ValueError("Regression did not reproduce an assertion failure on the unchanged base")
        candidate, after, candidate_ids = run_cases(repository, tests[0], baseline / "candidate.xml")
        if candidate.returncode != 0 or list(after.iter("failure")) or candidate_ids != base_ids:
            raise ValueError("Candidate must pass the same regression cases without removing or skipping them")
        return {"state": "reproduced_on_base", "base": parents[1], "head": parents[0], "test": tests[0],
                "comparison": {"cases": len(base_ids), "base_failures": len(failures), "candidate_failures": 0}}


if __name__ == "__main__":
    print(json.dumps(check(Path.cwd()), indent=2))

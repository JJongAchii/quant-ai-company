import importlib.util
import subprocess
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("regression_gate", Path(__file__).resolve().parents[1]
                                           / "scripts/check_maintenance_regression.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def candidate(tmp_path, original):
    def git(*args):
        subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
                        "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", *args],
                       cwd=tmp_path, check=True, capture_output=True)
    source = tmp_path / "src/quant_company"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text("")
    module = source / "tools.py"
    module.write_text(original)
    git("init")
    git("add", ".")
    git("commit", "-m", "fixture baseline")
    module.write_text("def value():\n    return 2\n# Proposed candidate\n")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_maintenance_regression_fixture.py").write_text(
        "from quant_company.tools import value\n\ndef test_value():\n    assert value() == 2\n")
    git("add", ".")
    git("commit", "-m", "fixture proposed repair")


def test_regression_must_fail_on_original_source(tmp_path):
    candidate(tmp_path, "def value():\n    return 1\n")
    assert gate.check(tmp_path)["state"] == "reproduced_on_base"


@pytest.mark.parametrize("original", ["def value():\n    return 2\n", "def value():\n    raise ValueError('unrelated')\n"])
def test_already_passing_or_unrelated_error_is_not_reproduction(tmp_path, original):
    candidate(tmp_path, original)
    with pytest.raises(ValueError, match="did not reproduce"):
        gate.check(tmp_path)
